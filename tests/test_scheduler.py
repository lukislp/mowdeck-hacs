"""Schedules and rain: switches, run now and skip next, next mowing, last run, rain hold."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mowdeck.const import DOMAIN
from custom_components.mowdeck.diagnostics import async_get_config_entry_diagnostics

from .fake_mowdeck import (
    MOWER_ID,
    SCHEDULE_ID,
    FakeMowDeck,
    in_hours,
    rain_decision,
    schedule,
    schedule_run,
)

SWITCH = "switch.luba_schedule_morning"
RUN_NOW = "button.luba_run_morning_now"
SKIP_NEXT = "button.luba_skip_next_run_of_morning"
NEXT_MOW = "sensor.luba_next_mowing"
LAST_RUN = "sensor.luba_last_schedule_run"
RAIN_HOLD = "binary_sensor.luba_rain_hold"
PAUSE_UNTIL = "sensor.luba_rain_pause_until"
WET_UNTIL = "sensor.luba_lawn_wet_until"
GROUP_ID = "7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c0d"


def state_of(hass: HomeAssistant, entity_id: str) -> Any:
    """The state object of an entity (asserting it exists)."""
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


def timestamp(value: str) -> Any:
    """An API timestamp as Home Assistant shows it in a timestamp sensor."""
    parsed = dt_util.parse_datetime(value)
    assert parsed is not None
    return parsed.replace(microsecond=0)


async def call(hass: HomeAssistant, domain: str, service: str, entity_id: str) -> None:
    """Call a service on one entity and wait."""
    await hass.services.async_call(domain, service, {ATTR_ENTITY_ID: entity_id}, blocking=True)
    await hass.async_block_till_done()


async def refresh(entry: MockConfigEntry) -> None:
    """Poll the Scheduler now."""
    await entry.runtime_data.scheduler.async_refresh()


@pytest.mark.usefixtures("loaded_entry")
async def test_schedule_entities(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """Switch, buttons, next mowing, last run and the rain decision of the mower."""
    switch = state_of(hass, SWITCH)
    assert switch.state == "on"
    assert switch.attributes["days_of_week"] == ["mon", "wed", "fri"]
    assert switch.attributes["friendly_name"] == "Luba Schedule Morning"
    assert state_of(hass, RUN_NOW).state == "unknown"
    assert state_of(hass, SKIP_NEXT).state == "unknown"

    next_mow = state_of(hass, NEXT_MOW)
    expected = timestamp(fake_api.schedules[SCHEDULE_ID]["next_run_at"])  # type: ignore[index]
    assert dt_util.parse_datetime(next_mow.state) == expected
    assert next_mow.attributes["schedule"] == "Morning"

    last = state_of(hass, LAST_RUN)
    assert last.state == "skipped_rain"
    assert last.attributes["details"] == "rain expected"
    assert last.attributes["schedule"] == "Morning"

    hold = state_of(hass, RAIN_HOLD)
    assert hold.state == "on"
    assert hold.attributes["verdict"] == "hold"
    assert hold.attributes["recent_mm_24h"] == 4.5
    assert state_of(hass, PAUSE_UNTIL).state != "unknown"
    assert state_of(hass, WET_UNTIL).state != "unknown"
    # The rain decision uses the rain settings of the mower's next schedule.
    assert fake_api.weather_queries[0] == {
        "probability_threshold": "70",
        "precipitation_mm_threshold": "0.5",
        "lookahead_hours": "3",
        "cooldown_hours": "2",
    }


@pytest.mark.usefixtures("loaded_entry")
async def test_toggle_run_and_skip(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """The switch patches enabled, run now starts a manual run, skip next skips the next
    firing; each refreshes the schedules at once."""
    await call(hass, "switch", SERVICE_TURN_OFF, SWITCH)
    assert fake_api.schedule_patches == [{"id": SCHEDULE_ID, "enabled": False}]
    assert state_of(hass, SWITCH).state == "off"
    assert state_of(hass, NEXT_MOW).state == "unknown"

    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "button", SERVICE_PRESS, SKIP_NEXT)
    assert err.value.translation_key == "action_failed"

    await call(hass, "switch", SERVICE_TURN_ON, SWITCH)
    assert state_of(hass, SWITCH).state == "on"

    await call(hass, "button", SERVICE_PRESS, SKIP_NEXT)
    # The next firing is skipped: the one after it is the next mowing.
    skipped = timestamp(fake_api.schedules[SCHEDULE_ID]["skip_until"])  # type: ignore[index]
    next_mow = dt_util.parse_datetime(state_of(hass, NEXT_MOW).state)
    assert next_mow is not None
    assert next_mow > skipped

    await call(hass, "button", SERVICE_PRESS, RUN_NOW)
    last = state_of(hass, LAST_RUN)
    assert last.state == "started"
    assert last.attributes["trigger"] == "manual"


async def test_permissions_decide_the_entities(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Without schedule.edit there is no switch and no skip button; without mower.control no
    run button either. The sensors stay. A permission lost later leaves the button registered
    and unavailable."""
    fake_api.relations = {"can_view"}
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    for entity_id in (SWITCH, RUN_NOW, SKIP_NEXT):
        assert hass.states.get(entity_id) is None, entity_id
    assert state_of(hass, NEXT_MOW).state != "unknown"

    fake_api.relations = {"can_view", "can_control"}
    await refresh(config_entry)
    await hass.async_block_till_done()
    assert hass.states.get(RUN_NOW) is not None
    assert hass.states.get(SWITCH) is None

    fake_api.relations = {"can_view"}
    await refresh(config_entry)
    await hass.async_block_till_done()
    assert entity_registry.async_get(RUN_NOW) is not None
    assert state_of(hass, RUN_NOW).state == STATE_UNAVAILABLE
    assert hass.states.get(SWITCH) is None
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("loaded_entry")
async def test_refused_toggle_names_the_permission(
    hass: HomeAssistant, fake_api: FakeMowDeck
) -> None:
    """A 403 from the Scheduler names schedule.edit."""
    fake_api.relations = {"can_view", "can_control"}
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "switch", SERVICE_TURN_OFF, SWITCH)
    assert err.value.translation_key == "action_forbidden"
    assert err.value.translation_placeholders == {
        "command": "schedule_disable",
        "permission": "schedule.edit",
    }


async def test_group_schedule_on_every_mower_of_the_group(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """A group schedule shows on the group's mowers; its runs count per mower."""
    fake_api.mowers[0]["group_ids"] = [GROUP_ID]
    group_id = "0b1c2d3e-4f5a-4b6c-8d7e-9f0a1b2c3d4e"
    fake_api.schedules[group_id] = schedule(  # type: ignore[index]
        id=group_id,
        name="Evening",
        target_type="group",
        target_id=GROUP_ID,
        next_run_at=in_hours(5),
    )
    fake_api.schedule_runs[group_id] = [
        schedule_run(outcome="skipped_mower_busy", hours_ago=1, mower_id="other-mower"),
        schedule_run(outcome="failed", hours_ago=2),
    ]
    await loaded_entry.runtime_data.coordinator.async_refresh()
    await refresh(loaded_entry)
    await hass.async_block_till_done()
    assert state_of(hass, "switch.luba_schedule_evening").state == "on"
    assert state_of(hass, NEXT_MOW).attributes["schedule"] == "Evening"
    assert state_of(hass, LAST_RUN).state == "failed"


async def test_deleted_schedule_removes_its_entities(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    loaded_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A schedule deleted in MowDeck takes its switch and buttons along."""
    fake_api.schedules.clear()  # type: ignore[union-attr]
    await refresh(loaded_entry)
    await hass.async_block_till_done()
    for entity_id in (SWITCH, RUN_NOW, SKIP_NEXT):
        assert entity_registry.async_get(entity_id) is None, entity_id
    assert state_of(hass, NEXT_MOW).state == "unknown"
    assert state_of(hass, LAST_RUN).state == "unknown"


async def test_schedule_entities_outlive_a_lost_permission(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    loaded_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Without the relations on the mower the schedule's switch and buttons stay registered
    and unavailable (the schedule still exists); with them back they work again."""
    fake_api.relations = {"can_view"}
    await refresh(loaded_entry)
    await hass.async_block_till_done()
    for entity_id in (SWITCH, RUN_NOW, SKIP_NEXT):
        assert entity_registry.async_get(entity_id) is not None, entity_id
        assert state_of(hass, entity_id).state == STATE_UNAVAILABLE, entity_id

    fake_api.relations = {
        "can_view",
        "can_control",
        "can_drive",
        "can_manage_mowers",
        "can_edit_schedule",
    }
    await refresh(loaded_entry)
    await hass.async_block_till_done()
    for entity_id in (SWITCH, RUN_NOW, SKIP_NEXT):
        assert state_of(hass, entity_id).state != STATE_UNAVAILABLE, entity_id


async def test_dry_lawn(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """A decision that lets the mower run turns the rain hold off and clears the times."""
    fake_api.weather[MOWER_ID] = rain_decision(
        verdict="run", holds=False, pause_until=None, wet=False, wet_until=None
    )
    await refresh(loaded_entry)
    await hass.async_block_till_done()
    assert state_of(hass, RAIN_HOLD).state == "off"
    assert state_of(hass, PAUSE_UNTIL).state == "unknown"
    assert state_of(hass, WET_UNTIL).state == "unknown"

    del fake_api.weather[MOWER_ID]
    await refresh(loaded_entry)
    await hass.async_block_till_done()
    assert state_of(hass, RAIN_HOLD).state == "unknown"


async def test_without_scheduler(
    hass: HomeAssistant, fake_api: FakeMowDeck, config_entry: MockConfigEntry
) -> None:
    """A MowDeck without the Scheduler has no schedule or rain entities and no issue."""
    fake_api.schedules = None
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    for entity_id in (SWITCH, NEXT_MOW, RAIN_HOLD, LAST_RUN):
        assert hass.states.get(entity_id) is None, entity_id
    assert state_of(hass, "sensor.luba_battery").state == "87"
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_scheduler_forbidden(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    loaded_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A 403 on the schedules raises a Repairs issue and makes only their entities
    unavailable."""
    fake_api.forbidden_paths.add("/api/v1/scheduler/")
    await refresh(loaded_entry)
    await hass.async_block_till_done()
    assert state_of(hass, SWITCH).state == "unavailable"
    assert state_of(hass, RAIN_HOLD).state == "unavailable"
    assert state_of(hass, "sensor.luba_battery").state == "87"
    issue_id = f"scheduler_forbidden_{loaded_entry.entry_id}"
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders == {
        "url": fake_api.base_url,
        "permission": "scheduler.view",
    }
    fake_api.forbidden_paths.clear()
    await refresh(loaded_entry)
    await hass.async_block_till_done()
    assert state_of(hass, SWITCH).state == "on"
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_rain_decision_failure_leaves_the_rest(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """A failing rain decision only leaves the rain hold unknown."""
    fake_api.problem_paths["/api/v1/scheduler/weather/"] = 500
    await refresh(loaded_entry)
    await hass.async_block_till_done()
    assert state_of(hass, RAIN_HOLD).state == "unknown"
    assert state_of(hass, SWITCH).state == "on"


async def test_schedule_permission_check_failure(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """When the permission check fails the schedule entities stay (MowDeck decides)."""
    fake_api.problem_paths["/api/v1/identity/me/permissions"] = 500
    await refresh(loaded_entry)
    await hass.async_block_till_done()
    assert loaded_entry.runtime_data.scheduler.data.permissions == {}
    assert state_of(hass, SWITCH).state == "on"


async def test_diagnostics_contain_the_scheduler(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Schedules, runs and the rain decision without owner or coordinates."""
    diagnostics = await async_get_config_entry_diagnostics(hass, loaded_entry)
    scheduler = diagnostics["scheduler"]
    assert scheduler["available"] is True
    assert scheduler["schedules"][SCHEDULE_ID]["owner"] == "**REDACTED**"
    assert scheduler["weather"][MOWER_ID]["latitude"] == "**REDACTED**"
    assert scheduler["permissions"][f"mower:{MOWER_ID}"] == ["can_control", "can_edit_schedule"]
