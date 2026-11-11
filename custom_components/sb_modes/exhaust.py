"""Exhaust fan add-on: a bathroom fan on with someone in the room or a humidity spike, off once
the room is empty and the air has settled (the Smart Exhaust Fan blueprint, 2026-11-11).

    {"type": "exhaust", "fan": "switch.upstairs_bath_exhaust_fan",
     "humidity_rate": "sensor.upstairs_bath_fp300_humidity_rate",    # %/min; optional
     "on_rate": 2,            # on when humidity rises faster than this
     "steady_rate": 0.1,      # "settled" below this
     "empty_minutes": 5,      # the room empty this long …
     "drying_minutes": 10,    # … and settled: runs this much longer, then off
     "max_on_minutes": 20}    # longest run — waived for the run if someone is in the room then (0 = no limit)

On is edge-triggered (someone comes in, humidity starts climbing) and only while the add-on's
When holds, so a fan switched off by hand stays off. The off paths run whatever the When says,
so a fan is never left running. Occupancy = the room's own sensors (its areas, or its
`occupancy` list). The fan is sent commands like a button press: a hold on the room's lights
does not stop it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, HassJob, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

from . import activity
from .zones import zone_entities

if TYPE_CHECKING:
    from .runtime import Runtime

DEFAULTS = {"on_rate": 2, "steady_rate": 0.1, "empty_minutes": 5, "drying_minutes": 10,
            "max_on_minutes": 20}


class Exhaust:
    def __init__(self, rt: Runtime, zone: dict, spec: dict) -> None:
        self.rt = rt
        self.zone_id = zone["id"]
        self.id = spec.get("id")
        self.spec = {**DEFAULTS, **{k: v for k, v in spec.items() if v is not None}}
        self.fan: str = spec.get("fan") or ""
        self.rate: str | None = spec.get("humidity_rate") or None
        self.sources = zone_entities(rt.hass, zone.get("areas") or [], None, zone.get("occupancy"))["occupancy"]
        self._unsubs: list[CALLBACK_TYPE] = []
        self._timers: dict[str, CALLBACK_TYPE] = {}
        self._occupied = False
        self._settled_since = None    # when the room was first seen empty-enough and settled
        self._cap_waived = False      # someone was in the room when the longest run came up

    @callback
    def start(self) -> None:
        if not self.fan:
            return
        self._occupied = self.rt.occupied(self.sources)
        watch = [self.fan, *self.sources, *([self.rate] if self.rate else [])]
        self._unsubs.append(async_track_state_change_event(self.rt.hass, watch, self._changed))
        self._evaluate()

    @callback
    def stop(self) -> None:
        for u in self._unsubs:
            u()
        self._unsubs.clear()
        for k in list(self._timers):
            self._cancel(k)

    def _cancel(self, key: str) -> None:
        if (t := self._timers.pop(key, None)) is not None:
            t()

    def _later(self, key: str, seconds: float, action) -> None:
        self._cancel(key)

        @callback
        def fire(now) -> None:
            self._timers.pop(key, None)
            action(now)

        self._timers[key] = async_call_later(self.rt.hass, max(seconds, 0), HassJob(fire, cancel_on_shutdown=True))

    # ── inputs ───────────────────────────────────────────────────────────────
    def _rate(self) -> float | None:
        st = self.rt.hass.states.get(self.rate) if self.rate else None
        try:
            return float(st.state) if st is not None else None
        except (TypeError, ValueError):
            return None

    def _fan_on(self) -> bool:
        st = self.rt.hass.states.get(self.fan)
        return st is not None and st.state == "on"

    def _name(self) -> str:
        return activity.short_name(self.rt.hass, self.fan, self.rt._zone_name(self.zone_id))

    @callback
    def _changed(self, event) -> None:
        eid = event.data["entity_id"]
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if eid == self.fan:
            self._fan_changed(old, new)
        elif eid == self.rate:
            self._rate_changed(old, new)
        else:
            was, self._occupied = self._occupied, self.rt.occupied(self.sources)
            if self._occupied and not was:
                self._turn_on("someone came in")
        self._evaluate()
        self.rt.notify()

    def _rate_changed(self, old, new) -> None:
        def val(st):
            try:
                return float(st.state) if st is not None else None
            except (TypeError, ValueError):
                return None
        before, now = val(old), val(new)
        if now is not None and now > self.spec["on_rate"] and (before is None or before <= self.spec["on_rate"]):
            self._turn_on(f"humidity rising {now:g} %/min")

    def _fan_changed(self, old, new) -> None:
        if old is None or new is None or old.state == new.state:
            return
        ours = new.context.id in self.rt.own_contexts
        if new.state == "off":
            self._settled_since = None
            self._cap_waived = False
        if not ours and new.state in ("on", "off"):
            self.rt.log("hand", self.zone_id, f"{self._name()} {new.state}", [self.fan])

    # ── actions ──────────────────────────────────────────────────────────────
    def _turn_on(self, why: str) -> None:
        if self._fan_on() or not self.rt.when_ok(self.spec.get("when")):
            return
        self._settled_since = None
        self.rt.send({self.fan: {"state": "on"}}, f"{self.spec.get('name') or 'exhaust'}: {why}", self.zone_id, cascade=False)

    def _turn_off(self, why: str) -> None:
        self.rt.send({self.fan: {"state": "off"}}, f"{self.spec.get('name') or 'exhaust'}: {why}", self.zone_id, cascade=False)

    # ── off paths ────────────────────────────────────────────────────────────
    @callback
    def _evaluate(self, _now=None) -> None:
        self._cancel("eval")
        if not self._fan_on():
            return
        now, nxt = dt_util.utcnow(), []
        occupied = self.rt.occupied(self.sources)
        st = self.rt.hass.states.get(self.fan)
        cap = self.spec["max_on_minutes"]
        if cap and not self._cap_waived:
            due = st.last_changed + timedelta(minutes=cap)
            if due > now:
                nxt.append(due)
            elif occupied:
                self._cap_waived = True                  # never cut short with someone there
            else:
                self._turn_off(f"on {cap:g} min")
                return
        vacant = self.rt.vacant_since(self.sources) if self.sources else None
        rate = self._rate()
        settled = rate is None or rate < self.spec["steady_rate"]
        empty_from = vacant + timedelta(minutes=self.spec["empty_minutes"]) if vacant is not None else None
        if empty_from is not None and empty_from > now:
            nxt.append(empty_from)
        if empty_from is not None and empty_from <= now and settled:
            self._settled_since = self._settled_since or now
            done = self._settled_since + timedelta(minutes=self.spec["drying_minutes"])
            if done <= now:
                self._turn_off(f"room empty, humidity settled, dried {self.spec['drying_minutes']:g} min")
                return
            if _now is not None:
                self.rt.notify()                         # "drying until" for the card
            nxt.append(done)
        else:
            self._settled_since = None
        if nxt:
            self._later("eval", (min(nxt) - now).total_seconds() + 0.5, self._evaluate)

    def status(self) -> str:
        if not self.fan:
            return ""
        if not self._fan_on():
            return "fan off"
        hm = lambda t: dt_util.as_local(t).strftime("%H:%M")   # noqa: E731
        if self._settled_since is not None:
            return f"drying until {hm(self._settled_since + timedelta(minutes=self.spec['drying_minutes']))}"
        return "fan on"
