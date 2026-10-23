"""Push over the Realtime WebSocket: states, events, reconnects, token refresh, repairs."""

from __future__ import annotations

from aiohttp import WSCloseCode
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.mowdeck.const import DOMAIN, EVENT_COMMAND_RESULT

from .conftest import wait_for
from .fake_mowdeck import MOWER_ID, FakeMowDeck, mower_state


def battery(hass: HomeAssistant) -> str | None:
    """State of the battery sensor."""
    state = hass.states.get("sensor.luba_battery")
    return state.state if state else None


async def test_push_updates_sensor_without_polling(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """A pushed state reaches the entities at once; no HTTP request is made."""
    assert fake_api.ws_subscriptions == [[MOWER_ID]]
    assert loaded_entry.runtime_data.realtime.connected
    requests_before = list(fake_api.requests)
    await fake_api.push_state(
        MOWER_ID, mower_state(activity="mowing", battery_percent=64, charging=False)
    )
    await wait_for(hass, lambda: battery(hass) == "64")
    lawn = hass.states.get("lawn_mower.luba")
    assert lawn is not None
    assert lawn.state == "mowing"
    # Only the map image reads the running job's planned path.
    assert [r for r in fake_api.requests if not r.endswith("/path")] == requests_before


async def test_start_mowing_turns_mowing_by_push(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """After start_mowing the pushed state turns the mower to mowing."""
    await hass.services.async_call(
        "lawn_mower", "start_mowing", {"entity_id": "lawn_mower.luba"}, blocking=True
    )
    await wait_for(hass, lambda: hass.states.get("lawn_mower.luba").state == "mowing")  # type: ignore[union-attr]


async def test_poll_interval_follows_connection(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """Polling slows down while push works and speeds up when the socket is lost."""
    coordinator = loaded_entry.runtime_data.coordinator
    assert coordinator.update_interval is not None
    assert coordinator.update_interval.total_seconds() == 300
    fake_api.ws_reject_auth = True
    await fake_api.close_sockets(WSCloseCode.GOING_AWAY)
    await wait_for(hass, lambda: not loaded_entry.runtime_data.realtime.connected)
    assert coordinator.update_interval.total_seconds() == 60


async def test_reconnects_after_close(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """Close code 1001 (service restart) leads to a new socket and subscription."""
    await fake_api.close_sockets(WSCloseCode.GOING_AWAY)
    await wait_for(hass, lambda: len(fake_api.ws_subscriptions) == 2)
    assert loaded_entry.runtime_data.realtime.last_close_code == 1001
    await fake_api.push_state(MOWER_ID, mower_state(battery_percent=55))
    await wait_for(hass, lambda: battery(hass) == "55")


async def test_command_result_event(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """command_result messages become mowdeck_command_result events."""
    events: list[Event] = async_capture_events(hass, EVENT_COMMAND_RESULT)
    await fake_api.push(
        {
            "type": "command_result",
            "mower_id": MOWER_ID,
            "result": {"command_id": "c1", "status": "executed", "reason": "ok"},
        }
    )
    await wait_for(hass, lambda: len(events) == 1)
    assert events[0].data == {
        "mower_id": MOWER_ID,
        "command_id": "c1",
        "status": "executed",
        "reason": "ok",
    }


async def test_revoked_mower_becomes_unavailable(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """A revoked message makes the mower's entities unavailable."""
    await fake_api.push({"type": "revoked", "mower_ids": [MOWER_ID], "reason": "access_revoked"})
    await wait_for(hass, lambda: battery(hass) == "unavailable")


async def test_token_refresh_before_expiry(
    hass: HomeAssistant, fake_api: FakeMowDeck, config_entry: MockConfigEntry
) -> None:
    """Shortly before expiry a new access token is exchanged and sent with auth."""
    fake_api.expires_in = 61  # the refresh margin is 60 s: refresh after about 1 s
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await wait_for(hass, lambda: len(fake_api.ws_auth_tokens) >= 2, rounds=500)
    assert fake_api.ws_auth_tokens[0] != fake_api.ws_auth_tokens[1]
    assert fake_api.exchanges >= 2
    await hass.config_entries.async_unload(config_entry.entry_id)


async def test_repeated_4401_raises_repairs_issue(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    loaded_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A socket refused even with fresh tokens raises a Repairs issue; it clears on success."""
    realtime = loaded_entry.runtime_data.realtime
    fake_api.ws_reject_auth = True
    await fake_api.close_sockets(4401)
    await wait_for(
        hass, lambda: issue_registry.async_get_issue(DOMAIN, realtime.issue_id) is not None
    )
    # The entry stays loaded and keeps polling.
    assert loaded_entry.state is ConfigEntryState.LOADED
    assert not realtime.connected


async def test_revoked_api_token_starts_reauth(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    loaded_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """When the API token is revoked the socket stops and a reauth flow opens."""
    realtime = loaded_entry.runtime_data.realtime
    fake_api.revoke()
    await fake_api.close_sockets(4401)
    await wait_for(hass, lambda: bool(hass.config_entries.flow.async_progress_by_handler(DOMAIN)))
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert flows[0]["context"]["source"] == "reauth"
    assert issue_registry.async_get_issue(DOMAIN, realtime.issue_id) is not None


async def test_new_mower_found_by_poll(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """A mower added later gets entities and a subscription after the next poll."""
    second = dict(fake_api.mowers[0], id="1b2c3d4e-5f60-4718-8a9b-0c1d2e3f4a5b", name="Yuka")
    fake_api.mowers.append(second)
    fake_api.states[second["id"]] = mower_state(mower_id=second["id"], battery_percent=33)
    await loaded_entry.runtime_data.coordinator.async_refresh()
    await wait_for(hass, lambda: hass.states.get("sensor.yuka_battery") is not None)
    assert hass.states.get("sensor.yuka_battery").state == "33"  # type: ignore[union-attr]
    await wait_for(hass, lambda: [second["id"]] in fake_api.ws_subscriptions)
