"""Health totals of the mowers (``GET /telemetry/mowers/{id}/health``) in their own coordinator.

Blade hours, mowing hours, distance and battery cycles change slowly, so they are polled every
``HEALTH_REFRESH`` and never hold up the live states.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback

from .const import HEALTH_REFRESH
from .coordinator import MowDeckCoordinator, MowDeckServiceCoordinator

if TYPE_CHECKING:
    from . import MowDeckConfigEntry


@dataclass
class HealthData:
    """Health totals by mower id (missing before the mower's first telemetry)."""

    totals: dict[str, dict[str, Any]] = field(default_factory=dict)


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
        for mower_id in list(self.state.data.mowers):
            totals = await self.client.get_health(mower_id)
            if totals is not None:
                data.totals[mower_id] = totals
        return data

    @callback
    def async_set_totals(self, mower_id: str, totals: dict[str, Any]) -> None:
        """New totals from an action (the blade reset answers them)."""
        self.data.totals[mower_id] = dict(totals)
        self.async_update_listeners()
