"""Buttons of a MowDeck mower: emergency stop (optional) and return to dock."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant

from .const import CONF_EXPOSE_EMERGENCY_STOP, DEFAULT_EXPOSE_EMERGENCY_STOP
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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MowDeckConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Return to dock always; the emergency stop when the option says so."""
    descriptions = [RETURN_TO_DOCK]
    if entry.options.get(CONF_EXPOSE_EMERGENCY_STOP, DEFAULT_EXPOSE_EMERGENCY_STOP):
        descriptions.insert(0, EMERGENCY_STOP)
    async_add_mower_entities(
        entry,
        async_add_entities,
        lambda coordinator, m: [MowDeckButton(coordinator, m, d) for d in descriptions],
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
