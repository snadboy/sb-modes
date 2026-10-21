"""Auto-off: lights a person turns on by hand, that SB Modes only ever turns OFF.

A zone's `auto_off` = [{"lights": [...], "with_empty": true, "on_minutes": 15}, …]: each light
goes off when either
  - with_empty: the zone's own behaviour puts it to its empty setting (the zone is vacant and
    that command turns at least one light off) — the lamp goes with the others; or
  - on_minutes: it has been on that long AND the zone has been empty for its usual delay (the
    behaviour's own "empty after", so a sensor's blink does not count) — for when it was the
    only light on.
Never turned on. Runs in every mode, like follow (the Office's side window lamp, 2026-10-21).
"""

from __future__ import annotations

from datetime import timedelta
from functools import partial
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, HassJob, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from .runtime import Runtime


class AutoOff:
    def __init__(self, rt: Runtime, zone: dict, rules: list[dict]) -> None:
        self.rt = rt
        self.zone_id = zone["id"]
        self.rules = [r for r in rules if isinstance(r, dict) and r.get("lights")]
        self.lights = sorted({e for r in self.rules for e in r["lights"]})
        self._unsubs: list[CALLBACK_TYPE] = []
        self._timer: CALLBACK_TYPE | None = None

    @callback
    def start(self) -> None:
        watch = self.lights + self.rt.zone_presence(self.zone_id)
        if watch:
            self._unsubs.append(async_track_state_change_event(self.rt.hass, watch, self._changed))
        self._evaluate()

    @callback
    def stop(self) -> None:
        for u in self._unsubs:
            u()
        self._unsubs.clear()
        self._cancel()

    @callback
    def _cancel(self) -> None:
        if self._timer:
            self._timer()
            self._timer = None

    @callback
    def _changed(self, _event) -> None:
        self._evaluate()

    def _on(self, light: str):
        st = self.rt.hass.states.get(light)
        return st if st is not None and st.state == "on" else None

    # ── with the empty setting ───────────────────────────────────────────────
    @callback
    def empty_setting_applied(self) -> None:
        """Runtime.send: the zone's behaviour just turned lights off while the zone is vacant."""
        out = [e for r in self.rules if r.get("with_empty") for e in r["lights"] if self._on(e)]
        if out:
            self._off(out, "with the empty setting")

    # ── on N minutes while empty ─────────────────────────────────────────────
    @callback
    def _evaluate(self, _now=None) -> None:
        self._cancel()
        sources = self.rt.zone_presence(self.zone_id)
        vacant = self.rt.vacant_since(sources) if sources else None
        if vacant is None:
            return
        empty_from = vacant + timedelta(minutes=self.rt.zone_empty_delay(self.zone_id))
        now, due_now, nxt = dt_util.utcnow(), [], None
        for r in self.rules:
            if not r.get("on_minutes"):
                continue
            for e in r["lights"]:
                st = self._on(e)
                if st is None:
                    continue
                due = max(st.last_changed + timedelta(minutes=r["on_minutes"]), empty_from)
                if due <= now:
                    due_now.append((e, r["on_minutes"]))
                elif nxt is None or due < nxt:
                    nxt = due
        for e, mins in due_now:
            self._off([e], f"on {mins:g} min and the room empty")
        if nxt is not None:
            self._timer = async_call_later(self.rt.hass, (nxt - now).total_seconds(),
                                           HassJob(self._evaluate, cancel_on_shutdown=True))

    @callback
    def _off(self, lights: list[str], why: str) -> None:
        # a person turned it on: the hand-change mark must not keep SB Modes from turning it off
        self.rt.keeper.forget(self.zone_id, lights)
        self.rt.send({e: {"state": "off"} for e in lights}, f"auto-off {why}", self.zone_id, cascade=False)
