"""Lawn mower entity: activity, start, pause and dock, plus the MowDeck entity services."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityFeature,
)
from homeassistant.core import HomeAssistant

from .entity import MowDeckEntity, async_add_mower_entities

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator

PARALLEL_UPDATES = 0

# MowerActivity (Telemetry) to Home Assistant; offline and unknown have no activity.
ACTIVITY_MAP: dict[str, LawnMowerActivity] = {
    "idle": LawnMowerActivity.IDLE,
    "mowing": LawnMowerActivity.MOWING,
    "paused": LawnMowerActivity.PAUSED,
    "returning": LawnMowerActivity.RETURNING,
    "docked": LawnMowerActivity.DOCKED,
    "charging": LawnMowerActivity.DOCKED,
    "error": LawnMowerActivity.ERROR,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MowDeckConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One lawn mower entity per mower."""
    async_add_mower_entities(
        entry, async_add_entities, lambda coordinator, m: [MowDeckLawnMower(coordinator, m)]
    )


class MowDeckLawnMower(MowDeckEntity, LawnMowerEntity):
    """A MowDeck mower."""

    _attr_name = None
    _attr_supported_features = (
        LawnMowerEntityFeature.START_MOWING
        | LawnMowerEntityFeature.PAUSE
        | LawnMowerEntityFeature.DOCK
    )

    def __init__(self, coordinator: MowDeckCoordinator, mower_id: str) -> None:
        """Create the entity; it is the device's main entity (no name of its own)."""
        super().__init__(coordinator, mower_id, "mower")
        self._attr_translation_key = None

    @property
    def activity(self) -> LawnMowerActivity | None:
        """The mapped activity of the newest state."""
        return ACTIVITY_MAP.get(str(self.mower_state.get("activity")))

    async def async_start_mowing(self) -> None:
        """Resume a paused job, otherwise start the selected area (or every area)."""
        if self.mower_state.get("activity") == "paused":
            await self.async_send_command({"kind": "resume_mow"})
            return
        await self.async_start_mow()

    async def async_pause(self) -> None:
        """Pause the running job."""
        await self.async_send_command({"kind": "pause_mow"})

    async def async_dock(self) -> None:
        """Return to the dock."""
        await self.async_send_command({"kind": "return_to_dock"})

    # Entity services (registered in services.py)

    async def async_start_mow(
        self, area_ids: list[str] | None = None, pattern: dict[str, Any] | None = None
    ) -> None:
        """``mowdeck.start_mow``: start the given areas (default: the selection)."""
        command: dict[str, Any] = {
            "kind": "start_mow",
            "area_ids": list(area_ids)
            if area_ids
            else self.coordinator.area_ids_to_mow(self.mower_id),
        }
        if pattern:
            command["pattern"] = dict(pattern)
        await self.async_send_command(command)

    async def async_emergency_stop(self) -> None:
        """``mowdeck.emergency_stop``: stop at once (allowed for viewers too)."""
        await self.async_send_command({"kind": "emergency_stop"})

    async def async_set_blade_height(self, mm: int) -> None:
        """``mowdeck.set_blade_height``."""
        await self.async_send_command({"kind": "set_blade_height", "mm": int(mm)})

    async def async_set_speed(self, mps: float) -> None:
        """``mowdeck.set_speed``."""
        await self.async_send_command({"kind": "set_speed", "mps": float(mps)})
