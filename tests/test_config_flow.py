"""Config flow: user step, errors, reauth and options."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mowdeck.const import (
    CONF_API_TOKEN,
    CONF_EXPOSE_EMERGENCY_STOP,
    CONF_PERSISTENT_SEVERITY,
    CONF_POLL_INTERVAL,
    DOMAIN,
)

from .fake_mowdeck import ACCOUNT_ID, API_TOKEN, FakeMowDeck


async def _start(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return str(result["flow_id"])


async def test_user_flow_creates_entry(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """A valid URL and token create an entry keyed by the account id."""
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: fake_api.base_url + "/", CONF_API_TOKEN: API_TOKEN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home Assistant"
    assert result["data"] == {CONF_URL: fake_api.base_url, CONF_API_TOKEN: API_TOKEN}
    assert result["result"].unique_id == ACCOUNT_ID
    # Validation read /me and listed the mowers.
    assert "GET /api/v1/identity/me" in fake_api.requests
    assert "GET /api/v1/fleet/mowers" in fake_api.requests


async def test_user_flow_invalid_token(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """A wrong token shows invalid_auth; a correct one then succeeds."""
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: fake_api.base_url, CONF_API_TOKEN: "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: fake_api.base_url, CONF_API_TOKEN: API_TOKEN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_cannot_connect(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """An unreachable URL shows cannot_connect."""
    assert fake_api.server is not None
    url = fake_api.base_url
    await fake_api.server.close()
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: url, CONF_API_TOKEN: API_TOKEN}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_invalid_url(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """A URL without http(s) is refused before any request."""
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: "ftp://mowdeck", CONF_API_TOKEN: API_TOKEN}
    )
    assert result["errors"] == {CONF_URL: "invalid_url"}
    assert fake_api.requests == []


async def test_user_flow_server_error(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """Persistent 503 answers count as cannot_connect."""
    fake_api.fail_requests = 10
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: fake_api.base_url, CONF_API_TOKEN: API_TOKEN}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_already_configured(
    hass: HomeAssistant, fake_api: FakeMowDeck, config_entry: MockConfigEntry
) -> None:
    """The same account cannot be added twice."""
    config_entry.add_to_hass(hass)
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: fake_api.base_url, CONF_API_TOKEN: API_TOKEN}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_token(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """Reauth stores the new token of the same account and reloads the entry."""
    fake_api.valid_api_tokens["new-token"] = ACCOUNT_ID
    result = await loaded_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "new-token"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert loaded_entry.data[CONF_API_TOKEN] == "new-token"


async def test_reauth_wrong_account(
    hass: HomeAssistant, fake_api: FakeMowDeck, config_entry: MockConfigEntry
) -> None:
    """A token of another account is refused during reauth."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "other-token"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert config_entry.data[CONF_API_TOKEN] == API_TOKEN


async def test_options_flow(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """Options change the poll interval and hide the emergency stop button."""
    assert hass.states.get("button.luba_emergency_stop") is not None
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_POLL_INTERVAL: 120, CONF_EXPOSE_EMERGENCY_STOP: False}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options == {
        CONF_POLL_INTERVAL: 120,
        CONF_EXPOSE_EMERGENCY_STOP: False,
        CONF_PERSISTENT_SEVERITY: "off",
    }
    coordinator = loaded_entry.runtime_data.coordinator
    assert coordinator.poll_interval.total_seconds() == 120
    state = hass.states.get("button.luba_emergency_stop")
    assert state is None or state.state == "unavailable"
