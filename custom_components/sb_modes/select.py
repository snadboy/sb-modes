"""select.sb_modes_mode — the active mode. Its options are the table's mode ids, nothing else.

Ids, not display names, so renaming a mode never breaks an automation that selects it.
Restored across restarts, with when it began (attribute `since`; last_changed is reset
by a restart) and any manual hold; a restored mode that no longer exists becomes the
fallback. Attributes also carry the schedule, the mode it says now and its next change.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import ModesEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add([ModeSelect(entry.runtime_data)])


class ModeSelect(ModesEntity, RestoreEntity, SelectEntity):
    _attr_translation_key = "mode"
    _attr_icon = "mdi:home-clock-outline"

    def __init__(self, rt) -> None:
        super().__init__(rt, "mode")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        since = (last.attributes.get("since") or last.last_changed.isoformat()) if last else None
        self._rt.restore_mode(last.state if last else None, since, last.attributes.get("manual") if last else None,
                              last.attributes.get("previous_mode") if last else None)

    @property
    def options(self) -> list[str]:
        return self._rt.mode_ids

    @property
    def current_option(self) -> str | None:
        return self._rt.mode

    @property
    def extra_state_attributes(self) -> dict:
        sched, _ = self._rt.scheduled
        nxt, at = self._rt.upcoming
        return {
            "sb_modes": True,
            "mode_names": {m: self._rt.mode_name(m) for m in self._rt.mode_ids},
            "fallback_mode": self._rt.fallback,
            "since": self._rt.mode_since,
            "previous_mode": self._rt.prev_mode,
            "takes_over": self._rt.takes_over(),
            "manual": self._rt.manual,
            "scheduled_mode": sched,
            "next_change": {"mode": nxt, "at": at.isoformat()} if at else None,
            "schedule": self._rt.config.get("schedule") or [],
        }

    async def async_select_option(self, option: str) -> None:
        """A plain select_option is a manual change held until the next scheduled change;
        sb_modes.set_mode takes other holds."""
        try:
            self._rt.set_mode(option, "next")
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
