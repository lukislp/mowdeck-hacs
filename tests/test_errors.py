"""Error paths: config flow answers, API client failures and unusual realtime traffic."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import patch

import aiohttp
import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mowdeck.api import MowDeckClient, MowDeckConnectionError
from custom_components.mowdeck.const import CONF_API_TOKEN, DOMAIN

from .conftest import wait_for
from .fake_mowdeck import API_TOKEN, MOWER_ID, FakeMowDeck, mower_state


@pytest.fixture
async def session(fake_api: FakeMowDeck) -> AsyncIterator[aiohttp.ClientSession]:
    """A plain aiohttp session."""
    async with aiohttp.ClientSession() as client_session:
        yield client_session


def client_for(fake_api: FakeMowDeck, session: aiohttp.ClientSession) -> MowDeckClient:
    """A client with fast retries."""
    return MowDeckClient(session, fake_api.base_url, API_TOKEN, backoff=0.001, retries=1)


async def configure(hass: HomeAssistant, fake_api: FakeMowDeck) -> dict[str, object]:
    """Run the user step with the fake's URL and token."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    return dict(
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_URL: fake_api.base_url, CONF_API_TOKEN: API_TOKEN}
        )
    )


# Config flow


async def test_flow_token_without_account(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """A token of no account (an operator) cannot be used."""
    fake_api.me_overrides = {"account_id": None}
    assert (await configure(hass, fake_api))["errors"] == {"base": "no_account"}


async def test_flow_token_without_view(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """A 403 on the mower list names the missing permission."""
    fake_api.forbidden_paths.add("/api/v1/fleet/mowers")
    assert (await configure(hass, fake_api))["errors"] == {"base": "missing_permission"}


async def test_flow_unexpected_answer(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """Any other problem document is an unknown error."""
    fake_api.problem_paths["/api/v1/identity/me"] = 400
    assert (await configure(hass, fake_api))["errors"] == {"base": "unknown"}


async def test_flow_title_falls_back(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """Without a display name the entry is named after the username, else MowDeck."""
    fake_api.me_overrides = {"display_name": None, "username": None}
    result = await configure(hass, fake_api)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "MowDeck"


# API client


async def test_exchange_without_access_token(
    fake_api: FakeMowDeck, session: aiohttp.ClientSession
) -> None:
    """A token answer without an access token is a connection error."""
    fake_api.token_overrides = {"access_token": None}
    with pytest.raises(MowDeckConnectionError, match="no access token"):
        await client_for(fake_api, session).async_get_access_token()


async def test_exchange_without_expiry(
    fake_api: FakeMowDeck, session: aiohttp.ClientSession
) -> None:
    """Without expires_in the access token counts as valid for ten minutes."""
    fake_api.token_overrides = {"expires_in": None}
    client = client_for(fake_api, session)
    await client.async_get_access_token()
    assert client.token_expires_in == pytest.approx(600, abs=5)


async def test_permission_answer_must_match(
    fake_api: FakeMowDeck, session: aiohttp.ClientSession
) -> None:
    """An answer of the wrong length is refused."""
    fake_api.permission_answer = [True]
    client = client_for(fake_api, session)
    with pytest.raises(MowDeckConnectionError, match="unexpected answer"):
        await client.check_permissions([("can_view", f"mower:{MOWER_ID}")] * 2)


async def test_dropped_command_is_not_resent(
    fake_api: FakeMowDeck, session: aiohttp.ClientSession
) -> None:
    """A command whose connection broke may have reached Fleet: no retry."""
    fake_api.disconnect_paths.add("/api/v1/fleet/mowers/")
    client = client_for(fake_api, session)
    with pytest.raises(MowDeckConnectionError, match="failed"):
        await client.send_command(MOWER_ID, {"kind": "pause_mow"})
    assert fake_api.requests.count(f"POST /api/v1/fleet/mowers/{MOWER_ID}/commands") == 1
    # A read is retried before it fails.
    fake_api.disconnect_paths.add("/api/v1/fleet/mowers")
    with pytest.raises(MowDeckConnectionError):
        await client.list_mowers()
    assert fake_api.requests.count("GET /api/v1/fleet/mowers") >= 2


async def test_media_urls(fake_api: FakeMowDeck, session: aiohttp.ClientSession) -> None:
    """Relative WHEP paths are on the MowDeck origin; other schemes are refused."""
    client = client_for(fake_api, session)
    assert client.media_url("/media/whep/x") == fake_api.base_url + "/media/whep/x"
    assert client.media_url("https://relay.example/whep") == "https://relay.example/whep"
    with pytest.raises(ValueError, match="not a WHEP URL"):
        client.media_url("//relay.example/whep")


async def test_whep_connection_error(session: aiohttp.ClientSession) -> None:
    """An unreachable media server is a connection error naming the method."""
    client = MowDeckClient(session, "http://127.0.0.1:9", API_TOKEN, request_timeout=2)
    with pytest.raises(MowDeckConnectionError, match="WHEP POST failed"):
        await client.whep_request("POST", "http://127.0.0.1:9/whep", "ticket", body="v=0")


# Services and realtime


async def test_health_server_error_keeps_the_rest(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """A failing health service only makes its sensors unavailable (no Repairs issue)."""
    health = loaded_entry.runtime_data.health
    fake_api.problem_paths[f"/api/v1/telemetry/mowers/{MOWER_ID}/health"] = 500
    await health.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get("sensor.luba_mowing_hours")
    assert state is not None
    assert state.state == "unavailable"
    assert not health.forbidden


async def test_realtime_tolerates_noise(
    hass: HomeAssistant, fake_api: FakeMowDeck, config_entry: MockConfigEntry
) -> None:
    """Errors before ready, broken frames, binary frames, denials and error messages do not
    break the socket."""
    fake_api.ws_error_before_ready = True
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await wait_for(hass, lambda: bool(fake_api.ws_subscriptions))
    await fake_api.push_raw("not json")
    await fake_api.push_raw("[1, 2]")
    await fake_api.push_raw(b"\x00")
    await fake_api.push({"type": "subscribed", "mower_ids": [], "denied": ["x"]})
    await fake_api.push({"type": "error", "code": "internal", "message": "oops"})
    await fake_api.push({"type": "state", "mower_id": 42, "state": "nonsense"})
    await fake_api.push_state(MOWER_ID, mower_state(battery_percent=12))
    await wait_for(
        hass, lambda: getattr(hass.states.get("sensor.luba_battery"), "state", "") == "12"
    )
    assert config_entry.runtime_data.realtime.connected
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_realtime_connection_limit(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """Close code 4429 (connection limit) waits longer, then reconnects."""
    with patch("custom_components.mowdeck.realtime.LIMIT_BACKOFF", 0.01):
        await fake_api.close_sockets(4429)
        await wait_for(hass, lambda: len(fake_api.ws_subscriptions) == 2)
    assert loaded_entry.runtime_data.realtime.last_close_code == 4429


async def test_realtime_refresh_failure_after_4401(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """A 4401 whose token refresh fails for a server error retries with backoff."""
    realtime = loaded_entry.runtime_data.realtime
    fake_api.fail_requests = 4
    await fake_api.close_sockets(4401)
    await wait_for(hass, lambda: len(fake_api.ws_subscriptions) == 2, rounds=600)
    assert realtime.connected
