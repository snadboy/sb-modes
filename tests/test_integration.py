"""SB Modes inside a real Home Assistant core: setup, the mode select, saving the table,
fallback, restore across a reload, the problem sensor / Repairs issue, and the
nightlight_then_dark and static behaviours driving (mocked) light services."""

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry, async_capture_events, async_fire_time_changed, async_mock_service,
)

from custom_components.sb_modes.const import DOMAIN, EVENT_MODE_CHANGED, ISSUE_ID

SELECT = "select.sb_modes_mode"
PROBLEM = "binary_sensor.sb_modes_problem"
ZONE = "sensor.sb_modes_kitchen"
OCC = "binary_sensor.kitchen_occ"
SINK = "light.sink"
CEILING = "light.ceiling"
LUX = "sensor.kitchen_lux"


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="SB Modes")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _save(hass: HomeAssistant, config: dict) -> dict:
    res = await hass.services.async_call(DOMAIN, "save_config", {"config": config}, blocking=True, return_response=True)
    await hass.async_block_till_done()
    return res


async def _mode(hass: HomeAssistant, mode: str) -> None:
    await hass.services.async_call("select", "select_option", {"entity_id": SELECT, "option": mode}, blocking=True)
    await hass.async_block_till_done()


def _table() -> dict:
    return {
        "fallback_mode": "day",
        "modes": [
            {"id": "day", "name": "Day", "default": {"behaviour": "hands_off", "params": {}}},
            {"id": "evening", "name": "Evening", "default": {"behaviour": "hands_off", "params": {}}},
            {"id": "overnight", "name": "Overnight",
             "default": {"behaviour": "nightlight_then_dark", "params": {"delay": 10}}},
        ],
        "zones": [
            {"id": "kitchen", "name": "Kitchen", "areas": ["kitchen"],
             "overrides": {"overnight": {"behaviour": "nightlight_then_dark",
                                         "params": {"nightlight": {SINK: {"brightness_pct": 10}}}}}},
        ],
    }


@pytest.fixture
def kitchen(hass: HomeAssistant):
    """An area with two lights and an occupancy sensor, registered so the zone finds them."""
    area = ar.async_get(hass).async_create("Kitchen")
    reg = er.async_get(hass)
    for domain, uid, dclass in (("light", "sink", None), ("light", "ceiling", None), ("binary_sensor", "kitchen_occ", "occupancy")):
        e = reg.async_get_or_create(domain, "test", uid, suggested_object_id=uid, original_device_class=dclass)
        reg.async_update_entity(e.entity_id, area_id=area.id)
    e = reg.async_get_or_create("sensor", "test", "kitchen_lux", suggested_object_id="kitchen_lux", original_device_class="illuminance")
    reg.async_update_entity(e.entity_id, area_id=area.id)
    hass.states.async_set(SINK, "off")
    hass.states.async_set(CEILING, "off")
    hass.states.async_set(OCC, "off")
    hass.states.async_set(LUX, "60")


@pytest.fixture
def lights(hass: HomeAssistant):
    return {"on": async_mock_service(hass, "light", "turn_on"), "off": async_mock_service(hass, "light", "turn_off")}


# ── step 1: the table, the select, problems ─────────────────────────────────

async def test_seeded_table(hass: HomeAssistant) -> None:
    await _setup(hass)
    st = hass.states.get(SELECT)
    assert st.attributes["options"] == ["morning", "day", "evening", "overnight"]
    assert st.state == "day"                             # no last state → the fallback
    assert hass.states.get(PROBLEM).state == "off"


async def test_select_changes_mode_and_fires_event(hass: HomeAssistant) -> None:
    await _setup(hass)
    events = async_capture_events(hass, EVENT_MODE_CHANGED)
    await _mode(hass, "evening")
    assert hass.states.get(SELECT).state == "evening"
    assert [e.data for e in events] == [{"from": "day", "to": "evening", "source": "manual"}]
    with pytest.raises(ServiceValidationError):
        await _mode(hass, "party")


async def test_invalid_table_is_refused(hass: HomeAssistant, kitchen) -> None:
    await _setup(hass)
    bad = _table()
    bad["zones"][0]["overrides"] = {"weekend": {"behaviour": "hands_off"}}
    with pytest.raises(ServiceValidationError, match="overrides mode “weekend”"):
        await _save(hass, bad)
    res = await hass.services.async_call(DOMAIN, "get_config", {}, blocking=True, return_response=True)
    assert [m["id"] for m in res["config"]["modes"]] == ["morning", "day", "evening", "overnight"]   # unchanged


async def test_zone_without_a_sensor_is_refused(hass: HomeAssistant, kitchen) -> None:
    await _setup(hass)
    er.async_get(hass).async_update_entity(OCC, area_id=None)
    with pytest.raises(ServiceValidationError, match="needs an occupancy sensor"):
        await _save(hass, _table())


async def test_save_makes_zone_sensors_and_keeps_the_mode(hass: HomeAssistant, kitchen, lights) -> None:
    await _setup(hass)
    await _mode(hass, "overnight")
    since = hass.states.get(SELECT).attributes["since"]
    assert await _save(hass, _table()) == {"warnings": []}
    assert hass.states.get(SELECT).attributes["options"] == ["day", "evening", "overnight"]
    assert hass.states.get(SELECT).state == "overnight"  # restored across the reload
    assert hass.states.get(SELECT).attributes["since"] == since                   # …and when it began
    assert hass.states.get(SELECT).attributes["takes_over"] == {
        "day": [], "evening": [], "overnight": [{"zone": "Kitchen", "behaviour": "nightlight_then_dark"}]}
    zone = hass.states.get(ZONE)
    assert zone.state == "nightlight_then_dark"
    assert zone.attributes["source"] == "override"
    assert zone.attributes["params"] == {"nightlight": {SINK: {"brightness_pct": 10}}, "delay": 10}
    assert zone.attributes["lights"] == [CEILING, SINK]
    assert zone.attributes["occupancy"] == [OCC]
    assert zone.attributes["by_mode"]["day"] == {"behaviour": "hands_off", "params": {}, "source": "default"}
    assert zone.attributes["by_mode"]["overnight"]["source"] == "override"

    await _mode(hass, "evening")
    zone = hass.states.get(ZONE)
    assert (zone.state, zone.attributes["source"]) == ("hands_off", "default")


async def test_deleting_the_active_mode_falls_back(hass: HomeAssistant, kitchen) -> None:
    await _setup(hass)
    await _save(hass, _table())
    await _mode(hass, "evening")
    smaller = _table()
    smaller["modes"] = [m for m in smaller["modes"] if m["id"] != "evening"]
    await _save(hass, smaller)
    assert hass.states.get(SELECT).state == "day"


async def test_lost_entity_raises_problem_then_repairs(hass: HomeAssistant, kitchen, freezer: FrozenDateTimeFactory) -> None:
    await _setup(hass)
    await _save(hass, _table())
    hass.states.async_remove(SINK)
    er.async_get(hass).async_remove(SINK)
    freezer.tick(timedelta(seconds=61))                  # periodic re-check
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    problem = hass.states.get(PROBLEM)
    assert problem.state == "on"
    assert problem.attributes["errors"] == ["Zone Kitchen, Overnight, nightlight: light.sink does not exist."]
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None          # still in its grace period

    freezer.tick(timedelta(seconds=121))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None

    hass.states.async_set(SINK, "off")
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(PROBLEM).state == "off"
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None


# ── step 2: behaviours ──────────────────────────────────────────────────────

async def test_entering_overnight_long_vacant_goes_dark_at_once(hass, kitchen, lights, freezer) -> None:
    await _setup(hass)
    await _save(hass, _table())
    hass.states.async_set(SINK, "on", {"brightness": 128})
    freezer.tick(timedelta(minutes=30))                  # the sensor has been off for 30 min
    await _mode(hass, "overnight")
    assert [c.data["entity_id"] for c in lights["off"]] == [SINK]                 # the ceiling is already off
    assert hass.states.get(ZONE).attributes["status"].startswith("dark since")
    rt = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    assert lights["off"][0].context.id in rt.own_contexts


async def test_vacancy_goes_dark_after_the_delay_once(hass, kitchen, lights, freezer) -> None:
    await _setup(hass)
    await _save(hass, _table())
    hass.states.async_set(OCC, "on")
    await _mode(hass, "overnight")
    hass.states.async_set(CEILING, "on")
    hass.states.async_set(OCC, "off")
    await hass.async_block_till_done()
    assert hass.states.get(ZONE).attributes["status"].startswith("vacant; goes dark at")
    freezer.tick(timedelta(minutes=9))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert lights["off"] == []
    freezer.tick(timedelta(minutes=1, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert [c.data["entity_id"] for c in lights["off"]] == [CEILING]
    # still vacant, someone turns a light on by hand: no second dark until someone comes and goes
    freezer.tick(timedelta(minutes=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(lights["off"]) == 1


async def test_arrival_with_lights_off_turns_on_the_nightlight(hass, kitchen, lights) -> None:
    await _setup(hass)
    await _save(hass, _table())
    await _mode(hass, "overnight")
    hass.states.async_set(OCC, "on")
    await hass.async_block_till_done()
    assert [(c.data["entity_id"], c.data.get("brightness_pct")) for c in lights["on"]] == [(SINK, 10)]
    assert hass.states.get(ZONE).attributes["status"] == "nightlight"


async def test_arrival_with_a_light_on_leaves_it(hass, kitchen, lights) -> None:
    await _setup(hass)
    await _save(hass, _table())
    await _mode(hass, "overnight")
    hass.states.async_set(CEILING, "on")
    hass.states.async_set(OCC, "on")
    await hass.async_block_till_done()
    assert lights["on"] == []
    assert hass.states.get(ZONE).attributes["status"] == "occupied"


async def test_unavailable_sensor_never_goes_dark(hass, kitchen, lights, freezer) -> None:
    await _setup(hass)
    await _save(hass, _table())
    hass.states.async_set(OCC, "unavailable")
    hass.states.async_set(SINK, "on")
    await _mode(hass, "overnight")
    freezer.tick(timedelta(minutes=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert lights["off"] == []
    assert hass.states.get(ZONE).attributes["status"] == "waiting for the sensors"


async def test_leaving_overnight_cancels_the_timer(hass, kitchen, lights, freezer) -> None:
    await _setup(hass)
    await _save(hass, _table())
    hass.states.async_set(SINK, "on")
    hass.states.async_set(OCC, "on")
    await _mode(hass, "overnight")
    hass.states.async_set(OCC, "off")
    await hass.async_block_till_done()
    await _mode(hass, "day")
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert lights["off"] == []


async def test_static_sends_only_what_differs(hass, kitchen, lights) -> None:
    await _setup(hass)
    table = _table()
    table["modes"][1]["default"] = {"behaviour": "static", "params": {"targets": {
        SINK: {"brightness_pct": 50}, CEILING: {"state": "off"}}}}
    await _save(hass, table)
    hass.states.async_set(SINK, "on", {"brightness": 128})                       # 50% already
    await _mode(hass, "evening")
    assert lights["on"] == [] and lights["off"] == []
    hass.states.async_set(SINK, "on", {"brightness": 255})
    await _mode(hass, "day")
    await _mode(hass, "evening")
    assert [(c.data["entity_id"], c.data["brightness_pct"]) for c in lights["on"]] == [(SINK, 50)]


# ── the schedule and manual holds ───────────────────────────────────────────

ALL = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _sched_table() -> dict:
    t = _table()
    t["schedule"] = [{"days": ALL, "at": "10:00", "mode": "day"},
                     {"days": ALL, "at": "10:10", "mode": "evening"},
                     {"days": ALL, "at": "10:20", "mode": "overnight"}]
    return t


async def _at(hass, freezer, h, m, s=0):
    from datetime import datetime
    from homeassistant.util import dt as dt_util
    freezer.move_to(datetime(2026, 10, 16, h, m, s, tzinfo=dt_util.get_default_time_zone()))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _sel(hass):
    return hass.states.get(SELECT)


async def test_schedule_switches_on_time(hass, kitchen, lights, freezer) -> None:
    await _at(hass, freezer, 10, 5)
    await _setup(hass)
    await _save(hass, _sched_table())
    assert _sel(hass).state == "day"                       # caught up on load (10:00 rule)
    assert _sel(hass).attributes["next_change"]["mode"] == "evening"
    events = async_capture_events(hass, EVENT_MODE_CHANGED)
    await _at(hass, freezer, 10, 10, 1)
    assert _sel(hass).state == "evening"
    assert events[-1].data["source"] == "schedule"


async def test_manual_holds_until_next_change(hass, kitchen, lights, freezer) -> None:
    await _at(hass, freezer, 10, 1)
    await _setup(hass)
    await _save(hass, _sched_table())
    await _mode(hass, "overnight")                         # plain select = hold "next"
    assert _sel(hass).attributes["manual"]["hold"] == "next"
    await _at(hass, freezer, 10, 5)
    assert _sel(hass).state == "overnight"                 # no scheduled change yet
    await _at(hass, freezer, 10, 10, 1)
    assert _sel(hass).state == "evening" and _sel(hass).attributes["manual"] is None


async def test_timed_hold_outlasts_a_change_then_returns(hass, kitchen, lights, freezer) -> None:
    await _at(hass, freezer, 10, 5)
    await _setup(hass)
    await _save(hass, _sched_table())
    await hass.services.async_call(DOMAIN, "set_mode", {"mode": "overnight", "hold": 15}, blocking=True)
    await _at(hass, freezer, 10, 10, 1)
    assert _sel(hass).state == "overnight"                 # held through the 10:10 change
    await _at(hass, freezer, 10, 20, 1)
    assert _sel(hass).state == "overnight"                 # 10:20 is overnight anyway; hold over at 10:20
    assert _sel(hass).attributes["manual"] is None


async def test_timed_hold_returns_to_the_scheduled_mode(hass, kitchen, lights, freezer) -> None:
    await _at(hass, freezer, 10, 1)
    await _setup(hass)
    await _save(hass, _sched_table())
    await hass.services.async_call(DOMAIN, "set_mode", {"mode": "overnight", "hold": 5}, blocking=True)
    await _at(hass, freezer, 10, 6, 1)
    assert _sel(hass).state == "day" and _sel(hass).attributes["manual"] is None


async def test_paused_until_resumed(hass, kitchen, lights, freezer) -> None:
    await _at(hass, freezer, 10, 5)
    await _setup(hass)
    await _save(hass, _sched_table())
    await hass.services.async_call(DOMAIN, "set_mode", {"mode": "overnight", "hold": "paused"}, blocking=True)
    await _at(hass, freezer, 10, 10, 1)
    await _at(hass, freezer, 10, 15)
    assert _sel(hass).state == "overnight" and _sel(hass).attributes["manual"]["hold"] == "paused"
    await hass.services.async_call(DOMAIN, "resume_schedule", {}, blocking=True)
    assert _sel(hass).state == "evening" and _sel(hass).attributes["manual"] is None


async def test_hold_survives_a_reload(hass, kitchen, lights, freezer) -> None:
    await _at(hass, freezer, 10, 5)
    await _setup(hass)
    await _save(hass, _sched_table())
    await hass.services.async_call(DOMAIN, "set_mode", {"mode": "overnight", "hold": "paused"}, blocking=True)
    await _save(hass, _sched_table())                      # reload: restored, not caught up
    assert _sel(hass).state == "overnight" and _sel(hass).attributes["manual"]["hold"] == "paused"


async def test_bad_schedule_is_refused(hass, kitchen) -> None:
    await _setup(hass)
    t = _table()
    t["schedule"] = [{"days": ["mon"], "at": "25:00", "mode": "weekend"}]
    with pytest.raises(ServiceValidationError, match="not defined"):
        await _save(hass, t)


# ── step 3: lights changed by hand, zone holds ──────────────────────────────

from homeassistant.core import Context  # noqa: E402


def _rt(hass):
    return hass.config_entries.async_entries(DOMAIN)[0].runtime_data


def _zone(hass):
    return hass.states.get(ZONE).attributes


async def _overnight(hass):
    await _setup(hass)
    await _save(hass, _table())
    await _mode(hass, "overnight")


async def test_a_persons_change_is_an_override(hass, kitchen, lights) -> None:
    await _overnight(hass)
    hass.states.async_set(SINK, "on", {"brightness": 200}, context=Context(user_id="someone"))
    await hass.async_block_till_done()
    assert _zone(hass)["overrides"][SINK]["by"] == "person"


async def test_a_wall_switch_is_an_override(hass, kitchen, lights) -> None:
    await _overnight(hass)
    hass.states.async_set(CEILING, "on", {"brightness": 255}, context=Context())
    await hass.async_block_till_done()
    assert _zone(hass)["overrides"][CEILING]["by"] == "device"


async def test_not_overrides_ours_automations_and_echoes(hass, kitchen, lights) -> None:
    await _overnight(hass)
    rt = _rt(hass)
    ours = Context(); rt.own_contexts.append(ours.id)
    hass.states.async_set(SINK, "on", {"brightness": 25}, context=ours)                        # our own call
    hass.states.async_set(SINK, "on", {"brightness": 120}, context=Context(parent_id="automation"))  # another automation
    rt.keeper.commanded([CEILING])
    hass.states.async_set(CEILING, "on", {"brightness": 128}, context=Context())               # the device echoing us
    await hass.async_block_till_done()
    assert _zone(hass)["overrides"] == {}


async def test_overridden_lights_are_skipped(hass, kitchen, lights) -> None:
    await _overnight(hass)
    hass.states.async_set(SINK, "on", {"brightness": 200}, context=Context(user_id="someone"))
    await hass.async_block_till_done()
    _rt(hass).apply({SINK: {"state": "off"}, CEILING: {"state": "on"}}, "test", "kitchen")
    await hass.async_block_till_done()
    assert [c.data["entity_id"] for c in lights["off"]] == [] and [c.data["entity_id"] for c in lights["on"]] == [CEILING]


async def test_override_ends_when_vacant_for_the_delay(hass, kitchen, lights, freezer) -> None:
    await _overnight(hass)
    hass.states.async_set(OCC, "on")
    hass.states.async_set(SINK, "on", {"brightness": 200}, context=Context(user_id="someone"))
    await hass.async_block_till_done()
    hass.states.async_set(OCC, "off")
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=9))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert SINK in _zone(hass)["overrides"]
    freezer.tick(timedelta(minutes=1, seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _zone(hass)["overrides"] == {}
    assert SINK in [c.data["entity_id"] for c in lights["off"]]          # and the zone went dark, sink included


async def test_override_ends_with_the_mode(hass, kitchen, lights) -> None:
    await _overnight(hass)
    hass.states.async_set(SINK, "on", {"brightness": 200}, context=Context(user_id="someone"))
    await hass.async_block_till_done()
    await _mode(hass, "evening")
    await _mode(hass, "overnight")
    assert _zone(hass)["overrides"] == {}


async def test_timed_hold_stops_the_behaviour_then_releases(hass, kitchen, lights, freezer) -> None:
    await _overnight(hass)
    await hass.services.async_call(DOMAIN, "hold_zone", {"zone": "kitchen", "hold": 30}, blocking=True)
    await hass.async_block_till_done()
    assert _zone(hass)["status"].startswith("held by hand until") and _zone(hass)["hold"]["hold"] == "timed"
    hass.states.async_set(OCC, "on")                                      # arrival in the dark: no nightlight
    await hass.async_block_till_done()
    assert lights["on"] == []
    freezer.tick(timedelta(minutes=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _zone(hass)["hold"] is None and _zone(hass)["status"] == "occupied"


async def test_hold_until_next_mode_change(hass, kitchen, lights) -> None:
    await _overnight(hass)
    await hass.services.async_call(DOMAIN, "hold_zone", {"zone": "kitchen"}, blocking=True)
    assert _zone(hass)["status"] == "held by hand until the next mode change"
    await _mode(hass, "evening")
    assert _zone(hass)["hold"] is None


async def test_paused_hold_survives_mode_changes_and_reloads_until_released(hass, kitchen, lights) -> None:
    await _overnight(hass)
    await hass.services.async_call(DOMAIN, "hold_zone", {"zone": "kitchen", "hold": "paused"}, blocking=True)
    await _mode(hass, "evening")
    await _save(hass, _table())                                           # reload
    assert _zone(hass)["hold"]["hold"] == "paused"
    await hass.services.async_call(DOMAIN, "release_zone", {"zone": "kitchen"}, blocking=True)
    await hass.async_block_till_done()
    assert _zone(hass)["hold"] is None and _zone(hass)["status"] != "held by hand until released"


async def test_hold_unknown_zone_refused(hass, kitchen) -> None:
    await _overnight(hass)
    with pytest.raises(ServiceValidationError, match="not a zone"):
        await hass.services.async_call(DOMAIN, "hold_zone", {"zone": "attic"}, blocking=True)


# ── step 4a: vacancy_off, occupied_vacant, numbers ──────────────────────────

def _with(behaviour: str, params: dict, mode: str = "*") -> dict:
    t = _table()
    t["zones"][0]["overrides"] = {mode: {"behaviour": behaviour, "params": params}}
    return t


async def _tick(hass, freezer, **kw):
    freezer.tick(timedelta(**kw))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_vacancy_off_after_the_delay(hass, kitchen, lights, freezer) -> None:
    await _setup(hass)
    hass.states.async_set(OCC, "on")
    hass.states.async_set(CEILING, "on", {"brightness": 255})
    await _save(hass, _with("vacancy_off", {"lights": [CEILING], "off_after_seconds": 180}))
    hass.states.async_set(OCC, "off")
    await hass.async_block_till_done()
    await _tick(hass, freezer, seconds=170)
    assert lights["off"] == []
    await _tick(hass, freezer, seconds=15)
    assert [c.data["entity_id"] for c in lights["off"]] == [CEILING]
    assert _zone(hass)["source"] == "zone"


async def test_vacancy_off_light_turned_on_while_vacant(hass, kitchen, lights, freezer) -> None:
    await _setup(hass)
    await _save(hass, _with("vacancy_off", {"lights": [CEILING], "off_after_seconds": 60}))
    await _tick(hass, freezer, minutes=30)                                # long vacant
    hass.states.async_set(CEILING, "on", {"brightness": 255}, context=Context(user_id="app"))
    await hass.async_block_till_done()
    await _tick(hass, freezer, seconds=50)
    assert lights["off"] == []                                            # counted from when it came on
    await _tick(hass, freezer, seconds=15)
    assert [c.data["entity_id"] for c in lights["off"]] == [CEILING]


async def test_vacancy_off_max_on(hass, kitchen, lights, freezer) -> None:
    await _setup(hass)
    await _save(hass, _with("vacancy_off", {"lights": [CEILING], "off_after_seconds": 3600, "max_on_minutes": 5}))
    hass.states.async_set(CEILING, "on", {"brightness": 255}, context=Context(user_id="app"))
    await hass.async_block_till_done()
    await _tick(hass, freezer, minutes=5, seconds=5)
    assert [c.data["entity_id"] for c in lights["off"]] == [CEILING]


async def test_vacancy_off_on_when_occupied(hass, kitchen, lights) -> None:
    await _setup(hass)
    await _save(hass, _with("vacancy_off", {"lights": [CEILING], "on_when_occupied": True}))
    hass.states.async_set(OCC, "on")
    await hass.async_block_till_done()
    assert [c.data["entity_id"] for c in lights["on"]] == [CEILING]


async def test_occupied_vacant_and_numbers(hass, kitchen, lights, freezer) -> None:
    number = async_mock_service(hass, "number", "set_value")
    er.async_get(hass).async_get_or_create("number", "test", "clock", suggested_object_id="clock")
    hass.states.async_set("number.clock", "255")
    await _setup(hass)
    await _save(hass, _with("occupied_vacant", {
        "occupied": {SINK: {"brightness_pct": 50, "hs_color": [30, 60]}, "number.clock": {"value": 255}},
        "vacant": {SINK: {"brightness_pct": 1, "effect": "Breath"}, "number.clock": {"value": 64}},
        "vacant_after_minutes": 2}))
    # entering the mode vacant (since setup) applies the vacant set once its delay has passed
    await _tick(hass, freezer, minutes=3)
    assert [c.data for c in number] == [{"entity_id": "number.clock", "value": 64}]
    assert lights["on"][-1].data == {"entity_id": SINK, "brightness_pct": 1, "effect": "Breath"}
    hass.states.async_set("number.clock", "64")
    hass.states.async_set(OCC, "on")
    await hass.async_block_till_done()
    assert number[-1].data == {"entity_id": "number.clock", "value": 255}
    assert lights["on"][-1].data == {"entity_id": SINK, "brightness_pct": 50, "hs_color": [30, 60]}


# ── step 4b: presence_lux ───────────────────────────────────────────────────

PL = {"target_lux": 25, "occupied": {CEILING: {"brightness_pct": 82}},
      "vacant": {SINK: {"brightness_pct": 40}, CEILING: {"state": "off"}}, "vacant_after_minutes": 15}


async def _evening_pl(hass):
    await _setup(hass)
    await _save(hass, _with("presence_lux", PL, mode="evening"))
    hass.states.async_set(OCC, "on")
    await _mode(hass, "evening")


async def test_presence_lux_bright_enough_does_nothing(hass, kitchen, lights) -> None:
    await _evening_pl(hass)
    assert lights["on"] == [] and _zone(hass)["status"].startswith("occupied; light enough (60")


async def test_presence_lux_turns_on_as_it_gets_dark(hass, kitchen, lights) -> None:
    await _evening_pl(hass)
    hass.states.async_set(LUX, "30")
    await hass.async_block_till_done()
    assert lights["on"] == []
    hass.states.async_set(LUX, "22")
    await hass.async_block_till_done()
    assert [(c.data["entity_id"], c.data.get("brightness_pct")) for c in lights["on"]] == [(CEILING, 82)]
    hass.states.async_set(LUX, "40")                                     # our own lamps raise it: no dimming
    hass.states.async_set(LUX, "20")
    await hass.async_block_till_done()
    assert len(lights["on"]) == 1 and _zone(hass)["status"] == "on"


async def test_presence_lux_arrival_in_the_dark(hass, kitchen, lights) -> None:
    hass.states.async_set(LUX, "10")
    await _setup(hass)
    await _save(hass, _with("presence_lux", PL, mode="evening"))
    await _mode(hass, "evening")
    assert lights["on"] == [] or lights["on"][0].data["entity_id"] == SINK   # entering vacant: the empty setting
    hass.states.async_set(OCC, "on")
    await hass.async_block_till_done()
    assert (CEILING, 82) in [(c.data["entity_id"], c.data.get("brightness_pct")) for c in lights["on"]]


async def test_presence_lux_empty_goes_to_the_nightlight(hass, kitchen, lights, freezer) -> None:
    await _evening_pl(hass)
    hass.states.async_set(LUX, "10")
    await hass.async_block_till_done()
    hass.states.async_set(CEILING, "on", {"brightness": 209})
    hass.states.async_set(OCC, "off")
    await hass.async_block_till_done()
    await _tick(hass, freezer, minutes=14)
    assert lights["off"] == []
    await _tick(hass, freezer, minutes=1, seconds=2)
    assert [c.data["entity_id"] for c in lights["off"]] == [CEILING]
    assert (SINK, 40) in [(c.data["entity_id"], c.data.get("brightness_pct")) for c in lights["on"]]
    hass.states.async_set(CEILING, "off")
    hass.states.async_set(OCC, "on")                                      # back in the dark: on again
    await hass.async_block_till_done()
    assert lights["on"][-1].data == {"entity_id": CEILING, "brightness_pct": 82}


# ── wake sequences, sun-relative rules, zone light lists ────────────────────

WAKE = {"after_mode": "overnight", "steps": [{"after_minutes": 30, "targets": {SINK: {"brightness_pct": 1}}},
                                             {"after_minutes": 40, "targets": {SINK: {"brightness_pct": 4}}}]}


async def test_wake_sequence_after_overnight_only(hass, kitchen, lights, freezer) -> None:
    await _setup(hass)
    t = _table()
    t["zones"][0]["overrides"] = {"*": {"behaviour": "wake_sequence", "params": WAKE}}
    await _save(hass, t)
    await _mode(hass, "overnight")
    await _mode(hass, "day")                                              # came from Overnight
    assert _zone(hass)["status"].startswith("next step at")
    await _tick(hass, freezer, minutes=30, seconds=1)
    assert [c.data.get("brightness_pct") for c in lights["on"]] == [1]
    hass.states.async_set(SINK, "on", {"brightness": 3})
    await _tick(hass, freezer, minutes=10)
    assert [c.data.get("brightness_pct") for c in lights["on"]] == [1, 4]
    assert _zone(hass)["status"] == "done"
    await _mode(hass, "evening")                                          # from Day: not a wake-up
    assert _zone(hass)["status"] == "only after Overnight"


async def test_sunset_relative_rule(hass, kitchen) -> None:
    from homeassistant.helpers.sun import get_astral_event_date
    from homeassistant.util import dt as dt_util
    await _setup(hass)
    t = _table()
    t["schedule"] = [{"days": ALL, "at": "sunset-01:00", "mode": "evening"}]
    await _save(hass, t)
    today = dt_util.now().date()
    expect = [get_astral_event_date(hass, "sunset", today + timedelta(days=n)) - timedelta(hours=1) for n in range(3)]
    expect = next(e for e in expect if e > dt_util.utcnow())
    got = dt_util.parse_datetime(_sel(hass).attributes["next_change"]["at"])
    assert abs((got - expect).total_seconds()) < 1, (got, expect)


async def test_zone_lights_list_replaces_the_areas_lights(hass, kitchen) -> None:
    await _setup(hass)
    t = _table()
    t["zones"][0]["lights"] = [CEILING]
    await _save(hass, t)
    await _mode(hass, "overnight")
    assert _zone(hass)["lights"] == [CEILING]


# ── reporting: schedule state, deviations, zone control ─────────────────────

SCHED = "sensor.sb_modes_schedule"
DEV = "sensor.sb_modes_deviations"


async def test_reporting_entities(hass, kitchen, lights, freezer) -> None:
    await _at(hass, freezer, 10, 5)
    await _setup(hass)
    assert hass.states.get(SCHED).state == "no_schedule"
    await _save(hass, _sched_table())
    assert hass.states.get(SCHED).state == "on_schedule"
    assert hass.states.get(SCHED).attributes["next_mode"] == "evening"
    assert hass.states.get(DEV).state == "0" and _zone(hass)["control"] == "auto"

    await hass.services.async_call(DOMAIN, "set_mode", {"mode": "overnight", "hold": 30}, blocking=True)
    await hass.services.async_call(DOMAIN, "hold_zone", {"zone": "kitchen", "hold": "paused"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(SCHED).state == "held_timed"
    dev = hass.states.get(DEV)
    assert dev.state == "2" and dev.attributes["mode_hold"] == 1 and dev.attributes["zone_hold"] == 1
    assert {d["type"] for d in dev.attributes["deviations"]} == {"mode_hold", "zone_hold"}
    assert _zone(hass)["control"] == "held"

    await hass.services.async_call(DOMAIN, "release_zone", {"zone": "kitchen"}, blocking=True)
    await hass.async_block_till_done()
    hass.states.async_set(SINK, "on", {"brightness": 200}, context=Context(user_id="someone"))
    await hass.async_block_till_done()
    dev = hass.states.get(DEV)
    assert dev.state == "2" and dev.attributes["changed_by_hand"] == 1
    hand = next(d for d in dev.attributes["deviations"] if d["type"] == "changed_by_hand")
    assert hand["entity"] == SINK and hand["by"] == "person" and hand["zone_name"] == "Kitchen"
    assert _zone(hass)["control"] == "by_hand"

    await hass.services.async_call(DOMAIN, "set_mode", {"mode": "day", "hold": "paused"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(SCHED).state == "paused"
    assert hass.states.get(DEV).state == "1"                              # the mode change ended the by-hand mark


# ── regression 2026-10-18 18:43: the empty setting re-applied over a hand change ─────────

async def test_empty_setting_once_and_hand_changes_while_empty_respected(hass, kitchen, lights, freezer) -> None:
    await _setup(hass)
    await _save(hass, _with("presence_lux", PL, mode="evening"))
    hass.states.async_set(CEILING, "on", {"brightness": 209})
    await _tick(hass, freezer, minutes=30)                                # long empty
    await _mode(hass, "evening")
    assert [c.data["entity_id"] for c in lights["off"]] == [CEILING]      # empty setting, once
    hass.states.async_set(CEILING, "off")
    await hass.async_block_till_done()
    await _tick(hass, freezer, minutes=20)                                # well after our own command
    # someone switches it on at the wall while the room still reads empty; the reading rises
    hass.states.async_set(CEILING, "on", {"brightness": 209}, context=Context())
    hass.states.async_set(LUX, "80")
    await hass.async_block_till_done()
    await _tick(hass, freezer, seconds=10)
    assert len(lights["off"]) == 1                                        # NOT switched off again
    assert CEILING in _zone(hass)["overrides"]
    await _tick(hass, freezer, minutes=14)
    assert len(lights["off"]) == 1                                        # still respected
    await _tick(hass, freezer, minutes=1, seconds=5)                      # 15 min after the hand change
    assert _zone(hass)["overrides"] == {}
    assert [c.data["entity_id"] for c in lights["off"]] == [CEILING, CEILING]   # back to the empty setting


async def test_occupied_vacant_vacant_set_applied_once(hass, kitchen, lights, freezer) -> None:
    await _setup(hass)
    await _save(hass, _with("occupied_vacant", {"occupied": {SINK: {"brightness_pct": 50}},
                                                "vacant": {SINK: {"state": "off"}}, "vacant_after_minutes": 1}))
    hass.states.async_set(SINK, "on", {"brightness": 128})
    await _tick(hass, freezer, minutes=2)
    assert len(lights["off"]) == 1
    hass.states.async_set(SINK, "off")
    await _tick(hass, freezer, minutes=1)
    hass.states.async_set(OCC, "unavailable")                             # a sensor blip while empty
    hass.states.async_set(OCC, "off")
    hass.states.async_set(SINK, "on", {"brightness": 128}, context=Context())
    await hass.async_block_till_done()
    await _tick(hass, freezer, seconds=5)
    assert len(lights["off"]) == 1                                        # not re-applied over the hand change


async def test_zone_occupancy_and_since(hass, kitchen, lights, freezer) -> None:
    await _setup(hass)
    await _save(hass, _table())                                           # day: hands_off — still reported
    a = _zone(hass)
    assert a["occupancy_state"] == "vacant" and a["occupancy"] == [OCC] and a["occupancy_since"] == hass.states.get(OCC).last_changed.isoformat()
    await _tick(hass, freezer, minutes=5)
    hass.states.async_set(OCC, "on")
    await hass.async_block_till_done()
    a = _zone(hass)
    assert a["occupancy_state"] == "occupied" and a["occupancy_since"] == hass.states.get(OCC).last_changed.isoformat()
    hass.states.async_set(OCC, "unavailable")
    await hass.async_block_till_done()
    assert _zone(hass)["occupancy_state"] == "unknown"


# ── on_enter: targets set once when a mode begins ───────────────────────────

async def test_on_enter_once_per_mode_change(hass, kitchen, lights) -> None:
    await _setup(hass)
    t = _table()
    t["zones"][0]["on_enter"] = {"evening": {SINK: {"brightness_pct": 70}, CEILING: {"state": "on"}}}
    await _save(hass, t)
    await _mode(hass, "evening")
    assert sorted((c.data["entity_id"], c.data.get("brightness_pct")) for c in lights["on"]) == [(CEILING, None), (SINK, 70)]
    await _save(hass, t)                                                  # a reload is not a mode change
    assert len(lights["on"]) == 2
    await _mode(hass, "day")
    hass.states.async_set(CEILING, "off", context=Context(user_id="someone"))   # off by hand… then the mode changes
    await hass.async_block_till_done()
    assert len(lights["on"]) == 2


async def test_on_enter_skips_held_zones(hass, kitchen, lights) -> None:
    await _setup(hass)
    t = _table()
    t["zones"][0]["on_enter"] = {"evening": {SINK: {"brightness_pct": 70}}}
    await _save(hass, t)
    await hass.services.async_call(DOMAIN, "hold_zone", {"zone": "kitchen", "hold": "paused"}, blocking=True)
    await _mode(hass, "evening")
    assert lights["on"] == []


async def test_on_enter_validation(hass, kitchen) -> None:
    await _setup(hass)
    t = _table()
    t["zones"][0]["on_enter"] = {"brunch": {SINK: {"state": "on"}}}
    with pytest.raises(ServiceValidationError, match="on_enter names mode “brunch”"):
        await _save(hass, t)


# ── follow: the aux lights track the ceiling, in every mode ─────────────────

FOLLOW = {"leader": CEILING, "bands": [{"min_pct": 79, "targets": {SINK: {"brightness_pct": 100}}},
                                      {"min_pct": 21, "targets": {SINK: {"brightness_pct": 70}}},
                                      {"min_pct": 0, "targets": {SINK: {"brightness_pct": 10}}}]}


async def _follow_setup(hass):
    await _setup(hass)
    t = _table()
    t["zones"][0]["follow"] = FOLLOW
    await _save(hass, t)
    return t


async def test_follow_bands_by_hand_in_any_mode(hass, kitchen, lights) -> None:
    await _follow_setup(hass)
    for mode in ("day", "overnight"):
        await _mode(hass, mode)
        lights["on"].clear()
        hass.states.async_set(CEILING, "on", {"brightness": 255}, context=Context(user_id="u"))
        await hass.async_block_till_done()
        hass.states.async_set(CEILING, "on", {"brightness": 128}, context=Context(user_id="u"))
        await hass.async_block_till_done()
        hass.states.async_set(CEILING, "off", context=Context(user_id="u"))
        await hass.async_block_till_done()
        assert [c.data.get("brightness_pct") for c in lights["on"] if c.data["entity_id"] == SINK] == [100, 70, 10], mode


async def test_follow_stands_aside_when_we_set_the_followers(hass, kitchen, lights) -> None:
    await _follow_setup(hass)
    hass.states.async_set(CEILING, "on", {"brightness": 255})
    await hass.async_block_till_done()
    lights["on"].clear()
    rt = _rt(hass)
    rt.apply({CEILING: {"state": "off"}, SINK: {"state": "off"}}, "dark", "kitchen")   # Overnight's dark
    await hass.async_block_till_done()
    ctx = lights["off"][-1].context
    hass.states.async_set(CEILING, "off", context=ctx)                    # the leader goes off under OUR context
    await hass.async_block_till_done()
    assert lights["on"] == []                                             # no dim aux undoing the dark


async def test_follow_after_our_leader_only_command(hass, kitchen, lights) -> None:
    await _follow_setup(hass)
    rt = _rt(hass)
    rt.apply({CEILING: {"brightness_pct": 100}}, "evening on", "kitchen")
    await hass.async_block_till_done()
    ctx = lights["on"][-1].context
    hass.states.async_set(CEILING, "on", {"brightness": 255}, context=ctx)
    await hass.async_block_till_done()
    assert lights["on"][-1].data == {"entity_id": SINK, "brightness_pct": 100}


async def test_follow_ignores_unavailable_and_held(hass, kitchen, lights) -> None:
    await _follow_setup(hass)
    hass.states.async_set(CEILING, "unavailable")
    await hass.async_block_till_done()
    hass.states.async_set(CEILING, "off")
    await hass.async_block_till_done()
    assert lights["on"] == []
    await hass.services.async_call(DOMAIN, "hold_zone", {"zone": "kitchen", "hold": "paused"}, blocking=True)
    hass.states.async_set(CEILING, "on", {"brightness": 255}, context=Context(user_id="u"))
    await hass.async_block_till_done()
    assert lights["on"] == []


async def test_follow_validation(hass, kitchen) -> None:
    await _setup(hass)
    t = _table()
    t["zones"][0]["follow"] = {"leader": CEILING, "bands": [{"min_pct": 0, "targets": {CEILING: {"state": "on"}}}]}
    with pytest.raises(ServiceValidationError, match="cannot follow itself"):
        await _save(hass, t)


# ── presence_lux all day: empty when it is light vs dark, judged on daylight ────

PL2 = {"target_lux": 50, "occupied": {CEILING: {"brightness_pct": 95}},
       "vacant": {SINK: {"brightness_pct": 40}, CEILING: {"state": "off"}},
       "vacant_bright": {SINK: {"state": "off"}, CEILING: {"state": "off"}}, "vacant_after_minutes": 15}


async def test_empty_when_light_uses_the_bright_setting(hass, kitchen, lights, freezer) -> None:
    hass.states.async_set(LUX, "120")
    await _setup(hass)
    await _save(hass, _with("presence_lux", PL2))                         # all modes
    hass.states.async_set(CEILING, "on", {"brightness": 242}, context=Context(user_id="u"))
    hass.states.async_set(SINK, "on", {"brightness": 100}, context=Context(user_id="u"))
    await _tick(hass, freezer, minutes=16)
    assert sorted(c.data["entity_id"] for c in lights["off"]) == [CEILING, SINK]
    assert lights["on"] == []                                             # no nightlight in daylight
    assert "'empty, light'" in _zone(hass)["status"]


async def test_empty_judged_on_daylight_not_our_own_light(hass, kitchen, lights, freezer) -> None:
    hass.states.async_set(LUX, "8")                                       # dusk, lights off
    await _setup(hass)
    await _save(hass, _with("presence_lux", PL2))
    hass.states.async_set(OCC, "on")
    await hass.async_block_till_done()
    assert lights["on"][-1].data == {"entity_id": CEILING, "brightness_pct": 95}
    hass.states.async_set(CEILING, "on", {"brightness": 242})
    hass.states.async_set(LUX, "150")                                     # the sensor now sees the ceiling
    hass.states.async_set(OCC, "off")
    await hass.async_block_till_done()
    await _tick(hass, freezer, minutes=15, seconds=5)
    assert (SINK, 40) in [(c.data["entity_id"], c.data.get("brightness_pct")) for c in lights["on"]]   # the dark setting
    assert "'empty'" in _zone(hass)["status"] and "light" not in _zone(hass)["status"]


async def test_bright_and_occupied_does_nothing_then_lights_when_dark(hass, kitchen, lights) -> None:
    hass.states.async_set(LUX, "120")
    await _setup(hass)
    await _save(hass, _with("presence_lux", PL2))
    hass.states.async_set(OCC, "on")
    await hass.async_block_till_done()
    assert lights["on"] == []
    hass.states.async_set(LUX, "45")
    await hass.async_block_till_done()
    assert lights["on"][-1].data == {"entity_id": CEILING, "brightness_pct": 95}


async def test_dark_even_with_our_lights_on_and_no_memory(hass, kitchen, lights, freezer) -> None:
    hass.states.async_set("sun.sun", "above_horizon")
    hass.states.async_set(CEILING, "on", {"brightness": 66})
    hass.states.async_set(LUX, "27")                                      # lamps on, still below 50
    await _setup(hass)
    await _save(hass, _with("presence_lux", PL2))
    await _tick(hass, freezer, minutes=15, seconds=5)
    assert (SINK, 40) in [(c.data["entity_id"], c.data.get("brightness_pct")) for c in lights["on"]]


# ── regression 2026-10-19 16:29: the kitchen sensor saw its own dim aux lights ──────

async def test_arrival_judged_on_daylight_when_aux_lights_are_on(hass, kitchen, lights, freezer) -> None:
    await _setup(hass)
    await _save(hass, _with("presence_lux", PL2))
    hass.states.async_set(LUX, "29")                                      # all off: real daylight
    await hass.async_block_till_done()
    hass.states.async_set(SINK, "on", {"brightness": 25})                 # aux on dim (follow)
    hass.states.async_set(LUX, "56")                                      # the sensor sees it
    await hass.async_block_till_done()
    hass.states.async_set(OCC, "on")
    await hass.async_block_till_done()
    assert (CEILING, 95) in [(c.data["entity_id"], c.data.get("brightness_pct")) for c in lights["on"]]
    assert "with the lights off" in _zone(hass)["status"]


async def test_stale_daylight_memory_falls_back_to_the_sun(hass, kitchen, lights, freezer) -> None:
    hass.states.async_set("sun.sun", "above_horizon")
    await _setup(hass)
    await _save(hass, _with("presence_lux", PL2))
    hass.states.async_set(LUX, "20")                                      # dark, lights off (e.g. before dawn)
    await hass.async_block_till_done()
    hass.states.async_set(SINK, "on", {"brightness": 178})
    hass.states.async_set(LUX, "140")
    await _tick(hass, freezer, hours=2)                                   # the memory is now stale
    hass.states.async_set(OCC, "on")
    await hass.async_block_till_done()
    assert CEILING not in [c.data["entity_id"] for c in lights["on"]]    # sun up + high reading: light


async def test_daylight_memory_survives_a_reload(hass, kitchen, lights) -> None:
    await _setup(hass)
    await _save(hass, _with("presence_lux", PL2))
    hass.states.async_set(LUX, "29")
    await hass.async_block_till_done()
    assert _rt(hass).daylight["kitchen"][0] == 29.0
    await _save(hass, _with("presence_lux", PL2))                         # reload
    assert _rt(hass).daylight["kitchen"][0] == 29.0


# ── overlays: the TV sets the lights while playing / paused ──────────────────

TV = "media_player.tv"
OVERLAY = {"entity": TV, "debounce_seconds": 5, "unavailable_cooldown_seconds": 60,
           "states": {"playing": {"targets": {SINK: {"brightness_pct": 5, "transition": 3}}, "holds": True},
                      "paused": {"targets": {SINK: {"brightness_pct": 20, "transition": 3}}, "holds": True},
                      "off": {"targets": {SINK: {"brightness_pct": 30, "transition": 3}}}}}


async def _tv(hass, freezer, state, wait=6):
    hass.states.async_set(TV, state)
    await hass.async_block_till_done()
    if wait:
        await _tick(hass, freezer, seconds=wait)


async def _overlay_setup(hass, behaviour="vacancy_off", params=None):
    hass.states.async_set(TV, "off")
    await _setup(hass)
    t = _with(behaviour, params if params is not None else {"lights": [SINK], "off_after_seconds": 60})
    t["zones"][0]["overlay"] = OVERLAY
    await _save(hass, t)


def _sink_calls(lights):
    return [(s, c.data.get("brightness_pct"), c.data.get("transition")) for s in ("on", "off")
            for c in lights[s] if c.data["entity_id"] == SINK]


async def test_overlay_debounce_holds_and_hands_back(hass, kitchen, lights, freezer) -> None:
    await _overlay_setup(hass)
    await _tv(hass, freezer, "playing", wait=3)
    assert lights["on"] == []                                 # not 5 s yet
    await _tick(hass, freezer, seconds=3)
    assert _sink_calls(lights) == [("on", 5, 3)]
    assert hass.states.get(ZONE).attributes["status"].startswith("overlay:")
    # the zone's own vacancy_off is stopped while the TV plays: no off after 60 s vacant
    hass.states.async_set(SINK, "on", {"brightness": 13})
    await _tick(hass, freezer, seconds=120)
    assert lights["off"] == []
    await _tv(hass, freezer, "paused")
    assert _sink_calls(lights)[-1] == ("on", 20, 3)
    hass.states.async_set(OCC, "on")                          # someone gets up and turns it off
    await _tv(hass, freezer, "off")
    assert _sink_calls(lights)[-1] == ("on", 30, 3)
    assert lights["off"] == []
    assert not hass.states.get(ZONE).attributes["status"].startswith("overlay:")   # vacancy_off again
    hass.states.async_set(OCC, "off")
    await _tick(hass, freezer, seconds=65)
    assert _sink_calls(lights)[-1] == ("off", None, None)


async def test_overlay_flicker_and_unavailable(hass, kitchen, lights, freezer) -> None:
    await _overlay_setup(hass)
    await _tv(hass, freezer, "playing", wait=2)
    await _tv(hass, freezer, "paused", wait=2)                # neither lasted 5 s
    hass.states.async_set(TV, "playing")
    await _tick(hass, freezer, seconds=3)
    hass.states.async_set(TV, "playing", {"media_position": 3})   # attribute updates don't restart the wait
    await _tick(hass, freezer, seconds=3)
    assert _sink_calls(lights) == [("on", 5, 3)]
    lights["on"].clear()
    await _tv(hass, freezer, "unavailable", wait=0)
    await _tv(hass, freezer, "off", wait=10)                  # back from unavailable, inside the cool-down
    assert lights["on"] == []
    await _tick(hass, freezer, seconds=60)
    await _tv(hass, freezer, "paused")                        # after the cool-down: counts again
    assert _sink_calls(lights) == [("on", 20, 3)]


async def test_overlay_yields_to_hold_and_hand_changes(hass, kitchen, lights, freezer) -> None:
    await _overlay_setup(hass)
    await _tv(hass, freezer, "playing")
    lights["on"].clear()
    # folding laundry: the lamp turned up by hand stays up when the TV pauses
    hass.states.async_set(SINK, "on", {"brightness": 255}, context=Context(user_id="u"))
    await hass.async_block_till_done()
    await _tv(hass, freezer, "paused")
    assert lights["on"] == []
    # a hold beats the overlay; releasing it goes back to the TV's setting
    await hass.services.async_call(DOMAIN, "hold_zone", {"zone": "kitchen", "hold": "paused"}, blocking=True)
    await hass.async_block_till_done()
    await _tv(hass, freezer, "playing")
    assert lights["on"] == []
    await hass.services.async_call(DOMAIN, "release_zone", {"zone": "kitchen"}, blocking=True)
    await hass.async_block_till_done()
    assert _sink_calls(lights) == [("on", 5, 3)]


async def test_overlay_validation(hass, kitchen) -> None:
    hass.states.async_set(TV, "off")
    await _setup(hass)
    t = _table()
    t["zones"][0]["overlay"] = {"entity": "media_player.nope", "states": {"playing": {"targets": {SINK: {"brightness_pct": 5}}}}}
    with pytest.raises(ServiceValidationError, match="does not exist"):
        await _save(hass, t)
    t["zones"][0]["overlay"] = {"entity": TV, "states": {"playing": {"holds": "yes"}}}
    with pytest.raises(ServiceValidationError, match="holds must be"):
        await _save(hass, t)


# ── buttons: a press holds or releases the zone ─────────────────────────────

BIG, SMALL = "event.hold_button_1", "event.hold_button_2"
BUTTONS = {BIG: {"multi_press_1": {"hold": 60}, "multi_press_2": {"hold": "paused"}},
           SMALL: {"multi_press_1": "release"}}


_presses = iter(range(10**6))


async def _press(hass, freezer, ent, etype, wait=3):
    # an event entity's state is the press time; frozen time needs a unique one per press
    hass.states.async_set(ent, f"2026-10-19T20:00:00.{next(_presses):06d}+00:00", {"event_type": etype})
    await hass.async_block_till_done()
    if wait:
        await _tick(hass, freezer, seconds=wait)


async def _buttons_setup(hass, overlay=False):
    for e in (BIG, SMALL):
        hass.states.async_set(e, "2026-10-01T00:00:00+00:00", {"event_type": "multi_press_1"})
    hass.states.async_set(TV, "off")
    await _setup(hass)
    t = _with("vacancy_off", {"lights": [SINK], "off_after_seconds": 60})
    t["zones"][0]["buttons"] = BUTTONS
    if overlay:
        t["zones"][0]["overlay"] = OVERLAY
    await _save(hass, t)


async def test_buttons_hold_and_release(hass, kitchen, lights, freezer) -> None:
    await _buttons_setup(hass)
    await _press(hass, freezer, BIG, "multi_press_1", wait=1)
    assert _rt(hass).keeper.held("kitchen") is None          # waits out the settle
    await _tick(hass, freezer, seconds=2)
    assert _rt(hass).keeper.held("kitchen")["hold"] == "timed"
    await _press(hass, freezer, BIG, "multi_press_2")
    assert _rt(hass).keeper.held("kitchen")["hold"] == "paused"
    await _press(hass, freezer, BIG, "long_release")          # not mapped: nothing
    assert _rt(hass).keeper.held("kitchen")["hold"] == "paused"
    await _press(hass, freezer, SMALL, "multi_press_1")
    assert _rt(hass).keeper.held("kitchen") is None


async def test_buttons_ignore_restores_and_collapse_bursts(hass, kitchen, lights, freezer) -> None:
    await _buttons_setup(hass)
    hass.states.async_set(BIG, "unavailable")
    await hass.async_block_till_done()
    await _press(hass, freezer, BIG, "multi_press_1")          # from unavailable: a restore, not a press
    assert _rt(hass).keeper.held("kitchen") is None
    # a replay burst: hold, hold, release within the settle → only the release counts
    await _press(hass, freezer, BIG, "multi_press_1", wait=0)
    await _press(hass, freezer, BIG, "multi_press_2", wait=0)
    await _press(hass, freezer, SMALL, "multi_press_1", wait=3)
    assert _rt(hass).keeper.held("kitchen") is None


async def test_button_hold_beats_the_tv_and_release_returns_to_it(hass, kitchen, lights, freezer) -> None:
    await _buttons_setup(hass, overlay=True)
    await _press(hass, freezer, BIG, "multi_press_1")
    await _tv(hass, freezer, "playing")
    assert _sink_calls(lights) == []                          # held: the TV doesn't dim it
    await _press(hass, freezer, SMALL, "multi_press_1")
    assert _sink_calls(lights) == [("on", 5, 3)]


async def test_buttons_validation(hass, kitchen) -> None:
    hass.states.async_set(BIG, "x")
    await _setup(hass)
    t = _table()
    t["zones"][0]["buttons"] = {"sensor.x": {"multi_press_1": "release"}}
    with pytest.raises(ServiceValidationError, match="not an event entity"):
        await _save(hass, t)
    t["zones"][0]["buttons"] = {BIG: {"multi_press_1": {"hold": "forever"}}}
    with pytest.raises(ServiceValidationError, match="action must be"):
        await _save(hass, t)


async def test_button_flash_hold_targets_and_release_restores(hass, kitchen, lights, freezer, monkeypatch) -> None:
    from custom_components.sb_modes import buttons as btn
    monkeypatch.setattr(btn, "FLASH_SECONDS", 0)
    for e in (BIG, SMALL):
        hass.states.async_set(e, "2026-10-01T00:00:00+00:00", {"event_type": "multi_press_1"})
    hass.states.async_set(TV, "off")
    hass.states.async_set(SINK, "on", {"brightness": 200})
    await _setup(hass)
    t = _with("vacancy_off", {"lights": [SINK], "off_after_seconds": 60})
    t["zones"][0]["overlay"] = OVERLAY
    t["zones"][0]["buttons"] = {BIG: {"multi_press_1": {"hold": 60, "flash": 1, "targets": {SINK: {"brightness_pct": 30}, CEILING: {"brightness_pct": 30}}}},
                                SMALL: {"multi_press_1": {"release": True, "flash": 2}}}
    await _save(hass, t)
    hass.states.async_set(OCC, "on")
    await hass.async_block_till_done()
    lights["on"].clear(); lights["off"].clear()
    await _press(hass, freezer, BIG, "multi_press_1")
    seq = lambda: [(c.service, c.data["entity_id"]) for c in sorted(lights["on"] + lights["off"], key=lambda c: c.context.id)]
    # one flash: the lit sink goes off and the dark ceiling comes on, then both back; then 30 %
    assert [(c.data["entity_id"], c.data.get("brightness")) for c in lights["off"]] == [(SINK, None), (CEILING, None)]
    assert [(c.data["entity_id"], c.data.get("brightness"), c.data.get("brightness_pct")) for c in lights["on"]] == [
        (CEILING, 128, None), (SINK, 200, None), (SINK, None, 30), (CEILING, None, 30)]
    assert _rt(hass).keeper.held("kitchen")["hold"] == "timed"
    lights["on"].clear(); lights["off"].clear()
    await _press(hass, freezer, SMALL, "multi_press_1")
    assert len(lights["off"]) == 4                            # two flashes × two lights
    assert _rt(hass).keeper.held("kitchen") is None
    assert [(c.data.get("brightness_pct"), c.data.get("transition")) for c in lights["on"]
            if c.data["entity_id"] == SINK][-1] == (30, 3)    # the TV off levels back
    # flashes are ours, not hand changes
    assert _rt(hass).keeper.overridden("kitchen") == {}


async def test_reload_after_boot_logs_no_listener_error(hass, kitchen, caplog) -> None:
    """Loaded before HA started, then reloaded: the fired 'started' listener isn't removed twice."""
    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
    from homeassistant.core import CoreState
    hass.set_state(CoreState.not_running)
    entry = await _setup(hass)
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert "Unable to remove unknown job listener" not in caplog.text


async def test_on_enter_at_least(hass, kitchen, lights) -> None:
    """min_brightness_pct: an off (or dimmer) light comes on at the floor; a brighter one is left alone."""
    await _setup(hass)
    t = _table()
    t["zones"][0]["on_enter"] = {"evening": {SINK: {"min_brightness_pct": 1}, CEILING: {"min_brightness_pct": 1}}}
    await _save(hass, t)
    hass.states.async_set(SINK, "off")
    hass.states.async_set(CEILING, "on", {"brightness": 10})          # 4 %: already above 1 %
    await hass.async_block_till_done()
    await _mode(hass, "evening")
    assert [c.data for c in lights["on"]] == [{"entity_id": SINK, "brightness_pct": 1}]
    bad = _table()
    bad["zones"][0]["on_enter"] = {"evening": {SINK: {"min_brightness_pct": 1, "brightness_pct": 5}}}
    with pytest.raises(ServiceValidationError, match="min_brightness_pct goes alone"):
        await _save(hass, bad)
