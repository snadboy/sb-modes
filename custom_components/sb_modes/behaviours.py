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
- wake_sequence          timed steps after the mode began, only when it followed `after_mode`
                         (the bedside lamps waking up after Overnight, weekday or weekend).
- presence_lux           occupied and darker than target_lux → occupied targets; vacant a while →
                         vacant targets (e.g. a nightlight). The reading only turns lights up.

A light changed by hand is skipped by every behaviour until the zone has been vacant for its
delay or the mode changes; a zone held by hand runs Held (nothing is sent). bookkeeping.py.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from functools import partial
from typing import TYPE_CHECKING

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HassJob, callback
from homeassistant.helpers.event import async_call_later, async_track_point_in_time, async_track_state_change_event
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


class Overlaid(Behaviour):
    """A zone an overlay holds (the TV playing or paused): the overlay's setting for that state
    instead of the zone's behaviour. Re-applied whenever the zone restarts under it — after a
    hold or a hand change ends, or a mode change — so it goes back to the TV levels."""
    name = "overlay"

    @callback
    def start(self) -> None:
        ov = self.rt.overlay_of(self.zone["id"])
        st = self.hass.states.get(ov.entity) if ov else None
        self._set_status(f"overlay: {st.name if st else 'overlay'} {ov.state if ov else ''}".strip())
        if ov is not None:
            ov.apply_current()


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
        return self.rt.occupied(self._sensors)

    def _vacant(self) -> bool:
        return self._vacant_since() is not None

    def _vacant_since(self):
        """When the zone emptied (None unless every presence source says no one)."""
        return self.rt.vacant_since(self._sensors)

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
        self._set_status("vacant; goes dark at " + dt_util.as_local(dt_util.utcnow() + timedelta(seconds=left)).strftime("%H:%M"))

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
        self.rt.keeper.release_overrides(self.zone["id"], vacant_since=self._vacant_since())   # vacant for the delay
        self._apply({light: {"state": STATE_OFF} for light in self._lights},
                    "dark since " + dt_util.as_local(dt_util.utcnow()).strftime("%H:%M"))


class _Occupancy(Behaviour):
    """Shared: the zone's occupancy sensors. Any on = occupied; all off = vacant; an
    unavailable sensor makes the zone neither (nothing is done on its say-so)."""

    @property
    def _sensors(self) -> list[str]:
        return self.entities.get("occupancy", [])

    def _occupied(self) -> bool:
        return self.rt.occupied(self._sensors)

    def _vacant_since(self):
        return self.rt.vacant_since(self._sensors)


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
            self.rt.keeper.release_overrides(self.zone["id"], vacant_since=vacant_since)   # vacant long enough
            self._apply({light: {"state": STATE_OFF} for light in now_off}, "off: vacant")
            later = {k: v for k, v in dues.items() if k not in now_off}
            if not later:
                return
            dues = later
        nxt = min(dues.values())
        self._timer = async_call_later(self.hass, max(1, (nxt - now).total_seconds()),
                                       HassJob(self._timer_done, cancel_on_shutdown=True))
        self._set_status("vacant; lights off at " + dt_util.as_local(nxt).strftime("%H:%M"))

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
        self._vacant_done = False

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
            self._vacant_done = False
            if first or not self._was_occupied:
                self._apply(self.params.get("occupied") or {}, "occupied")
            else:
                self._set_status("occupied")
            self._was_occupied = True
            return
        self._was_occupied = False
        if self._vacant_done:
            return                      # applied once for this vacancy
        since = self._vacant_since()
        if since is None:
            self._set_status("waiting for the sensors")
            return
        due = since + timedelta(minutes=self.params.get("vacant_after_minutes", 0))
        now = dt_util.utcnow()
        if due <= now:
            self._vacant_done = True
            self.rt.keeper.release_overrides(self.zone["id"], vacant_since=since)
            self._apply(self.params.get("vacant") or {}, "vacant; 'empty' since " + dt_util.as_local(now).strftime("%H:%M"))
            return
        self._timer = async_call_later(self.hass, (due - now).total_seconds(), HassJob(self._timer_done, cancel_on_shutdown=True))
        self._set_status("vacant; at " + dt_util.as_local(due).strftime("%H:%M") + " switches to 'empty'")

    @callback
    def _timer_done(self, _now) -> None:
        self._timer = None
        self._evaluate(first=True)


class PresenceLux(_Occupancy):
    """Someone in the zone and the light reading below `target_lux` → the `occupied` targets;
    vacant `vacant_after_minutes` → the `vacant` targets when it is dark (default: the occupied
    ones off), or `vacant_bright` when it is light (default: the same as `vacant`).

    The reading only ever turns lights UP while the zone is occupied (sunset); it never
    dims them — the zone's own lights would fool it. Lit stays lit until the zone empties.
    "Dark or light" for the empty setting is judged on DAYLIGHT: the last reading taken while
    none of the zone's occupied-target lights were on (a kitchen sensor sees its ceiling)."""
    name = "presence_lux"

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self._timer: CALLBACK_TYPE | None = None
        self._lit = False
        self._vacant_done = False      # the vacant setting is applied ONCE per vacancy

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

    DAYLIGHT_MAX_AGE = 3600            # seconds a lights-off reading stays usable

    def _own_lights(self) -> list[str]:
        """Every light that can add to this zone's reading: the zone's lights plus our targets."""
        own = set(self.entities.get("lights", []))
        for key in ("occupied", "vacant", "vacant_bright"):
            own |= {e for e in self.params.get(key) or {} if e.startswith(("light.", "switch."))}
        return sorted(own)

    def _lights_on(self) -> bool:
        return any(self.hass.states.is_state(e, STATE_ON) for e in self._own_lights())

    def _note_daylight(self) -> None:
        """Remember the zone's reading while none of its lights are on (kept by the runtime,
        so it survives this behaviour being restarted)."""
        if not self._lights_on() and (lux := self._lux()) is not None:
            self.rt.daylight[self.zone["id"]] = (lux, dt_util.utcnow())
            self.rt.keeper.save_soon()

    def _memory(self) -> float | None:
        mem = self.rt.daylight.get(self.zone["id"])
        if mem and (dt_util.utcnow() - mem[1]).total_seconds() <= self.DAYLIGHT_MAX_AGE:
            return mem[0]
        return None

    def _daylight(self) -> tuple[float | None, str]:
        """(the reading to judge daylight by, where it came from): 'now' with the zone's lights
        off, or even with them on when it is below target (lights only add light); else the
        last lights-off reading within the hour ('earlier'); else (None, 'sun')."""
        target = self.params.get("target_lux")
        now = self._lux()
        if not self._lights_on() or (now is not None and now < target):
            return now, "now"
        mem = self._memory()
        return (mem, "earlier") if mem is not None else (None, "sun")

    def _is_dark(self) -> bool:
        """For both decisions: is the DAYLIGHT below target? With nothing to go on, the sun:
        below the horizon counts as dark (a missing reading with lights off counts as dark)."""
        lux, src = self._daylight()
        if lux is None:
            return src == "now" or not self.hass.states.is_state("sun.sun", "above_horizon")
        return lux < self.params.get("target_lux")

    def _reading_txt(self) -> str:
        lux, src = self._daylight()
        if lux is None:
            return "no reading" if src == "now" else "by the sun"
        return f"{lux:.0f} lx" + (" (with the lights off)" if src == "earlier" else "")

    def _vacant_targets(self) -> tuple[dict, str]:
        dark = self.params.get("vacant") or {e: {"state": STATE_OFF} for e in self.params.get("occupied") or {}}
        if self._is_dark():
            return dark, "'empty'"
        return self.params.get("vacant_bright") or dark, "'empty, light'"

    @callback
    def start(self) -> None:
        self._unsubs.append(async_track_state_change_event(self.hass, self._sensors + self._lux_sensors + self._own_lights(), self._changed))
        self._note_daylight()
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
    def _changed(self, event) -> None:
        self._note_daylight()
        if event.data["entity_id"] in self._own_lights():
            return                     # the zone's lights only matter to the daylight memory
        self._evaluate()

    @callback
    def _evaluate(self) -> None:
        self._cancel()
        target = self.params.get("target_lux")
        if self._occupied():
            self._vacant_done = False
            lux = self._lux()
            dark = self._is_dark()     # daylight, not our own lights (the kitchen sensor sees its aux)
            if not self._lit and dark:
                self._lit = True
                self._apply(self.params.get("occupied") or {}, f"on: {self._reading_txt()} < {target} lx")
            elif self._lit:
                self._set_status("on")
            else:
                self._set_status(f"occupied; light enough ({self._reading_txt()} ≥ {target} lx)")
            return
        if self._vacant_done:
            return                      # already set for this vacancy: a lux change or a hand change must not redo it
        since = self._vacant_since()
        if since is None:
            self._set_status("waiting for the sensors")
            return
        due = since + timedelta(minutes=self.params.get("vacant_after_minutes", 15))
        now = dt_util.utcnow()
        if due <= now:
            self._lit = False
            self._vacant_done = True
            self.rt.keeper.release_overrides(self.zone["id"], vacant_since=since)
            targets, which = self._vacant_targets()
            self._apply(targets, f"vacant; {which} since " + dt_util.as_local(now).strftime("%H:%M"))
            return
        self._timer = async_call_later(self.hass, (due - now).total_seconds(), HassJob(self._timer_done, cancel_on_shutdown=True))
        self._set_status("vacant; at " + dt_util.as_local(due).strftime("%H:%M") + " switches to 'empty'")

    @callback
    def _timer_done(self, _now) -> None:
        self._timer = None
        self._evaluate()


class WakeSequence(Behaviour):
    """Steps of targets at `after_minutes` after the mode began — only when the house came
    into this mode from `after_mode` (Overnight). Steps already past are skipped."""
    name = "wake_sequence"

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self._timers: list[CALLBACK_TYPE] = []
        self._pending: list = []

    @callback
    def start(self) -> None:
        after = self.params.get("after_mode")
        if self.rt.prev_mode != after:
            self._set_status(f"only after {self.rt.mode_name(after) or after}")
            return
        began = dt_util.parse_datetime(self.rt.mode_since) if self.rt.mode_since else dt_util.utcnow()
        now = dt_util.utcnow()
        steps = sorted(self.params.get("steps") or [], key=lambda st: st.get("after_minutes", 0))
        future = [(began + timedelta(minutes=st.get("after_minutes", 0)), n, st) for n, st in enumerate(steps, 1)]
        future = [f for f in future if f[0] > now]
        for when, n, st in future:
            self._timers.append(async_track_point_in_time(
                self.hass, partial(self._step, n, st.get("targets") or {}, len(steps)), when))
        self._show_next(future)

    @callback
    def _show_next(self, future) -> None:
        self._pending = future
        if future:
            self._set_status("next step at " + dt_util.as_local(future[0][0]).strftime("%H:%M"))
        else:
            self._set_status("done")

    @callback
    def _step(self, n: int, targets: dict, total: int, _now) -> None:
        self._pending = [f for f in self._pending if f[1] != n]
        self._apply(targets, f"step {n} of {total}")
        self._show_next(self._pending)

    @callback
    def stop(self) -> None:
        for t in self._timers:
            t()
        self._timers.clear()
        super().stop()


KINDS: dict[str, type[Behaviour]] = {
    "wake_sequence": WakeSequence,
    "vacancy_off": VacancyOff,
    "occupied_vacant": OccupiedVacant,
    "hands_off": HandsOff,
    "static": Static,
    "nightlight_then_dark": NightlightThenDark,
    "presence_lux": PresenceLux,
}
