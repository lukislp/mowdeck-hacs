"""Firmware of a MowDeck mower as an update entity (``MowerState.firmware_info``).

Only for mowers whose capabilities have ``firmware_update``. Installing sends
``firmware_update`` with the offered version; MowDeck refuses it unless the mower is docked
with enough battery and idle, and the command needs the token permission ``fleet.manage``.
The vendor is asked for new versions by MowDeck, not by Home Assistant.
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
