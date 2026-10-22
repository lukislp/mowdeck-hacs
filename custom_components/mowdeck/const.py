"""Constants of the MowDeck integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "mowdeck"
MANUFACTURER: Final = "Mammotion"

CONF_API_TOKEN: Final = "api_token"  # noqa: S105 - a config key, not a secret
CONF_POLL_INTERVAL: Final = "poll_interval"
CONF_EXPOSE_EMERGENCY_STOP: Final = "expose_emergency_stop"

DEFAULT_POLL_INTERVAL: Final = 60
MIN_POLL_INTERVAL: Final = 15
MAX_POLL_INTERVAL: Final = 3600
DEFAULT_EXPOSE_EMERGENCY_STOP: Final = True

# While the WebSocket delivers states, polling only discovers new mowers and refreshes
# capabilities and maps.
PUSH_POLL_INTERVAL: Final = timedelta(minutes=5)
# Capabilities and maps change rarely; refetch them at most this often.
STATIC_REFRESH: Final = timedelta(minutes=10)
# Health totals (blade hours, mowing hours, distance, battery cycles) change slowly.
HEALTH_REFRESH: Final = timedelta(minutes=15)
# Schedules, their runs and the rain decision; also refreshed right after schedule actions.
SCHEDULER_REFRESH: Final = timedelta(minutes=5)
# A state older than this counts as offline (Telemetry's own stale flag also counts).
STALE_AFTER: Final = timedelta(minutes=5)

# The value of the area select that means "every area of the map".
ALL_AREAS: Final = "all"

# Unnamed areas read "Area N" in map order, as in MowDeck and the vendor app (same rule as
# mowdeck_contracts.v1.area_display_name, which this integration cannot import).
AREA_FALLBACK: Final = {"en": "Area {n}", "de": "Bereich {n}"}

EVENT_COMMAND_RESULT: Final = "mowdeck_command_result"

ISSUE_REALTIME_UNAUTHORIZED: Final = "realtime_unauthorized"
# Repairs issue of a polled MowDeck service that answers 403 ("<service>_forbidden").
ISSUE_FORBIDDEN: Final = "{service}_forbidden"

# Limits of the Fleet command API (CommandRequest in apps/web/openapi/fleet.json).
API_BLADE_HEIGHT_MIN_MM: Final = 10
API_BLADE_HEIGHT_MAX_MM: Final = 120
API_SPEED_MAX_MPS: Final = 2.0
# SetDriveSpeed.speed_mps (the vendor's manual-drive speed setting).
API_DRIVE_SPEED_MAX_MPS: Final = 5.0

# Speeds are shown and sent with two decimals (m/s); finer values are float noise.
SPEED_DECIMALS: Final = 2
SPEED_STEP_MPS: Final = 0.01
# Slowest mowing speed when the capabilities name no minimum (MowerCapabilities.min_speed_mps).
DEFAULT_MIN_SPEED_MPS: Final = 0.1

# The API token permission each command needs (Fleet's COMMAND_RELATION); a 403 names it.
COMMAND_PERMISSION: Final = {
    "emergency_stop": "mower.view",
    "set_drive_speed": "mower.drive",
    "firmware_update": "fleet.manage",
}
DEFAULT_COMMAND_PERMISSION: Final = "mower.control"

# Relations asked per mower with POST /identity/me/permissions, and the token permission that
# grants each one (named in errors and in the README).
CAN_CONTROL: Final = "can_control"
CAN_DRIVE: Final = "can_drive"
CAN_MANAGE_MOWERS: Final = "can_manage_mowers"
CAN_EDIT_SCHEDULE: Final = "can_edit_schedule"
# Outcomes of a schedule run (ScheduleRunOutcome in apps/web/openapi/scheduler.json).
RUN_OUTCOMES: Final = [
    "started",
    "skipped_rain",
    "skipped_permission",
    "skipped_window",
    "skipped_mower_busy",
    "skipped_manual",
    "failed",
]
# Verdicts of the rain decision (RainDecisionOut.verdict).
RAIN_VERDICTS: Final = ["run", "hold", "unknown_run", "unknown_hold", "no_location", "disabled"]
RELATION_PERMISSION: Final = {
    CAN_CONTROL: "mower.control",
    CAN_DRIVE: "mower.drive",
    CAN_MANAGE_MOWERS: "fleet.manage",
    CAN_EDIT_SCHEDULE: "schedule.edit",
}
