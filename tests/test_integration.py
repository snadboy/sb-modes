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
    assert await _save(hass, _table()) == {"warnings": [], "version": 2}   # 1 = the table before it
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
    assert "at least 5 min" in _zone(hass)["status"] or _zone(hass)["overrides"][CEILING]["unseen"]
    await _tick(hass, freezer, seconds=65)
    assert lights["off"] == []                          # turned on with no one seen: someone is there
    await _tick(hass, freezer, minutes=3, seconds=50)
    assert lights["off"] == []
    await _tick(hass, freezer, seconds=10)              # 5 min and still no one seen
    assert [c.data["entity_id"] for c in lights["off"]] == [CEILING]


async def test_vacancy_off_unseen_then_seen_uses_the_usual_delay(hass, kitchen, lights, freezer) -> None:
    await _setup(hass)
    await _save(hass, _with("vacancy_off", {"lights": [CEILING], "off_after_seconds": 60}))
    await _tick(hass, freezer, minutes=30)
    hass.states.async_set(CEILING, "on", {"brightness": 255}, context=Context(user_id="app"))
    await hass.async_block_till_done()
    await _tick(hass, freezer, seconds=20)
    hass.states.async_set(OCC, "on")                    # the sensor catches them…
    await hass.async_block_till_done()
    await _tick(hass, freezer, seconds=10)
    hass.states.async_set(OCC, "off")                   # …and they leave
    await hass.async_block_till_done()
    await _tick(hass, freezer, seconds=55)
    assert lights["off"] == []
    await _tick(hass, freezer, seconds=10)              # the usual 60 s after they left, not 5 min
    assert [c.data["entity_id"] for c in lights["off"]] == [CEILING]


GROUP = "light.kitchen_lights"


async def test_a_group_with_a_member_changed_by_hand_is_left_alone(hass, kitchen, lights, freezer) -> None:
    hass.states.async_set(GROUP, "off", {"entity_id": [SINK, CEILING]})
    await _setup(hass)
    await _save(hass, _with("vacancy_off", {"lights": [GROUP], "off_after_seconds": 30}))
    await _tick(hass, freezer, minutes=30)
    hass.states.async_set(SINK, "on", {"brightness": 255}, context=Context())       # at the wall, no one seen
    hass.states.async_set(GROUP, "on", {"entity_id": [SINK, CEILING]})
    await hass.async_block_till_done()
    await _tick(hass, freezer, seconds=35)
    assert lights["off"] == []                          # the group's off would undo the sink
    await _tick(hass, freezer, minutes=5)
    assert [c.data["entity_id"] for c in lights["off"]] == [SINK]           # lamp by lamp: only the one that is on


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


LUX2 = "sensor.kitchen_lux_2"


def _second_lux(hass, state):
    e = er.async_get(hass).async_get_or_create("sensor", "test", "kitchen_lux_2", suggested_object_id="kitchen_lux_2",
                                               original_device_class="illuminance")
    er.async_get(hass).async_update_entity(e.entity_id, area_id=ar.async_get(hass).async_get_area_by_name("Kitchen").id)
    hass.states.async_set(LUX2, state)


async def test_presence_lux_any_sensor_below_is_dark(hass, kitchen, lights) -> None:
    _second_lux(hass, "unavailable")
    await _evening_pl(hass)
    assert lights["on"] == [] and _zone(hass)["status"].startswith("occupied; light enough (60")   # offline: ignored
    hass.states.async_set(LUX2, "22")                                    # one of the two below 25: dark
    await hass.async_block_till_done()
    assert [(c.data["entity_id"], c.data.get("brightness_pct")) for c in lights["on"]] == [(CEILING, 82)]


async def test_presence_lux_no_sensor_reporting_goes_by_the_sun(hass, kitchen, lights) -> None:
    hass.states.async_set("sun.sun", "above_horizon")
    hass.states.async_set(LUX, "unavailable")
    await _evening_pl(hass)
    assert lights["on"] == [] and "by the sun" in _zone(hass)["status"]   # not "dark" for want of a reading
    hass.states.async_set("sun.sun", "below_horizon")
    await hass.async_block_till_done()
    hass.states.async_set(OCC, "off")
    hass.states.async_set(OCC, "on")                                      # the next look at it
    await hass.async_block_till_done()
    assert (CEILING, 82) in [(c.data["entity_id"], c.data.get("brightness_pct")) for c in lights["on"]]


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


def _st(hass, addon_id):
    return _zone(hass)["addon_status"].get(addon_id)


async def test_wake_up_kept_from_an_older_table(hass, kitchen, lights, freezer) -> None:
    """An older table's wake_up key still runs, as a "lights" add-on after Overnight ends."""
    await _setup(hass)
    t = _with("vacancy_off", {"lights": [CEILING], "off_after_seconds": 300})   # the room's own rule, untouched
    t["zones"][0]["wake_up"] = WAKE
    await _save(hass, t)
    await _mode(hass, "overnight")
    await _mode(hass, "day")                                              # came from Overnight
    assert _st(hass, "wake-up") == "running"
    await _tick(hass, freezer, minutes=30, seconds=1)
    assert [c.data.get("brightness_pct") for c in lights["on"]] == [1]
    hass.states.async_set(SINK, "on", {"brightness": 3}, context=Context(id=_rt(hass).own_contexts[-1]))   # our own step
    await _tick(hass, freezer, minutes=10)
    assert [c.data.get("brightness_pct") for c in lights["on"]] == [1, 4]
    assert _st(hass, "wake-up") == ""
    await _mode(hass, "evening")                                          # from Day: not a wake-up
    await _tick(hass, freezer, minutes=45)
    assert len(lights["on"]) == 2


def _clock_addon(at: str, when: dict | None = None, steps=None) -> dict:
    return {"id": "wake", "name": "Workday wake-up", "type": "lights", "trigger": {"at": at}, **({"when": when} if when else {}),
            "steps": steps or [{"after_minutes": 0, "targets": {SINK: {"brightness_pct": 1}}},
                               {"after_minutes": 10, "targets": {SINK: {"brightness_pct": 4}}}]}


async def test_lights_addon_at_a_clock_time_in_any_mode(hass, kitchen, lights, freezer) -> None:
    from homeassistant.util import dt as dt_util
    await _setup(hass)
    at = (dt_util.now() + timedelta(minutes=2)).strftime("%H:%M")
    t = _table()
    t["zones"][0]["addons"] = [_clock_addon(at)]
    await _save(hass, t)
    await _mode(hass, "evening")                                          # any mode: the clock decides
    assert _st(hass, "wake").startswith("next ")
    await _tick(hass, freezer, minutes=2, seconds=5)
    assert [c.data.get("brightness_pct") for c in lights["on"]] == [1]
    assert _st(hass, "wake") == "running"
    await _tick(hass, freezer, minutes=10)
    assert [c.data.get("brightness_pct") for c in lights["on"]] == [1, 4]
    assert _st(hass, "wake").startswith("next ")                          # tomorrow


async def test_lights_addon_when_modes_not_matching_is_skipped(hass, kitchen, lights, freezer) -> None:
    from homeassistant.util import dt as dt_util
    await _setup(hass)
    at = (dt_util.now() + timedelta(minutes=2)).strftime("%H:%M")
    t = _table()
    t["zones"][0]["addons"] = [_clock_addon(at, {"modes": ["overnight"]})]
    await _save(hass, t)
    await _mode(hass, "day")
    await _tick(hass, freezer, minutes=15)
    assert lights["on"] == []


async def test_lights_addon_respects_a_hold(hass, kitchen, lights, freezer) -> None:
    from homeassistant.util import dt as dt_util
    await _setup(hass)
    at = (dt_util.now() + timedelta(minutes=2)).strftime("%H:%M")
    t = _table()
    t["zones"][0]["addons"] = [_clock_addon(at)]
    await _save(hass, t)
    await hass.services.async_call(DOMAIN, "hold_zone", {"zone": "kitchen", "hold": 120}, blocking=True)
    await _tick(hass, freezer, minutes=2, seconds=5)
    assert lights["on"] == []                                             # held: the step is skipped


async def test_lights_addon_when_a_mode_begins_and_a_script(hass, kitchen, lights) -> None:
    scripts = async_mock_service(hass, "script", "turn_on")
    hass.states.async_set("script.fan_quiet", "off")
    await _setup(hass)
    t = _table()
    t["zones"][0]["addons"] = [
        {"id": "aux", "name": "Aux on", "type": "lights", "trigger": {"mode_begins": ["evening"]},
         "steps": [{"after_minutes": 0, "targets": {SINK: {"brightness_pct": 30}}}]},
        {"id": "fan", "name": "Fan", "type": "lights", "trigger": {"mode_ends": ["evening"]},
         "steps": [{"after_minutes": 0, "script": "script.fan_quiet"}]},
    ]
    await _save(hass, t)
    await _mode(hass, "evening")
    assert [c.data.get("brightness_pct") for c in lights["on"]] == [30]
    assert scripts == []
    await _mode(hass, "overnight")
    assert len(scripts) == 1 and scripts[0].data["entity_id"] == "script.fan_quiet"
    v = scripts[0].data["variables"]
    assert v["zone"] == "kitchen" and v["mode"] == "overnight" and v["addon"] == "Fan"


async def test_overlay_when_modes(hass, kitchen, lights, freezer) -> None:
    """A While add-on outside its When does nothing; the room keeps its own rule."""
    await _setup(hass)
    hass.states.async_set("media_player.tv", "off")
    t = _table()
    t["zones"][0]["addons"] = [{"id": "tv", "name": "TV lighting", "type": "while", "entity": "media_player.tv",
                                "when": {"modes": ["evening"]},
                                "states": {"playing": {"holds": True, "targets": {SINK: {"brightness_pct": 5}}}}}]
    await _save(hass, t)
    await _mode(hass, "day")
    hass.states.async_set("media_player.tv", "playing")
    await _tick(hass, freezer, seconds=6)
    assert lights["on"] == [] and _st(hass, "tv") == "not now"
    await _mode(hass, "evening")                                          # its When opens: it takes over
    assert (SINK, 5) in [(c.data["entity_id"], c.data.get("brightness_pct")) for c in lights["on"]]
    assert _st(hass, "tv").startswith("active")


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
    await _tick(hass, freezer, minutes=6)                                 # on with no one seen: the 5 min grace
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
    with pytest.raises(ServiceValidationError, match="trigger names mode “brunch”"):
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
    with pytest.raises(ServiceValidationError, match="an event entity"):
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


# ── groups are left out; the TV counts as presence; off by hand stays off ───

async def test_groups_are_not_zone_members(hass, kitchen) -> None:
    from custom_components.sb_modes.zones import zone_entities
    reg = er.async_get(hass)
    area = ar.async_get(hass).async_get_area_by_name("Kitchen")
    for domain, uid, dclass in (("light", "kitchen_all", None), ("binary_sensor", "kitchen_any", "occupancy")):
        e = reg.async_get_or_create(domain, "group", uid, suggested_object_id=uid, original_device_class=dclass)
        reg.async_update_entity(e.entity_id, area_id=area.id)
    z = zone_entities(hass, [area.id])
    assert z["lights"] == [CEILING, SINK] and z["occupancy"] == [OCC]


async def test_tv_counts_as_presence(hass, kitchen, lights, freezer) -> None:
    await _overlay_setup(hass)                       # vacancy_off 60 s, overlay on TV
    hass.states.async_set(SINK, "on", {"brightness": 200})
    hass.states.async_set(OCC, "off")
    await _tv(hass, freezer, "idle")                 # watching (idle between shows): occupied
    await _tick(hass, freezer, seconds=300)
    assert lights["off"] == [] and _zone(hass)["occupancy_state"] == "occupied"
    await _tv(hass, freezer, "off", wait=1)          # turned off: the empty clock starts NOW
    lights["on"].clear()
    await _tick(hass, freezer, seconds=30)
    # drop-outs while off don't restart that clock
    await _tv(hass, freezer, "unavailable", wait=0)
    await _tv(hass, freezer, "unknown", wait=0)
    await _tv(hass, freezer, "off", wait=0)
    await _tick(hass, freezer, seconds=25)
    assert [c.data["entity_id"] for c in lights["off"]] == []
    await _tick(hass, freezer, seconds=10)           # 66 s after the TV went off
    assert SINK in [c.data["entity_id"] for c in lights["off"]]


async def test_off_by_hand_stays_off_until_someone_returns(hass, kitchen, lights, freezer) -> None:
    await _setup(hass)
    hass.states.async_set(LUX, "5")
    hass.states.async_set(SINK, "on", {"brightness": 102})
    await _save(hass, _with("presence_lux", PL2))
    # bedtime: the lamp switched off by hand in an "empty" room (the sensor never saw them)
    hass.states.async_set(SINK, "off", context=Context(user_id="u"))
    await hass.async_block_till_done()
    lights["on"].clear()
    await _tick(hass, freezer, minutes=20)           # past the 15-min empty delay
    assert [c.data["entity_id"] for c in lights["on"]] == []          # not re-lit by 'empty'
    assert SINK in _rt(hass).keeper.overridden("kitchen")
    hass.states.async_set(OCC, "on")
    await hass.async_block_till_done()
    assert SINK not in _rt(hass).keeper.overridden("kitchen")         # someone came back: normal again


async def test_off_by_hand_survives_a_sensor_dropout(hass, kitchen, lights, freezer) -> None:
    await _setup(hass)
    hass.states.async_set(LUX, "5")
    hass.states.async_set(OCC, "on")
    hass.states.async_set(SINK, "on", {"brightness": 102})
    await _save(hass, _with("presence_lux", PL2))
    hass.states.async_set(SINK, "off", context=Context(user_id="u"))  # off by hand, in the room
    await hass.async_block_till_done()
    hass.states.async_set(OCC, "off")
    await _tick(hass, freezer, minutes=1)
    hass.states.async_set(OCC, "on")                 # the sensor blinks: they never left
    await hass.async_block_till_done()
    assert SINK in _rt(hass).keeper.overridden("kitchen")
    lights["on"].clear()
    await hass.async_block_till_done()
    assert SINK not in [c.data["entity_id"] for c in lights["on"]]


async def test_zone_own_occupancy_and_illuminance_lists(hass, kitchen, lights, freezer) -> None:
    """A zone's own occupancy list replaces the area's sensors (a sensor being retired)."""
    hass.states.async_set("binary_sensor.new_occ", "off", {"device_class": "occupancy"})
    await _setup(hass)
    t = _with("vacancy_off", {"lights": [SINK], "off_after_seconds": 60})
    t["zones"][0]["occupancy"] = ["binary_sensor.new_occ"]
    t["zones"][0]["illuminance"] = [LUX]
    await _save(hass, t)
    assert _zone(hass)["occupancy"] == ["binary_sensor.new_occ"]
    hass.states.async_set(SINK, "on", {"brightness": 200})
    hass.states.async_set(OCC, "on")                       # the old sensor no longer counts
    await _tick(hass, freezer, seconds=90)
    assert SINK in [c.data["entity_id"] for c in lights["off"]]
    bad = _table()
    bad["zones"][0]["occupancy"] = ["light.sink"]
    with pytest.raises(ServiceValidationError, match="not a binary_sensor"):
        await _save(hass, bad)


# ── activity: what SB Modes did, and why ─────────────────────────────────────

def _acts(hass):
    from custom_components.sb_modes.activity import EVENT_ACTIVITY
    return async_capture_events(hass, EVENT_ACTIVITY)


async def test_activity_commands_and_noops(hass, kitchen, lights, freezer) -> None:
    await _setup(hass)
    hass.states.async_set(LUX, "5")
    await _save(hass, _with("presence_lux", PL2))
    acts = _acts(hass)
    hass.states.async_set(OCC, "on")
    await hass.async_block_till_done()
    cmd = [a.data for a in acts if a.data["kind"] == "command"]
    assert cmd and cmd[0]["zone"] == "kitchen" and cmd[0]["zone_name"] == "Kitchen"
    assert cmd[0]["message"].startswith("occupied and dark (") and cmd[0]["message"].endswith("→ ceiling 95%")
    assert cmd[0]["entity_id"] == "sensor.sb_modes_kitchen" and cmd[0]["entities"] == [CEILING]
    # the light calls carry the event's context (the Logbook's "triggered by")
    assert lights["on"][-1].context.id == acts[-1].context.id
    # a decision that changes nothing is not logged
    hass.states.async_set(CEILING, "on", {"brightness": 242}, context=lights["on"][-1].context)
    await hass.async_block_till_done()
    n = len(acts)
    _rt(hass).apply({CEILING: {"brightness_pct": 95}}, "kitchen: again", "kitchen")
    await hass.async_block_till_done()
    assert len(acts) == n


async def test_activity_hand_hold_mode(hass, kitchen, lights, freezer) -> None:
    await _setup(hass)
    await _save(hass, _with("vacancy_off", {"lights": [SINK], "off_after_seconds": 60}))
    acts = _acts(hass)
    hass.states.async_set(SINK, "on", {"brightness": 128}, context=Context(user_id="u"))
    await hass.async_block_till_done()
    assert [(a.data["kind"], a.data["message"]) for a in acts] == [("hand", "sink 50% by hand — left alone at least 5 min (no one seen yet)")]
    await hass.services.async_call(DOMAIN, "hold_zone", {"zone": "kitchen", "hold": 30}, blocking=True)
    _rt(hass).apply({SINK: {"state": "off"}}, "kitchen: off: vacant", "kitchen")
    await hass.async_block_till_done()
    kinds = [(a.data["kind"], a.data["message"]) for a in acts[1:]]
    assert ("hold", "held 30 min (by hand)") in kinds
    assert ("skipped", "empty: lights off — held, nothing sent (sink off)") in kinds
    await hass.services.async_call(DOMAIN, "release_zone", {"zone": "kitchen"}, blocking=True)
    await _mode(hass, "evening")
    msgs = [a.data["message"] for a in acts]
    assert "released (by hand): back to its setting for Day" in msgs
    assert any(m.startswith("Day → Evening (by hand)") for m in msgs)
    assert [a.data["entity_id"] for a in acts if a.data["kind"] == "mode"] == [SELECT]


async def test_logbook_describes_activity(hass) -> None:
    from custom_components.sb_modes import logbook as lb
    from custom_components.sb_modes.activity import EVENT_ACTIVITY
    from homeassistant.core import Event
    got = {}
    lb.async_describe_events(hass, lambda domain, event_type, fn: got.setdefault(event_type, fn))
    entry = got[EVENT_ACTIVITY](Event(EVENT_ACTIVITY, {"kind": "command", "zone": "kitchen", "zone_name": "Kitchen",
                                                       "message": "empty since 16:45 → ceiling off", "entity_id": "sensor.sb_modes_kitchen"}))
    assert entry == {"name": "SB Modes · Kitchen", "message": "empty since 16:45 → ceiling off",
                     "icon": "mdi:lightbulb-auto-outline", "entity_id": "sensor.sb_modes_kitchen"}


async def test_tv_turned_on_ends_earlier_hand_changes(hass, kitchen, lights, freezer) -> None:
    """Lamps switched on to walk in, then the TV on: the TV levels take over. Changes made
    while it is on still win."""
    await _overlay_setup(hass)
    hass.states.async_set(OCC, "on")
    await _tick(hass, freezer, seconds=30)                     # the TV has been off a while
    hass.states.async_set(SINK, "on", {"brightness": 255}, context=Context(user_id="u"))
    await hass.async_block_till_done()
    assert SINK in _rt(hass).keeper.overridden("kitchen")
    await _tv(hass, freezer, "playing")                        # turned on
    assert SINK not in _rt(hass).keeper.overridden("kitchen")
    assert _sink_calls(lights)[-1] == ("on", 5, 3)
    hass.states.async_set(SINK, "on", {"brightness": 13}, context=lights["on"][-1].context)  # the lamp dims
    await hass.async_block_till_done()
    lights["on"].clear()
    hass.states.async_set(SINK, "on", {"brightness": 255}, context=Context(user_id="u"))   # laundry, during the show
    await hass.async_block_till_done()
    await _tv(hass, freezer, "paused")
    assert lights["on"] == []


async def test_tv_off_blip_is_not_a_turn_on(hass, kitchen, lights, freezer) -> None:
    await _overlay_setup(hass)
    hass.states.async_set(OCC, "on")
    await _tv(hass, freezer, "playing")
    hass.states.async_set(SINK, "on", {"brightness": 255}, context=Context(user_id="u"))
    await hass.async_block_till_done()
    await _tv(hass, freezer, "off", wait=1)                    # a one-second "off" mid-show
    await _tv(hass, freezer, "playing")
    assert SINK in _rt(hass).keeper.overridden("kitchen")


# ── editor backend (websocket) ──────────────────────────────────────────────

async def _ws(hass, hass_ws_client, **msg):
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(msg)
    return await client.receive_json()


async def test_editor_schema_and_check(hass, kitchen, hass_ws_client) -> None:
    await _setup(hass)
    r = await _ws(hass, hass_ws_client, type="sb_modes/schema")
    assert r["success"] and r["result"]["behaviours"]["presence_lux"]["label"] == "Presence + light level"
    assert r["result"]["behaviours"]["presence_lux"]["params"]["target_lux"]["unit"] == "lx"
    bad = _table()
    bad["zones"][0]["overrides"]["overnight"]["params"]["delay"] = 999
    r = await _ws(hass, hass_ws_client, type="sb_modes/check", config=bad)
    assert r["result"]["errors"] == [{"path": ["zones", 0, "overrides", "overnight", "params", "delay"],
                                      "message": "Zone Kitchen, Overnight, delay: 999 is outside 1–240."}]


async def test_editor_draft_save_versions_revert(hass, kitchen, hass_ws_client) -> None:
    await _setup(hass)
    await _save(hass, _table())                                       # version 2 (1 = what was there)
    client = await hass_ws_client(hass)
    async def ws(**m):
        await client.send_json_auto_id(m)
        return await client.receive_json()
    t = _table()
    t["modes"][1]["name"] = "Night-ish"
    r = await ws(type="sb_modes/draft/set", config=t)
    assert r["result"]["changes"] == ["Mode Evening renamed Night-ish"] and r["result"]["errors"] == []
    r = await ws(type="sb_modes/draft/get")
    assert r["result"]["draft"] == t and r["result"]["stale"] is False and r["result"]["base_version"] == 2
    r = await ws(type="sb_modes/save")
    assert r["success"] and r["result"]["version"] == 3 and r["result"]["changes"] == ["Mode Evening renamed Night-ish"]
    await hass.async_block_till_done()
    assert hass.states.get(SELECT).attributes["mode_names"]["evening"] == "Night-ish"
    assert (await ws(type="sb_modes/draft/get"))["result"]["draft"] is None            # saved → draft gone
    vs = (await ws(type="sb_modes/versions"))["result"]["versions"]
    assert [v["version"] for v in vs] == [3, 2, 1] and "config" not in vs[0]
    r = await ws(type="sb_modes/revert", version=2)
    assert r["success"] and r["result"]["version"] == 4 and r["result"]["changes"] == ["Mode Night-ish renamed Evening"]
    await hass.async_block_till_done()
    assert hass.states.get(SELECT).attributes["mode_names"]["evening"] == "Evening"


async def test_editor_stale_draft_and_errors_block_save(hass, kitchen, hass_ws_client) -> None:
    await _setup(hass)
    client = await hass_ws_client(hass)
    async def ws(**m):
        await client.send_json_auto_id(m)
        return await client.receive_json()
    t = _table()
    t["modes"][0]["name"] = "Daytime"
    await ws(type="sb_modes/draft/set", config=t)                     # began at version 0
    await _save(hass, _table())                                       # someone else saves meanwhile
    assert (await ws(type="sb_modes/draft/get"))["result"]["stale"] is True
    r = await ws(type="sb_modes/save")
    assert not r["success"] and "saved elsewhere" in r["error"]["message"]
    assert (await ws(type="sb_modes/save", force=True))["success"]
    bad = _table()
    bad["fallback_mode"] = "party"
    r = await ws(type="sb_modes/save", config=bad)
    assert not r["success"] and "not a defined mode" in r["error"]["message"]


async def test_editor_draft_same_as_the_table_is_not_kept(hass, kitchen, hass_ws_client) -> None:
    await _setup(hass)
    await _save(hass, _table())
    client = await hass_ws_client(hass)
    async def ws(**m):
        await client.send_json_auto_id(m)
        return await client.receive_json()
    t = _table()
    t["modes"][0]["name"] = "Daytime"
    await ws(type="sb_modes/draft/set", config=t)
    r = await ws(type="sb_modes/draft/set", config=_table())          # the edit undone (or a form's first value)
    assert r["result"]["same"] is True and r["result"]["changes"] == []
    assert (await ws(type="sb_modes/draft/get"))["result"]["draft"] is None


async def test_editor_try_it(hass, kitchen, lights, hass_ws_client) -> None:
    await _setup(hass)
    await _save(hass, _table())
    r = await _ws(hass, hass_ws_client, type="sb_modes/try", zone="kitchen", targets={SINK: {"brightness_pct": 40}})
    assert r["success"] and r["result"]["held_minutes"] == 15
    await hass.async_block_till_done()
    assert _rt(hass).keeper.held("kitchen")["hold"] == "timed"
    assert lights["on"][-1].data == {"entity_id": SINK, "brightness_pct": 40}
    r = await _ws(hass, hass_ws_client, type="sb_modes/try", zone="kitchen", targets={SINK: {"brightness_pct": 400}})
    assert not r["success"]


async def test_editor_preview_week(hass, kitchen, hass_ws_client, freezer) -> None:
    client = await hass_ws_client(hass)                     # log in first: the clock goes back below
    async def ws(**m):
        await client.send_json_auto_id(m)
        return await client.receive_json()
    await _at(hass, freezer, 10, 5)
    await _setup(hass)
    await _save(hass, _sched_table())
    t = _sched_table()
    t["schedule"].append({"days": ["sat", "sun"], "at": "12:00", "mode": "day"})
    r = await ws(type="sb_modes/preview", config=t, days=2)
    res = r["result"]
    assert r["success"] and res["now"] == "day" and res["missing_day_sets"] == []
    got = [(c["at"][11:16], c["mode"]) for c in res["changes"]]
    # Fri 10:05: evening 10:10, overnight 10:20; Sat: day 10:00, evening, overnight, day again 12:00;
    # Sun 10:00 "day" is no change (already Day since Sat 12:00), so it is left out
    assert got == [("10:10", "evening"), ("10:20", "overnight"), ("10:00", "day"), ("10:10", "evening"),
                   ("10:20", "overnight"), ("12:00", "day")], got
    # a rule on a day set SB Scheduler doesn't publish shows as missing
    t["schedule"].append({"day_set": "nope", "at": "08:00", "mode": "day"})
    r = await ws(type="sb_modes/preview", config=t, days=1)
    assert r["result"]["missing_day_sets"] == ["nope"]


async def test_mode_icons_reach_the_select(hass, kitchen) -> None:
    await _setup(hass)
    t = _table()
    t["modes"][0]["icon"] = "mdi:white-balance-sunny"
    await _save(hass, t)
    assert hass.states.get(SELECT).attributes["mode_icons"] == {"day": "mdi:white-balance-sunny"}
    t["modes"][0]["icon"] = "sunny"
    with pytest.raises(ServiceValidationError, match="must look like mdi:"):
        await _save(hass, t)


async def test_offline_sensor_does_not_block_empty(hass, kitchen, lights, freezer) -> None:
    """Two presence sensors, one offline: the room empties on the other's say, counted from
    no earlier than the moment the offline one dropped out. Both offline: nothing happens."""
    hass.states.async_set("binary_sensor.k2", "on")
    await _setup(hass)
    t = _with("vacancy_off", {"lights": [SINK], "off_after_seconds": 60})
    t["zones"][0]["occupancy"] = [OCC, "binary_sensor.k2"]
    await _save(hass, t)
    hass.states.async_set(SINK, "on", {"brightness": 200})
    hass.states.async_set(OCC, "off")
    await _tick(hass, freezer, seconds=600)
    hass.states.async_set("binary_sensor.k2", "unavailable")          # the one seeing someone drops out
    await _tick(hass, freezer, seconds=30)
    assert lights["off"] == []                                         # not yet: 60 s from the dropout
    await _tick(hass, freezer, seconds=40)
    assert SINK in [c.data["entity_id"] for c in lights["off"]]
    hass.states.async_set(OCC, "unavailable")                          # both offline: can't tell
    hass.states.async_set(SINK, "on", {"brightness": 200})
    lights["off"].clear()
    await _tick(hass, freezer, seconds=600)
    assert lights["off"] == [] and _zone(hass)["occupancy_state"] == "unknown"


# ── auto-off: a lamp turned on by hand, that SB Modes only turns off ─────────

def _office_like():
    t = _with("occupied_vacant", {"occupied": {CEILING: {"brightness_pct": 80}}, "vacant": {CEILING: {"state": "off"}},
                                  "vacant_after_minutes": 2})
    t["zones"][0]["auto_off"] = [{"lights": [SINK], "with_empty": True, "on_minutes": 15}]
    return t


async def test_auto_off_goes_with_the_empty_setting(hass, kitchen, lights, freezer) -> None:
    hass.states.async_set(OCC, "on")
    hass.states.async_set(CEILING, "on", {"brightness": 204})
    await _setup(hass)
    await _save(hass, _office_like())
    hass.states.async_set(SINK, "on", {"brightness": 255}, context=Context(user_id="u"))    # turned on by hand
    await _tick(hass, freezer, seconds=30)
    hass.states.async_set(OCC, "off")
    await _tick(hass, freezer, seconds=100)
    assert lights["off"] == []                                          # not yet empty 2 min
    await _tick(hass, freezer, seconds=30)
    offs = [c.data["entity_id"] for c in lights["off"]]
    assert CEILING in offs and SINK in offs, offs                       # the lamp went with the others
    assert SINK not in _rt(hass).keeper.overridden("kitchen")


async def test_auto_off_after_minutes_when_alone(hass, kitchen, lights, freezer) -> None:
    hass.states.async_set(OCC, "on")
    await _setup(hass)
    await _save(hass, _office_like())
    hass.states.async_set(CEILING, "off")                               # the other lights are off
    hass.states.async_set(SINK, "on", {"brightness": 255}, context=Context(user_id="u"))
    await _tick(hass, freezer, minutes=1)
    hass.states.async_set(OCC, "off")
    await _tick(hass, freezer, minutes=5)                               # empty 5 min, lamp on 6 min
    assert SINK not in [c.data["entity_id"] for c in lights["off"]]
    await _tick(hass, freezer, minutes=10)                              # lamp on 16 min, room empty
    assert SINK in [c.data["entity_id"] for c in lights["off"]]
    # someone in the room: it stays on however long
    lights["off"].clear()
    hass.states.async_set(OCC, "on")
    hass.states.async_set(SINK, "on", {"brightness": 255}, context=Context(user_id="u"))
    await _tick(hass, freezer, minutes=40)
    assert lights["off"] == []
    assert SINK not in [c.data["entity_id"] for c in lights["on"]]      # and SB Modes never turned it on


async def test_auto_off_validation(hass, kitchen) -> None:
    await _setup(hass)
    t = _table()
    t["zones"][0]["auto_off"] = [{"lights": [SINK]}]
    with pytest.raises(ServiceValidationError, match="choose when it turns off"):
        await _save(hass, t)


async def test_addon_validation(hass, kitchen) -> None:
    await _setup(hass)
    def bad(addons, msg):
        t = _table()
        t["zones"][0]["addons"] = addons
        return pytest.raises(ServiceValidationError, match=msg), t
    good = {"id": "a", "name": "Wake", "type": "lights", "trigger": {"at": "06:30"}, "steps": [{"after_minutes": 0, "targets": {SINK: {"brightness_pct": 1}}}]}
    cases = [
        ([{**good, "when": {"modes": ["brunch"]}}], "When names mode “brunch”"),
        ([{**good, "trigger": {"at": "6:30"}}], "is not a time"),
        ([{**good, "steps": [{"after_minutes": 0, "targets": {SINK: {"state": "on"}}, "script": "script.x"}]}], "choose one action"),
        ([good, {**good}], "used twice"),
        ([{"id": "t1", "name": "TV", "type": "while", "entity": SINK, "states": {"on": {}}},
          {"id": "t2", "name": "TV 2", "type": "while", "entity": SINK, "states": {"on": {}}}], "one TV lighting"),
        ([{**good, "type": "party"}], "unknown add-on type"),
        ([{**good, "when": {"between": ["22:00"]}}], "between must be two times"),
        ([{**good, "steps": [{"after_minutes": 0, "targets": {}}]}], "choose at least one light"),
    ]
    for addons, msg in cases:
        ctx, t = bad(addons, msg)
        with ctx:
            await _save(hass, t)


async def test_off_light_given_a_colour_is_turned_on_first(hass, kitchen, lights) -> None:
    """Matter lights ignore colour while off: on (brightness) first, then the colour."""
    await _setup(hass)
    await _save(hass, _table())
    _rt(hass).apply({SINK: {"brightness_pct": 25, "color_temp_kelvin": 3401}, CEILING: {"brightness_pct": 50}}, "test", "kitchen")
    await hass.async_block_till_done()
    sink = [c.data for c in lights["on"] if c.data["entity_id"] == SINK]
    assert sink == [{"entity_id": SINK, "brightness_pct": 25}, {"entity_id": SINK, "brightness_pct": 25, "color_temp_kelvin": 3401}]
    assert [c.data for c in lights["on"] if c.data["entity_id"] == CEILING] == [{"entity_id": CEILING, "brightness_pct": 50}]
    hass.states.async_set(SINK, "on", {"brightness": 64, "color_temp_kelvin": 2700})        # on already: one call
    lights["on"].clear()
    _rt(hass).apply({SINK: {"brightness_pct": 25, "color_temp_kelvin": 3401}}, "test", "kitchen")
    await hass.async_block_till_done()
    assert [c.data for c in lights["on"]] == [{"entity_id": SINK, "brightness_pct": 25, "color_temp_kelvin": 3401}]



async def test_group_checked_lamp_by_lamp(hass, kitchen, lights) -> None:
    """A group that 'matches' on average can hide one lamp in another colour: that lamp gets a call."""
    hass.states.async_set(SINK, "on", {"brightness": 64, "color_temp_kelvin": 3401})
    hass.states.async_set(CEILING, "on", {"brightness": 64, "color_temp_kelvin": None})
    hass.states.async_set(GROUP, "on", {"entity_id": [SINK, CEILING], "brightness": 64, "color_temp_kelvin": 3401})
    await _setup(hass)
    await _save(hass, _table())
    _rt(hass).apply({GROUP: {"brightness_pct": 25, "color_temp_kelvin": 3401}}, "test", "kitchen")
    await hass.async_block_till_done()
    assert [c.data for c in lights["on"]] == [{"entity_id": CEILING, "brightness_pct": 25, "color_temp_kelvin": 3401}]


async def test_lights_addon_before_a_calendar_event_ends(hass, kitchen, lights, freezer) -> None:
    """'1 h before the #vac event ends' runs a script; a '#vacuum' event does not count."""
    from homeassistant.core import SupportsResponse
    from homeassistant.util import dt as dt_util
    now = dt_util.utcnow()
    events = [{"summary": "#vacuum day", "start": (now + timedelta(minutes=10)).isoformat(), "end": (now + timedelta(minutes=70)).isoformat()},
              {"summary": "Trip #vac", "start": (now - timedelta(days=1)).isoformat(), "end": (now + timedelta(hours=2)).isoformat()}]
    async def get_events(call):
        return {e: {"events": events} for e in call.data["entity_id"]} if isinstance(call.data["entity_id"], list) else {call.data["entity_id"]: {"events": events}}
    hass.services.async_register("calendar", "get_events", get_events, supports_response=SupportsResponse.ONLY)
    hass.states.async_set("calendar.anderson", "off")
    hass.states.async_set("script.thermostat_home", "off")
    scripts = async_mock_service(hass, "script", "turn_on")
    await _setup(hass)
    t = _table()
    t["zones"][0]["addons"] = [{"id": "home", "name": "Thermostat home", "type": "lights",
                                "trigger": {"calendar": "calendar.anderson", "match": "#vac", "edge": "end", "offset_minutes": -60},
                                "steps": [{"after_minutes": 0, "script": "script.thermostat_home"}]}]
    await _save(hass, t)
    await hass.async_block_till_done()
    assert _st(hass, "home").startswith("next ")
    await _tick(hass, freezer, minutes=59)
    assert scripts == []
    await _tick(hass, freezer, minutes=2)
    assert [c.data["entity_id"] for c in scripts] == ["script.thermostat_home"]


def _calendar(hass, events):
    from homeassistant.core import SupportsResponse
    async def get_events(call):
        ids = call.data["entity_id"] if isinstance(call.data["entity_id"], list) else [call.data["entity_id"]]
        return {e: {"events": events} for e in ids}
    hass.services.async_register("calendar", "get_events", get_events, supports_response=SupportsResponse.ONLY)
    hass.states.async_set("calendar.anderson", "off")


async def test_mode_steps_from_a_calendar_event(hass, kitchen, lights, freezer) -> None:
    """'#vac' starts → the house goes to a mode until released; it ends → back on schedule."""
    from homeassistant.util import dt as dt_util
    now = dt_util.utcnow()
    _calendar(hass, [{"summary": "#vac", "start": (now + timedelta(minutes=30)).isoformat(), "end": (now + timedelta(hours=3)).isoformat()}])
    await _setup(hass)
    t = _table()
    cal = lambda edge: {"calendar": "calendar.anderson", "match": "#vac", "edge": edge, "offset_minutes": 0}   # noqa: E731
    t["zones"][0]["addons"] = [
        {"id": "away", "name": "Away", "type": "lights", "trigger": cal("start"), "steps": [{"after_minutes": 0, "set_mode": "overnight", "hold": "paused"}]},
        {"id": "back", "name": "Back", "type": "lights", "trigger": cal("end"), "steps": [{"after_minutes": 0, "resume_schedule": True}]}]
    await _save(hass, t)
    await _mode(hass, "day")
    await _tick(hass, freezer, minutes=31)
    assert _sel(hass).state == "overnight"
    await _tick(hass, freezer, hours=3)
    assert _sel(hass).attributes.get("manual") in (None, {}) or _sel(hass).state != "overnight"


async def test_time_trigger_random_shift(hass, kitchen, lights, freezer, monkeypatch) -> None:
    from homeassistant.util import dt as dt_util
    import custom_components.sb_modes.addons as ad
    monkeypatch.setattr(ad.random, "uniform", lambda a, b: b)              # always the latest
    await _setup(hass)
    at = (dt_util.now() + timedelta(minutes=5)).strftime("%H:%M")
    t = _table()
    t["zones"][0]["addons"] = [{"id": "lit", "name": "Lived-in", "type": "lights", "trigger": {"at": at, "random_minutes": 10},
                                "steps": [{"after_minutes": 0, "targets": {SINK: {"brightness_pct": 40}}},
                                          {"after_minutes": 30, "random_minutes": 20, "targets": {SINK: {"state": "off"}}}]}]
    await _save(hass, t)
    await _tick(hass, freezer, minutes=14)
    assert lights["on"] == []                                               # 5 + 10 min: not yet
    await _tick(hass, freezer, minutes=2)
    assert [c.data.get("brightness_pct") for c in lights["on"]] == [40]
    hass.states.async_set(SINK, "on", {"brightness": 102}, context=Context(id=_rt(hass).own_contexts[-1]))
    await _tick(hass, freezer, minutes=49)
    assert lights["off"] == []                                              # 30 + 20 min later
    await _tick(hass, freezer, minutes=2)
    assert [c.data["entity_id"] for c in lights["off"]] == [SINK]


async def test_replay_plays_last_weeks_changes(hass, kitchen, lights, freezer, monkeypatch) -> None:
    from homeassistant.core import State
    from homeassistant.util import dt as dt_util
    import custom_components.sb_modes.replay as rp
    now = dt_util.utcnow()
    week = timedelta(days=7)
    recorded = {SINK: [State(SINK, "on", {"brightness": 128}, last_changed=now - week + timedelta(minutes=10)),
                       State(SINK, "off", {}, last_changed=now - week + timedelta(minutes=20))]}
    async def history(self, start, end):
        return {e: [s for s in v if start <= s.last_changed <= end] for e, v in recorded.items()}
    monkeypatch.setattr(rp.ReplayAddon, "history", history)
    await _setup(hass)
    t = _table()
    t["zones"][0]["addons"] = [{"id": "rp", "name": "Replay", "type": "replay", "lights": [SINK], "days_back": 7, "when": {"modes": ["evening"]}}]
    await _save(hass, t)
    await _mode(hass, "evening")
    await hass.async_block_till_done()
    await _tick(hass, freezer, minutes=11)
    assert [c.data.get("brightness_pct") for c in lights["on"]] == [50]
    hass.states.async_set(SINK, "on", {"brightness": 128}, context=Context(id=_rt(hass).own_contexts[-1]))
    await _tick(hass, freezer, minutes=10)
    assert [c.data["entity_id"] for c in lights["off"]] == [SINK]


async def test_time_addon_outside_its_modes_does_not_resume(hass, kitchen, lights, freezer) -> None:
    """A lived-in add-on (When: Away) loaded after its time today, in another mode, arms nothing."""
    from homeassistant.util import dt as dt_util
    at = (dt_util.now() - timedelta(minutes=30)).strftime("%H:%M")
    await _setup(hass)
    t = _table()
    t["zones"][0]["addons"] = [{"id": "lit", "name": "Lived-in", "type": "lights", "when": {"modes": ["overnight"]}, "trigger": {"at": at},
                                "steps": [{"after_minutes": 0, "targets": {SINK: {"brightness_pct": 40}}},
                                          {"after_minutes": 60, "targets": {SINK: {"state": "off"}}}]}]
    await _mode(hass, "day")
    await _save(hass, t)
    assert _st(hass, "lit") == "only in Overnight"
    await _tick(hass, freezer, minutes=45)
    assert lights["off"] == []


async def test_button_flash_only_chosen_lights(hass, kitchen, lights, freezer, monkeypatch) -> None:
    """flash_lights: the confirmation blinks only those lights (the Office desk bulb)."""
    from custom_components.sb_modes import buttons as btn
    monkeypatch.setattr(btn, "FLASH_SECONDS", 0)
    hass.states.async_set(BIG, "2026-10-01T00:00:00+00:00", {"event_type": "multi_press_1"})
    hass.states.async_set(SINK, "on", {"brightness": 200})
    await _setup(hass)
    t = _with("vacancy_off", {"lights": [SINK], "off_after_seconds": 60})
    t["zones"][0]["addons"] = [{"id": "b", "name": "Hold", "type": "button", "entity": BIG,
                                "presses": {"multi_press_1": {"hold": "next", "flash": 1, "flash_lights": [SINK]}}}]
    await _save(hass, t)
    lights["on"].clear(); lights["off"].clear()
    await _press(hass, freezer, BIG, "multi_press_1")
    assert [c.data["entity_id"] for c in lights["off"]] == [SINK]           # the ceiling (off) is not blinked
    assert [c.data["entity_id"] for c in lights["on"]] == [SINK]
