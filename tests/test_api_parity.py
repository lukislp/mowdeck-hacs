"""Fields of the current MowDeck API: precision, firmware, drive speed, health, links, errors."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.components.number import ATTR_VALUE, SERVICE_SET_VALUE
from homeassistant.components.update import SERVICE_INSTALL
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mowdeck.const import DOMAIN

from .conftest import wait_for
from .fake_mowdeck import MOWER_ID, FakeMowDeck, mower_state

MOWER = "lawn_mower.luba"
FIRMWARE = "update.luba_firmware"
DRIVE_SPEED = "number.luba_manual_drive_speed"


def sent(fake_api: FakeMowDeck) -> list[dict[str, Any]]:
    """The command bodies the fake Fleet received."""
    return [c["command"] for c in fake_api.commands]


def state_of(hass: HomeAssistant, entity_id: str) -> Any:
    """The state object of an entity (asserting it exists)."""
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


async def call(hass: HomeAssistant, domain: str, service: str, entity_id: str, **data: Any) -> None:
    """Call a service on one entity and wait."""
    await hass.services.async_call(
        domain, service, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True
    )


@pytest.fixture
def rich_mower(fake_api: FakeMowDeck) -> FakeMowDeck:
    """A mower with every optional capability: firmware updates, the manual-drive speed
    setting, a Bluetooth bridge link and a mowing speed range."""
    fake_api.capabilities[MOWER_ID].update(
        {
            "min_speed_mps": 0.25,
            "max_speed_mps": 1.0000000000000002,
            "firmware_update": True,
            "drive_speed_setting": {"min_mps": 0.2, "max_mps": 1.5, "step_mps": 0.1},
            "links_supported": ["ble_bridge"],
        }
    )
    fake_api.states[MOWER_ID] = mower_state(
        speed_mps=0.30000000000000004,
        drive_speed_mps=0.7000000000000001,
        firmware="1.10.4",
        firmware_info={
            "installed": "1.10.4",
            "available": "1.11.0",
            "update_state": "available",
            "progress": None,
            "release_notes": "Faster RTK fix.",
            "checked_at": None,
            "failure_reason": None,
        },
        links={"ble_bridge": {"connected": True, "rssi": -67}},
    )
    return fake_api


@pytest.mark.usefixtures("rich_mower", "loaded_entry")
async def test_speeds_have_two_decimals(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """Sensor and number show the mowing speed with two decimals, no float noise; the
    number's range comes from the capabilities."""
    assert state_of(hass, "sensor.luba_speed").state == "0.3"
    speed = state_of(hass, "number.luba_mowing_speed")
    assert speed.state == "0.3"
    assert speed.attributes["step"] == 0.01
    assert (speed.attributes["min"], speed.attributes["max"]) == (0.25, 1.0)

    await fake_api.push_state(MOWER_ID, mower_state(speed_mps=0.5549999999))
    await wait_for(hass, lambda: state_of(hass, "sensor.luba_speed").state == "0.55")
    assert state_of(hass, "number.luba_mowing_speed").state == "0.55"

    await call(hass, "number", SERVICE_SET_VALUE, "number.luba_mowing_speed", **{ATTR_VALUE: 0.6})
    await call(hass, DOMAIN, "set_speed", MOWER, mps=0.7000000001)
    assert sent(fake_api) == [
        {"kind": "set_speed", "mps": 0.6},
        {"kind": "set_speed", "mps": 0.7},
    ]


@pytest.mark.usefixtures("loaded_entry")
async def test_speed_minimum_without_capability(hass: HomeAssistant) -> None:
    """Without min_speed_mps the number starts at 0.1 m/s."""
    speed = state_of(hass, "number.luba_mowing_speed")
    assert (speed.attributes["min"], speed.attributes["max"]) == (0.1, 1.2)


@pytest.mark.usefixtures("rich_mower")
async def test_drive_speed_number(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    loaded_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """The manual-drive speed setting is disabled by default; enabled it shows the setting
    and sends set_drive_speed."""
    registered = entity_registry.async_get(DRIVE_SPEED)
    assert registered is not None
    assert registered.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    entity_registry.async_update_entity(DRIVE_SPEED, disabled_by=None)
    await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    await wait_for(hass, lambda: hass.states.get(DRIVE_SPEED) is not None)

    number = state_of(hass, DRIVE_SPEED)
    assert number.state == "0.7"
    assert (number.attributes["min"], number.attributes["max"]) == (0.2, 1.5)
    assert number.attributes["step"] == 0.1
    await call(hass, "number", SERVICE_SET_VALUE, DRIVE_SPEED, **{ATTR_VALUE: 0.9})
    assert sent(fake_api) == [{"kind": "set_drive_speed", "speed_mps": 0.9}]

    fake_api.forbid_commands = True
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "number", SERVICE_SET_VALUE, DRIVE_SPEED, **{ATTR_VALUE: 1.0})
    assert err.value.translation_key == "command_forbidden"
    assert err.value.translation_placeholders == {
        "command": "set_drive_speed",
        "permission": "mower.drive",
    }


@pytest.mark.usefixtures("loaded_entry")
async def test_optional_entities_need_capabilities(hass: HomeAssistant) -> None:
    """Without the capabilities there is no firmware, drive speed or bridge entity."""
    assert hass.states.get(FIRMWARE) is None
    assert hass.states.get("sensor.luba_bluetooth_bridge_signal") is None
    assert hass.states.get(DRIVE_SPEED) is None


@pytest.mark.usefixtures("rich_mower", "loaded_entry")
async def test_firmware_update(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """The update entity offers the available version, installs it and shows progress."""
    update = state_of(hass, FIRMWARE)
    assert update.state == "on"
    assert update.attributes["installed_version"] == "1.10.4"
    assert update.attributes["latest_version"] == "1.11.0"
    assert update.attributes["release_summary"] == "Faster RTK fix."
    assert update.attributes["in_progress"] is False

    await call(hass, "update", SERVICE_INSTALL, FIRMWARE)
    assert sent(fake_api) == [{"kind": "firmware_update", "version": "1.11.0"}]

    info = {"installed": "1.10.4", "available": "1.11.0", "update_state": "installing"}
    await fake_api.push_state(
        MOWER_ID, mower_state(activity="updating", firmware_info={**info, "progress": 42})
    )
    await wait_for(hass, lambda: state_of(hass, FIRMWARE).attributes["in_progress"] is True)
    assert state_of(hass, FIRMWARE).attributes["update_percentage"] == 42
    assert state_of(hass, MOWER).state == "docked"
    assert state_of(hass, "sensor.luba_activity").state == "updating"

    done = {"installed": "1.11.0", "available": None, "update_state": "idle"}
    await fake_api.push_state(MOWER_ID, mower_state(firmware="1.11.0", firmware_info=done))
    await wait_for(hass, lambda: state_of(hass, FIRMWARE).state == "off")
    assert state_of(hass, FIRMWARE).attributes["latest_version"] == "1.11.0"


@pytest.mark.usefixtures("rich_mower", "entity_registry_enabled_by_default", "loaded_entry")
async def test_bridge_signal(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """The Bluetooth bridge's RSSI in whole dBm."""
    signal = state_of(hass, "sensor.luba_bluetooth_bridge_signal")
    assert signal.state == "-67"
    assert signal.attributes["unit_of_measurement"] == "dBm"
    await fake_api.push_state(MOWER_ID, mower_state(links={}))
    await wait_for(
        hass, lambda: state_of(hass, "sensor.luba_bluetooth_bridge_signal").state == "unknown"
    )


@pytest.mark.usefixtures("loaded_entry")
async def test_health_sensors(hass: HomeAssistant) -> None:
    """Blade hours since the change, mowing hours, distance (in km) and battery cycles."""
    assert state_of(hass, "sensor.luba_blade_hours_since_change").state == "12.35"
    assert state_of(hass, "sensor.luba_mowing_hours").state == "40.0"
    distance = state_of(hass, "sensor.luba_distance_driven")
    assert distance.attributes["unit_of_measurement"] == "km"
    assert float(distance.state) == pytest.approx(12.346, abs=0.001)
    cycles = state_of(hass, "sensor.luba_battery_cycles")
    assert cycles.state == "17"
    # the fake answers like MowDeck before the source fields: its own count
    assert (cycles.attributes["source"], cycles.attributes["tracked_cycles"]) == ("telemetry", 17)


async def test_battery_cycles_from_the_device_counter(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """With battery_cycles_source device the mower's lifetime counter is shown."""
    fake_api.health[MOWER_ID].update(
        {
            "battery_cycles": 152,
            "battery_cycles_tracked": 2,
            "device_battery_cycles": 152,
            "battery_cycles_source": "device",
        }
    )
    health = loaded_entry.runtime_data.health
    await health.async_refresh()
    await hass.async_block_till_done()
    cycles = state_of(hass, "sensor.luba_battery_cycles")
    assert cycles.state == "152"
    assert (cycles.attributes["source"], cycles.attributes["tracked_cycles"]) == ("device", 2)

    fake_api.health[MOWER_ID].update(
        {
            "battery_cycles": 3,
            "battery_cycles_tracked": 3,
            "device_battery_cycles": None,
            "battery_cycles_source": "telemetry",
        }
    )
    await health.async_refresh()
    await hass.async_block_till_done()
    cycles = state_of(hass, "sensor.luba_battery_cycles")
    assert cycles.state == "3"
    assert (cycles.attributes["source"], cycles.attributes["tracked_cycles"]) == ("telemetry", 3)


async def test_blade_hours_from_the_device_counter(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """With blade_source device the mower's own counter is shown; no health means unknown."""
    fake_api.health[MOWER_ID].update(
        {"blade_source": "device", "device_blade_hours": 3.5, "device_blade_warn_hours": 60.0}
    )
    health = loaded_entry.runtime_data.health
    await health.async_refresh()
    await hass.async_block_till_done()
    blade = state_of(hass, "sensor.luba_blade_hours_since_change")
    assert blade.state == "3.5"
    assert blade.attributes["threshold"] == 60.0

    fake_api.health.clear()
    await health.async_refresh()
    await hass.async_block_till_done()
    assert state_of(hass, "sensor.luba_mowing_hours").state == "unknown"


@pytest.mark.usefixtures("loaded_entry")
async def test_error_category_attributes(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """Error code with message, category, source and drive_recoverable."""
    await fake_api.push_state(
        MOWER_ID,
        mower_state(
            activity="error",
            charging=False,
            error_code="1201",
            error_message="The robot is stuck",
            drive_recoverable=True,
            raw={"error_category": "trapped", "error_source": "error_log"},
        ),
    )
    await wait_for(hass, lambda: state_of(hass, "sensor.luba_error_code").state == "1201")
    attributes = state_of(hass, "sensor.luba_error_code").attributes
    assert attributes["error_message"] == "The robot is stuck"
    assert attributes["error_category"] == "trapped"
    assert attributes["error_source"] == "error_log"
    assert attributes["drive_recoverable"] is True

    # A cleared error drops the category even when raw still carries it.
    await fake_api.push_state(MOWER_ID, mower_state(raw={"error_category": "trapped"}))
    await wait_for(hass, lambda: state_of(hass, "sensor.luba_error_code").state == "unknown")
    attributes = state_of(hass, "sensor.luba_error_code").attributes
    assert attributes["error_category"] is None
    assert attributes["drive_recoverable"] is False


@pytest.mark.usefixtures("loaded_entry")
async def test_link_lost_is_offline(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """The gateway's link-lost state (connected false) turns Online off at once."""
    assert state_of(hass, "binary_sensor.luba_online").state == "on"
    await fake_api.push_state(MOWER_ID, mower_state(connected=False))
    await wait_for(hass, lambda: state_of(hass, "binary_sensor.luba_online").state == "off")
    await fake_api.push_state(MOWER_ID, mower_state())
    await wait_for(hass, lambda: state_of(hass, "binary_sensor.luba_online").state == "on")


@pytest.mark.usefixtures("loaded_entry")
async def test_position_attributes(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """Heading with one decimal and the position source."""
    tracker = state_of(hass, "device_tracker.luba_position")
    assert tracker.attributes["position_source"] == "rtk"
    position = {"lat": 48.137, "lon": 11.575, "heading": 123.456789}
    await fake_api.push_state(MOWER_ID, mower_state(position=position, position_source="unknown"))
    await wait_for(
        hass,
        lambda: state_of(hass, "device_tracker.luba_position").attributes.get("heading") == 123.5,
    )
    assert "position_source" not in state_of(hass, "device_tracker.luba_position").attributes


@pytest.mark.usefixtures("loaded_entry")
async def test_forbidden_names_the_permission(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """The 403 message names the permission the command needs."""
    fake_api.forbid_commands = True
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "lawn_mower", "dock", MOWER)
    assert err.value.translation_placeholders == {
        "command": "return_to_dock",
        "permission": "mower.control",
    }
