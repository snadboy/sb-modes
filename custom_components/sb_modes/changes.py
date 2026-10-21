"""What changed between two tables, in words — the editor's Review and each saved version.

Pure (no Home Assistant): `describe(old, new, name_of)` with name_of(entity_id) → a display name.
One line per change, "Where · what: before → after", for example
  "Kitchen · All modes · Empty, dark: Ceiling off → Ceiling off, Aux Sink 10%".
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .model import ALL_MODES, BEHAVIOUR_INFO, BEHAVIOURS

NONE = "—"


def describe(old: dict, new: dict, name_of: Callable[[str], str] = lambda e: e) -> list[str]:
    old, new = old or {}, new or {}
    out: list[str] = []
    mode_name = _mode_names(old, new)

    # ── modes ───────────────────────────────────────────────────────────────
    om, nm = _by_id(old.get("modes")), _by_id(new.get("modes"))
    for mid, m in nm.items():
        if mid not in om:
            out.append(f"Mode {_n(m)} added (default: {_choice(m.get('default'), name_of, mode_name)})")
    for mid, m in om.items():
        if mid not in nm:
            out.append(f"Mode {_n(m)} removed")
    for mid in om.keys() & nm.keys():
        a, b = om[mid], nm[mid]
        where = f"Mode {_n(b)}"
        if _n(a) != _n(b):
            out.append(f"Mode {_n(a)} renamed {_n(b)}")
        if a.get("icon") != b.get("icon"):
            out.append(f"{where} · icon: {a.get('icon') or NONE} → {b.get('icon') or NONE}")
        out += _choice_changes(f"{where} · default", a.get("default"), b.get("default"), name_of, mode_name)
    shared = [m for m in om if m in nm]
    if [m for m in nm if m in om] != shared:
        out.append("Mode order: " + ", ".join(mode_name(m) for m in shared) + " → "
                   + ", ".join(mode_name(m) for m in nm if m in om))
    if old.get("fallback_mode") != new.get("fallback_mode"):
        out.append(f"Fallback mode: {mode_name(old.get('fallback_mode'))} → {mode_name(new.get('fallback_mode'))}")

    # ── zones ───────────────────────────────────────────────────────────────
    oz, nz = _by_id(old.get("zones")), _by_id(new.get("zones"))
    for zid, z in nz.items():
        if zid not in oz:
            out.append(f"Zone {_n(z)} added (areas: {', '.join(z.get('areas') or []) or NONE})")
    for zid, z in oz.items():
        if zid not in nz:
            out.append(f"Zone {_n(z)} removed")
    for zid in [z for z in nz if z in oz]:
        out += _zone_changes(oz[zid], nz[zid], name_of, mode_name)
    if [z for z in nz if z in oz] != [z for z in oz if z in nz]:
        out.append("Zone order changed")

    # ── schedule ────────────────────────────────────────────────────────────
    orules = [_rule(r, mode_name) for r in old.get("schedule") or []]
    nrules = [_rule(r, mode_name) for r in new.get("schedule") or []]
    for r in nrules:
        if r not in orules:
            out.append(f"Schedule · added: {r}")
    for r in orules:
        if r not in nrules:
            out.append(f"Schedule · removed: {r}")
    return out


# ── zones ───────────────────────────────────────────────────────────────────
def _zone_changes(a: dict, b: dict, name_of, mode_name) -> list[str]:
    z = _n(b)
    out = []
    if _n(a) != z:
        out.append(f"Zone {_n(a)} renamed {z}")
    if (a.get("areas") or []) != (b.get("areas") or []):
        out.append(f"{z} · areas: {', '.join(a.get('areas') or []) or NONE} → {', '.join(b.get('areas') or []) or NONE}")
    for key, label in (("lights", "own lights"), ("occupancy", "own occupancy sensors"), ("illuminance", "own light sensors")):
        if a.get(key) != b.get(key):
            out.append(f"{z} · {label}: {_names(a.get(key), name_of)} → {_names(b.get(key), name_of)}")
    ao, bo = a.get("overrides") or {}, b.get("overrides") or {}
    for mid in _keys(ao, bo):
        where = f"{z} · {'All modes' if mid == ALL_MODES else mode_name(mid)}"
        if mid not in ao:
            out.append(f"{where}: now {_choice(bo[mid], name_of, mode_name)}")
        elif mid not in bo:
            out.append(f"{where}: own setting removed ({'each mode' if mid == ALL_MODES else 'All modes or the mode'}'s setting applies)")
        else:
            out += _choice_changes(where, ao[mid], bo[mid], name_of, mode_name)
    ae, be = a.get("on_enter") or {}, b.get("on_enter") or {}
    for mid in _keys(ae, be):
        if ae.get(mid) != be.get(mid):
            out.append(f"{z} · when {mode_name(mid)} begins: {_targets(ae.get(mid), name_of)} → {_targets(be.get(mid), name_of)}")
    for key, label in (("follow", "follow"), ("overlay", "TV overlay"), ("buttons", "buttons")):
        out += _generic(f"{z} · {label}", a.get(key), b.get(key), name_of)
    return out


def _choice_changes(where: str, a: Any, b: Any, name_of, mode_name) -> list[str]:
    a, b = a or {}, b or {}
    if a == b:
        return []
    if a.get("behaviour") != b.get("behaviour"):
        return [f"{where}: {_choice(a, name_of, mode_name)} → {_choice(b, name_of, mode_name)}"]
    schema = BEHAVIOURS.get(b.get("behaviour"), {})
    ap, bp = a.get("params") or {}, b.get("params") or {}
    out = []
    for key in _keys(ap, bp):
        if ap.get(key) == bp.get(key):
            continue
        spec = schema.get(key, {})
        out.append(f"{where} · {spec.get('label', key)}: {_value(ap.get(key), spec, name_of, mode_name)} → "
                   f"{_value(bp.get(key), spec, name_of, mode_name)}")
    return out


def _generic(where: str, a: Any, b: Any, name_of) -> list[str]:
    """Follow / overlay / buttons: field by field, values compact."""
    if a == b:
        return []
    if not a:
        return [f"{where}: added"]
    if not b:
        return [f"{where}: removed"]
    if not (isinstance(a, dict) and isinstance(b, dict)):
        return [f"{where}: {_compact(a, name_of)} → {_compact(b, name_of)}"]
    out = []
    for key in _keys(a, b):
        if a.get(key) != b.get(key):
            sub = f"{where} · {name_of(key) if '.' in str(key) else key}"
            if isinstance(a.get(key), dict) and isinstance(b.get(key), dict) and not _looks_like_targets(b.get(key)):
                out += _generic(sub, a.get(key), b.get(key), name_of)
            else:
                out.append(f"{sub}: {_compact(a.get(key), name_of)} → {_compact(b.get(key), name_of)}")
    return out


# ── rendering values ────────────────────────────────────────────────────────
def _value(v: Any, spec: dict, name_of, mode_name) -> str:
    if v is None or v == {} or v == []:
        return NONE
    kind = spec.get("type")
    if kind == "targets":
        return _targets(v, name_of)
    if kind == "entities":
        return _names(v, name_of)
    if kind == "entity":
        return name_of(v)
    if kind == "mode":
        return mode_name(v)
    if kind == "steps":
        return "; ".join(f"+{s.get('after_minutes')} min: {_targets(s.get('targets'), name_of)}" for s in v if isinstance(s, dict))
    if kind == "bool":
        return "yes" if v else "no"
    unit = spec.get("unit")
    return f"{v} {unit}" if unit else str(v)


def _target(t: dict) -> str:
    if not isinstance(t, dict):
        return str(t)
    if "value" in t:
        return str(t["value"])
    if t.get("state") == "off":
        return "off"
    parts = []
    if t.get("min_brightness_pct") is not None:
        parts.append(f"≥{t['min_brightness_pct']}%")
    if t.get("brightness_pct") is not None:
        parts.append(f"{t['brightness_pct']}%")
    if t.get("color_temp_kelvin") is not None:
        parts.append(f"{t['color_temp_kelvin']} K")
    if t.get("effect"):
        parts.append(str(t["effect"]))
    return " ".join(parts) or "on"


def _targets(v: Any, name_of) -> str:
    if not v:
        return NONE
    if not isinstance(v, dict):
        return str(v)
    return ", ".join(f"{name_of(e)} {_target(t)}" for e, t in v.items())


def _looks_like_targets(v: Any) -> bool:
    return isinstance(v, dict) and bool(v) and all(isinstance(k, str) and "." in k and isinstance(t, dict) for k, t in v.items())


def _compact(v: Any, name_of) -> str:
    if v is None or v == {} or v == []:
        return NONE
    if _looks_like_targets(v):
        return _targets(v, name_of)
    if isinstance(v, list) and all(isinstance(x, str) and "." in x for x in v):
        return _names(v, name_of)
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _names(v: Any, name_of) -> str:
    if not v:
        return NONE
    return ", ".join(name_of(e) for e in v) if isinstance(v, list) else str(v)


def _choice(c: Any, name_of, mode_name) -> str:
    if not isinstance(c, dict) or not c.get("behaviour"):
        return NONE
    label = BEHAVIOUR_INFO.get(c["behaviour"], {}).get("label", c["behaviour"])
    schema = BEHAVIOURS.get(c["behaviour"], {})
    bits = [f"{schema.get(k, {}).get('label', k)} {_value(v, schema.get(k, {}), name_of, mode_name)}"
            for k, v in (c.get("params") or {}).items() if schema.get(k, {}).get("type") in ("number", "mode", "bool")]
    return f"{label} ({', '.join(bits)})" if bits else label


def _rule(r: dict, mode_name) -> str:
    if not isinstance(r, dict):
        return str(r)
    if r.get("day_set"):
        days = ("not " if r.get("not") else "") + ("eve of " if r.get("eve") else "") + str(r["day_set"])
    else:
        days = ", ".join(r.get("days") or []) or NONE
    return f"{days} {r.get('at')} → {mode_name(r.get('mode'))}"


# ── helpers ─────────────────────────────────────────────────────────────────
def _by_id(items: Any) -> dict[str, dict]:
    return {x["id"]: x for x in items or [] if isinstance(x, dict) and x.get("id")}


def _n(x: dict) -> str:
    return str(x.get("name") or x.get("id"))


def _keys(a: dict, b: dict) -> list:
    return list(dict.fromkeys([*a.keys(), *b.keys()]))


def _mode_names(old: dict, new: dict) -> Callable[[Any], str]:
    names = {m["id"]: _n(m) for m in [*(old.get("modes") or []), *(new.get("modes") or [])] if isinstance(m, dict) and m.get("id")}
    return lambda mid: NONE if mid is None else names.get(mid, str(mid))
