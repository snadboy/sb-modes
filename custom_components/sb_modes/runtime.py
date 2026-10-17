"""The running state of SB Modes: the saved table, the active mode, and its problems.

One Runtime per (single) config entry. The table lives in a Store, not in the entry's
options, because it is a structured document edited as a whole and saved only when it
validates. Entities subscribe with add_listener() and re-render on any change.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.storage import Store

from .const import ALERT_GRACE_SECONDS, CHECK_SECONDS, DOMAIN, EVENT_MODE_CHANGED, ISSUE_ID, STORE_KEY, STORE_VERSION
from .model import default_config, find, mode_ids, resolve
from .validate import check_config

_LOGGER = logging.getLogger(__name__)


def state_lookup(hass: HomeAssistant) -> Callable[[str], str | None]:
    """state_of() for validation: a registered entity with no state yet (its integration
    still loading) reads 'unavailable', not missing; a disabled one counts as missing."""
    reg = er.async_get(hass)

    def state_of(eid: str) -> str | None:
        if (st := hass.states.get(eid)) is not None:
            return st.state
        if (e := reg.async_get(eid)) is not None:
            return "unavailable" if not e.disabled_by else None
        return None

    return state_of


def area_lookup(hass: HomeAssistant) -> Callable[[str], bool]:
    reg = ar.async_get(hass)
    return lambda area_id: isinstance(area_id, str) and reg.async_get_area(area_id) is not None


class Runtime:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.store: Store = Store(hass, STORE_VERSION, STORE_KEY)
        self.config: dict[str, Any] = default_config()
        self.mode: str | None = None
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._grace: CALLBACK_TYPE | None = None
        self._issue_raised = False

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def async_load(self) -> None:
        stored = await self.store.async_load()
        if stored is None:
            await self.store.async_save(self.config)     # seed: four hands_off modes, no zones
        else:
            self.config = stored

    @callback
    def start(self) -> None:
        self.check()
        self._unsubs.append(async_track_time_interval(
            self.hass, self._tick, timedelta(seconds=CHECK_SECONDS), cancel_on_shutdown=True))

    @callback
    def _tick(self, _now) -> None:
        self.check()

    @callback
    def stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._cancel_grace()

    # ── the table ────────────────────────────────────────────────────────────
    def validate(self, config: Any) -> tuple[list[str], list[str]]:
        return check_config(config, state_lookup(self.hass), area_lookup(self.hass))

    async def async_save(self, config: dict[str, Any]) -> list[str]:
        """Save a table that has no errors (the caller checked); returns its warnings.
        The entry reloads afterwards, so zones added or removed become entities."""
        errors, warnings = self.validate(config)
        if errors:
            raise ValueError(" ".join(errors))
        await self.store.async_save(config)
        self.config = config
        return warnings

    @property
    def mode_ids(self) -> list[str]:
        return mode_ids(self.config)

    @property
    def fallback(self) -> str | None:
        fb = self.config.get("fallback_mode")
        if fb in self.mode_ids:
            return fb
        return self.mode_ids[0] if self.mode_ids else None

    def mode_name(self, mode_id: str | None) -> str | None:
        m = find(self.config.get("modes"), mode_id) if mode_id else None
        return (m.get("name") or mode_id) if m else None

    def zones(self) -> list[dict]:
        return [z for z in self.config.get("zones") or [] if isinstance(z, dict) and z.get("id")]

    def zone_state(self, zone_id: str) -> dict | None:
        return resolve(self.config, self.mode, zone_id) if self.mode else None

    # ── the active mode ──────────────────────────────────────────────────────
    @callback
    def restore_mode(self, last: str | None) -> None:
        """At startup: the last mode if it still exists, else the fallback."""
        self.mode = last if last in self.mode_ids else self.fallback
        self._notify()

    @callback
    def set_mode(self, mode_id: str) -> None:
        if mode_id not in self.mode_ids:
            raise ValueError(f"“{mode_id}” is not a defined mode.")
        if mode_id == self.mode:
            return
        old, self.mode = self.mode, mode_id
        self.hass.bus.async_fire(EVENT_MODE_CHANGED, {"from": old, "to": mode_id})
        self._notify()

    # ── problems ─────────────────────────────────────────────────────────────
    @callback
    def check(self) -> None:
        errors, warnings = self.validate(self.config)
        changed = (errors, warnings) != (self.errors, self.warnings)
        self.errors, self.warnings = errors, warnings
        if errors:
            if self._issue_raised:
                self._raise_issue()                 # keep the text current
            elif self._grace is None:
                self._grace = async_call_later(
                    self.hass, ALERT_GRACE_SECONDS, HassJob(self._grace_over, cancel_on_shutdown=True))
        else:
            self._cancel_grace()
            if self._issue_raised:
                ir.async_delete_issue(self.hass, DOMAIN, ISSUE_ID)
                self._issue_raised = False
        if changed:
            self._notify()

    @callback
    def _grace_over(self, _now=None) -> None:
        self._grace = None
        if self.errors:
            self._raise_issue()

    @callback
    def _raise_issue(self) -> None:
        self._issue_raised = True
        ir.async_create_issue(
            self.hass, DOMAIN, ISSUE_ID, is_fixable=False, is_persistent=False,
            severity=ir.IssueSeverity.WARNING, translation_key=ISSUE_ID,
            translation_placeholders={"errors": "\n".join(f"- {e}" for e in self.errors)},
        )

    @callback
    def _cancel_grace(self) -> None:
        if self._grace is not None:
            self._grace()
            self._grace = None

    # ── listeners ────────────────────────────────────────────────────────────
    @callback
    def add_listener(self, fn: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(fn)
        return lambda: self._listeners.remove(fn)

    @callback
    def _notify(self) -> None:
        for fn in list(self._listeners):
            fn()

