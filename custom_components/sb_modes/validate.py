"""Is a mode table usable? Pure checks — no Home Assistant imports, tested offline.

ERRORS block a save, and on a saved table (an entity or area deleted since) raise a
Repairs issue: a missing or duplicate id, no fallback mode, an override naming a
mode that does not exist, an unknown behaviour, a parameter that does not fit its
schema, an entity that does not exist or that no driver can control, an unknown area.

A zone whose behaviour, in any mode, needs lights, occupancy or illuminance sensors
that its areas do not have is an error too.

WARNINGS never block: an entity that is unavailable right now.

Every message starts with where the problem is ("Zone Kitchen, Overnight: …"), so a
list of them reads on its own in Repairs and in the editor. The lists returned are Issues: each
message also carries the PATH of the table field it belongs to (["zones", 2, "overrides",
"evening", "params", "target_lux"]) for the editor to place it — `Issues.detailed()`.
"""

from __future__ import annotations

import contextvars
import re
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from .model import ALL_MODES, BEHAVIOURS, DAYS, DRIVER_DOMAINS, NEEDS, TARGET_KEYS, mode_ids, parse_at, parse_when, resolve

UNAVAILABLE = ("unavailable", "unknown")
NEED_LABELS = {"lights": "a light", "occupancy": "an occupancy sensor", "illuminance": "an illuminance sensor"}
ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

_PATH: contextvars.ContextVar[tuple] = contextvars.ContextVar("sb_modes_validate_path", default=())


def _here(*parts) -> None:
    """The table field the next messages belong to."""
    _PATH.set(tuple(parts))


@contextmanager
def _sub(*parts):
    """…and, inside this block, a field within it."""
    token = _PATH.set(_PATH.get() + tuple(parts))
    try:
        yield
    finally:
        _PATH.reset(token)


class Issues(list):
    """Messages that also remember the path of the field each one belongs to."""

    def __init__(self) -> None:
        super().__init__()
        self.paths: list[list] = []

    def append(self, message: str) -> None:
        super().append(message)
        self.paths.append(list(_PATH.get()))

    def detailed(self) -> list[dict]:
        return [{"path": p, "message": m} for m, p in zip(self, self.paths)]


def check_config(
    config: Any,
    state_of: Callable[[str], str | None],
    area_exists: Callable[[str], bool],
    zone_entities: Callable[[list[str]], dict[str, list[str]]] | None = None,
    day_set_exists: Callable[[str], bool] | None = None,
) -> tuple[list[str], list[str]]:
    """(errors, warnings) for a whole table.

    state_of(entity_id) → its state, or None when the entity does not exist.
    area_exists(area_id) → whether the area registry has it.
    zone_entities(area_ids) → {"lights": [...], "occupancy": [...], "illuminance": [...]}
    found in those areas; None skips the needs check and the light-overlap check.
    day_set_exists(id) → whether SB Scheduler publishes that day set; None skips that check.
    """
    token = _PATH.set(())
    try:
        return _check_config(config, state_of, area_exists, zone_entities, day_set_exists)
    finally:
        _PATH.reset(token)


def _check_config(config, state_of, area_exists, zone_entities, day_set_exists) -> tuple[Issues, Issues]:
    errors, warnings = Issues(), Issues()
    if not isinstance(config, dict):
        errors.append("The table must be an object.")
        return errors, warnings

    modes = config.get("modes")
    if not isinstance(modes, list) or not modes:
        errors.append("Define at least one mode.")
        modes = []
    names = {}
    for i, mode in enumerate(modes):
        _here("modes", i)
        if not isinstance(mode, dict):
            errors.append(f"Mode {i + 1} must be an object.")
            continue
        where = f"Mode {mode.get('name') or mode.get('id') or i + 1}"
        _check_id(mode.get("id"), where, errors)
        icon = mode.get("icon")
        if icon is not None and not (isinstance(icon, str) and re.match(r"^[a-z0-9-]+:[a-z0-9-]+$", icon)):
            with _sub("icon"):
                errors.append(f"{where}: icon {icon!r} must look like mdi:weather-night.")
        if not mode.get("default"):
            errors.append(f"{where}: needs a default behaviour (a mode exists only through its default).")
        else:
            with _sub("default"):
                _check_choice(mode["default"], f"{where}, default", state_of, errors, warnings, mode_ids(config))
        names[mode.get("id")] = mode.get("name") or mode.get("id")
    _here("modes")
    _check_unique([m.get("id") for m in modes if isinstance(m, dict)], "Mode", errors)

    ids = mode_ids(config)
    fallback = config.get("fallback_mode")
    _here("fallback_mode")
    if not fallback:
        errors.append("Choose a fallback mode (used at startup and when the active mode is deleted).")
    elif fallback not in ids:
        errors.append(f"The fallback mode “{fallback}” is not a defined mode.")

    zones = config.get("zones") or []
    _here("zones")
    if not isinstance(zones, list):
        errors.append("Zones must be a list.")
        zones = []
    claimed: dict[str, str] = {}                      # light → the zone that has it
    for i, zone in enumerate(zones):
        _here("zones", i)
        if not isinstance(zone, dict):
            errors.append(f"Zone {i + 1} must be an object.")
            continue
        where = f"Zone {zone.get('name') or zone.get('id') or i + 1}"
        _check_id(zone.get("id"), where, errors)
        areas = zone.get("areas")
        areas_ok = isinstance(areas, list) and bool(areas)
        _here("zones", i, "areas")
        if not areas_ok:
            errors.append(f"{where}: list at least one area.")
        else:
            for area in areas:
                if not area_exists(area):
                    errors.append(f"{where}: area “{area}” does not exist.")
                    areas_ok = False
        overrides = zone.get("overrides") or {}
        _here("zones", i, "overrides")
        if not isinstance(overrides, dict):
            errors.append(f"{where}: overrides must map a mode id to a behaviour.")
            continue
        for mode_id, choice in overrides.items():
            _here("zones", i, "overrides", mode_id)
            if mode_id == ALL_MODES:
                _check_choice(choice, f"{where}, all modes", state_of, errors, warnings, ids)
                continue
            if mode_id not in ids:
                errors.append(f"{where}: overrides mode “{mode_id}”, which is not defined.")
                continue
            _check_choice(choice, f"{where}, {names.get(mode_id, mode_id)}", state_of, errors, warnings, ids)
        follow = zone.get("follow")
        _here("zones", i, "follow")
        if follow is not None:
            if not isinstance(follow, dict) or not follow.get("leader") or not isinstance(follow.get("bands"), list) or not follow["bands"]:
                errors.append(f"{where}: follow needs a leader light and a list of bands {{min_pct, targets}}.")
            else:
                _check_entity(follow["leader"], ["light", "switch"], f"{where}, follow leader", state_of, errors, warnings)
                for n, band in enumerate(follow["bands"], 1):
                    _here("zones", i, "follow", "bands", n - 1)
                    mp = band.get("min_pct") if isinstance(band, dict) else None
                    if isinstance(mp, bool) or not isinstance(mp, (int, float)) or not 0 <= mp <= 100:
                        errors.append(f"{where}, follow band {n}: min_pct must be 0–100.")
                        continue
                    _check_param(band.get("targets"), {"type": "targets"}, f"{where}, follow band {n}", state_of, errors, warnings)
                    if follow["leader"] in (band.get("targets") or {}):
                        errors.append(f"{where}, follow band {n}: the leader cannot follow itself.")
        overlay = zone.get("overlay")
        _here("zones", i, "overlay")
        if overlay is not None:
            ent, sts = (overlay.get("entity"), overlay.get("states")) if isinstance(overlay, dict) else (None, None)
            if not isinstance(ent, str) or "." not in ent or not isinstance(sts, dict) or not sts:
                errors.append(f"{where}: overlay needs an entity and states {{state: {{targets, holds}}}}.")
            else:
                if state_of(ent) is None:
                    errors.append(f"{where}, overlay: {ent} does not exist.")
                for st_name, cfg in sts.items():
                    _here("zones", i, "overlay", "states", st_name)
                    if not isinstance(cfg, dict):
                        errors.append(f"{where}, overlay {st_name}: must be {{targets, holds}}.")
                        continue
                    if "holds" in cfg and not isinstance(cfg["holds"], bool):
                        errors.append(f"{where}, overlay {st_name}: holds must be true or false.")
                    if cfg.get("targets") is not None:
                        _check_param(cfg["targets"], {"type": "targets"}, f"{where}, overlay {st_name}", state_of, errors, warnings)
                _here("zones", i, "overlay")
                if "presence" in overlay and not isinstance(overlay["presence"], bool):
                    errors.append(f"{where}, overlay: presence must be true or false.")
                for key in ("debounce_seconds", "unavailable_cooldown_seconds"):
                    v = overlay.get(key)
                    if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 3600):
                        errors.append(f"{where}, overlay: {key} must be 0–3600.")
        buttons = zone.get("buttons")
        _here("zones", i, "buttons")
        if buttons is not None:
            if not isinstance(buttons, dict) or not buttons:
                errors.append(f"{where}: buttons must be {{event entity: {{event type: action}}}}.")
            else:
                for ent, acts in buttons.items():
                    _here("zones", i, "buttons", ent)
                    if not isinstance(ent, str) or not ent.startswith("event."):
                        errors.append(f"{where}, buttons: {ent} is not an event entity.")
                        continue
                    if state_of(ent) is None:
                        errors.append(f"{where}, buttons: {ent} does not exist.")
                    if not isinstance(acts, dict) or not acts:
                        errors.append(f"{where}, buttons {ent}: must be {{event type: action}}.")
                        continue
                    for etype, act in acts.items():
                        _here("zones", i, "buttons", ent, etype)
                        at = f"{where}, buttons {ent} {etype}"
                        if act == "release":
                            continue
                        hold = act.get("hold") if isinstance(act, dict) else None
                        ok = isinstance(act, dict) and (
                            (act.get("release") is True and "hold" not in act) or hold in ("next", "paused") or (
                                isinstance(hold, int) and not isinstance(hold, bool) and 1 <= hold <= 1440))
                        if not ok:
                            errors.append(f"{at}: action must be {{release: true}} or {{hold: minutes | next | paused}}.")
                            continue
                        flash = act.get("flash")
                        if flash is not None and (isinstance(flash, bool) or not isinstance(flash, int) or not 0 <= flash <= 5):
                            errors.append(f"{at}: flash must be 0–5.")
                        if act.get("targets") is not None:
                            if "hold" not in act:
                                errors.append(f"{at}: targets go with a hold.")
                            else:
                                _check_param(act["targets"], {"type": "targets"}, at, state_of, errors, warnings)
        auto = zone.get("auto_off")
        _here("zones", i, "auto_off")
        if auto is not None:
            if not isinstance(auto, list):
                errors.append(f"{where}: auto_off must be a list of {{lights, with_empty, on_minutes}}.")
            else:
                for n, r in enumerate(auto):
                    _here("zones", i, "auto_off", n)
                    if not isinstance(r, dict) or not isinstance(r.get("lights"), list) or not r["lights"]:
                        errors.append(f"{where}, auto-off {n + 1}: list the lights it turns off.")
                        continue
                    for eid in r["lights"]:
                        _check_entity(eid, ["light", "switch"], f"{where}, auto-off {n + 1}", state_of, errors, warnings)
                    if "with_empty" in r and not isinstance(r["with_empty"], bool):
                        errors.append(f"{where}, auto-off {n + 1}: with_empty must be true or false.")
                    m = r.get("on_minutes")
                    if m is not None and (isinstance(m, bool) or not isinstance(m, (int, float)) or not 0 <= m <= 1440):
                        errors.append(f"{where}, auto-off {n + 1}: on_minutes must be 0–1440.")
                    if not r.get("with_empty") and not r.get("on_minutes"):
                        errors.append(f"{where}, auto-off {n + 1}: choose when it turns off (with the empty setting, after some minutes, or both).")
        wake = zone.get("wake_up")
        _here("zones", i, "wake_up")
        if wake is not None:
            if not isinstance(wake, dict):
                errors.append(f"{where}: wake_up must be {{after_mode, steps}}.")
            else:
                with _sub("after_mode"):
                    if not wake.get("after_mode"):
                        errors.append(f"{where}, wake-up: choose the mode it follows.")
                    else:
                        _check_param(wake["after_mode"], {"type": "mode"}, f"{where}, wake-up", state_of, errors, warnings, ids)
                with _sub("steps"):
                    _check_param(wake.get("steps"), {"type": "steps"}, f"{where}, wake-up step", state_of, errors, warnings)
        enter = zone.get("on_enter")
        _here("zones", i, "on_enter")
        if enter is not None:
            if not isinstance(enter, dict):
                errors.append(f"{where}: on_enter must map a mode id to targets.")
            else:
                for mode_id, targets in enter.items():
                    _here("zones", i, "on_enter", mode_id)
                    if mode_id not in ids:
                        errors.append(f"{where}: on_enter names mode “{mode_id}”, which is not defined.")
                        continue
                    _check_param(targets, {"type": "targets"}, f"{where}, when {names.get(mode_id, mode_id)} begins",
                                 state_of, errors, warnings)
        own = zone.get("lights")
        _here("zones", i, "lights")
        if own is not None:
            if not isinstance(own, list) or not own:
                errors.append(f"{where}: lights must be a list of light entity ids (or left out to use the areas' lights).")
                own = None
            else:
                for eid in own:
                    _check_entity(eid, ["light", "switch"], f"{where}, lights", state_of, errors, warnings)
        own_sensors = {}
        for key, domain in (("occupancy", "binary_sensor"), ("illuminance", "sensor")):
            _here("zones", i, key)
            lst = zone.get(key)
            if lst is None:
                continue
            if not isinstance(lst, list) or not lst:
                errors.append(f"{where}: {key} must be a list of {domain} entity ids (or left out to use the areas' sensors).")
                continue
            for eid in lst:
                _check_entity(eid, [domain], f"{where}, {key}", state_of, errors, warnings, driven=False)
            own_sensors[key] = list(lst)
        if areas_ok and zone_entities is not None and isinstance(zone.get("id"), str):
            found = dict(zone_entities(areas))
            if own:
                found["lights"] = list(own)
            found.update(own_sensors)
            _here("zones", i)
            _check_needs(config, zone, ids, names, found, where, errors)
            _here("zones", i, "lights")
            for light in found.get("lights", []):
                other = claimed.get(light)
                if other is not None and other != where:
                    errors.append(f"{where}: {light} is also in {other} — give one of them its own `lights` list.")
                claimed.setdefault(light, where)
    _here("zones")
    _check_unique([z.get("id") for z in zones if isinstance(z, dict)], "Zone", errors)
    _here("schedule")
    _check_schedule(config.get("schedule"), ids, names, errors, day_set_exists)
    _check_day_types(config, ids, names, errors, day_set_exists)
    return errors, warnings


def _check_day_types(config: dict, ids: list[str], names: dict, errors, day_set_exists=None) -> None:
    """Day types: an ordered list, each but the last on an SB Scheduler calendar; their times
    name real modes and valid times, and never two modes at one moment of one day type."""
    types, times = config.get("day_types"), config.get("times")
    _here("day_types")
    if types is None:
        if times:
            _here("times")
            errors.append("Times are set per day type: define the day types first.")
        return
    if not isinstance(types, list) or not types:
        errors.append("Day types must be a list with at least one entry (the last takes every other day).")
        return
    tnames = {}
    for i, t in enumerate(types):
        _here("day_types", i)
        if not isinstance(t, dict):
            errors.append(f"Day type {i + 1} must be an object.")
            continue
        where = f"Day type {t.get('name') or t.get('id') or i + 1}"
        _check_id(t.get("id"), where, errors)
        tnames[t.get("id")] = t.get("name") or t.get("id")
        if not t.get("name"):
            errors.append(f"{where}: give it a name.")
        ds, last = t.get("day_set"), i == len(types) - 1
        if last and ds:
            errors.append(f"{where}: the last day type takes every other day — it has no calendar.")
        elif not last and not ds:
            errors.append(f"{where}: only the last day type can be “every other day”; choose its calendar.")
        elif ds and (not isinstance(ds, str) or (day_set_exists is not None and not day_set_exists(ds))):
            errors.append(f"{where}: calendar {ds!r} is not published by SB Scheduler.")
    _here("day_types")
    _check_unique([t.get("id") for t in types if isinstance(t, dict)], "Day type", errors)
    if times is None:
        return
    _here("times")
    if not isinstance(times, dict):
        errors.append("Times must map a day type to its list of mode changes.")
        return
    for tid, entries in times.items():
        _here("times", tid)
        if tid not in tnames:
            errors.append(f"Times are set for day type “{tid}”, which is not defined.")
            continue
        if not isinstance(entries, list):
            errors.append(f"{tnames[tid]}: times must be a list.")
            continue
        seen: dict = {}
        for j, e in enumerate(entries):
            _here("times", tid, j)
            where = f"{tnames[tid]}, time {j + 1}"
            if not isinstance(e, dict):
                errors.append(f"{where} must be an object.")
                continue
            if e.get("mode") not in ids:
                errors.append(f"{where}: mode “{e.get('mode')}” is not defined.")
            if parse_when(e.get("at")) is None:
                errors.append(f"{where}: time {e.get('at')!r} must be HH:MM (24-hour), sunrise or sunset, optionally ±HH:MM.")
            if "eve" in e and not isinstance(e["eve"], bool):
                errors.append(f"{where}: “the night before” must be true or false.")
            key = (e.get("at"), bool(e.get("eve")))
            if key in seen and seen[key] != e.get("mode"):
                errors.append(f"{where}: {names.get(seen[key], seen[key])} and {names.get(e.get('mode'), e.get('mode'))} both start at {e.get('at')}.")
            seen[key] = e.get("mode")


def _check_schedule(schedule: Any, ids: list[str], names: dict, errors: list[str], day_set_exists=None) -> None:
    """Rules name existing modes, real days / day sets and times, and never two modes at one moment."""
    if schedule is None:
        return
    if not isinstance(schedule, list):
        errors.append("The schedule must be a list of rules.")
        return
    seen: dict[tuple[str, str], str] = {}
    for i, rule in enumerate(schedule):
        _here("schedule", i)
        where = f"Schedule rule {i + 1}"
        if not isinstance(rule, dict):
            errors.append(f"{where} must be an object.")
            continue
        mode, at, days, day_set = rule.get("mode"), rule.get("at"), rule.get("days"), rule.get("day_set")
        if mode not in ids:
            errors.append(f"{where}: mode “{mode}” is not defined.")
        if parse_when(at) is None:
            errors.append(f"{where}: time {at!r} must be HH:MM (24-hour), sunrise or sunset, optionally ±HH:MM (sunset-01:00).")
        for flag in ("eve", "not"):
            if flag in rule and not isinstance(rule[flag], bool):
                errors.append(f"{where}: {flag} must be true or false.")
        if (days is None) == (day_set is None):
            errors.append(f"{where}: give either days ({', '.join(DAYS)}) or a day_set — one of the two.")
            continue
        if day_set is not None:
            if not isinstance(day_set, str) or (day_set_exists is not None and not day_set_exists(day_set)):
                errors.append(f"{where}: day set {day_set!r} is not published by SB Scheduler.")
            continue
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


def _check_choice(choice: Any, where: str, state_of, errors: list[str], warnings: list[str], ids: list[str] | None = None) -> None:
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
            with _sub("params", key):
                errors.append(f"{where}: {name} has no parameter “{key}”.")
    for key, spec in schema.items():
        value = params.get(key)
        with _sub("params", key):
            if value is None or value == "" or value == {}:
                if spec.get("required"):
                    errors.append(f"{where}: {name} needs “{key}”.")
                continue
            _check_param(value, spec, f"{where}, {key}", state_of, errors, warnings, ids)


def _check_param(value: Any, spec: dict, where: str, state_of, errors: list[str], warnings: list[str], ids=None) -> None:
    kind = spec["type"]
    if kind == "mode":
        if ids is not None and value not in ids:
            errors.append(f"{where}: mode “{value}” is not defined.")
        return
    if kind == "steps":
        if not isinstance(value, list) or not value:
            errors.append(f"{where}: must be a list of steps {{after_minutes, targets}}.")
            return
        for n, step in enumerate(value, 1):
            with _sub(n - 1):
                if not isinstance(step, dict):
                    errors.append(f"{where} {n}: must be an object.")
                    continue
                am = step.get("after_minutes")
                if isinstance(am, bool) or not isinstance(am, (int, float)) or not 0 <= am <= 720:
                    with _sub("after_minutes"):
                        errors.append(f"{where} {n}: after_minutes must be 0–720.")
                with _sub("targets"):
                    _check_param(step.get("targets"), {"type": "targets"}, f"{where} {n}, targets", state_of, errors, warnings)
        return
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{where}: {value!r} is not a number.")
        elif not spec.get("min", value) <= value <= spec.get("max", value):
            errors.append(f"{where}: {value} is outside {spec['min']}–{spec['max']}.")
    elif kind == "bool":
        if not isinstance(value, bool):
            errors.append(f"{where}: {value!r} must be true or false.")
    elif kind == "entity":
        _check_entity(value, spec.get("domains"), where, state_of, errors, warnings)
    elif kind == "entities":
        if not isinstance(value, list) or not value:
            errors.append(f"{where}: must be a list of entity ids.")
            return
        for eid in value:
            _check_entity(eid, spec.get("domains"), where, state_of, errors, warnings)
    elif kind == "targets":
        if not isinstance(value, dict):
            errors.append(f"{where}: must map entity ids to their target states.")
            return
        for eid, target in value.items():
            with _sub(eid):
                _check_target(eid, target, where, state_of, errors, warnings)


def _check_target(eid, target, where: str, state_of, errors, warnings) -> None:
    _check_entity(eid, None, where, state_of, errors, warnings)
    if not isinstance(target, dict):
        errors.append(f"{where}, {eid}: target must be an object.")
        return
    for k in target:
        if k not in TARGET_KEYS:
            errors.append(f"{where}, {eid}: unknown target key “{k}”.")
    if target.get("state") not in (None, "on", "off"):
        errors.append(f"{where}, {eid}: state must be on or off.")
    if eid.split(".", 1)[0] == "number" if isinstance(eid, str) else False:
        v = target.get("value")
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            errors.append(f"{where}, {eid}: a number entity needs a numeric value.")
        return
    hs = target.get("hs_color")
    if hs is not None and not (isinstance(hs, list) and len(hs) == 2 and all(isinstance(x, (int, float)) for x in hs)):
        errors.append(f"{where}, {eid}: hs_color must be [hue, saturation].")
    for key in ("brightness_pct", "min_brightness_pct"):
        pct = target.get(key)
        if pct is not None and (isinstance(pct, bool) or not isinstance(pct, (int, float)) or not 0 <= pct <= 100):
            errors.append(f"{where}, {eid}: {key} must be 0–100.")
    if "min_brightness_pct" in target and ({"brightness_pct", "state"} & set(target)):
        errors.append(f"{where}, {eid}: min_brightness_pct goes alone (it means: on, at least that bright).")


def _check_entity(eid: Any, domains: list[str] | None, where: str, state_of, errors: list[str], warnings: list[str],
                  driven: bool = True) -> None:
    if not isinstance(eid, str) or "." not in eid:
        errors.append(f"{where}: {eid!r} is not an entity id.")
        return
    domain = eid.split(".", 1)[0]
    if domains and domain not in domains:
        errors.append(f"{where}: {eid} is not a {' / '.join(domains)} entity.")
        return
    if driven and domain not in DRIVER_DOMAINS:
        errors.append(f"{where}: no driver controls {domain} entities ({eid}).")
        return
    st = state_of(eid)
    if st is None:
        errors.append(f"{where}: {eid} does not exist.")
    elif st in UNAVAILABLE:
        warnings.append(f"{where}: {eid} is {st}.")
