"""SB Modes inside a real Home Assistant core: setup, the mode select, saving the table,
fallback, restore across a reload, and the problem sensor / Repairs issue."""

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events, async_fire_time_changed

from custom_components.sb_modes.const import DOMAIN, EVENT_MODE_CHANGED, ISSUE_ID

SELECT = "select.sb_modes_mode"
PROBLEM = "binary_sensor.sb_modes_problem"


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


def _table(**zones_extra) -> dict:
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
                                         "params": {"nightlight": "light.sink", "level": 10}}}},
        ],
        **zones_extra,
    }


@pytest.fixture
def kitchen(hass: HomeAssistant):
    ar.async_get(hass).async_create("Kitchen")          # area id "kitchen"
    hass.states.async_set("light.sink", "off")


async def test_seeded_table(hass: HomeAssistant) -> None:
    await _setup(hass)
    st = hass.states.get(SELECT)
    assert st.attributes["options"] == ["morning", "day", "evening", "overnight"]
    assert st.state == "day"                             # no last state → the fallback
    assert hass.states.get(PROBLEM).state == "off"


async def test_select_changes_mode_and_fires_event(hass: HomeAssistant) -> None:
    await _setup(hass)
    events = async_capture_events(hass, EVENT_MODE_CHANGED)
    await hass.services.async_call("select", "select_option", {"entity_id": SELECT, "option": "evening"}, blocking=True)
    assert hass.states.get(SELECT).state == "evening"
    assert [e.data for e in events] == [{"from": "day", "to": "evening"}]
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call("select", "select_option", {"entity_id": SELECT, "option": "party"}, blocking=True)


async def test_invalid_table_is_refused(hass: HomeAssistant, kitchen) -> None:
    await _setup(hass)
    bad = _table()
    bad["zones"][0]["overrides"] = {"weekend": {"behaviour": "hands_off"}}
    with pytest.raises(ServiceValidationError, match="overrides mode “weekend”"):
        await _save(hass, bad)
    res = await hass.services.async_call(DOMAIN, "get_config", {}, blocking=True, return_response=True)
    assert [m["id"] for m in res["config"]["modes"]] == ["morning", "day", "evening", "overnight"]   # unchanged


async def test_save_makes_zone_sensors_and_keeps_the_mode(hass: HomeAssistant, kitchen) -> None:
    await _setup(hass)
    await hass.services.async_call("select", "select_option", {"entity_id": SELECT, "option": "overnight"}, blocking=True)
    assert await _save(hass, _table()) == {"warnings": []}
    assert hass.states.get(SELECT).attributes["options"] == ["day", "evening", "overnight"]
    assert hass.states.get(SELECT).state == "overnight"  # restored across the reload
    zone = hass.states.get("sensor.sb_modes_kitchen")
    assert zone.state == "nightlight_then_dark"
    assert zone.attributes["source"] == "override"
    assert zone.attributes["params"] == {"nightlight": "light.sink", "level": 10}

    await hass.services.async_call("select", "select_option", {"entity_id": SELECT, "option": "evening"}, blocking=True)
    zone = hass.states.get("sensor.sb_modes_kitchen")
    assert (zone.state, zone.attributes["source"]) == ("hands_off", "default")


async def test_deleting_the_active_mode_falls_back(hass: HomeAssistant, kitchen) -> None:
    await _setup(hass)
    await _save(hass, _table())
    await hass.services.async_call("select", "select_option", {"entity_id": SELECT, "option": "evening"}, blocking=True)
    smaller = _table()
    smaller["modes"] = [m for m in smaller["modes"] if m["id"] != "evening"]
    await _save(hass, smaller)
    assert hass.states.get(SELECT).state == "day"


async def test_lost_entity_raises_problem_then_repairs(hass: HomeAssistant, kitchen) -> None:
    await _setup(hass)
    await _save(hass, _table())
    hass.states.async_remove("light.sink")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))     # periodic re-check
    await hass.async_block_till_done()
    problem = hass.states.get(PROBLEM)
    assert problem.state == "on"
    assert problem.attributes["errors"] == ["Zone Kitchen, Overnight, nightlight: light.sink does not exist."]
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None          # still in its grace period

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61 + 121))
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None

    hass.states.async_set("light.sink", "off")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61 + 121 + 61))
    await hass.async_block_till_done()
    assert hass.states.get(PROBLEM).state == "off"
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None
