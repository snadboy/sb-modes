# sb-modes — session notes

| | |
|---|---|
| Repo | github.com/snadboy/sb-modes (private, created 2026-10-17) — local `~/projects/git/sb-modes`, branch `main` |
| Domain | `sb_modes` (MIT), single config entry |
| Design | https://claude.ai/artifact/9GAUvd4N2gctYm88fnMqhs (Claude Docs doc "SB Modes — design") |
| Tests | `python3 tests/test_validate.py` (offline) · `.venv/bin/pytest` (all, inside HA 2026.10.2 via pytest-homeassistant-custom-component; Python 3.14 venv, 670 MB, gitignored) |
| Frontend | `frontend/sb-modes.js` = card + panel, one module (add_extra_js_url + panel_custom), SB UI Kit row style; bump `VERSION` in the JS AND manifest version together (`?v=` cache-buster). Headless check: a Playwright script injecting `hassTokens` (long-lived token) via add_init_script, then screenshot `/sb-modes`. To compare cards, create them on the `/sb-modes` page (document.createElement works there); on a dashboard page the script's evaluate saw no custom elements at all (even sb-background-card) and loadCardHelpers gave error cards |
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
2. ✅ `nightlight_then_dark` + `static` with a light driver; move the 3 overnight zones; retire SB Background instances. (0.2.0, live 2026-10-17 09:30 CDT; first overnight 23:00 that night)
3. Bookkeeping: own-context tracking, manual overrides with expiry (kitchen first).
4. `presence_lux`; evening for one zone, then the rest. Replaces the 8 `Lights - Occupancy` automations.
5. Overlays (TV first) and holds (the "Family Room Light Hold" BILRESA button, currently unavailable).
6. Sidebar panel `/sb-modes` + `custom:sb-modes-card` (SB UI kit, ha-form). **Started early (0.3.x, 2026-10-17): view-only panel + card**; 0.3.4 matches the other SB cards' header (title from the card editor · "Day · 4 modes · 3 zones" · ⋮ Open the SB Modes page · collapse chevron, remembered per browser); a "+ Add" comes with the editor — mode picker, "since", problems, a row per zone (behaviour chip + live status), tap a zone for its areas / nightlight / occupancy / lights. Editor still to come.

**User (2026-10-17): the sidebar page should grow and develop with every step**, not wait for
step 6 — each new behaviour/overlay/hold gets its view (and later controls) on the panel as it lands.
7. Drivers beyond lights (Dreo) when a mode needs them.
8. Delete replaced scenes/automations (list in the design doc's Migration section).

## Live state (2026-10-17)

- Table: modes morning/day/evening/overnight (overnight default `nightlight_then_dark`,
  delay 10; others `hands_off`), fallback day. Zones kitchen (kitchen + kitchen_aux;
  nightlight sink 10% + cabinet on), dining_room (+ dining_room_aux; sideboard on),
  living_room (plants lamp 40% since 2026-10-18; 20% too dim on Caseta). Copy of the saved JSON: `sb_modes.get_config`.
- **Mode source (interim):** `automation.sb_modes_schedule_interim` — 06:00 morning,
  09:00 day, 23:00 overnight. No evening trigger. No HA-start recompute (the select restores).
- **SB Background nightlight instances disabled, not deleted** (switch.*_overnight_enabled
  off). Rollback: turn those three switches on and disable the schedule automation.
  Delete them, `script.area_nightlight`, and the six overnight scenes after a week.
- Behaviour change vs the old scenes: "dark" now turns off EVERY light in the zone, so the
  dining chandelier and the living room reading lamp also go off after 10 min vacant
  overnight (the old dark scenes left them alone).

## Live test 2026-10-18 06:12 (Overnight forced from Morning)

- Dining Room: sideboard nightlight on entry ✓, dark after 10 min ✓.
- Living Room: plants lamp turned on at 06:13:27 with brightness_pct 20 (proved from the
  MariaDB call_service record — `docker exec app_core_mariadb mariadb -u root homeassistant`),
  but 20% on that Lutron Caseta lamp is too subtle → nightlight raised to **40%**.
  Note: this lamp's live state carries brightness, but the recorder/history keep NO brightness
  for it — history attributes are not evidence of a level.
- Kitchen: **conflict found** — when SB Modes turns the ceiling off, `automation.ambience_kitchen`
  (low_bright) re-applies the dim scene 1 s later, undoing dark. Fixed 2026-10-18 by a `not`
  condition on that branch: skip while `select.sb_modes_mode` is overnight. Kitchen nightlight
  on arrival still untested live (lights were dim, so correctly "occupied").
- Restored afterwards: mode Morning + scene.early_morning_scene.

## Schedule inside SB Modes (0.4.0, 2026-10-18)

User: weekends skip Morning, start Day at 09:00, Overnight at 00:30 on Fri/Sat nights; manual
mode changes take a duration (15/30/60 min, until next scheduled change, until resumed).
Live rules: Mon–Fri 06:00 morning · Mon–Fri 09:00 day · Sun–Thu 23:00 overnight ·
Sat–Sun 00:30 overnight · Sat–Sun 09:00 day. `automation.sb_modes_schedule_interim` DISABLED
2026-10-18 (delete after a week). Manual light changes (switch/app) will default to: until the
zone is vacant for its delay, or the next mode change (user concurred) — step 3.
Note "Lights - Whole House" (06:00 Early Morning scene, tod_morning) is still weekend-blind; not SB Modes.

## HA 2026.10 card registration (2026-10-18)

On dashboard pages the frontend replaces `window.customElements` with a scoped-registry
polyfill (constructor name "A", non-native define) AFTER add_extra_js_url modules have run,
so their definitions are lost → "Configuration error" cards. Panel pages are fine (the panel
loads module_url itself). Fix in 0.3.5: `ensureDefined()` re-defines a fresh subclass of each
element if missing — every 500 ms for 60 s after load, and on `location-changed`; HA's error
card rebuilds once the tag exists. **SB Background's card has the same bug** (its module uses
add_extra_js_url too) — fix it the same way in that repo. Lovelace-resource cards (SB Watch,
Scheduler) are unaffected.

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
