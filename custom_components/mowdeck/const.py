"""Constants of the MowDeck integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "mowdeck"
MANUFACTURER: Final = "Mammotion"

CONF_API_TOKEN: Final = "api_token"  # noqa: S105 - a config key, not a secret
CONF_POLL_INTERVAL: Final = "poll_interval"
CONF_EXPOSE_EMERGENCY_STOP: Final = "expose_emergency_stop"
CONF_PERSISTENT_SEVERITY: Final = "persistent_notifications"

DEFAULT_POLL_INTERVAL: Final = 60
MIN_POLL_INTERVAL: Final = 15
MAX_POLL_INTERVAL: Final = 3600
DEFAULT_EXPOSE_EMERGENCY_STOP: Final = True
# Mirror MowDeck notifications as persistent notifications from this severity on.
PERSISTENT_OFF: Final = "off"
DEFAULT_PERSISTENT_SEVERITY: Final = PERSISTENT_OFF

# While the WebSocket delivers states, polling only discovers new mowers and refreshes
# capabilities and maps.
PUSH_POLL_INTERVAL: Final = timedelta(minutes=5)
# Capabilities and maps change rarely; refetch them at most this often.
STATIC_REFRESH: Final = timedelta(minutes=10)
# Health totals (blade hours, mowing hours, distance, battery cycles) change slowly.
HEALTH_REFRESH: Final = timedelta(minutes=15)
# Schedules, their runs and the rain decision; also refreshed right after schedule actions.
SCHEDULER_REFRESH: Final = timedelta(minutes=5)
# The newest Vision proposal per mower; changes arrive live over the WebSocket.
VISION_REFRESH: Final = timedelta(minutes=10)
# Heatmap layers are a query over a week of positions: refreshed rarely.
HEATMAP_REFRESH: Final = timedelta(minutes=30)
# The map image is rendered again at most this often, and the running job's path is read at
# most this often.
MAP_MIN_INTERVAL: Final = timedelta(seconds=10)
PATH_REFRESH: Final = timedelta(seconds=30)
# A state older than this counts as offline (Telemetry's own stale flag also counts).
STALE_AFTER: Final = timedelta(minutes=5)

# The value of the area select that means "every area of the map", and the one shown while
# the area switches select several (but not all) areas.
ALL_AREAS: Final = "all"
SEVERAL_AREAS: Final = "selection"

# MowPattern (apps/web/openapi/fleet.json): the native patterns and their limits; the
# defaults apply while a mower has no saved mowing defaults.
PATTERNS: Final = ["stripes", "diagonal", "checkerboard", "edge_only", "random_angle"]
DEFAULT_PATTERN: Final = {
    "name": "stripes",
    "angle_deg": 0.0,
    "border_laps": 1,
    "spacing_m": None,
}
API_ANGLE_MAX_DEG: Final = 180
API_BORDER_LAPS_MAX: Final = 4
API_SPACING_MIN_M: Final = 0.05
API_SPACING_MAX_M: Final = 2.0
# Heatmap layers and cell sizes (GET /telemetry/mowers/{id}/heatmap).
HEATMAP_LAYERS: Final = ["coverage", "rtk", "wifi", "mnet"]
HEATMAP_CELLS: Final = [0.5, 1.0, 2.0]

# Unnamed areas read "Area N" in map order, as in MowDeck and the vendor app (same rule as
# mowdeck_contracts.v1.area_display_name, which this integration cannot import).
AREA_FALLBACK: Final = {"en": "Area {n}", "de": "Bereich {n}"}

EVENT_COMMAND_RESULT: Final = "mowdeck_command_result"
EVENT_NOTIFICATION: Final = "mowdeck_notification"
EVENT_VISION_PROPOSAL: Final = "mowdeck_vision_proposal"

# NotificationSeverity (apps/web/openapi/notifications.json), lowest first.
SEVERITIES: Final = ["info", "warning", "critical"]
# NotificationKind: the event types of the notification event entity.
NOTIFICATION_KINDS: Final = [
    "mowing_finished",
    "mower_stuck",
    "battery_low",
    "mower_offline",
    "mower_online",
    "job_started",
    "mower_lifted",
    "mower_left_dock_unexpectedly",
    "geofence_exit",
    "blade_maintenance",
    "vision_proposal",
    "vision_auto_proposal",
    "firmware_update_available",
    "firmware_update_finished",
    "firmware_update_failed",
    "test",
]
# CommandState (apps/web/openapi/fleet.json): the event types of the command event entity.
COMMAND_STATES: Final = ["pending", "accepted", "rejected", "executed", "expired", "failed"]

ISSUE_REALTIME_UNAUTHORIZED: Final = "realtime_unauthorized"
# MowDeck's API token page (the Repairs issues about the token link to it).
TOKEN_PAGE: Final = "/settings/api-tokens"  # noqa: S105 - a web app path, not a secret
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
    "spot_mow": "mower.drive",
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
# Typed parts of MowerState (apps/web/openapi/telemetry.json): GnssSolution, NetworkKind,
# SelfCheckResult and MowerErrorCategory; "unknown" becomes the unknown state.
GNSS_SOLUTIONS: Final = ["none", "single", "float", "fixed"]
NETWORK_KINDS: Final = ["wifi", "mobile", "ble", "none"]
SELF_CHECK_RESULTS: Final = ["ok", "blocked"]
ERROR_CATEGORIES: Final = [
    "trapped",
    "obstacle",
    "outside",
    "wheel_slip",
    "lift_tilt",
    "collision",
    "battery",
    "blade_motor",
    "rain",
    "positioning",
    "communication",
    "docking",
    "firmware",
    "other",
]
# What a category of a newer MowDeck that this version does not know yet shows as.
ERROR_CATEGORY_FALLBACK: Final = "other"
# Severity of an explained error (ErrorSeverity of the error lexicon, ADR 0067 in
# lukislp/mowdeck).
ERROR_SEVERITIES: Final = ["info", "warning", "error", "critical"]
# A failed read of an error explanation is retried after this long; a 404 (a MowDeck without
# the error lexicon) is not retried before the error code changes.
ERROR_EXPLAIN_RETRY: Final = timedelta(minutes=5)
# Verdicts of the rain decision (RainDecisionOut.verdict).
RAIN_VERDICTS: Final = ["run", "hold", "unknown_run", "unknown_hold", "no_location", "disabled"]
RELATION_PERMISSION: Final = {
    CAN_CONTROL: "mower.control",
    CAN_DRIVE: "mower.drive",
    CAN_MANAGE_MOWERS: "fleet.manage",
    CAN_EDIT_SCHEDULE: "schedule.edit",
}
