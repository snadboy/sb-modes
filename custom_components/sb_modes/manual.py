"""Was a light's change made by hand? Pure — no Home Assistant imports, tested offline.

Every state change carries a context: its id, the user who caused it (if any) and the
context it descends from (if any). SB Modes remembers the ids of the contexts of its own
calls. From those:

- one of OUR contexts, or descended from one        → ours, not manual
- carries a user (app, dashboard, voice)              → manual ("person")
- descends from another context (an automation or a
  script reacting to something, e.g. Ambience - Kitchen) → not manual
- no user, no parent: the device itself reported it — a wall switch, the Lutron app —
  UNLESS we commanded this light within the last few seconds: devices often report the
  level they reached a moment after HA's own write, under a fresh context → not manual
- otherwise                                           → manual ("device")
"""

from __future__ import annotations

ECHO_SECONDS = 15        # a device report this soon after our own command is its echo

UNAVAILABLE = ("unavailable", "unknown")


def classify(ctx_id: str | None, user_id: str | None, parent_id: str | None,
             own: set[str] | frozenset[str], since_our_command: float | None) -> str | None:
    """"person" or "device" for a manual change, None when it is not one."""
    if ctx_id in own or (parent_id is not None and parent_id in own):
        return None
    if user_id:
        return "person"
    if parent_id:
        return None
    if since_our_command is not None and since_our_command < ECHO_SECONDS:
        return None
    return "device"


def significant(old_state: str | None, new_state: str | None, old_bri: int | None, new_bri: int | None) -> bool:
    """A change worth treating as someone's intent: on/off flipped, or the level moved."""
    if old_state is None or new_state is None or old_state in UNAVAILABLE or new_state in UNAVAILABLE:
        return False
    if old_state != new_state:
        return True
    if new_state == "on" and old_bri is not None and new_bri is not None:
        return abs(new_bri - old_bri) > 3
    return False
