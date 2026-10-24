"""Calendar of a MowDeck mower: the next mowing windows of its schedules.

The events are the firings the Scheduler previews (``GET /scheduler/schedules/{id}/preview``,
read with the schedules every ``SCHEDULER_REFRESH``) for every enabled schedule of the mower
or one of its groups: from the start to the end of the window, named after the schedule.
Firings skipped with "Skip next run" are left out. The calendar is read only; schedules are
edited in MowDeck (or switched on and off with the schedule switches).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .entity import MowDeckEntity, async_add_mower_entities
from .scheduler import parse_time

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator
    from .scheduler import SchedulerCoordinator

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MowDeckConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One mowing calendar per mower, where MowDeck runs the Scheduler."""
    scheduler = entry.runtime_data.scheduler
    async_add_mower_entities(
        entry,
        async_add_entities,
        lambda coordinator, m: (
            [MowingCalendar(coordinator, m, scheduler)] if scheduler.data.available else []
        ),
        fingerprint=lambda m: scheduler.data.available,
        listen=[scheduler],
    )


class MowingCalendar(MowDeckEntity, CalendarEntity):
    """The upcoming mowing windows of the mower's enabled schedules."""

    def __init__(
        self, coordinator: MowDeckCoordinator, mower_id: str, scheduler: SchedulerCoordinator
    ) -> None:
        """Create the calendar."""
        super().__init__(coordinator, mower_id, "mowing_schedule", service=scheduler)
        self._scheduler = scheduler

    def _events(self) -> list[CalendarEvent]:
        """Every previewed, not skipped firing of the mower's enabled schedules, by start."""
        events: list[CalendarEvent] = []
        names = dict(self.coordinator.area_labels(self.mower_id))
        for schedule in self._scheduler.schedules_for(self.mower_id):
            if not schedule.get("enabled"):
                continue
            schedule_id = str(schedule["id"])
            for item in self._scheduler.data.previews.get(schedule_id, []):
                event = _event(schedule, item, names)
                if event is not None:
                    events.append(event)
        return sorted(events, key=lambda e: (e.start_datetime_local, e.summary))

    @property
    def event(self) -> CalendarEvent | None:
        """The window running now, else the next one."""
        now = dt_util.utcnow()
        return next((e for e in self._events() if e.end_datetime_local > now), None)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """The windows that overlap the range."""
        return [
            e
            for e in self._events()
            if e.end_datetime_local > start_date and e.start_datetime_local < end_date
        ]


def _event(
    schedule: dict[str, Any], item: dict[str, Any], names: dict[str, str]
) -> CalendarEvent | None:
    """A previewed firing as an event (the description names the schedule's areas, none for
    every area); None when skipped or malformed."""
    if item.get("skipped"):
        return None
    start = parse_time(item.get("at"))
    end = parse_time(item.get("window_end"))
    if start is None or end is None or end <= start:
        return None
    areas = [names.get(str(a), str(a)) for a in schedule.get("area_ids") or []]
    return CalendarEvent(
        start=start,
        end=end,
        summary=str(schedule.get("name") or schedule["id"]),
        description=", ".join(areas) or None,
        uid=f"{schedule['id']}_{start.isoformat()}",
    )
