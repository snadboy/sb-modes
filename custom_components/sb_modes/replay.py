"""The "replay" add-on: a room's lights do what they did N days ago (the lived-in look, 2026-10-23).

    {"type": "replay", "lights": [...], "days_back": 7, "when": {"modes": ["away"]}}

While its When matches (normally only the Away mode), every RESCAN it reads the recorder for
those lights over the next stretch of time, N days back, and schedules each on / off / brightness
change at the same clock time today. Changes go through Runtime.apply, so a hold or a hand change
still wins. A gap in the history (a purge, a recorder outage) just means nothing happens then.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from functools import partial
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.event import async_track_point_in_time, async_track_time_interval
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from .runtime import Runtime

_LOGGER = logging.getLogger(__name__)
RESCAN = timedelta(minutes=30)
AHEAD = timedelta(minutes=40)                            # each read covers a little more than RESCAN


def target_of(state) -> dict | None:
    """A recorded light state as a target: off, or on at its brightness."""
    if state.state == "off":
        return {"state": "off"}
    if state.state != "on":
        return None                                      # unavailable / unknown: nothing to copy
    b = state.attributes.get("brightness")
    return {"brightness_pct": max(1, round(b * 100 / 255))} if b is not None else {"state": "on"}


class ReplayAddon:
    def __init__(self, rt: Runtime, zone: dict, spec: dict) -> None:
        self.rt = rt
        self.zone_id = zone["id"]
        self.spec = spec
        self.id = spec.get("id")
        self.name = spec.get("name") or "Replay"
        self.lights = list(spec.get("lights") or [])
        self.days = spec.get("days_back") or 7
        self.when = spec.get("when") or {}
        self._unsubs: list[CALLBACK_TYPE] = []
        self._timers: dict = {}                          # (entity, when) → unsub
        self.last_count = 0

    @callback
    def start(self) -> None:
        self.stop()
        if not self.spec.get("enabled", True) or not self.lights:
            return
        self._unsubs.append(async_track_time_interval(self.rt.hass, self._tick, RESCAN, cancel_on_shutdown=True))
        self._tick()

    @callback
    def stop(self) -> None:
        for u in [*self._unsubs, *self._timers.values()]:
            u()
        self._unsubs.clear()
        self._timers.clear()

    @callback
    def mode_changed(self, _old, _new) -> None:
        """A mode change may open or close its When (entering Away): look again now."""
        self._tick()

    @callback
    def _tick(self, _now=None) -> None:
        if not self.rt.when_ok(self.when):
            for u in self._timers.values():
                u()
            self._timers.clear()
            return
        self.rt.hass.async_create_task(self._plan(), f"sb_modes replay {self.name}")

    async def history(self, start, end) -> dict[str, list]:
        """entity → recorded states over [start, end] (the recorder; tests replace this)."""
        from homeassistant.components.recorder import get_instance, history
        return await get_instance(self.rt.hass).async_add_executor_job(
            partial(history.get_significant_states, self.rt.hass, start, end, self.lights,
                    include_start_time_state=False, significant_changes_only=False))

    async def _plan(self) -> None:
        now, back = dt_util.utcnow(), timedelta(days=self.days)
        try:
            found = await self.history(now - back, now - back + AHEAD)
        except Exception:  # noqa: BLE001 — no history now: try again at the next rescan
            _LOGGER.warning("SB Modes: replay %s could not read history", self.name, exc_info=True)
            return
        count = 0
        for eid, states in (found or {}).items():
            for st in states:
                target = target_of(st)
                at = st.last_changed + back
                if target is None or at <= now or (eid, at) in self._timers:
                    continue
                self._timers[(eid, at)] = async_track_point_in_time(
                    self.rt.hass, partial(self._play, eid, at, target), at)
                count += 1
        self.last_count = count

    @callback
    def _play(self, eid: str, at, target: dict, _now) -> None:
        self._timers.pop((eid, at), None)
        if self.rt.when_ok(self.when):
            self.rt.apply({eid: target}, f"{self.name}: as {self.days} days ago", self.zone_id)

    def status(self) -> str:
        if not self.spec.get("enabled", True):
            return "off"
        return f"{len(self._timers)} change{'s' if len(self._timers) != 1 else ''} queued" if self._timers else ""
