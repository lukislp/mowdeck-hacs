"""Schedules and the rain decision of MowDeck's Scheduler in their own coordinator.

Polled every ``SCHEDULER_REFRESH`` and right after every schedule action: the schedules the
token sees, their newest runs, the next firing that is not skipped, the rain decision per
mower and whether the token may change or start each schedule. A MowDeck without the
Scheduler (404) simply has no schedule entities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import MowDeckAuthError, MowDeckError, MowDeckForbiddenError, MowDeckNotFoundError
from .const import CAN_CONTROL, CAN_EDIT_SCHEDULE, SCHEDULER_REFRESH
from .coordinator import MowDeckCoordinator, MowDeckServiceCoordinator

if TYPE_CHECKING:
    from . import MowDeckConfigEntry

_LOGGER = logging.getLogger(__name__)

# Run records read per schedule (a group schedule has one per mower and firing).
RUNS_PER_SCHEDULE = 10
# Firings previewed when the next one is skipped.
PREVIEW_COUNT = 3


def parse_time(value: Any) -> datetime | None:
    """An ISO timestamp of the API as an aware datetime; None for anything else."""
    return dt_util.parse_datetime(value) if isinstance(value, str) else None


def target_ref(schedule: dict[str, Any]) -> str:
    """The schedule's target as an authorization object (``mower:<id>`` or ``group:<id>``)."""
    return f"{schedule.get('target_type')}:{schedule.get('target_id')}"


@dataclass
class SchedulerData:
    """What the Scheduler said at the last poll."""

    # False when this MowDeck runs no Scheduler (the API answered 404).
    available: bool = True
    schedules: dict[str, dict[str, Any]] = field(default_factory=dict)
    runs: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # The next firing that is not skipped, for schedules whose next one is skipped.
    next_unskipped: dict[str, datetime | None] = field(default_factory=dict)
    weather: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Relations the token holds on each target; missing while unknown.
    permissions: dict[str, frozenset[str]] = field(default_factory=dict)


class SchedulerCoordinator(MowDeckServiceCoordinator[SchedulerData]):
    """Polls schedules, runs and the rain decision."""

    service = "scheduler"
    permission = "scheduler.view"

    def __init__(
        self, hass: HomeAssistant, entry: MowDeckConfigEntry, state: MowDeckCoordinator
    ) -> None:
        """Create the coordinator (refreshed every ``SCHEDULER_REFRESH``)."""
        super().__init__(hass, entry, state, update_interval=SCHEDULER_REFRESH)
        self.data = SchedulerData()

    async def _async_fetch(self) -> SchedulerData:
        try:
            schedules = await self.client.list_schedules()
        except MowDeckNotFoundError:
            return SchedulerData(available=False)
        data = SchedulerData(schedules={str(s["id"]): s for s in schedules})
        for schedule_id, schedule in data.schedules.items():
            data.runs[schedule_id] = await self.client.list_schedule_runs(
                schedule_id, limit=RUNS_PER_SCHEDULE
            )
            if _next_is_skipped(schedule):
                preview = await self.client.preview_schedule(schedule_id, count=PREVIEW_COUNT)
                data.next_unskipped[schedule_id] = next(
                    (parse_time(i.get("at")) for i in preview if not i.get("skipped")), None
                )
        for mower_id in list(self.state.data.mowers):
            weather = await self._async_weather(data, mower_id)
            if weather is not None:
                data.weather[mower_id] = weather
        data.permissions = await self._async_permissions(data)
        return data

    async def _async_weather(self, data: SchedulerData, mower_id: str) -> dict[str, Any] | None:
        """The rain decision with the rain settings of the mower's next schedule."""
        upcoming = self._upcoming(data, mower_id)
        settings = upcoming[0][1].get("rain_pause") if upcoming else None
        try:
            answer = await self.client.get_weather(
                mower_id, settings if isinstance(settings, dict) else None
            )
        except (MowDeckAuthError, MowDeckForbiddenError):
            raise
        except MowDeckError as err:
            _LOGGER.debug("No rain decision for %s: %s", mower_id, err)
            return None
        decision = answer.get("decision")
        return dict(decision) if isinstance(decision, dict) else None

    async def _async_permissions(self, data: SchedulerData) -> dict[str, frozenset[str]]:
        """May the token change (and start) each schedule's target? Unknown on failure."""
        targets = sorted({target_ref(s) for s in data.schedules.values()})
        relations = (CAN_EDIT_SCHEDULE, CAN_CONTROL)
        if not targets:
            return {}
        checks = [(relation, target) for target in targets for relation in relations]
        try:
            answers = await self.client.check_permissions(checks)
        except MowDeckAuthError:
            raise
        except MowDeckError as err:
            _LOGGER.debug("Schedule permission check failed: %s", err)
            return {}
        return {
            target: frozenset(
                relation
                for relation, ok in zip(relations, answers[i * 2 : i * 2 + 2], strict=True)
                if ok
            )
            for i, target in enumerate(targets)
        }

    # Read helpers

    def schedules_for(self, mower_id: str) -> list[dict[str, Any]]:
        """Schedules of the mower itself and of its groups, by name."""
        mower = self.state.data.mowers.get(mower_id) or {}
        groups = {str(g) for g in mower.get("group_ids") or []}
        found = [
            s
            for s in self.data.schedules.values()
            if (s.get("target_type") == "mower" and str(s.get("target_id")) == mower_id)
            or (s.get("target_type") == "group" and str(s.get("target_id")) in groups)
        ]
        return sorted(found, key=lambda s: (str(s.get("name") or ""), str(s["id"])))

    def permitted(self, schedule: dict[str, Any], *relations: str) -> bool:
        """The token holds every relation on the schedule's target (or that is unknown)."""
        held = self.data.permissions.get(target_ref(schedule))
        return held is None or all(relation in held for relation in relations)

    def next_firing(
        self, schedule: dict[str, Any], data: SchedulerData | None = None
    ) -> datetime | None:
        """When the schedule fires next, skipping a firing that was skipped."""
        data = data or self.data
        if not schedule.get("enabled"):
            return None
        if _next_is_skipped(schedule):
            return data.next_unskipped.get(str(schedule["id"]))
        return parse_time(schedule.get("next_run_at"))

    def _upcoming(
        self, data: SchedulerData, mower_id: str
    ) -> list[tuple[datetime, dict[str, Any]]]:
        mower = self.state.data.mowers.get(mower_id) or {}
        groups = {str(g) for g in mower.get("group_ids") or []}
        found: list[tuple[datetime, dict[str, Any]]] = []
        for schedule in data.schedules.values():
            target = str(schedule.get("target_id"))
            mine = schedule.get("target_type") == "mower" and target == mower_id
            if not (mine or (schedule.get("target_type") == "group" and target in groups)):
                continue
            at = self.next_firing(schedule, data)
            if at is not None:
                found.append((at, schedule))
        return sorted(found, key=lambda item: item[0])

    def next_mow(self, mower_id: str) -> tuple[datetime, dict[str, Any]] | None:
        """The mower's next scheduled start and its schedule."""
        upcoming = self._upcoming(self.data, mower_id)
        return upcoming[0] if upcoming else None

    def last_run(self, mower_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """The mower's newest run record of any of its schedules, with the schedule."""
        newest: tuple[datetime, dict[str, Any], dict[str, Any]] | None = None
        for schedule in self.schedules_for(mower_id):
            for run in self.data.runs.get(str(schedule["id"]), []):
                if run.get("mower_id") not in (None, mower_id):
                    continue
                started = parse_time(run.get("started_at"))
                if started is not None and (newest is None or started > newest[0]):
                    newest = (started, run, schedule)
        return (newest[1], newest[2]) if newest else None

    def fingerprint(self, mower_id: str) -> tuple[Any, ...]:
        """Which schedule entities a mower has: its schedules and what the token may do."""
        return (
            self.data.available,
            tuple(
                (
                    str(s["id"]),
                    self.permitted(s, CAN_EDIT_SCHEDULE, CAN_CONTROL),
                    self.permitted(s, CAN_CONTROL),
                )
                for s in self.schedules_for(mower_id)
            ),
        )


def _next_is_skipped(schedule: dict[str, Any]) -> bool:
    skip_until = parse_time(schedule.get("skip_until"))
    next_run = parse_time(schedule.get("next_run_at"))
    return skip_until is not None and next_run is not None and next_run <= skip_until
