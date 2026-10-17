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

## Editing the table (until the panel exists)

| Action | Does |
|---|---|
| `sb_modes.get_config` | Returns the saved table with its errors and warnings |
| `sb_modes.check_config` `{config}` | Validates a table without saving |
| `sb_modes.save_config` `{config}` | Saves a table with no errors, then reloads (zones become entities) |

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
