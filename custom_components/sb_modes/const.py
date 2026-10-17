"""Constants for SB Modes."""

DOMAIN = "sb_modes"

STORE_KEY = f"{DOMAIN}.config"
STORE_VERSION = 1

EVENT_MODE_CHANGED = f"{DOMAIN}_mode_changed"    # {"from": mode id | None, "to": mode id}

STARTUP_SETTLE_SECONDS = 20       # after EVENT_HOMEASSISTANT_STARTED, before behaviours start
CHECK_SECONDS = 60                # how often a saved table is re-validated (entities, areas)
ALERT_GRACE_SECONDS = 120         # an error must last this long before a Repairs issue (boot races)
ISSUE_ID = "config_problem"

PANEL_URL = "sb-modes"
STATIC_URL = "/sb_modes_static"
