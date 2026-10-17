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
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import driver
from .behaviours import KINDS, Behaviour, HandsOff
from .const import (
    ALERT_GRACE_SECONDS, CHECK_SECONDS, DOMAIN, EVENT_MODE_CHANGED, ISSUE_ID, STARTUP_SETTLE_SECONDS, STORE_KEY,
    STORE_VERSION,
)
from .model import default_config, find, mode_ids, resolve
from .validate import check_config
from .zones import zone_entities

_LOGGER = logging.getLogger(__name__)


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
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.ready = False                                # behaviours may run
        self.own_contexts: deque[str] = deque(maxlen=500)  # ids of the contexts of our own calls
        self._behaviours: dict[str, Behaviour] = {}
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
        self._cancel_grace()
        self._stop_behaviours()

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

    def zone_state(self, zone_id: str) -> dict | None:
        """What the zone runs now, for its sensor: the resolved choice plus live status."""
        picked = resolve(self.config, self.mode, zone_id) if self.mode else None
        if picked is None:
            return None
        b = self._behaviours.get(zone_id)
        picked["status"] = b.status if b else "waiting for start-up"
        picked["entities"] = b.entities if b else {}
        return picked

    # ── the active mode ──────────────────────────────────────────────────────
    @callback
    def restore_mode(self, last: str | None, since: str | None = None) -> None:
        """At startup: the last mode (and when it began) if it still exists, else the fallback."""
        if last in self.mode_ids:
            self.mode, self.mode_since = last, since or dt_util.utcnow().isoformat()
        else:
            self.mode, self.mode_since = self.fallback, dt_util.utcnow().isoformat()
        self._restart_behaviours()

    @callback
    def set_mode(self, mode_id: str) -> None:
        if mode_id not in self.mode_ids:
            raise ValueError(f"“{mode_id}” is not a defined mode.")
        if mode_id == self.mode:
            return
        old, self.mode = self.mode, mode_id
        self.mode_since = dt_util.utcnow().isoformat()
        self.hass.bus.async_fire(EVENT_MODE_CHANGED, {"from": old, "to": mode_id})
        self._restart_behaviours()

    # ── behaviours ───────────────────────────────────────────────────────────
    @callback
    def _restart_behaviours(self) -> None:
        self._stop_behaviours()
        if self.ready and self.mode:
            for zone in self.zones():
                picked = resolve(self.config, self.mode, zone["id"]) or {}
                kind = KINDS.get(picked.get("behaviour") or "", HandsOff)
                b = kind(self, zone, picked.get("params") or {}, zone_entities(self.hass, zone.get("areas") or []))
                self._behaviours[zone["id"]] = b
                b.start()
        self.notify()

    @callback
    def _stop_behaviours(self) -> None:
        for b in self._behaviours.values():
            b.stop()
        self._behaviours.clear()

    @callback
    def apply(self, targets: dict[str, dict], why: str) -> None:
        """Send targets through the driver, under a context remembered as our own."""
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
