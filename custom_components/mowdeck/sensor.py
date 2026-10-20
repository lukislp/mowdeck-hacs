"""Sensors of a MowDeck mower (values of the Telemetry ``MowerState``)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfLength,
    UnitOfSpeed,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .entity import MowDeckEntity, async_add_mower_entities

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator

PARALLEL_UPDATES = 0

# Enum values of the API (MowerActivity, RtkFixStatus, RtkSource, ConnectionKind).
ACTIVITIES = ["idle", "mowing", "paused", "returning", "docked", "charging", "error", "offline"]
RTK_FIX = ["none", "float", "fixed"]
RTK_SOURCES = ["base_station", "network"]
CONNECTION_KINDS = ["ble_bridge", "wifi", "cloud", "simulated"]


def _enum(options: list[str], key: str) -> Callable[[dict[str, Any]], str | None]:
    """Read an enum field; "unknown" and unexpected values become None (unknown state)."""

    def read(state: dict[str, Any]) -> str | None:
        value = state.get(key)
        return value if value in options else None

    return read


def _timestamp(state: dict[str, Any]) -> datetime | None:
    raw = state.get("observed_at")
    return dt_util.parse_datetime(raw) if isinstance(raw, str) else None


@dataclass(frozen=True, kw_only=True)
class MowDeckSensorDescription(SensorEntityDescription):
    """A sensor and how to read its value from the state."""

    value_fn: Callable[[dict[str, Any]], Any]


SENSORS: tuple[MowDeckSensorDescription, ...] = (
    MowDeckSensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.get("battery_percent"),
    ),
    MowDeckSensorDescription(
        key="activity",
        device_class=SensorDeviceClass.ENUM,
        options=ACTIVITIES,
        value_fn=_enum(ACTIVITIES, "activity"),
    ),
    MowDeckSensorDescription(
        key="rtk_fix",
        device_class=SensorDeviceClass.ENUM,
        options=RTK_FIX,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_enum(RTK_FIX, "rtk_fix"),
    ),
    MowDeckSensorDescription(
        key="rtk_source",
        device_class=SensorDeviceClass.ENUM,
        options=RTK_SOURCES,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_enum(RTK_SOURCES, "rtk_source"),
    ),
    MowDeckSensorDescription(
        key="connection",
        device_class=SensorDeviceClass.ENUM,
        options=CONNECTION_KINDS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_enum(CONNECTION_KINDS, "connection"),
    ),
    MowDeckSensorDescription(
        key="blade_height",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.get("blade_height_mm"),
    ),
    MowDeckSensorDescription(
        key="speed",
        device_class=SensorDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda s: s.get("speed_mps"),
    ),
    MowDeckSensorDescription(
        key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_timestamp,
    ),
    MowDeckSensorDescription(
        key="error_code",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.get("error_code"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MowDeckConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """The sensors of every mower."""
    async_add_mower_entities(
        entry,
        async_add_entities,
        lambda coordinator, m: [MowDeckSensor(coordinator, m, d) for d in SENSORS],
    )


class MowDeckSensor(MowDeckEntity, SensorEntity):
    """A value of the mower state."""

    entity_description: MowDeckSensorDescription

    def __init__(
        self,
        coordinator: MowDeckCoordinator,
        mower_id: str,
        description: MowDeckSensorDescription,
    ) -> None:
        """Create the sensor from its description."""
        super().__init__(coordinator, mower_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """The value read from the newest state."""
        return self.entity_description.value_fn(self.mower_state)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The error message next to the error code."""
        if self.entity_description.key == "error_code":
            return {"error_message": self.mower_state.get("error_message")}
        return None
