"""Binary sensors of a MowDeck mower: charging, online and error."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant

from .entity import MowDeckEntity, async_add_mower_entities

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator

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
