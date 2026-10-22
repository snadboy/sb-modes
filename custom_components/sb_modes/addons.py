"""The "lights" add-on: a trigger, a When, and steps of actions (2026-10-22).

    {"type": "lights", "trigger": {…}, "when": {…}, "steps": [{"after_minutes": 0, <action>}, …]}

Triggers: a clock or sun time ({"at": "06:30" | "sunset-01:00"}), a mode beginning or ending
({"mode_begins": [ids]} / {"mode_ends": [ids]} — a real mode change, never a restart), or a
device reaching a state ({"entity": id, "to": state}). The When is checked as the trigger fires.
Step 0 runs then, the others `after_minutes` later. An action is {"targets"} (Runtime.apply: a
held zone or a light changed by hand is left alone), {"hold", "targets"?, "flash"?},
{"release": true, "flash"?} or {"script": "script.x"} (run under SB Modes' own context, with the
room, mode and who is there as variables).

After a restart, the steps still to come of a sequence that began before it are re-armed;
nothing already past is replayed.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from functools import partial
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, Context, Event, EventStateChangedData, callback
from homeassistant.helpers.event import async_track_point_in_time, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .manual import UNAVAILABLE
from .model import _when_on, parse_when, when_matches

if TYPE_CHECKING:
    from .runtime import Runtime

_LOGGER = logging.getLogger(__name__)
LOOKAHEAD_DAYS = 14                                      # how far "next run" looks for a matching day


class LightsAddon:
    def __init__(self, rt: Runtime, zone: dict, spec: dict) -> None:
        self.rt = rt
        self.zone_id = zone["id"]
        self.spec = spec
        self.id = spec.get("id")
        self.name = spec.get("name") or "Add-on"
        self.trigger = spec.get("trigger") or {}
        self.when = spec.get("when") or {}
        self.steps = sorted((s for s in spec.get("steps") or [] if isinstance(s, dict)),
                            key=lambda s: s.get("after_minutes", 0))
        self._unsubs: list[CALLBACK_TYPE] = []
        self._steps: list[tuple] = []                   # (due, unsub) of steps still to come
        self.next_run = None                             # datetime of the next time trigger, for the status

    # ── life cycle ───────────────────────────────────────────────────────────
    @callback
    def start(self) -> None:
        self.stop()
        if "at" in self.trigger:
            self._resume_time()
            self._arm_time()
        elif self.trigger.get("entity"):
            self._unsubs.append(async_track_state_change_event(self.rt.hass, [self.trigger["entity"]], self._state))
        elif self.trigger.get("mode_begins") or self.trigger.get("mode_ends"):
            self._resume_mode()

    @callback
    def stop(self) -> None:
        for u in [*self._unsubs, *(x[1] for x in self._steps)]:
            u()
        self._unsubs.clear()
        self._steps.clear()

    # ── triggers ─────────────────────────────────────────────────────────────
    def _occurrence(self, day):
        when = parse_when(self.trigger.get("at"))
        return _when_on(day, when, dt_util.get_default_time_zone(), self.rt.sun) if when else None

    def _days_ok(self, at) -> bool:
        """The When's days and window at `at` (the mode is checked when it fires)."""
        return when_matches(self.rt.config, {k: v for k, v in self.when.items() if k != "modes"}, None,
                            dt_util.as_local(at), self.rt.in_set)

    @callback
    def _arm_time(self) -> None:
        now = dt_util.now()
        self.next_run = None
        armed = False
        for n in range(LOOKAHEAD_DAYS + 1):
            at = self._occurrence(now.date() + timedelta(days=n))
            if at is None or at <= now:
                continue
            if not armed:
                self._unsubs.append(async_track_point_in_time(self.rt.hass, self._time_fired, at))
                armed = True                             # fires daily; the When decides on the day
            if self._days_ok(at):
                self.next_run = at                       # what to show: the next day it will act
                break

    @callback
    def _time_fired(self, _now) -> None:
        for u in self._unsubs:
            u()
        self._unsubs.clear()
        self.fire(f"at {dt_util.as_local(dt_util.utcnow()).strftime('%H:%M')}")
        self._arm_time()
        self.rt.notify()

    @callback
    def _resume_time(self) -> None:
        """A restart inside today's sequence: arm the steps still to come."""
        at = self._occurrence(dt_util.now().date())
        if at is not None and at <= dt_util.now() and self._days_ok(at):
            self._arm_steps(dt_util.as_utc(at), skip_first=True)

    @callback
    def _resume_mode(self) -> None:
        began = dt_util.parse_datetime(self.rt.mode_since) if self.rt.mode_since else None
        if began is not None and self._mode_hit(self.rt.prev_mode, self.rt.mode):
            self._arm_steps(began, skip_first=True)

    def _mode_hit(self, old, new) -> bool:
        return new in (self.trigger.get("mode_begins") or []) or old in (self.trigger.get("mode_ends") or [])

    @callback
    def mode_changed(self, old, new) -> None:
        """Runtime._switch: a real mode change (never a restart or a reload)."""
        if self._mode_hit(old, new):
            for _due, u in self._steps:
                u()
            self._steps.clear()
            names = " / ".join(self.rt.mode_name(m) or m for m in (
                [new] if new in (self.trigger.get("mode_begins") or []) else [old]))
            self.fire(f"{names} {'begins' if new in (self.trigger.get('mode_begins') or []) else 'ended'}")

    @callback
    def _state(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if new is None or old is None or new.state == old.state or new.state in UNAVAILABLE or old.state in UNAVAILABLE:
            return
        if new.state == self.trigger.get("to"):
            self.fire(f"{new.name or new.entity_id} {new.state}")

    # ── running ──────────────────────────────────────────────────────────────
    @callback
    def fire(self, why: str) -> None:
        if not self.spec.get("enabled", True):
            return
        if not when_matches(self.rt.config, self.when, self.rt.mode, dt_util.now(), self.rt.in_set):
            self.rt.log("skipped", self.zone_id, f"{self.name} ({why}) — not now: its When does not match")
            return
        self._arm_steps(dt_util.utcnow(), skip_first=False, why=why)

    @callback
    def _arm_steps(self, began, skip_first: bool, why: str = "") -> None:
        now = dt_util.utcnow()
        total = len(self.steps)
        for n, step in enumerate(self.steps, 1):
            due = began + timedelta(minutes=step.get("after_minutes", 0))
            label = f"{self.name}" + (f", step {n} of {total}" if total > 1 else "") + (f" ({why})" if why and n == 1 else "")
            if due <= now:
                if not skip_first:
                    self._act(step, label)
                continue
            self._steps.append((due, async_track_point_in_time(self.rt.hass, partial(self._step_due, step, label), due)))

    @callback
    def _step_due(self, step: dict, label: str, _now) -> None:
        now = dt_util.utcnow()
        self._steps = [x for x in self._steps if x[0] > now]
        self._act(step, label)
        self.rt.notify()

    @callback
    def _act(self, step: dict, label: str) -> None:
        rt = self.rt
        if step.get("script"):
            ctx = Context()
            rt.own_contexts.append(ctx.id)
            sources = rt.zone_presence(self.zone_id)
            variables = {"zone": self.zone_id, "zone_name": rt._zone_name(self.zone_id), "mode": rt.mode,
                         "occupied": bool(sources) and rt.occupied(sources), "addon": self.name,
                         "held": bool(rt.keeper.held(self.zone_id)), "changed_by_hand": sorted(rt.keeper.overridden(self.zone_id))}
            rt.log("command", self.zone_id, f"{label} → ran {step['script']}", [step["script"]], ctx)
            rt.hass.async_create_task(rt.hass.services.async_call(
                "script", "turn_on", {"entity_id": step["script"], "variables": variables}, context=ctx),
                f"sb_modes addon script {step['script']}")
            return
        if "hold" in step or step.get("release"):
            rt.hold_or_release(self.zone_id, step, by=self.name)
            return
        rt.apply(step.get("targets") or {}, label, self.zone_id)

    # ── status ───────────────────────────────────────────────────────────────
    def status(self) -> str:
        if not self.spec.get("enabled", True):
            return "off"
        if self._steps:
            return "running"
        if self.next_run is not None:
            return "next " + dt_util.as_local(self.next_run).strftime("%a %d %H:%M")
        return ""
