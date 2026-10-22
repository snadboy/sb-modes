"""Wake-up add-on: timed steps of lights after a mode ends (the bedside bulbs after Overnight).

A zone's `wake_up` = {"after_mode": "overnight", "steps": [{"after_minutes": 30, "targets": {…}}, …]}:
when the house leaves `after_mode`, each step's targets are set that many minutes after the
next mode began. Steps already past (a restart, a reload) are skipped. Runs in every mode,
like the other add-ons, but through Runtime.apply: a held zone or a light changed by hand is
left alone. Was the "Bedside" zone's wake_sequence behaviour until 2026-10-21.
"""

from __future__ import annotations

from datetime import timedelta
from functools import partial
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from .runtime import Runtime


class WakeUp:
    def __init__(self, rt: Runtime, zone: dict, spec: dict) -> None:
        self.rt = rt
        self.zone_id = zone["id"]
        self.after = spec.get("after_mode")
        self.steps = sorted((s for s in spec.get("steps") or [] if isinstance(s, dict)),
                            key=lambda s: s.get("after_minutes", 0))
        self._timers: list[CALLBACK_TYPE] = []
        self._pending: list = []
        self.status = ""

    @callback
    def start(self) -> None:
        """On every mode change (and start-up): arm the steps if the house just left `after_mode`."""
        self.stop()
        if self.rt.prev_mode != self.after:
            self.status = f"after {self.rt.mode_name(self.after) or self.after} ends"
            return
        began = dt_util.parse_datetime(self.rt.mode_since) if self.rt.mode_since else dt_util.utcnow()
        now = dt_util.utcnow()
        total = len(self.steps)
        future = [(began + timedelta(minutes=s.get("after_minutes", 0)), n, s) for n, s in enumerate(self.steps, 1)]
        self._pending = [f for f in future if f[0] > now]
        for when, n, s in self._pending:
            self._timers.append(async_track_point_in_time(
                self.rt.hass, partial(self._step, n, s.get("targets") or {}, total), when))
        self._show()

    @callback
    def stop(self) -> None:
        for t in self._timers:
            t()
        self._timers.clear()
        self._pending = []

    @callback
    def _show(self) -> None:
        self.status = ("next step at " + dt_util.as_local(self._pending[0][0]).strftime("%H:%M")) if self._pending else "done"

    @callback
    def _step(self, n: int, targets: dict, total: int, _now) -> None:
        self._pending = [f for f in self._pending if f[1] != n]
        self._show()
        self.rt.apply(targets, f"wake-up step {n} of {total}", self.zone_id)
        self.rt.notify()
