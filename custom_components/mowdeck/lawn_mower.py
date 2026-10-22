"""Lawn mower entity: activity, start, pause, dock and stop, plus the MowDeck entity services."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityFeature,
)
from homeassistant.core import HomeAssistant, ServiceResponse
from homeassistant.exceptions import ServiceValidationError

from .const import DOMAIN, SPEED_DECIMALS
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
    # A firmware update installs only on a docked mower.
    "updating": LawnMowerActivity.DOCKED,
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
        | LawnMowerEntityFeature.STOP
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
        """Return to the dock (a running job is paused and can be resumed)."""
        await self.async_send_command({"kind": "return_to_dock"})

    async def async_stop(self) -> None:
        """End the job and return to the dock (``return_to_dock`` with ``end_job``)."""
        await self.async_send_command({"kind": "return_to_dock", "end_job": True})

    # Entity services (registered in services.py)

    async def async_start_mow(
        self, area_ids: list[str] | None = None, pattern: dict[str, Any] | None = None
    ) -> None:
        """``mowdeck.start_mow``: start the given areas (default: the selected areas) with the
        given pattern (default: the mower's mowing defaults, when saved)."""
        areas = list(area_ids) if area_ids else self.coordinator.area_ids_to_mow(self.mower_id)
        if not areas:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_area_selected"
            )
        command: dict[str, Any] = {"kind": "start_mow", "area_ids": areas}
        if pattern:
            command["pattern"] = dict(pattern)
        elif self.coordinator.has_mow_defaults(self.mower_id):
            command["pattern"] = self.coordinator.mow_pattern(self.mower_id)
        await self.async_send_command(command)

    async def async_get_map(self) -> ServiceResponse:
        """``mowdeck.get_map``: the mower's current map with MowDeck's area names."""
        answer = await self.async_call_api(
            self.coordinator.client.get_map(self.mower_id), "get_map", "mower.view"
        )
        if answer is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_map")
        mower_map = dict(answer.get("map") or {})
        names = dict(self.coordinator.area_labels(self.mower_id))
        areas = [
            {**area, "display_name": names.get(str(area.get("id")))}
            for area in mower_map.get("areas") or []
            if isinstance(area, dict)
        ]
        return {
            "mower_id": self.mower_id,
            "revision": answer.get("revision"),
            "observed_at": answer.get("observed_at"),
            "map": {**mower_map, "areas": areas},
        }

    async def async_get_heatmap(
        self,
        layer: str,
        start: datetime | None = None,
        end: datetime | None = None,
        cell: float | None = None,
    ) -> ServiceResponse:
        """``mowdeck.get_heatmap``: a heatmap layer of the mower (default: the last 7 days)."""
        return await self.async_call_api(
            self.coordinator.client.get_heatmap(
                self.mower_id,
                layer,
                start=start.isoformat() if start else None,
                end=end.isoformat() if end else None,
                cell=cell,
            ),
            "get_heatmap",
            "mower.view",
        )

    async def async_emergency_stop(self) -> None:
        """``mowdeck.emergency_stop``: stop at once (allowed for viewers too)."""
        await self.async_send_command({"kind": "emergency_stop"})

    async def async_set_blade_height(self, mm: int) -> None:
        """``mowdeck.set_blade_height``."""
        await self.async_send_command({"kind": "set_blade_height", "mm": int(mm)})

    async def async_set_speed(self, mps: float) -> None:
        """``mowdeck.set_speed`` (two decimals, like the number entity)."""
        await self.async_send_command(
            {"kind": "set_speed", "mps": round(float(mps), SPEED_DECIMALS)}
        )
