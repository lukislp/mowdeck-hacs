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
# A state older than this counts as offline (Telemetry's own stale flag also counts).
STALE_AFTER: Final = timedelta(minutes=5)

# The value of the area select that means "every area of the map".
ALL_AREAS: Final = "all"

# Unnamed areas read "Area N" in map order, as in MowDeck and the vendor app (same rule as
# mowdeck_contracts.v1.area_display_name, which this integration cannot import).
AREA_FALLBACK: Final = {"en": "Area {n}", "de": "Bereich {n}"}

EVENT_COMMAND_RESULT: Final = "mowdeck_command_result"

ISSUE_REALTIME_UNAUTHORIZED: Final = "realtime_unauthorized"

# Limits of the Fleet command API (CommandRequest in apps/web/openapi/fleet.json).
API_BLADE_HEIGHT_MIN_MM: Final = 10
API_BLADE_HEIGHT_MAX_MM: Final = 120
API_SPEED_MAX_MPS: Final = 2.0
