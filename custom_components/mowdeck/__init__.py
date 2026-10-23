"""The MowDeck integration: MowDeck mowers in Home Assistant through the public MowDeck API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL, Platform
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .api import MowDeckClient
from .camera import storage_key as camera_storage_key
from .const import CONF_API_TOKEN, DOMAIN
from .coordinator import MowDeckCoordinator
from .health import HealthCoordinator
from .heatmap import HeatmapCoordinator
from .issues import IssueMonitor
from .notifications import NotificationHub
from .notifications import storage_key as notifications_storage_key
from .realtime import RealtimeClient
from .scheduler import SchedulerCoordinator
from .services import async_setup_services
from .vision import VisionCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CALENDAR,
    Platform.CAMERA,
    Platform.DEVICE_TRACKER,
    Platform.EVENT,
    Platform.IMAGE,
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
    health: HealthCoordinator
    scheduler: SchedulerCoordinator
    notifications: NotificationHub
    vision: VisionCoordinator
    heatmap: HeatmapCoordinator


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

    health = HealthCoordinator(hass, entry, coordinator)
    scheduler = SchedulerCoordinator(hass, entry, coordinator)
    notifications = NotificationHub(hass, entry, coordinator)
    await notifications.async_load()
    vision = VisionCoordinator(hass, entry, coordinator)
    realtime = RealtimeClient(
        hass,
        client,
        coordinator,
        entry_id=entry.entry_id,
        on_auth_failed=_auth_failed,
        on_notification=notifications.async_push,
        on_vision_proposal=vision.async_push,
    )
    # The further services never block the setup; their entities wait for them.
    await health.async_refresh()
    await scheduler.async_refresh()
    await vision.async_refresh()
    # Heatmaps are slow queries: read in the background, their entities wait for them.
    heatmap = HeatmapCoordinator(hass, entry, coordinator)
    entry.runtime_data = MowDeckRuntimeData(
        client, coordinator, realtime, health, scheduler, notifications, vision, heatmap
    )
    entry.async_on_unload(
        coordinator.async_add_listener(_device_updater(hass, entry.entry_id, coordinator))
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(await IssueMonitor(hass, entry, coordinator).async_start())

    # Background tasks of an entry are cancelled when it unloads; stop() closes the socket.
    entry.async_create_background_task(hass, realtime.run(), f"{DOMAIN} realtime")
    entry.async_create_background_task(hass, heatmap.async_refresh(), f"{DOMAIN} heatmaps")
    entry.async_on_unload(realtime.stop)
    entry.async_on_unload(notifications.async_save)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MowDeckConfigEntry) -> bool:
    """Unload the platforms; the realtime task ends through ``async_on_unload``."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _device_updater(
    hass: HomeAssistant, entry_id: str, coordinator: MowDeckCoordinator
) -> CALLBACK_TYPE:
    """A listener that keeps name, model and firmware of the mower devices current."""
    known: dict[str, tuple[str | None, str | None, str | None]] = {}

    @callback
    def _update() -> None:
        registry = dr.async_get(hass)
        for mower_id, mower in coordinator.data.mowers.items():
            info = (
                mower.get("name"),
                coordinator.model(mower_id),
                coordinator.firmware_version(mower_id),
            )
            if known.get(mower_id) == info:
                continue
            device = registry.async_get_device_by_identifier((DOMAIN, mower_id), entry_id)
            if device is None:
                continue
            known[mower_id] = info
            name, model, firmware = info
            changes: dict[str, Any] = {}
            if name and device.name != name:
                changes["name"] = name
            if model and device.model != model:
                changes["model"] = model
            if firmware and device.sw_version != firmware:
                changes["sw_version"] = firmware
            if changes:
                registry.async_update_device(device.id, **changes)

    return _update


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: MowDeckConfigEntry, device: dr.DeviceEntry
) -> bool:
    """A mower's device can be deleted once the API token no longer sees the mower."""
    mowers = entry.runtime_data.coordinator.data.mowers
    return not any(domain == DOMAIN and ident in mowers for domain, ident in device.identifiers)


async def async_remove_entry(hass: HomeAssistant, entry: MowDeckConfigEntry) -> None:
    """Forget the entry's stored camera list and delivered notification ids."""
    await Store[dict[str, object]](hass, 1, camera_storage_key(entry.entry_id)).async_remove()
    await Store[dict[str, object]](
        hass, 1, notifications_storage_key(entry.entry_id)
    ).async_remove()
