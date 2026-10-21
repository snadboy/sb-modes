"""HA Logbook: SB Modes' decisions, in words (see activity.py)."""

from __future__ import annotations

from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_ENTITY_ID, LOGBOOK_ENTRY_ICON, LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME,
)
from homeassistant.core import Event, HomeAssistant, callback

from .activity import EVENT_ACTIVITY, ICONS
from .const import DOMAIN


@callback
def describe(event: Event) -> dict:
    d = event.data
    entry = {
        LOGBOOK_ENTRY_NAME: f"SB Modes · {d['zone_name']}" if d.get("zone_name") else "SB Modes",
        LOGBOOK_ENTRY_MESSAGE: d.get("message") or "",
        LOGBOOK_ENTRY_ICON: ICONS.get(d.get("kind"), "mdi:texture-box"),
    }
    if d.get("entity_id"):
        entry[LOGBOOK_ENTRY_ENTITY_ID] = d["entity_id"]
    return entry


@callback
def async_describe_events(hass: HomeAssistant, async_describe_event) -> None:
    async_describe_event(DOMAIN, EVENT_ACTIVITY, describe)
