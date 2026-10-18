"""Bookkeeping: lights changed by hand, and zones held by hand. Saved, so a restart keeps them.

OVERRIDES — a zone light changed by hand (see manual.py for what counts) is left alone by
every behaviour until whichever comes first:
  - the zone has been vacant for its delay (the behaviour's `delay` / `off_delay`, else 10 min);
  - the mode changes;
  - someone releases the zone.

HOLDS — a person holds a zone: its behaviour stops (nothing is sent to its lights) until
  - `minutes` have passed, or the mode changes ("next"), or someone releases it ("paused").
Releasing a zone ends its hold and its overrides, and its behaviour starts again for the
current mode, as if just entered.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_OFF
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HassJob, callback
from homeassistant.helpers.event import async_call_later, async_track_point_in_time, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .manual import classify, significant

if TYPE_CHECKING:
    from .runtime import Runtime

_LOGGER = logging.getLogger(__name__)
DEFAULT_RELEASE_MINUTES = 10


class Keeper:
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt
        self.hass = rt.hass
        self.store: Store = Store(rt.hass, 1, f"{DOMAIN}.state")
        self.holds: dict[str, dict] = {}                 # zone → {hold, since, until, mode}
        self.overrides: dict[str, dict[str, dict]] = {}  # zone → light → {since, by, mode}
        self._last_cmd: dict[str, float] = {}            # light → monotonic time of our last command
        self._zone_of: dict[str, str] = {}               # watched light → zone
        self._watch: dict[str, dict] = {}                # zone → {lights, occupancy, delay}
        self._unsubs: list[CALLBACK_TYPE] = []
        self._expiry: dict[str, CALLBACK_TYPE] = {}      # zone → hold expiry timer
        self._vacant: dict[str, CALLBACK_TYPE] = {}      # zone → override release timer

    # ── storage ──────────────────────────────────────────────────────────────
    async def async_load(self) -> None:
        # A reload (table saved) hands the state over in memory: the delayed disk write of the
        # keeper being replaced may not have landed yet.
        data = self.hass.data.get(DOMAIN, {}).pop("keeper_handover", None) or await self.store.async_load() or {}
        self.holds = data.get("holds") or {}
        self.overrides = data.get("overrides") or {}

    @callback
    def _save(self) -> None:
        self.store.async_delay_save(lambda: {"holds": self.holds, "overrides": self.overrides}, 2)

    @callback
    def restored(self, mode: str | None) -> None:
        """After a restart: drop what has lapsed — expired timed holds, and holds/overrides
        tied to a mode the house is no longer in."""
        now = dt_util.utcnow()
        for zone, h in list(self.holds.items()):
            if (h["hold"] == "timed" and dt_util.parse_datetime(h["until"]) <= now) or \
               (h["hold"] == "next" and h.get("mode") != mode):
                self.holds.pop(zone)
        for zone, lights in list(self.overrides.items()):
            for light, o in list(lights.items()):
                if o.get("mode") != mode:
                    lights.pop(light)
            if not lights:
                self.overrides.pop(zone)
        for zone in self.holds:
            self._arm_expiry(zone)
        self._save()

    @callback
    def stop(self) -> None:
        self.hass.data.setdefault(DOMAIN, {})["keeper_handover"] = {"holds": self.holds, "overrides": self.overrides}
        self._unwatch()
        for timers in (self._expiry, self._vacant):
            for unsub in timers.values():
                unsub()
            timers.clear()

    # ── queries ──────────────────────────────────────────────────────────────
    def held(self, zone: str) -> dict | None:
        return self.holds.get(zone)

    def overridden(self, zone: str) -> dict[str, dict]:
        return self.overrides.get(zone, {})

    @callback
    def commanded(self, lights) -> None:
        now = time.monotonic()
        for light in lights:
            self._last_cmd[light] = now

    # ── watching zone lights for manual changes ──────────────────────────────
    @callback
    def watch(self, zones: dict[str, dict]) -> None:
        """zones: zone → {lights, occupancy, delay} for zones whose behaviour acts (not held,
        not hands_off). Replaces whatever was watched before."""
        self._unwatch()
        self._watch = zones
        self._zone_of = {light: z for z, info in zones.items() for light in info["lights"]}
        if self._zone_of:
            self._unsubs.append(async_track_state_change_event(self.hass, list(self._zone_of), self._light_changed))
        occ = sorted({s for info in zones.values() for s in info["occupancy"]})
        if occ:
            self._unsubs.append(async_track_state_change_event(self.hass, occ, self._occupancy_changed))
        for zone in list(self.overrides):
            self._arm_release(zone)

    @callback
    def _unwatch(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    @callback
    def _light_changed(self, event: Event[EventStateChangedData]) -> None:
        light = event.data["entity_id"]
        old, new = event.data["old_state"], event.data["new_state"]
        zone = self._zone_of.get(light)
        if zone is None or new is None or old is None:
            return
        if not significant(old.state, new.state, old.attributes.get("brightness"), new.attributes.get("brightness")):
            return
        last = self._last_cmd.get(light)
        by = classify(new.context.id, new.context.user_id, new.context.parent_id, set(self.rt.own_contexts),
                      None if last is None else time.monotonic() - last)
        if by is None:
            return
        self.overrides.setdefault(zone, {})[light] = {"since": dt_util.utcnow().isoformat(), "by": by, "mode": self.rt.mode}
        _LOGGER.debug("SB Modes: %s changed by hand (%s) in %s", light, by, zone)
        self._save()
        self._arm_release(zone)
        self.rt.notify()

    # ── releasing overrides when the zone has been vacant for its delay ──────
    @callback
    def _occupancy_changed(self, _event) -> None:
        for zone in list(self.overrides):
            self._arm_release(zone)

    @callback
    def _arm_release(self, zone: str) -> None:
        if (unsub := self._vacant.pop(zone, None)) is not None:
            unsub()
        info = self._watch.get(zone)
        if not self.overrides.get(zone) or not info or not info["occupancy"]:
            return                                       # no sensors: held until the mode changes
        states = [self.hass.states.get(s) for s in info["occupancy"]]
        if not all(st is not None and st.state == STATE_OFF for st in states):
            return                                       # occupied (or a sensor unavailable)
        since = max(st.last_changed for st in states)
        left = (since + timedelta(minutes=info["delay"]) - dt_util.utcnow()).total_seconds()
        if left <= 0:
            self.release_overrides(zone)
            return
        self._vacant[zone] = async_call_later(self.hass, left, HassJob(partial(self._release_due, zone), cancel_on_shutdown=True))

    @callback
    def _release_due(self, zone: str, _now=None) -> None:
        self._vacant.pop(zone, None)
        self._arm_release(zone)                          # re-checks vacancy; releases when due

    @callback
    def release_overrides(self, zone: str) -> None:
        if self.overrides.pop(zone, None) is not None:
            if (unsub := self._vacant.pop(zone, None)) is not None:
                unsub()
            self._save()
            self.rt.notify()

    # ── holds ────────────────────────────────────────────────────────────────
    @callback
    def hold(self, zone: str, hold: str | int) -> None:
        now = dt_util.utcnow()
        if isinstance(hold, int) and not isinstance(hold, bool):
            self.holds[zone] = {"hold": "timed", "since": now.isoformat(),
                                "until": (now + timedelta(minutes=hold)).isoformat(), "mode": self.rt.mode}
        elif hold in ("next", "paused"):
            self.holds[zone] = {"hold": hold, "since": now.isoformat(), "until": None, "mode": self.rt.mode}
        else:
            raise ValueError(f"Unknown hold {hold!r}: next, paused or a number of minutes.")
        self.overrides.pop(zone, None)
        self._arm_expiry(zone)
        self._save()
        self.rt.restart_zone(zone)

    @callback
    def release(self, zone: str) -> None:
        """End the zone's hold and overrides; its behaviour starts again for the current mode."""
        self.holds.pop(zone, None)
        self.overrides.pop(zone, None)
        for timers in (self._expiry, self._vacant):
            if (unsub := timers.pop(zone, None)) is not None:
                unsub()
        self._save()
        self.rt.restart_zone(zone)

    @callback
    def _arm_expiry(self, zone: str) -> None:
        if (unsub := self._expiry.pop(zone, None)) is not None:
            unsub()
        h = self.holds.get(zone)
        if h and h["hold"] == "timed":
            self._expiry[zone] = async_track_point_in_time(
                self.hass, partial(self._expired, zone), dt_util.parse_datetime(h["until"]))

    @callback
    def _expired(self, zone: str, _now=None) -> None:
        self._expiry.pop(zone, None)
        self.release(zone)

    @callback
    def mode_changed(self) -> None:
        """A new mode: overrides end, and so do holds 'until the next mode change'."""
        changed = bool(self.overrides)
        self.overrides.clear()
        for unsub in self._vacant.values():
            unsub()
        self._vacant.clear()
        for zone, h in list(self.holds.items()):
            if h["hold"] == "next":
                self.holds.pop(zone)
                if (unsub := self._expiry.pop(zone, None)) is not None:
                    unsub()
                changed = True
        if changed:
            self._save()


def describe_hold(h: dict | None) -> str | None:
    """The zone's status line while held."""
    if not h:
        return None
    if h["hold"] == "timed":
        return "held by hand until " + dt_util.as_local(dt_util.parse_datetime(h["until"])).strftime("%H:%M")
    if h["hold"] == "next":
        return "held by hand until the next mode change"
    return "held by hand until released"


def release_delay(behaviour: str | None, params: dict[str, Any]) -> float:
    """Minutes a zone must be vacant before its overrides end."""
    for key in ("delay", "off_delay"):
        if isinstance(params.get(key), (int, float)):
            return params[key]
    return DEFAULT_RELEASE_MINUTES

