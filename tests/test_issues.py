"""Repairs: the outdated Bluetooth bridge, the missing notification permission, and the link
to MowDeck's API token page on the token issues."""

from __future__ import annotations

from datetime import timedelta

from aiohttp import WSCloseCode
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.mowdeck.const import DOMAIN

from .conftest import wait_for
from .fake_mowdeck import MOWER_ID, FakeMowDeck, mower_state

BRIDGE = {
    "connected": True,
    "rssi": -70,
    "outdated": True,
    "outdated_reason": "project_version",
    "esphome_version": "2025.6.0",
    "project_version": "1.2.0",
}


def issues(registry: ir.IssueRegistry) -> dict[str, ir.IssueEntry]:
    """The MowDeck issues by id."""
    return {i.issue_id: i for i in registry.issues.values() if i.domain == DOMAIN}


async def test_outdated_bridge(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    loaded_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """An issue per mower while its bridge reports outdated firmware; it goes with the fix."""
    issue_id = f"ble_bridge_outdated_{MOWER_ID}_{loaded_entry.entry_id}"
    assert issue_id not in issues(issue_registry)
    await fake_api.push_state(MOWER_ID, mower_state(links={"ble_bridge": BRIDGE}))
    await wait_for(hass, lambda: issue_id in issues(issue_registry))
    issue = issues(issue_registry)[issue_id]
    assert issue.translation_key == "ble_bridge_outdated"
    assert issue.translation_placeholders == {
        "mower": "Luba",
        "reason": "project_version",
        "esphome_version": "2025.6.0",
        "project_version": "1.2.0",
    }
    assert issue.learn_more_url == f"{fake_api.base_url}/mowers/{MOWER_ID}"

    # Still outdated: one issue; current firmware: gone.
    await fake_api.push_state(MOWER_ID, mower_state(links={"ble_bridge": {**BRIDGE, "rssi": -60}}))
    await fake_api.push_state(
        MOWER_ID, mower_state(links={"ble_bridge": {**BRIDGE, "outdated": False}})
    )
    await wait_for(hass, lambda: issue_id not in issues(issue_registry))

    # The issue also goes when the entry unloads.
    await fake_api.push_state(MOWER_ID, mower_state(links={"ble_bridge": BRIDGE}))
    await wait_for(hass, lambda: issue_id in issues(issue_registry))
    await hass.config_entries.async_unload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert issue_id not in issues(issue_registry)


async def test_token_without_notifications(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A token without notifications.view gets an issue that links to the token page; it goes
    once a token with the key is in use (checked every 10 minutes)."""
    fake_api.me_overrides["permissions"] = ["mower.view"]
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    issue_id = f"missing_permission_notifications_view_{config_entry.entry_id}"
    issue = issues(issue_registry)[issue_id]
    assert issue.translation_key == "missing_permission_notifications"
    assert issue.learn_more_url == f"{fake_api.base_url}/settings/api-tokens"
    assert issue.translation_placeholders == {
        "permission": "notifications.view",
        "url": f"{fake_api.base_url}/settings/api-tokens",
    }

    # A failing check changes nothing; a token with the key clears it.
    fake_api.problem_paths["/api/v1/identity/me"] = 500
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=11))
    await hass.async_block_till_done()
    assert issue_id in issues(issue_registry)
    fake_api.problem_paths.clear()
    fake_api.me_overrides["permissions"] = ["mower.view", "notifications.view"]
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=22))
    await wait_for(hass, lambda: issue_id not in issues(issue_registry))

    # Lost again, then the entry unloads: the issue goes with it.
    fake_api.me_overrides["permissions"] = ["mower.view"]
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=33))
    await wait_for(hass, lambda: issue_id in issues(issue_registry))
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert issue_id not in issues(issue_registry)


async def test_me_without_permissions_raises_nothing(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    loaded_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A MowDeck that names no permissions, or a revoked token, raises no permission issue."""
    monitor_issue = f"missing_permission_notifications_view_{loaded_entry.entry_id}"
    fake_api.me_overrides["permissions"] = None
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=11))
    await hass.async_block_till_done()
    assert monitor_issue not in issues(issue_registry)
    fake_api.revoke()
    fake_api.expire_access_tokens()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=22))
    await hass.async_block_till_done()
    assert monitor_issue not in issues(issue_registry)


async def test_token_issues_link_the_token_page(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    loaded_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """The 403 issues of the polled services and the refused live connection link to MowDeck's
    API token page."""
    fake_api.forbidden_paths.add("/api/v1/telemetry/mowers")
    await loaded_entry.runtime_data.health.async_refresh()
    await hass.async_block_till_done()
    health = issues(issue_registry)[f"health_forbidden_{loaded_entry.entry_id}"]
    assert health.learn_more_url == f"{fake_api.base_url}/settings/api-tokens"

    fake_api.ws_reject_auth = True
    await fake_api.close_sockets(WSCloseCode.GOING_AWAY)
    issue_id = f"realtime_unauthorized_{loaded_entry.entry_id}"
    await wait_for(hass, lambda: issue_id in issues(issue_registry), rounds=600)
    assert issues(issue_registry)[issue_id].learn_more_url == (
        f"{fake_api.base_url}/settings/api-tokens"
    )
