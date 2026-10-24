"""Health totals of the mowers (``GET /telemetry/mowers/{id}/health``) and today's totals
(``.../health/daily``, the current UTC day) in their own coordinator.

Blade hours, mowing hours, distance and battery cycles change slowly, so they are polled every
``HEALTH_REFRESH`` and never hold up the live states.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import HEALTH_REFRESH
from .coordinator import MowDeckCoordinator, MowDeckServiceCoordinator

if TYPE_CHECKING:
    from . import MowDeckConfigEntry


def blade_threshold(health: dict[str, Any]) -> tuple[Any, str]:
    """The one blade change threshold in effect and its source (``custom`` set in MowDeck,
    ``device`` the mower's own warning, ``default`` MowDeck's default), as MowDeck names it in
    ``blade_hours_threshold_effective``. MowDeck before that field: the threshold set in MowDeck,
    else the mower's own warning while its own counter is shown, else MowDeck's threshold."""
    effective = health.get("blade_hours_threshold_effective")
    source = health.get("blade_hours_threshold_source")
    if isinstance(effective, int | float) and not isinstance(effective, bool) and source:
        return effective, str(source)
    if health.get("blade_hours_threshold_custom"):
        return health.get("blade_hours_threshold"), "custom"
    warn = health.get("device_blade_warn_hours")
    if health.get("blade_source") == "device" and warn is not None:
        return warn, "device"
    return health.get("blade_hours_threshold"), "default"


@dataclass
class HealthData:
    """Health totals by mower id (missing before the mower's first telemetry)."""

    totals: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Totals of the current UTC day (MowDeck counts days in UTC).
    today: dict[str, dict[str, Any]] = field(default_factory=dict)


class HealthCoordinator(MowDeckServiceCoordinator[HealthData]):
    """Polls the health totals of every visible mower."""

    service = "health"
    permission = "mower.view"

    def __init__(
        self, hass: HomeAssistant, entry: MowDeckConfigEntry, state: MowDeckCoordinator
    ) -> None:
        """Create the coordinator (refreshed every ``HEALTH_REFRESH``)."""
        super().__init__(hass, entry, state, update_interval=HEALTH_REFRESH)
        self.data = HealthData()

    async def _async_fetch(self) -> HealthData:
        data = HealthData()
        day = dt_util.utcnow().date().isoformat()
        for mower_id in list(self.state.data.mowers):
            totals = await self.client.get_health(mower_id)
            if totals is not None:
                data.totals[mower_id] = totals
            today = await self.client.get_health_daily(mower_id, day)
            if today is not None:
                data.today[mower_id] = today
        return data

    @callback
    def async_set_totals(self, mower_id: str, totals: dict[str, Any]) -> None:
        """New totals from an action (the blade reset answers them)."""
        self.data = HealthData(totals={**self.data.totals}, today=self.data.today)
        self.data.totals[mower_id] = dict(totals)
        self.async_update_listeners()
