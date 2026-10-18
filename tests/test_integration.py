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
    hass.states.async_set(SINK, "off")
    hass.states.async_set(CEILING, "off")
    hass.states.async_set(OCC, "off")


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
    assert hass.states.get(ZONE).attributes["status"].startswith("vacant; dark at")
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
