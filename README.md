# SB Modes

A Home Assistant integration: the house is in a **mode** (morning, day, evening,
overnight…), and each **zone** runs one validated **behaviour** for that mode.
It replaces hand-built scenes and per-room automations with one table that cannot
leave a mode, zone or entity unhandled.

Design: [SB Modes — design](https://claude.ai/artifact/9GAUvd4N2gctYm88fnMqhs)

## Status: step 2 of 8 (first behaviours)

- The **mode table**: modes (each with a default behaviour), zones (an area plus its
  aux areas) and per-zone overrides. Validated on save and re-checked every minute.
- **`select.sb_modes_mode`**: the active mode. Its options are the table's mode ids,
  so it cannot hold a mode the table does not define. Restored across restarts; falls
  back to the table's `fallback_mode` when the active mode is deleted.
- **`sensor.sb_modes_<zone>`**: what the zone runs in the active mode (state), whether
  that is the mode's default or the zone's override, and its parameters.
- **`binary_sensor.sb_modes_problem`** and a **Repairs** issue (after 2 minutes) when
  the saved table stops validating, for example because an entity or area was deleted.
- Event **`sb_modes_mode_changed`** `{from, to}`.

Behaviours that act (0.2.0):

- **`nightlight_then_dark`**: someone detected while every zone light is off → the
  `nightlight` targets; every occupancy sensor off for `delay` minutes (default 10) →
  every light in the zone off, once per vacancy. Entering the mode with the zone already
  vacant that long goes dark at once. An unavailable sensor counts as not vacant.
- **`static`**: sets its `targets` once on entering the mode.
- `hands_off` does nothing; `presence_lux` is validated but not built yet.

A zone's lights and sensors come from its areas: `light.*` entities, `binary_sensor.*`
with device class occupancy or presence, `sensor.*` with device class illuminance. A zone
whose behaviour needs one of these and has none is refused. The driver only sends what
differs from the current state, and every call carries a context SB Modes remembers.

## The schedule and manual changes (0.4.0)

The table's `schedule` is a list of rules `{days: [mon…sun], at: "HH:MM", mode}`; SB Modes
switches itself at each one (local time; a 00:30 rule on `sat` starts Friday night's mode).
A manual change carries a **hold**:

- `next` (default; also any plain `select.select_option`) — until the next scheduled change
- minutes — for that long, then back to whatever the schedule says by then
- `paused` — the schedule is ignored until `sb_modes.resume_schedule`

At startup SB Modes catches up with any scheduled change it missed, unless a hold stands.
The mode-changed event carries `source`: manual, schedule, resume, hold_ended or startup.

## Lights changed by hand, and zone holds (0.5.0)

- **Changed by hand**: a zone light changed by a person (app, dashboard, voice) or at the
  device (wall switch, Lutron app) is left alone by every behaviour until the zone has been
  empty for its delay (`delay` / `off_delay`, else 10 min) or the mode changes. Changes by
  SB Modes itself, by other automations, and a device echoing SB Modes' own command within
  15 s do not count. Shown on the zone's row; `overrides` attribute on its sensor.
- **Hold a zone** (row ⋮, or `sb_modes.hold_zone {zone, hold}`): its behaviour sends nothing
  for 15/30/60 min (`hold: minutes`), until the next mode change (`next`, default) or until
  released (`paused`). **Release** (`sb_modes.release_zone {zone}`) ends the hold and the
  hand changes; the zone's behaviour starts again for the current mode.
- Both are saved (`.storage/sb_modes.state`) and survive restarts and table reloads.

## Reporting (0.9.0)

| Entity | State | Attributes |
|---|---|---|
| `select.sb_modes_mode` | the current mode | names, schedule, next change, manual hold, previous mode |
| `sensor.sb_modes_schedule` | `on_schedule` · `held_until_next` · `held_timed` · `paused` · `no_schedule` | scheduled mode now, next mode / at, previous mode, hold since / until |
| `sensor.sb_modes_deviations` | how many things differ from automatic (0 = none) | `deviations`: list of `{type: mode_hold \| zone_hold \| changed_by_hand, zone, entity, hold, by, since, until}`; counts per type |
| `sensor.sb_modes_<zone>` | the zone's behaviour now | `control`: `auto` · `held` · `by_hand`; `hold`, `overrides`, status, lights, sensors, by_mode |

Event `sb_modes_mode_changed` `{from, to, source}`.

## Editing the table (until the panel exists)

| Action | Does |
|---|---|
| `sb_modes.get_config` | Returns the saved table with its errors and warnings |
| `sb_modes.check_config` `{config}` | Validates a table without saving |
| `sb_modes.save_config` `{config}` | Saves a table with no errors, then reloads (zones become entities) |
| `sb_modes.set_mode` `{mode, hold}` | Manual mode change; hold `next` (default), `paused`, or minutes |
| `sb_modes.resume_schedule` | End any hold; switch to the scheduled mode now |
| `sb_modes.hold_zone` `{zone, hold}` | Hold a zone by hand: `next` (default), `paused`, or minutes |
| `sb_modes.release_zone` `{zone}` | End a zone's hold and its lights changed by hand |

```yaml
fallback_mode: day
modes:
  - id: overnight
    name: Overnight
    default: {behaviour: nightlight_then_dark, params: {delay: 10}}
  # …
zones:
  - id: kitchen
    name: Kitchen
    areas: [kitchen, kitchen_aux]
    overrides:
      overnight:
        behaviour: nightlight_then_dark
        params:
          nightlight:
            light.kitchen_aux_sink: {brightness_pct: 10}
            light.kitchen_aux_cabinet: {state: "on"}
```

Behaviours: `hands_off`, `static` (`targets`), `nightlight_then_dark` (`nightlight`
targets, `delay`), `presence_lux` (`target_lux`, `color_temp_kelvin`, `off_delay`).
A target is `{state: on|off, brightness_pct, color_temp_kelvin}`; entities in targets
must be `light` or `switch` (the domains a driver controls).

## Tests

```bash
python3 tests/test_validate.py                       # pure checks, no Home Assistant needed
uv venv .venv --python 3.14
uv pip install --python .venv/bin/python pytest-homeassistant-custom-component
.venv/bin/pytest                                     # everything, inside a real HA core
```
