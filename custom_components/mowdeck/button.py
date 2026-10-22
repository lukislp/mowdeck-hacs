"""Buttons of a MowDeck mower: emergency stop (optional), return to dock, end the job, check
for firmware and record a blade change."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant

from .const import CAN_MANAGE_MOWERS, CONF_EXPOSE_EMERGENCY_STOP, DEFAULT_EXPOSE_EMERGENCY_STOP
from .entity import MowDeckEntity, async_add_mower_entities

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class MowDeckButtonDescription(ButtonEntityDescription):
    """A button and the command it sends."""

    command: dict[str, Any]


EMERGENCY_STOP = MowDeckButtonDescription(key="emergency_stop", command={"kind": "emergency_stop"})
RETURN_TO_DOCK = MowDeckButtonDescription(key="return_to_dock", command={"kind": "return_to_dock"})
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
        if (coordinator.data.capabilities.get(mower_id) or {}).get("firmware_update"):
            buttons.append(FirmwareCheckButton(coordinator, mower_id))
        if coordinator.permitted(mower_id, CAN_MANAGE_MOWERS):
            buttons.append(BladeChangedButton(coordinator, mower_id, entry))
        return buttons

    async_add_mower_entities(entry, async_add_entities, entities, domain=Platform.BUTTON)


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
