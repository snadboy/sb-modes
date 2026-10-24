"""What changed between two tables, in words — the editor's Review and each saved version.

Pure (no Home Assistant): `describe(old, new, name_of)` with name_of(entity_id) → a display name.
One line per change, "Where · what: before → after", for example
  "Kitchen · All modes · Empty, dark: Ceiling off → Ceiling off, Aux Sink 10%".
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from .model import ALL_MODES, BEHAVIOUR_INFO, BEHAVIOURS, zone_addons

NONE = "—"


def describe(old: dict, new: dict, name_of: Callable[[str], str] = lambda e: e) -> list[str]:
    old, new = old or {}, new or {}
    out: list[str] = []
    mode_name = _mode_names(old, new)
    types = {t.get("id"): t.get("name") or t.get("id") for c in (old, new) for t in c.get("day_types") or [] if isinstance(t, dict)}
    day_name = lambda d: types.get(d, d)   # noqa: E731

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
        out += _zone_changes(oz[zid], nz[zid], name_of, mode_name, day_name)
    if [z for z in nz if z in oz] != [z for z in oz if z in nz]:
        out.append("Zone order changed")

    # ── day types and their times ───────────────────────────────────────────
    ot, nt = _by_id(old.get("day_types")), _by_id(new.get("day_types"))
    tname = lambda tid: _n(nt.get(tid) or ot.get(tid) or {"id": tid})  # noqa: E731
    for tid, t in nt.items():
        if tid not in ot:
            out.append(f"Day type {_n(t)} added ({_calendar(t)})")
    for tid, t in ot.items():
        if tid not in nt:
            out.append(f"Day type {_n(t)} removed")
    for tid in [t for t in nt if t in ot]:
        a, b = ot[tid], nt[tid]
        if _n(a) != _n(b):
            out.append(f"Day type {_n(a)} renamed {_n(b)}")
        if a.get("day_set") != b.get("day_set"):
            out.append(f"Day type {_n(b)}: {_calendar(a)} → {_calendar(b)}")
    if [t for t in nt if t in ot] != [t for t in ot if t in nt]:
        out.append("Day type order: " + ", ".join(tname(t) for t in ot if t in nt) + " → " + ", ".join(tname(t) for t in nt if t in ot))
    otimes, ntimes = old.get("times") or {}, new.get("times") or {}
    for tid in _keys(otimes, ntimes):
        out += _paired(f"{tname(tid)}", [_entry(e, mode_name) for e in otimes.get(tid) or []],
                       [_entry(e, mode_name) for e in ntimes.get(tid) or []])

    # ── other rules ─────────────────────────────────────────────────────────
    orules = [_rule(r, mode_name) for r in old.get("schedule") or []]
    nrules = [_rule(r, mode_name) for r in new.get("schedule") or []]
    out += _paired("Schedule", orules, nrules)
    return out


def _paired(where: str, old: list[str], new: list[str]) -> list[str]:
    """Two lists of described items: unchanged ones (wherever they sit) say nothing, an edited one
    pairs with the leftover it replaced ("A → B"), the rest are added / removed."""
    gone, came = list(old), []
    for r in new:
        if r in gone:
            gone.remove(r)
        else:
            came.append(r)
    out = [f"{where}: {a} → {b}" for a, b in zip(gone, came)]
    out += [f"{where} · added: {r}" for r in came[len(gone):]]
    out += [f"{where} · removed: {r}" for r in gone[len(came):]]
    return out


def _entry(e: dict, mode_name) -> str:
    if not isinstance(e, dict):
        return str(e)
    return f"{mode_name(e.get('mode'))} at {_at(e.get('at'))}" + (" the night before" if e.get("eve") else "")


def _auto(r: dict, name_of) -> str:
    """'Side Window Lamp off with the empty setting, or once on 15 min with the room empty'."""
    if not isinstance(r, dict):
        return str(r)
    when = [w for w in ("with the empty setting" if r.get("with_empty") else "",
                        f"once on {r['on_minutes']:g} min with the room empty" if r.get("on_minutes") else "") if w]
    return f"{_names(r.get('lights'), name_of)} off " + (", or ".join(when) or "(never)")


def _calendar(t: dict) -> str:
    return f"calendar {t['day_set']}" if t.get("day_set") else "every other day"


# ── zones ───────────────────────────────────────────────────────────────────
def _zone_changes(a: dict, b: dict, name_of, mode_name, day_name=lambda d: d) -> list[str]:
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
    out += _addon_changes(z, zone_addons(a), zone_addons(b), name_of, mode_name, day_name)
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
        days = _days(r.get("days") or [])
    on = days if days in ("every day", "weekdays", "weekends") else f"on {days}"
    return f"{mode_name(r.get('mode'))} at {_at(r.get('at'))} {on}"


def _at(at) -> str:
    """'sunset-01:30' → 'sunset − 1:30'; clock times stay as they are."""
    m = re.fullmatch(r"(sunrise|sunset)([+-])(\d{1,2}):(\d{2})", str(at or ""))
    return f"{m[1]} {'−' if m[2] == '-' else '+'} {int(m[3])}:{m[4]}" if m else str(at)


def _days(days: list) -> str:
    named = {("mon", "tue", "wed", "thu", "fri", "sat", "sun"): "every day", ("mon", "tue", "wed", "thu", "fri"): "weekdays",
             ("sat", "sun"): "weekends"}
    order = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    key = tuple(d for d in order if d in days)
    return named.get(key) or ", ".join(d.capitalize() for d in key) or NONE


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


# ── add-ons ─────────────────────────────────────────────────────────────────
def _hold_txt(act: Any, name_of) -> str:
    if act == "release" or (isinstance(act, dict) and act.get("release")):
        txt = "release"
    elif isinstance(act, dict) and "hold" in act:
        h = act["hold"]
        txt = {"next": "hold until the next mode", "paused": "hold until released"}.get(h, f"hold {h} min")
        if act.get("targets"):
            txt += f", {_targets(act['targets'], name_of)}"
    else:
        return _compact(act, name_of)
    if isinstance(act, dict) and act.get("flash"):
        txt += f", flash ×{act['flash']}" + (f" ({_names(act['flash_lights'], name_of)})" if act.get("flash_lights") else "")
    return txt


def _trigger_txt(t: Any, name_of, mode_name) -> str:
    t = t if isinstance(t, dict) else {}
    if "at" in t:
        return f"at {_at(t['at'])}" + (f" ±{t['random_minutes']:g} min" if t.get("random_minutes") else "")
    if t.get("mode_begins"):
        return " or ".join(mode_name(m) for m in t["mode_begins"]) + " begins"
    if t.get("mode_ends"):
        return " or ".join(mode_name(m) for m in t["mode_ends"]) + " ends"
    if t.get("calendar"):
        off = t.get("offset_minutes") or 0
        lead = f"{abs(off):g} min {'before' if off < 0 else 'after'} " if off else "when "
        what = f"“{t['match']}” on {name_of(t['calendar'])}" if t.get("match") else f"an event on {name_of(t['calendar'])}"
        return f"{lead}{what} {'ends' if t.get('edge') == 'end' else 'starts'}"
    if t.get("entity"):
        return f"{name_of(t['entity'])} becomes {t.get('to', '?')}"
    return NONE


def _when_txt(w: Any, mode_name, day_name=lambda d: d) -> str:
    w = w if isinstance(w, dict) else {}
    parts = []
    if w.get("modes"):
        parts.append("in " + ", ".join(mode_name(m) for m in w["modes"]))
    if w.get("days"):
        parts.append("only on " + ", ".join(day_name(d) for d in w["days"]))
    if w.get("not_days"):
        parts.append("never on " + ", ".join(day_name(d) for d in w["not_days"]))
    if w.get("between"):
        parts.append("between " + "–".join(w["between"]))
    return " · ".join(parts)


def _step_txt(st: Any, name_of, mode_name=lambda m: m) -> str:
    if not isinstance(st, dict):
        return str(st)
    lead = f"+{st['after_minutes']:g} min " if st.get("after_minutes") else ""
    if st.get("random_minutes"):
        lead = f"+{st.get('after_minutes', 0):g}–{st.get('after_minutes', 0) + st['random_minutes']:g} min "
    if st.get("set_mode"):
        h = st.get("hold", "paused")
        until = {"paused": "until released", "next": "until the next scheduled change"}.get(h, f"for {h} min")
        return f"{lead}house mode {mode_name(st['set_mode'])} {until}"
    if st.get("resume_schedule"):
        return f"{lead}back on schedule"
    if st.get("script"):
        return f"{lead}run {st['script']}"
    if "hold" in st or st.get("release"):
        return lead + _hold_txt({k: v for k, v in st.items() if k != "after_minutes"}, name_of)
    return lead + _targets(st.get("targets"), name_of)


def addon_summary(a: dict, name_of=lambda e: e, mode_name=lambda m: m, day_name=lambda d: d) -> str:
    """One line for an add-on instance: what triggers it, when, and what it does."""
    kind = a.get("type")
    if kind == "lights":
        txt = f"{_trigger_txt(a.get('trigger'), name_of, mode_name)} → " + " · ".join(_step_txt(s, name_of, mode_name) for s in a.get("steps") or [])
    elif kind == "while":
        txt = f"while {name_of(a.get('entity') or '?')} is " + ", ".join(
            f"{st}{' (holds)' if (c or {}).get('holds') else ''}" for st, c in (a.get("states") or {}).items())
    elif kind == "button":
        txt = f"{name_of(a.get('entity') or '?')}: " + ", ".join(
            f"{et} → {_hold_txt(act, name_of)}" for et, act in (a.get("presses") or {}).items())
    elif kind == "follow":
        txt = f"follows {name_of(a.get('leader') or '?')} ({len(a.get('bands') or [])} bands)"
    elif kind == "auto_off":
        txt = _auto(a, name_of)
    elif kind == "replay":
        txt = f"{_names(a.get('lights'), name_of)} replay {a.get('days_back', 7):g} days ago"
    else:
        txt = str(kind)
    when = _when_txt(a.get("when"), mode_name, day_name)
    return txt + (f" · {when}" if when else "") + ("" if a.get("enabled", True) else " · off")


def _addon_changes(z: str, old: list, new: list, name_of, mode_name, day_name=lambda d: d) -> list[str]:
    oa = {a.get("id"): a for a in old if isinstance(a, dict)}
    na = {a.get("id"): a for a in new if isinstance(a, dict)}
    out = []
    for aid, a in na.items():
        if aid not in oa:
            out.append(f"{z} · add-on {a.get('name') or aid} added: {addon_summary(a, name_of, mode_name, day_name)}")
    for aid, a in oa.items():
        if aid not in na:
            out.append(f"{z} · add-on {a.get('name') or aid} removed")
    for aid in [i for i in na if i in oa]:
        a, b = oa[aid], na[aid]
        if a == b:
            continue
        where = f"{z} · add-on {b.get('name') or aid}"
        if a.get("name") != b.get("name"):
            out.append(f"{z} · add-on {a.get('name') or aid} renamed {b.get('name')}")
        if a.get("enabled", True) != b.get("enabled", True):
            out.append(f"{where}: turned {'on' if b.get('enabled', True) else 'off'}")
        rest_a = {k: v for k, v in a.items() if k not in ("name", "enabled")}
        rest_b = {k: v for k, v in b.items() if k not in ("name", "enabled")}
        if rest_a != rest_b:
            out.append(f"{where}: {addon_summary({**a, 'enabled': True}, name_of, mode_name, day_name)} → "
                       f"{addon_summary({**b, 'enabled': True}, name_of, mode_name, day_name)}")
    shared = [i for i in oa if i in na]
    if [i for i in na if i in oa] != shared:
        out.append(f"{z} · add-on order: " + ", ".join(oa[i].get("name") or i for i in shared) + " → "
                   + ", ".join(na[i].get("name") or i for i in na if i in oa))
    return out
