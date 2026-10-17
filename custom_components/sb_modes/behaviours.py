"""The behaviours a zone can run while a mode is active.

One Behaviour object per zone, created when the mode (or the table) changes and stopped
when it changes again. Leaving a mode does nothing to the devices: the next mode's
behaviour decides what happens from there.

- hands_off              does nothing.
- static                 sets its targets once on entering the mode.
- nightlight_then_dark   someone is detected while every zone light is off → the
                         nightlight targets; every occupancy sensor off for `delay`
                         minutes → every zone light off, once per vacancy. Entering the
                         mode with the zone already vacant that long goes dark at once.
                         An unavailable sensor counts as not vacant.
- presence_lux           not built yet (step 4); runs as hands_off.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HassJob, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from .runtime import Runtime

_LOGGER = logging.getLogger(__name__)


class Behaviour:
    name = "hands_off"

    def __init__(self, rt: Runtime, zone: dict, params: dict, entities: dict[str, list[str]]) -> None:
        self.rt = rt
        self.hass = rt.hass
        self.zone = zone
        self.params = params
        self.entities = entities
        self.status = "idle"
        self._unsubs: list[CALLBACK_TYPE] = []

    @callback
    def start(self) -> None:
        """Begin; subclasses listen and act from here."""

    @callback
    def stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    @callback
    def _set_status(self, status: str) -> None:
        if status != self.status:
            self.status = status
            self.rt.notify()

    @callback
    def _apply(self, targets: dict[str, dict], status: str) -> None:
        self._set_status(status)
        self.rt.apply(targets, f"{self.zone['id']}: {status}")


class HandsOff(Behaviour):
    name = "hands_off"


class NotBuilt(Behaviour):
    name = "presence_lux"

    @callback
    def start(self) -> None:
        self._set_status("not built yet; does nothing")


class Static(Behaviour):
    name = "static"

    @callback
    def start(self) -> None:
        self._apply(self.params.get("targets") or {}, "applied")


class NightlightThenDark(Behaviour):
    name = "nightlight_then_dark"

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self._timer: CALLBACK_TYPE | None = None
        self._dark_done = False
        self._any_on = False

    @property
    def _sensors(self) -> list[str]:
        return self.entities.get("occupancy", [])

    @property
    def _lights(self) -> list[str]:
        return self.entities.get("lights", [])

    @callback
    def start(self) -> None:
        self._unsubs.append(async_track_state_change_event(self.hass, self._sensors, self._sensor_changed))
        self._any_on = self._occupied()
        if self._any_on:
            self._set_status("occupied")
        else:
            self._arm(since=self._vacant_since())

    @callback
    def stop(self) -> None:
        self._disarm()
        super().stop()

    def _occupied(self) -> bool:
        return any(self.hass.states.is_state(s, STATE_ON) for s in self._sensors)

    def _vacant(self) -> bool:
        states = [self.hass.states.get(s) for s in self._sensors]
        return bool(states) and all(st is not None and st.state == STATE_OFF for st in states)

    def _vacant_since(self):
        """When the last sensor went off (None unless every one is off)."""
        if not self._vacant():
            return None
        return max(self.hass.states.get(s).last_changed for s in self._sensors)

    @callback
    def _sensor_changed(self, event: Event[EventStateChangedData]) -> None:
        any_on = self._occupied()
        if any_on and not self._any_on:                  # someone arrived
            self._disarm()
            self._dark_done = False
            nightlight = self.params.get("nightlight") or {}
            if nightlight and not any(self.hass.states.is_state(light, STATE_ON) for light in self._lights):
                self._apply(nightlight, "nightlight")
            else:
                self._set_status("occupied")
        self._any_on = any_on
        if not any_on:
            self._arm(since=self._vacant_since())

    @callback
    def _arm(self, since) -> None:
        """Go dark `delay` minutes after `since` (None: not vacant, nothing to arm)."""
        self._disarm()
        if since is None or self._dark_done:
            if since is None and not self._any_on:
                self._set_status("waiting for the sensors")
            return
        left = (since + timedelta(minutes=self.params.get("delay", 10)) - dt_util.utcnow()).total_seconds()
        if left <= 0:
            self._go_dark()
            return
        self._timer = async_call_later(self.hass, left, HassJob(self._timer_done, cancel_on_shutdown=True))
        self._set_status("vacant; dark at " + dt_util.as_local(dt_util.utcnow() + timedelta(seconds=left)).strftime("%H:%M"))

    @callback
    def _disarm(self) -> None:
        if self._timer is not None:
            self._timer()
            self._timer = None

    @callback
    def _timer_done(self, _now) -> None:
        self._timer = None
        if self._vacant():
            self._go_dark()

    @callback
    def _go_dark(self) -> None:
        self._dark_done = True
        self._apply({light: {"state": STATE_OFF} for light in self._lights},
                    "dark since " + dt_util.as_local(dt_util.utcnow()).strftime("%H:%M"))


KINDS: dict[str, type[Behaviour]] = {
    "hands_off": HandsOff,
    "static": Static,
    "nightlight_then_dark": NightlightThenDark,
    "presence_lux": NotBuilt,
}
