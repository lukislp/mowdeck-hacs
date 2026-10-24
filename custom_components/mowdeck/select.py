"""Selects of a MowDeck mower: which map areas "start mowing" mows, the mowing pattern,
obstacle detection and path order of the mower's mowing defaults (the latter two where the
mower declares them in ``start_options``), and one per option setting the mower declares
(``settings.py``).

The area select is the shortcut (all areas or one); the area switches (``switch.py``) make the
multi-area selection, which the select then shows as ``selection``. Both edit the same
selection, which the select restores after a restart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from .const import ALL_AREAS, CAN_CONTROL, SEVERAL_AREAS
from .entity import MowDeckEntity, async_add_mower_entities
from .settings import SettingEntity, setting_entities
from .start_options import OBSTACLE_DETECTION, PATH_ORDERS, native_patterns, offered_options

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
    """One area select per mower; the pattern select where the token may control the mower
    and the mower runs native patterns."""

    def entities(coordinator: MowDeckCoordinator, mower_id: str) -> list[SelectEntity]:
        selects: list[SelectEntity] = [AreaSelect(coordinator, mower_id)]
        caps = coordinator.data.capabilities.get(mower_id) or {}
        if not coordinator.permitted(mower_id, CAN_CONTROL):
            return selects
        if native_patterns(caps):
            selects.append(PatternSelect(coordinator, mower_id))
        selects += [
            JobOptionSelect(coordinator, mower_id, description)
            for description in JOB_OPTION_SELECTS
            if offered_options(caps, description.declared, description.known)
        ]
        return selects

    async_add_mower_entities(entry, async_add_entities, entities)
    async_add_mower_entities(
        entry,
        async_add_entities,
        lambda c, m: setting_entities(c, m, "enum", lambda c, m, key: [SettingSelect(c, m, key)]),
    )


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
        """Restore the last selection (older versions stored only the selected label); the
        area switches, which may already be written, follow at once."""
        await super().async_added_to_hass()
        extra = await self.async_get_last_extra_data()
        if extra is not None and "area_ids" in extra.as_dict():
            stored = extra.as_dict()["area_ids"]
            self.coordinator.area_selection[self.mower_id] = (
                [str(a) for a in stored] if isinstance(stored, list) else None
            )
            self.coordinator.async_update_listeners()
            return
        last = await self.async_get_last_state()
        if last is not None and last.state in self._labels():
            self.coordinator.area_selection[self.mower_id] = [self._labels()[last.state]]
            self.coordinator.async_update_listeners()


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


@dataclass(frozen=True, kw_only=True)
class JobOptionDescription(SelectEntityDescription):
    """A job option of the mowing defaults: its key in ``MowPattern``, the list in
    ``start_options`` that declares its values and the values MowDeck knows."""

    field: str
    declared: str
    known: list[str]


JOB_OPTION_SELECTS: tuple[JobOptionDescription, ...] = (
    JobOptionDescription(
        key="mow_obstacle_detection",
        field="obstacle_detection",
        declared="obstacle_detection",
        known=OBSTACLE_DETECTION,
        entity_category=EntityCategory.CONFIG,
    ),
    JobOptionDescription(
        key="mow_order",
        field="order",
        declared="orders",
        known=PATH_ORDERS,
        entity_category=EntityCategory.CONFIG,
    ),
)


class JobOptionSelect(MowDeckEntity, SelectEntity):
    """Obstacle detection or path order of the mower's mowing defaults (``mower.control``),
    with the values the mower declares in ``start_options``; unknown while the mower's own
    default applies. Kept, unavailable, while the mower no longer declares the option."""

    entity_description: JobOptionDescription

    def __init__(
        self, coordinator: MowDeckCoordinator, mower_id: str, description: JobOptionDescription
    ) -> None:
        """Create the select from its description."""
        super().__init__(coordinator, mower_id, description.key)
        self.entity_description = description

    @property
    def options(self) -> list[str]:
        """The values the mower declares."""
        description = self.entity_description
        return offered_options(self.capabilities, description.declared, description.known)

    @property
    def current_option(self) -> str | None:
        """The saved value."""
        value = self.coordinator.mow_pattern(self.mower_id).get(self.entity_description.field)
        return value if value in self.options else None

    async def async_select_option(self, option: str) -> None:
        """Save the value into the mowing defaults."""
        await async_save_mow_defaults(self, {self.entity_description.field: option})


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


class SettingSelect(SettingEntity, SelectEntity):
    """An option setting of the mower (``set_setting`` with one of the declared options)."""

    setting_type = "enum"

    @property
    def options(self) -> list[str]:
        """The declared options, in the declared order."""
        return [str(option) for option in self.spec.get("options") or []]

    @property
    def current_option(self) -> str | None:
        """The reported option; unknown while the mower has not reported one it declares."""
        value = self.setting_value
        return value if isinstance(value, str) and value in self.options else None

    async def async_select_option(self, option: str) -> None:
        """Send the option."""
        await self.async_set_setting(option)
