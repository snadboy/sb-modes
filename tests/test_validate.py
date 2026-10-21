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
MAN = _load("manual")
CH = _load("changes")

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
    assert e == ["Mode Day, default: presence_lux needs “target_lux”.", "Mode Day, default: presence_lux needs “occupied”."], e


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
    cfg["modes"][0]["default"] = {"behaviour": "presence_lux", "params": {"target_lux": 50, "occupied": {"light.kitchen_aux_sink": {"brightness_pct": 80}}}}
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
                 "Schedule rule 5: time '9:00' must be HH:MM (24-hour), sunrise or sunset, optionally ±HH:MM (sunset-01:00).",
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


def test_manual_classification():
    own = {"ours"}
    assert MAN.classify("ours", None, None, own, None) is None                  # our own call
    assert MAN.classify("x", None, "ours", own, None) is None                   # descended from ours
    assert MAN.classify("x", "user1", None, own, None) == "person"              # app / dashboard / voice
    assert MAN.classify("x", None, "automation-ctx", own, None) is None         # another automation
    assert MAN.classify("x", None, None, own, None) == "device"                 # wall switch / Lutron app
    assert MAN.classify("x", None, None, own, 3.0) is None                      # the device echoing our command
    assert MAN.classify("x", None, None, own, 60.0) == "device"


def test_significant_changes():
    assert MAN.significant("off", "on", None, 128)
    assert MAN.significant("on", "on", 100, 140)
    assert not MAN.significant("on", "on", 100, 102)                           # rounding noise
    assert not MAN.significant("unavailable", "on", None, 128)
    assert not MAN.significant("off", "off", None, None)


def test_all_modes_setting_precedence():
    cfg = copy.deepcopy(GOOD)
    cfg["zones"][1]["overrides"] = {"*": {"behaviour": "vacancy_off", "params": {"lights": ["light.kitchen_aux_sink"]}}}
    assert check(cfg) == ([], [])
    assert M.resolve(cfg, "day", "dining")["source"] == "zone"                 # zone-wide beats the mode default
    assert M.resolve(cfg, "overnight", "dining")["behaviour"] == "vacancy_off"
    cfg["zones"][1]["overrides"]["overnight"] = {"behaviour": "hands_off"}
    assert M.resolve(cfg, "overnight", "dining") == {"behaviour": "hands_off", "params": {}, "source": "override"}
    assert M.resolve(cfg, "day", "dining")["params"] == {"lights": ["light.kitchen_aux_sink"], "off_after_seconds": 60,
                                                        "max_on_minutes": 0, "on_when_occupied": False}


def test_new_param_types():
    cfg = copy.deepcopy(GOOD)
    cfg["zones"][1]["overrides"] = {"*": {"behaviour": "vacancy_off", "params": {"lights": [], "on_when_occupied": "yes"}}}
    e, _ = check(cfg)
    assert e == ["Zone Dining Room, all modes, lights: must be a list of entity ids.",
                 "Zone Dining Room, all modes, on_when_occupied: 'yes' must be true or false."], e
    STATES["number.clock_brightness"] = "64"
    cfg["zones"][1]["overrides"] = {"*": {"behaviour": "occupied_vacant", "params": {
        "occupied": {"number.clock_brightness": {"value": 255}, "light.kitchen_aux_sink": {"hs_color": [220, 50], "effect": "Breath"}},
        "vacant": {"number.clock_brightness": {"value": "dim"}}}}}
    e, _ = check(cfg)
    assert e == ["Zone Dining Room, all modes, vacant, number.clock_brightness: a number entity needs a numeric value."], e


WORKDAYS = {"2026-10-16", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"}   # Fri, then Mon–Thu; Fri 23rd off


def in_set(ds, d):
    from datetime import date
    if ds != "workday" or d > date(2026, 10, 30):
        return None
    return d.isoformat() in WORKDAYS


def sun(event, d):
    return _at(d.year, d.month, d.day, 18, 10) if event == "sunset" else _at(d.year, d.month, d.day, 7, 0)


DAYSET = [
    {"day_set": "workday", "at": "06:00", "mode": "morning"},
    {"days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], "at": "09:00", "mode": "day"},
    {"days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], "at": "sunset-01:00", "mode": "evening"},
    {"day_set": "workday", "eve": True, "at": "23:00", "mode": "overnight"},
    {"day_set": "workday", "not": True, "at": "00:30", "mode": "overnight"},
]


def test_parse_when():
    assert M.parse_when("06:30") == ("clock", 6, 30)
    assert M.parse_when("sunset") == ("sun", "sunset", 0)
    assert M.parse_when("sunset-01:00") == ("sun", "sunset", -60)
    assert M.parse_when("sunrise+00:30") == ("sun", "sunrise", 30)
    assert M.parse_when("sunset-1:00") is None and M.parse_when("noon") is None


def test_day_set_and_sun_schedule():
    cfg = {"schedule": DAYSET}
    sm = lambda *a: M.scheduled_mode(cfg, _at(*a), in_set, sun)[0]
    assert sm(2026, 10, 19, 6, 1) == "morning"            # Monday, a workday
    assert sm(2026, 10, 19, 17, 11) == "evening"          # sunset 18:10 − 1 h = 17:10
    assert sm(2026, 10, 19, 23, 1) == "overnight"         # eve of Tuesday, a workday
    assert sm(2026, 10, 22, 23, 1) == "evening"           # Thursday: Friday 23rd is a day off → no 23:00
    assert sm(2026, 10, 23, 0, 31) == "overnight"         # Friday (day off) 00:30
    assert sm(2026, 10, 23, 8, 0) == "overnight"          # day off: no 06:00 Morning
    assert sm(2026, 10, 23, 9, 0) == "day"
    assert M.next_change(cfg, _at(2026, 10, 19, 10, 0), in_set, sun) == ("evening", _at(2026, 10, 19, 17, 10))
    assert M.rule_day_sets(cfg) == {"workday"}


def test_unknown_day_set_membership_is_skipped():
    cfg = {"schedule": DAYSET}
    assert M.scheduled_mode(cfg, _at(2026, 10, 19, 6, 1))[0] == "day"   # no resolver: day-set rules unknown


def test_day_set_rule_validation():
    cfg = copy.deepcopy(GOOD)
    cfg["schedule"] = [{"day_set": "workday", "at": "06:00", "mode": "day"},
                       {"day_set": "payday", "at": "sunset-01:00", "mode": "day"},
                       {"day_set": "workday", "days": ["mon"], "at": "06:00", "mode": "day"},
                       {"day_set": "workday", "eve": "yes", "at": "06:00", "mode": "day"}]
    e, _ = V.check_config(cfg, state_of, area_exists, None, lambda ds: ds == "workday")
    assert e == ["Schedule rule 2: day set 'payday' is not published by SB Scheduler.",
                 "Schedule rule 3: give either days (mon, tue, wed, thu, fri, sat, sun) or a day_set — one of the two.",
                 "Schedule rule 4: eve must be true or false."], e


def test_zone_lights_must_not_overlap():
    cfg = copy.deepcopy(GOOD)
    cfg["zones"][1]["areas"] = ["kitchen"]                 # same area as Kitchen → same lights
    e, _ = V.check_config(cfg, state_of, area_exists, zone_entities)
    assert "Zone Dining Room: light.kitchen_aux_sink is also in Zone Kitchen — give one of them its own `lights` list." in e, e
    cfg["zones"][1]["lights"] = ["light.kitchen_aux_cabinet"]
    e, _ = V.check_config(cfg, state_of, area_exists, zone_entities)
    assert not [x for x in e if "also in" in x], e


def test_not_an_object():
    assert check([]) == (["The table must be an object."], [])


def test_messages_carry_field_paths():
    cfg = copy.deepcopy(GOOD)
    k = cfg["zones"][0]["overrides"]["overnight"]["params"]
    k["delay"] = 999                                                # out of range
    k["nightlight"]["light.kitchen_aux_sink"] = {"brightness_pct": 150}
    cfg["modes"][0]["default"] = {"behaviour": "hands_off", "params": {"bogus": 1}}
    cfg["zones"][1]["areas"] = ["attic"]
    cfg["schedule"] = [{"days": ["mon"], "at": "25:00", "mode": "day"}]
    cfg["fallback_mode"] = "party"
    e, w = check(cfg)
    got = {tuple(d["path"]) for d in e.detailed()}
    assert ("zones", 0, "overrides", "overnight", "params", "delay") in got, got
    assert ("zones", 0, "overrides", "overnight", "params", "nightlight", "light.kitchen_aux_sink") in got, got
    assert ("modes", 0, "default", "params", "bogus") in got, got
    assert ("zones", 1, "areas") in got, got
    assert ("schedule", 0) in got, got
    assert ("fallback_mode",) in got, got
    # warnings too, and the plain lists still read as before
    assert any(d["path"] == ["zones", 0, "overrides", "overnight", "params", "nightlight", "light.kitchen_aux_cabinet"]
               for d in w.detailed()) is False                  # off is not unavailable
    assert all(isinstance(m, str) for m in e)


def test_paths_do_not_leak_between_checks():
    e, _ = check(copy.deepcopy(GOOD))
    assert e == [] and V._PATH.get() == ()


NAMES = {"light.kitchen_aux_sink": "Aux Sink", "light.kitchen_aux_cabinet": "Cabinet", "light.ceiling": "Ceiling"}


def test_changes_in_words():
    a = copy.deepcopy(GOOD)
    a["schedule"] = [{"days": ["mon"], "at": "06:00", "mode": "day"}]
    b = copy.deepcopy(a)
    assert CH.describe(a, b, NAMES.get) == []
    b["modes"][0]["name"] = "Daytime"
    b["modes"].append({"id": "party", "name": "Party", "default": {"behaviour": "hands_off"}})
    b["fallback_mode"] = "overnight"
    k = b["zones"][0]
    k["overrides"]["overnight"]["params"]["delay"] = 5
    k["overrides"]["overnight"]["params"]["nightlight"] = {"light.kitchen_aux_sink": {"brightness_pct": 20}}
    k["overrides"]["*"] = {"behaviour": "presence_lux", "params": {"target_lux": 50, "occupied": {"light.ceiling": {"brightness_pct": 100}}}}
    k["on_enter"] = {"day": {"light.kitchen_aux_cabinet": {"state": "on"}}}
    b["zones"][1]["name"] = "Dining"
    b["schedule"] = [{"days": ["mon", "tue"], "at": "06:00", "mode": "day"}]
    lines = CH.describe(a, b, lambda e: NAMES.get(e, e))
    assert "Mode Day renamed Daytime" in lines, lines
    assert "Mode Party added (default: Hands off)" in lines, lines
    assert "Fallback mode: Daytime → Overnight" in lines, lines
    assert "Kitchen · Overnight · Dark after empty: 10 min → 5 min" in lines, lines
    assert "Kitchen · Overnight · Nightlight: Aux Sink 10%, Cabinet on → Aux Sink 20%" in lines, lines
    assert "Kitchen · All modes: now Presence + light level (Dark below 50 lx)" in lines, lines
    assert "Kitchen · when Daytime begins: — → Cabinet on" in lines, lines
    assert "Zone Dining Room renamed Dining" in lines, lines
    assert "Schedule: Daytime at 06:00 on Mon → Daytime at 06:00 on Mon, Tue" in lines, lines
    d = copy.deepcopy(b)
    d["schedule"].append({"days": ["mon", "tue", "wed", "thu", "fri"], "at": "sunset-01:00", "mode": "overnight"})
    assert CH.describe(b, d) == ["Schedule · added: Overnight at sunset − 1:00 weekdays"], CH.describe(b, d)
    # removing a zone setting, and a behaviour swap
    c = copy.deepcopy(b)
    del c["zones"][0]["overrides"]["overnight"]
    c["zones"][0]["overrides"]["*"] = {"behaviour": "vacancy_off", "params": {"lights": ["light.ceiling"]}}
    lines = CH.describe(b, c, lambda e: NAMES.get(e, e))
    assert "Kitchen · Overnight: own setting removed (All modes or the mode's setting applies)" in lines, lines
    assert "Kitchen · All modes: Presence + light level (Dark below 50 lx) → Off when empty" in lines, lines


LIVE_RULES = [
    {"day_set": "workday", "at": "06:00", "mode": "morning"},
    {"day_set": "workday", "not": True, "at": "08:00", "mode": "morning"},
    {"days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], "at": "09:00", "mode": "day"},
    {"days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], "at": "sunset-01:00", "mode": "evening"},
    {"day_set": "workday", "eve": True, "at": "23:00", "mode": "overnight"},
    {"day_set": "workday", "not": True, "at": "00:30", "mode": "overnight"},
]
AS_DAY_TYPES = {
    "day_types": [{"id": "vacation", "name": "Vacation", "day_set": "vacation"},
                  {"id": "workday", "name": "Workdays", "day_set": "workday"},
                  {"id": "day_off", "name": "Days off"}],
    "times": {
        "workday": [{"mode": "morning", "at": "06:00"}, {"mode": "day", "at": "09:00"},
                    {"mode": "evening", "at": "sunset-01:00"}, {"mode": "overnight", "at": "23:00", "eve": True}],
        "day_off": [{"mode": "overnight", "at": "00:30"}, {"mode": "morning", "at": "08:00"},
                    {"mode": "day", "at": "09:00"}, {"mode": "evening", "at": "sunset-01:00"}],
        "vacation": [{"mode": "overnight", "at": "01:00"}, {"mode": "morning", "at": "09:30"},
                     {"mode": "evening", "at": "sunset-01:00"}],
    },
}


def _week_sets(vacation=()):
    import datetime as D
    holiday = {D.date(2026, 11, 26)}                       # Thanksgiving, a Thursday
    def in_set(ds, d):
        if ds == "workday":
            return d.weekday() < 5 and d not in holiday
        if ds == "vacation":
            return d in set(vacation)
        return None
    return in_set


def _sun(event, d):
    import datetime as D
    return D.datetime(d.year, d.month, d.day, 23, 4, tzinfo=D.timezone.utc)   # 18:04 local (UTC-5)


def test_day_types_reproduce_the_live_rules():
    import datetime as D
    tz = D.timezone(D.timedelta(hours=-5))
    start, end = D.datetime(2026, 10, 21, 0, 0, tzinfo=tz), D.datetime(2026, 12, 20, 0, 0, tzinfo=tz)
    rules = {"schedule": LIVE_RULES}
    types = {k: v for k, v in AS_DAY_TYPES.items()}
    a = M.occurrences(rules, start, end, _week_sets(), _sun)
    b = M.occurrences(types, start, end, _week_sets(), _sun)
    assert [(t.isoformat(), m) for t, m in a] == [(t.isoformat(), m) for t, m in b]
    assert len(a) > 200
    assert M.rule_day_sets(types) == {"vacation", "workday"}


def test_vacation_outranks_workdays():
    import datetime as D
    tz = D.timezone(D.timedelta(hours=-5))
    vac = [D.date(2026, 10, 26), D.date(2026, 10, 27)]     # Mon, Tue
    got = M.occurrences(AS_DAY_TYPES, D.datetime(2026, 10, 25, 12, 0, tzinfo=tz), D.datetime(2026, 10, 28, 12, 0, tzinfo=tz),
                        _week_sets(vac), _sun)
    seq = [(t.strftime("%a %H:%M"), m) for t, m in got]
    # Sunday night: Monday is vacation, not a workday, so no 23:00 Overnight; vacation times on Mon/Tue;
    # Tuesday night: Wednesday is a workday again → 23:00 Overnight
    assert ("Sun 23:00", "overnight") not in seq, seq
    assert ("Mon 01:00", "overnight") in seq and ("Mon 09:30", "morning") in seq and ("Mon 06:00", "morning") not in seq, seq
    assert ("Tue 23:00", "overnight") in seq and ("Wed 06:00", "morning") in seq, seq
    assert M.day_type_of(AS_DAY_TYPES, D.date(2026, 10, 26), _week_sets(vac)) == "vacation"
    assert M.day_type_of(AS_DAY_TYPES, D.date(2026, 10, 25), _week_sets(vac)) == "day_off"
    assert M.day_type_of(AS_DAY_TYPES, D.date(2026, 10, 25), lambda ds, d: None) is None    # calendars not read yet


def test_day_types_validate():
    cfg = copy.deepcopy(GOOD)
    cfg["modes"] += [{"id": m, "name": m.title(), "default": {"behaviour": "hands_off"}} for m in ("morning", "evening")]
    cfg.update(copy.deepcopy(AS_DAY_TYPES))
    cfg["times"]["workday"][3]["mode"] = "overnight"
    sets = {"workday", "vacation"}
    e, _ = V.check_config(cfg, state_of, area_exists, None, sets.__contains__)
    assert e == [], e
    bad = copy.deepcopy(cfg)
    bad["day_types"][2]["day_set"] = "weekend"                  # the last must take every other day
    bad["day_types"][1].pop("day_set")                          # and only the last may
    bad["times"]["workday"][0]["at"] = "25:00"
    bad["times"]["holiday"] = []
    bad["times"]["day_off"].append({"mode": "day", "at": "08:00"})   # Morning is at 08:00 too
    e, _ = V.check_config(bad, state_of, area_exists, None, sets.__contains__)
    got = {(tuple(d["path"]), d["message"].split(": ", 1)[-1][:30]) for d in e.detailed()}
    paths = {p for p, _ in got}
    assert ("day_types", 2) in paths and ("day_types", 1) in paths, got
    assert ("times", "workday", 0) in paths and ("times", "holiday") in paths and ("times", "day_off", 4) in paths, got


def test_day_type_changes_in_words():
    a = copy.deepcopy(GOOD)
    a["modes"] += [{"id": m, "name": m.title(), "default": {"behaviour": "hands_off"}} for m in ("morning", "evening")]
    a.update(copy.deepcopy(AS_DAY_TYPES))
    b = copy.deepcopy(a)
    b["times"]["workday"][0]["at"] = "06:30"
    b["day_types"][0]["name"] = "Holidays away"
    b["times"]["vacation"].append({"mode": "day", "at": "11:00"})
    lines = CH.describe(a, b)
    assert "Workdays: Morning at 06:00 → Morning at 06:30" in lines, lines
    assert "Day type Vacation renamed Holidays away" in lines, lines
    assert "Holidays away · added: Day at 11:00" in lines, lines


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
    print(f"{n} checks passed")
