"""binary_sensor.sb_modes_problem — on while the saved table has errors (an entity or area gone).

Errors and warnings are attributes, so a dashboard can list them without the panel.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import ModesEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add([ProblemSensor(entry.runtime_data)])


class ProblemSensor(ModesEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "problem"

    def __init__(self, rt) -> None:
        super().__init__(rt, "problem")

    @property
    def is_on(self) -> bool:
        return bool(self._rt.errors)

    @property
    def extra_state_attributes(self) -> dict:
        return {"sb_modes_problem": True, "errors": self._rt.errors, "warnings": self._rt.warnings}
