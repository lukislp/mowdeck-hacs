"""Sensors of a MowDeck mower: values of the Telemetry ``MowerState`` (only its typed fields,
never the adapter extras in ``raw``), health totals and the Scheduler (next start, last
schedule run, rain pause, wet lawn)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
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
    UnitOfArea,
    UnitOfLength,
    UnitOfSpeed,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    ERROR_CATEGORIES,
    GNSS_SOLUTIONS,
    NETWORK_KINDS,
    RUN_OUTCOMES,
    SELF_CHECK_RESULTS,
    SPEED_DECIMALS,
)
from .entity import MowDeckEntity, async_add_mower_entities
from .scheduler import parse_time
from .spot import RUN_STATES
from .vision import PROPOSAL_STATES

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator
    from .health import HealthCoordinator
    from .scheduler import SchedulerCoordinator
    from .vision import VisionCoordinator

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
    """What a sensor reads: the newest state, the health totals and the area names of one
    mower."""

    state: dict[str, Any]
    health: dict[str, Any]
    today: dict[str, Any]
    # Map area id -> the name MowDeck shows for it.
    areas: dict[str, str] = field(default_factory=dict)


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


def _part(key: str, name: str) -> Callable[[MowerData], Any]:
    """A field of a typed part of the state (``job``, ``gnss``, ``signal``, ``self_check``);
    None while the part is null (no job runs, or the connection does not report it)."""

    def read(data: MowerData) -> Any:
        part = data.state.get(key)
        return part.get(name) if isinstance(part, dict) else None

    return read


def _part_enum(key: str, name: str, options: list[str]) -> Callable[[MowerData], str | None]:
    """An enum field of a typed part; values outside ``options`` are unknown."""
    source = _part(key, name)

    def read(data: MowerData) -> str | None:
        value = source(data)
        return value if value in options else None

    return read


def _job_area(data: MowerData) -> str | None:
    """The name of the map area the running job works in, as MowDeck shows it (the id when
    the area is not on the map Home Assistant knows); None when the job names no area."""
    area_id = _part("job", "area_id")(data)
    if not area_id:
        return None
    return data.areas.get(str(area_id), str(area_id))


def _error_category(data: MowerData) -> str | None:
    """The manufacturer-neutral kind of the fault; only while the mower is in error."""
    if data.state.get("activity") != "error":
        return None
    value = data.state.get("error_category")
    return value if value in ERROR_CATEGORIES else None


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


def _today(key: str) -> Callable[[MowerData], Any]:
    """A total of the current UTC day; zero while the mower has health totals but no record
    for today, unknown without any health totals."""

    def read(data: MowerData) -> Any:
        if key in data.today:
            return data.today[key]
        return 0 if data.health else None

    return read


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
    attributes_fn: Callable[[MowerData], dict[str, Any]] | None = None
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
    # The running job (MowerState.job, null while none runs); the vendor reports the times in
    # whole minutes.
    MowDeckSensorDescription(
        key="job_progress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_number(_part("job", "progress_percent")),
    ),
    MowDeckSensorDescription(
        key="job_area",
        value_fn=_job_area,
        attributes_fn=lambda d: {"area_id": _part("job", "area_id")(d)},
    ),
    MowDeckSensorDescription(
        key="job_elapsed",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        value_fn=_number(_part("job", "elapsed_s")),
    ),
    MowDeckSensorDescription(
        key="job_remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        value_fn=_number(_part("job", "remaining_s")),
    ),
    MowDeckSensorDescription(
        key="job_mowed_area",
        device_class=SensorDeviceClass.AREA,
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_number(_part("job", "mowed_m2"), 1),
    ),
    # Satellite positioning (MowerState.gnss). Satellites and correction age change with
    # nearly every state, so they are disabled by default.
    MowDeckSensorDescription(
        key="gnss_satellites",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_number(_part("gnss", "satellites")),
    ),
    MowDeckSensorDescription(
        key="rtk_correction_age",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_number(_part("gnss", "correction_age_s"), 1),
    ),
    MowDeckSensorDescription(
        key="rtk_status",
        device_class=SensorDeviceClass.ENUM,
        options=GNSS_SOLUTIONS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_part_enum("gnss", "rtk_status", GNSS_SOLUTIONS),
    ),
    # The mower's own radio and uplink (MowerState.signal); the dBm values are chatty.
    MowDeckSensorDescription(
        key="wifi_signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_number(_part("signal", "wifi_dbm")),
    ),
    MowDeckSensorDescription(
        key="mobile_signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_number(_part("signal", "mobile_dbm")),
    ),
    MowDeckSensorDescription(
        key="network",
        device_class=SensorDeviceClass.ENUM,
        options=NETWORK_KINDS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_part_enum("signal", "network", NETWORK_KINDS),
    ),
    # The start self-check (MowerState.self_check): ok, or blocked with a reason key.
    MowDeckSensorDescription(
        key="self_check",
        device_class=SensorDeviceClass.ENUM,
        options=SELF_CHECK_RESULTS,
        value_fn=_part_enum("self_check", "result", SELF_CHECK_RESULTS),
        attributes_fn=lambda d: {"detail": _part("self_check", "detail")(d)},
    ),
    MowDeckSensorDescription(
        key="error_category",
        device_class=SensorDeviceClass.ENUM,
        options=ERROR_CATEGORIES,
        value_fn=_error_category,
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
    # Totals of the current UTC day (GET /telemetry/mowers/{id}/health/daily); they restart
    # at midnight UTC, which TOTAL_INCREASING treats as a reset.
    MowDeckSensorDescription(
        key="mowing_time_today",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        value_fn=_number(_today("mowing_hours"), 3),
        health=True,
    ),
    MowDeckSensorDescription(
        key="distance_today",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        value_fn=_number(_today("distance_m"), 1),
        health=True,
    ),
    MowDeckSensorDescription(
        key="jobs_today",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_number(_today("jobs")),
        health=True,
    ),
    MowDeckSensorDescription(
        key="online_time_today",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_number(_today("online_hours"), 2),
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
    vision = entry.runtime_data.vision
    async_add_mower_entities(
        entry,
        async_add_entities,
        lambda coordinator, m: (
            [VisionSensor(coordinator, m, vision)] if vision.data.available else []
        ),
        domain=Platform.SENSOR,
        fingerprint=lambda m: vision.data.available,
        listen=[vision],
    )
    async_add_mower_entities(
        entry,
        async_add_entities,
        lambda coordinator, m: (
            [SpotRunSensor(coordinator, m)]
            if (coordinator.data.capabilities.get(m) or {}).get("manual_drive")
            else []
        ),
        domain=Platform.SENSOR,
    )
    scheduler = entry.runtime_data.scheduler
    async_add_mower_entities(
        entry,
        async_add_entities,
        lambda coordinator, m: (
            [SchedulerSensor(coordinator, m, scheduler, d) for d in SCHEDULER_SENSORS]
            if scheduler.data.available
            else []
        ),
        domain=Platform.SENSOR,
        fingerprint=lambda m: scheduler.data.available,
        listen=[scheduler],
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
        """The value read from the newest state, the health totals or today's totals (zero
        while the mower has health totals but nothing was recorded today)."""
        return self.entity_description.value_fn(self._data())

    def _data(self) -> MowerData:
        today = self._health.data.today.get(self.mower_id) if self._health else None
        areas = dict(self.coordinator.area_labels(self.mower_id))
        return MowerData(self.mower_state, self._totals(), today or {}, areas)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Battery cycles: their source and MowDeck's own count. Blade hours: the reminder
        threshold and the last recorded blade change. Next to the error code: its message and
        category, and whether driving the mower may free it. Others as their description says."""
        if self.entity_description.attributes_fn is not None:
            return self.entity_description.attributes_fn(self._data())
        if self.entity_description.key == "battery_cycles":
            health = self._totals()
            return _battery_cycle_attributes(health) if health else None
        if self.entity_description.key == "blade_hours":
            health = self._totals()
            return _blade_attributes(health) if health else None
        if self.entity_description.key != "error_code":
            return None
        state = self.mower_state
        return {
            "error_message": state.get("error_message"),
            "error_category": _error_category(self._data()),
            "drive_recoverable": bool(state.get("drive_recoverable", False)),
        }


def _next_mow(scheduler: SchedulerCoordinator, mower_id: str) -> datetime | None:
    upcoming = scheduler.next_mow(mower_id)
    return upcoming[0] if upcoming else None


def _next_mow_attributes(scheduler: SchedulerCoordinator, mower_id: str) -> dict[str, Any]:
    upcoming = scheduler.next_mow(mower_id)
    schedule = upcoming[1] if upcoming else {}
    return {"schedule": schedule.get("name"), "schedule_id": schedule.get("id")}


def _last_run(scheduler: SchedulerCoordinator, mower_id: str) -> str | None:
    found = scheduler.last_run(mower_id)
    outcome = found[0].get("outcome") if found else None
    return outcome if outcome in RUN_OUTCOMES else None


def _last_run_attributes(scheduler: SchedulerCoordinator, mower_id: str) -> dict[str, Any]:
    found = scheduler.last_run(mower_id)
    run, schedule = found if found else ({}, {})
    return {
        "schedule": schedule.get("name"),
        "schedule_id": schedule.get("id"),
        "scheduled_for": run.get("scheduled_for"),
        "started_at": run.get("started_at"),
        "trigger": run.get("trigger"),
        "details": run.get("details"),
        "followup": run.get("followup"),
    }


def _decision_time(key: str) -> Callable[[SchedulerCoordinator, str], datetime | None]:
    def read(scheduler: SchedulerCoordinator, mower_id: str) -> datetime | None:
        return parse_time((scheduler.data.weather.get(mower_id) or {}).get(key))

    return read


@dataclass(frozen=True, kw_only=True)
class SchedulerSensorDescription(SensorEntityDescription):
    """A sensor of the Scheduler's data for one mower."""

    value_fn: Callable[[SchedulerCoordinator, str], Any]
    attributes_fn: Callable[[SchedulerCoordinator, str], dict[str, Any]] | None = None


SCHEDULER_SENSORS: tuple[SchedulerSensorDescription, ...] = (
    SchedulerSensorDescription(
        key="next_mow",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_next_mow,
        attributes_fn=_next_mow_attributes,
    ),
    SchedulerSensorDescription(
        key="last_schedule_run",
        device_class=SensorDeviceClass.ENUM,
        options=RUN_OUTCOMES,
        value_fn=_last_run,
        attributes_fn=_last_run_attributes,
    ),
    SchedulerSensorDescription(
        key="rain_pause_until",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_decision_time("pause_until"),
    ),
    SchedulerSensorDescription(
        key="lawn_wet_until",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_decision_time("wet_until"),
    ),
)


class SchedulerSensor(MowDeckEntity, SensorEntity):
    """A value of the Scheduler for the mower: next start, last run, rain pause, wet lawn."""

    entity_description: SchedulerSensorDescription

    def __init__(
        self,
        coordinator: MowDeckCoordinator,
        mower_id: str,
        scheduler: SchedulerCoordinator,
        description: SchedulerSensorDescription,
    ) -> None:
        """Create the sensor from its description."""
        super().__init__(coordinator, mower_id, description.key, service=scheduler)
        self.entity_description = description
        self._scheduler = scheduler

    @property
    def native_value(self) -> Any:
        """The value computed from the Scheduler's data."""
        return self.entity_description.value_fn(self._scheduler, self.mower_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The schedule behind the value, where there is one."""
        attributes = self.entity_description.attributes_fn
        return attributes(self._scheduler, self.mower_id) if attributes else None


def _steps(proposal: dict[str, Any]) -> list[dict[str, Any]]:
    """The drive steps of a full proposal (the realtime summary only counts them)."""
    steps = proposal.get("steps")
    if not isinstance(steps, list):
        return []
    keys = ("linear", "angular", "seconds", "outcome")
    return [{k: step.get(k) for k in keys} for step in steps if isinstance(step, dict)]


def proposal_attributes(vision: VisionCoordinator, proposal: dict[str, Any]) -> dict[str, Any]:
    """What a proposal says, from the full proposal or the realtime summary: the action, a
    summary of the drive steps, the model's confidence and situation, until when it can be
    approved and the link to approve or reject it in MowDeck."""
    assessment = proposal.get("assessment")
    assessment = assessment if isinstance(assessment, dict) else {}
    steps = _steps(proposal)
    count = proposal.get("steps") if isinstance(proposal.get("steps"), int) else len(steps)
    return {
        "proposal_id": proposal.get("id"),
        "task": proposal.get("task"),
        "trigger": proposal.get("trigger"),
        "round": proposal.get("round"),
        "action": proposal.get("action"),
        "fallback": proposal.get("fallback"),
        "step_count": count,
        "drive_seconds": round(sum(float(s.get("seconds") or 0) for s in steps), 1)
        if steps
        else None,
        "steps": steps,
        "confidence": proposal.get("confidence", assessment.get("confidence")),
        "situation": proposal.get("situation", assessment.get("situation")),
        "rationale": assessment.get("rationale"),
        "detail": proposal.get("detail"),
        "final": proposal.get("final"),
        "expires_at": proposal.get("expires_at"),
        "link": vision.absolute_link(proposal),
    }


class VisionSensor(MowDeckEntity, SensorEntity):
    """The state of the mower's newest Vision proposal, with what it proposes. Approving or
    rejecting it happens in MowDeck (the ``link`` attribute)."""

    entity_description = SensorEntityDescription(
        key="ai_recovery", device_class=SensorDeviceClass.ENUM, options=PROPOSAL_STATES
    )

    def __init__(
        self, coordinator: MowDeckCoordinator, mower_id: str, vision: VisionCoordinator
    ) -> None:
        """Create the sensor."""
        super().__init__(coordinator, mower_id, "ai_recovery", service=vision)
        self._vision = vision

    @property
    def native_value(self) -> str | None:
        """The proposal's state; unknown without a proposal."""
        state = self._vision.proposal(self.mower_id).get("state")
        return state if state in PROPOSAL_STATES else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """What the newest proposal says (nothing without one)."""
        proposal = self._vision.proposal(self.mower_id)
        return proposal_attributes(self._vision, proposal) if proposal else None


class SpotRunSensor(MowDeckEntity, SensorEntity):
    """The spot run MowDeck drives on the mower (``MowerState.spot_run``): its state, kept
    after the end until the next run, with the progress as attributes. Only for mowers that
    can be driven (``manual_drive``)."""

    entity_description = SensorEntityDescription(
        key="spot_run", device_class=SensorDeviceClass.ENUM, options=RUN_STATES
    )

    def __init__(self, coordinator: MowDeckCoordinator, mower_id: str) -> None:
        """Create the sensor."""
        super().__init__(coordinator, mower_id, "spot_run")

    def _run(self) -> dict[str, Any]:
        run = self.mower_state.get("spot_run")
        return run if isinstance(run, dict) else {}

    @property
    def native_value(self) -> str | None:
        """The run's state; unknown before the first run."""
        state = self._run().get("state")
        return state if state in RUN_STATES else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Progress of the run: percent, points and segments, dry run, why it ended early."""
        run = self._run()
        if not run:
            return None
        keys: tuple[str, ...] = ("run_id", "progress_percent", "driven_points")
        keys += ("planned_points", "segment_index", "segments_total", "dry_run", "reason")
        return {key: run.get(key) for key in keys}
