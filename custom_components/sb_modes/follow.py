"""Followers: lights that track a leader light, in every mode.

A zone's `follow` = {"leader": light, "bands": [{"min_pct": n, "targets": {…}}, …]}. Whenever the
leader changes — by hand, by a mode, by anything — the band its brightness falls in (off counts
as 0 %) sets the followers. Bands are matched highest `min_pct` first.

Two exceptions keep it from fighting the zone's own behaviour:
- when SB Modes' own command that changed the leader ALSO set any follower (Overnight's "dark"
  turns the ceiling and the aux lights off together), following stands aside;
- followers changed by hand, and held zones, are skipped (as everywhere: Runtime.apply).
The kitchen ceiling → aux lights (the old "Ambience - Kitchen").
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, callback
from homeassistant.helpers.event import async_track_state_change_event

from .manual import UNAVAILABLE

if TYPE_CHECKING:
    from .runtime import Runtime


def leader_pct(state) -> int | None:
    """The leader's brightness as 0–100 (off → 0); None while unavailable."""
    if state is None or state.state in UNAVAILABLE:
        return None
    if state.state != "on":
        return 0
    b = state.attributes.get("brightness")
    return 100 if b is None else round(b * 100 / 255)


def band_for(bands: list[dict], pct: int) -> int | None:
    """Index of the band `pct` falls in (highest min_pct first), or None."""
    order = sorted(range(len(bands)), key=lambda i: -bands[i].get("min_pct", 0))
    for i in order:
        if pct >= bands[i].get("min_pct", 0):
            return i
    return None


class Follower:
    def __init__(self, rt: Runtime, zone: dict, spec: dict) -> None:
        self.rt = rt
        self.zone_id = zone["id"]
        self.leader = spec["leader"]
        self.bands = spec.get("bands") or []
        self.followers = {e for b in self.bands for e in (b.get("targets") or {})}
        self._unsub: CALLBACK_TYPE | None = None

    @callback
    def start(self) -> None:
        self._unsub = async_track_state_change_event(self.rt.hass, [self.leader], self._changed)

    @callback
    def stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _changed(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        pct, was = leader_pct(new), leader_pct(old)
        if pct is None or was is None:
            return                                       # to or from unavailable: not a real change
        if pct == was and (old.state == new.state):
            return
        leader = new.name or self.leader
        if self.followers & self.rt.targets_of(new.context.id):
            # our own command already set the followers
            self.rt.log("skipped", self.zone_id, f"{leader} {pct}% — following stands aside: the same command set the followers")
            return
        i = band_for(self.bands, pct)
        if i is not None:
            self.rt.apply(self.bands[i].get("targets") or {}, f"follows {leader} at {pct}%", self.zone_id)
