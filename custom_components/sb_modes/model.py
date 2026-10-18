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
#   bool:     true / false               mode: a mode id
#   steps:    [{"after_minutes": n, "targets": {…}}, …]   (minutes after the mode began)
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
    "wake_sequence": {
        "after_mode": {"type": "mode", "required": True},
        "steps": {"type": "steps", "required": True},
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
# config["schedule"] = [rule, …]; a rule starts `mode` at `at` on the days it matches.
#   days:     ["mon", …]                    — those weekdays, or
#   day_set:  "workday"                     — an SB Scheduler day set (its calendar), with
#     eve:    true                          — "the day BEFORE a day in the set", and/or
#     not:    true                          — "a day NOT in the set"
#   at:       "HH:MM" | "sunset" | "sunset-01:00" | "sunrise+00:30"   (local time)
# The scheduled mode at any moment is the mode of the most recent occurrence; a rule at 00:30
# starts that night's mode for the day before. Day-set membership and sun times come from
# resolvers (in_set(day_set, date) → bool | None when unknown; sun(event, date) → datetime |
# None), so this stays pure.
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
SUN_EVENTS = ("sunrise", "sunset")


def parse_at(at: Any) -> tuple[int, int] | None:
    """'HH:MM' → (h, m), or None when it is not a valid clock time."""
    if not isinstance(at, str) or len(at) != 5 or at[2] != ":":
        return None
    try:
        h, m = int(at[:2]), int(at[3:])
    except ValueError:
        return None
    return (h, m) if 0 <= h <= 23 and 0 <= m <= 59 else None


def parse_when(at: Any):
    """("clock", h, m) | ("sun", event, offset_minutes) | None for an invalid `at`."""
    hm = parse_at(at)
    if hm:
        return ("clock", hm[0], hm[1])
    if not isinstance(at, str):
        return None
    for event in SUN_EVENTS:
        if at == event:
            return ("sun", event, 0)
        if at.startswith(event) and len(at) == len(event) + 6 and at[len(event)] in "+-":
            off = parse_at(at[len(event) + 1:])
            if off:
                sign = -1 if at[len(event)] == "-" else 1
                return ("sun", event, sign * (off[0] * 60 + off[1]))
    return None


def rule_day_sets(config: dict) -> set[str]:
    return {r["day_set"] for r in config.get("schedule") or [] if isinstance(r, dict) and r.get("day_set")}


def _matches(rule: dict, day, in_set):
    """True / False whether the rule applies on this date; None when its day set is unknown."""
    from datetime import timedelta
    if rule.get("days") is not None:
        return DAYS[day.weekday()] in rule["days"]
    if in_set is None:
        return None
    m = in_set(rule.get("day_set"), day + timedelta(days=1) if rule.get("eve") else day)
    if m is None:
        return None
    return (not m) if rule.get("not") else m


def occurrences(config: dict, start, end, in_set=None, sun=None):
    """Every rule occurrence in [start, end), oldest first: (when, mode id). `start` and
    `end` are timezone-aware datetimes in the house's local time zone."""
    from datetime import datetime, timedelta
    out = []
    tz = start.tzinfo
    day = start.date() - timedelta(days=1)
    while day <= end.date():
        for rule in config.get("schedule") or []:
            when = parse_when(rule.get("at"))
            if not when or not _matches(rule, day, in_set):
                continue
            if when[0] == "clock":
                at = datetime(day.year, day.month, day.day, when[1], when[2], tzinfo=tz)
            else:
                base = sun(when[1], day) if sun else None
                if base is None:
                    continue
                at = base.astimezone(tz) + timedelta(minutes=when[2])
            if start <= at < end:
                out.append((at, rule.get("mode")))
        day += timedelta(days=1)
    return sorted(out, key=lambda o: o[0])


def scheduled_mode(config: dict, now, in_set=None, sun=None):
    """(mode id, since when) the schedule puts the house in at `now`; (None, None) with no rules."""
    from datetime import timedelta
    past = occurrences(config, now - timedelta(days=8), now + timedelta(microseconds=1), in_set, sun)
    return past[-1][::-1] if past else (None, None)


def next_change(config: dict, now, in_set=None, sun=None):
    """(mode id, when) of the next rule occurrence after `now`; (None, None) with no rules."""
    from datetime import timedelta
    nxt = occurrences(config, now + timedelta(microseconds=1), now + timedelta(days=8), in_set, sun)
    return nxt[0][::-1] if nxt else (None, None)
