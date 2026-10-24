"""Switches of a MowDeck mower: its camera light (headlamp), for mowers that have one, one
per schedule that mows it (enabled or not), one per map area (part of the next start or not)
and one per on/off setting the mower declares (``settings.py``)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant, callback

from .const import CAN_CONTROL, CAN_EDIT_SCHEDULE
from .entity import MowDeckEntity, ScheduleEntity, async_add_mower_entities
from .settings import SettingEntity, setting_entities

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator
    from .scheduler import SchedulerCoordinator

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MowDeckConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """The light of every mower whose capabilities say it can be switched, and the schedules
    the token may change."""
    async_add_mower_entities(
        entry,
        async_add_entities,
        lambda coordinator, m: (
            [LightSwitch(coordinator, m)]
            if coordinator.data.capabilities.get(m, {}).get("light")
            else []
        ),
        domain=Platform.SWITCH,
    )
    async_add_mower_entities(
        entry,
        async_add_entities,
        lambda coordinator, m: [
            AreaSwitch(coordinator, m, str(area["id"])) for area in coordinator.areas(m)
        ],
        domain=Platform.SWITCH,
    )
    async_add_mower_entities(
        entry,
        async_add_entities,
        lambda coordinator, m: setting_entities(
            coordinator, m, "bool", lambda c, mower, key: [SettingSwitch(c, mower, key)]
        ),
        domain=Platform.SWITCH,
    )
    scheduler = entry.runtime_data.scheduler
    async_add_mower_entities(
        entry,
        async_add_entities,
        lambda coordinator, m: [
            ScheduleSwitch(coordinator, m, scheduler, str(s["id"]))
            for s in scheduler.schedules_for(m)
            if scheduler.permitted(s, CAN_EDIT_SCHEDULE, CAN_CONTROL)
        ],
        domain=Platform.SWITCH,
        fingerprint=scheduler.fingerprint,
        listen=[scheduler],
    )


class LightSwitch(MowDeckEntity, SwitchEntity):
    """The mower's light (``set_light``); the state is what the mower reports (``light_on``)."""

    entity_description = SwitchEntityDescription(key="light")

    def __init__(self, coordinator: MowDeckCoordinator, mower_id: str) -> None:
        """Create the switch."""
        super().__init__(coordinator, mower_id, "light")

    @property
    def is_on(self) -> bool | None:
        """The reported light state; unknown while the mower has not reported it."""
        value = self.mower_state.get("light_on")
        return value if isinstance(value, bool) else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Send ``set_light`` on."""
        await self.async_send_command({"kind": "set_light", "on": True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Send ``set_light`` off."""
        await self.async_send_command({"kind": "set_light", "on": False})


class ScheduleSwitch(ScheduleEntity, SwitchEntity):
    """Enables or disables a schedule (``PATCH`` with ``enabled``).

    Only for tokens that may edit the schedule (``schedule.edit``) and control its target
    (``mower.control``). MowDeck makes whoever changes a schedule its owner, so after a toggle
    the schedule fires on behalf of this API token."""

    def __init__(
        self,
        coordinator: MowDeckCoordinator,
        mower_id: str,
        scheduler: SchedulerCoordinator,
        schedule_id: str,
    ) -> None:
        """Create the switch."""
        super().__init__(coordinator, mower_id, scheduler, schedule_id, "schedule")

    @property
    def is_on(self) -> bool | None:
        """The schedule is enabled."""
        enabled = self.schedule.get("enabled")
        return enabled if isinstance(enabled, bool) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """When and what the schedule mows, and why MowDeck disabled it (if it did)."""
        schedule = self.schedule
        return {
            "schedule_id": self.schedule_id,
            "kind": schedule.get("kind"),
            "days_of_week": schedule.get("days_of_week"),
            "interval_days": schedule.get("interval_days"),
            "start_time": schedule.get("start_time"),
            "duration_minutes": schedule.get("duration_minutes"),
            "timezone": schedule.get("timezone"),
            "area_ids": schedule.get("area_ids"),
            "target_type": schedule.get("target_type"),
            "next_run": self.scheduler.next_firing(schedule),
            "last_run_at": schedule.get("last_run_at"),
            "disabled_reason": schedule.get("disabled_reason"),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the schedule."""
        await self._set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the schedule."""
        await self._set_enabled(False)

    async def _set_enabled(self, enabled: bool) -> None:
        await self.async_schedule_action(
            self.coordinator.client.update_schedule(self.schedule_id, {"enabled": enabled}),
            "schedule_enable" if enabled else "schedule_disable",
            "schedule.edit",
        )


class AreaSwitch(MowDeckEntity, SwitchEntity):
    """Whether "start mowing" (and ``mowdeck.start_mow`` without ``area_ids``) mows this map
    area. Together the area switches are the multi-area selection; the area select shows it."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: MowDeckCoordinator, mower_id: str, area_id: str) -> None:
        """Create the switch of one area."""
        super().__init__(coordinator, mower_id, "area")
        self.area_id = area_id
        self._attr_unique_id = f"{mower_id}_area_{area_id}"
        self._attr_translation_placeholders = {"area": self._label()}

    def _label(self) -> str:
        return dict(self.coordinator.area_labels(self.mower_id)).get(self.area_id, self.area_id)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Follow a renamed area."""
        self._attr_translation_placeholders = {"area": self._label()}
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        """Available while the area is on the mower's map."""
        ids = {str(area["id"]) for area in self.coordinator.areas(self.mower_id)}
        return super().available and self.area_id in ids

    @property
    def is_on(self) -> bool:
        """The area is part of the next start."""
        return self.coordinator.area_selected(self.mower_id, self.area_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Add the area to the next start."""
        self.coordinator.async_toggle_area(self.mower_id, self.area_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Take the area out of the next start."""
        self.coordinator.async_toggle_area(self.mower_id, self.area_id, False)


class SettingSwitch(SettingEntity, SwitchEntity):
    """An on/off setting of the mower (``set_setting`` with true or false)."""

    setting_type = "bool"

    @property
    def is_on(self) -> bool | None:
        """The reported value; unknown while the mower has not reported it."""
        value = self.setting_value
        return value if isinstance(value, bool) else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch the setting on."""
        await self.async_set_setting(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch the setting off."""
        await self.async_set_setting(False)
