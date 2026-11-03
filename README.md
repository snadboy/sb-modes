# SB Modes

A Home Assistant integration that runs the house's lighting from one table instead of
scenes and per-room automations. The house is always in one **mode** (Morning, Day,
Evening, Overnight…); each **room** has a **rule** in charge of its lights, and any
number of **add-ons** that act on top of it. The table is validated on every save, so a
mode, room or entity can never be left unhandled.

## Concepts

- **Modes and times.** Modes change on a schedule per **day type** (Workdays, Days off,
  Vacation…). A day type is an [SB Scheduler](https://github.com/snadboy/sb-scheduler)
  calendar, so holidays and trips change the times on their own. Times can be clock
  times or sunrise / sunset ± an offset. A mode can also be set by hand, for a while or
  until the next scheduled change.
- **Rules.** One per room is in charge at a time: *off when empty*, *occupied / empty*,
  *presence + light level*, *nightlight, then dark*, *static*, or *hands off*. A room has a
  **default rule** and may have its own rule in a given mode (shown in that mode's square on
  the House page; a blank square means the default).
- **Add-ons.** Extra actions on top of the rule, each with a **trigger**, an optional
  **When** (modes, days, a time window) and steps that set lights (or "at least"), hold
  or release the room, or run a Home Assistant script. Triggers: a time of day, a mode
  beginning or ending, a device reaching a state, a calendar event starting or ending
  (± an offset — "an hour before the #vac event ends"), a TV's state (*while* playing…), a
  button press. Steps can also set the house mode or resume the schedule, and times and
  steps can vary at random. Built-in add-ons: **follow** a leader light, **auto-off** for
  lamps turned on by hand, and **replay**, where lights repeat what they did a week ago
  (a lived-in look while away).
- **Rooms can be switched off.** A switch on the House grid or the room page makes SB
  Modes leave that room alone in every mode (no rule, no add-ons); its settings are kept.
- **People win.** A light changed by hand is left alone until the room has been empty
  for its delay. A light turned off by hand is the same, except one the room's empty
  setting would turn on (a nightlight lamp): that stays off until someone comes back. A room
  can be **held** (for minutes, until the next mode, or until released).
- **Out of service.** Disable a device's entity in Home Assistant and SB Modes skips it everywhere,
  with no error or warning, keeping its settings; Status lists it so it isn't forgotten. Enable it
  again (with the same entity ID) to bring it back.
- **Presence.** Any of a room's sensors counts; an offline sensor never blocks "empty";
  a TV that is playing counts as someone there.
- **Light groups** are checked and set lamp by lamp, and a light that is off is turned
  on before it is given a colour (Matter lights ignore colour while off).

## What you get

- **SB Modes** in the sidebar: *Status*, *House* and *Times*, with a page per room and per
  mode (← back to House) and a **?** legend. Edits go into a shared draft — nothing reaches
  the house until you review and save (the bar at the bottom shows while there are unsaved
  changes), and every save is a version you can restore. Sections start closed and stay as
  you leave them for the browser session; rooms, lights and sensors are A to Z, and the
  larger tables sort by a tap on a heading.
  - *House*: rooms × modes, a row per room's rule and (opened) a row per add-on, marked by
    the modes it runs in; on a phone, a list of what each room does in a chosen mode.
  - *Room*: the rule (its light settings as tabs), by mode, the add-ons, the devices.
  - *Mode*: name and icon, when it begins, what each room does in it, its default rule.
  - *Times*: the day types and their times, other rules, and the coming week.
- A **card** (`custom:sb-modes-card`) with the current mode, a *Held* line (only while a room is
  held: each held room, until when, and Release), each room's live status, a
  room dialog (the row's ⋮): hold the room (for N minutes, until released, until the mode
  changes) or release it, then set its lights live — a slider for the dimmable ones, All off /
  All on for the ones that only switch; Cancel puts the lights and the hold back as they were, lights that open their own controls when tapped, and an activity log; entries also go
  to the Logbook.
- Entities: `select.sb_modes_mode` (the mode), `sensor.sb_modes_<room>` (what the room is
  doing, its add-ons and their next runs), `binary_sensor.sb_modes_problem` (the saved
  table no longer validates — also raised as a Repairs issue).
- Actions: `sb_modes.set_mode`, `resume_schedule`, `hold_zone`, `set_level`, `switch_lights`, `set_lights`, `release_zone`,
  `get_config`, `check_config`, `save_config`. Event: `sb_modes_mode_changed`.

## Install

Requires Home Assistant 2026.6 or later and
[SB UI Kit](https://github.com/snadboy/sb-ui-kit); day types use
[SB Scheduler](https://github.com/snadboy/sb-scheduler) calendars.

**Marketplace (Home Assistant 2026.11+):** *Settings → Marketplace*, connect GitHub
(custom repositories need it), add `https://github.com/snadboy/sb-modes` as a custom
*Integration* repository, then download **SB Modes** and restart Home Assistant. Updates then
appear under *Settings → Updates*. A HACS install carries over on its own when Home
Assistant is upgraded.

**HACS (2026.10 and older):** HACS → ⋮ → *Custom repositories* → add the same URL as an
*Integration*, download **SB Modes** and restart.

**Manual:** copy `custom_components/sb_modes` into your `config/custom_components/` and
restart.

Then *Settings → Devices & services → Add integration → SB Modes*, and open **SB Modes**
in the sidebar to build the table.

## Tests

```bash
python3 tests/test_validate.py                       # pure checks, no Home Assistant needed
uv venv .venv --python 3.14
uv pip install --python .venv/bin/python pytest-homeassistant-custom-component
.venv/bin/pytest                                     # everything, inside a real HA core
```
