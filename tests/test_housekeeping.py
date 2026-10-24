"""Reconfigure, end job, firmware check, blade change, permissions, capability changes,
devices, the health coordinator and diagnostics."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.components.lawn_mower import SERVICE_STOP, LawnMowerEntityFeature
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    CONF_URL,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mowdeck import async_remove_config_entry_device
from custom_components.mowdeck.const import CONF_API_TOKEN, DOMAIN
from custom_components.mowdeck.diagnostics import async_get_config_entry_diagnostics

from .conftest import wait_for
from .fake_mowdeck import ACCOUNT_ID, API_TOKEN, MOWER_ID, FakeMowDeck, mower_state

MOWER = "lawn_mower.luba"
FIRMWARE = "update.luba_firmware"
FIRMWARE_CHECK = "button.luba_check_firmware_now"
BLADE_CHANGED = "button.luba_blade_changed"
LIGHT = "switch.luba_camera_light"


def sent(fake_api: FakeMowDeck) -> list[dict[str, Any]]:
    """The command bodies the fake Fleet received."""
    return [c["command"] for c in fake_api.commands]


def state_of(hass: HomeAssistant, entity_id: str) -> Any:
    """The state object of an entity (asserting it exists)."""
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


async def press(hass: HomeAssistant, entity_id: str) -> None:
    """Press a button and wait."""
    await hass.services.async_call(
        "button", SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


def mower_device(registry: dr.DeviceRegistry, entry: MockConfigEntry) -> dr.DeviceEntry | None:
    """The mower's device of the entry."""
    return registry.async_get_device_by_identifier((DOMAIN, MOWER_ID), entry.entry_id)


async def refresh_static(entry: MockConfigEntry) -> None:
    """Refetch capabilities, maps and permissions now."""
    coordinator = entry.runtime_data.coordinator
    coordinator._static_fetched_at.clear()
    await coordinator.async_refresh()


# Reconfigure


async def test_reconfigure_changes_url_and_keeps_token(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """Without a token the stored one is kept; the URL is normalised."""
    result = await loaded_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "ftp://nowhere"}
    )
    assert result["errors"] == {CONF_URL: "invalid_url"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: fake_api.base_url + "/"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert loaded_entry.data == {CONF_URL: fake_api.base_url, CONF_API_TOKEN: API_TOKEN}


async def test_reconfigure_with_new_token(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """A new token of the same account replaces the stored one."""
    fake_api.valid_api_tokens["new-token"] = ACCOUNT_ID
    result = await loaded_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: fake_api.base_url, CONF_API_TOKEN: "new-token"}
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert loaded_entry.data[CONF_API_TOKEN] == "new-token"


async def test_reconfigure_refuses_another_account(
    hass: HomeAssistant, fake_api: FakeMowDeck, config_entry: MockConfigEntry
) -> None:
    """A token of another account aborts and changes nothing."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: fake_api.base_url, CONF_API_TOKEN: "other-token"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert config_entry.data[CONF_API_TOKEN] == API_TOKEN


# End job


@pytest.mark.usefixtures("loaded_entry")
async def test_end_job_button_and_stop(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """The End job button and lawn_mower.stop end the job; dock only pauses it."""
    assert state_of(hass, MOWER).attributes[ATTR_SUPPORTED_FEATURES] & LawnMowerEntityFeature.STOP
    await press(hass, "button.luba_end_job")
    await hass.services.async_call(
        "lawn_mower", SERVICE_STOP, {ATTR_ENTITY_ID: MOWER}, blocking=True
    )
    assert sent(fake_api) == [
        {"kind": "return_to_dock", "end_job": True},
        {"kind": "return_to_dock", "end_job": True},
    ]


# Firmware check


async def test_firmware_check(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """The button appears with the firmware_update capability; its answer shows at once."""
    assert hass.states.get(FIRMWARE_CHECK) is None
    fake_api.capabilities[MOWER_ID]["firmware_update"] = True
    await refresh_static(loaded_entry)
    await hass.async_block_till_done()
    assert state_of(hass, FIRMWARE).state == "off"

    await press(hass, FIRMWARE_CHECK)
    await hass.async_block_till_done()
    update = state_of(hass, FIRMWARE)
    assert update.state == "on"
    assert update.attributes["latest_version"] == "1.12.0"

    fake_api.firmware_check_status = 409
    with pytest.raises(HomeAssistantError) as err:
        await press(hass, FIRMWARE_CHECK)
    assert err.value.translation_key == "action_failed"
    assert "firmware-check-unsupported" in str(err.value.translation_placeholders)


async def test_capability_loss_keeps_entities(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    loaded_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A lost capability leaves its entities registered and unavailable, with the owner's
    changes; when it returns they are available again under the same entity ids."""
    fake_api.capabilities[MOWER_ID]["firmware_update"] = True
    await refresh_static(loaded_entry)
    await hass.async_block_till_done()
    assert entity_registry.async_get(FIRMWARE) is not None
    assert hass.states.get(LIGHT) is not None
    entity_registry.async_update_entity(LIGHT, name="Headlight", icon="mdi:car-light-high")
    registered = len(er.async_entries_for_config_entry(entity_registry, loaded_entry.entry_id))

    fake_api.capabilities[MOWER_ID]["firmware_update"] = False
    fake_api.capabilities[MOWER_ID]["light"] = False
    await refresh_static(loaded_entry)
    await hass.async_block_till_done()
    for entity_id in (FIRMWARE, FIRMWARE_CHECK, LIGHT):
        assert entity_registry.async_get(entity_id) is not None, entity_id
        assert state_of(hass, entity_id).state == STATE_UNAVAILABLE, entity_id
    light = entity_registry.async_get(LIGHT)
    assert light is not None
    assert (light.name, light.icon) == ("Headlight", "mdi:car-light-high")

    fake_api.capabilities[MOWER_ID]["firmware_update"] = True
    fake_api.capabilities[MOWER_ID]["light"] = True
    await refresh_static(loaded_entry)
    await hass.async_block_till_done()
    for entity_id in (FIRMWARE, FIRMWARE_CHECK, LIGHT):
        assert state_of(hass, entity_id).state != STATE_UNAVAILABLE, entity_id
    assert state_of(hass, LIGHT).attributes["friendly_name"].endswith("Headlight")
    assert (
        len(er.async_entries_for_config_entry(entity_registry, loaded_entry.entry_id)) == registered
    )


async def test_never_offered_capability_creates_nothing(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    loaded_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Entities of a capability the mower never had are not created, not even unavailable."""
    assert not fake_api.capabilities[MOWER_ID].get("firmware_update")
    fake_api.capabilities[MOWER_ID]["model"] = "Luba mini"
    await refresh_static(loaded_entry)
    await hass.async_block_till_done()
    for entity_id in (FIRMWARE, FIRMWARE_CHECK):
        assert entity_registry.async_get(entity_id) is None, entity_id
        assert hass.states.get(entity_id) is None, entity_id


# Blade change and permissions


async def test_blade_changed_button(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    loaded_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Disabled by default; enabled it records the change and resets the blade hours."""
    registered = entity_registry.async_get(BLADE_CHANGED)
    assert registered is not None
    assert registered.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    entity_registry.async_update_entity(BLADE_CHANGED, disabled_by=None)
    await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    await wait_for(hass, lambda: hass.states.get(BLADE_CHANGED) is not None)

    assert state_of(hass, "sensor.luba_blade_hours_since_change").state == "12.35"
    await press(hass, BLADE_CHANGED)
    await hass.async_block_till_done()
    assert fake_api.blade_resets == [{}]
    blade = state_of(hass, "sensor.luba_blade_hours_since_change")
    assert blade.state == "0.0"
    assert blade.attributes["changed_at"] is not None


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_blade_changed_needs_fleet_manage(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """Without can_manage_mowers the button is unavailable; a 403 names fleet.manage."""
    assert hass.states.get(BLADE_CHANGED) is not None
    fake_api.relations = {"can_view", "can_control"}
    with pytest.raises(HomeAssistantError) as err:
        await press(hass, BLADE_CHANGED)
    assert err.value.translation_key == "action_forbidden"
    assert err.value.translation_placeholders == {
        "command": "blade_changed",
        "permission": "fleet.manage",
    }
    await refresh_static(loaded_entry)
    await hass.async_block_till_done()
    # Kept (a permission can come back), but unavailable.
    assert state_of(hass, BLADE_CHANGED).state == STATE_UNAVAILABLE


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_unknown_permissions_hide_nothing(
    hass: HomeAssistant, fake_api: FakeMowDeck, config_entry: MockConfigEntry
) -> None:
    """A MowDeck without the permission endpoint: every entity is offered."""
    fake_api.relations = None
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(BLADE_CHANGED) is not None
    assert config_entry.runtime_data.coordinator.data.permissions == {}
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


# Devices


async def test_device_follows_firmware_and_model(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    loaded_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Firmware, model and name changes reach the device registry."""
    await fake_api.push_state(MOWER_ID, mower_state(firmware="sim-2.0"))
    await wait_for(
        hass,
        lambda: (
            mower_device(device_registry, loaded_entry).sw_version  # type: ignore[union-attr]
            == "sim-2.0"
        ),
    )
    fake_api.capabilities[MOWER_ID]["model"] = "Luba 2 AWD X"
    fake_api.mowers[0]["name"] = "Luba hinten"
    await refresh_static(loaded_entry)
    await hass.async_block_till_done()
    device = mower_device(device_registry, loaded_entry)
    assert device is not None
    assert (device.model, device.name) == ("Luba 2 AWD X", "Luba hinten")


async def test_remove_device(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    loaded_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A visible mower's device cannot be deleted; a vanished mower's device goes by itself."""
    device = mower_device(device_registry, loaded_entry)
    assert device is not None
    assert not await async_remove_config_entry_device(hass, loaded_entry, device)

    fake_api.mowers.clear()
    await loaded_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert mower_device(device_registry, loaded_entry) is None
    assert entity_registry.async_get(MOWER) is None
    assert entity_registry.async_get(LIGHT) is None
    assert await async_remove_config_entry_device(hass, loaded_entry, device)


# Health coordinator


async def test_health_forbidden_raises_repair(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    loaded_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A 403 on the health totals: only those sensors go unavailable, a Repairs issue names
    the permission, and both clear with the next successful poll."""
    health = loaded_entry.runtime_data.health
    issue_id = f"health_forbidden_{loaded_entry.entry_id}"
    fake_api.forbidden_paths.add(f"/api/v1/telemetry/mowers/{MOWER_ID}/health")
    await health.async_refresh()
    await hass.async_block_till_done()
    assert state_of(hass, "sensor.luba_mowing_hours").state == "unavailable"
    assert state_of(hass, "sensor.luba_battery").state == "87"
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders == {"url": fake_api.base_url, "permission": "mower.view"}

    fake_api.forbidden_paths.clear()
    await health.async_refresh()
    await hass.async_block_till_done()
    assert state_of(hass, "sensor.luba_mowing_hours").state == "40.0"
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_health_down_does_not_block_setup(
    hass: HomeAssistant, fake_api: FakeMowDeck, config_entry: MockConfigEntry
) -> None:
    """Setup succeeds while the health totals fail; their sensors are unavailable."""
    fake_api.forbidden_paths.add(f"/api/v1/telemetry/mowers/{MOWER_ID}/health")
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert state_of(hass, "sensor.luba_battery").state == "87"
    assert state_of(hass, "sensor.luba_blade_hours_since_change").state == "unavailable"
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_diagnostics_redact_places_and_names(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """Maps become counts; coordinates and area names never appear."""
    fake_api.mowers[0]["area_names"] = {"zone-front": "Vorgarten"}
    await refresh_static(loaded_entry)
    diagnostics = await async_get_config_entry_diagnostics(hass, loaded_entry)
    text = str(diagnostics)
    for secret in ("Vorgarten", "48.137", "11.575", "Front"):
        assert secret not in text
    assert diagnostics["coordinator"]["maps"][MOWER_ID]["areas"] == 2
    assert diagnostics["coordinator"]["permissions"][MOWER_ID] == [
        "can_control",
        "can_drive",
        "can_edit_schedule",
        "can_manage_mowers",
    ]
    assert diagnostics["health"]["totals"][MOWER_ID]["mowing_hours"] == pytest.approx(40.0)


THRESHOLD = "number.luba_blade_change_reminder"


async def test_blade_change_reminder(
    hass: HomeAssistant, fake_api: FakeMowDeck, config_entry: MockConfigEntry
) -> None:
    """With MowDeck 0.8 the reminder threshold is a number: it shows the threshold in effect
    and sets the mower's own; a 403 names fleet.manage."""
    fake_api.blade_thresholds = []
    fake_api.health[MOWER_ID]["blade_hours_threshold_custom"] = False
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    number = hass.states.get(THRESHOLD)
    assert number is not None
    assert number.state == "50.0"
    assert number.attributes["unit_of_measurement"] == "h"
    assert number.attributes["custom"] is False

    await hass.services.async_call(
        "number", "set_value", {ATTR_ENTITY_ID: THRESHOLD, "value": 80}, blocking=True
    )
    assert fake_api.blade_thresholds == [{"hours": 80.0}]
    number = hass.states.get(THRESHOLD)
    assert number is not None
    assert (number.state, number.attributes["custom"]) == ("80.0", True)

    fake_api.relations = {"can_view", "can_control"}
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "number", "set_value", {ATTR_ENTITY_ID: THRESHOLD, "value": 60}, blocking=True
        )
    assert err.value.translation_placeholders == {
        "command": "blade_threshold",
        "permission": "fleet.manage",
    }
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("loaded_entry")
async def test_no_blade_change_reminder_before_mowdeck_0_8(hass: HomeAssistant) -> None:
    """A MowDeck whose health totals do not say whether the threshold is the mower's own
    cannot set it per mower: no number."""
    assert hass.states.get(THRESHOLD) is None
