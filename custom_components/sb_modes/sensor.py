"""sensor.sb_modes_<zone> — what a zone runs in the active mode, and why.

State: the behaviour. Attributes: the zone, the mode, whether the behaviour is the
mode's default or the zone's own override, and its parameters. Also its live status
("vacant; dark at 23:14"), the lights and sensors it found in its areas, and what it
does in EVERY mode (by_mode), so the panel can preview a mode without switching to it.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import ModesEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    rt = entry.runtime_data
    add([ZoneSensor(rt, z) for z in rt.zones()])


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
            "hold": st.get("hold"),
            "overrides": st.get("overrides") or {},
            "by_mode": self._rt.zone_by_mode(self._zone_id),
            **{k: v for k, v in (st.get("entities") or {}).items()},
        }
