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

A room's own `lights` / `occupancy` adjust what its areas hold: {"exclude": [...], "add": [...]}
(an excluded entity is left out, an added one comes from anywhere, switches included). The older
plain list replaced the areas' list outright; it is still read, and `migrate_rooms` converts it.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .model import apply_spec

OCCUPANCY_CLASSES = ("occupancy", "presence")


def area_entities(hass: HomeAssistant, areas: list[str]) -> dict[str, list[str]]:
    """Everything a room's areas offer: lights, groups (light groups, kept apart), switches
    (not hidden, not device settings), occupancy and illuminance sensors."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    wanted = set(areas or [])
    out: dict[str, list[str]] = {"lights": [], "groups": [], "switches": [], "occupancy": [], "illuminance": []}
    for entry in ent_reg.entities.values():
        if entry.disabled_by:
            continue
        area = entry.area_id
        if area is None and entry.device_id and (dev := dev_reg.async_get(entry.device_id)):
            area = dev.area_id
        if area not in wanted:
            continue
        dclass = entry.device_class or entry.original_device_class
        if entry.platform == "group":
            if entry.domain == "light":
                out["groups"].append(entry.entity_id)    # groups only repeat their members
        elif entry.domain == "light":
            out["lights"].append(entry.entity_id)
        elif entry.domain == "switch" and not entry.hidden_by and entry.entity_category is None:
            out["switches"].append(entry.entity_id)      # hidden = shown as a light; category = LED, child lock…
        elif entry.domain == "binary_sensor" and dclass in OCCUPANCY_CLASSES:
            out["occupancy"].append(entry.entity_id)
        elif entry.domain == "sensor" and dclass == "illuminance":
            out["illuminance"].append(entry.entity_id)
    return {k: sorted(v) for k, v in out.items()}


def zone_entities(hass: HomeAssistant, areas: list[str], lights=None, occupancy=None,
                  illuminance=None) -> dict[str, list[str]]:
    """The room's entities: what its areas hold, adjusted by its own `lights` / `occupancy` /
    `illuminance` (see apply_spec)."""
    found = area_entities(hass, areas)
    return {"lights": sorted(apply_spec(found["lights"], lights)),
            "occupancy": sorted(apply_spec(found["occupancy"], occupancy)),
            "illuminance": sorted(apply_spec(found["illuminance"], illuminance))}


def watched_lights(hass: HomeAssistant, lights: list[str]) -> list[str]:
    """The lights to watch for hand changes: a light group is left out when all its members are
    watched anyway (a member changed by hand would otherwise count twice)."""
    have = set(lights)
    out = []
    for eid in lights:
        st = hass.states.get(eid)
        members = st.attributes.get("entity_id") if st is not None else None
        if isinstance(members, list) and members and set(members) <= have:
            continue
        out.append(eid)
    return out
