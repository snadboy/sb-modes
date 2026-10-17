"""SB Modes — the house is in a mode; each zone runs one validated behaviour for it.

Step 1 (this version) is the skeleton: the mode table and its validation, the mode
select generated from it, a status sensor per zone, a problem sensor and a Repairs
issue. No behaviour acts on a device yet. Design:
https://claude.ai/artifact/9GAUvd4N2gctYm88fnMqhs

The sidebar page (/sb-modes) and the card (custom:sb-modes-card) are one JS module served
by this integration and loaded on every page, like SB Background's. They show the mode
(with a picker) and every zone's live status; the table is still edited through the
sb_modes.get_config / check_config / save_config actions.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import panel_custom
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from . import services
from .const import DOMAIN, ISSUE_ID, PANEL_URL, STATIC_URL
from .runtime import Runtime

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SELECT, Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    integration = await async_get_integration(hass, DOMAIN)
    version = str(integration.version) if integration.version else "0"
    hass.data[DOMAIN] = {"version": version}
    services.async_register(hass)
    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "frontend"), False)])
        url = f"{STATIC_URL}/sb-modes.js?v={version}"
        add_extra_js_url(hass, url)          # the card, on every dashboard
        if PANEL_URL not in hass.data.get("frontend_panels", {}):
            await panel_custom.async_register_panel(
                hass, frontend_url_path=PANEL_URL, webcomponent_name="sb-modes-panel",
                sidebar_title="SB Modes", sidebar_icon="mdi:home-clock-outline", module_url=url,
                embed_iframe=False, require_admin=False)
    except Exception:  # noqa: BLE001 — the UI is a convenience; the modes must run without it
        _LOGGER.exception("SB Modes: could not register the card / sidebar page")
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
