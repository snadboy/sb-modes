"""Overlays: temporary context that sets a zone's lights from another entity's state — the TV.

A zone's `overlay` = {
    "entity": "media_player.family_room_tv",
    "states": {"playing": {"targets": {…}, "holds": true},     # set, and hold the zone while in it
               "paused":  {"targets": {…}, "holds": true},
               "idle":    {"targets": {…}},                    # set once; the zone's behaviour resumes
               "off":     {"targets": {…}}},
    "debounce_seconds": 5,                # a state must last this long before it counts
    "unavailable_cooldown_seconds": 60,   # after the entity was unavailable, ignore its changes this long
}
States not listed (buffering…) are ignored, but still restart the debounce. A state reached straight from `unavailable` sets nothing (the
entity coming back is not someone turning the TV off), but still ends a hold.

While the entity is in a watching state (playing / paused / idle) it also counts as presence
in the zone (presence.py) — unless the overlay says "presence": false.

Precedence: a zone hold beats the overlay (Runtime.apply sends nothing to a held zone); the
overlay beats the zone's behaviour while it holds (the behaviour is stopped, as for a hold);
lights changed by hand stay as set (Runtime.apply skips them).
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HassJob, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .presence import MEDIA_WATCHING

if TYPE_CHECKING:
    from .runtime import Runtime

UNAVAILABLE = ("unavailable", "unknown")


class Overlay:
    def __init__(self, rt: Runtime, zone: dict, spec: dict) -> None:
        self.rt = rt
        self.zone_id = zone["id"]
        self.entity = spec["entity"]
        self.states = spec.get("states") or {}
        self.debounce = spec.get("debounce_seconds", 5)
        self.cooldown = spec.get("unavailable_cooldown_seconds", 60)
        self.holding = False
        self.state: str | None = None
        self._last_unavailable = None
        self._quiet = False                              # set while restarting from 'unavailable'
        self._pending: CALLBACK_TYPE | None = None
        self._unsub: CALLBACK_TYPE | None = None

    @callback
    def start(self) -> None:
        st = self.rt.hass.states.get(self.entity)
        self.state = st.state if st else None
        # at start, only learn whether the zone is held — set nothing (the lights already are)
        self.holding = bool((self.states.get(self.state) or {}).get("holds"))
        self._unsub = async_track_state_change_event(self.rt.hass, [self.entity], self._changed)

    @callback
    def apply_current(self) -> None:
        """Set the current state's targets (the zone's Overlaid behaviour calls this on start)."""
        cfg = self.states.get(self.state) or {}
        if cfg.get("targets") and not self._quiet:
            st = self.rt.hass.states.get(self.entity)
            self.rt.apply(cfg["targets"], f"{st.name if st else self.entity} {self.state}", self.zone_id)

    @callback
    def stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        self._cancel()

    @callback
    def _cancel(self) -> None:
        if self._pending:
            self._pending()
            self._pending = None

    @callback
    def _changed(self, event: Event[EventStateChangedData]) -> None:
        new, old = event.data["new_state"], event.data["old_state"]
        if new is not None and old is not None and old.state in MEDIA_WATCHING and new.state not in MEDIA_WATCHING:
            self.rt.media_left[self.entity] = new.last_changed    # stopped being watched (presence)
        if new is None or (old is not None and old.state == new.state):
            return                                       # attributes only (media position…): not a change
        self._cancel()
        if new.state in UNAVAILABLE:
            self._last_unavailable = dt_util.utcnow()
            return
        from_unavailable = old is not None and old.state in UNAVAILABLE
        self._pending = async_call_later(self.rt.hass, self.debounce,
                                         HassJob(partial(self._settled, new.state, from_unavailable), cancel_on_shutdown=True))

    @callback
    def _settled(self, state: str, from_unavailable: bool, _now=None) -> None:
        self._pending = None
        current = self.rt.hass.states.get(self.entity)
        if current is None or current.state != state:
            return                                       # it did not last the debounce
        cfg = self.states.get(state)
        if self._last_unavailable and (dt_util.utcnow() - self._last_unavailable).total_seconds() < self.cooldown:
            if cfg is not None:                          # still in the cool-down after a drop-out
                self.rt.log("skipped", self.zone_id, f"{current.name} {state} — ignored: under {self.cooldown:g} s since it dropped out")
            return
        if cfg is None:
            return
        self.state = state
        was = self.holding
        self.holding = bool(cfg.get("holds"))
        if self.holding:
            # the zone's Overlaid behaviour sets the targets as it (re)starts
            self._quiet = from_unavailable
            try:
                self.rt.restart_zone(self.zone_id)
            finally:
                self._quiet = False
            return
        if cfg.get("targets") and not from_unavailable:
            self.rt.apply(cfg["targets"], f"{current.name} {state}", self.zone_id)
        if was:
            self.rt.restart_zone(self.zone_id)           # back to the zone's own behaviour
        else:
            self.rt.notify()
