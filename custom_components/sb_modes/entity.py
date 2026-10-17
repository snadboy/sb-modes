"""Shared base: every entity hangs off one SB Modes service device and re-renders on any change."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .runtime import Runtime


class ModesEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, rt: Runtime, key: str) -> None:
        self._rt = rt
        self._attr_unique_id = f"{rt.entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, rt.entry.entry_id)},
            entry_type=DeviceEntryType.SERVICE,
            name="SB Modes",
            manufacturer="snadboy",
            model="SB Modes",
            sw_version=rt.hass.data.get(DOMAIN, {}).get("version"),
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._rt.add_listener(self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()
