"""Drivers: make devices match targets, sending only what differs.

A target is {"state": "on"|"off", "brightness_pct": 0-100, "color_temp_kelvin": K,
"hs_color": [h, s], "effect": name} for lights and switches — any light setting without a
state means on — or {"value": n} for a number entity (number.set_value). An entity that is
unavailable or missing is skipped (validation reports it). Every call carries a
Context the caller keeps, so later steps can tell SB Modes' own changes from a
person's.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Context, HomeAssistant, State

_LOGGER = logging.getLogger(__name__)
SKIP = (STATE_UNAVAILABLE, STATE_UNKNOWN)


def plan(state: State | None, target: dict) -> tuple[str, dict] | None:
    """The one call that brings `state` to `target`, or None when it already matches."""
    if state is None or state.state in SKIP:
        return None
    domain = state.entity_id.split(".", 1)[0]
    if domain == "number":
        value = target.get("value")
        if value is None:
            return None
        try:
            if abs(float(state.state) - float(value)) < 1e-6:
                return None
        except (TypeError, ValueError):
            pass
        return ("set_value", {"value": value})
    if target.get("state") == STATE_OFF:
        if state.state == STATE_OFF:
            return None
        tr = target.get("transition")
        return ("turn_off", {"transition": tr} if domain == "light" and tr is not None else {})
    data: dict = {}
    if domain == "light":
        if (pct := target.get("brightness_pct")) is not None:
            data["brightness_pct"] = pct
        if (kelvin := target.get("color_temp_kelvin")) is not None:
            data["color_temp_kelvin"] = kelvin
        if (hs := target.get("hs_color")) is not None:
            data["hs_color"] = hs
        if (effect := target.get("effect")) is not None:
            data["effect"] = effect
    if state.state == STATE_ON and _matches(state, data):
        return None
    if domain == "light" and (tr := target.get("transition")) is not None:
        data["transition"] = tr
    return ("turn_on", data)


def _matches(state: State, data: dict) -> bool:
    if (pct := data.get("brightness_pct")) is not None:
        b = state.attributes.get("brightness")
        if b is None or abs(round(b * 100 / 255) - pct) > 1:
            return False
    if (kelvin := data.get("color_temp_kelvin")) is not None:
        k = state.attributes.get("color_temp_kelvin")
        if k is None or abs(k - kelvin) > 50:
            return False
    if (hs := data.get("hs_color")) is not None:
        cur = state.attributes.get("hs_color")
        if not cur or abs(cur[0] - hs[0]) > 2 or abs(cur[1] - hs[1]) > 2:
            return False
    if (effect := data.get("effect")) is not None and state.attributes.get("effect") != effect:
        return False
    return True


async def apply(hass: HomeAssistant, targets: dict[str, dict], context: Context) -> list[str]:
    """Bring every entity to its target; returns the entities a call was sent to."""
    calls = []
    for eid, target in targets.items():
        step = plan(hass.states.get(eid), target or {})
        if step is not None:
            calls.append((eid, *step))
    if calls:
        await asyncio.gather(*(_call(hass, eid, service, data, context) for eid, service, data in calls))
    return [eid for eid, _, _ in calls]


async def _call(hass: HomeAssistant, eid: str, service: str, data: dict, context: Context) -> None:
    try:
        await hass.services.async_call(eid.split(".", 1)[0], service, {"entity_id": eid, **data},
                                       blocking=True, context=context)
    except Exception:  # noqa: BLE001 — one device failing must not stop the others
        _LOGGER.exception("SB Modes: %s.%s failed for %s", eid.split(".", 1)[0], service, eid)
