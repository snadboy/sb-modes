"""The mode table and what can go in it. Pure — no Home Assistant imports, tested offline.

The CONFIG is the one definition everything else is derived from:

    {
      "fallback_mode": "day",
      "modes": [                                   # order = the select's option order
        {"id": "overnight", "name": "Overnight",
         "default": {"behaviour": "nightlight_then_dark", "params": {"delay": 10}}},
        …
      ],
      "zones": [
        {"id": "kitchen", "name": "Kitchen", "areas": ["kitchen", "kitchen_aux"],
         "overrides": {"overnight": {"behaviour": "nightlight_then_dark",
                                     "params": {"nightlight": "light.kitchen_aux_sink", "level": 10}}}},
        …
      ],
    }

A mode exists only because it has a default. A zone without an override for a mode
inherits that mode's default, so every (mode, zone) pair resolves to exactly one
behaviour and its parameters.
"""

from __future__ import annotations

import copy
from typing import Any

# Behaviours and their parameter schemas. The code that RUNS each behaviour arrives
# in later steps; the schemas exist now so a table can be validated from day one.
#   number:   {"min", "max"}            entity:  {"domains"}
#   targets:  {entity_id: {"state": "on"|"off", "brightness_pct": 0-100, "color_temp_kelvin": K}}
BEHAVIOURS: dict[str, dict[str, dict[str, Any]]] = {
    "hands_off": {},
    "static": {
        "targets": {"type": "targets", "required": True},
    },
    "nightlight_then_dark": {
        "nightlight": {"type": "entity", "domains": ["light", "switch"]},
        "level": {"type": "number", "min": 1, "max": 100},
        "delay": {"type": "number", "min": 1, "max": 240},
    },
    "presence_lux": {
        "target_lux": {"type": "number", "min": 1, "max": 2000, "required": True},
        "color_temp_kelvin": {"type": "number", "min": 2000, "max": 6500},
        "off_delay": {"type": "number", "min": 1, "max": 240},
    },
}

# Domains a driver can control. An entity outside these is an error, not skipped.
DRIVER_DOMAINS = ("light", "switch")

TARGET_KEYS = {"state", "brightness_pct", "color_temp_kelvin"}

DEFAULT_CONFIG: dict[str, Any] = {
    "fallback_mode": "day",
    "modes": [
        {"id": "morning", "name": "Morning", "default": {"behaviour": "hands_off", "params": {}}},
        {"id": "day", "name": "Day", "default": {"behaviour": "hands_off", "params": {}}},
        {"id": "evening", "name": "Evening", "default": {"behaviour": "hands_off", "params": {}}},
        {"id": "overnight", "name": "Overnight", "default": {"behaviour": "hands_off", "params": {}}},
    ],
    "zones": [],
}


def default_config() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_CONFIG)


def mode_ids(config: dict) -> list[str]:
    return [m.get("id") for m in config.get("modes") or [] if isinstance(m, dict) and m.get("id")]


def find(items: list | None, item_id: str) -> dict | None:
    return next((x for x in items or [] if isinstance(x, dict) and x.get("id") == item_id), None)


def resolve(config: dict, mode_id: str, zone_id: str) -> dict | None:
    """The behaviour a zone runs in a mode: {"behaviour", "params", "source"}.

    source is "override" when the zone sets its own, else "default". None when the
    mode or zone does not exist (validation keeps a saved table from getting there).
    """
    mode = find(config.get("modes"), mode_id)
    zone = find(config.get("zones"), zone_id)
    if mode is None or zone is None:
        return None
    own = (zone.get("overrides") or {}).get(mode_id)
    picked, source = (own, "override") if own else (mode.get("default") or {}, "default")
    return {"behaviour": picked.get("behaviour"), "params": dict(picked.get("params") or {}), "source": source}
