# sb-modes — session notes

| | |
|---|---|
| Repo | github.com/snadboy/sb-modes (private, created 2026-10-17) — local `~/projects/git/sb-modes`, branch `main` |
| Domain | `sb_modes` (MIT), single config entry |
| Design | https://claude.ai/artifact/9GAUvd4N2gctYm88fnMqhs (Claude Docs doc "SB Modes — design") |
| Tests | `python3 tests/test_validate.py` (offline) · `.venv/bin/pytest` (all, inside HA 2026.10.2 via pytest-homeassistant-custom-component; Python 3.14 venv, 670 MB, gitignored) |
| Frontend | `frontend/sb-modes.js` = card + panel, one module (add_extra_js_url + panel_custom), SB UI Kit row style; bump `VERSION` in the JS AND manifest version together (`?v=` cache-buster). Headless check: a Playwright script injecting `hassTokens` (long-lived token) via add_init_script, then screenshot `/sb-modes`. To compare cards, create them on the `/sb-modes` page (document.createElement works there); on a dashboard page the script's evaluate saw no custom elements at all (even sb-background-card) and loadCardHelpers gave error cards |
| Release (since 2026-10-23, repo PUBLIC) | bump version in manifest.json + VERSION in sb-modes.js, commit, push, `gh release create vX.Y.Z`; wait for `update.sb_modes_update` to show the new `latest_version`, call `update.install` on it, check `/config/custom_components/sb_modes/manifest.json`, THEN a FULL HA restart (never batch the restart with the install). Since HA 2026.11 HACS is built in as **Marketplace** (Settings → Marketplace; took over the HACS installs, `custom_components/hacs` is gone, the old HACS websocket commands / `ha_manage_hacs` return "Unknown command"). `hacs.json` is still what the catalog reads. |
| Quick deploy (testing only) | `tar czf - --exclude=__pycache__ -C custom_components sb_modes \| ssh snadboy@homeassistant "cd /config/custom_components && sudo tar xzf - && sudo rm -f sb_modes/frontend/*.gz"` then a FULL HA restart — HACS/Marketplace leave `.gz` copies of the JS; a stale `.gz` would be served instead of the new file |
| Public repo | NEVER commit Matter setup codes, tokens or IPs. History was scrubbed with git-filter-repo 2026-10-23 (5 codes → "(setup code in SB Device Notes)") before going public |

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

## Editor — step 1, backend (0.20.0, 2026-10-21)

Spec: the design doc's "Editor" section (sidebar page only; card gets an Edit button; draft →
check → review → save → revert). Built: editor.py websocket commands (admin only)
sb_modes/schema, check, diff, draft/get|set|discard, save, versions, revert, try. One shared
draft in .storage/sb_modes.draft (cleared by saving `{}` — the test harness's mock storage
ignores async_remove); versions (last 20, with the review text) in .storage/sb_modes.versions;
the first save also records the table as it was (version 1). EVERY save goes through
editor.async_save_table — including the sb_modes.save_config action (now returns
{warnings, version}); a draft whose base version is behind is refused unless force.
validate.py messages now carry field paths (Issues.detailed(): {path, message}) via a
context-var (_here/_sub) — callers still get plain lists. changes.py = pure "what changed, in
words". model.BEHAVIOUR_INFO + label/help/unit per param feed the forms.
**Step 2, Zones tab (0.21.x, 2026-10-21):** frontend/sb-modes-editor.js, loaded by the page only
at /sb-modes/edit (page bar: Status | Edit; the card's ⋮ menu has "Edit modes & zones" for
admins). Zone chips (+ Zone / Delete zone), basics via ha-form (name, areas, own lights /
occupancy / light sensors), "Behaviour by mode" rows (All modes + each mode) with a behaviour
picker and forms built from the schema; custom light-settings table (on / off / at least, %, K,
add, remove, "Use the lights as they are now", Try it) and wake-step editor. Field edits never
redraw (focus); the draft is stored 0.5 s after the last edit; errors go to the deepest section
whose path matches, scalar-param errors onto the ha-form field. ha-form is pulled in through
loadCardHelpers → an entities card editor. Tested headless: edit → "Draft · 1 change" → Review
→ out-of-range error disables Save → Discard (saved table untouched).
**Step 3, Modes + Schedule (0.22.x, 2026-10-21):** editor tabs Zones | Modes | Schedule. The
behaviour editor works on an OWNER — a zone's setting for a mode or a mode's default — so
modes reuse it (Try it only for zones). Modes: fallback, name, icon (modes may carry `icon`;
select attribute mode_icons; the card's built-in icons are the placeholders), default
behaviour, ↑/↓ order, + Mode, delete (lists what goes with it; refused while the mode is the
fallback or a wake-up's after_mode). Schedule: rules (mode, a time / sunrise / sunset ± h:mm,
days or an SB Scheduler calendar with "the evening before" / "not on those days"), + Rule,
and "The coming week" from sb_modes/preview (draft's rules; day sets read fresh via
Runtime.async_read_day_sets; consecutive same-mode occurrences are not changes). Review text:
edited rules pair up ("Schedule: A → B"), "every day" / "weekdays", "sunset − 1:30".
**Restructure (0.23–0.24, 2026-10-21)** after the user found the tabs disjointed; mock-ups:
https://claude.ai/artifact/JPyvAkDUgyBfjt8dRswrdz (House · Times · Room · phone). The user wants
day types incl. **Vacation**, and edits mainly in a browser.
- **Day types** in the table: `day_types` (ordered; first whose SB Scheduler day set holds the date
  wins; the last has no day set = every other day) + `times` {type: [{mode, at, eve?}]} ("eve" =
  the evening before such a day). model.day_type_of/_day_entries; `schedule` stays as "other
  rules". Proven equal to the 6 old rules (60 days offline, 14 days live) and the LIVE TABLE WAS
  CONVERTED (version 2 "rules → day types"; version 1 = the old rules, revertable).
- **Editor = House | Times, with Room pages.** House: rooms × modes grid (Usual / own / ▸ when it
  begins / Add-ons, each a link to its box on the room page), mode headings open a mode panel (name, icon, order, delete, the default
  behaviour under "advanced"), + Room / + Mode, start-up mode. Times: day types (name, calendar,
  priority, + Day type → goes first with the last type's times copied), a 24-h band and editable
  times per type ("same in …" edits the identical entries of the other types too), other rules,
  the coming week with each day's type. Room: Usually · By mode (behaviour + when it begins) ·
  Add-ons (follow bands, TV overlay states/holds/debounce, buttons per event type: hold N min / next
  / until released / release, flash, lights) · Devices (folded). Step 4 (extras) is included.
- Status card's Schedule section lists day types' times + other rules.
**Vacation (2026-10-21):** SB Scheduler day type "Vacation" (`calendar.sb_vacation`, created via
sb_scheduler.set_day_set with NO id): no weekdays, `base_dates` for dates the user enters, and
`force_calendars [calendar.anderson]` + `force_match "calendar.anderson: #vac"` (tagged events).
SB Modes day types: Vacation (first) → Workdays → Days off; Vacation starts with the Days-off
times (version 3). **Offline sensors (0.24.3):** presence.vacant_since leaves out an
unavailable occupancy sensor while another source reports; the empty clock starts no earlier
than when it went offline; all offline → can't tell (as before). Prompted by the Living Room
Shelves LWR02 going offline 13:56 and blocking the room's empty setting.
**Auto-off (0.25, 2026-10-21):** zone `auto_off: [{lights, with_empty?, on_minutes?}]` (autooff.py) —
lights a person turns on by hand that SB Modes only turns OFF: with the zone's empty setting
(Runtime.send cascades when a zone command turns lights off while the zone is vacant), or once
on N min while the zone has been empty its usual delay. Clears the light's hand-change mark.
Editor: Room → Add-ons → "+ Auto-off". For the Office's Lutron "Floor Lamp Side Window": off with
the empty setting, or on 15 min while empty — the user sets it up in the editor (not yet saved).
Kitchen "dark below" 50 → **60 lx** (version 4, 2026-10-21 16:2x): it came on at 45 lx in passing
clouds and the user wanted it earlier.
**Lux rule (0.25.2, 2026-10-21):** a zone's reading is the LOWEST of its light sensors that
report (any one below target → dark); offline sensors are ignored; with none reporting, the
last lights-off reading within the hour, else the sun (it used to be the mean, and "no reading"
counted as dark — the Shelves LWR02 dropout lit the Living Room all afternoon). Table version 5:
living_room lux = Shelves + Clock (60 lx), dining_room = Driveway + Pantry (50 lx).
Shelves re-pair: node 191 = 0xBF, merged into the same HA device by serial. A dead node left
in the Matter server re-creates its entities (as `_2`) on every restart, so: remove the node
(matter server `remove_node` — this deletes the shared HA device), reload Matter, and HA
restores the device's name/area/labels and the entity IDs from its deleted-registry. Done.
**Hand grace (0.25.3, 2026-10-21):** a light turned on/up by hand while the zone's sensors see
no one is marked `unseen` and kept at least HAND_GRACE_MINUTES (5) — unless the sensors see
someone after it, then the usual delay. The Basement Bath's FP300 (sensitivity was "low",
now "standard") missed the person ~100 s and vacancy_off turned the vanity off at 30 s, three
times. Also: Runtime.apply leaves out a light GROUP target when any member is changed by hand
(the bath's rule targets `light.basement_bath_lights`, so the override never protected it).
Table version 6 (same evening): every empty delay under 5 min raised to **5 min** at the user's
request ("30 s is too short") — kitchen 3, office 2, bedroom/laundry/bath 30 s, hallway 60 s.
Left as they were: living room 15 min, Overnight dark 10 min, family room 5 min.
Was open: how Vacation days are marked (an SB Scheduler day type from dates and/or a tagged
calendar event) — then add the Vacation day type in Times.

## Cleanup 2026-10-21

Removed (restore point on HA: /config/cleanup-2026-10-21/ — automations/scripts/scenes yaml,
core.config_entries, sb_scheduler store, plus each item's own JSON in cleanup/):
12 automations (8 "Lights/Scene - Occupancy - …", SB Modes - Schedule (interim), Lights - Whole
House, Ambience - Kitchen, Ambience - TV), the 3 SB Background nightlight instances,
script.area_nightlight, 17 scenes only those used (ambience_kitchen_*, ambience_tv_*,
*_overnight_dark/_nightlight, early_morning_scene, overnight_scene, night_light_office,
office_normal), SB Scheduler "Bedside Lamps - Wakeup"/"Get Up". Living Room and Dining Room
FP300s removed from HA/Matter (nodes 0x97, 0x99) with their Battery Notes entries; their MTR
labels ((setup code in SB Device Notes), (setup code in SB Device Notes)) left in place, unused. Still on: "Ambience - Dining Room"
(chandelier on → sideboard on) — redundant now, awaiting the user's OK.
Correction: the Family Room TV-area FP300 is fine — it sits in a low-traffic spot (user).

## Activity log (0.19.0, 2026-10-20)

User wanted to see what SB Modes did through the day. activity.py: Runtime.log() fires
`sb_modes_activity` {kind, zone, zone_name, message, entities, entity_id}; logbook.py describes
it for HA's Logbook (name "SB Modes · <zone>", icon per kind, filed under the zone sensor, or
select.sb_modes_mode for mode lines). A command's event has the SAME Context as its light
calls → the Logbook shows those light changes "triggered by" it (verified live 18:59).
Logged: commands (only what actually changes — Runtime.send filters by driver.plan, but
`_ctx_targets` keeps the FULL command so follow's stand-aside still works), skips (held zone,
left alone as changed by hand, follow stood aside, TV change in its cool-down), hand changes and
their end (vacancy / arrival / mode change), holds and releases (by hand / button / time up),
mode changes (with how, and what ended). Status lines reworded via activity.PHRASES.
Card: an "Activity" section (last 24 h from logbook/get_events filtered to the zone sensors +
the mode select, refreshed every 30 s while shown, filter chips per zone) and "Recent" (last 5)
in each zone's detail. Fixed: only the first section header (Schedule) was wired to open.

## Kitchen & dining aux always on (2026-10-20)

User: outside Overnight the kitchen and dining aux lights are always on. Table changes (no code):
kitchen `vacant` = ceiling off + recessed/sink 10 % + cabinet on (explicit, so it also lights
them when the ceiling was already off), `vacant_bright` removed (= vacant); dining `occupied`
+= sideboard on, `vacant` = chandelier off + sideboard on, `vacant_bright` removed;
`on_enter` day/evening: kitchen cabinet on + recessed/sink ≥10 %, dining sideboard on (Morning
already had them). Overnight unchanged (nightlight then dark, aux off). Applied at once 16:5x.

## Living Room sensors redone (0.18.0, 2026-10-20)

User replaced the Living Room FP300 with two GL LWR02s (Matter/Thread sleepy end devices):
"Living Room Clock LWR02" (binary_sensor.main_floor_living_room_living_room_clock_lwr02_*,
renamed from lwr02_*_2 the same day) and
"Living Room Shelves LWR02" (binary_sensor.main_floor_living_room_living_room_shelves_lwr02_*).
New zone keys `occupancy` / `illuminance` (own lists, like `lights`). living_room: occupancy =
both LWR02s; illuminance = Shelves LWR02 only (user's choice); target_lux 50 → **125**
provisional (Shelves read ~2.5× the FP300 around noon: 208 vs 73–87) — tune from dusk data.
All FP300 uses removed: SB Modes table; Monitor dashboard Environment view (temp/humidity →
Shelves LWR02; before-copy in that session's scratchpad); the disabled SB Background "Living
Room Nightlight" instance (occupancy → both LWR02s, still disabled). Clock LWR02 dropped off
Thread 11:26–~11:55 right after pairing (address expired), came back by itself.
125 lx looked high already at 12:4x: the Shelves read 75 lx and the lamps came on at midday.

**Dining Room Driveway LWR02** (2026-10-20, serial b759b3c1a5463ad2, labels " MTR (setup code in SB Device Notes)"
+ Presence; entities `*.main_floor_dining_room_dining_room_driveway_lwr02_*`). The HA app's
add failed half-way (left on the phone's fabric, ECM window); shared from the phone. dining_room:
occupancy = FP300 + Driveway LWR02 (alongside — user didn't pick), illuminance = FP300 only
(an explicit list, so the LWR02's lux — 673 lx at 16:xx — does not join the mean).
Later the same day: FP300 being removed → occupancy = Driveway + **Dining Room Pantry LWR02**
(" MTR (setup code in SB Device Notes)", `*.main_floor_dining_room_dining_room_pantry_lwr02_*`), illuminance =
Driveway LWR02 at 50 lx (read ~1.0–1.2× the FP300 in daylight 16:20–16:54; it does see the
chandelier after dark: 26 lx at 30 %). Pantry read 1 lx after dark — check its daylight before
ever using it for lux. No FP300 (dining or living) uses remain anywhere in HA config.

## Presence fixes (0.17.0, 2026-10-20)

From the overnight review (2026-10-19/20): the Family Room TV-area FP300
(binary_sensor.basement_family_room_tv_fp300_occupancy) detects rarely — it is in a low-traffic
spot and working as expected (user, 2026-10-21); seated TV viewers are covered by the TV. Consequences fixed in software:
- **TV = presence** (presence.py): an overlay's media player playing/paused/idle/buffering/on
  counts as occupied for its zone (`overlay.presence`, default true); off/unavailable/unknown
  only mean "not watching". Empty-since for the TV = when it LEFT a watching state
  (Runtime.media_left, set by the overlay), so its nightly drop-outs don't restart clocks.
  TV idle never lasted > 10 min in 2 weeks (always → off), so idle is safe.
- **Off by hand stays off**: an override now records `off`; those are not released by the
  vacancy delay (nor by a behaviour's release_overrides) but by an ARRIVAL after the zone has
  been empty its delay since the off (Keeper._arrived; brief sensor drop-outs don't count).
  Fixes: living plants re-lit 22:46 after a bedtime off at 22:31; couch re-lit 21:57.
- **Groups left out of zones** (zones.py: entity platform "group"): they were in Family Room
  (occupancy group), Laundry Room (occupancy group), Living Room (light group incl. the
  Christmas tree light). An explicit zone `lights` list can still name one.
Card: the TV lights up as presence; "Turned off by hand: … stays off until someone comes back".
(0.41.2 narrowed this to lights the empty setting would light: see below.)

## Bedside 1 % floor (0.16.0, 2026-10-19)

User: outside Overnight the bedside bulbs should be on at least 1 %. Chosen (of two): a check
at the START of Day and Evening only — bedside `on_enter` day/evening `{min_brightness_pct: 1}`
(new target key: on and at least n %, a brighter light is left alone). Not Morning: the wake
sequence lights them at +30 min and a 1 % at 06:00 would pre-empt it. Not continuous: they are
switched off at bedtime (~22:10) while still in Evening, and a floor would turn them back on.
Trade-off accepted: an accidental off mid-mode stays off until the next mode change. Old dead
"Family Room Light Hold BILRESA" device removed (and its orphaned Battery Notes sub-entry).

## Evening check 2026-10-19 (0.15.1)

Evening ran as designed: arrivals lit within ~1 s (kitchen 38 lx, dining 6–21, living 9),
empties on time (kitchen 3, dining 5, living 15 min to the second), people leaving at 18:17
switched kitchen/dining off by hand (released normally), no follow undid anything, Office
LWR02 46-s dropout ridden out by the 2-min delay. **Unexplained once:** 19:32:05, the first
kitchen arrival after a restart (19:31), the aux lights did not follow the ceiling to 100 %
(no command, no error; every other change that evening followed in ~70 ms; the boot path
follows fine in tests). Watch for a repeat; turn on debug logging for
custom_components.sb_modes if it recurs. User confirmed the TV play/pause overlay and the
hold button work. 0.15.1 fixes "Unable to remove unknown job listener" logged when the entry
unloads after a restart (the fired listen_once was removed again).

## Hold button (0.15.0, 2026-10-19)

The old Good Morning BILRESA (Matter, labels button + " MTR (setup code in SB Device Notes)") is now **Family
Room Hold BILRESA** (area Family Room; entities `*.family_room_hold_bilresa_*`, Matter battery
type = `sensor.family_room_hold_bilresa_battery_type_matter`). Its automation
`good_morning_bilresa` was DELETED at the user's request (copy kept only in that session's
scratchpad); its small press had turned the bedside lamps off at 19:21, which is why they were
dark that evening. Zone `buttons` in family_room: big press → flash once, hold 60 min at couch/TV
30 %; big double press → same, hold until the next mode; small press → flash twice, release
(zone restarts: TV levels if playing/paused, else the TV's current-state levels, then vacancy_off).
Presses are acted on 2 s later (a newer one replaces it: Matter replay bursts collapse) and
to/from unavailable is ignored. The flash is made (Lutron has no flash): on lights go off, off
lights come on (50 %), 0.7 s, back. The dead old "Family Room Light Hold BILRESA" device was removed later (0.16.0 notes).

## TV overlay (0.14.0, 2026-10-19)

Family Room `overlay` on media_player.family_room_tv replaces **automation.tv_lighting** (TV
Lighting blueprint; DISABLED, delete with the others ~25 Oct). Same levels, couch / TV light,
3 s transition: playing 5/5, paused 20/20 (both HOLD the zone: vacancy_off is stopped), idle and
off 30/20 (set once, then vacancy_off again: off 5 min after empty). A state must last 5 s
(attribute-only updates ignored; buffering restarts the wait); for 60 s after the TV was
unavailable/unknown nothing counts (it drops out to unavailable → unknown → off several times
an evening). Precedence: zone hold > hand change > overlay > zone behaviour. When a hold or a
hand change ends while the TV plays, the zone goes back to the TV levels (Overlaid re-applies on
start). Not done: the "Family Room Light Hold" BILRESA button (still unavailable) as a hold
trigger.

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

## Add-ons (0.25.4, 2026-10-21)

The user named the per-room extras that apply in every mode **add-ons**: Follow, TV overlay,
Hold button, Auto-off (table keys `follow`, `overlay`, `buttons`, `auto_off`). The editor's
"Always" column/section is now "Add-ons"; on House each add-on is a link to its box on the
room page (`data-addon="zone|key"` → `_goRoom(zone, key)` scrolls to `[data-box=key]`).
A new add-on TYPE is code: a runtime module (like overlay.py / buttons.py / autooff.py)
started in `Runtime._restart_followers`, checks in validate.py, review text in changes.py,
a box + "+" button in the editor's `_addonsHtml`, and tests.

## Wake-up add-on; Bedside folded into Bedroom (0.26.0–0.26.1, 2026-10-21)

New add-on `wake_up: {after_mode, steps: [{after_minutes, targets}]}` (wake.py): restarted with
the behaviours on every mode change; arms its steps only when the house just left `after_mode`;
steps go through Runtime.apply (a hold or a hand change wins). Status in the zone sensor's
`wake_up_status`; card detail line; editor box + "+ Wake-up"; House link. The wake_sequence
BEHAVIOUR still exists (old table versions validate) but is hidden from the editor's picker.
Table version 7: the Bedside zone removed; Bedroom gets wake_up (after Overnight +30 min 1 %,
+40 min 4 %), its `lights` gain the two bedside bulbs (so hand changes on them are tracked),
and on_enter Day/Evening "at least 1 %" for the bulbs. sensor.sb_modes_bedside deleted.
Trade-off accepted by the user: holding the Bedroom now also pauses the wake-up.
0.26.1: `draft/set` with a config equal to the live table keeps NO draft (returns `same`). An
ha-form reporting its first value on load had stored a no-op copy of version 6; once version 7
was saved it showed "5 changes · saved elsewhere" and Save would have undone the Bedside move.

## Add-on instances (0.27.x, built 2026-10-22) — LIVE, table version 8

Editor layout 0.27.3–0.27.4 (user-approved mock-up, canvas boards 8–9): a ROOM has one RULE
(normal rule = overrides["*"]; a mode with its own is a framed "Different rule" cell; blank =
normal) and ADD-ONS (extra actions on top). House grid: a row per room (rule) and, folded per
room, a row per add-on with marks in the mode columns — ▸ mode begins (◂ ends), ● bar active
throughout those modes, 🕒 bar clock, ⚡ bar device state (_addonMarks/_addonCells). No Add-ons
column, no add-on names in mode cells. Room page: 1 · Rule (normal + per-mode rows), 2 · Add-ons
(same rows-by-mode table, editor opens under its row), 3 · Devices. Tables use table-layout fixed.

Zone `addons` = ordered list of instances {id, name, type, enabled?, when?, …} (model.py block
comment has the schema). Types: lights (trigger at / mode_begins / mode_ends / entity+to; steps of
targets | hold | release | script — addons.LightsAddon), while (overlay.Overlay), button
(buttons.Buttons, merged per room), follow, auto_off (merged into one AutoOff, per-rule When).
When = modes, days / not_days (day type ids or SB Scheduler day sets), between. One while and
one follow per room (validation). Older keys (overlay, buttons, follow, auto_off, wake_up,
on_enter) convert on read (model.zone_addons; editor toAddons) so old versions still load;
on_enter is now a lights add-on with a mode_begins trigger (Runtime._apply_on_enter is gone).
Runtime: Overlay.regate() on every tick (60 s) and mode change; add-ons re-armed in
_after_day_sets (calendars load after start — before that a Days-off add-on showed today).
Sensor attrs: `addons`, `addon_status`. Editor: Room → 1 · Room modes, 2 · Mode exceptions,
3 · Add-ons (cards: switch, Trigger · When · Then pills, next run, ▲▼, Edit expands in place;
"+ Add-on…" select). House: add-on names (with clock time) link to their card; ▸ marks show
mode-begins add-ons. Table v8: names given; Bedroom = Workday wake-up 06:30 (+10 4%) on
workday, Day-off wake-up 08:30 on day_off + vacation, Bulbs at least 1% on Day/Evening begins.

## Matter colour while off (0.27.9, 2026-10-22)

Office floor lamps (5 × Govee H607C, Matter over Wi-Fi, group light.office_floor_lamps_h607c):
on arrival the group came on at 25 % but two lamps kept a hand-set colour. Matter lights ignore
ColorControl commands while off, so driver._turn now turns an off light on first, then sends the
colour. Separately, a colour set in the Govee app may not reach Matter: the east lamp reported
3401 K while showing another colour, and a repeat of 3401 K was a no-op on the device; a nudge
(3600 K, then 3401 K) fixed it. User chose "check, then send if necessary" (0.27.10): light groups
are checked and set lamp by lamp (driver.members / needs), so a lamp that differs from a group
that matches on average gets its own call. A lamp that MISREPORTS (Govee app) still can't be caught.

## Table version 9 (2026-10-23): wake-ups only ever raise

The first workday clock wake-up (06:30) dimmed the bedside bulbs from a hand-set 4 % back to 1 %:
hand changes end with the 06:00 mode change. Both Bedroom wake-ups now use min_brightness_pct
("at least" 1 %, then 4 %), so they never dim a lamp someone already turned up.

## Calendar trigger; thermostat for trips (0.29.0, table version 11, 2026-10-23)

Lights add-on trigger {"calendar", "match", "edge": start|end, "offset_minutes"} (addons.py
_scan every 15 min over 60 days; title matched as a whole word/tag like SB Scheduler; an edge
missed while a rescan replaced its timer fires once, never one from before a restart).
Dining Room add-ons: "Thermostat — away" (#vac starts → script.thermostat_away: heat_cool
65–80 °F) and "Thermostat — coming home" (60 min before #vac ends → script.thermostat_home:
heat_cool 70–76 °F) on climate.dining_room_dining_room_hvac. First use: Nov 13 09:00 / Nov 15
14:00. SB Scheduler's Vacation day set only picked up the new #vac after `sb_scheduler.refresh`.
Option 2 (phone proximity / drive time for everyday returns) = nice-to-have, not built.
Table version 12 (2026-10-23): the Day-off wake-up no longer runs on Vacation days (house empty).

## Away mode and the lived-in look (0.30.x, table version 13, 2026-10-23)

New add-on pieces: steps {set_mode, hold} / {resume_schedule}; "random_minutes" on a time
trigger (±N, one draw per day) and on a step (+0…N); type "replay" (replay.py: every 30 min reads
the recorder for the next 40 min, N days back, and schedules each on/off/brightness change).
LightsAddon.start() now re-arms the trigger WITHOUT cancelling steps under way (the hourly
calendar refresh had cut sequences short); stop() is the full teardown. 0.30.1: a time add-on
whose When names modes resumes nothing at start-up outside them; status "only in <mode>".
Table v13: mode `away` (mdi:airplane, never scheduled); every room's rule in Away = hands_off.
Dining Room: "Away mode on" (#vac starts → all house lights off, then set_mode away paused) and
"Away mode off" (#vac ends → resume_schedule). Lived-in pattern add-ons (When: Away) in
Kitchen, Dining, Living, Office, Bedroom, Upstairs Hallway; a "Lived-in replay" (7 days back)
per room, switched OFF — the user can flip pattern ↔ replay per room.

## Office hold buttons (0.31.0, table version 14, 2026-10-24)

Office Lights BILRESA (event.bilresa_dual_button_button_1 large / _2 small — generic ids).
Large press → hold until the next mode at the Office's usual occupied values; small press →
same at dim values (mini rack 20 %, desk 8 %, floor lamps 8 %, Mova 12 %, all 2700 K; clocks
96 / 30); double-press either → release. Confirmation blinks only the desk bulb (new
`flash_lights` on a press action). Bedside BILRESAs stay on the snadboy/bilresa-dual-button-light
blueprint (tap ladder + hold ramp); their double-press is free for SB Modes if wanted.

## Laundry Room: door FP300 only (table version 16, 2026-10-24)

The Laundry Room furnace FP300 reported "occupied" non-stop from 12:21 to 15:4x (10 s hold, alive —
temperature/lux kept reporting) and held the light on; user: "the furnace sensor seems broken".
laundry_room `occupancy` = [door FP300] only. Re-add the furnace FP300 once it is fixed or replaced.

## TV lighting on several players (0.32.0, table version 17, 2026-10-24)

A "while" add-on takes `entities` (or the old single `entity`): Overlay follows the most active
(overlay.combined: playing > paused > buffering > idle > on > standby > off; unavailable ignored)
and every player counts as presence. Family Room TV lighting = media_player.family_room_tv +
media_player.plex_plex_for_android_tv_google_tv_streamer — under Plex the TV's own player reads
"off", which had left the lights at the off levels.

## Room on/off switch (0.33.0, 2026-10-27)

`zone.enabled: false` switches a room off (user wanted "a method to disable an area"; per-mode
off was declined for now — a mode's rule can already be "Hands off", and add-ons have When modes).
model.resolve returns hands_off with source "disabled" in every mode; runtime starts the
`Disabled` behaviour (status "disabled", not watched for hand changes) and skips all the room's
add-ons in `_restart_followers`. Editor: a switch per room on the House grid (row dimmed, cells
"off") and in the Room page header (with a note; settings kept). Card: "Off — SB Modes leaves
this room alone". changes.py: "switched off / switched back on".

## Room level slider + tappable lights on the card (0.34.0, 2026-10-31)

User: "Is there no way to set light levels from SB Mode card?" → both options. In a room's open
detail on the card: each light / sensor is a button firing `hass-more-info` (HA's own dialog), and
a **Room level** slider (0–100, step 5; starts at the average brightness of the lights that are on)
calling the new action `sb_modes.set_level {zone, brightness_pct, hold=next}` →
`Runtime.set_level`: hold first (keeper.hold, by "card"), then `send` — dimmable lights (any
supported_color_mode but onoff) to the %, on/off lights and switches just on; 0 = all off. The
card skips re-rendering while the slider is dragged (`_sliding`, cleared on change / blur).

## Dimmable vs on/off room controls (0.35.0, 2026-10-31)

User: "Room level should not effect devices without brightness. There should be a way to handle
brightness AND on/off lights." `Runtime.set_level` now sends only to `zone_dimmable(zone)` (a
`light.*` with any supported_color_mode but onoff, read from the state or, while unavailable,
the entity registry's capabilities; 0 = those off) and raises if there are none. New action
`sb_modes.switch_lights {zone, state: on|off, hold=next}` → `Runtime.switch_lights`: every other
zone light (on/off lights, switches) on or off. Both hold first. The zone sensor gains a
`dimmable` attribute; the card shows a *Dimmable* slider and/or an *On/off* On / Off pair with
"n of m on", only for the kinds the room has. Tests: 157 pass.

## Room light controls in a dialog (0.36.0, 2026-10-31)

User: the inline slider / On-Off was "much too easy to hit accidentally when I'm scrolling on the
phone" → make it a modal. The open detail now shows only a **Set lights…** button
(`data-lights`); it sets `this._dlg` and `_renderDialog()` fills a native `<dialog class="lights">`
kept beside `<ha-card>` in the shadow root (`showModal`, so it sits in the top layer and the page
cannot scroll under it). Closes on Done, Esc or a backdrop tap (content is wrapped in `.dlg` so only
a real backdrop tap has `target === dialog`). Re-rendered on every `_render` while open; `_sliding`
still blocks re-render mid-drag. Service errors show inside the dialog.

## One room dialog: hold + lights (0.37.0, 2026-10-31)

User: "Hold and Levels/On-Off shouldn't be so disjointed" → the row's ⋮ (`.zkb`) no longer opens
the kit hold menu; it opens the `<dialog class="lights">` (`_renderDialog`, state `_dlg` = zone,
`_dlgHold` = chosen hold). Hold section: current hold text, chips 15 min / 30 min / 1 hour / Next
mode / Until released (tap = `hold_zone` now; the chip matching the current hold is selected) and
Release. Lights section: Dimmable slider and On/off buttons, which pass the chosen hold to
`set_level` / `switch_lights`. The detail's Set lights… button is gone. The card's head ⋮ menu
(open page / editor) is unchanged. Checked in headless Chromium with a stub hass (live states,
real sb-ui-kit.js): renders, calls carry the hold, no errors.

## Room dialog per the user's sketch (0.38.0, 2026-10-31)

From a hand-drawn sketch ("Living Room Hold"). `_openDialog(zid)` snapshots the room's hold and each
light (`off` / `brightness_pct` / `on`; unavailable skipped) into `_dlgEntry`. Hold = radios *For
[15/30/45/60/90/120] minutes* (`HOLD_MINUTES`; the select is outside the radio's label so picking
minutes doesn't start a hold) / *Until released* / *Until mode change*; picking one calls
`hold_zone` at once; always enabled. *Release to <mode>* only when held. *Light settings* (Dimmable
slider, All off / All on) are disabled unless the room is held (and unless it has that kind of light)
and are live, calling `set_level` / `switch_lights` with the new hold value **"keep"** (leave a hold
as it is; else next). **Cancel** (also Esc/back via the dialog's `cancel` event; backdrop taps do
nothing): if a light was touched, `set_lights` the snapshot (new action: `{zone, lights: {eid:
target}, hold}`, rejects lights not in the zone), with the entry hold (timed → minutes left); else
`hold_zone` the entry hold; then `release_zone` if the room was not held on entry. **Set** just
closes. `_sliding` also guards the minutes select while it is focused (a re-render would close it
on a phone).

Header fix in the same release: `.sbk-count` (kit: `flex:1; white-space:nowrap`, no min-width) pushed
the ⋮ / › past the card border when "· n needs attention" showed; the card now sets
`.sbk-head .sbk-count { min-width:0; white-space:normal }` so the text wraps. The same kit rule
may bite sb-background / sb-watch (they use `sbk-hwrap` heads too); a real fix belongs in sb-ui-kit.

## Page redesign (0.39.0, 2026-11-01) — BUILT

Mock-ups: https://claude.ai/artifact/LTdsG5k8wupmDmQuhjxd7r (Design canvas: Status, House, Room
desktop + phone; Mode; Legend; Times). The user's asks: a back button top-left on every sub-page;
every collapsible starts collapsed; lists as tables, not word wrap; nothing spilling out of its box.
Agreed design:
- One app bar: HA menu (top-level) or **← back** (Room, Mode = their own pages) · title · nav
  Status | House | Times · **?** legend popup (the intro paragraphs move into it).
- Draft bar only when there are unsaved changes, sticky at the bottom.
- Sections start collapsed with a one-line summary; (proposed) remember open/closed per section.
- Status: rooms as a table (Room ▲ · Rule now · Doing · Hold · ⋮); an opened room shows Lights /
  Sensors / Add-ons / Recent tables. Warnings grouped per device ("Mova Bulb L535E unavailable ·
  6 settings").
- House: fixed-width grid cells, chips ellipsize inside their cell; add-on rows collapsed to
  "n add-ons ▸". On a phone: no grid — pick a mode, list what each room does in it.
- Room: Rule / By mode / Add-ons / Devices sections; fields sized to content; light settings as a
  table with the three settings (Occupied and dark · Empty, dark · Empty, light) as tabs; long
  names one line + ellipsis.
- Mode: own page (name, icon, begins-at, rooms table, when-it-begins, default rule, delete).
- Times: day types with bands; each type's mode changes collapsed ("4 changes ▸") into a table;
  the coming week as a days × modes table.
- **Sorting:** rooms alphabetical everywhere (card too, replacing the editor's room order);
  lights, sensors, devices, warnings alphabetical; modes keep time-of-day order, day types and
  add-ons keep priority order, week/activity by time. Larger tables get sortable headings,
  the choice remembered.
- Open questions put to the user: drop the separate Edit mode (editing always available, draft bar
  as the only signal)? remember open sections?

Built as 0.39.0. The user answered: Edit stops being a separate mode (yes); sections remember
being open only for the browser session (sessionStorage `sb-modes-open`, keys `status:<x>`,
`ed:<section>`, `house:addons:<room>`, `times:<daytype>`; table sorts `sb-modes-sort:<table>`).
- Panel (sb-modes.js `SbModesPanel`): routes `/sb-modes` (Status), `/house` (old `/edit`,
  `/edit?zone=` → room), `/times`, `/room/<id>`, `/mode/<id>`; app bar = ha-menu-button or
  ← back (room/mode → House) · title (+ "House › X", from the editor's `sb-modes-title` event)
  · nav Status | House | Times (under the bar on a phone) · ? → `<dialog class="legend">`
  (LEGEND: the old intro paragraphs + grid colours). `navigate()` = pushState + location-changed.
- Status = the card with `panel: true` (host attr `panel`): sections as `.pc` cards; rooms as a
  div grid table `_roomsTableHtml` (Room · Rule now · Doing · Hold; `_th` sortable headings);
  `_roomInfo` shared with the dashboard row; opened room `_roomDetailHtml`: In <mode>, Lights /
  Sensors / Add-ons (`_addonInfo` When/Does) / Recent tables; names lose the room's prefix
  (`_inRoom` while rendering a room, used by `_name`). "Needs attention" `_attentionHtml`
  groups "<entity> is unavailable" warnings per device.
- Card on dashboards: rooms A to Z (`_zones()` sorts by name; the `order` attribute is unused).
- Editor: `route` setter (no tabs, no crumb link); `_go(view,id)`; draft bar sticky at the
  bottom and hidden when there is nothing to say ("Saved" clears after 5 s). House: rooms A to
  Z, add-on rows closed ("n add-ons ▸"), mode headings → mode page, `_houseListHtml` on phones
  (CSS swaps `.hgrid` / `.hlist` at 700 px). Room: sections Rule / By mode / Add-ons / Devices
  (`_secHtml(key,title,summary,body)`); several light settings → `.seg` tabs (`_tgTab`); the
  light table has headings, A to Z, one-line names, a phone layout. `_modePageHtml` replaces
  the inline mode panel. Times: each day type's changes behind "n changes ▸"; Other rules a
  section; the coming week a days × modes table.
- Checked against the live HA in headless Chromium (local JS routed in for /sb_modes_static/),
  desktop 1440 and phone 390, plus a click-through (sort, mode page, back, browser back).

## Out of service = disabled in HA (0.40.0, 2026-11-01)

User: a way to mark an entity out of service (the Mova bulb won't be replaced for a while) →
"Enabled = out of service". `state_lookup` now returns "disabled" for a registry entity with
`disabled_by` (was None = "does not exist" = an ERROR that blocked saving); validate's
`_check_entity` skips "disabled" silently (other `state_of(x) is None` checks pass it too). The
driver already skips entities with no state. `Runtime._out_of_service()` (in `check()`, every tick)
lists table-named disabled entities [{entity_id, name}] (registry name / device name) →
`binary_sensor.sb_modes_problem` attribute `out_of_service` → Status "Needs attention" gets an
Out of service list (a quiet grey "Out of service" header when that's all there is).
Tests: test_validate entity checks; test_disabled_entity_is_out_of_service.

## Held rooms at a glance (0.41.0, 2026-11-01)

User: "Is there a convenient method for finding all 'held' rooms?" → `_heldHtml(zones)` in the card:
"Held" heading, then one row per held room (name · until when · Release), only while a zone
sensor has `hold`; on the dashboard card under the current mode, on the page above the Rooms table
(not while previewing another mode). Release = `sb_modes.release_zone`. Also documented for
templates: `sensor.sb_modes_deviations` → attribute `deviations`, entries `type: zone_hold`.

## By-hand rooms releasable; off by hand ends when empty (0.41.2, 2026-11-01)
- Card: the Held list also lists rooms with lights changed by hand ("Held or by hand",
  naming the lights) with Release; the room popup's Release works for them too.
- Off by hand now ends after the room has been empty its delay, like any hand change, unless
  the behaviour's empty setting would turn that light ON (`Behaviour.lit_when_empty()`:
  presence_lux vacant/vacant_bright, occupied_vacant vacant, static targets; others none).
  Those still end only on an arrival (the bedtime case: Living Room plants lamp).
  Bookkeeping._end_when_empty picks what ends; zone attribute overrides[e].until_back says
  which rule applies, and the card words it accordingly.
  Why: Laundry Room light switched off at the wall 18:58 stayed "by hand" all evening.
- 0.41.3: the light table (`.tg-wrap`) stacks via a container query (≤600 px of its own width)
  instead of the 700 px viewport media query — a wide screen with the sidebar open gave a
  mode's own rule a narrow column and names shrank to one letter. The @container block must
  come AFTER the base `table.tg` rules (same specificity; order wins).
- 0.41.4: a House cell (or the phone list, or a mode page's room row) opens the room at THAT
  mode (`_goRoomMode`: By mode open, Rule collapsed, scrolled to `[data-box=mode-<id>]`). Before,
  the session-remembered Rule section sat open on top and the user edited the normal rule
  thinking it was Overnight. Context banners (`.ctx`): "<Room> in <Mode> only" in a mode row;
  "<Room> normal rule · used in … — not …" above the normal rule.

## "Default rule" wording; mode default hidden (0.42.0, 2026-11-02)
User found "Normal rule" / "Different rule" confusing. Now: a room's rule for all modes is its
**Default rule** (section title, headings, mode rows show "Default"); a mode's own rule shows as
the rule itself (no "Different rule" label) and a blank House square means the default. The
mode-level `default` (fallback for rooms with no default rule) is no longer shown or editable:
every room has a default rule (new rooms start with hands_off) so it was never reached. Data and
model.resolve are unchanged. The room's default-rule picker no longer offers "Not set".

## Devices popup on the House grid (0.43.0, 2026-11-09)
House squares and the room column no longer spell out every device (an Office square ran to 20 lines).
They show the rule in words (`_sayChoice` without targets) plus a **Devices ›** link (`_detBtn`, only
when the rule sets something). It opens a native `<dialog class="detd">` (`_detHtml` / `_openDet`):
one row per device, one column per targets param (Occupied, Empty…) or per step (+N min), full
settings via `_sayTargetFull` (brightness, K, colour swatch, effect, fade, value). Edit goes to the
room at that mode (`_goRoomMode`) or opens the default rule. `this._det` survives a re-render.
Gotcha: the dialog's class must not be `det` — `.det { all: unset }` (the link) also hit the dialog.
Same release: the Status page's room detail "In <mode>" line uses `_brief` (behaviour name + timings,
no devices) instead of `_describe`; the lights table under it already shows them. The card's own
detail still uses `_describe`.
