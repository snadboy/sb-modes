"""Buttons: a press holds or releases a zone (the Family Room Hold BILRESA).

A zone's `buttons` = {event_entity: {event_type: action}}, an action being
  {"hold": minutes | "next" | "paused", "targets": {…}, "flash": n}  — hold the zone (the same
      holds as the hold_zone action), flash its lights n times as the cue, then set `targets`;
  {"release": true, "flash": n}  (or just "release") — flash n times, end the hold, and put the
      lights back: the zone's behaviour starts again, and an overlay re-applies its current
      state (the TV's off levels too, since a person pressing the button is in the room).
The lights have no flash of their own (Lutron), so a flash is made: lights that are on go off,
lights that are off come on, for FLASH_SECONDS, then back.

Matter buttons replay queued presses when they reconnect, stamped as live (see the history in
the old Good Morning BILRESA automation), so:
- changes to or from unavailable/unknown are ignored (a restore is not a press);
- a press is acted on SETTLE_SECONDS later, and a newer press replaces it, so a burst collapses
  to its last event.
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, Context, Event, EventStateChangedData, HassJob, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .manual import UNAVAILABLE

if TYPE_CHECKING:
    from .runtime import Runtime

_LOGGER = logging.getLogger(__name__)
SETTLE_SECONDS = 2
FLASH_SECONDS = 0.7


class Buttons:
    def __init__(self, rt: Runtime, zone: dict, spec: dict, whens: dict | None = None) -> None:
        self.rt = rt
        self.zone_id = zone["id"]
        self.spec = spec
        self.whens = whens or {}                         # event entity → its add-on's When
        self._pending: CALLBACK_TYPE | None = None
        self._task: asyncio.Task | None = None
        self._unsub: CALLBACK_TYPE | None = None

    @callback
    def start(self) -> None:
        self._unsub = async_track_state_change_event(self.rt.hass, list(self.spec), self._pressed)

    @callback
    def stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        self._cancel()
        if self._task and not self._task.done():
            self._task.cancel()

    @callback
    def _cancel(self) -> None:
        if self._pending:
            self._pending()
            self._pending = None

    @callback
    def _pressed(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if new is None or old is None or new.state in UNAVAILABLE or old.state in UNAVAILABLE:
            return
        if new.state == old.state:
            return                                       # attributes only: not a press
        action = (self.spec.get(event.data["entity_id"]) or {}).get(new.attributes.get("event_type"))
        self._cancel()                                   # a newer event replaces a pending one
        if action is None:
            return
        if not self.rt.when_ok(self.whens.get(event.data["entity_id"])):
            self.rt.log("skipped", self.zone_id, f"{new.name or event.data['entity_id']} pressed — not now: its When does not match")
            return
        self._pending = async_call_later(self.rt.hass, SETTLE_SECONDS,
                                         HassJob(partial(self._act, action), cancel_on_shutdown=True))

    @callback
    def _act(self, action, _now=None, by: str = "button") -> None:
        self._pending = None
        if action == "release":
            action = {"release": True}
        if self._task and not self._task.done():
            self._task.cancel()                          # a newer press wins over a flash in progress
        if "hold" in action:
            self.rt.keeper.hold(self.zone_id, action["hold"], by=by)   # first: nothing else sends meanwhile
        self._task = self.rt.hass.async_create_task(self._run(action, by), f"sb_modes button {self.zone_id}")

    async def _run(self, action: dict, by: str = "button") -> None:
        if action.get("flash"):
            await self._flash(self.rt.zone_lights(self.zone_id), action["flash"])
        if "hold" in action:
            if action.get("targets"):
                self.rt.send(action["targets"], f"held by {by}", self.zone_id)
            return
        self.rt.keeper.release(self.zone_id, by=by)
        if (ov := self.rt.overlay_of(self.zone_id)) is not None and not ov.holding:
            ov.apply_current()                           # holding: the zone's restart already did

    async def _flash(self, lights: list[str], times: int) -> None:
        hass = self.rt.hass
        before = {e: st for e in lights if (st := hass.states.get(e)) is not None and st.state in ("on", "off")}
        if not before:
            return

        async def call(eid: str, on: bool, brightness=None) -> None:
            ctx = Context()
            self.rt.own_contexts.append(ctx.id)
            self.rt.keeper.commanded([eid])
            data = {"entity_id": eid, "transition": 0}
            if on:
                data["brightness"] = brightness if brightness is not None else 128
            try:
                await hass.services.async_call("light", "turn_on" if on else "turn_off", data, blocking=True, context=ctx)
            except Exception:  # noqa: BLE001 — a flash is a cue; carry on without it
                _LOGGER.warning("SB Modes: flash failed for %s", eid, exc_info=True)

        for i in range(times):
            if i:
                await asyncio.sleep(FLASH_SECONDS)
            await asyncio.gather(*(call(e, st.state == "off") for e, st in before.items()))
            await asyncio.sleep(FLASH_SECONDS)
            await asyncio.gather(*(call(e, st.state == "on", st.attributes.get("brightness")) for e, st in before.items()))
