"""Heatmap layers of the mowers (``GET /telemetry/mowers/{id}/heatmap``, the last 7 days) in
their own coordinator.

The coverage layer is read for every mower (its missed spots give the missed area sensor);
the other layers (RTK, Wi-Fi, mobile network) only while their image entity is enabled, so a
disabled entity costs MowDeck nothing. A layer is a database query over a week of positions,
so it is refreshed every ``HEATMAP_REFRESH``. A mower without a map (404 ``no-map-yet``) has
no layers.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback

from .api import MowDeckNotFoundError
from .const import HEATMAP_REFRESH
from .coordinator import MowDeckCoordinator, MowDeckServiceCoordinator

if TYPE_CHECKING:
    from . import MowDeckConfigEntry

COVERAGE = "coverage"


@dataclass
class HeatmapData:
    """The layers read at the last refresh, keyed by ``(mower id, layer)``."""

    layers: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)


def missed_area(heatmap: dict[str, Any]) -> float:
    """The area of the coverage layer's missed spots in square metres."""
    return sum(
        float(gap.get("area_m2") or 0) for gap in heatmap.get("gaps") or [] if isinstance(gap, dict)
    )


class HeatmapCoordinator(MowDeckServiceCoordinator[HeatmapData]):
    """Reads the coverage layer of every mower and the layers enabled entities want."""

    service = "heatmap"
    permission = "mower.view"

    def __init__(
        self, hass: HomeAssistant, entry: MowDeckConfigEntry, state: MowDeckCoordinator
    ) -> None:
        """Create the coordinator (refreshed every ``HEATMAP_REFRESH``)."""
        super().__init__(hass, entry, state, update_interval=HEATMAP_REFRESH)
        self.data = HeatmapData()
        self._wanted: dict[str, dict[str, int]] = {}

    async def _async_fetch(self) -> HeatmapData:
        data = HeatmapData()
        for mower_id in list(self.state.data.mowers):
            for layer in self._layers(mower_id):
                try:
                    data.layers[(mower_id, layer)] = await self.client.get_heatmap(mower_id, layer)
                except MowDeckNotFoundError:
                    break
        return data

    def _layers(self, mower_id: str) -> list[str]:
        wanted = [layer for layer, n in self._wanted.get(mower_id, {}).items() if n > 0]
        return [COVERAGE, *sorted(layer for layer in wanted if layer != COVERAGE)]

    @callback
    def async_want(self, mower_id: str, layer: str) -> Callable[[], None]:
        """An entity shows ``layer`` of the mower: read it from now on (at once when it is
        missing). Returns the function that ends the interest."""
        counts = self._wanted.setdefault(mower_id, {})
        counts[layer] = counts.get(layer, 0) + 1
        if (mower_id, layer) not in self.data.layers:
            self.config_entry.async_create_background_task(
                self.hass, self.async_request_refresh(), "mowdeck heatmap layer"
            )

        @callback
        def release() -> None:
            counts[layer] -= 1

        return release

    def layer(self, mower_id: str, layer: str) -> dict[str, Any] | None:
        """A layer of the mower as last read, if any."""
        return self.data.layers.get((mower_id, layer))
