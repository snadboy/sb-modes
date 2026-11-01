"""Services for reading and editing the table until the panel exists.

  sb_modes.get_config                 → {config, errors, warnings} (the saved table)
  sb_modes.check_config {config}      → {errors, warnings} for a table, without saving
  sb_modes.save_config  {config}      → saves a table with no errors and reloads the entry
                                        (zones become entities); refused otherwise
  sb_modes.set_mode {mode, hold}      → a manual mode change; hold = "next" (until the next
                                        scheduled change, default), "paused" (until resumed) or
                                        minutes (then back to the scheduled mode)
  sb_modes.resume_schedule            → end any manual hold; go to the scheduled mode now
  sb_modes.hold_zone {zone, hold}     → hold a zone by hand (its behaviour sends nothing): hold =
                                        "next" (until the next mode change, default), "paused"
                                        (until released) or minutes
  sb_modes.release_zone {zone}        → end the zone's hold and lights changed by hand
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError

from .const import DOMAIN
from .runtime import Runtime

CONFIG_SCHEMA = vol.Schema({vol.Required("config"): dict})
HOLD_ZONE_SCHEMA = vol.Schema({
    vol.Required("zone"): str,
    vol.Optional("hold", default="next"): vol.Any(vol.In(["next", "paused"]), vol.All(vol.Coerce(int), vol.Range(min=1, max=1440))),
})
# the card's room dialog: "keep" leaves a hold in place (else holds until the next mode)
LIGHTS_HOLD = vol.Any(vol.In(["next", "paused", "keep"]), vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)))
SET_LEVEL_SCHEMA = HOLD_ZONE_SCHEMA.extend({
    vol.Optional("hold", default="next"): LIGHTS_HOLD,
    vol.Required("brightness_pct"): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
})
SWITCH_LIGHTS_SCHEMA = HOLD_ZONE_SCHEMA.extend({vol.Optional("hold", default="next"): LIGHTS_HOLD,
                                               vol.Required("state"): vol.In(["on", "off"])})
SET_LIGHTS_SCHEMA = HOLD_ZONE_SCHEMA.extend({
    vol.Optional("hold", default="next"): LIGHTS_HOLD,
    vol.Required("lights"): {str: vol.Any({vol.Required("state"): vol.In(["on", "off"])},
                                          {vol.Required("brightness_pct"): vol.All(vol.Coerce(int), vol.Range(min=0, max=100))})},
})
SET_MODE_SCHEMA = vol.Schema({
    vol.Required("mode"): str,
    vol.Optional("hold", default="next"): vol.Any(vol.In(["next", "paused"]), vol.All(vol.Coerce(int), vol.Range(min=1, max=1440))),
})


def _runtime(hass: HomeAssistant) -> Runtime:
    entry = next(iter(hass.config_entries.async_entries(DOMAIN)), None)
    if entry is None or entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError("SB Modes is not set up.")
    return entry.runtime_data


@callback
def async_register(hass: HomeAssistant) -> None:
    async def get_config(call: ServiceCall) -> ServiceResponse:
        rt = _runtime(hass)
        return {"config": rt.config, "errors": rt.errors, "warnings": rt.warnings}

    async def check_config(call: ServiceCall) -> ServiceResponse:
        errors, warnings = _runtime(hass).validate(call.data["config"])
        return {"errors": errors, "warnings": warnings}

    async def save_config(call: ServiceCall) -> ServiceResponse:
        from .editor import TableError, async_save_table   # the editor's save: validated and versioned
        rt = _runtime(hass)
        user = await hass.auth.async_get_user(call.context.user_id) if call.context.user_id else None
        try:
            result = await async_save_table(hass, rt, call.data["config"], user.name if user else None, "sb_modes.save_config")
        except TableError as err:
            raise ServiceValidationError(str(err)) from err
        return {"warnings": result["warnings"], "version": result["version"]}

    async def set_mode(call: ServiceCall) -> None:
        try:
            _runtime(hass).set_mode(call.data["mode"], call.data["hold"])
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

    async def resume_schedule(call: ServiceCall) -> None:
        _runtime(hass).resume_schedule()

    def _zone(rt: Runtime, zone: str) -> str:
        if zone not in [z["id"] for z in rt.zones()]:
            raise ServiceValidationError(f"“{zone}” is not a zone.")
        return zone

    async def hold_zone(call: ServiceCall) -> None:
        rt = _runtime(hass)
        try:
            rt.keeper.hold(_zone(rt, call.data["zone"]), call.data["hold"])
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

    async def set_level(call: ServiceCall) -> None:
        rt = _runtime(hass)
        try:
            rt.set_level(_zone(rt, call.data["zone"]), call.data["brightness_pct"], call.data["hold"])
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

    async def switch_lights(call: ServiceCall) -> None:
        rt = _runtime(hass)
        try:
            rt.switch_lights(_zone(rt, call.data["zone"]), call.data["state"] == "on", call.data["hold"])
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

    async def set_lights(call: ServiceCall) -> None:
        rt = _runtime(hass)
        try:
            rt.set_lights(_zone(rt, call.data["zone"]), call.data["lights"], call.data["hold"])
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

    async def release_zone(call: ServiceCall) -> None:
        rt = _runtime(hass)
        rt.keeper.release(_zone(rt, call.data["zone"]))

    hass.services.async_register(DOMAIN, "hold_zone", hold_zone, schema=HOLD_ZONE_SCHEMA)
    hass.services.async_register(DOMAIN, "set_level", set_level, schema=SET_LEVEL_SCHEMA)
    hass.services.async_register(DOMAIN, "switch_lights", switch_lights, schema=SWITCH_LIGHTS_SCHEMA)
    hass.services.async_register(DOMAIN, "set_lights", set_lights, schema=SET_LIGHTS_SCHEMA)
    hass.services.async_register(DOMAIN, "release_zone", release_zone, schema=vol.Schema({vol.Required("zone"): str}))
    hass.services.async_register(DOMAIN, "set_mode", set_mode, schema=SET_MODE_SCHEMA)
    hass.services.async_register(DOMAIN, "resume_schedule", resume_schedule)
    hass.services.async_register(DOMAIN, "get_config", get_config, supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, "check_config", check_config, schema=CONFIG_SCHEMA,
                                 supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, "save_config", save_config, schema=CONFIG_SCHEMA,
                                 supports_response=SupportsResponse.OPTIONAL)
