"""Setup, devices, unload, setup failures and diagnostics."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mowdeck.const import DOMAIN
from custom_components.mowdeck.diagnostics import async_get_config_entry_diagnostics

from .conftest import wait_for
from .fake_mowdeck import API_TOKEN, MOWER_ID, FakeMowDeck

EXPECTED_ENTITIES = {
    "lawn_mower.luba": "docked",
    "sensor.luba_battery": "87",
    "sensor.luba_activity": "docked",
    "sensor.luba_rtk_fix": "fixed",
    "sensor.luba_blade_height": "50",
    "sensor.luba_speed": "0.0",
    "sensor.luba_error_code": "unknown",
    "binary_sensor.luba_charging": "on",
    "binary_sensor.luba_online": "on",
    "binary_sensor.luba_error": "off",
    "button.luba_emergency_stop": "unknown",
    "button.luba_return_to_dock": "unknown",
    "button.luba_end_job": "unknown",
    "number.luba_blade_height": "50.0",
    "number.luba_mowing_speed": "0.0",
    "select.luba_area": "all",
    "device_tracker.luba_position": "not_home",
}


async def test_setup_creates_device_and_entities(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """One device per mower with every entity, values from the fake API."""
    assert loaded_entry.state is ConfigEntryState.LOADED
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, MOWER_ID), loaded_entry.entry_id
    )
    assert device is not None
    assert device.name == "Luba"
    assert device.manufacturer == "Mammotion"
    assert device.model == "Luba 2 AWD (simulated)"
    assert device.sw_version == "sim-1.0"
    assert device.serial_number == "SIM-0001"
    assert device.configuration_url is not None
    assert device.configuration_url.endswith(f"/mowers/{MOWER_ID}")

    for entity_id, expected in EXPECTED_ENTITIES.items():
        state = hass.states.get(entity_id)
        assert state is not None, entity_id
        assert state.state == expected, (entity_id, state.state)
        entry = entity_registry.async_get(entity_id)
        assert entry is not None
        assert entry.device_id == device.id

    tracker = hass.states.get("device_tracker.luba_position")
    assert tracker is not None
    assert tracker.attributes["latitude"] == 48.137
    assert tracker.attributes["longitude"] == 11.575
    assert tracker.attributes["source_type"] == "gps"
    assert tracker.attributes["heading"] == 90.0

    number = hass.states.get("number.luba_blade_height")
    assert number is not None
    assert (number.attributes["min"], number.attributes["max"]) == (25, 70)
    speed = hass.states.get("number.luba_mowing_speed")
    assert speed is not None
    assert speed.attributes["max"] == 1.2

    area = hass.states.get("select.luba_area")
    assert area is not None
    assert area.attributes["options"] == ["all", "Front", "Back", "selection"]


async def test_unload_cleans_up(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fake_api: FakeMowDeck
) -> None:
    """Unloading closes the WebSocket and removes the entities' states."""
    assert fake_api.open_sockets == 1
    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert loaded_entry.state is ConfigEntryState.NOT_LOADED
    await wait_for(hass, lambda: fake_api.open_sockets == 0)
    state = hass.states.get("lawn_mower.luba")
    assert state is not None
    assert state.state == "unavailable"


async def test_setup_with_revoked_token_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, fake_api: FakeMowDeck
) -> None:
    """A rejected token fails the setup and opens a reauth flow."""
    fake_api.revoke()
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_setup_retries_when_api_down(
    hass: HomeAssistant, config_entry: MockConfigEntry, fake_api: FakeMowDeck
) -> None:
    """Server errors make the setup retry later."""
    fake_api.fail_requests = 100
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_diagnostics_redact_token(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    """The API token and connection options never appear in diagnostics."""
    diagnostics = await async_get_config_entry_diagnostics(hass, loaded_entry)
    assert API_TOKEN not in str(diagnostics)
    assert diagnostics["entry"]["data"]["api_token"] == "**REDACTED**"  # noqa: S105
    assert diagnostics["realtime"]["connected"] is True
    mower = diagnostics["coordinator"]["mowers"][MOWER_ID]
    assert mower["connection_options"] == "**REDACTED**"
    assert diagnostics["coordinator"]["states"][MOWER_ID]["battery_percent"] == 87
