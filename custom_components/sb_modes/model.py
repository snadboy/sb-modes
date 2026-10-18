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
                                     "params": {"nightlight": {"light.kitchen_aux_sink": {"brightness_pct": 10}}}}}},
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
#   number:   {"min", "max"}            entity:  {"domains"}      entities: a list of entity ids
#   bool:     true / false
#   targets:  {entity_id: {"state": "on"|"off", "brightness_pct": 0-100, "color_temp_kelvin": K,
#                          "hs_color": [h, s], "effect": name}}   for lights and switches
#             {entity_id: {"value": n}}                           for number entities
BEHAVIOURS: dict[str, dict[str, dict[str, Any]]] = {
    "hands_off": {},
    "static": {
        "targets": {"type": "targets", "required": True},
    },
    "nightlight_then_dark": {
        "nightlight": {"type": "targets"},
        "delay": {"type": "number", "min": 1, "max": 240, "default": 10},
    },
    "vacancy_off": {
        "lights": {"type": "entities", "domains": ["light", "switch"], "required": True},
        "off_after_seconds": {"type": "number", "min": 0, "max": 3600, "default": 60},
        "max_on_minutes": {"type": "number", "min": 0, "max": 1440, "default": 0},
        "on_when_occupied": {"type": "bool", "default": False},
    },
    "occupied_vacant": {
        "occupied": {"type": "targets", "required": True},
        "vacant": {"type": "targets", "required": True},
        "vacant_after_minutes": {"type": "number", "min": 0, "max": 240, "default": 0},
    },
    "presence_lux": {
        "target_lux": {"type": "number", "min": 1, "max": 2000, "required": True},
        "occupied": {"type": "targets", "required": True},
        "vacant": {"type": "targets"},
        "vacant_after_minutes": {"type": "number", "min": 1, "max": 240, "default": 15},
    },
}

# What a behaviour needs from its zone's areas. A zone that runs it in any mode without
# these is a validation error, not a behaviour that silently does nothing.
NEEDS: dict[str, tuple[str, ...]] = {
    "vacancy_off": ("occupancy",),
    "occupied_vacant": ("occupancy",),
    "nightlight_then_dark": ("lights", "occupancy"),
    "presence_lux": ("occupancy", "illuminance"),
}

# Domains a driver can control. An entity outside these is an error, not skipped.
DRIVER_DOMAINS = ("light", "switch", "number")

TARGET_KEYS = {"state", "brightness_pct", "color_temp_kelvin", "hs_color", "effect", "value"}

ALL_MODES = "*"     # a zone override that applies in every mode the zone does not set itself

DEFAULT_CONFIG: dict[str, Any] = {
    "fallback_mode": "day",
    "modes": [
        {"id": "morning", "name": "Morning", "default": {"behaviour": "hands_off", "params": {}}},
        {"id": "day", "name": "Day", "default": {"behaviour": "hands_off", "params": {}}},
        {"id": "evening", "name": "Evening", "default": {"behaviour": "hands_off", "params": {}}},
        {"id": "overnight", "name": "Overnight", "default": {"behaviour": "hands_off", "params": {}}},
    ],
    "zones": [],
    "schedule": [],
}


def default_config() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_CONFIG)


def mode_ids(config: dict) -> list[str]:
    return [m.get("id") for m in config.get("modes") or [] if isinstance(m, dict) and m.get("id")]


def find(items: list | None, item_id: str) -> dict | None:
    return next((x for x in items or [] if isinstance(x, dict) and x.get("id") == item_id), None)


def resolve(config: dict, mode_id: str, zone_id: str) -> dict | None:
    """The behaviour a zone runs in a mode: {"behaviour", "params", "source"}.

    Precedence: the zone's own setting for this mode ("override"), then the zone's setting for
    all modes, key "*" ("zone"), then the mode's default ("default"). None when the
    mode or zone does not exist (validation keeps a saved table from getting there).
    """
    mode = find(config.get("modes"), mode_id)
    zone = find(config.get("zones"), zone_id)
    if mode is None or zone is None:
        return None
    overrides = zone.get("overrides") or {}
    if overrides.get(mode_id):
        picked, source = overrides[mode_id], "override"
    elif overrides.get(ALL_MODES):
        picked, source = overrides[ALL_MODES], "zone"
    else:
        picked, source = mode.get("default") or {}, "default"
    return {"behaviour": picked.get("behaviour"), "params": with_defaults(picked.get("behaviour"), picked.get("params")),
            "source": source}


def with_defaults(behaviour: str | None, params: dict | None) -> dict:
    """The parameters as given, plus the schema's default for any left out."""
    out = dict(params or {})
    for key, spec in BEHAVIOURS.get(behaviour or "", {}).items():
        if out.get(key) is None and "default" in spec:
            out[key] = spec["default"]
    return out


# ── the schedule ─────────────────────────────────────────────────────────────
# config["schedule"] = [{"days": ["mon", …], "at": "HH:MM", "mode": id}, …]
# Each rule starts its mode at that local time on those calendar days. The scheduled mode at
# any moment is the mode of the most recent rule occurrence; a rule at 00:30 on "sat" is the
# start of Friday night's overnight.
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def parse_at(at: Any) -> tuple[int, int] | None:
    """'HH:MM' → (h, m), or None when it is not a valid time."""
    if not isinstance(at, str) or len(at) != 5 or at[2] != ":":
        return None
    try:
        h, m = int(at[:2]), int(at[3:])
    except ValueError:
        return None
    return (h, m) if 0 <= h <= 23 and 0 <= m <= 59 else None


def occurrences(config: dict, start, end):
    """Every rule occurrence in [start, end), oldest first: (when, mode id). `start` and
    `end` are timezone-aware datetimes in the house's local time zone."""
    from datetime import datetime, timedelta
    out = []
    tz = start.tzinfo
    day = start.date() - timedelta(days=1)
    while day <= end.date():
        name = DAYS[day.weekday()]
        for rule in config.get("schedule") or []:
            hm = parse_at(rule.get("at"))
            if hm and name in (rule.get("days") or []):
                when = datetime(day.year, day.month, day.day, hm[0], hm[1], tzinfo=tz)
                if start <= when < end:
                    out.append((when, rule.get("mode")))
        day += timedelta(days=1)
    return sorted(out, key=lambda o: o[0])


def scheduled_mode(config: dict, now):
    """(mode id, since when) the schedule puts the house in at `now`; (None, None) with no rules."""
    from datetime import timedelta
    past = occurrences(config, now - timedelta(days=8), now + timedelta(microseconds=1))
    return past[-1][::-1] if past else (None, None)


def next_change(config: dict, now):
    """(mode id, when) of the next rule occurrence after `now`; (None, None) with no rules."""
    from datetime import timedelta
    nxt = occurrences(config, now + timedelta(microseconds=1), now + timedelta(days=8))
    return nxt[0][::-1] if nxt else (None, None)
