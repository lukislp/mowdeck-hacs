"""The API client: token exchange and refresh, retries, problem+json errors."""

from __future__ import annotations

from collections.abc import AsyncIterator

import aiohttp
import pytest

from custom_components.mowdeck.api import (
    MowDeckAuthError,
    MowDeckClient,
    MowDeckConnectionError,
    MowDeckForbiddenError,
    MowDeckNotFoundError,
    Problem,
    normalize_base_url,
    websocket_url,
)

from .fake_mowdeck import API_TOKEN, MOWER_ID, FakeMowDeck


class Clock:
    """A settable monotonic clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
async def session(fake_api: FakeMowDeck) -> AsyncIterator[aiohttp.ClientSession]:
    """A plain aiohttp session."""
    async with aiohttp.ClientSession() as client_session:
        yield client_session


def make_client(
    fake_api: FakeMowDeck, session: aiohttp.ClientSession, **kwargs: object
) -> MowDeckClient:
    return MowDeckClient(session, fake_api.base_url, API_TOKEN, backoff=0.001, **kwargs)  # type: ignore[arg-type]


async def test_token_refreshed_before_expiry(
    fake_api: FakeMowDeck, session: aiohttp.ClientSession
) -> None:
    """The access token is reused until 60 s before expiry, then exchanged again."""
    clock = Clock()
    client = make_client(fake_api, session, clock=clock)
    first = await client.async_get_access_token()
    assert client.account_id is not None
    assert await client.async_get_access_token() == first
    assert client.token_expires_in == 600
    clock.now += 539
    assert await client.async_get_access_token() == first
    clock.now += 2  # 59 s left: inside the refresh margin
    second = await client.async_get_access_token()
    assert second != first
    assert fake_api.exchanges == 2


async def test_401_refreshes_once(fake_api: FakeMowDeck, session: aiohttp.ClientSession) -> None:
    """An access token rejected early is replaced and the request repeated."""
    client = make_client(fake_api, session)
    await client.list_mowers()
    fake_api.expire_access_tokens()
    mowers = await client.list_mowers()
    assert [m["id"] for m in mowers] == [MOWER_ID]
    assert fake_api.exchanges == 2


async def test_revoked_token_raises_auth_error(
    fake_api: FakeMowDeck, session: aiohttp.ClientSession
) -> None:
    """A revoked API token fails the exchange with MowDeckAuthError."""
    client = make_client(fake_api, session)
    await client.get_me()
    fake_api.revoke()
    with pytest.raises(MowDeckAuthError) as err:
        await client.get_me()
    assert err.value.status == 401
    assert err.value.problem.correlation_id == "test-correlation"


async def test_retries_idempotent_requests(
    fake_api: FakeMowDeck, session: aiohttp.ClientSession
) -> None:
    """503 answers are retried with backoff for GETs and the exchange."""
    client = make_client(fake_api, session)
    fake_api.fail_requests = 2
    assert await client.get_states()
    fake_api.fail_requests = 10
    with pytest.raises(MowDeckConnectionError, match="503"):
        await client.get_states()


async def test_commands_not_retried_on_server_error(
    fake_api: FakeMowDeck, session: aiohttp.ClientSession
) -> None:
    """A command answered with 503 is not sent again (it may have been executed)."""
    client = make_client(fake_api, session)
    await client.async_get_access_token()
    fake_api.fail_requests = 1
    with pytest.raises(MowDeckConnectionError):
        await client.send_command(MOWER_ID, {"kind": "pause_mow"})
    assert fake_api.requests.count(f"POST /api/v1/fleet/mowers/{MOWER_ID}/commands") == 1


async def test_problem_mapping(fake_api: FakeMowDeck, session: aiohttp.ClientSession) -> None:
    """403 and 404 problems map to their exceptions; missing telemetry is None."""
    client = make_client(fake_api, session)
    fake_api.forbid_commands = True
    with pytest.raises(MowDeckForbiddenError):
        await client.send_command(MOWER_ID, {"kind": "pause_mow"})
    with pytest.raises(MowDeckNotFoundError):
        await client.send_command("unknown", {"kind": "pause_mow"})
    assert await client.get_state("unknown") is None
    assert await client.get_capabilities("unknown") is None
    assert await client.get_map("unknown") is None


async def test_read_endpoints(fake_api: FakeMowDeck, session: aiohttp.ClientSession) -> None:
    """State, map with ETag, command record and a command with TTL."""
    client = make_client(fake_api, session)
    state = await client.get_state(MOWER_ID)
    assert state is not None
    assert state["state"]["battery_percent"] == 87
    mower_map = await client.get_map(MOWER_ID)
    assert mower_map is not None
    assert mower_map["revision"] == "rev-1"
    assert await client.get_map(MOWER_ID, revision="rev-1") is None  # 304
    accepted = await client.send_command(MOWER_ID, {"kind": "pause_mow"}, ttl_seconds=30)
    assert fake_api.commands[-1]["ttl_seconds"] == 30
    record = await client.get_command(accepted["command_id"])
    assert record["status"] == "executed"


async def test_connection_refused(fake_api: FakeMowDeck, session: aiohttp.ClientSession) -> None:
    """A closed port is a MowDeckConnectionError after the retries."""
    client = make_client(fake_api, session, retries=1)
    assert fake_api.server is not None
    await fake_api.server.close()
    with pytest.raises(MowDeckConnectionError, match="cannot connect"):
        await client.get_me()


def test_urls() -> None:
    """Base URL normalisation and the WebSocket URL."""
    assert normalize_base_url(" https://mow.example.ts.net/ ") == "https://mow.example.ts.net"
    assert normalize_base_url("http://host:8080/sub/?x=1") == "http://host:8080/sub"
    with pytest.raises(ValueError, match="not an http"):
        normalize_base_url("mowdeck.local")
    assert websocket_url("https://h") == "wss://h/api/v1/realtime/ws"
    assert websocket_url("http://h:81/x") == "ws://h:81/x/api/v1/realtime/ws"


def test_problem_parsing() -> None:
    """Problem documents and non-JSON bodies."""
    assert str(Problem.from_body(500, "oops")) == "HTTP 500: error"
    problem = Problem.from_body(404, {"title": "Not found", "detail": 3, "correlation_id": "c"})
    assert str(problem) == "HTTP 404: Not found (correlation id c)"
