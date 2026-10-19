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
3. ✅ Bookkeeping (0.5.0, 2026-10-18): manual changes (`manual.py` classify: ours / user / automation parent / device echo within 15 s) → per-light overrides released on vacancy-for-delay or mode change; zone holds (15/30/60, next, paused) + release; `bookkeeping.Keeper`, `.storage/sb_modes.state`, handed over in hass.data across reloads (delayed save would otherwise lose it). Overrides are only tracked for zones whose behaviour acts (not hands_off / held).
4. **4a ✅ (0.6.0, live 2026-10-18 09:48 CDT)**: `vacancy_off` + `occupied_vacant` + zone `"*"` (all modes). Zones added: family_room (area basement), office, bedroom (master_bedroom), upstairs_hallway, laundry_room (on_when_occupied), basement_bath (downstairs_bath); kitchen/dining got `"*"` vacancy_off (ceiling 180 s / chandelier 300 s, max 60). Exact copies of the old inputs (blueprint max_on default 60 where unset). **Disabled, not deleted**: automation.light_off_occupancy, .lights_occupancy_dining_room, .family_room_lights_occupancy, .bedroom_light_off_occupancy, .upstairs_hallway_light_off_occupancy, .lights_occupancy_laundry_room, .lights_off_occupancy_basement_bath, .office_occupancy_scene_toggle. automation.upstairs_bath_lights_off_occupancy DELETED (user, was disabled since June).
   **4b ✅ (0.7.0, 2026-10-18)**: `presence_lux` pilot, Living Room, Evening only: occupied & < 25 lx → plants/picture/table/window 82% (not the group: it holds the Christmas tree); empty 15 min → plants 40%, others off (user: nightlight, not all off). Lux only turns lights up. Reading lamp (user 2026-10-18): SB Modes NEVER turns it on; it goes off after the room is empty 15 min in every mode (`"*"` vacancy_off for Morning/Day, in Evening's vacant set; Overnight's dark covers it at 10 min).
   **0.8.0 (2026-10-18)**: schedule rules take `day_set` (SB Scheduler calendars, `eve`/`not`) and `sunrise|sunset±HH:MM`. Live rules: workday 06:00 morning · every day 09:00 day · every day sunset−01:00 evening (user: evening follows sunset) · eve of workday 23:00 overnight · not-workday 00:30 overnight. Holidays / days off / Anderson #do behave like weekends. Zones may list `lights` (bedroom = floor lamp; new `bedside` zone = the two bulbs, `wake_sequence` after overnight +30 min 1%, +40 min 4%). SB Scheduler `switch.bedside_lamps_wakeup` + `switch.sb_bedside_lamps_get_up` DISABLED (delete after a week).
   (old note) 4b was: `presence_lux` pilot in the Living Room in Evening. Lux history (sensor barely sees the lamps: group 82% adds ~4–5 lux, reading lamp ~20; dark floor 8; lamps-off Mon 12 Oct: 17:00 46, 17:30 24, 18:00 18, sunset ~14). Suggested 25 lux; Evening start 16:00 daily; off when empty ~15 min — awaiting user answers.
   **Open (user 2026-10-18)**: SB Scheduler "Bedside Lamps - Wakeup" (06:30, bri 3) and "Get Up" (06:40, bri 10) run on the workday day set; user wants them in SB Modes with a weekend version in the 09:00 mode — needs a wake-sequence behaviour anchored to the end of Overnight, and two behaviours in one zone (bedroom floor lamp vacancy_off + bedside wake).
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
Live rules: Mon–Fri 06:00 morning · every day 09:00 day · Sun–Thu 23:00 overnight ·
Sat–Sun 00:30 overnight. `automation.sb_modes_schedule_interim` DISABLED
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

## 2026-10-19 changes (0.11.0)

- Office `vacant_after_minutes` 0 → 2 (LWR02 drops out for seconds while occupied; Office Dim
  flickered 11× 06:29–07:58).
- `on_enter` (zone: {mode: targets}, once per real mode change). Morning: kitchen aux on
  (cabinet on, recessed/sink 70%), dining sideboard on, living plants 40%. **automation.lights_morning
  ("Lights - Whole House") DISABLED** (its overheads were undone by off-when-empty within minutes;
  its overnight half dead since Aug). Delete after a week.
- Schedule: + not-workday 08:00 Morning (user). Bedside wake on those days → 08:30 / 08:40.
- light.living_room_floor_lamp_window deleted by the user; removed from the table.
- Rules now: workday 06:00 morning · not-workday 08:00 morning · daily 09:00 day ·
  daily sunset−01:00 evening · eve-of-workday 23:00 overnight · not-workday 00:30 overnight.

## All-day presence + light (0.13.x, 2026-10-19)

User: Day needs the same presence/lux trigger as Evening; no nightlight in daylight. Living room,
kitchen, dining now `"*"` presence_lux (Morning/Day/Evening; Overnight keeps nightlight_then_dark),
target **50 lx** (33 lx felt too dark). Empty: dark → `vacant` (living plants 40 % + others off;
kitchen ceiling off → aux follow dim; dining sideboard on), light → `vacant_bright` (all off incl.
kitchen aux / sideboard / reading lamp). Dark/light judged on daylight memory (per zone, in the
runtime); below target with lights on = dark; no memory → sun. Delays: living 15, kitchen 3,
dining 5 min. Evening-only overrides removed (the "*" covers Evening).

**0.13.2–0.13.3 (2026-10-19 16:29 regression):** walking into the kitchen didn't light it — it
read 56 lx because its dim aux lights were on (29 with all off). Arrival now uses the same daylight
rule as leaving, over ALL zone lights; lights-off memory expires after 1 h (then the sun) and is
saved with the bookkeeping so restarts keep it. Statuses name the reading used ("… (with the
lights off)", "by the sun").

## Kitchen & dining evening (0.12.0, 2026-10-19)

- Kitchen `follow`: ceiling leads the aux lights in EVERY mode (≥79 % → recessed/sink 100,
  ≥21 % → 70, else 10; cabinet on) — stands aside when our own command set the aux too
  (Overnight dark). **automation.ambience_kitchen DISABLED** (delete after a week; its
  overnight-condition patch is moot).
- Evening (presence_lux, 25 lx): kitchen occupied ceiling 100 % (aux follow → 100), empty 3 min
  → ceiling off (aux follow → 10 % = "aux on"); dining occupied chandelier 30 %, empty 5 min →
  chandelier off + sideboard on. automation.ambience_dining_room (chandelier on → sideboard on)
  left running: no conflict.

## Bug fixed 2026-10-18 (0.9.1)

18:43:20 living room lamps switched on at the wall while the FP300 still read empty; SB Modes
switched them off at 18:43:27. Causes: presence_lux re-applied its vacant setting on every
lux change while empty; the keeper released a hand change immediately when the room had
already been empty longer than the delay. Now: vacant settings apply once per vacancy
(presence_lux, occupied_vacant); a hand change during a vacancy lasts its delay counted
from the change (vacancy_off: min(off_after, max_on)); a timer release re-runs the zone's
behaviour so the released lights follow the rule again. Regression tests added.

## Known limitations (accepted)

- A light changed by hand WHILE HA IS DOWN is not marked as an override (changes out of
  `unavailable` are ignored on purpose). Off-when-empty rules then treat it as the house's
  state; occupied_vacant (Office) may re-apply its set over it. A shutdown snapshot +
  startup compare would fix it — user 2026-10-18: not worth the effort.
- After a restart, vacancy clocks restart (HA resets last_changed) and wake steps whose time
  passed during the downtime are skipped, not run late.

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
