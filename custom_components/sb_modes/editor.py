"""The editor's backend: websocket commands for the sidebar page (admin only).

  sb_modes/schema                         the form definitions (behaviours, params, labels)
  sb_modes/check     {config}             errors / warnings, each with the path of its field
  sb_modes/diff      {config?}            the saved table → this one (or the draft), in words
  sb_modes/draft/get                      the shared draft, what it changes, whether it is stale
  sb_modes/draft/set {config}             store the draft; returns its check and changes
  sb_modes/draft/discard
  sb_modes/save      {config?, force?}    save it (or the draft) as the table; keeps a version
  sb_modes/versions                       the last VERSIONS_KEPT versions (no tables)
  sb_modes/revert    {version}            save that version's table again (a new version)
  sb_modes/try       {zone, targets, minutes?}   set lights now and hold the zone
  sb_modes/preview   {config?, days?}     the coming days' mode changes under that schedule

One draft for the house, kept in HA (.storage/sb_modes.draft) so it follows the person between
devices. A draft remembers the version it started from; saving after someone else saved is
refused unless forced, and Review shows it.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import changes
from .activity import short_name
from .const import DOMAIN
from datetime import timedelta

from .model import ALL_MODES, BEHAVIOUR_INFO, BEHAVIOURS, DAYS, NEEDS, day_type_of, occurrences, rule_day_sets, scheduled_mode
from .runtime import Runtime, state_lookup
from .validate import Issues, _check_param

VERSIONS_KEPT = 20


class TableError(Exception):
    """A save that cannot go ahead (errors in the table, or it changed since the draft began)."""


def _runtime(hass: HomeAssistant) -> Runtime | None:
    entry = next(iter(hass.config_entries.async_entries(DOMAIN)), None)
    if entry is None or entry.state is not ConfigEntryState.LOADED:
        return None
    return entry.runtime_data


def _stores(hass: HomeAssistant) -> tuple[Store, Store]:
    data = hass.data.setdefault(DOMAIN, {})
    if "editor_stores" not in data:
        data["editor_stores"] = (Store(hass, 1, f"{DOMAIN}.versions"), Store(hass, 1, f"{DOMAIN}.draft"))
    return data["editor_stores"]


def _name_of(hass: HomeAssistant):
    def name_of(eid: str) -> str:
        st = hass.states.get(eid)
        return st.name if st and st.name else eid
    return name_of


async def _versions(hass: HomeAssistant) -> dict:
    return await _stores(hass)[0].async_load() or {"versions": []}


async def current_version(hass: HomeAssistant) -> int:
    vs = (await _versions(hass))["versions"]
    return vs[-1]["version"] if vs else 0


async def async_save_table(hass: HomeAssistant, rt: Runtime, config: dict, user: str | None = None,
                           note: str | None = None, base_version: int | None = None, force: bool = False) -> dict:
    """Validate, save and version a table, then reload SB Modes. Everything that saves the table
    comes through here: the editor, revert and the sb_modes.save_config action."""
    errors, warnings = rt.validate(config)
    if errors:
        raise TableError("Not saved: " + " ".join(errors))
    store, draft_store = _stores(hass)
    data = await _versions(hass)
    vs = data["versions"]
    current = vs[-1]["version"] if vs else 0
    if base_version is not None and base_version != current and not force:
        raise TableError(f"The table was saved elsewhere since this draft began (version {base_version} → {current}).")
    lines = changes.describe(rt.config, config, _name_of(hass))
    now = dt_util.utcnow().isoformat()
    if not vs:                       # the table as it was before versions were kept, so it can be restored
        vs.append({"version": 1, "saved_at": now, "by": None, "note": "as it was before the editor", "changes": [],
                   "config": rt.config})
    version = vs[-1]["version"] + 1
    vs.append({"version": version, "saved_at": now, "by": user, "note": note, "changes": lines, "config": config})
    del vs[:-VERSIONS_KEPT]
    await store.async_save(data)
    saved_warnings = await rt.async_save(config)
    rt.log("table", None, f"Table saved (version {version}{', ' + note if note else ''}): "
           f"{len(lines)} change{'' if len(lines) == 1 else 's'}" + (f" by {user}" if user else ""))
    draft = await draft_store.async_load()
    if draft and draft.get("config") == config:
        await draft_store.async_save({})              # saved → no draft (an empty one reads as none)
    hass.config_entries.async_schedule_reload(rt.entry.entry_id)
    return {"version": version, "changes": lines, "warnings": list(saved_warnings)}


@callback
def async_register(hass: HomeAssistant) -> None:
    for handler in (ws_schema, ws_check, ws_diff, ws_draft_get, ws_draft_set, ws_draft_discard,
                    ws_save, ws_versions, ws_revert, ws_try, ws_preview):
        websocket_api.async_register_command(hass, handler)


def _need_rt(hass, connection, msg) -> Runtime | None:
    rt = _runtime(hass)
    if rt is None:
        connection.send_error(msg["id"], "not_loaded", "SB Modes is not set up.")
    return rt


def _user(connection) -> str | None:
    return getattr(connection.user, "name", None)


def _check(rt: Runtime, config: Any) -> dict:
    errors, warnings = rt.validate(config)
    as_list = lambda xs: xs.detailed() if isinstance(xs, Issues) else [{"path": [], "message": m} for m in xs]  # noqa: E731
    return {"errors": as_list(errors), "warnings": as_list(warnings)}


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "sb_modes/schema"})
@callback
def ws_schema(hass, connection, msg) -> None:
    connection.send_result(msg["id"], {
        "behaviours": {name: {**BEHAVIOUR_INFO.get(name, {"label": name}), "params": params, "needs": list(NEEDS.get(name, ()))}
                       for name, params in BEHAVIOURS.items()},
        "all_modes": ALL_MODES,
        "days": DAYS,
        "day_sets": rt.day_set_choices() if (rt := _runtime(hass)) else [],
        "target_fields": {
            "state": {"type": "select", "options": ["on", "off"], "label": "On / off"},
            "brightness_pct": {"type": "number", "min": 0, "max": 100, "unit": "%", "label": "Brightness"},
            "min_brightness_pct": {"type": "number", "min": 0, "max": 100, "unit": "%", "label": "At least",
                                   "help": "On, and at least this bright; a brighter light is left alone."},
            "color_temp_kelvin": {"type": "number", "min": 1500, "max": 9000, "unit": "K", "label": "Colour temperature"},
            "transition": {"type": "number", "min": 0, "max": 60, "unit": "s", "label": "Fade"},
            "value": {"type": "number", "label": "Value", "help": "For number entities."},
        },
    })


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "sb_modes/check", vol.Required("config"): dict})
@callback
def ws_check(hass, connection, msg) -> None:
    if rt := _need_rt(hass, connection, msg):
        connection.send_result(msg["id"], _check(rt, msg["config"]))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "sb_modes/diff", vol.Optional("config"): dict})
@websocket_api.async_response
async def ws_diff(hass, connection, msg) -> None:
    if not (rt := _need_rt(hass, connection, msg)):
        return
    config = msg.get("config")
    if config is None:
        draft = await _stores(hass)[1].async_load()
        config = (draft or {}).get("config") or rt.config
    connection.send_result(msg["id"], {"changes": changes.describe(rt.config, config, _name_of(hass))})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "sb_modes/draft/get"})
@websocket_api.async_response
async def ws_draft_get(hass, connection, msg) -> None:
    if not (rt := _need_rt(hass, connection, msg)):
        return
    draft = await _stores(hass)[1].async_load()
    current = await current_version(hass)
    if not draft or "config" not in draft:
        connection.send_result(msg["id"], {"draft": None, "saved": rt.config, "version": current})
        return
    connection.send_result(msg["id"], {
        "draft": draft["config"], "saved": rt.config, "version": current,
        "base_version": draft.get("base_version"), "stale": draft.get("base_version") != current,
        "updated_at": draft.get("updated_at"), "by": draft.get("by"),
        "changes": changes.describe(rt.config, draft["config"], _name_of(hass)),
        **_check(rt, draft["config"]),
    })


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "sb_modes/draft/set", vol.Required("config"): dict})
@websocket_api.async_response
async def ws_draft_set(hass, connection, msg) -> None:
    if not (rt := _need_rt(hass, connection, msg)):
        return
    store = _stores(hass)[1]
    if msg["config"] == rt.config:
        # nothing changed (a form reporting its first value on load, or an edit undone): keep no
        # draft — a stored copy of the table goes stale when it is saved elsewhere, and saving
        # that copy later would undo the newer table (2026-10-21)
        await store.async_save({})
        connection.send_result(msg["id"], {"base_version": await current_version(hass), "changes": [], "same": True,
                                           **_check(rt, msg["config"])})
        return
    old = await store.async_load() or {}
    base = old.get("base_version") if old.get("config") else await current_version(hass)
    await store.async_save({"config": msg["config"], "base_version": base,
                            "updated_at": dt_util.utcnow().isoformat(), "by": _user(connection)})
    connection.send_result(msg["id"], {
        "base_version": base, "changes": changes.describe(rt.config, msg["config"], _name_of(hass)),
        **_check(rt, msg["config"]),
    })


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "sb_modes/draft/discard"})
@websocket_api.async_response
async def ws_draft_discard(hass, connection, msg) -> None:
    await _stores(hass)[1].async_save({})
    connection.send_result(msg["id"], {})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "sb_modes/save", vol.Optional("config"): dict,
                                  vol.Optional("force", default=False): bool, vol.Optional("note"): str})
@websocket_api.async_response
async def ws_save(hass, connection, msg) -> None:
    if not (rt := _need_rt(hass, connection, msg)):
        return
    base = None
    config = msg.get("config")
    if config is None:
        draft = await _stores(hass)[1].async_load()
        if not draft or "config" not in draft:
            connection.send_error(msg["id"], "no_draft", "There is no draft to save.")
            return
        config, base = draft["config"], draft.get("base_version")
    try:
        result = await async_save_table(hass, rt, config, _user(connection), msg.get("note"), base, msg["force"])
    except TableError as err:
        connection.send_error(msg["id"], "not_saved", str(err))
        return
    connection.send_result(msg["id"], result)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "sb_modes/versions"})
@websocket_api.async_response
async def ws_versions(hass, connection, msg) -> None:
    vs = (await _versions(hass))["versions"]
    connection.send_result(msg["id"], {"versions": [{k: v for k, v in x.items() if k != "config"} for x in reversed(vs)]})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "sb_modes/revert", vol.Required("version"): int})
@websocket_api.async_response
async def ws_revert(hass, connection, msg) -> None:
    if not (rt := _need_rt(hass, connection, msg)):
        return
    found = next((x for x in (await _versions(hass))["versions"] if x["version"] == msg["version"]), None)
    if found is None:
        connection.send_error(msg["id"], "not_found", f"There is no version {msg['version']}.")
        return
    try:
        result = await async_save_table(hass, rt, found["config"], _user(connection), f"back to version {msg['version']}")
    except TableError as err:
        connection.send_error(msg["id"], "not_saved", str(err))
        return
    connection.send_result(msg["id"], result)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "sb_modes/try", vol.Required("zone"): str,
                                  vol.Required("targets"): dict,
                                  vol.Optional("minutes", default=15): vol.All(int, vol.Range(min=1, max=240))})
@callback
def ws_try(hass, connection, msg) -> None:
    if not (rt := _need_rt(hass, connection, msg)):
        return
    if msg["zone"] not in [z["id"] for z in rt.zones()]:
        connection.send_error(msg["id"], "not_found", f"“{msg['zone']}” is not a zone.")
        return
    errors, warnings = Issues(), Issues()
    _check_param(msg["targets"], {"type": "targets"}, "Try it", state_lookup(hass), errors, warnings)
    if errors:
        connection.send_error(msg["id"], "invalid", " ".join(errors))
        return
    rt.keeper.hold(msg["zone"], msg["minutes"], by="Try it")
    rt.send(msg["targets"], "Try it", msg["zone"])
    connection.send_result(msg["id"], {"held_minutes": msg["minutes"],
                                       "set": [short_name(hass, e, None) for e in msg["targets"]]})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "sb_modes/preview", vol.Optional("config"): dict,
                                  vol.Optional("days", default=7): vol.All(int, vol.Range(min=1, max=14))})
@websocket_api.async_response
async def ws_preview(hass, connection, msg) -> None:
    """The mode the schedule gives now and each change over the next days, for a table (the
    draft by default) — day sets read fresh from SB Scheduler, so new rules preview too."""
    if not (rt := _need_rt(hass, connection, msg)):
        return
    config = msg.get("config")
    if config is None:
        draft = await _stores(hass)[1].async_load()
        config = (draft or {}).get("config") or rt.config
    now = dt_util.now()
    end = now + timedelta(days=msg["days"])
    found = await rt.async_read_day_sets(rule_day_sets(config), now.date() - timedelta(days=2), end.date() + timedelta(days=1))
    in_set = lambda ds, d: (d in found[ds]) if ds in found else None  # noqa: E731
    sched = scheduled_mode(config, now, in_set, rt.sun)          # (mode, since) or None
    current = sched[0] if isinstance(sched, (tuple, list)) else sched
    out, last = [], current
    for at, mode in occurrences(config, now, end, in_set, rt.sun):
        if mode != last:
            out.append({"at": at.isoformat(), "mode": mode})
            last = mode
    missing = sorted(rule_day_sets(config) - set(found))
    types = {}
    d = now.date()
    while d <= end.date():                                   # each day's type, for the week view
        types[d.isoformat()] = day_type_of(config, d, in_set)
        d += timedelta(days=1)
    connection.send_result(msg["id"], {"now": current, "changes": out, "missing_day_sets": missing, "day_types": types})
