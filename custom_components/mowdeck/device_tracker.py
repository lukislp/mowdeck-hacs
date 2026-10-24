"""GPS position of a MowDeck mower (``position`` of the state, WGS84) and of each RTK base
station (``base_station.position`` of a linked mower's state)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant

from .base_station import (
    BaseStationCoordinator,
    BaseStationEntity,
    async_add_base_station_entities,
)
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
    """One tracker per mower and per base station."""
    async_add_mower_entities(
        entry, async_add_entities, lambda coordinator, m: [MowerTracker(coordinator, m)]
    )
    async_add_base_station_entities(
        entry,
        async_add_entities,
        lambda base_stations, station_id: [BaseStationTracker(base_stations, station_id)],
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
        """Heading in degrees (0 = north, clockwise, one decimal) when known, and where the
        position comes from (``rtk``, ``gnss``, ``dock``)."""
        attributes: dict[str, Any] = {}
        heading = self._position.get("heading")
        if isinstance(heading, int | float) and not isinstance(heading, bool):
            attributes["heading"] = round(float(heading), 1)
        source = self.mower_state.get("position_source")
        if self._position and source and source != "unknown":
            attributes["position_source"] = source
        return attributes or None


class BaseStationTracker(BaseStationEntity, TrackerEntity):
    """Where the base station stands, as its linked mower knows it (source type GPS)."""

    _attr_source_type = SourceType.GPS

    def __init__(self, base_stations: BaseStationCoordinator, station_id: str) -> None:
        """Create the tracker."""
        super().__init__(base_stations, station_id, "position")

    @property
    def _position(self) -> dict[str, Any]:
        position = self.base_state.get("position")
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
