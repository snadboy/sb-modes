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
  normal rule and may use a different one in a given mode.
- **Add-ons.** Extra actions on top of the rule, each with a **trigger**, an optional
  **When** (modes, days, a time window) and steps that set lights (or "at least"), hold
  or release the room, or run a Home Assistant script. Triggers: a time of day, a mode
  beginning or ending, a device reaching a state, a calendar event starting or ending
  (± an offset — "an hour before the #vac event ends"), a TV's state (*while* playing…), a
  button press. Built-in add-ons: **follow** a leader light, **auto-off** for lamps
  turned on by hand.
- **People win.** A light changed by hand is left alone until the room has been empty
  for its delay; a light turned off by hand stays off until someone comes back. A room
  can be **held** (for minutes, until the next mode, or until released).
- **Presence.** Any of a room's sensors counts; an offline sensor never blocks "empty";
  a TV that is playing counts as someone there.
- **Light groups** are checked and set lamp by lamp, and a light that is off is turned
  on before it is given a colour (Matter lights ignore colour while off).

## What you get

- **SB Modes** in the sidebar: a *Status* view and an *Edit* view. The editor works on a
  shared draft — nothing reaches the house until you review and save, and every save is
  a version you can restore.
  - *House*: rooms × modes, a row per room's rule and a row per add-on, marked by the
    modes it runs in.
  - *Room*: the rule, the add-ons, the devices.
  - *Times*: the day types and their times, other rules, and the coming week.
- A **card** (`custom:sb-modes-card`) with the current mode, each room's live status, a
  hold / release menu, and an activity log; entries also go to the Logbook.
- Entities: `select.sb_modes_mode` (the mode), `sensor.sb_modes_<room>` (what the room is
  doing, its add-ons and their next runs), `binary_sensor.sb_modes_problem` (the saved
  table no longer validates — also raised as a Repairs issue).
- Actions: `sb_modes.set_mode`, `resume_schedule`, `hold_zone`, `release_zone`,
  `get_config`, `check_config`, `save_config`. Event: `sb_modes_mode_changed`.

## Install

Requires Home Assistant 2026.6 or later and
[SB UI Kit](https://github.com/snadboy/sb-ui-kit); day types use
[SB Scheduler](https://github.com/snadboy/sb-scheduler) calendars.

**HACS:** HACS → ⋮ → *Custom repositories* → add `https://github.com/snadboy/sb-modes`
as an *Integration*, then download **SB Modes** and restart Home Assistant.

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
