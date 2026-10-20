"""What a zone holds: the entities in its areas that behaviours act on or listen to.

Taken from the registries each time a behaviour starts, so a device moved between
areas is followed without editing the table. An entity's own area wins over its
device's. Disabled entities and groups (platform "group": a light group, an occupancy
group) are left out — a group only repeats its members, and hides which of them sees what.

- lights:      light.* only. Switches are left out on purpose: the areas also hold
               locks, outlets and fan switches, and a switch shown as a light would
               otherwise appear twice.
- occupancy:   binary_sensor.* with device class occupancy or presence
- illuminance: sensor.* with device class illuminance
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

OCCUPANCY_CLASSES = ("occupancy", "presence")


def zone_entities(hass: HomeAssistant, areas: list[str], lights: list[str] | None = None,
                  occupancy: list[str] | None = None, illuminance: list[str] | None = None) -> dict[str, list[str]]:
    """The zone's entities; the zone's own `lights` / `occupancy` / `illuminance` list replaces
    what the areas hold (a sensor being replaced, or one that doesn't belong)."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    wanted = set(areas or [])
    out: dict[str, list[str]] = {"lights": [], "occupancy": [], "illuminance": []}
    for entry in ent_reg.entities.values():
        if entry.disabled_by or entry.platform == "group":
            continue                                     # groups only repeat their members
        area = entry.area_id
        if area is None and entry.device_id and (dev := dev_reg.async_get(entry.device_id)):
            area = dev.area_id
        if area not in wanted:
            continue
        dclass = entry.device_class or entry.original_device_class
        if entry.domain == "light":
            out["lights"].append(entry.entity_id)
        elif entry.domain == "binary_sensor" and dclass in OCCUPANCY_CLASSES:
            out["occupancy"].append(entry.entity_id)
        elif entry.domain == "sensor" and dclass == "illuminance":
            out["illuminance"].append(entry.entity_id)
    for key, own in (("lights", lights), ("occupancy", occupancy), ("illuminance", illuminance)):
        if own:
            out[key] = list(own)
    return {k: sorted(v) for k, v in out.items()}
