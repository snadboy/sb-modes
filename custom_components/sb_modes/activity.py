"""Activity: what SB Modes did, and why — one `sb_modes_activity` event per decision.

The events are kept by the recorder like any other, described for HA's Logbook (logbook.py)
and read back by the card's Activity section. A command's event carries the same Context as
the light calls it makes, so the Logbook shows those light changes as "triggered by" it.

Kinds:
  command   lights sent to a setting (only when something actually changes)
  skipped   a decision that sent nothing on purpose (held, changed by hand, follow stood
            aside, a TV change ignored)
  hand      a light changed by hand was noticed / hand_end: that ended
  hold      a zone held / release: its hold ended
  mode      the house mode changed
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from homeassistant.core import Context, HomeAssistant

if TYPE_CHECKING:
    from .runtime import Runtime

EVENT_ACTIVITY = "sb_modes_activity"

ICONS = {
    "command": "mdi:lightbulb-auto-outline",
    "skipped": "mdi:debug-step-over",
    "hand": "mdi:hand-back-right-outline",
    "hand_end": "mdi:hand-back-right-off-outline",
    "hold": "mdi:pause-circle-outline",
    "release": "mdi:play-circle-outline",
    "mode": "mdi:swap-horizontal",
}


def short_name(hass: HomeAssistant, entity_id: str, zone_name: str | None) -> str:
    """'Kitchen Aux Sink' in the Kitchen zone → 'Aux Sink'."""
    st = hass.states.get(entity_id)
    name = (st.name if st else None) or entity_id
    if zone_name and name.lower().startswith(zone_name.lower() + " ") and len(name) > len(zone_name) + 1:
        name = name[len(zone_name) + 1:]
    return name


def describe_target(target: dict) -> str:
    if "value" in target:
        return str(target["value"])
    if target.get("state") == "off":
        return "off"
    if target.get("min_brightness_pct") is not None:
        return f"≥{target['min_brightness_pct']}%"
    if target.get("brightness_pct") is not None:
        return f"{target['brightness_pct']}%"
    return "on"


def summary(hass: HomeAssistant, targets: dict[str, dict], zone_name: str | None) -> str:
    return ", ".join(f"{short_name(hass, e, zone_name)} {describe_target(t or {})}" for e, t in targets.items())


PHRASES = [                                   # behaviours' status lines, as activity wording
    (re.compile(r"^vacant; 'empty, light' since (\S+)$"), r"empty since \1 (daylight)"),
    (re.compile(r"^vacant; 'empty' since (\S+)$"), r"empty since \1"),
    (re.compile(r"^dark since (\S+)$"), r"empty: dark at \1"),
    (re.compile(r"^off: vacant$"), "empty: lights off"),
    (re.compile(r"^on: someone came in$"), "someone came in"),
    (re.compile(r"^occupied$"), "occupied"),
    (re.compile(r"^nightlight$"), "someone came in: nightlight"),
    (re.compile(r"^on: (.*)$"), r"occupied and dark (\1)"),
    (re.compile(r"^step (\d+) of (\d+)$"), r"wake-up step \1 of \2"),
    (re.compile(r"^applied$"), "fixed setting"),
]


def strip_zone(why: str, zone_id: str | None) -> str:
    """Runtime 'why' strings start with the zone id ('kitchen: on: …'), which the event names
    anyway; behaviours' status lines are reworded for reading as a log."""
    if zone_id and why.startswith(f"{zone_id}: "):
        why = why[len(zone_id) + 2:]
    for rx, repl in PHRASES:
        if rx.match(why):
            return rx.sub(repl, why)
    return why


def fire(rt: Runtime, kind: str, zone_id: str | None, text: str, entities=(), context: Context | None = None) -> None:
    zone = next((z for z in rt.zones() if z["id"] == zone_id), None) if zone_id else None
    data = {
        "kind": kind,
        "zone": zone_id,
        "zone_name": (zone.get("name") or zone_id) if zone else None,
        "message": text,
        "entities": list(entities),
        # the Logbook files the entry under this entity, and the card filters by it
        "entity_id": rt.zone_entity_id(zone_id) if zone_id else rt.mode_entity_id(),
    }
    rt.hass.bus.async_fire(EVENT_ACTIVITY, data, context=context)
