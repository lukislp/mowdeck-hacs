"""Notifications (inbox polling, realtime push, event entity, bus event, persistent
notifications, logbook) and the command event entity."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from homeassistant.components import persistent_notification
from homeassistant.core import Event, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.mowdeck import logbook
from custom_components.mowdeck.const import (
    CONF_EXPOSE_EMERGENCY_STOP,
    CONF_PERSISTENT_SEVERITY,
    CONF_POLL_INTERVAL,
    DOMAIN,
    EVENT_COMMAND_RESULT,
    EVENT_NOTIFICATION,
    INBOX_RECHECK,
)
from custom_components.mowdeck.diagnostics import async_get_config_entry_diagnostics

from .conftest import wait_for
from .fake_mowdeck import MOWER_ID, FakeMowDeck, notification

NOTIFICATION = "event.luba_notification"
COMMAND = "event.luba_command"


def state_of(hass: HomeAssistant, entity_id: str) -> Any:
    """The state object of an entity (asserting it exists)."""
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set the entry up and wait for the socket."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def teardown(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Unload the entry (closes the socket)."""
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Poll the inbox now."""
    await entry.runtime_data.notifications.async_refresh()
    await hass.async_block_till_done()


async def test_api_token_without_inbox(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    loaded_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """MowDeck refuses API tokens an inbox: no notification entity, no Repairs issue, the
    inbox is asked again only rarely."""
    notifications = loaded_entry.runtime_data.notifications
    assert notifications.data.inbox is False
    assert notifications.update_interval == INBOX_RECHECK
    assert hass.states.get(NOTIFICATION) is None
    assert notifications.last_update_success
    assert not [i for i in issue_registry.issues.values() if i.domain == DOMAIN]


async def test_inbox_notifications(
    hass: HomeAssistant, fake_api: FakeMowDeck, config_entry: MockConfigEntry
) -> None:
    """With an inbox: the backlog stays quiet, new notifications fire once on the event
    entity and the bus; others only on the bus."""
    fake_api.inbox_allowed = True
    fake_api.inbox = [notification(kind="battery_low", severity="warning")]
    events: list[Event] = async_capture_events(hass, EVENT_NOTIFICATION)
    await setup(hass, config_entry)
    assert state_of(hass, NOTIFICATION).state == "unknown"
    assert events == []

    stuck = notification()
    fake_api.inbox.insert(0, stuck)
    fake_api.inbox.insert(0, notification(kind="brand_new_kind", severity="info"))
    fake_api.inbox.insert(0, notification(kind="test", mower_id=None, severity="info"))
    await poll(hass, config_entry)
    await poll(hass, config_entry)
    assert [e.data["kind"] for e in events] == ["mower_stuck", "brand_new_kind", "test"]
    assert events[0].data["mower_name"] == "Luba"
    assert events[0].data["notification_id"] == stuck["id"]
    entity = state_of(hass, NOTIFICATION)
    assert entity.attributes["event_type"] == "mower_stuck"
    assert entity.attributes["severity"] == "critical"
    assert entity.attributes["title"] == "Luba is stuck"
    # Off by default: no persistent notification.
    assert persistent_notification._async_get_or_create_notifications(hass) == {}
    await teardown(hass, config_entry)


async def test_restart_does_not_replay(
    hass: HomeAssistant, fake_api: FakeMowDeck, config_entry: MockConfigEntry
) -> None:
    """Delivered notifications are remembered across a reload."""
    fake_api.inbox_allowed = True
    events: list[Event] = async_capture_events(hass, EVENT_NOTIFICATION)
    await setup(hass, config_entry)
    fake_api.inbox.append(notification())
    await poll(hass, config_entry)
    assert len(events) == 1
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    await poll(hass, config_entry)
    assert len(events) == 1
    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    assert diagnostics["notifications"] == {"inbox": True, "realtime": False, "delivered": 1}
    await teardown(hass, config_entry)


async def test_persistent_notifications_by_severity(
    hass: HomeAssistant, fake_api: FakeMowDeck, config_entry: MockConfigEntry
) -> None:
    """With the option at critical only critical notifications are mirrored."""
    fake_api.inbox_allowed = True
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_PERSISTENT_SEVERITY: "critical"}
    )
    await setup(hass, config_entry)
    stuck = notification()
    fake_api.inbox += [stuck, notification(kind="battery_low", severity="warning")]
    await poll(hass, config_entry)
    shown = persistent_notification._async_get_or_create_notifications(hass)
    assert list(shown) == [f"{DOMAIN}_{stuck['id']}"]
    assert shown[f"{DOMAIN}_{stuck['id']}"]["title"] == "Luba is stuck"
    await teardown(hass, config_entry)


async def test_realtime_notification(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """A notification message on the socket creates the entity and fires it, once."""
    events: list[Event] = async_capture_events(hass, EVENT_NOTIFICATION)
    item = notification(kind="mowing_finished", severity="info")
    await fake_api.push({"type": "notification", "notification": item})
    await wait_for(hass, lambda: hass.states.get(NOTIFICATION) is not None)
    await fake_api.push({"type": "notification", "notification": item})
    await fake_api.push({"type": "notification", "notification": "garbage"})
    second = notification(kind="mowing_finished", severity="info")
    await fake_api.push({"type": "notification", "notification": second})
    await wait_for(hass, lambda: len(events) == 2)
    assert state_of(hass, NOTIFICATION).attributes["notification_id"] == second["id"]


async def test_command_event(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """Command results fire the command event entity next to the bus event."""
    events: list[Event] = async_capture_events(hass, EVENT_COMMAND_RESULT)
    assert state_of(hass, COMMAND).state == "unknown"
    await fake_api.push(
        {
            "type": "command_result",
            "mower_id": MOWER_ID,
            "result": {"command_id": "c1", "status": "rejected", "reason": "mower busy"},
        }
    )
    await wait_for(hass, lambda: state_of(hass, COMMAND).state != "unknown")
    command = state_of(hass, COMMAND)
    assert command.attributes["event_type"] == "rejected"
    assert command.attributes["command_id"] == "c1"
    assert command.attributes["reason"] == "mower busy"
    await fake_api.push(
        {"type": "command_result", "mower_id": MOWER_ID, "result": {"status": "weird"}}
    )
    await wait_for(hass, lambda: len(events) == 2)
    assert state_of(hass, COMMAND).attributes["event_type"] == "rejected"


async def test_options_offer_persistent_notifications(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The options flow stores the severity."""
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_POLL_INTERVAL: 60,
            CONF_EXPOSE_EMERGENCY_STOP: True,
            CONF_PERSISTENT_SEVERITY: "warning",
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options[CONF_PERSISTENT_SEVERITY] == "warning"


@pytest.mark.usefixtures("loaded_entry")
async def test_logbook_descriptions(hass: HomeAssistant) -> None:
    """Both bus events read well in the logbook."""
    described: dict[str, Callable[[Event], dict[str, str]]] = {}

    def register(domain: str, event: str, describe: Callable[[Event], dict[str, str]]) -> None:
        assert domain == DOMAIN
        described[event] = describe

    logbook.async_describe_events(hass, register)
    note = described[EVENT_NOTIFICATION](
        Event(EVENT_NOTIFICATION, {"mower_name": "Luba", "title": "Stuck", "body": "Help"})
    )
    assert note == {"name": "Luba", "message": "notified: Stuck (Help)"}
    command = described[EVENT_COMMAND_RESULT](
        Event(
            EVENT_COMMAND_RESULT,
            {"mower_id": MOWER_ID, "command_id": "c1", "status": "failed", "reason": "offline"},
        )
    )
    assert command == {"name": "Luba", "message": "command c1 is failed: offline"}
    unknown = described[EVENT_COMMAND_RESULT](
        Event(EVENT_COMMAND_RESULT, {"mower_id": "other", "status": "executed"})
    )
    assert unknown["name"] == "MowDeck"
    assert (
        described[EVENT_NOTIFICATION](Event(EVENT_NOTIFICATION, {"kind": "test"}))["message"]
        == "notified: test"
    )
