"""Buttons of a MowDeck mower: emergency stop (optional), return to dock, end the job, check
for firmware, record a blade change, and run or skip each schedule that mows it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant

from .const import (
    CAN_CONTROL,
    CAN_DRIVE,
    CAN_EDIT_SCHEDULE,
    CAN_MANAGE_MOWERS,
    CONF_EXPOSE_EMERGENCY_STOP,
    DEFAULT_EXPOSE_EMERGENCY_STOP,
)
from .entity import MowDeckEntity, ScheduleEntity, async_add_mower_entities

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator
    from .scheduler import SchedulerCoordinator
    from .vision import VisionCoordinator

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class MowDeckButtonDescription(ButtonEntityDescription):
    """A button and the command it sends."""

    command: dict[str, Any]


EMERGENCY_STOP = MowDeckButtonDescription(key="emergency_stop", command={"kind": "emergency_stop"})
RETURN_TO_DOCK = MowDeckButtonDescription(key="return_to_dock", command={"kind": "return_to_dock"})
# Ends MowDeck's spot run: zero frame, blades off, the mower stays where it is.
STOP_SPOT_RUN = MowDeckButtonDescription(key="stop_spot_run", command={"kind": "spot_stop"})
# Ends the job instead of pausing it (the mower forgets its progress), then docks.
END_JOB = MowDeckButtonDescription(
    key="end_job", command={"kind": "return_to_dock", "end_job": True}
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MowDeckConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Command buttons for every mower; firmware check and blade change where they apply."""
    descriptions = [RETURN_TO_DOCK, END_JOB]
    if entry.options.get(CONF_EXPOSE_EMERGENCY_STOP, DEFAULT_EXPOSE_EMERGENCY_STOP):
        descriptions.insert(0, EMERGENCY_STOP)

    def entities(coordinator: MowDeckCoordinator, mower_id: str) -> list[ButtonEntity]:
        buttons: list[ButtonEntity] = [
            MowDeckButton(coordinator, mower_id, d) for d in descriptions
        ]
        caps = coordinator.data.capabilities.get(mower_id) or {}
        if caps.get("manual_drive") and coordinator.permitted(mower_id, CAN_CONTROL):
            buttons.append(MowDeckButton(coordinator, mower_id, STOP_SPOT_RUN))
        if caps.get("firmware_update"):
            buttons.append(FirmwareCheckButton(coordinator, mower_id))
        if coordinator.permitted(mower_id, CAN_MANAGE_MOWERS):
            buttons.append(BladeChangedButton(coordinator, mower_id, entry))
        return buttons

    async_add_mower_entities(entry, async_add_entities, entities, domain=Platform.BUTTON)

    vision = entry.runtime_data.vision
    async_add_mower_entities(
        entry,
        async_add_entities,
        lambda coordinator, m: (
            [VisionStartButton(coordinator, m, vision)]
            if vision.data.available and coordinator.permitted(m, CAN_DRIVE)
            else []
        ),
        domain=Platform.BUTTON,
        fingerprint=lambda m: (
            entry.runtime_data.coordinator.fingerprint(m),
            vision.data.available,
        ),
        listen=[vision],
    )

    scheduler = entry.runtime_data.scheduler

    def schedule_buttons(coordinator: MowDeckCoordinator, mower_id: str) -> list[ButtonEntity]:
        buttons: list[ButtonEntity] = []
        for schedule in scheduler.schedules_for(mower_id):
            schedule_id = str(schedule["id"])
            if scheduler.permitted(schedule, CAN_CONTROL):
                buttons.append(ScheduleRunButton(coordinator, mower_id, scheduler, schedule_id))
            if scheduler.permitted(schedule, CAN_EDIT_SCHEDULE):
                buttons.append(ScheduleSkipButton(coordinator, mower_id, scheduler, schedule_id))
        return buttons

    async_add_mower_entities(
        entry,
        async_add_entities,
        schedule_buttons,
        domain=Platform.BUTTON,
        fingerprint=scheduler.fingerprint,
        listen=[scheduler],
    )


class MowDeckButton(MowDeckEntity, ButtonEntity):
    """Sends one command."""

    entity_description: MowDeckButtonDescription

    def __init__(
        self,
        coordinator: MowDeckCoordinator,
        mower_id: str,
        description: MowDeckButtonDescription,
    ) -> None:
        """Create the button from its description."""
        super().__init__(coordinator, mower_id, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Send the command."""
        await self.async_send_command(dict(self.entity_description.command))


class FirmwareCheckButton(MowDeckEntity, ButtonEntity):
    """Asks the vendor for firmware updates now (``POST .../firmware/check``, ``mower.view``).

    The answer updates the firmware entity at once; MowDeck refuses mowers whose connection
    cannot ask (no cloud link) and offline mowers."""

    entity_description = ButtonEntityDescription(
        key="firmware_check", entity_category=EntityCategory.CONFIG
    )

    def __init__(self, coordinator: MowDeckCoordinator, mower_id: str) -> None:
        """Create the button."""
        super().__init__(coordinator, mower_id, "firmware_check")

    async def async_press(self) -> None:
        """Check and show the fresh firmware information."""
        info = await self.async_call_api(
            self.coordinator.client.check_firmware(self.mower_id), "firmware_check", "mower.view"
        )
        self.coordinator.async_merge_firmware(self.mower_id, info)


class BladeChangedButton(MowDeckEntity, ButtonEntity):
    """Records a blade change (``POST .../health/blade-reset``, ``fleet.manage``): the blade
    hours since the change restart at zero. Disabled by default; only offered when the token
    may manage the mower."""

    entity_description = ButtonEntityDescription(
        key="blade_changed",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
    )

    def __init__(
        self, coordinator: MowDeckCoordinator, mower_id: str, entry: MowDeckConfigEntry
    ) -> None:
        """Create the button."""
        super().__init__(coordinator, mower_id, "blade_changed")
        self._health = entry.runtime_data.health

    async def async_press(self) -> None:
        """Record the change; the health totals of the answer show at once."""
        totals = await self.async_call_api(
            self.coordinator.client.reset_blade(self.mower_id), "blade_changed", "fleet.manage"
        )
        self._health.async_set_totals(self.mower_id, totals)


class ScheduleRunButton(ScheduleEntity, ButtonEntity):
    """Starts a schedule now (``run-now``, ``mower.control``) without its rain pause; the
    next firing stays as it is."""

    def __init__(
        self,
        coordinator: MowDeckCoordinator,
        mower_id: str,
        scheduler: SchedulerCoordinator,
        schedule_id: str,
    ) -> None:
        """Create the button."""
        super().__init__(coordinator, mower_id, scheduler, schedule_id, "schedule_run_now")

    async def async_press(self) -> None:
        """Start the schedule now."""
        await self.async_schedule_action(
            self.coordinator.client.run_schedule_now(self.schedule_id),
            "schedule_run_now",
            "mower.control",
        )


class ScheduleSkipButton(ScheduleEntity, ButtonEntity):
    """Skips the schedule's next firing (``skip-next``, ``schedule.edit``)."""

    def __init__(
        self,
        coordinator: MowDeckCoordinator,
        mower_id: str,
        scheduler: SchedulerCoordinator,
        schedule_id: str,
    ) -> None:
        """Create the button."""
        super().__init__(coordinator, mower_id, scheduler, schedule_id, "schedule_skip_next")

    async def async_press(self) -> None:
        """Skip the next firing (MowDeck answers 409 when there is none)."""
        await self.async_schedule_action(
            self.coordinator.client.skip_next_run(self.schedule_id),
            "schedule_skip_next",
            "schedule.edit",
        )


class VisionStartButton(MowDeckEntity, ButtonEntity):
    """Starts a Vision analysis of the mower (``POST /vision/mowers/{id}/recover``, task
    ``recover``, ``mower.drive``): the mower takes a photo and MowDeck proposes how to free it.
    The proposal shows in the AI recovery sensor and image; approving it happens in MowDeck."""

    entity_description = ButtonEntityDescription(key="start_ai_analysis")

    def __init__(
        self, coordinator: MowDeckCoordinator, mower_id: str, vision: VisionCoordinator
    ) -> None:
        """Create the button."""
        super().__init__(coordinator, mower_id, "start_ai_analysis", service=vision)
        self._vision = vision

    async def async_press(self) -> None:
        """Start the analysis; MowDeck refuses while a proposal of the mower is open."""
        proposal = await self.async_call_api(
            self.coordinator.client.start_vision(self.mower_id),
            "start_ai_analysis",
            "mower.drive",
        )
        self._vision.async_set_proposal(self.mower_id, proposal)
