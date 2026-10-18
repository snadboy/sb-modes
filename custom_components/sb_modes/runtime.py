"""The running state of SB Modes: the saved table, the active mode, the behaviour each
zone runs, and the table's problems.

One Runtime per (single) config entry. The table lives in a Store, not in the entry's
options, because it is a structured document edited as a whole and saved only when it
validates. Entities subscribe with add_listener() and re-render on any change.

Behaviours start once Home Assistant has started plus a settle period (sensor states
are still loading before that), and are restarted, for every zone, on each mode change.
A table change reloads the entry, which stops and starts them too.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CALLBACK_TYPE, Context, CoreState, HassJob, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later, async_track_point_in_time, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import driver
from .behaviours import KINDS, Behaviour, HandsOff, Held
from .bookkeeping import Keeper, describe_hold, release_delay
from .const import (
    ALERT_GRACE_SECONDS, CHECK_SECONDS, DOMAIN, EVENT_MODE_CHANGED, ISSUE_ID, STARTUP_SETTLE_SECONDS, STORE_KEY,
    STORE_VERSION,
)
from .model import default_config, find, mode_ids, next_change, resolve, scheduled_mode
from .validate import check_config
from .zones import zone_entities

_LOGGER = logging.getLogger(__name__)
HOLDS = ("next", "timed", "paused")


def state_lookup(hass: HomeAssistant) -> Callable[[str], str | None]:
    """state_of() for validation: a registered entity with no state yet (its integration
    still loading) reads 'unavailable', not missing; a disabled one counts as missing."""
    reg = er.async_get(hass)

    def state_of(eid: str) -> str | None:
        if (st := hass.states.get(eid)) is not None:
            return st.state
        if (e := reg.async_get(eid)) is not None:
            return "unavailable" if not e.disabled_by else None
        return None

    return state_of


def area_lookup(hass: HomeAssistant) -> Callable[[str], bool]:
    reg = ar.async_get(hass)
    return lambda area_id: isinstance(area_id, str) and reg.async_get_area(area_id) is not None


class Runtime:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.store: Store = Store(hass, STORE_VERSION, STORE_KEY)
        self.config: dict[str, Any] = default_config()
        self.mode: str | None = None
        self.mode_since: str | None = None                 # ISO time the mode last changed
        self.manual: dict | None = None                    # {"hold": next|timed|paused, "since", "until"}
        self._sched_unsub: CALLBACK_TYPE | None = None
        self._hold_unsub: CALLBACK_TYPE | None = None
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.ready = False                                # behaviours may run
        self.own_contexts: deque[str] = deque(maxlen=500)  # ids of the contexts of our own calls
        self._behaviours: dict[str, Behaviour] = {}
        self.keeper = Keeper(self)
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._grace: CALLBACK_TYPE | None = None
        self._issue_raised = False

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def async_load(self) -> None:
        stored = await self.store.async_load()
        if stored is None:
            await self.store.async_save(self.config)     # seed: four hands_off modes, no zones
        else:
            self.config = stored
        await self.keeper.async_load()

    @callback
    def start(self) -> None:
        self.check()
        self._unsubs.append(async_track_time_interval(
            self.hass, self._tick, timedelta(seconds=CHECK_SECONDS), cancel_on_shutdown=True))
        if self.hass.state is CoreState.running:
            self._set_ready()
        else:
            self._unsubs.append(self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, self._started))

    @callback
    def _started(self, _event) -> None:
        self._unsubs.append(async_call_later(
            self.hass, STARTUP_SETTLE_SECONDS, HassJob(self._settled, cancel_on_shutdown=True)))

    @callback
    def _settled(self, _now) -> None:
        self._set_ready()

    @callback
    def _set_ready(self) -> None:
        self.ready = True
        self._restart_behaviours()

    @callback
    def _tick(self, _now) -> None:
        self.check()

    @callback
    def stop(self) -> None:
        for unsub in self._unsubs:
            try:
                unsub()
            except ValueError:                           # a listen_once that already fired
                pass
        self._unsubs.clear()
        for name in ("_sched_unsub", "_hold_unsub"):
            if getattr(self, name):
                getattr(self, name)()
                setattr(self, name, None)
        self._cancel_grace()
        self._stop_behaviours()
        self.keeper.stop()

    # ── the table ────────────────────────────────────────────────────────────
    def validate(self, config: Any) -> tuple[list[str], list[str]]:
        return check_config(config, state_lookup(self.hass), area_lookup(self.hass),
                            lambda areas: zone_entities(self.hass, areas))

    async def async_save(self, config: dict[str, Any]) -> list[str]:
        """Save a table that has no errors (the caller checked); returns its warnings.
        The entry reloads afterwards, so zones added or removed become entities."""
        errors, warnings = self.validate(config)
        if errors:
            raise ValueError(" ".join(errors))
        await self.store.async_save(config)
        self.config = config
        return warnings

    @property
    def mode_ids(self) -> list[str]:
        return mode_ids(self.config)

    @property
    def fallback(self) -> str | None:
        fb = self.config.get("fallback_mode")
        if fb in self.mode_ids:
            return fb
        return self.mode_ids[0] if self.mode_ids else None

    def mode_name(self, mode_id: str | None) -> str | None:
        m = find(self.config.get("modes"), mode_id) if mode_id else None
        return (m.get("name") or mode_id) if m else None

    def zones(self) -> list[dict]:
        return [z for z in self.config.get("zones") or [] if isinstance(z, dict) and z.get("id")]

    def takes_over(self) -> dict[str, list[dict]]:
        """Per mode, the zones it controls (behaviour other than hands_off), for the panel's
        "switch to …?" confirmation."""
        out: dict[str, list[dict]] = {}
        for m in self.mode_ids:
            out[m] = [{"zone": z.get("name") or z["id"], "behaviour": picked["behaviour"]}
                      for z in self.zones()
                      if (picked := resolve(self.config, m, z["id"])) and picked["behaviour"] != "hands_off"]
        return out

    def zone_by_mode(self, zone_id: str) -> dict[str, dict]:
        """{mode id: {behaviour, params, source}} for one zone, every mode."""
        return {m: picked for m in self.mode_ids if (picked := resolve(self.config, m, zone_id))}

    def zone_state(self, zone_id: str) -> dict | None:
        """What the zone runs now, for its sensor: the resolved choice plus live status."""
        picked = resolve(self.config, self.mode, zone_id) if self.mode else None
        if picked is None:
            return None
        b = self._behaviours.get(zone_id)
        picked["status"] = b.status if b else "waiting for start-up"
        picked["entities"] = b.entities if b else {}
        picked["hold"] = self.keeper.held(zone_id)
        picked["overrides"] = self.keeper.overridden(zone_id)
        return picked

    # ── the active mode ──────────────────────────────────────────────────────
    @callback
    def restore_mode(self, last: str | None, since: str | None = None, manual: dict | None = None) -> None:
        """At startup (and after a table reload): the last mode, when it began and any manual hold,
        then catch up with the schedule — HA may have been down across a scheduled change."""
        if last in self.mode_ids:
            self.mode, self.mode_since = last, since or dt_util.utcnow().isoformat()
        else:
            self.mode, self.mode_since = self.fallback, dt_util.utcnow().isoformat()
        self.manual = manual if isinstance(manual, dict) and manual.get("hold") in HOLDS else None
        self.keeper.restored(self.mode)
        self._catch_up(source="startup", restart=False)
        self._arm_hold()
        self._arm_schedule()
        self._restart_behaviours()

    @callback
    def set_mode(self, mode_id: str, hold: str | int = "next") -> None:
        """A MANUAL mode change (the select, the panel, voice, an automation). `hold` says how long
        it beats the schedule: "next" until the next scheduled change, minutes (int) for that long
        then back to the scheduled mode, or "paused" until resume_schedule()."""
        if mode_id not in self.mode_ids:
            raise ValueError(f"“{mode_id}” is not a defined mode.")
        now = dt_util.utcnow()
        if isinstance(hold, int) and not isinstance(hold, bool):
            self.manual = {"hold": "timed", "since": now.isoformat(), "until": (now + timedelta(minutes=hold)).isoformat()}
        elif hold in ("next", "paused"):
            self.manual = {"hold": hold, "since": now.isoformat(), "until": None}
        else:
            raise ValueError(f"Unknown hold {hold!r}: next, paused or a number of minutes.")
        self._arm_hold()
        if not self._switch(mode_id, "manual"):
            self.notify()

    @callback
    def resume_schedule(self) -> None:
        """End any manual hold and go to the mode the schedule says now."""
        self.manual = None
        self._arm_hold()
        if not self._catch_up(source="resume"):
            self.notify()

    @property
    def scheduled(self) -> tuple[str | None, Any]:
        return scheduled_mode(self.config, dt_util.now())

    @property
    def upcoming(self) -> tuple[str | None, Any]:
        return next_change(self.config, dt_util.now())

    @callback
    def _switch(self, mode_id: str, source: str, restart: bool = True) -> bool:
        """Make mode_id the active mode; False when it already is."""
        if mode_id == self.mode:
            return False
        old, self.mode = self.mode, mode_id
        self.mode_since = dt_util.utcnow().isoformat()
        self.keeper.mode_changed()
        self.hass.bus.async_fire(EVENT_MODE_CHANGED, {"from": old, "to": mode_id, "source": source})
        if restart:
            self._restart_behaviours()
        return True

    @callback
    def _catch_up(self, source: str = "schedule", restart: bool = True) -> bool:
        """Follow the schedule unless a manual hold still stands. True when the mode changed."""
        sched, at = self.scheduled
        if sched is None or sched not in self.mode_ids:
            return False
        m, now = self.manual, dt_util.utcnow()
        if m:
            if m["hold"] == "paused":
                return False
            if m["hold"] == "timed" and m.get("until") and dt_util.parse_datetime(m["until"]) > now:
                return False
            if m["hold"] == "next" and at is not None and at <= dt_util.parse_datetime(m["since"]):
                return False                         # no scheduled change since the manual one
            self.manual = None
        return self._switch(sched, source, restart)

    @callback
    def _arm_schedule(self) -> None:
        if self._sched_unsub:
            self._sched_unsub()
            self._sched_unsub = None
        _mode, when = self.upcoming
        if when is not None:
            self._sched_unsub = async_track_point_in_time(self.hass, self._schedule_due, when)

    @callback
    def _schedule_due(self, _now) -> None:
        self._sched_unsub = None
        if not self._catch_up():
            self.notify()                            # a held change still moves "next change"
        self._arm_schedule()

    @callback
    def _arm_hold(self) -> None:
        if self._hold_unsub:
            self._hold_unsub()
            self._hold_unsub = None
        m = self.manual
        if m and m["hold"] == "timed" and m.get("until"):
            self._hold_unsub = async_track_point_in_time(self.hass, self._hold_over, dt_util.parse_datetime(m["until"]))

    @callback
    def _hold_over(self, _now) -> None:
        self._hold_unsub = None
        self.manual = None
        if not self._catch_up(source="hold_ended"):
            self.notify()

    # ── behaviours ───────────────────────────────────────────────────────────
    @callback
    def _restart_behaviours(self) -> None:
        self._stop_behaviours()
        if self.ready and self.mode:
            for zone in self.zones():
                self._start_zone(zone)
        self._rewatch()
        self.notify()

    @callback
    def _start_zone(self, zone: dict) -> None:
        picked = resolve(self.config, self.mode, zone["id"]) or {}
        kind = Held if self.keeper.held(zone["id"]) else KINDS.get(picked.get("behaviour") or "", HandsOff)
        b = kind(self, zone, picked.get("params") or {}, zone_entities(self.hass, zone.get("areas") or []))
        self._behaviours[zone["id"]] = b
        b.start()

    @callback
    def restart_zone(self, zone_id: str) -> None:
        """One zone's behaviour again, for the current mode (after a hold or a release)."""
        if (b := self._behaviours.pop(zone_id, None)) is not None:
            b.stop()
        zone = next((z for z in self.zones() if z["id"] == zone_id), None)
        if zone and self.ready and self.mode:
            self._start_zone(zone)
        self._rewatch()
        self.notify()

    @callback
    def _rewatch(self) -> None:
        """Watch the lights of every zone whose behaviour acts, for changes made by hand."""
        watch = {}
        for zid, b in self._behaviours.items():
            if b.name in ("hands_off", "held"):
                continue
            picked = resolve(self.config, self.mode, zid) or {}
            watch[zid] = {"lights": b.entities.get("lights", []), "occupancy": b.entities.get("occupancy", []),
                          "delay": release_delay(picked.get("behaviour"), picked.get("params") or {})}
        self.keeper.watch(watch)

    @callback
    def _stop_behaviours(self) -> None:
        for b in self._behaviours.values():
            b.stop()
        self._behaviours.clear()

    @callback
    def apply(self, targets: dict[str, dict], why: str, zone_id: str | None = None) -> None:
        """Send targets through the driver, under a context remembered as our own. Lights
        changed by hand in the zone are left out."""
        if zone_id is not None:
            if self.keeper.held(zone_id):
                return
            skip = self.keeper.overridden(zone_id)
            targets = {e: t for e, t in targets.items() if e not in skip}
        if not targets:
            return
        self.keeper.commanded(targets)
        ctx = Context()
        self.own_contexts.append(ctx.id)
        _LOGGER.debug("SB Modes: %s → %s", why, targets)
        self.hass.async_create_task(driver.apply(self.hass, targets, ctx), f"sb_modes apply {why}")

    # ── problems ─────────────────────────────────────────────────────────────
    @callback
    def check(self) -> None:
        errors, warnings = self.validate(self.config)
        changed = (errors, warnings) != (self.errors, self.warnings)
        self.errors, self.warnings = errors, warnings
        if errors:
            if self._issue_raised:
                self._raise_issue()                 # keep the text current
            elif self._grace is None:
                self._grace = async_call_later(
                    self.hass, ALERT_GRACE_SECONDS, HassJob(self._grace_over, cancel_on_shutdown=True))
        else:
            self._cancel_grace()
            if self._issue_raised:
                ir.async_delete_issue(self.hass, DOMAIN, ISSUE_ID)
                self._issue_raised = False
        if changed:
            self.notify()

    @callback
    def _grace_over(self, _now=None) -> None:
        self._grace = None
        if self.errors:
            self._raise_issue()

    @callback
    def _raise_issue(self) -> None:
        self._issue_raised = True
        ir.async_create_issue(
            self.hass, DOMAIN, ISSUE_ID, is_fixable=False, is_persistent=False,
            severity=ir.IssueSeverity.WARNING, translation_key=ISSUE_ID,
            translation_placeholders={"errors": "\n".join(f"- {e}" for e in self.errors)},
        )

    @callback
    def _cancel_grace(self) -> None:
        if self._grace is not None:
            self._grace()
            self._grace = None

    # ── listeners ────────────────────────────────────────────────────────────
    @callback
    def add_listener(self, fn: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(fn)
        return lambda: self._listeners.remove(fn)

    @callback
    def notify(self) -> None:
        for fn in list(self._listeners):
            fn()
