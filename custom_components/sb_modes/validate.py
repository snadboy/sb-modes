"""Is a mode table usable? Pure checks — no Home Assistant imports, tested offline.

ERRORS block a save, and on a saved table (an entity or area deleted since) raise a
Repairs issue: a missing or duplicate id, no fallback mode, an override naming a
mode that does not exist, an unknown behaviour, a parameter that does not fit its
schema, an entity that does not exist or that no driver can control, an unknown area.

A zone whose behaviour, in any mode, needs lights, occupancy or illuminance sensors
that its areas do not have is an error too.

WARNINGS never block: an entity that is unavailable right now.

Every message starts with where the problem is ("Zone Kitchen, Overnight: …"), so a
list of them reads on its own in Repairs and in the editor.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from .model import BEHAVIOURS, DAYS, DRIVER_DOMAINS, NEEDS, TARGET_KEYS, mode_ids, parse_at, resolve

UNAVAILABLE = ("unavailable", "unknown")
NEED_LABELS = {"lights": "a light", "occupancy": "an occupancy sensor", "illuminance": "an illuminance sensor"}
ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def check_config(
    config: Any,
    state_of: Callable[[str], str | None],
    area_exists: Callable[[str], bool],
    zone_entities: Callable[[list[str]], dict[str, list[str]]] | None = None,
) -> tuple[list[str], list[str]]:
    """(errors, warnings) for a whole table.

    state_of(entity_id) → its state, or None when the entity does not exist.
    area_exists(area_id) → whether the area registry has it.
    zone_entities(area_ids) → {"lights": [...], "occupancy": [...], "illuminance": [...]}
    found in those areas; None skips the needs check.
    """
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(config, dict):
        return ["The table must be an object."], []

    modes = config.get("modes")
    if not isinstance(modes, list) or not modes:
        errors.append("Define at least one mode.")
        modes = []
    names = {}
    for i, mode in enumerate(modes):
        if not isinstance(mode, dict):
            errors.append(f"Mode {i + 1} must be an object.")
            continue
        where = f"Mode {mode.get('name') or mode.get('id') or i + 1}"
        _check_id(mode.get("id"), where, errors)
        if not mode.get("default"):
            errors.append(f"{where}: needs a default behaviour (a mode exists only through its default).")
        else:
            _check_choice(mode["default"], f"{where}, default", state_of, errors, warnings)
        names[mode.get("id")] = mode.get("name") or mode.get("id")
    _check_unique([m.get("id") for m in modes if isinstance(m, dict)], "Mode", errors)

    ids = mode_ids(config)
    fallback = config.get("fallback_mode")
    if not fallback:
        errors.append("Choose a fallback mode (used at startup and when the active mode is deleted).")
    elif fallback not in ids:
        errors.append(f"The fallback mode “{fallback}” is not a defined mode.")

    zones = config.get("zones") or []
    if not isinstance(zones, list):
        errors.append("Zones must be a list.")
        zones = []
    for i, zone in enumerate(zones):
        if not isinstance(zone, dict):
            errors.append(f"Zone {i + 1} must be an object.")
            continue
        where = f"Zone {zone.get('name') or zone.get('id') or i + 1}"
        _check_id(zone.get("id"), where, errors)
        areas = zone.get("areas")
        areas_ok = isinstance(areas, list) and bool(areas)
        if not areas_ok:
            errors.append(f"{where}: list at least one area.")
        else:
            for area in areas:
                if not area_exists(area):
                    errors.append(f"{where}: area “{area}” does not exist.")
                    areas_ok = False
        overrides = zone.get("overrides") or {}
        if not isinstance(overrides, dict):
            errors.append(f"{where}: overrides must map a mode id to a behaviour.")
            continue
        for mode_id, choice in overrides.items():
            if mode_id not in ids:
                errors.append(f"{where}: overrides mode “{mode_id}”, which is not defined.")
                continue
            _check_choice(choice, f"{where}, {names.get(mode_id, mode_id)}", state_of, errors, warnings)
        if areas_ok and zone_entities is not None and isinstance(zone.get("id"), str):
            _check_needs(config, zone, ids, names, zone_entities(areas), where, errors)
    _check_unique([z.get("id") for z in zones if isinstance(z, dict)], "Zone", errors)
    _check_schedule(config.get("schedule"), ids, names, errors)
    return errors, warnings


def _check_schedule(schedule: Any, ids: list[str], names: dict, errors: list[str]) -> None:
    """Rules name existing modes, real days and times, and never two modes at one moment."""
    if schedule is None:
        return
    if not isinstance(schedule, list):
        errors.append("The schedule must be a list of rules.")
        return
    seen: dict[tuple[str, str], str] = {}
    for i, rule in enumerate(schedule):
        where = f"Schedule rule {i + 1}"
        if not isinstance(rule, dict):
            errors.append(f"{where} must be an object.")
            continue
        mode, at, days = rule.get("mode"), rule.get("at"), rule.get("days")
        if mode not in ids:
            errors.append(f"{where}: mode “{mode}” is not defined.")
        if parse_at(at) is None:
            errors.append(f"{where}: time {at!r} must be HH:MM (24-hour).")
        if not isinstance(days, list) or not days:
            errors.append(f"{where}: list the days it applies to ({', '.join(DAYS)}).")
            continue
        for d in days:
            if d not in DAYS:
                errors.append(f"{where}: {d!r} is not a day ({', '.join(DAYS)}).")
            elif parse_at(at) is not None:
                other = seen.get((d, at))
                if other is not None and other != mode:
                    errors.append(f"{where}: {names.get(other, other)} and {names.get(mode, mode)} both start at {at} on {d}.")
                seen[(d, at)] = mode


def _check_needs(config: dict, zone: dict, ids: list[str], names: dict, found: dict, where: str,
                 errors: list[str]) -> None:
    """Each behaviour the zone runs in some mode must find what it needs in the zone's areas."""
    missing: dict[tuple[str, str], list[str]] = {}
    for mode_id in ids:
        picked = resolve(config, mode_id, zone["id"]) or {}
        for need in NEEDS.get(picked.get("behaviour") or "", ()):
            if not found.get(need):
                missing.setdefault((picked["behaviour"], need), []).append(str(names.get(mode_id, mode_id)))
    for (behaviour, need), modes in missing.items():
        errors.append(f"{where}: {behaviour} ({', '.join(modes)}) needs {NEED_LABELS[need]} in the zone's areas; there is none.")


def _check_id(value: Any, where: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not ID_RE.match(value):
        errors.append(f"{where}: id {value!r} must be lowercase letters, digits and _ (starting with a letter).")


def _check_unique(ids: list, kind: str, errors: list[str]) -> None:
    seen: set = set()
    for i in ids:
        if i in seen:
            errors.append(f"{kind} id “{i}” is used more than once.")
        seen.add(i)


def _check_choice(choice: Any, where: str, state_of, errors: list[str], warnings: list[str]) -> None:
    """One {"behaviour", "params"} pair against its behaviour's schema."""
    if not isinstance(choice, dict):
        errors.append(f"{where}: must be {{behaviour, params}}.")
        return
    name = choice.get("behaviour")
    if name not in BEHAVIOURS:
        errors.append(f"{where}: unknown behaviour {name!r} (one of {', '.join(BEHAVIOURS)}).")
        return
    schema = BEHAVIOURS[name]
    params = choice.get("params") or {}
    if not isinstance(params, dict):
        errors.append(f"{where}: params must be an object.")
        return
    for key in params:
        if key not in schema:
            errors.append(f"{where}: {name} has no parameter “{key}”.")
    for key, spec in schema.items():
        value = params.get(key)
        if value is None or value == "" or value == {}:
            if spec.get("required"):
                errors.append(f"{where}: {name} needs “{key}”.")
            continue
        _check_param(value, spec, f"{where}, {key}", state_of, errors, warnings)


def _check_param(value: Any, spec: dict, where: str, state_of, errors: list[str], warnings: list[str]) -> None:
    kind = spec["type"]
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{where}: {value!r} is not a number.")
        elif not spec.get("min", value) <= value <= spec.get("max", value):
            errors.append(f"{where}: {value} is outside {spec['min']}–{spec['max']}.")
    elif kind == "entity":
        _check_entity(value, spec.get("domains"), where, state_of, errors, warnings)
    elif kind == "targets":
        if not isinstance(value, dict):
            errors.append(f"{where}: must map entity ids to their target states.")
            return
        for eid, target in value.items():
            _check_entity(eid, None, where, state_of, errors, warnings)
            if not isinstance(target, dict):
                errors.append(f"{where}, {eid}: target must be an object.")
                continue
            for k in target:
                if k not in TARGET_KEYS:
                    errors.append(f"{where}, {eid}: unknown target key “{k}”.")
            if target.get("state") not in (None, "on", "off"):
                errors.append(f"{where}, {eid}: state must be on or off.")
            pct = target.get("brightness_pct")
            if pct is not None and (isinstance(pct, bool) or not isinstance(pct, (int, float)) or not 0 <= pct <= 100):
                errors.append(f"{where}, {eid}: brightness_pct must be 0–100.")


def _check_entity(eid: Any, domains: list[str] | None, where: str, state_of, errors: list[str], warnings: list[str]) -> None:
    if not isinstance(eid, str) or "." not in eid:
        errors.append(f"{where}: {eid!r} is not an entity id.")
        return
    domain = eid.split(".", 1)[0]
    if domains and domain not in domains:
        errors.append(f"{where}: {eid} is not a {' / '.join(domains)} entity.")
        return
    if domain not in DRIVER_DOMAINS:
        errors.append(f"{where}: no driver controls {domain} entities ({eid}).")
        return
    st = state_of(eid)
    if st is None:
        errors.append(f"{where}: {eid} does not exist.")
    elif st in UNAVAILABLE:
        warnings.append(f"{where}: {eid} is {st}.")
