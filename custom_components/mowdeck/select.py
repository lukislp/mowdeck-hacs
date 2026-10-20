"""Area select of a MowDeck mower: which map area "start mowing" mows.

A Home Assistant select holds one option, so it chooses the primary area (or all areas);
several areas at once go through the ``mowdeck.start_mow`` service with ``area_ids``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity

from .const import ALL_AREAS
from .entity import MowDeckEntity, async_add_mower_entities

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MowDeckConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One area select per mower."""
    async_add_mower_entities(
        entry, async_add_entities, lambda coordinator, m: [AreaSelect(coordinator, m)]
    )


class AreaSelect(MowDeckEntity, SelectEntity, RestoreEntity):
    """Options: ``all`` plus the area names as MowDeck shows them: the custom name from Fleet,
    the mower's own name, else "Area N" in map order."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: MowDeckCoordinator, mower_id: str) -> None:
        """Create the select."""
        super().__init__(coordinator, mower_id, "area")

    def _labels(self) -> dict[str, str]:
        """Option label to area id, in map order; duplicate names get the id appended."""
        labels: dict[str, str] = {}
        for area_id, label in self.coordinator.area_labels(self.mower_id):
            if label in labels or label == ALL_AREAS:
                label = f"{label} ({area_id})"
            labels[label] = area_id
        return labels

    @property
    def options(self) -> list[str]:
        """``all`` and one option per area."""
        return [ALL_AREAS, *self._labels()]

    @property
    def current_option(self) -> str:
        """The selected area's label, ``all`` when nothing (or a vanished area) is selected."""
        selected = self.coordinator.selected_area.get(self.mower_id, ALL_AREAS)
        for label, area_id in self._labels().items():
            if area_id == selected:
                return label
        return ALL_AREAS

    async def async_select_option(self, option: str) -> None:
        """Remember the choice (in memory; restored after a restart)."""
        self.coordinator.selected_area[self.mower_id] = self._labels().get(option, ALL_AREAS)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore the last selection."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in self._labels():
            self.coordinator.selected_area[self.mower_id] = self._labels()[last.state]
