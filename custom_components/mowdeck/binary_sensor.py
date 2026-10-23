"""Binary sensors of a MowDeck mower: charging, online, error and the Scheduler's rain hold."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant

from .base_station import (
    BaseStationCoordinator,
    BaseStationEntity,
    async_add_base_station_entities,
)
from .const import RAIN_VERDICTS
from .entity import MowDeckEntity, async_add_mower_entities

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator
    from .scheduler import SchedulerCoordinator

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class MowDeckBinarySensorDescription(BinarySensorEntityDescription):
    """A binary sensor and how to compute it."""

    value_fn: Callable[[MowDeckCoordinator, str], bool | None]


def _charging(coordinator: MowDeckCoordinator, mower_id: str) -> bool | None:
    state = coordinator.state(mower_id)
    if state is None:
        return None
    return bool(state.get("charging")) or state.get("activity") == "charging"


def _error(coordinator: MowDeckCoordinator, mower_id: str) -> bool | None:
    state = coordinator.state(mower_id)
    if state is None:
        return None
    return state.get("activity") == "error" or bool(state.get("error_code"))


BINARY_SENSORS: tuple[MowDeckBinarySensorDescription, ...] = (
    MowDeckBinarySensorDescription(
        key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=_charging,
    ),
    MowDeckBinarySensorDescription(
        key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator, mower_id: coordinator.is_online(mower_id),
    ),
    MowDeckBinarySensorDescription(
        key="error",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=_error,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MowDeckConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """The binary sensors of every mower."""
    async_add_mower_entities(
        entry,
        async_add_entities,
        lambda coordinator, m: [MowDeckBinarySensor(coordinator, m, d) for d in BINARY_SENSORS],
    )
    scheduler = entry.runtime_data.scheduler
    async_add_mower_entities(
        entry,
        async_add_entities,
        lambda coordinator, m: (
            [RainHoldBinarySensor(coordinator, m, scheduler)] if scheduler.data.available else []
        ),
        domain=Platform.BINARY_SENSOR,
        fingerprint=lambda m: scheduler.data.available,
        listen=[scheduler],
    )
    async_add_base_station_entities(
        entry,
        async_add_entities,
        lambda base_stations, station_id: [
            BaseStationBinarySensor(base_stations, station_id, d)
            for d in BASE_STATION_BINARY_SENSORS
        ],
    )


@dataclass(frozen=True, kw_only=True)
class BaseStationBinarySensorDescription(BinarySensorEntityDescription):
    """A flag of a base station's live state (``MowerState.base_station``)."""

    field: str


BASE_STATION_BINARY_SENSORS: tuple[BaseStationBinarySensorDescription, ...] = (
    # The mower receives the base station (over LoRa the radio link); unknown while the
    # mower does not say, which includes network RTK.
    BaseStationBinarySensorDescription(
        key="link", device_class=BinarySensorDeviceClass.CONNECTIVITY, field="link_up"
    ),
    # Moved since the map was recorded: positions may be shifted.
    BaseStationBinarySensorDescription(
        key="moved", device_class=BinarySensorDeviceClass.PROBLEM, field="moved"
    ),
)


class BaseStationBinarySensor(BaseStationEntity, BinarySensorEntity):
    """A flag of a base station; unknown while no linked mower reports it."""

    entity_description: BaseStationBinarySensorDescription

    def __init__(
        self,
        base_stations: BaseStationCoordinator,
        station_id: str,
        description: BaseStationBinarySensorDescription,
    ) -> None:
        """Create the binary sensor from its description."""
        super().__init__(base_stations, station_id, description.key, f"base_{description.key}")
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """The reported flag."""
        value = self.base_state.get(self.entity_description.field)
        return value if isinstance(value, bool) else None


class MowDeckBinarySensor(MowDeckEntity, BinarySensorEntity):
    """A flag derived from the mower state."""

    entity_description: MowDeckBinarySensorDescription

    def __init__(
        self,
        coordinator: MowDeckCoordinator,
        mower_id: str,
        description: MowDeckBinarySensorDescription,
    ) -> None:
        """Create the binary sensor from its description."""
        super().__init__(coordinator, mower_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """The computed flag."""
        return self.entity_description.value_fn(self.coordinator, self.mower_id)


class RainHoldBinarySensor(MowDeckEntity, BinarySensorEntity):
    """On while the Scheduler holds this mower's runs for rain (``GET /scheduler/weather``).

    The decision uses the rain settings of the mower's next schedule (MowDeck's defaults
    without one): rain in the forecast, rain within the cooldown, or a lawn still wet."""

    entity_description = BinarySensorEntityDescription(key="rain_hold")

    def __init__(
        self, coordinator: MowDeckCoordinator, mower_id: str, scheduler: SchedulerCoordinator
    ) -> None:
        """Create the binary sensor."""
        super().__init__(coordinator, mower_id, "rain_hold", service=scheduler)
        self._scheduler = scheduler

    @property
    def _decision(self) -> dict[str, Any]:
        return self._scheduler.data.weather.get(self.mower_id) or {}

    @property
    def is_on(self) -> bool | None:
        """The rain decision holds the runs."""
        holds = self._decision.get("holds")
        return holds if isinstance(holds, bool) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Why: the verdict, MowDeck's reason, recent rain and the weather provider."""
        decision = self._decision
        if not decision:
            return None
        verdict = decision.get("verdict")
        return {
            "verdict": verdict if verdict in RAIN_VERDICTS else None,
            "reason": decision.get("reason"),
            "wet": decision.get("wet"),
            "recent_mm_6h": decision.get("recent_mm_6h"),
            "recent_mm_24h": decision.get("recent_mm_24h"),
            "provider": decision.get("provider"),
            "attribution": decision.get("attribution"),
        }
