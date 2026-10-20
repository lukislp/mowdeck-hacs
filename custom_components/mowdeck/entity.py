"""Base entity and platform helper of the MowDeck integration."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import MowDeckError, MowDeckForbiddenError
from .const import DOMAIN, MANUFACTURER
from .coordinator import MowDeckCoordinator

if TYPE_CHECKING:
    from . import MowDeckConfigEntry


class MowDeckEntity(CoordinatorEntity[MowDeckCoordinator]):
    """An entity of one mower; one device per mower."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MowDeckCoordinator, mower_id: str, key: str) -> None:
        """Bind the entity to a mower; ``key`` is the unique id suffix and translation key."""
        super().__init__(coordinator)
        self.mower_id = mower_id
        self._attr_unique_id = f"{mower_id}_{key}"
        self._attr_translation_key = key
        mower = coordinator.data.mowers.get(mower_id, {})
        caps = coordinator.data.capabilities.get(mower_id, {})
        state = coordinator.data.states.get(mower_id, {})
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mower_id)},
            name=mower.get("name") or "MowDeck mower",
            manufacturer=MANUFACTURER,
            model=caps.get("model") or mower.get("model"),
            serial_number=mower.get("serial"),
            sw_version=state.get("firmware"),
            configuration_url=f"{coordinator.client.base_url}/mowers/{mower_id}",
        )

    @property
    def mower_state(self) -> dict[str, Any]:
        """The newest state of the mower (empty while unknown)."""
        return self.coordinator.data.states.get(self.mower_id) or {}

    async def async_send_command(self, command: dict[str, Any]) -> dict[str, Any]:
        """Send a Fleet command; API errors become translated Home Assistant errors."""
        try:
            return await self.coordinator.client.send_command(self.mower_id, command)
        except MowDeckForbiddenError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_forbidden",
                translation_placeholders={"command": str(command["kind"])},
            ) from err
        except MowDeckError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"command": str(command["kind"]), "error": str(err)},
            ) from err

    @property
    def available(self) -> bool:
        """Available while the mower is visible to the token and the API answers."""
        return super().available and self.mower_id in self.coordinator.data.mowers


@callback
def async_add_mower_entities(
    entry: MowDeckConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[MowDeckCoordinator, str], Iterable[Entity]],
) -> None:
    """Add the entities of every mower now and of mowers that later polls discover."""
    coordinator = entry.runtime_data.coordinator
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = [mower_id for mower_id in coordinator.data.mowers if mower_id not in known]
        if not new:
            return
        known.update(new)
        async_add_entities([entity for m in new for entity in factory(coordinator, m)])

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))
