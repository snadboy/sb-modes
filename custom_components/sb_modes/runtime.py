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
from datetime import date, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CALLBACK_TYPE, Context, CoreState, HassJob, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import (
    async_call_later, async_track_point_in_time, async_track_state_change_event, async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

from . import driver
from .behaviours import KINDS, Behaviour, HandsOff, Held, Overlaid
from .bookkeeping import Keeper, describe_hold, release_delay
from .follow import Follower
from .overlay import Overlay
from .const import (
    ALERT_GRACE_SECONDS, CHECK_SECONDS, DOMAIN, EVENT_MODE_CHANGED, ISSUE_ID, STARTUP_SETTLE_SECONDS, STORE_KEY,
    STORE_VERSION,
)
from .model import default_config, find, mode_ids, next_change, resolve, rule_day_sets, scheduled_mode
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
        self.prev_mode: str | None = None                  # the mode before this one (wake sequences)
        self._day_sets: dict[str, set[date]] = {}         # day set → dates in it, within _day_window
        self._day_window: tuple[date, date] | None = None
        self._calendar_wait = False                        # catch-up held until the day sets load
        self._sched_unsub: CALLBACK_TYPE | None = None
        self._hold_unsub: CALLBACK_TYPE | None = None
        self._occ_unsub: CALLBACK_TYPE | None = None
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.ready = False                                # behaviours may run
        self.own_contexts: deque[str] = deque(maxlen=500)  # ids of the contexts of our own calls
        self._ctx_targets: dict[str, frozenset[str]] = {}  # our context id → the entities it set
        self._followers: dict[str, Follower] = {}
        self._overlays: dict[str, Overlay] = {}
        self.daylight: dict[str, tuple] = {}              # zone → (last reading with its lights off, when)
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
        self.hass.async_create_task(self.async_refresh_day_sets(), "sb_modes day sets")
        self._unsubs.append(async_track_time_interval(
            self.hass, self._refresh_tick, timedelta(hours=1), cancel_on_shutdown=True))

    @callback
    def _refresh_tick(self, _now) -> None:
        self.hass.async_create_task(self.async_refresh_day_sets(), "sb_modes day sets")

    # ── resolvers for the schedule: sun times, day sets (SB Scheduler calendars) ──
    def sun(self, event: str, day: date):
        return get_astral_event_date(self.hass, event, day)

    def in_set(self, day_set: str, day: date) -> bool | None:
        dates = self._day_sets.get(day_set)
        if dates is None or self._day_window is None or not self._day_window[0] <= day <= self._day_window[1]:
            return None
        return day in dates

    def _day_set_entity(self, day_set: str) -> str | None:
        """The calendar SB Scheduler publishes for a day set (attribute day_set_id)."""
        for st in self.hass.states.async_all("calendar"):
            if st.attributes.get("day_set_id") == day_set:
                return st.entity_id
        return None

    async def async_refresh_day_sets(self) -> None:
        """Read which dates each day set the schedule uses holds, from its calendar."""
        wanted = rule_day_sets(self.config)
        today = dt_util.now().date()
        lo, hi = today - timedelta(days=9), today + timedelta(days=10)
        found: dict[str, set[date]] = {}
        for ds in wanted:
            eid = self._day_set_entity(ds)
            if eid is None:
                continue
            try:
                res = await self.hass.services.async_call(
                    "calendar", "get_events", {"entity_id": eid,
                                               "start_date_time": dt_util.start_of_local_day(lo).isoformat(),
                                               "end_date_time": dt_util.start_of_local_day(hi + timedelta(days=1)).isoformat()},
                    blocking=True, return_response=True)
            except Exception:  # noqa: BLE001 — a calendar not ready yet; the next refresh retries
                _LOGGER.debug("SB Modes: could not read %s", eid, exc_info=True)
                continue
            dates: set[date] = set()
            for ev in (res or {}).get(eid, {}).get("events", []):
                start, end = dt_util.parse_date(str(ev["start"])[:10]), dt_util.parse_date(str(ev["end"])[:10])
                if start is None or end is None:
                    continue
                d = start
                while d < end or d == start:
                    dates.add(d)
                    d += timedelta(days=1)
            found[ds] = dates
        self._day_sets, self._day_window = found, (lo, hi)
        if wanted and not wanted <= set(found):
            # not all calendars there yet (SB Scheduler still starting): try again in a minute
            self._unsubs.append(async_call_later(self.hass, 60, HassJob(self._refresh_tick, cancel_on_shutdown=True)))
            return
        if self._calendar_wait:
            self._calendar_wait = False
            self._catch_up(source="startup")
        self._arm_schedule()
        self.notify()

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
        for name in ("_sched_unsub", "_hold_unsub", "_occ_unsub"):
            if getattr(self, name):
                getattr(self, name)()
                setattr(self, name, None)
        self._cancel_grace()
        self._stop_behaviours()
        for f in [*self._followers.values(), *self._overlays.values()]:
            f.stop()
        self._followers.clear()
        self._overlays.clear()
        self.keeper.stop()

    # ── the table ────────────────────────────────────────────────────────────
    def validate(self, config: Any) -> tuple[list[str], list[str]]:
        return check_config(config, state_lookup(self.hass), area_lookup(self.hass),
                            lambda areas: zone_entities(self.hass, areas),
                            lambda ds: self._day_set_entity(ds) is not None)

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

    @property
    def schedule_state(self) -> str:
        """on_schedule | held_until_next | held_timed | paused | no_schedule."""
        if self.manual:
            return {"next": "held_until_next", "timed": "held_timed", "paused": "paused"}[self.manual["hold"]]
        return "on_schedule" if self.config.get("schedule") else "no_schedule"

    def deviations(self) -> list[dict]:
        """Everything that is not as the table and schedule would have it, each with its type:
        mode_hold (the mode set by hand), zone_hold (a zone held), changed_by_hand (a light)."""
        out: list[dict] = []
        if self.manual:
            out.append({"type": "mode_hold", "mode": self.mode, "hold": self.manual["hold"],
                        "since": self.manual.get("since"), "until": self.manual.get("until")})
        names = {z["id"]: z.get("name") or z["id"] for z in self.zones()}
        for zid, h in self.keeper.holds.items():
            out.append({"type": "zone_hold", "zone": zid, "zone_name": names.get(zid, zid), "hold": h["hold"],
                        "since": h.get("since"), "until": h.get("until")})
        for zid, lights in self.keeper.overrides.items():
            for light, o in lights.items():
                out.append({"type": "changed_by_hand", "zone": zid, "zone_name": names.get(zid, zid), "entity": light,
                            "by": o.get("by"), "since": o.get("since")})
        return out

    def zone_control(self, zone_id: str) -> str:
        if self.keeper.held(zone_id):
            return "held"
        return "by_hand" if self.keeper.overridden(zone_id) else "auto"

    def _occupancy(self, sensors: list[str]) -> tuple[str | None, str | None]:
        """("occupied" | "vacant" | "unknown", since when) from the zone's occupancy sensors.
        Occupied since the earliest sensor still on; vacant since the last one went off."""
        states = [self.hass.states.get(s) for s in sensors]
        if not states:
            return None, None
        if any(st is not None and st.state == "on" for st in states):
            return "occupied", min(st.last_changed for st in states if st is not None and st.state == "on").isoformat()
        if all(st is not None and st.state == "off" for st in states):
            return "vacant", max(st.last_changed for st in states).isoformat()
        return "unknown", None

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
        picked["occupancy"], picked["occupancy_since"] = self._occupancy(b.entities.get("occupancy", []) if b else [])
        ov = self._overlays.get(zone_id)
        picked["overlay"] = {"entity": ov.entity, "state": ov.state, "holding": ov.holding} if ov else None
        picked["hold"] = self.keeper.held(zone_id)
        picked["overrides"] = self.keeper.overridden(zone_id)
        return picked

    # ── the active mode ──────────────────────────────────────────────────────
    @callback
    def restore_mode(self, last: str | None, since: str | None = None, manual: dict | None = None,
                     previous: str | None = None) -> None:
        """At startup (and after a table reload): the last mode, when it began and any manual hold,
        then catch up with the schedule — HA may have been down across a scheduled change."""
        if last in self.mode_ids:
            self.mode, self.mode_since = last, since or dt_util.utcnow().isoformat()
        else:
            self.mode, self.mode_since = self.fallback, dt_util.utcnow().isoformat()
        self.manual = manual if isinstance(manual, dict) and manual.get("hold") in HOLDS else None
        self.prev_mode = previous if previous in self.mode_ids else None
        self.keeper.restored(self.mode)
        if rule_day_sets(self.config) and not self._day_sets:
            self._calendar_wait = True                   # catch up once the calendars are read
        else:
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
        return scheduled_mode(self.config, dt_util.now(), self.in_set, self.sun)

    @property
    def upcoming(self) -> tuple[str | None, Any]:
        return next_change(self.config, dt_util.now(), self.in_set, self.sun)

    @callback
    def _switch(self, mode_id: str, source: str, restart: bool = True) -> bool:
        """Make mode_id the active mode; False when it already is."""
        if mode_id == self.mode:
            return False
        old, self.mode = self.mode, mode_id
        self.prev_mode = old
        self.mode_since = dt_util.utcnow().isoformat()
        self.keeper.mode_changed()
        self.hass.bus.async_fire(EVENT_MODE_CHANGED, {"from": old, "to": mode_id, "source": source})
        if restart:
            self._restart_behaviours()
        self._apply_on_enter(mode_id)
        return True

    @callback
    def _apply_on_enter(self, mode_id: str) -> None:
        """Each zone's `on_enter` targets for the mode that just began — once, on the change
        itself (never on a restart or reload). Held zones are skipped; hand changes have just
        ended with the mode change, as they always do."""
        if not self.ready:
            return
        for zone in self.zones():
            targets = (zone.get("on_enter") or {}).get(mode_id)
            if targets:
                self.apply(targets, f"{zone['id']}: {self.mode_name(mode_id)} begins", zone["id"])

    @callback
    def _catch_up(self, source: str = "schedule", restart: bool = True) -> bool:
        """Follow the schedule unless a manual hold still stands. True when the mode changed."""
        if self._calendar_wait:
            return False
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
        if self.ready and not self._followers and not self._overlays:
            self._restart_followers()
        if self.ready and self.mode:
            for zone in self.zones():
                self._start_zone(zone)
        self._rewatch()
        self.notify()

    @callback
    def _start_zone(self, zone: dict) -> None:
        picked = resolve(self.config, self.mode, zone["id"]) or {}
        ov = self._overlays.get(zone["id"])
        if self.keeper.held(zone["id"]):
            kind = Held
        elif ov is not None and ov.holding:
            kind = Overlaid
        else:
            kind = KINDS.get(picked.get("behaviour") or "", HandsOff)
        b = kind(self, zone, picked.get("params") or {}, zone_entities(self.hass, zone.get("areas") or [], zone.get("lights")))
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
        """Watch the lights of every zone whose behaviour acts, for changes made by hand; and
        every zone's occupancy sensors, so 'vacant since' / 'occupied since' stay current."""
        if self._occ_unsub:
            self._occ_unsub()
            self._occ_unsub = None
        occ = sorted({s for b in self._behaviours.values() for s in b.entities.get("occupancy", [])})
        if occ:
            self._occ_unsub = async_track_state_change_event(self.hass, occ, self._occupancy_changed)
        watch = {}
        for zid, b in self._behaviours.items():
            if b.name in ("hands_off", "held"):
                continue                                 # (an overlay-held zone IS watched)
            picked = resolve(self.config, self.mode, zid) or {}
            watch[zid] = {"lights": b.entities.get("lights", []), "occupancy": b.entities.get("occupancy", []),
                          "delay": release_delay(picked.get("behaviour"), picked.get("params") or {})}
        self.keeper.watch(watch)

    def targets_of(self, ctx_id: str | None) -> frozenset[str]:
        """The entities one of our own commands set (empty for anyone else's context)."""
        return self._ctx_targets.get(ctx_id or "", frozenset())

    def overlay_of(self, zone_id: str) -> Overlay | None:
        return self._overlays.get(zone_id)

    @callback
    def _restart_followers(self) -> None:
        """Followers and overlays: zone-level, independent of the mode's behaviour."""
        for f in self._followers.values():
            f.stop()
        self._followers.clear()
        for o in self._overlays.values():
            o.stop()
        self._overlays.clear()
        if not self.ready:
            return
        for zone in self.zones():
            if zone.get("overlay"):
                o = Overlay(self, zone, zone["overlay"])
                self._overlays[zone["id"]] = o
                o.start()
        for zone in self.zones():
            if zone.get("follow"):
                f = Follower(self, zone, zone["follow"])
                self._followers[zone["id"]] = f
                f.start()

    @callback
    def _occupancy_changed(self, _event) -> None:
        self.notify()

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
        self._ctx_targets[ctx.id] = frozenset(targets)
        if len(self._ctx_targets) > 500:                 # keep it as bounded as own_contexts
            for old in list(self._ctx_targets)[:-500]:
                self._ctx_targets.pop(old, None)
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
