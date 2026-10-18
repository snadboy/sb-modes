"""Offline checks for model.py and validate.py.   python3 tests/test_validate.py   (or pytest)

The two modules are loaded as a bare package, so their relative imports work without
running the integration's __init__ (which needs Home Assistant).
"""

import copy
import importlib.util
import pathlib
import sys
import types

_dir = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "sb_modes"
_pkg = types.ModuleType("sbm")
_pkg.__path__ = [str(_dir)]
sys.modules["sbm"] = _pkg


def _load(name):
    spec = importlib.util.spec_from_file_location(f"sbm.{name}", _dir / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load("model")
V = _load("validate")

STATES = {
    "light.kitchen_aux_sink": "on",
    "light.kitchen_aux_cabinet": "off",
    "light.dining_room_aux_sideboard": "unavailable",
    "switch.kitchen_aux_cabinet": "on",
    "climate.bedroom_ac": "cool",
}
AREAS = {"kitchen", "kitchen_aux", "dining_room", "dining_room_aux"}
state_of = STATES.get
area_exists = AREAS.__contains__

GOOD = {
    "fallback_mode": "day",
    "modes": [
        {"id": "day", "name": "Day", "default": {"behaviour": "hands_off", "params": {}}},
        {"id": "overnight", "name": "Overnight", "default": {"behaviour": "nightlight_then_dark", "params": {"delay": 10}}},
    ],
    "zones": [
        {"id": "kitchen", "name": "Kitchen", "areas": ["kitchen", "kitchen_aux"],
         "overrides": {"overnight": {"behaviour": "nightlight_then_dark",
                                     "params": {"nightlight": {"light.kitchen_aux_sink": {"brightness_pct": 10},
                                                                "light.kitchen_aux_cabinet": {"state": "on"}},
                                                 "delay": 10}}}},
        {"id": "dining", "name": "Dining Room", "areas": ["dining_room", "dining_room_aux"]},
    ],
}


def check(cfg):
    return V.check_config(cfg, state_of, area_exists)


def edit(fn):
    cfg = copy.deepcopy(GOOD)
    fn(cfg)
    return check(cfg)


def test_good():
    assert check(GOOD) == ([], []), check(GOOD)


def test_default_config_is_valid():
    assert check(M.default_config()) == ([], [])


def test_resolve_override_and_default():
    assert M.resolve(GOOD, "overnight", "kitchen") == {
        "behaviour": "nightlight_then_dark", "source": "override",
        "params": {"nightlight": {"light.kitchen_aux_sink": {"brightness_pct": 10},
                                  "light.kitchen_aux_cabinet": {"state": "on"}}, "delay": 10}}
    assert M.resolve(GOOD, "overnight", "dining") == {"behaviour": "nightlight_then_dark", "params": {"delay": 10}, "source": "default"}
    assert M.resolve(GOOD, "evening", "dining") is None
    assert M.resolve(GOOD, "day", "nowhere") is None


def test_every_mode_zone_pair_resolves():
    for m in M.mode_ids(GOOD):
        for z in GOOD["zones"]:
            assert M.resolve(GOOD, m, z["id"])["behaviour"] in M.BEHAVIOURS


def test_mode_needs_a_default():
    e, _ = edit(lambda c: c["modes"][0].pop("default"))
    assert e == ["Mode Day: needs a default behaviour (a mode exists only through its default)."], e


def test_override_of_undefined_mode():
    e, _ = edit(lambda c: c["zones"][1].update(overrides={"evening": {"behaviour": "hands_off"}}))
    assert e == ["Zone Dining Room: overrides mode “evening”, which is not defined."], e


def test_fallback_must_exist():
    e, _ = edit(lambda c: c.update(fallback_mode="evening"))
    assert e == ["The fallback mode “evening” is not a defined mode."], e
    e, _ = edit(lambda c: c.pop("fallback_mode"))
    assert e[0].startswith("Choose a fallback mode"), e


def test_no_modes():
    e, _ = check({"fallback_mode": "day", "modes": [], "zones": []})
    assert "Define at least one mode." in e, e


def test_duplicate_and_bad_ids():
    e, _ = edit(lambda c: c["modes"].append({"id": "day", "name": "Again", "default": {"behaviour": "hands_off"}}))
    assert e == ["Mode id “day” is used more than once."], e
    e, _ = edit(lambda c: c["zones"][0].update(id="Kitchen Zone"))
    assert e[0].startswith("Zone Kitchen: id 'Kitchen Zone' must be lowercase"), e


def test_unknown_behaviour_and_param():
    e, _ = edit(lambda c: c["modes"][0].update(default={"behaviour": "disco"}))
    assert e[0].startswith("Mode Day, default: unknown behaviour 'disco'"), e
    e, _ = edit(lambda c: c["modes"][1]["default"]["params"].update(colour="red"))
    assert e == ["Mode Overnight, default: nightlight_then_dark has no parameter “colour”."], e


def test_required_param():
    e, _ = edit(lambda c: c["modes"][0].update(default={"behaviour": "presence_lux", "params": {}}))
    assert e == ["Mode Day, default: presence_lux needs “target_lux”."], e


def test_number_range_and_type():
    e, _ = edit(lambda c: c["modes"][1]["default"]["params"].update(delay=0))
    assert e == ["Mode Overnight, default, delay: 0 is outside 1–240."], e
    e, _ = edit(lambda c: c["modes"][1]["default"]["params"].update(delay="ten"))
    assert e == ["Mode Overnight, default, delay: 'ten' is not a number."], e
    e, _ = edit(lambda c: c["modes"][1]["default"]["params"].update(delay=True))
    assert e == ["Mode Overnight, default, delay: True is not a number."], e


def test_entity_checks():
    def nl(eid):
        return lambda c: c["zones"][0]["overrides"]["overnight"]["params"].update(nightlight={eid: {"state": "on"}})
    e, _ = edit(nl("light.gone"))
    assert e == ["Zone Kitchen, Overnight, nightlight: light.gone does not exist."], e
    e, _ = edit(nl("climate.bedroom_ac"))
    assert e == ["Zone Kitchen, Overnight, nightlight: no driver controls climate entities (climate.bedroom_ac)."], e
    e, w = edit(nl("light.dining_room_aux_sideboard"))
    assert e == [] and w == ["Zone Kitchen, Overnight, nightlight: light.dining_room_aux_sideboard is unavailable."], (e, w)


FOUND = {
    "kitchen": {"lights": ["light.kitchen_aux_sink"], "occupancy": ["binary_sensor.kitchen_occ"], "illuminance": []},
    "dining_room": {"lights": ["light.chandelier"], "occupancy": [], "illuminance": []},
}


def zone_entities(areas):
    out = {"lights": [], "occupancy": [], "illuminance": []}
    for a in areas:
        for k, v in FOUND.get(a, {}).items():
            out[k] += v
    return out


def test_needs_from_areas():
    e, _ = V.check_config(GOOD, state_of, area_exists, zone_entities)
    assert e == ["Zone Dining Room: nightlight_then_dark (Overnight) needs an occupancy sensor in the zone's areas; there is none."], e
    cfg = copy.deepcopy(GOOD)
    cfg["zones"][1]["overrides"] = {"overnight": {"behaviour": "hands_off"}}
    assert V.check_config(cfg, state_of, area_exists, zone_entities) == ([], [])
    cfg["modes"][0]["default"] = {"behaviour": "presence_lux", "params": {"target_lux": 50}}
    e, _ = V.check_config(cfg, state_of, area_exists, zone_entities)
    assert e == ["Zone Kitchen: presence_lux (Day) needs an illuminance sensor in the zone's areas; there is none.",
                 "Zone Dining Room: presence_lux (Day) needs an occupancy sensor in the zone's areas; there is none.",
                 "Zone Dining Room: presence_lux (Day) needs an illuminance sensor in the zone's areas; there is none."], e


def test_defaults_filled_in():
    assert M.with_defaults("nightlight_then_dark", {}) == {"delay": 10}
    assert M.with_defaults("nightlight_then_dark", {"delay": 3}) == {"delay": 3}


def test_targets_need_a_driver():
    targets = {"light.kitchen_aux_sink": {"state": "on", "brightness_pct": 50}, "climate.bedroom_ac": {"state": "on"}}
    e, _ = edit(lambda c: c["modes"][0].update(default={"behaviour": "static", "params": {"targets": targets}}))
    assert e == ["Mode Day, default, targets: no driver controls climate entities (climate.bedroom_ac)."], e


def test_target_values():
    targets = {"light.kitchen_aux_sink": {"state": "dim", "brightness_pct": 150, "hue": 3}}
    e, _ = edit(lambda c: c["modes"][0].update(default={"behaviour": "static", "params": {"targets": targets}}))
    assert e == ["Mode Day, default, targets, light.kitchen_aux_sink: unknown target key “hue”.",
                 "Mode Day, default, targets, light.kitchen_aux_sink: state must be on or off.",
                 "Mode Day, default, targets, light.kitchen_aux_sink: brightness_pct must be 0–100."], e


def test_areas():
    e, _ = edit(lambda c: c["zones"][1].update(areas=["dining_room", "attic"]))
    assert e == ["Zone Dining Room: area “attic” does not exist."], e
    e, _ = edit(lambda c: c["zones"][1].update(areas=[]))
    assert e == ["Zone Dining Room: list at least one area."], e


SCHEDULE = [
    {"days": ["mon", "tue", "wed", "thu", "fri"], "at": "09:00", "mode": "day"},
    {"days": ["sun", "mon", "tue", "wed", "thu"], "at": "23:00", "mode": "overnight"},
    {"days": ["sat", "sun"], "at": "00:30", "mode": "overnight"},
    {"days": ["sat", "sun"], "at": "09:00", "mode": "day"},
]


def _at(y, mo, d, h, mi):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo("America/Chicago"))


def test_schedule_validates():
    cfg = copy.deepcopy(GOOD); cfg["schedule"] = SCHEDULE
    assert check(cfg) == ([], [])
    cfg["schedule"] = SCHEDULE + [{"days": ["sat", "funday"], "at": "9:00", "mode": "evening"},
                                  {"days": ["mon"], "at": "09:00", "mode": "overnight"}]
    e, _ = check(cfg)
    assert e == ["Schedule rule 5: mode “evening” is not defined.",
                 "Schedule rule 5: time '9:00' must be HH:MM (24-hour).",
                 "Schedule rule 5: 'funday' is not a day (mon, tue, wed, thu, fri, sat, sun).",
                 "Schedule rule 6: Day and Overnight both start at 09:00 on mon."], e


def test_scheduled_mode_weekday_and_weekend():
    cfg = {"schedule": SCHEDULE}
    # 2026-10-16 is a Friday
    assert M.scheduled_mode(cfg, _at(2026, 10, 16, 10, 0))[0] == "day"
    assert M.scheduled_mode(cfg, _at(2026, 10, 16, 23, 30))[0] == "day"          # Friday night: no 23:00
    assert M.scheduled_mode(cfg, _at(2026, 10, 17, 0, 31))[0] == "overnight"     # Saturday 00:30
    assert M.scheduled_mode(cfg, _at(2026, 10, 17, 8, 59))[0] == "overnight"     # weekend: no 06:00 morning
    assert M.scheduled_mode(cfg, _at(2026, 10, 17, 9, 0))[0] == "day"
    assert M.scheduled_mode(cfg, _at(2026, 10, 18, 23, 5))[0] == "overnight"     # Sunday night 23:00
    assert M.next_change(cfg, _at(2026, 10, 16, 10, 0)) == ("overnight", _at(2026, 10, 17, 0, 30))
    assert M.next_change(cfg, _at(2026, 10, 18, 9, 0)) == ("overnight", _at(2026, 10, 18, 23, 0))
    assert M.scheduled_mode({}, _at(2026, 10, 16, 10, 0)) == (None, None)


def test_not_an_object():
    assert check([]) == (["The table must be an object."], [])


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
    print(f"{n} checks passed")
