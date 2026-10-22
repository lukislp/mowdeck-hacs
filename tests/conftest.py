"""Fixtures: the Home Assistant test harness, a fake MowDeck API and a loaded entry.

This conftest.py lives in this standalone uv project only; the monorepo's root pytest run
never collects integrations/ (its testpaths are tools, libs and services).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from unittest.mock import PropertyMock, patch

import pytest
from aiohttp.test_utils import TestServer
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mowdeck.const import CONF_API_TOKEN, DOMAIN

from .fake_mowdeck import ACCOUNT_ID, API_TOKEN, FakeMowDeck

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/mowdeck in every test."""


@pytest.fixture(autouse=True)
def fast_retries() -> Iterator[None]:
    """No real sleeping between API retries and WebSocket reconnects."""
    with patch("custom_components.mowdeck.realtime.MIN_BACKOFF", 0.01):
        yield


@pytest.fixture
def entity_registry_enabled_by_default() -> Iterator[None]:
    """Create the entities that are disabled by default enabled (as Home Assistant's own
    test suite does)."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        new_callable=PropertyMock,
        return_value=True,
    ):
        yield


@pytest.fixture
async def fake_api(socket_enabled: None) -> AsyncIterator[FakeMowDeck]:
    """A running fake MowDeck API on 127.0.0.1."""
    fake = FakeMowDeck()
    server = TestServer(fake.app(), host="127.0.0.1")
    await server.start_server()
    fake.server = server
    yield fake
    await server.close()


@pytest.fixture
def config_entry(fake_api: FakeMowDeck) -> MockConfigEntry:
    """A config entry pointing at the fake API."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Home Assistant",
        unique_id=ACCOUNT_ID,
        data={CONF_URL: fake_api.base_url, CONF_API_TOKEN: API_TOKEN},
    )


@pytest.fixture
async def loaded_entry(
    hass: HomeAssistant, config_entry: MockConfigEntry, fake_api: FakeMowDeck
) -> AsyncIterator[MockConfigEntry]:
    """The entry set up, with the WebSocket connected and subscribed."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await wait_for(hass, lambda: bool(fake_api.ws_subscriptions))
    yield config_entry
    if config_entry.state is ConfigEntryState.LOADED:
        await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()


async def wait_for(hass: HomeAssistant, condition: Callable[[], bool], rounds: int = 300) -> None:
    """Let the event loop run until ``condition()`` holds (real sockets need real time)."""
    for _ in range(rounds):
        if condition():
            return
        await asyncio.sleep(0.01)
        await hass.async_block_till_done()
    raise AssertionError("condition not met")
