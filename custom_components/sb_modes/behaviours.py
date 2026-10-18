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
- vacancy_off            lights off when the zone has been vacant a while (and optionally on
                         when someone arrives) — the old "Lights - Occupancy" automations.
- occupied_vacant        one set of targets while occupied, another once vacant — the old
                         occupancy scene toggle (the Office).
- presence_lux           occupied and darker than target_lux → occupied targets; vacant a while →
                         vacant targets (e.g. a nightlight). The reading only turns lights up.

A light changed by hand is skipped by every behaviour until the zone has been vacant for its
delay or the mode changes; a zone held by hand runs Held (nothing is sent). bookkeeping.py.
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
        self.rt.apply(targets, f"{self.zone['id']}: {status}", self.zone["id"])


class HandsOff(Behaviour):
    name = "hands_off"


class Held(Behaviour):
    """A zone a person holds: nothing is sent; the status says until when."""
    name = "held"

    @callback
    def start(self) -> None:
        from .bookkeeping import describe_hold
        self._set_status(describe_hold(self.rt.keeper.held(self.zone["id"])) or "held by hand")


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
        self.rt.keeper.release_overrides(self.zone["id"])   # vacant for the delay: hand changes end
        self._apply({light: {"state": STATE_OFF} for light in self._lights},
                    "dark since " + dt_util.as_local(dt_util.utcnow()).strftime("%H:%M"))


class _Occupancy(Behaviour):
    """Shared: the zone's occupancy sensors. Any on = occupied; all off = vacant; an
    unavailable sensor makes the zone neither (nothing is done on its say-so)."""

    @property
    def _sensors(self) -> list[str]:
        return self.entities.get("occupancy", [])

    def _occupied(self) -> bool:
        return any(self.hass.states.is_state(s, STATE_ON) for s in self._sensors)

    def _vacant_since(self):
        states = [self.hass.states.get(s) for s in self._sensors]
        if not states or not all(st is not None and st.state == STATE_OFF for st in states):
            return None
        return max(st.last_changed for st in states)


class VacancyOff(_Occupancy):
    """Lights off once the zone has been vacant `off_after_seconds` (counted from when it
    emptied or the light came on, whichever is later), or once a light has been on
    `max_on_minutes` while the zone is vacant. Optionally on when someone arrives."""
    name = "vacancy_off"

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self._timer: CALLBACK_TYPE | None = None
        self._was_occupied = False

    @property
    def _lights(self) -> list[str]:
        return list(self.params.get("lights") or [])

    @callback
    def start(self) -> None:
        self._unsubs.append(async_track_state_change_event(self.hass, self._sensors + self._lights, self._changed))
        self._was_occupied = self._occupied()
        self._evaluate()

    @callback
    def stop(self) -> None:
        self._cancel()
        super().stop()

    @callback
    def _cancel(self) -> None:
        if self._timer is not None:
            self._timer()
            self._timer = None

    @callback
    def _changed(self, event: Event[EventStateChangedData]) -> None:
        occupied = self._occupied()
        if occupied and not self._was_occupied and self.params.get("on_when_occupied"):
            self._apply({light: {"state": STATE_ON} for light in self._lights}, "on: someone came in")
        self._was_occupied = occupied
        self._evaluate()

    def _due(self, light: str, vacant_since):
        """When this light should go off (None: it is off, or the zone is not vacant)."""
        st = self.hass.states.get(light)
        if st is None or st.state != STATE_ON or vacant_since is None:
            return None
        due = max(vacant_since, st.last_changed) + timedelta(seconds=self.params.get("off_after_seconds", 60))
        if max_on := self.params.get("max_on_minutes"):
            due = min(due, max(vacant_since, st.last_changed + timedelta(minutes=max_on)))
        return due

    @callback
    def _evaluate(self) -> None:
        self._cancel()
        vacant_since = self._vacant_since()
        now = dt_util.utcnow()
        dues = {light: d for light in self._lights if (d := self._due(light, vacant_since)) is not None}
        if self._occupied():
            self._set_status("occupied")
            return
        if vacant_since is None:
            self._set_status("waiting for the sensors" if self._sensors else "no occupancy sensor")
            return
        if not dues:
            self._set_status("vacant; lights off")
            return
        now_off = [light for light, d in dues.items() if d <= now]
        if now_off:
            self.rt.keeper.release_overrides(self.zone["id"])     # vacant long enough: hand changes end
            self._apply({light: {"state": STATE_OFF} for light in now_off}, "off: vacant")
            later = {k: v for k, v in dues.items() if k not in now_off}
            if not later:
                return
            dues = later
        nxt = min(dues.values())
        self._timer = async_call_later(self.hass, max(1, (nxt - now).total_seconds()),
                                       HassJob(self._timer_done, cancel_on_shutdown=True))
        self._set_status("vacant; off at " + dt_util.as_local(nxt).strftime("%H:%M:%S"))

    @callback
    def _timer_done(self, _now) -> None:
        self._timer = None
        self._evaluate()


class OccupiedVacant(_Occupancy):
    """`occupied` targets when someone is in the zone; `vacant` targets once it has been
    vacant `vacant_after_minutes`. On entering the mode, whichever applies now."""
    name = "occupied_vacant"

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self._timer: CALLBACK_TYPE | None = None
        self._was_occupied: bool | None = None

    @callback
    def start(self) -> None:
        self._unsubs.append(async_track_state_change_event(self.hass, self._sensors, self._changed))
        self._evaluate(first=True)

    @callback
    def stop(self) -> None:
        if self._timer is not None:
            self._timer()
            self._timer = None
        super().stop()

    @callback
    def _changed(self, _event) -> None:
        self._evaluate()

    @callback
    def _evaluate(self, first: bool = False) -> None:
        if self._timer is not None:
            self._timer()
            self._timer = None
        occupied = self._occupied()
        if occupied:
            if first or not self._was_occupied:
                self._apply(self.params.get("occupied") or {}, "occupied")
            else:
                self._set_status("occupied")
            self._was_occupied = True
            return
        self._was_occupied = False
        since = self._vacant_since()
        if since is None:
            self._set_status("waiting for the sensors")
            return
        due = since + timedelta(minutes=self.params.get("vacant_after_minutes", 0))
        now = dt_util.utcnow()
        if due <= now:
            self.rt.keeper.release_overrides(self.zone["id"])
            self._apply(self.params.get("vacant") or {}, "vacant")
            return
        self._timer = async_call_later(self.hass, (due - now).total_seconds(), HassJob(self._timer_done, cancel_on_shutdown=True))
        self._set_status("vacant; switching at " + dt_util.as_local(due).strftime("%H:%M"))

    @callback
    def _timer_done(self, _now) -> None:
        self._timer = None
        self._evaluate(first=True)


class PresenceLux(_Occupancy):
    """Someone in the zone and the light reading below `target_lux` → the `occupied` targets;
    vacant `vacant_after_minutes` → the `vacant` targets (default: the occupied ones off).

    The reading only ever turns lights UP while the zone is occupied (sunset); it never
    dims them — the zone's own lights would fool it. Lit stays lit until the zone empties."""
    name = "presence_lux"

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self._timer: CALLBACK_TYPE | None = None
        self._lit = False

    @property
    def _lux_sensors(self) -> list[str]:
        return self.entities.get("illuminance", [])

    def _lux(self) -> float | None:
        """The zone's light reading: the mean of its illuminance sensors that report a number."""
        vals = []
        for eid in self._lux_sensors:
            try:
                vals.append(float(self.hass.states.get(eid).state))
            except (AttributeError, TypeError, ValueError):
                continue
        return sum(vals) / len(vals) if vals else None

    def _vacant_targets(self) -> dict:
        vacant = self.params.get("vacant")
        if vacant:
            return vacant
        return {e: {"state": STATE_OFF} for e in self.params.get("occupied") or {}}

    @callback
    def start(self) -> None:
        self._unsubs.append(async_track_state_change_event(self.hass, self._sensors + self._lux_sensors, self._changed))
        self._evaluate()

    @callback
    def stop(self) -> None:
        self._cancel()
        super().stop()

    @callback
    def _cancel(self) -> None:
        if self._timer is not None:
            self._timer()
            self._timer = None

    @callback
    def _changed(self, _event) -> None:
        self._evaluate()

    @callback
    def _evaluate(self) -> None:
        self._cancel()
        target = self.params.get("target_lux")
        if self._occupied():
            lux = self._lux()
            dark = lux is None or lux < target
            if not self._lit and dark:
                self._lit = True
                self._apply(self.params.get("occupied") or {}, f"on: {'no reading' if lux is None else f'{lux:.0f} lx'} < {target} lx")
            elif self._lit:
                self._set_status("on")
            else:
                self._set_status(f"occupied; light enough ({lux:.0f} ≥ {target} lx)")
            return
        since = self._vacant_since()
        if since is None:
            self._set_status("waiting for the sensors")
            return
        due = since + timedelta(minutes=self.params.get("vacant_after_minutes", 15))
        now = dt_util.utcnow()
        if due <= now:
            self._lit = False
            self.rt.keeper.release_overrides(self.zone["id"])
            self._apply(self._vacant_targets(), "empty")
            return
        self._timer = async_call_later(self.hass, (due - now).total_seconds(), HassJob(self._timer_done, cancel_on_shutdown=True))
        self._set_status("vacant; empty setting at " + dt_util.as_local(due).strftime("%H:%M"))

    @callback
    def _timer_done(self, _now) -> None:
        self._timer = None
        self._evaluate()


KINDS: dict[str, type[Behaviour]] = {
    "vacancy_off": VacancyOff,
    "occupied_vacant": OccupiedVacant,
    "hands_off": HandsOff,
    "static": Static,
    "nightlight_then_dark": NightlightThenDark,
    "presence_lux": PresenceLux,
}
