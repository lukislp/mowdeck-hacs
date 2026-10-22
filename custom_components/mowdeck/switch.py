"""Switch of a MowDeck mower: its camera light (headlamp), for mowers that have one."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .entity import MowDeckEntity, async_add_mower_entities

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MowDeckConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """The light of every mower whose capabilities say it can be switched."""
    async_add_mower_entities(
        entry,
        async_add_entities,
        lambda coordinator, m: (
            [LightSwitch(coordinator, m)]
            if coordinator.data.capabilities.get(m, {}).get("light")
            else []
        ),
        domain=Platform.SWITCH,
    )


class LightSwitch(MowDeckEntity, SwitchEntity):
    """The mower's light (``set_light``); the state is what the mower reports (``light_on``)."""

    entity_description = SwitchEntityDescription(key="light")

    def __init__(self, coordinator: MowDeckCoordinator, mower_id: str) -> None:
        """Create the switch."""
        super().__init__(coordinator, mower_id, "light")

    @property
    def is_on(self) -> bool | None:
        """The reported light state; unknown while the mower has not reported it."""
        value = self.mower_state.get("light_on")
        return value if isinstance(value, bool) else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Send ``set_light`` on."""
        await self.async_send_command({"kind": "set_light", "on": True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Send ``set_light`` off."""
        await self.async_send_command({"kind": "set_light", "on": False})
