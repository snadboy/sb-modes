"""Overlays: temporary context that sets a zone's lights from another entity's state — the TV.

A zone's `overlay` = {
    "entity": "media_player.family_room_tv",     # or "entities": [...] — several players of one TV
                                                 # (its own, Plex's…): the most active state counts
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

Turning the entity ON (from off / standby, after it has been off at least the debounce) ends the
zone's hand changes made BEFORE that: lights switched on to walk in are not a wish to watch
in full light. Hand changes made while it is on still win (folding laundry during the show).

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
OFF_STATES = ("off", "standby")
# With several players (the TV's own and an app's, e.g. Plex on the streamer), the overlay follows
# the most active one: a TV whose own player says "off" while Plex plays is playing (2026-10-24).
PRIORITY = ("playing", "paused", "buffering", "idle", "on", "standby", "off")


def combined(states: list[str | None]) -> str:
    """The most active of several players' states; "unavailable" when none reports."""
    real = [x for x in states if x and x not in UNAVAILABLE]
    if not real:
        return "unavailable"
    known = [x for x in real if x in PRIORITY]
    return min(known, key=PRIORITY.index) if known else real[0]


class Overlay:
    def __init__(self, rt: Runtime, zone: dict, spec: dict) -> None:
        self.rt = rt
        self.zone_id = zone["id"]
        self.entities = [e for e in (spec.get("entities") or [spec.get("entity")]) if e]
        self.entity = self.entities[0]                   # the TV's own player: its name in messages
        self.id = spec.get("id", "tv")
        self.name = spec.get("name") or "TV lighting"
        self.when = spec.get("when") or {}
        self.presence = spec.get("presence", True)
        self.active = True                               # its When matches (regate)
        self.states = spec.get("states") or {}
        self.debounce = spec.get("debounce_seconds", 5)
        self.cooldown = spec.get("unavailable_cooldown_seconds", 60)
        self.holding = False
        self.state: str | None = None
        self._last_unavailable = None
        self._quiet = False                              # set while restarting from 'unavailable'
        self._off_since = None                           # when it last went off / standby
        self._turned_on = None                           # when it was turned on, until acted on
        self._pending: CALLBACK_TYPE | None = None
        self._unsub: CALLBACK_TYPE | None = None

    def _combined(self) -> str:
        return combined([(st.state if (st := self.rt.hass.states.get(e)) else None) for e in self.entities])

    def _label(self) -> str:
        st = self.rt.hass.states.get(self.entity)
        return st.name if st else self.entity

    @callback
    def start(self) -> None:
        self.state = self._combined()
        self._prev = self.state
        if self.state in OFF_STATES:
            st = self.rt.hass.states.get(self.entity)
            self._off_since = st.last_changed if st else dt_util.utcnow()   # off already at start (a restart)
        # at start, only learn whether the zone is held — set nothing (the lights already are)
        self.active = self.rt.when_ok(self.when)
        self.holding = self.active and bool((self.states.get(self.state) or {}).get("holds"))
        self._unsub = async_track_state_change_event(self.rt.hass, list(self.entities), self._changed)

    @callback
    def apply_current(self) -> None:
        """Set the current state's targets (the zone's Overlaid behaviour calls this on start)."""
        cfg = self.states.get(self.state) or {}
        if cfg.get("targets") and not self._quiet:
            st = self.rt.hass.states.get(self.entity)
            self.rt.apply(cfg["targets"], f"{st.name if st else self.entity} {self.state}", self.zone_id)

    @callback
    def regate(self, restart: bool = True) -> None:
        """Its When opened or closed (a mode change, a day, a time window): a holding overlay that
        no longer applies hands the room back; one that applies again takes the current state."""
        ok = self.rt.when_ok(self.when)
        if ok == self.active:
            return
        self.active = ok
        cfg = self.states.get(self.state) or {}
        if not ok:
            self.rt.log("hand_end", self.zone_id, f"{self.name}: its When no longer matches — the room's own rule again")
            if self.holding:
                self.holding = False
                if restart:
                    self.rt.restart_zone(self.zone_id)
            return
        self.holding = bool(cfg.get("holds"))
        if self.holding:
            if restart:
                self.rt.restart_zone(self.zone_id)       # Overlaid sets the targets
        elif cfg.get("targets"):
            self.apply_current()

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
        new_st, old_st = event.data["new_state"], event.data["old_state"]
        if new_st is not None and old_st is not None and old_st.state in MEDIA_WATCHING and new_st.state not in MEDIA_WATCHING:
            self.rt.media_left[event.data["entity_id"]] = new_st.last_changed    # stopped being watched (presence)
        new, old = self._combined(), getattr(self, "_prev", None)
        self._prev = new
        if new == old:
            return                                       # attributes only, or another player that doesn't win
        now = dt_util.utcnow()
        if new in OFF_STATES:
            self._off_since = now
            self._turned_on = None
        elif new in MEDIA_WATCHING and old in OFF_STATES and self._off_since is not None \
                and (now - self._off_since).total_seconds() >= self.debounce:
            self._turned_on = now                        # a real turn-on, not a one-second "off" blip
        self._cancel()
        if new in UNAVAILABLE:
            self._last_unavailable = now
            return
        from_unavailable = old in UNAVAILABLE
        self._pending = async_call_later(self.rt.hass, self.debounce,
                                         HassJob(partial(self._settled, new, from_unavailable), cancel_on_shutdown=True))

    @callback
    def _settled(self, state: str, from_unavailable: bool, _now=None) -> None:
        self._pending = None
        if self._combined() != state:
            return                                       # it did not last the debounce
        name = self._label()
        cfg = self.states.get(state)
        if self._last_unavailable and (dt_util.utcnow() - self._last_unavailable).total_seconds() < self.cooldown:
            if cfg is not None:                          # still in the cool-down after a drop-out
                self.rt.log("skipped", self.zone_id, f"{name} {state} — ignored: under {self.cooldown:g} s since it dropped out")
            return
        if cfg is None:
            return
        self.state = state
        if not self.active:
            return                                       # outside its When: noted, nothing done
        was = self.holding
        self.holding = bool(cfg.get("holds"))
        if self._turned_on is not None:
            self.rt.keeper.release_before(self.zone_id, self._turned_on, f"{name} turned on")
            self._turned_on = None
        if self.holding:
            # the zone's Overlaid behaviour sets the targets as it (re)starts
            self._quiet = from_unavailable
            try:
                self.rt.restart_zone(self.zone_id)
            finally:
                self._quiet = False
            return
        if cfg.get("targets") and not from_unavailable:
            self.rt.apply(cfg["targets"], f"{name} {state}", self.zone_id)
        if was:
            self.rt.restart_zone(self.zone_id)           # back to the zone's own behaviour
        else:
            self.rt.notify()
