"""SB Modes — the house is in a mode; each zone runs one validated behaviour for it.

Step 1 (this version) is the skeleton: the mode table and its validation, the mode
select generated from it, a status sensor per zone, a problem sensor and a Repairs
issue. No behaviour acts on a device yet. Design:
https://claude.ai/artifact/9GAUvd4N2gctYm88fnMqhs

The table is edited through services for now (sb_modes.get_config / check_config /
save_config); a sidebar panel and a card come in a later step.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from . import services
from .const import DOMAIN, ISSUE_ID
from .runtime import Runtime

PLATFORMS = [Platform.SELECT, Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    integration = await async_get_integration(hass, DOMAIN)
    hass.data[DOMAIN] = {"version": str(integration.version) if integration.version else "0"}
    services.async_register(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    rt = Runtime(hass, entry)
    await rt.async_load()
    entry.runtime_data = rt
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    rt.start()
    entry.async_on_unload(rt.stop)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    ir.async_delete_issue(hass, DOMAIN, ISSUE_ID)
