"""Numbers of a MowDeck mower: blade height and speed, ranges from the capabilities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfLength, UnitOfSpeed
from homeassistant.core import HomeAssistant

from .const import API_BLADE_HEIGHT_MAX_MM, API_BLADE_HEIGHT_MIN_MM, API_SPEED_MAX_MPS
from .entity import MowDeckEntity, async_add_mower_entities

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator

PARALLEL_UPDATES = 1

MIN_SPEED_MPS = 0.1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MowDeckConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Blade height and speed of every mower."""
    async_add_mower_entities(
        entry,
        async_add_entities,
        lambda coordinator, m: [BladeHeightNumber(coordinator, m), SpeedNumber(coordinator, m)],
    )


class BladeHeightNumber(MowDeckEntity, NumberEntity):
    """Cutting height in millimetres (``set_blade_height``)."""

    entity_description = NumberEntityDescription(
        key="blade_height",
        device_class=NumberDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        native_step=1,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
    )

    def __init__(self, coordinator: MowDeckCoordinator, mower_id: str) -> None:
        """Create the number."""
        super().__init__(coordinator, mower_id, "blade_height")

    def _range(self) -> tuple[int, int]:
        caps = self.coordinator.data.capabilities.get(self.mower_id, {})
        bounds = caps.get("blade_height_range_mm")
        if isinstance(bounds, list | tuple) and len(bounds) == 2:
            low = max(API_BLADE_HEIGHT_MIN_MM, int(bounds[0]))
            high = min(API_BLADE_HEIGHT_MAX_MM, int(bounds[1]))
            if low <= high:
                return low, high
        return API_BLADE_HEIGHT_MIN_MM, API_BLADE_HEIGHT_MAX_MM

    @property
    def native_min_value(self) -> float:
        """Lower bound from the capabilities (API limit otherwise)."""
        return self._range()[0]

    @property
    def native_max_value(self) -> float:
        """Upper bound from the capabilities (API limit otherwise)."""
        return self._range()[1]

    @property
    def native_value(self) -> float | None:
        """The reported blade height."""
        value = self.mower_state.get("blade_height_mm")
        return float(value) if isinstance(value, int | float) else None

    async def async_set_native_value(self, value: float) -> None:
        """Send ``set_blade_height``."""
        await self.async_send_command({"kind": "set_blade_height", "mm": round(value)})


class SpeedNumber(MowDeckEntity, NumberEntity):
    """Driving speed while mowing in metres per second (``set_speed``)."""

    entity_description = NumberEntityDescription(
        key="speed",
        device_class=NumberDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        native_step=0.05,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
    )

    def __init__(self, coordinator: MowDeckCoordinator, mower_id: str) -> None:
        """Create the number."""
        super().__init__(coordinator, mower_id, "speed")

    @property
    def native_min_value(self) -> float:
        """Slowest settable speed."""
        return MIN_SPEED_MPS

    @property
    def native_max_value(self) -> float:
        """Fastest speed of the capabilities, at most the API limit."""
        caps = self.coordinator.data.capabilities.get(self.mower_id, {})
        top = caps.get("max_speed_mps")
        if isinstance(top, int | float) and top >= MIN_SPEED_MPS:
            return min(float(top), API_SPEED_MAX_MPS)
        return API_SPEED_MAX_MPS

    @property
    def native_value(self) -> float | None:
        """The reported speed."""
        value = self.mower_state.get("speed_mps")
        return float(value) if isinstance(value, int | float) else None

    async def async_set_native_value(self, value: float) -> None:
        """Send ``set_speed``."""
        await self.async_send_command({"kind": "set_speed", "mps": round(value, 2)})
