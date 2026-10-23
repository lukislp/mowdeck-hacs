"""Numbers of a MowDeck mower: blade height, mowing speed, the manual-drive speed setting, the
angle, border laps and path spacing of the mowing defaults, and MowDeck's blade change
reminder.

Ranges come from the mower's capabilities, limited to what the Fleet command API accepts.
Speeds are read, shown and sent with two decimals (``SPEED_DECIMALS``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import (
    DEGREE,
    EntityCategory,
    Platform,
    UnitOfLength,
    UnitOfSpeed,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant

from .const import (
    API_ANGLE_MAX_DEG,
    API_BLADE_HEIGHT_MAX_MM,
    API_BLADE_HEIGHT_MIN_MM,
    API_BORDER_LAPS_MAX,
    API_DRIVE_SPEED_MAX_MPS,
    API_SPACING_MAX_M,
    API_SPACING_MIN_M,
    API_SPEED_MAX_MPS,
    CAN_CONTROL,
    CAN_MANAGE_MOWERS,
    DEFAULT_MIN_SPEED_MPS,
    SPEED_DECIMALS,
    SPEED_STEP_MPS,
)
from .entity import MowDeckEntity, async_add_mower_entities
from .select import async_save_mow_defaults, native_patterns

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator
    from .health import HealthCoordinator

PARALLEL_UPDATES = 1

# BladeThresholdIn.hours: above 0 and at most 10 000 hours.
BLADE_THRESHOLD_MAX_HOURS = 10_000
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
        if native_patterns(caps) and coordinator.permitted(mower_id, CAN_CONTROL):
            numbers += [
                MowDefaultsNumber(coordinator, mower_id, ANGLE),
                MowDefaultsNumber(coordinator, mower_id, BORDER_LAPS),
            ]
            if _spacing_range(caps) is not None:
                numbers.append(PathSpacingNumber(coordinator, mower_id))
        return numbers

    async_add_mower_entities(entry, async_add_entities, entities, domain=Platform.NUMBER)

    health = entry.runtime_data.health
    coordinator = entry.runtime_data.coordinator
    async_add_mower_entities(
        entry,
        async_add_entities,
        lambda c, m: (
            [BladeThresholdNumber(c, m, health)]
            if blade_threshold_settable(health, m) and c.permitted(m, CAN_MANAGE_MOWERS)
            else []
        ),
        domain=Platform.NUMBER,
        fingerprint=lambda m: (coordinator.fingerprint(m), blade_threshold_settable(health, m)),
        listen=[health],
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


# Path spacing in centimetres, the unit people set it in; MowDeck stores metres.
CM_PER_M = 100


def _spacing_range(caps: dict[str, Any]) -> tuple[float, float] | None:
    """The model's path spacing range in cm, within MowDeck's limits; None when not settable."""
    bounds = caps.get("path_spacing_range_mm")
    if not isinstance(bounds, list | tuple) or len(bounds) != 2:
        return None
    low = max(API_SPACING_MIN_M * CM_PER_M, float(bounds[0]) / 10)
    high = min(API_SPACING_MAX_M * CM_PER_M, float(bounds[1]) / 10)
    return (low, high) if low <= high else None


@dataclass(frozen=True, kw_only=True)
class MowDefaultsDescription(NumberEntityDescription):
    """A number of the mowing defaults and its key in ``MowPattern``."""

    field: str
    integer: bool = False


ANGLE = MowDefaultsDescription(
    key="mow_angle",
    field="angle_deg",
    native_unit_of_measurement=DEGREE,
    native_min_value=0,
    native_max_value=API_ANGLE_MAX_DEG,
    native_step=1,
    mode=NumberMode.BOX,
    entity_category=EntityCategory.CONFIG,
)
BORDER_LAPS = MowDefaultsDescription(
    key="mow_border_laps",
    field="border_laps",
    native_min_value=0,
    native_max_value=API_BORDER_LAPS_MAX,
    native_step=1,
    mode=NumberMode.BOX,
    entity_category=EntityCategory.CONFIG,
    integer=True,
)


class MowDefaultsNumber(MowDeckEntity, NumberEntity):
    """The base angle or the border laps of the mower's mowing defaults
    (``PUT .../mow-defaults``, ``mower.control``)."""

    entity_description: MowDefaultsDescription

    def __init__(
        self, coordinator: MowDeckCoordinator, mower_id: str, description: MowDefaultsDescription
    ) -> None:
        """Create the number from its description."""
        super().__init__(coordinator, mower_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        """The saved value (MowDeck's default until defaults are saved)."""
        value = self.coordinator.mow_pattern(self.mower_id).get(self.entity_description.field)
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        return float(value)

    async def async_set_native_value(self, value: float) -> None:
        """Save the value into the mowing defaults."""
        saved: float | int = round(value) if self.entity_description.integer else float(value)
        await async_save_mow_defaults(self, {self.entity_description.field: saved})


class PathSpacingNumber(MowDeckEntity, NumberEntity):
    """The distance between neighbouring paths of the mowing defaults in cm, within the
    model's range (``path_spacing_range_mm``); unknown while the mower's own default applies."""

    entity_description = NumberEntityDescription(
        key="mow_path_spacing",
        device_class=NumberDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.CENTIMETERS,
        native_step=1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    )

    def __init__(self, coordinator: MowDeckCoordinator, mower_id: str) -> None:
        """Create the number."""
        super().__init__(coordinator, mower_id, "mow_path_spacing")

    def _range(self) -> tuple[float, float]:
        return _spacing_range(self.capabilities) or (
            API_SPACING_MIN_M * CM_PER_M,
            API_SPACING_MAX_M * CM_PER_M,
        )

    @property
    def native_min_value(self) -> float:
        """Smallest spacing of the model."""
        return self._range()[0]

    @property
    def native_max_value(self) -> float:
        """Largest spacing of the model."""
        return self._range()[1]

    @property
    def native_value(self) -> float | None:
        """The saved spacing in cm."""
        value = self.coordinator.mow_pattern(self.mower_id).get("spacing_m")
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        return round(float(value) * CM_PER_M, 1)

    async def async_set_native_value(self, value: float) -> None:
        """Save the spacing (in metres) into the mowing defaults."""
        await async_save_mow_defaults(self, {"spacing_m": round(value / CM_PER_M, 3)})


def blade_threshold_settable(health: HealthCoordinator, mower_id: str) -> bool:
    """MowDeck lets the reminder threshold be set per mower (0.8: the health totals name
    whether the mower has its own)."""
    return "blade_hours_threshold_custom" in (health.data.totals.get(mower_id) or {})


class BladeThresholdNumber(MowDeckEntity, NumberEntity):
    """MowDeck's blade change reminder: the blade hours since the change after which MowDeck
    notifies (``PUT .../health/blade-threshold``, ``fleet.manage``). It concerns MowDeck's own
    count; the mower's own warning (``device_blade_warn_hours``) stays as set in the vendor
    app."""

    entity_description = NumberEntityDescription(
        key="blade_threshold",
        device_class=NumberDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        native_min_value=1,
        native_max_value=BLADE_THRESHOLD_MAX_HOURS,
        native_step=1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    )

    def __init__(
        self, coordinator: MowDeckCoordinator, mower_id: str, health: HealthCoordinator
    ) -> None:
        """Create the number."""
        super().__init__(coordinator, mower_id, "blade_threshold", service=health)
        self._health = health

    @property
    def native_value(self) -> float | None:
        """The threshold in effect (the mower's own, else MowDeck's default)."""
        value = (self._health.data.totals.get(self.mower_id) or {}).get("blade_hours_threshold")
        return float(value) if isinstance(value, int | float) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Whether the mower has its own threshold or MowDeck's default applies."""
        totals = self._health.data.totals.get(self.mower_id) or {}
        return {"custom": bool(totals.get("blade_hours_threshold_custom"))}

    async def async_set_native_value(self, value: float) -> None:
        """Set the mower's own threshold; the health totals of the answer show at once."""
        totals = await self.async_call_api(
            self.coordinator.client.set_blade_threshold(self.mower_id, float(value)),
            "blade_threshold",
            "fleet.manage",
        )
        self._health.async_set_totals(self.mower_id, totals)
