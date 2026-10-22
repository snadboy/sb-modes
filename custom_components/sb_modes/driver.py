"""Drivers: make devices match targets, sending only what differs.

A target is {"state": "on"|"off", "brightness_pct": 0-100, "color_temp_kelvin": K,
"hs_color": [h, s], "effect": name} for lights and switches ({"min_brightness_pct": n} = on and at
least n %: a brighter light is left alone) — any light setting without a
state means on — or {"value": n} for a number entity (number.set_value). An entity that is
unavailable or missing is skipped (validation reports it). Every call carries a
Context the caller keeps, so later steps can tell SB Modes' own changes from a
person's.

A light that is OFF and gets a colour (colour temperature, hs, effect) is turned on first and
given the colour in a second call: Matter lights ignore colour commands while off (ExecuteIfOff
is not set), so one call turns them on in their OLD colour — the Office door and closet floor
lamps kept a hand-set colour while the group came back on at 3401 K (2026-10-22).

A LIGHT GROUP is checked and set lamp by lamp: a group's own state blends its members (an
average colour, "on" if any is on), so one lamp left in another colour hid behind the rest and
nothing was sent. Each member gets the group's target; only members that differ get a call.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Context, HomeAssistant, State

_LOGGER = logging.getLogger(__name__)
SKIP = (STATE_UNAVAILABLE, STATE_UNKNOWN)
COLOUR_KEYS = ("color_temp_kelvin", "hs_color", "effect")


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
    if domain == "light" and (floor := target.get("min_brightness_pct")) is not None:
        # "at least": a light already on at or above the floor is left as it is
        b = state.attributes.get("brightness")
        if state.state == STATE_ON and (b is None or round(b * 100 / 255) >= floor):
            return None
        return ("turn_on", {"brightness_pct": floor})
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


def members(hass: HomeAssistant, eid: str) -> list[str]:
    """A light group's member lights (its entity_id attribute); [eid] for anything else."""
    st = hass.states.get(eid)
    m = st.attributes.get("entity_id") if st is not None and eid.startswith("light.") else None
    return [x for x in m if isinstance(x, str) and x.startswith("light.")] if isinstance(m, (list, tuple)) and m else [eid]


def needs(hass: HomeAssistant, eid: str, target: dict) -> bool:
    """Would bringing `eid` (each lamp of a group) to `target` send anything?"""
    return any(plan(hass.states.get(m), target or {}) is not None for m in members(hass, eid))


async def apply(hass: HomeAssistant, targets: dict[str, dict], context: Context) -> list[str]:
    """Bring every entity to its target (a group lamp by lamp); returns the entities called."""
    calls = []
    expanded = {m: t for eid, t in targets.items() for m in members(hass, eid)}
    for eid, target in expanded.items():
        st = hass.states.get(eid)
        step = plan(st, target or {})
        if step is not None:
            calls.append((eid, *step, st is not None and st.state == STATE_OFF))
    if calls:
        await asyncio.gather(*(_turn(hass, eid, service, data, was_off, context) for eid, service, data, was_off in calls))
    return [eid for eid, *_ in calls]


async def _turn(hass: HomeAssistant, eid: str, service: str, data: dict, was_off: bool, context: Context) -> None:
    """One entity's call — for an off light given a colour: on first, then the colour."""
    if service == "turn_on" and was_off and eid.startswith("light.") and any(k in data for k in COLOUR_KEYS):
        await _call(hass, eid, service, {k: v for k, v in data.items() if k not in COLOUR_KEYS}, context)
        data = {k: v for k, v in data.items() if k != "transition"}
    await _call(hass, eid, service, data, context)


async def _call(hass: HomeAssistant, eid: str, service: str, data: dict, context: Context) -> None:
    try:
        await hass.services.async_call(eid.split(".", 1)[0], service, {"entity_id": eid, **data},
                                       blocking=True, context=context)
    except Exception:  # noqa: BLE001 — one device failing must not stop the others
        _LOGGER.exception("SB Modes: %s.%s failed for %s", eid.split(".", 1)[0], service, eid)
