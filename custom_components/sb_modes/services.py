"""Services for reading and editing the table until the panel exists.

  sb_modes.get_config                 → {config, errors, warnings} (the saved table)
  sb_modes.check_config {config}      → {errors, warnings} for a table, without saving
  sb_modes.save_config  {config}      → saves a table with no errors and reloads the entry
                                        (zones become entities); refused otherwise
  sb_modes.set_mode {mode, hold}      → a manual mode change; hold = "next" (until the next
                                        scheduled change, default), "paused" (until resumed) or
                                        minutes (then back to the scheduled mode)
  sb_modes.resume_schedule            → end any manual hold; go to the scheduled mode now
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError

from .const import DOMAIN
from .runtime import Runtime

CONFIG_SCHEMA = vol.Schema({vol.Required("config"): dict})
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
        rt = _runtime(hass)
        errors, _ = rt.validate(call.data["config"])
        if errors:
            raise ServiceValidationError("Not saved: " + " ".join(errors))
        warnings = await rt.async_save(call.data["config"])
        hass.config_entries.async_schedule_reload(rt.entry.entry_id)
        return {"warnings": warnings}

    async def set_mode(call: ServiceCall) -> None:
        try:
            _runtime(hass).set_mode(call.data["mode"], call.data["hold"])
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

    async def resume_schedule(call: ServiceCall) -> None:
        _runtime(hass).resume_schedule()

    hass.services.async_register(DOMAIN, "set_mode", set_mode, schema=SET_MODE_SCHEMA)
    hass.services.async_register(DOMAIN, "resume_schedule", resume_schedule)
    hass.services.async_register(DOMAIN, "get_config", get_config, supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, "check_config", check_config, schema=CONFIG_SCHEMA,
                                 supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, "save_config", save_config, schema=CONFIG_SCHEMA,
                                 supports_response=SupportsResponse.OPTIONAL)
