"""Presence: is anyone in a zone, and since when has it been empty.

A zone's presence sources are its occupancy sensors (binary_sensor: on / off; anything else,
e.g. unavailable, makes the zone neither occupied nor vacant) and, when it has an overlay, the
overlay's media player: playing / paused / idle / buffering / on mean someone is watching
(the Family Room's TV-area sensor does not see a seated viewer). An occupancy sensor that is
offline is left out while another source reports (vacant_since). A media player that is off
or dropped out (unavailable / unknown — the TV does that several times an evening) only means
"not watching", never "unknown", so it can't block vacancy.

The empty-since time of a media player is when it LEFT a watching state (Runtime.media_left,
kept by the overlay), not its last_changed: the drop-outs change that all night long.
"""

from __future__ import annotations

from datetime import datetime

from homeassistant.core import HomeAssistant, State

MEDIA_WATCHING = ("playing", "paused", "idle", "buffering", "on")


def is_media(entity_id: str) -> bool:
    return entity_id.startswith("media_player.")


def present(st: State | None) -> bool | None:
    """True someone is there, False no one, None can't tell (an unavailable sensor)."""
    if st is None:
        return None
    if is_media(st.entity_id):
        return st.state in MEDIA_WATCHING
    if st.state == "on":
        return True
    if st.state == "off":
        return False
    return None


def occupied(hass: HomeAssistant, sources: list[str]) -> bool:
    return any(present(hass.states.get(s)) is True for s in sources)


def vacant_since(hass: HomeAssistant, sources: list[str], media_left: dict[str, datetime]) -> datetime | None:
    """When the zone emptied: every source that is reporting says no one (None otherwise).

    A sensor that is offline (unavailable / unknown) is left out, as long as at least one other
    source reports: one dead battery must not keep a room 'occupied' for ever. It still bounds
    the time — the empty clock starts no earlier than when it went offline, in case it was the
    one seeing someone. All sensors offline (and no media player): can't tell → None."""
    if not sources:
        return None
    times, reporting = [], 0
    for s in sources:
        st = hass.states.get(s)
        if is_media(s):
            if st is not None and st.state in MEDIA_WATCHING:
                return None
            reporting += 1
            if s in media_left:
                times.append(media_left[s])
            continue                                     # never watched since start: no say in "since"
        p = present(st)
        if p is True:
            return None
        if p is False:
            reporting += 1
            times.append(st.last_changed)
        elif st is not None:
            times.append(st.last_changed)                # offline: when it went offline
    if not reporting:
        return None
    if not times:                                        # only a media player, never watched
        st = hass.states.get(sources[0])
        return st.last_changed if st is not None else None
    return max(times)
