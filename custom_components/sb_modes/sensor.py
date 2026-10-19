"""sensor.sb_modes_<zone> — what a zone runs in the active mode, and why.

State: the behaviour. Attributes: the zone, the mode, whether the behaviour is the
mode's default or the zone's own override, and its parameters. Also its live status
("vacant; dark at 23:14"), the lights and sensors it found in its areas, and what it
does in EVERY mode (by_mode), so the panel can preview a mode without switching to it.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import ModesEntity


SCHEDULE_STATES = ["on_schedule", "held_until_next", "held_timed", "paused", "no_schedule"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    rt = entry.runtime_data
    add([ScheduleSensor(rt), DeviationsSensor(rt), *[ZoneSensor(rt, z) for z in rt.zones()]])


class ScheduleSensor(ModesEntity, SensorEntity):
    """sensor.sb_modes_schedule — is the house following its schedule, and if not, how is it held."""
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = SCHEDULE_STATES
    _attr_translation_key = "schedule"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, rt) -> None:
        super().__init__(rt, "schedule")

    @property
    def native_value(self) -> str:
        return self._rt.schedule_state

    @property
    def extra_state_attributes(self) -> dict:
        sched, sched_since = self._rt.scheduled
        nxt, at = self._rt.upcoming
        m = self._rt.manual or {}
        return {
            "mode": self._rt.mode,
            "scheduled_mode": sched,
            "scheduled_since": sched_since.isoformat() if sched_since else None,
            "next_mode": nxt,
            "next_at": at.isoformat() if at else None,
            "previous_mode": self._rt.prev_mode,
            "hold_since": m.get("since"),
            "hold_until": m.get("until"),
        }


class DeviationsSensor(ModesEntity, SensorEntity):
    """sensor.sb_modes_deviations — how many things differ from automatic (0 = all automatic),
    each listed with its type: mode_hold, zone_hold, changed_by_hand."""
    _attr_translation_key = "deviations"
    _attr_icon = "mdi:hand-back-right-outline"
    _attr_state_class = "measurement"

    def __init__(self, rt) -> None:
        super().__init__(rt, "deviations")

    @property
    def native_value(self) -> int:
        return len(self._rt.deviations())

    @property
    def extra_state_attributes(self) -> dict:
        devs = self._rt.deviations()
        counts = {t: sum(1 for d in devs if d["type"] == t) for t in ("mode_hold", "zone_hold", "changed_by_hand")}
        return {"sb_modes_deviations": True, "deviations": devs, **counts}


class ZoneSensor(ModesEntity, SensorEntity):
    _attr_icon = "mdi:texture-box"

    def __init__(self, rt, zone: dict) -> None:
        super().__init__(rt, f"zone_{zone['id']}")
        self._zone_id = zone["id"]
        self._attr_name = zone.get("name") or zone["id"]

    @property
    def native_value(self) -> str | None:
        st = self._rt.zone_state(self._zone_id)
        return st["behaviour"] if st else None

    @property
    def extra_state_attributes(self) -> dict:
        zone = next((z for z in self._rt.zones() if z["id"] == self._zone_id), {})
        st = self._rt.zone_state(self._zone_id) or {}
        return {
            "sb_modes_zone": self._zone_id,
            "zone_name": zone.get("name") or self._zone_id,
            "order": next((i for i, z in enumerate(self._rt.zones()) if z["id"] == self._zone_id), None),
            "areas": zone.get("areas", []),
            "mode": self._rt.mode,
            "source": st.get("source"),
            "params": st.get("params", {}),
            "status": st.get("status"),
            "occupancy": st.get("occupancy"),
            "occupancy_since": st.get("occupancy_since"),
            "control": self._rt.zone_control(self._zone_id),
            "hold": st.get("hold"),
            "overrides": st.get("overrides") or {},
            "by_mode": self._rt.zone_by_mode(self._zone_id),
            **{k: v for k, v in (st.get("entities") or {}).items()},
        }
