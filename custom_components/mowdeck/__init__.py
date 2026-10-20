"""The MowDeck integration: MowDeck mowers in Home Assistant through the public MowDeck API."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .api import MowDeckClient
from .camera import storage_key as camera_storage_key
from .const import CONF_API_TOKEN, DOMAIN
from .coordinator import MowDeckCoordinator
from .realtime import RealtimeClient
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.DEVICE_TRACKER,
    Platform.LAWN_MOWER,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class MowDeckRuntimeData:
    """Objects that live as long as the config entry is loaded."""

    client: MowDeckClient
    coordinator: MowDeckCoordinator
    realtime: RealtimeClient


type MowDeckConfigEntry = ConfigEntry[MowDeckRuntimeData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's services (once, independent of config entries)."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MowDeckConfigEntry) -> bool:
    """Exchange the token, load the mowers, start the WebSocket and the platforms."""
    client = MowDeckClient(
        async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_API_TOKEN]
    )
    coordinator = MowDeckCoordinator(hass, entry, client)
    # Raises ConfigEntryAuthFailed (starts reauth) or ConfigEntryNotReady (retries).
    await coordinator.async_config_entry_first_refresh()

    @callback
    def _auth_failed() -> None:
        entry.async_start_reauth(hass)

    realtime = RealtimeClient(
        hass, client, coordinator, entry_id=entry.entry_id, on_auth_failed=_auth_failed
    )
    entry.runtime_data = MowDeckRuntimeData(client, coordinator, realtime)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Background tasks of an entry are cancelled when it unloads; stop() closes the socket.
    entry.async_create_background_task(hass, realtime.run(), f"{DOMAIN} realtime")
    entry.async_on_unload(realtime.stop)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MowDeckConfigEntry) -> bool:
    """Unload the platforms; the realtime task ends through ``async_on_unload``."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: MowDeckConfigEntry) -> None:
    """Forget the entry's stored camera list."""
    await Store[dict[str, object]](hass, 1, camera_storage_key(entry.entry_id)).async_remove()
