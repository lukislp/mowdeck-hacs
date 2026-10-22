"""Sensors of a MowDeck mower: values of the Telemetry ``MowerState`` and health totals."""

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
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    Platform,
    UnitOfLength,
    UnitOfSpeed,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import SPEED_DECIMALS
from .entity import MowDeckEntity, async_add_mower_entities

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator
    from .health import HealthCoordinator

PARALLEL_UPDATES = 0

# Enum values of the API (MowerActivity, RtkFixStatus, RtkSource, ConnectionKind); "unknown"
# becomes the unknown state.
ACTIVITIES = [
    "idle",
    "mowing",
    "paused",
    "returning",
    "docked",
    "charging",
    "error",
    "offline",
    "updating",
]
RTK_FIX = ["none", "float", "fixed"]
RTK_SOURCES = ["base_station", "network"]
CONNECTION_KINDS = ["ble_bridge", "wifi", "cloud", "simulated"]
BLE_BRIDGE = "ble_bridge"


@dataclass(frozen=True, slots=True)
class MowerData:
    """What a sensor reads: the newest state and the health totals of one mower."""

    state: dict[str, Any]
    health: dict[str, Any]


def _enum(options: list[str], key: str) -> Callable[[MowerData], str | None]:
    """Read an enum field; "unknown" and unexpected values become None (unknown state)."""

    def read(data: MowerData) -> str | None:
        value = data.state.get(key)
        return value if value in options else None

    return read


def _number(
    source: Callable[[MowerData], Any], decimals: int | None = None
) -> Callable[[MowerData], float | int | None]:
    """Read a number. ``decimals`` rounds away float noise (0.30000000000000004 -> 0.3);
    without it the value is a whole number. Anything that is not a number is unknown."""

    def read(data: MowerData) -> float | int | None:
        value = source(data)
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        if decimals is None:
            return round(value)
        return round(float(value), decimals)

    return read


def _timestamp(data: MowerData) -> datetime | None:
    raw = data.state.get("observed_at")
    return dt_util.parse_datetime(raw) if isinstance(raw, str) else None


def _blade_hours(data: MowerData) -> Any:
    """Hours since the last blade change, from the counter MowDeck shows (``blade_source``)."""
    if data.health.get("blade_source") == "device":
        return data.health.get("device_blade_hours")
    return data.health.get("blade_hours_since_change")


def _battery_cycles(data: MowerData) -> Any:
    """Battery cycles as MowDeck shows them: the mower's own lifetime counter when it reports
    one (``battery_cycles_source`` device), MowDeck's count otherwise. MowDeck before the
    source fields answers its own count in ``battery_cycles``."""
    health = data.health
    if health.get("battery_cycles_source") == "device":
        device = health.get("device_battery_cycles")
        if device is not None:
            return device
    return health.get("battery_cycles")


def _battery_cycle_attributes(health: dict[str, Any]) -> dict[str, Any]:
    """Where the battery cycles come from (``device`` or ``telemetry``) and MowDeck's own
    count; MowDeck before the source fields only has its own count."""
    device = health.get("battery_cycles_source") == "device" and (
        health.get("device_battery_cycles") is not None
    )
    tracked = health.get("battery_cycles_tracked", None if device else health.get("battery_cycles"))
    return {
        "source": "device" if device else "telemetry",
        "tracked_cycles": tracked,
    }


def _blade_attributes(health: dict[str, Any]) -> dict[str, Any]:
    """The reminder threshold of the counter shown (the mower's own warning or MowDeck's
    threshold) and when MowDeck last recorded a blade change."""
    device = health.get("blade_source") == "device"
    threshold = health.get("device_blade_warn_hours") if device else None
    if threshold is None:
        threshold = health.get("blade_hours_threshold")
    return {"threshold": threshold, "changed_at": health.get("blade_changed_at")}


def _bridge_rssi(data: MowerData) -> Any:
    link = (data.state.get("links") or {}).get(BLE_BRIDGE)
    return link.get("rssi") if isinstance(link, dict) else None


def _has_ble_bridge(caps: dict[str, Any]) -> bool:
    return BLE_BRIDGE in (caps.get("links_supported") or [])


@dataclass(frozen=True, kw_only=True)
class MowDeckSensorDescription(SensorEntityDescription):
    """A sensor, how to read its value and whether a mower has it."""

    value_fn: Callable[[MowerData], Any]
    exists_fn: Callable[[dict[str, Any]], bool] = lambda caps: True
    # Read from the health coordinator (unavailable while the health totals fail).
    health: bool = False


SENSORS: tuple[MowDeckSensorDescription, ...] = (
    MowDeckSensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_number(lambda d: d.state.get("battery_percent")),
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
        entity_registry_enabled_default=False,
        value_fn=_enum(RTK_SOURCES, "rtk_source"),
    ),
    MowDeckSensorDescription(
        key="connection",
        device_class=SensorDeviceClass.ENUM,
        options=CONNECTION_KINDS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_enum(CONNECTION_KINDS, "connection"),
    ),
    MowDeckSensorDescription(
        key="blade_height",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_number(lambda d: d.state.get("blade_height_mm")),
    ),
    MowDeckSensorDescription(
        key="speed",
        device_class=SensorDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=SPEED_DECIMALS,
        value_fn=_number(lambda d: d.state.get("speed_mps"), SPEED_DECIMALS),
    ),
    MowDeckSensorDescription(
        key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_timestamp,
    ),
    MowDeckSensorDescription(
        key="error_code",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.state.get("error_code"),
    ),
    MowDeckSensorDescription(
        key="bridge_signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_number(_bridge_rssi),
        exists_fn=_has_ble_bridge,
    ),
    # Health totals of Telemetry (GET /telemetry/mowers/{id}/health) from the health
    # coordinator (HEALTH_REFRESH).
    MowDeckSensorDescription(
        key="blade_hours",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_number(_blade_hours, 2),
        health=True,
    ),
    MowDeckSensorDescription(
        key="mowing_hours",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        value_fn=_number(lambda d: d.health.get("mowing_hours"), 2),
        health=True,
    ),
    MowDeckSensorDescription(
        key="mowing_distance",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        suggested_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=_number(lambda d: d.health.get("distance_m")),
        health=True,
    ),
    MowDeckSensorDescription(
        key="battery_cycles",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_number(_battery_cycles),
        health=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MowDeckConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """The sensors of every mower (the bridge signal only where a bridge link exists)."""
    health = entry.runtime_data.health
    async_add_mower_entities(
        entry,
        async_add_entities,
        lambda coordinator, m: [
            MowDeckSensor(coordinator, m, d, health if d.health else None)
            for d in SENSORS
            if d.exists_fn(coordinator.data.capabilities.get(m) or {})
        ],
        domain=Platform.SENSOR,
    )


class MowDeckSensor(MowDeckEntity, SensorEntity):
    """A value of the mower state or of its health totals."""

    entity_description: MowDeckSensorDescription

    def __init__(
        self,
        coordinator: MowDeckCoordinator,
        mower_id: str,
        description: MowDeckSensorDescription,
        health: HealthCoordinator | None = None,
    ) -> None:
        """Create the sensor from its description (``health`` for the health totals)."""
        super().__init__(coordinator, mower_id, description.key, service=health)
        self.entity_description = description
        self._health = health

    def _totals(self) -> dict[str, Any]:
        if self._health is None:
            return {}
        return self._health.data.totals.get(self.mower_id) or {}

    @property
    def native_value(self) -> Any:
        """The value read from the newest state or the health totals."""
        return self.entity_description.value_fn(MowerData(self.mower_state, self._totals()))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Battery cycles: their source and MowDeck's own count. Blade hours: the reminder
        threshold and the last recorded blade change. Next to the error code: its
        message, category and source, and whether driving the mower may free it. Category and
        source are adapter extras (``raw``), so they are only shown while an error code is set
        and may be missing for other manufacturers."""
        if self.entity_description.key == "battery_cycles":
            health = self._totals()
            return _battery_cycle_attributes(health) if health else None
        if self.entity_description.key == "blade_hours":
            health = self._totals()
            return _blade_attributes(health) if health else None
        if self.entity_description.key != "error_code":
            return None
        state = self.mower_state
        raw = state.get("raw")
        extras = raw if isinstance(raw, dict) and state.get("error_code") else {}
        return {
            "error_message": state.get("error_message"),
            "error_category": extras.get("error_category"),
            "error_source": extras.get("error_source"),
            "drive_recoverable": bool(state.get("drive_recoverable", False)),
        }
