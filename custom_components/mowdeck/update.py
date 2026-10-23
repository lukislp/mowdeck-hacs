"""Firmware of a MowDeck mower (``MowerState.firmware_info``) and of each RTK base station
(``MowerState.base_station.firmware_info``) as update entities.

Only for mowers whose capabilities have ``firmware_update``. Installing sends
``firmware_update`` with the offered version; MowDeck refuses it unless the mower is docked
with enough battery and idle, and the command needs the token permission ``fleet.manage``.
The vendor is asked for new versions by MowDeck, not by Home Assistant.

A base station's update is installed through a linked mower whose connection can relay it
(``base_station_firmware_update``): ``POST /fleet/base-stations/{id}/firmware/update``,
``can_manage_mowers`` on the account (``fleet.manage``); the relaying mower must not mow,
return, update or drive. ``homeassistant.update_entity`` on it asks the vendor now
(``POST .../firmware/check``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityDescription,
    UpdateEntityFeature,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .base_station import (
    BaseStationCoordinator,
    BaseStationEntity,
    async_add_base_station_entities,
)
from .entity import MowDeckEntity, async_add_mower_entities

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator

PARALLEL_UPDATES = 1

# Home Assistant shows at most this many characters as the release summary.
SUMMARY_MAX = 255


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MowDeckConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """The firmware of every mower whose connection can update it."""
    async_add_mower_entities(
        entry,
        async_add_entities,
        lambda coordinator, m: (
            [FirmwareUpdate(coordinator, m)]
            if (coordinator.data.capabilities.get(m) or {}).get("firmware_update")
            else []
        ),
        domain=Platform.UPDATE,
    )
    async_add_base_station_entities(
        entry,
        async_add_entities,
        lambda base_stations, station_id: [BaseStationFirmware(base_stations, station_id)],
    )


class FirmwareUpdate(MowDeckEntity, UpdateEntity):
    """Installed and offered firmware, progress while it installs."""

    entity_description = UpdateEntityDescription(
        key="firmware", device_class=UpdateDeviceClass.FIRMWARE
    )
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.PROGRESS
        | UpdateEntityFeature.RELEASE_NOTES
    )

    def __init__(self, coordinator: MowDeckCoordinator, mower_id: str) -> None:
        """Create the update entity."""
        super().__init__(coordinator, mower_id, "firmware")

    @property
    def _info(self) -> dict[str, Any]:
        info = self.mower_state.get("firmware_info")
        return info if isinstance(info, dict) else {}

    @property
    def installed_version(self) -> str | None:
        """The version running on the mower."""
        installed = self._info.get("installed") or self.mower_state.get("firmware")
        return str(installed) if installed else None

    @property
    def latest_version(self) -> str | None:
        """The offered version while one is available, else the installed one."""
        available = self._info.get("available")
        if available and self._info.get("update_state") in ("available", "installing", "failed"):
            return str(available)
        return self.installed_version

    @property
    def in_progress(self) -> bool:
        """An update is installing (``update_state`` installing or activity ``updating``)."""
        return (
            self._info.get("update_state") == "installing"
            or self.mower_state.get("activity") == "updating"
        )

    @property
    def update_percentage(self) -> int | None:
        """Percent done while installing, if the mower reports it."""
        progress = self._info.get("progress")
        if not self.in_progress or isinstance(progress, bool):
            return None
        return int(progress) if isinstance(progress, int | float) else None

    @property
    def release_summary(self) -> str | None:
        """The start of the release notes (Home Assistant shows at most 255 characters)."""
        notes = self._info.get("release_notes")
        if not notes:
            return None
        text = str(notes).strip()
        return text if len(text) <= SUMMARY_MAX else text[: SUMMARY_MAX - 1].rstrip() + "…"

    async def async_release_notes(self) -> str | None:
        """The full release notes of the offered version."""
        notes = self._info.get("release_notes")
        return str(notes) if notes else None

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        """Send ``firmware_update`` with the offered version (MowDeck checks the rest)."""
        await self.async_send_command(
            {"kind": "firmware_update", "version": version or self.latest_version}
        )


class BaseStationFirmware(BaseStationEntity, UpdateEntity):
    """Installed and offered firmware of a base station, installed through a linked mower."""

    _attr_device_class = UpdateDeviceClass.FIRMWARE

    def __init__(self, base_stations: BaseStationCoordinator, station_id: str) -> None:
        """Create the update entity."""
        super().__init__(base_stations, station_id, "firmware")

    @property
    def _info(self) -> dict[str, Any]:
        return self.coordinator.firmware_info(self.station_id)

    @property
    def supported_features(self) -> UpdateEntityFeature:
        """Install only through a linked mower that can relay it."""
        features = UpdateEntityFeature.PROGRESS | UpdateEntityFeature.RELEASE_NOTES
        if self.coordinator.update_relay(self.station_id) is not None:
            features |= UpdateEntityFeature.INSTALL
        return features

    @property
    def installed_version(self) -> str | None:
        """The version running on the base station."""
        installed = self._info.get("installed") or self.base_state.get("firmware")
        return str(installed) if installed else None

    @property
    def latest_version(self) -> str | None:
        """The offered version while one is available, else the installed one."""
        available = self._info.get("available")
        if available and self._info.get("update_state") in ("available", "installing", "failed"):
            return str(available)
        return self.installed_version

    @property
    def in_progress(self) -> bool:
        """An update of the base station is installing."""
        return self._info.get("update_state") == "installing"

    @property
    def update_percentage(self) -> int | None:
        """Percent done while installing, if reported."""
        progress = self._info.get("progress")
        if not self.in_progress or isinstance(progress, bool):
            return None
        return int(progress) if isinstance(progress, int | float) else None

    @property
    def release_summary(self) -> str | None:
        """The start of the release notes."""
        notes = self._info.get("release_notes")
        if not notes:
            return None
        text = str(notes).strip()
        return text if len(text) <= SUMMARY_MAX else text[: SUMMARY_MAX - 1].rstrip() + "…"

    async def async_release_notes(self) -> str | None:
        """The full release notes of the offered version."""
        notes = self._info.get("release_notes")
        return str(notes) if notes else None

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        """Install the offered version through the relaying mower (MowDeck checks the rest)."""
        await self.async_call_api(
            self.coordinator.client.update_base_station_firmware(
                self.station_id,
                version or self.latest_version or "",
                self.coordinator.update_relay(self.station_id),
            ),
            "base_station_firmware_update",
            "fleet.manage",
        )

    async def async_update(self) -> None:
        """``homeassistant.update_entity``: ask the vendor for the base station's firmware now."""
        info = await self.async_call_api(
            self.coordinator.client.check_base_station_firmware(self.station_id),
            "base_station_firmware_check",
            "mower.view",
        )
        self.coordinator.async_set_checked(self.station_id, info)
