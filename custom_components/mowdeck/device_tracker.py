"""GPS position of a MowDeck mower (``position`` of the state, WGS84)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant

from .entity import MowDeckEntity, async_add_mower_entities

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MowDeckConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One tracker per mower."""
    async_add_mower_entities(
        entry, async_add_entities, lambda coordinator, m: [MowerTracker(coordinator, m)]
    )


class MowerTracker(MowDeckEntity, TrackerEntity):
    """Reports latitude and longitude with source type GPS."""

    _attr_source_type = SourceType.GPS

    def __init__(self, coordinator: MowDeckCoordinator, mower_id: str) -> None:
        """Create the tracker."""
        super().__init__(coordinator, mower_id, "position")

    @property
    def _position(self) -> dict[str, Any]:
        position = self.mower_state.get("position")
        return position if isinstance(position, dict) else {}

    @property
    def latitude(self) -> float | None:
        """Latitude in degrees."""
        value = self._position.get("lat")
        return float(value) if isinstance(value, int | float) else None

    @property
    def longitude(self) -> float | None:
        """Longitude in degrees."""
        value = self._position.get("lon")
        return float(value) if isinstance(value, int | float) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Heading in degrees (0 = north, clockwise), when known."""
        heading = self._position.get("heading")
        return {"heading": heading} if heading is not None else None
