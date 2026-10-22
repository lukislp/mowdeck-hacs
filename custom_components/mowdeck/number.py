"""Numbers of a MowDeck mower: blade height, mowing speed and the manual-drive speed setting.

Ranges come from the mower's capabilities, limited to what the Fleet command API accepts.
Speeds are read, shown and sent with two decimals (``SPEED_DECIMALS``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, Platform, UnitOfLength, UnitOfSpeed
from homeassistant.core import HomeAssistant

from .const import (
    API_BLADE_HEIGHT_MAX_MM,
    API_BLADE_HEIGHT_MIN_MM,
    API_DRIVE_SPEED_MAX_MPS,
    API_SPEED_MAX_MPS,
    DEFAULT_MIN_SPEED_MPS,
    SPEED_DECIMALS,
    SPEED_STEP_MPS,
)
from .entity import MowDeckEntity, async_add_mower_entities

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator

PARALLEL_UPDATES = 1

# DriveSpeedSetting.step_mps when the capabilities name none.
DEFAULT_DRIVE_STEP_MPS = 0.1


def _speed(value: Any) -> float | None:
    """A speed in m/s with two decimals; None for anything that is not a number."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return round(float(value), SPEED_DECIMALS)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MowDeckConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Blade height and mowing speed of every mower; the drive speed where it is settable."""

    def entities(coordinator: MowDeckCoordinator, mower_id: str) -> list[NumberEntity]:
        numbers: list[NumberEntity] = [
            BladeHeightNumber(coordinator, mower_id),
            SpeedNumber(coordinator, mower_id),
        ]
        caps = coordinator.data.capabilities.get(mower_id) or {}
        if isinstance(caps.get("drive_speed_setting"), dict):
            numbers.append(DriveSpeedNumber(coordinator, mower_id))
        return numbers

    async_add_mower_entities(entry, async_add_entities, entities, domain=Platform.NUMBER)


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
        bounds = self.capabilities.get("blade_height_range_mm")
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
        """The reported blade height in whole millimetres."""
        value = self.mower_state.get("blade_height_mm")
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        return float(round(value))

    async def async_set_native_value(self, value: float) -> None:
        """Send ``set_blade_height``."""
        await self.async_send_command({"kind": "set_blade_height", "mm": round(value)})


class SpeedNumber(MowDeckEntity, NumberEntity):
    """Driving speed while mowing in metres per second (``set_speed``)."""

    entity_description = NumberEntityDescription(
        key="speed",
        device_class=NumberDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        native_step=SPEED_STEP_MPS,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
    )

    def __init__(self, coordinator: MowDeckCoordinator, mower_id: str) -> None:
        """Create the number."""
        super().__init__(coordinator, mower_id, "speed")

    def _range(self) -> tuple[float, float]:
        top = _speed(self.capabilities.get("max_speed_mps"))
        high = min(top, API_SPEED_MAX_MPS) if top is not None and top > 0 else API_SPEED_MAX_MPS
        bottom = _speed(self.capabilities.get("min_speed_mps"))
        low = bottom if bottom is not None and 0 < bottom <= high else DEFAULT_MIN_SPEED_MPS
        return min(low, high), high

    @property
    def native_min_value(self) -> float:
        """Slowest settable speed of the capabilities (0.1 m/s when they name none)."""
        return self._range()[0]

    @property
    def native_max_value(self) -> float:
        """Fastest speed of the capabilities, at most the API limit."""
        return self._range()[1]

    @property
    def native_value(self) -> float | None:
        """The reported speed, rounded to two decimals."""
        return _speed(self.mower_state.get("speed_mps"))

    async def async_set_native_value(self, value: float) -> None:
        """Send ``set_speed`` with two decimals."""
        await self.async_send_command({"kind": "set_speed", "mps": _speed(value)})


class DriveSpeedNumber(MowDeckEntity, NumberEntity):
    """The vendor's speed setting for manual driving (``set_drive_speed``).

    Only for mowers whose capabilities carry ``drive_speed_setting``. The command needs the
    token permission ``mower.drive``, so the entity starts disabled.
    """

    entity_description = NumberEntityDescription(
        key="drive_speed",
        device_class=NumberDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
    )

    def __init__(self, coordinator: MowDeckCoordinator, mower_id: str) -> None:
        """Create the number."""
        super().__init__(coordinator, mower_id, "drive_speed")

    def _setting(self) -> dict[str, Any]:
        setting = self.capabilities.get("drive_speed_setting")
        return setting if isinstance(setting, dict) else {}

    @property
    def native_min_value(self) -> float:
        """Lower bound of the setting."""
        low = _speed(self._setting().get("min_mps"))
        return low if low is not None and low > 0 else SPEED_STEP_MPS

    @property
    def native_max_value(self) -> float:
        """Upper bound of the setting, at most the API limit."""
        high = _speed(self._setting().get("max_mps"))
        if high is None or high < self.native_min_value:
            return API_DRIVE_SPEED_MAX_MPS
        return min(high, API_DRIVE_SPEED_MAX_MPS)

    @property
    def native_step(self) -> float:
        """Step of the setting (0.1 m/s when the capabilities name none, as in the API)."""
        step = _speed(self._setting().get("step_mps"))
        return step if step is not None and step > 0 else DEFAULT_DRIVE_STEP_MPS

    @property
    def native_value(self) -> float | None:
        """The current setting, rounded to two decimals."""
        return _speed(self.mower_state.get("drive_speed_mps"))

    async def async_set_native_value(self, value: float) -> None:
        """Send ``set_drive_speed`` with two decimals."""
        await self.async_send_command({"kind": "set_drive_speed", "speed_mps": _speed(value)})
