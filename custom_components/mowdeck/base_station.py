"""RTK base stations (ADR 0047 in lukislp/mowdeck, MowDeck 0.11): one device per base station.

Fleet lists the account's base stations with their linked mowers (``GET /fleet/base-stations``,
``can_view`` on the account). A base station has no connection of its own: its live state is
``MowerState.base_station`` of a linked mower (correction link, LoRa link and channel,
satellites, its own RTK solution, Wi-Fi signal, position, whether it was moved, firmware).
Its firmware is checked (``POST .../firmware/check``) and installed (``POST .../firmware/update``)
through a linked mower.

Each base station becomes a device (``via_device`` the first linked mower) with sensors, binary
sensors, a position tracker and a firmware update entity; the platforms add them through
``async_add_base_station_entities``. A MowDeck before 0.11 (404) or a token without ``can_view``
on the whole account (403) simply has no base stations here; the mowers' own RTK sensors keep
working.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import MowDeckError, MowDeckForbiddenError, MowDeckNotFoundError
from .const import DOMAIN, MANUFACTURER, STATIC_REFRESH
from .coordinator import MowDeckCoordinator, MowDeckServiceCoordinator

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MowDeckConfigEntry

_LOGGER = logging.getLogger(__name__)

# Device identifiers of base stations: "base_station_<id>" (mowers use their bare id).
BASE_STATION_PREFIX = "base_station_"


def base_station_identifier(station_id: str) -> str:
    """The device identifier of a base station."""
    return f"{BASE_STATION_PREFIX}{station_id}"


@dataclass
class BaseStationData:
    """The account's base stations (``BaseStationOut``) by id.

    ``available`` is false while this MowDeck or this token cannot list them. ``checked``
    holds the ``FirmwareInfo`` of a firmware check until a mower state carries a newer one."""

    available: bool = False
    stations: dict[str, dict[str, Any]] = field(default_factory=dict)
    checked: dict[str, dict[str, Any]] = field(default_factory=dict)


class BaseStationCoordinator(MowDeckServiceCoordinator[BaseStationData]):
    """Polls the base station list every ``STATIC_REFRESH`` (it changes when one is registered,
    renamed, deleted or linked); the live values come with the mower states."""

    service = "base_stations"
    permission = "mower.view"

    def __init__(
        self, hass: HomeAssistant, entry: MowDeckConfigEntry, state: MowDeckCoordinator
    ) -> None:
        """Create the coordinator."""
        super().__init__(hass, entry, state, update_interval=STATIC_REFRESH)
        self.data = BaseStationData()

    async def _async_fetch(self) -> BaseStationData:
        checked = self.data.checked if self.data is not None else {}
        try:
            stations = await self.client.list_base_stations()
        except MowDeckNotFoundError:
            # A MowDeck before 0.11 has no base stations.
            return BaseStationData()
        except MowDeckForbiddenError as err:
            # Base stations need can_view on the whole account; a token scoped to a site,
            # group or mower sees its mowers' base station only in their state.
            _LOGGER.debug("Base stations are not visible to this token: %s", err)
            return BaseStationData()
        data = BaseStationData(
            available=True,
            stations={str(s["id"]): s for s in stations if s.get("id")},
            checked={k: v for k, v in checked.items() if k in {str(s.get("id")) for s in stations}},
        )
        self._async_remove_stale_devices(set(data.stations))
        return data

    @callback
    def _async_remove_stale_devices(self, station_ids: set[str]) -> None:
        """Detach the devices of base stations that are gone from the list."""
        registry = dr.async_get(self.hass)
        entry_id = self.config_entry.entry_id
        wanted = {base_station_identifier(s) for s in station_ids}
        for device in dr.async_entries_for_config_entry(registry, entry_id):
            ids = {
                identifier
                for domain, identifier in device.identifiers
                if domain == DOMAIN and identifier.startswith(BASE_STATION_PREFIX)
            }
            if ids and not ids & wanted:
                registry.async_update_device(device.id, remove_config_entry_id=entry_id)

    # Read helpers for the entities

    def linked_mowers(self, station_id: str) -> list[str]:
        """The visible mowers linked to the base station, in Fleet's order (by name)."""
        station = self.data.stations.get(station_id) or {}
        mowers = self.state.data.mowers
        return [str(m) for m in station.get("mower_ids") or [] if str(m) in mowers]

    def live_state(self, station_id: str) -> dict[str, Any]:
        """``MowerState.base_station`` of the base station as the first linked mower that
        reports it sees it (empty while none does)."""
        for mower_id in self.linked_mowers(station_id):
            state = self.state.data.states.get(mower_id) or {}
            base = state.get("base_station")
            if not isinstance(base, dict):
                continue
            reported = base.get("base_station_id")
            if reported is None or str(reported) == station_id:
                return base
        return {}

    def firmware_info(self, station_id: str) -> dict[str, Any]:
        """The base station's ``FirmwareInfo``: from the mower state, or from a firmware check
        made here when that is newer."""
        live = self.live_state(station_id).get("firmware_info")
        live = live if isinstance(live, dict) else {}
        checked = self.data.checked.get(station_id) or {}
        if checked and _checked_at(checked) >= _checked_at(live):
            return checked
        return live

    def update_relay(self, station_id: str) -> str | None:
        """The linked mower that can relay a firmware update (``base_station_firmware_update``)."""
        for mower_id in self.linked_mowers(station_id):
            caps = self.state.data.capabilities.get(mower_id) or {}
            if caps.get("base_station_firmware_update"):
                return mower_id
        return None

    @callback
    def async_set_checked(self, station_id: str, info: dict[str, Any]) -> None:
        """A fresh ``FirmwareInfo`` from a firmware check: show it at once."""
        self.data.checked[station_id] = dict(info)
        self.async_update_listeners()


def _checked_at(info: dict[str, Any]) -> datetime:
    parsed = dt_util.parse_datetime(str(info.get("checked_at") or ""))
    return parsed or datetime.min.replace(tzinfo=dt_util.UTC)


class BaseStationEntity(CoordinatorEntity[BaseStationCoordinator]):
    """An entity of one base station; it also follows the mower states (its live values)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        base_stations: BaseStationCoordinator,
        station_id: str,
        key: str,
        translation_key: str | None = None,
    ) -> None:
        """Bind the entity to a base station; ``key`` is the unique id suffix and, unless
        ``translation_key`` is given, the translation key."""
        super().__init__(base_stations)
        self.station_id = station_id
        self._attr_unique_id = f"{base_station_identifier(station_id)}_{key}"
        self._attr_translation_key = translation_key or key
        self._attr_device_info = base_station_device_info(base_stations, station_id)

    async def async_added_to_hass(self) -> None:
        """Also follow the mower states."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.state.async_add_listener(self._handle_coordinator_update)
        )

    @property
    def base_state(self) -> dict[str, Any]:
        """The live state of the base station (empty while no linked mower reports it)."""
        return self.coordinator.live_state(self.station_id)

    async def async_call_api[T](self, request: Awaitable[T], action: str, permission: str) -> T:
        """Await an API request; a 403 names ``permission``, other errors the API's reason."""
        try:
            return await request
        except MowDeckForbiddenError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="action_forbidden",
                translation_placeholders={"command": action, "permission": permission},
            ) from err
        except MowDeckError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="action_failed",
                translation_placeholders={"command": action, "error": str(err)},
            ) from err

    @property
    def available(self) -> bool:
        """Available while the base station is listed and the mower states arrive."""
        return (
            super().available
            and self.coordinator.state.last_update_success
            and self.station_id in self.coordinator.data.stations
        )


def base_station_device_info(base_stations: BaseStationCoordinator, station_id: str) -> DeviceInfo:
    """The device of a base station, reached through its first linked mower."""
    station = base_stations.data.stations.get(station_id) or {}
    mowers = base_stations.linked_mowers(station_id)
    firmware = base_stations.live_state(station_id).get("firmware")
    info = DeviceInfo(
        identifiers={(DOMAIN, base_station_identifier(station_id))},
        name=str(station.get("name") or "RTK base station"),
        manufacturer=MANUFACTURER,
        model=str(station.get("model") or station.get("device_name") or "RTK base station"),
        sw_version=str(firmware) if firmware else None,
    )
    if mowers:
        info["configuration_url"] = f"{base_stations.client.base_url}/mowers/{mowers[0]}"
        mower = dr.async_get(base_stations.hass).async_get_device_by_identifier(
            (DOMAIN, mowers[0]), base_stations.config_entry.entry_id
        )
        if mower is not None:
            info["via_device_id"] = mower.id
    return info


@callback
def async_add_base_station_entities(
    entry: MowDeckConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[BaseStationCoordinator, str], Iterable[Entity]],
) -> None:
    """Add the entities of every base station, also of base stations that appear later.

    Entities of a base station that disappears go with its device (see
    ``BaseStationCoordinator._async_remove_stale_devices``)."""
    base_stations = entry.runtime_data.base_stations
    added: set[str] = set()

    @callback
    def _sync() -> None:
        new = [
            entity
            for station_id in base_stations.data.stations
            if station_id not in added
            for entity in factory(base_stations, station_id)
        ]
        added.update(base_stations.data.stations)
        if new:
            async_add_entities(new)

    _sync()
    entry.async_on_unload(base_stations.async_add_listener(_sync))


@callback
def async_update_base_station_devices(
    hass: HomeAssistant, entry_id: str, base_stations: BaseStationCoordinator
) -> Callable[[], None]:
    """A listener that keeps name, model, firmware and the mower link of the base station
    devices current."""
    known: dict[str, DeviceInfo] = {}

    @callback
    def _update() -> None:
        registry = dr.async_get(hass)
        for station_id in base_stations.data.stations:
            info = base_station_device_info(base_stations, station_id)
            if known.get(station_id) == info:
                continue
            device = registry.async_get_device_by_identifier(
                (DOMAIN, base_station_identifier(station_id)), entry_id
            )
            if device is None:
                continue
            known[station_id] = info
            registry.async_update_device(
                device.id,
                name=info.get("name"),
                model=info.get("model"),
                sw_version=info.get("sw_version"),
                via_device_id=info.get("via_device_id"),
                configuration_url=info.get("configuration_url"),
            )

    return _update
