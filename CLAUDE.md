# sb-modes — session notes

| | |
|---|---|
| Repo | github.com/snadboy/sb-modes (private, created 2026-10-17) — local `~/projects/git/sb-modes`, branch `main` |
| Domain | `sb_modes` (MIT), single config entry |
| Design | https://claude.ai/artifact/9GAUvd4N2gctYm88fnMqhs (Claude Docs doc "SB Modes — design") |
| Tests | `python3 tests/test_validate.py` (offline) · `.venv/bin/pytest` (all, inside HA 2026.10.2 via pytest-homeassistant-custom-component; Python 3.14 venv, 670 MB, gitignored) |
| Quick deploy | `tar czf - --exclude=__pycache__ -C custom_components sb_modes \| ssh snadboy@homeassistant "cd /config/custom_components && sudo tar xzf -"` then a FULL HA restart (new integration / Python changes) |

## Why it exists (2026-10-16/17)

User: scenes are clunky, rigid and duplicated; wanted a background mechanism reacting
to mode changes instead. Key decisions, all in the design doc:
- **Single source of truth.** A mode exists only through its default in the table; the
  select's options are generated from it. The user rejected input_select because the
  mode list and the handlers would drift apart.
- **Mode = a behaviour per zone**, not a set of states. Behaviours are a fixed set with
  parameter schemas (`model.BEHAVIOURS`); device quirks live in drivers below them.
- **Zone, not area**, is the unit: Kitchen Aux / Dining Room Aux have no sensors.
- **Precedence: hold, then overlay, then mode.** When an overlay or hold ends, the zone
  is re-resolved for the current mode (no snapshot restore).
- **Lux:** brighten on the raw reading (sunset); dim only with a per-zone calibration
  estimate, else wait for vacancy. FP300 settle ~2 min.
- **SB Modes absorbs SB Background's nightlight role completely** (user, 2026-10-17):
  the three `area_nightlight` instances and that script retire at step 2.
- Other open questions (day vs evening, what changes the mode, override duration, office
  vacant level, TV idle end, morning) are deliberately deferred.

## Plan (from the design doc)

1. ✅ Skeleton: table + validation, mode select, zone sensors, problem sensor + Repairs, fallback. (0.1.0, deployed 2026-10-17 by tar + full restart; entry added via the config flow API)
2. `nightlight_then_dark` + `static` with a light driver; move the 3 overnight zones; retire SB Background instances.
3. Bookkeeping: own-context tracking, manual overrides with expiry (kitchen first).
4. `presence_lux`; evening for one zone, then the rest. Replaces the 8 `Lights - Occupancy` automations.
5. Overlays (TV first) and holds (the "Family Room Light Hold" BILRESA button, currently unavailable).
6. Sidebar panel `/sb-modes` + `custom:sb-modes-card` (SB UI kit, ha-form).
7. Drivers beyond lights (Dreo) when a mode needs them.
8. Delete replaced scenes/automations (list in the design doc's Migration section).

## Design notes (code)

- `model.py`, `validate.py`: pure, no HA imports. Tests load them as a bare package so
  relative imports work without HA.
- The table lives in a Store (`.storage/sb_modes.config`), seeded with four `hands_off`
  modes (morning/day/evening/overnight, fallback day) and no zones.
- Select options are mode **ids**, so renaming a mode never breaks an automation.
- `save_config` validates, saves, then schedules an entry reload (zones ↔ entities);
  the select restores the mode across that reload.
- Problem re-check every 60 s; Repairs after 120 s of errors; cleared on recovery.
- Entity domains a driver controls: `model.DRIVER_DOMAINS` = light, switch.
