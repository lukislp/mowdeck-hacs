"""The mowing calendar: the previewed windows of the mower's enabled schedules."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mowdeck.calendar import _event

from .fake_mowdeck import SCHEDULE_ID, FakeMowDeck, in_hours, schedule

CALENDAR = "calendar.luba_mowing_schedule"


def state_of(hass: HomeAssistant, entity_id: str) -> Any:
    """The state object of an entity (asserting it exists)."""
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


async def events(hass: HomeAssistant, days: int = 14) -> list[dict[str, Any]]:
    """The calendar's events of the next ``days`` days (calendar.get_events)."""
    now = dt_util.utcnow()
    answer: Any = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": CALENDAR,
            "start_date_time": (now - timedelta(hours=3)).isoformat(),
            "end_date_time": (now + timedelta(days=days)).isoformat(),
        },
        blocking=True,
        return_response=True,
    )
    return list(answer[CALENDAR]["events"])


@pytest.mark.usefixtures("loaded_entry")
async def test_next_windows(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """The next window is the calendar's event; get_events lists the previewed windows."""
    calendar = state_of(hass, CALENDAR)
    assert calendar.state == "off"
    assert calendar.attributes["message"] == "Morning"
    found = await events(hass)
    assert len(found) == 10
    assert {e["summary"] for e in found} == {"Morning"}
    assert "description" not in found[0]
    start = dt_util.parse_datetime(found[0]["start"])
    end = dt_util.parse_datetime(found[0]["end"])
    assert start is not None and end is not None
    assert end - start == timedelta(hours=2)
    assert await events(hass, days=3) == found[:3]


async def test_running_window_skips_and_areas(
    hass: HomeAssistant, fake_api: FakeMowDeck, config_entry: MockConfigEntry
) -> None:
    """A window that runs now turns the calendar on; a skipped firing is left out; the
    description names the schedule's areas."""
    assert fake_api.schedules is not None
    fake_api.schedules[SCHEDULE_ID] = schedule(next_run_at=in_hours(-1), area_ids=["zone-back"])
    other = schedule(id="other", name="Evening", next_run_at=in_hours(30))
    other["skip_until"] = other["next_run_at"]
    fake_api.schedules["other"] = other
    fake_api.schedules["off"] = schedule(id="off", name="Off", enabled=False)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    calendar = state_of(hass, CALENDAR)
    assert calendar.state == "on"
    assert calendar.attributes["message"] == "Morning"
    assert calendar.attributes["description"] == "Back"
    found = await events(hass, days=2)
    assert [e["summary"] for e in found] == ["Morning", "Morning", "Morning"]
    found = await events(hass, days=3)
    # Evening's first firing (in 30 h) is skipped; its second one (in 54 h) is listed.
    assert [e["summary"] for e in found] == ["Morning", "Morning", "Morning", "Evening", "Morning"]
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_no_scheduler_no_calendar(
    hass: HomeAssistant, fake_api: FakeMowDeck, config_entry: MockConfigEntry
) -> None:
    """A MowDeck without the Scheduler has no calendar."""
    fake_api.schedules = None
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(CALENDAR) is None
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


def test_malformed_previews_are_ignored() -> None:
    """A window without times or ending before it starts is no event."""
    item = {"id": "s", "name": "S"}
    assert _event(item, {"at": None, "window_end": None}, {}) is None
    now = dt_util.utcnow().isoformat()
    assert _event(item, {"at": now, "window_end": now}, {}) is None
