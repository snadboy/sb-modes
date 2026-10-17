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
    assert [e.data for e in events] == [{"from": "day", "to": "evening"}]
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
    assert await _save(hass, _table()) == {"warnings": []}
    assert hass.states.get(SELECT).attributes["options"] == ["day", "evening", "overnight"]
    assert hass.states.get(SELECT).state == "overnight"  # restored across the reload
    zone = hass.states.get(ZONE)
    assert zone.state == "nightlight_then_dark"
    assert zone.attributes["source"] == "override"
    assert zone.attributes["params"] == {"nightlight": {SINK: {"brightness_pct": 10}}, "delay": 10}
    assert zone.attributes["lights"] == [CEILING, SINK]
    assert zone.attributes["occupancy"] == [OCC]

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
