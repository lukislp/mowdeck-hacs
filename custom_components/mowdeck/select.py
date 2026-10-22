"""Selects of a MowDeck mower: which map areas "start mowing" mows, and the mowing pattern of
the mower's mowing defaults.

The area select is the shortcut (all areas or one); the area switches (``switch.py``) make the
multi-area selection, which the select then shows as ``selection``. Both edit the same
selection, which the select restores after a restart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from .const import ALL_AREAS, CAN_CONTROL, PATTERNS, SEVERAL_AREAS
from .entity import MowDeckEntity, async_add_mower_entities

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator

PARALLEL_UPDATES = 1


def native_patterns(caps: dict[str, Any]) -> list[str]:
    """The mower's native patterns that MowDeck's mowing defaults know, in MowDeck's order."""
    offered = {str(p) for p in caps.get("native_patterns") or []}
    return [pattern for pattern in PATTERNS if pattern in offered]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MowDeckConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One area select per mower; the pattern select where the token may control the mower
    and the mower runs native patterns."""

    def entities(coordinator: MowDeckCoordinator, mower_id: str) -> list[SelectEntity]:
        selects: list[SelectEntity] = [AreaSelect(coordinator, mower_id)]
        caps = coordinator.data.capabilities.get(mower_id) or {}
        if native_patterns(caps) and coordinator.permitted(mower_id, CAN_CONTROL):
            selects.append(PatternSelect(coordinator, mower_id))
        return selects

    async_add_mower_entities(entry, async_add_entities, entities, domain=Platform.SELECT)


@dataclass
class AreaSelection(ExtraStoredData):
    """The stored area selection: area ids, or None for every area."""

    area_ids: list[str] | None

    def as_dict(self) -> dict[str, Any]:
        """For the restore state store."""
        return {"area_ids": self.area_ids}


class AreaSelect(MowDeckEntity, SelectEntity, RestoreEntity):
    """Options: ``all``, the area names as MowDeck shows them (the custom name from Fleet, the
    mower's own name, else "Area N" in map order) and ``selection`` (several areas, chosen
    with the area switches)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: MowDeckCoordinator, mower_id: str) -> None:
        """Create the select."""
        super().__init__(coordinator, mower_id, "area")

    def _labels(self) -> dict[str, str]:
        """Option label to area id, in map order; duplicate names get the id appended."""
        labels: dict[str, str] = {}
        for area_id, label in self.coordinator.area_labels(self.mower_id):
            if label in labels or label in (ALL_AREAS, SEVERAL_AREAS):
                label = f"{label} ({area_id})"
            labels[label] = area_id
        return labels

    @property
    def options(self) -> list[str]:
        """``all``, one option per area and ``selection``."""
        return [ALL_AREAS, *self._labels(), SEVERAL_AREAS]

    @property
    def current_option(self) -> str:
        """``all`` while every area is selected, the area's label for one, else ``selection``."""
        if self.coordinator.area_selection.get(self.mower_id) is None:
            return ALL_AREAS
        chosen = self.coordinator.area_ids_to_mow(self.mower_id)
        if len(chosen) == 1:
            for label, area_id in self._labels().items():
                if area_id == chosen[0]:
                    return label
        return SEVERAL_AREAS

    async def async_select_option(self, option: str) -> None:
        """Select every area or one; ``selection`` keeps what the switches selected."""
        if option == SEVERAL_AREAS:
            return
        area_id = self._labels().get(option)
        self.coordinator.async_select_areas(self.mower_id, [area_id] if area_id else None)

    @property
    def extra_restore_state_data(self) -> AreaSelection:
        """The selection to restore after a restart."""
        return AreaSelection(self.coordinator.area_selection.get(self.mower_id))

    async def async_added_to_hass(self) -> None:
        """Restore the last selection (older versions stored only the selected label)."""
        await super().async_added_to_hass()
        extra = await self.async_get_last_extra_data()
        if extra is not None and "area_ids" in extra.as_dict():
            stored = extra.as_dict()["area_ids"]
            self.coordinator.area_selection[self.mower_id] = (
                [str(a) for a in stored] if isinstance(stored, list) else None
            )
            return
        last = await self.async_get_last_state()
        if last is not None and last.state in self._labels():
            self.coordinator.area_selection[self.mower_id] = [self._labels()[last.state]]


class PatternSelect(MowDeckEntity, SelectEntity):
    """The pattern of the mower's mowing defaults (``PUT .../mow-defaults``): what every
    client, Home Assistant's start included, starts with."""

    entity_description = SelectEntityDescription(
        key="mow_pattern", entity_category=EntityCategory.CONFIG
    )

    def __init__(self, coordinator: MowDeckCoordinator, mower_id: str) -> None:
        """Create the select."""
        super().__init__(coordinator, mower_id, "mow_pattern")

    @property
    def options(self) -> list[str]:
        """The native patterns of the mower."""
        return native_patterns(self.capabilities)

    @property
    def current_option(self) -> str | None:
        """The saved pattern (stripes until defaults are saved)."""
        name = self.coordinator.mow_pattern(self.mower_id).get("name")
        return name if name in self.options else None

    async def async_select_option(self, option: str) -> None:
        """Save the pattern into the mowing defaults."""
        await async_save_mow_defaults(self, {"name": option})


async def async_save_mow_defaults(entity: MowDeckEntity, change: dict[str, Any]) -> None:
    """Save the mower's mowing defaults with one setting changed."""
    coordinator = entity.coordinator
    pattern = {**coordinator.mow_pattern(entity.mower_id), **change}
    mower = await entity.async_call_api(
        coordinator.client.set_mow_defaults(entity.mower_id, pattern),
        "mow_defaults",
        "mower.control",
    )
    coordinator.async_set_mower(mower)
