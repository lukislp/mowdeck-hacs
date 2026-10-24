"""Base entity and platform helper of the MowDeck integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Hashable, Iterable
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .api import MowDeckError, MowDeckForbiddenError
from .const import COMMAND_PERMISSION, DEFAULT_COMMAND_PERMISSION, DOMAIN, MANUFACTURER
from .coordinator import MowDeckCoordinator

if TYPE_CHECKING:
    from . import MowDeckConfigEntry
    from .scheduler import SchedulerCoordinator


def mower_device_info(coordinator: MowDeckCoordinator, mower_id: str) -> DeviceInfo:
    """The device of a mower (one per mower)."""
    mower = coordinator.data.mowers.get(mower_id, {})
    return DeviceInfo(
        identifiers={(DOMAIN, mower_id)},
        name=mower.get("name") or "MowDeck mower",
        manufacturer=MANUFACTURER,
        model=coordinator.model(mower_id),
        serial_number=mower.get("serial"),
        sw_version=coordinator.firmware_version(mower_id),
        configuration_url=f"{coordinator.client.base_url}/mowers/{mower_id}",
    )


class MowDeckEntity(CoordinatorEntity[MowDeckCoordinator]):
    """An entity of one mower; one device per mower.

    ``service`` is the coordinator of a further MowDeck service the entity reads (health,
    scheduler, ...): the entity also updates with it and is unavailable while it fails.
    """

    _attr_has_entity_name = True
    # False while the mower no longer offers the entity (a capability or a permission is
    # gone for now): it stays registered and is unavailable (``async_add_mower_entities``).
    offered = True

    def __init__(
        self,
        coordinator: MowDeckCoordinator,
        mower_id: str,
        key: str,
        *,
        service: DataUpdateCoordinator[Any] | None = None,
    ) -> None:
        """Bind the entity to a mower; ``key`` is the unique id suffix and translation key."""
        super().__init__(coordinator)
        self.mower_id = mower_id
        self.service = service
        self._attr_unique_id = f"{mower_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = mower_device_info(coordinator, mower_id)

    async def async_added_to_hass(self) -> None:
        """Also follow the service coordinator."""
        await super().async_added_to_hass()
        if self.service is not None:
            self.async_on_remove(self.service.async_add_listener(self._handle_coordinator_update))

    @property
    def mower_state(self) -> dict[str, Any]:
        """The newest state of the mower (empty while unknown)."""
        return self.coordinator.data.states.get(self.mower_id) or {}

    @property
    def capabilities(self) -> dict[str, Any]:
        """The mower's capabilities (empty while unknown)."""
        return self.coordinator.data.capabilities.get(self.mower_id) or {}

    async def async_send_command(self, command: dict[str, Any]) -> dict[str, Any]:
        """Send a Fleet command; API errors become translated Home Assistant errors."""
        kind = str(command["kind"])
        return await self.async_call_api(
            self.coordinator.client.send_command(self.mower_id, command),
            kind,
            COMMAND_PERMISSION.get(kind, DEFAULT_COMMAND_PERMISSION),
            forbidden_key="command_forbidden",
            failed_key="command_failed",
        )

    async def async_call_api[T](
        self,
        request: Awaitable[T],
        action: str,
        permission: str,
        *,
        forbidden_key: str = "action_forbidden",
        failed_key: str = "action_failed",
    ) -> T:
        """Await an API request; a 403 names ``permission``, other errors the API's reason."""
        try:
            return await request
        except MowDeckForbiddenError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=forbidden_key,
                translation_placeholders={"command": action, "permission": permission},
            ) from err
        except MowDeckError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=failed_key,
                translation_placeholders={"command": action, "error": str(err)},
            ) from err

    @property
    def available(self) -> bool:
        """Available while the mower offers the entity, the mower is visible to the token and
        the API answers."""
        if not self.offered:
            return False
        if self.service is not None and not self.service.last_update_success:
            return False
        return super().available and self.mower_id in self.coordinator.data.mowers


@callback
def async_add_mower_entities(
    entry: MowDeckConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[MowDeckCoordinator, str], Iterable[Entity]],
    *,
    domain: str | None = None,
    gone: Callable[[Entity], bool] | None = None,
    fingerprint: Callable[[str], Hashable] | None = None,
    listen: Iterable[DataUpdateCoordinator[Any]] = (),
    signals: Iterable[str] = (),
) -> None:
    """Add the entities of every mower and keep them in line with what the mower offers.

    The factory runs for a new mower and again whenever the mower's ``fingerprint`` changes
    (default: capabilities, permissions and map areas; ``listen`` adds coordinators and
    ``signals`` dispatcher signals whose updates are checked too). Entities it newly yields
    are added. An entity it no longer yields stays registered (with its entity id, name, area
    and enabled state) and is unavailable until the factory yields it again: a capability that
    comes and goes with a link (the Bluetooth bridge, ...) or a revoked permission must not cost
    the owner's customisations. Only an entity for which ``gone`` says the thing it stands for
    no longer exists (a deleted schedule, a map area that was removed) is removed from the
    ``domain`` entity registry. Entities the factory never yielded are not created. Mowers that
    disappear keep their entities until their device is removed.
    """
    coordinator = entry.runtime_data.coordinator
    fingerprint_of = fingerprint or coordinator.fingerprint
    added: dict[str, dict[str, Entity]] = {}
    seen: dict[str, Hashable] = {}

    @callback
    def _sync(*_: Any) -> None:
        new: list[Entity] = []
        for mower_id in list(coordinator.data.mowers):
            current = fingerprint_of(mower_id)
            if mower_id in seen and seen[mower_id] == current:
                continue
            seen[mower_id] = current
            known = added.setdefault(mower_id, {})
            wanted: set[str] = set()
            for entity in factory(coordinator, mower_id):
                unique_id = str(entity.unique_id)
                wanted.add(unique_id)
                if unique_id not in known:
                    known[unique_id] = entity
                    new.append(entity)
            for unique_id, entity in list(known.items()):
                offered = unique_id in wanted
                if not offered and gone is not None and domain is not None and gone(entity):
                    _async_remove_entity(entry, domain, unique_id)
                    del known[unique_id]
                    continue
                _async_set_offered(entity, offered)
        if new:
            async_add_entities(new)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))
    for other in listen:
        entry.async_on_unload(other.async_add_listener(_sync))
    for signal in signals:
        entry.async_on_unload(async_dispatcher_connect(coordinator.hass, signal, _sync))


@callback
def _async_set_offered(entity: Entity, offered: bool) -> None:
    """Mark an entity as offered by its mower or not; a change shows at once."""
    if not isinstance(entity, MowDeckEntity) or entity.offered is offered:
        return
    entity.offered = offered
    platform = entity.platform
    if platform is not None and platform.entities.get(entity.entity_id) is entity:
        entity.async_write_ha_state()


@callback
def _async_remove_entity(entry: MowDeckConfigEntry, domain: str, unique_id: str) -> None:
    """Remove an entity whose subject no longer exists from the entity registry."""
    registry = er.async_get(entry.runtime_data.coordinator.hass)
    entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
    if entity_id is not None:
        registry.async_remove(entity_id)


class ScheduleEntity(MowDeckEntity):
    """An entity of one schedule shown on one of the mowers it mows.

    A group schedule appears on every mower of the group. The name carries the schedule's
    current name; the entity is unavailable while the schedule is gone."""

    def __init__(
        self,
        coordinator: MowDeckCoordinator,
        mower_id: str,
        scheduler: SchedulerCoordinator,
        schedule_id: str,
        key: str,
    ) -> None:
        """Bind the entity to a schedule; the unique id is ``<mower>_<key>_<schedule>``."""
        super().__init__(coordinator, mower_id, key, service=scheduler)
        self.scheduler = scheduler
        self.schedule_id = schedule_id
        self._attr_unique_id = f"{mower_id}_{key}_{schedule_id}"
        self._attr_translation_placeholders = self._placeholders()

    @property
    def schedule(self) -> dict[str, Any]:
        """The schedule as the Scheduler last answered it (empty once deleted)."""
        return self.scheduler.data.schedules.get(self.schedule_id) or {}

    def _placeholders(self) -> dict[str, str]:
        return {"schedule": str(self.schedule.get("name") or self.schedule_id)}

    @callback
    def _handle_coordinator_update(self) -> None:
        """Follow a renamed schedule."""
        self._attr_translation_placeholders = self._placeholders()
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        """Available while the schedule exists."""
        return super().available and bool(self.schedule)

    async def async_schedule_action[T](
        self, request: Awaitable[T], action: str, permission: str
    ) -> T:
        """Run a Scheduler request, then refresh the schedules at once."""
        try:
            return await self.async_call_api(request, action, permission)
        finally:
            await self.scheduler.async_refresh()
