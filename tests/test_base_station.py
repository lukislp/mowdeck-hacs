"""RTK base stations: one device each with sensors, binary sensors, a tracker and firmware."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.components.update import ATTR_INSTALLED_VERSION, ATTR_LATEST_VERSION
from homeassistant.components.update import SERVICE_INSTALL as UPDATE_INSTALL
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mowdeck import async_remove_config_entry_device
from custom_components.mowdeck.const import DOMAIN
from custom_components.mowdeck.diagnostics import async_get_config_entry_diagnostics

from .conftest import wait_for
from .fake_mowdeck import (
    BASE_STATION_ID,
    MOWER_ID,
    FakeMowDeck,
    base_station,
    base_station_state,
    mower_state,
)

CORRECTION = "sensor.garden_base_rtk_correction_link"
RTK_STATUS = "sensor.garden_base_rtk_status"
SATELLITES = "sensor.garden_base_satellites"
LORA = "sensor.garden_base_lora_channel"
WIFI = "sensor.garden_base_wi_fi_signal"
LINK = "binary_sensor.garden_base_mower_link"
MOVED = "binary_sensor.garden_base_moved"
POSITION = "device_tracker.garden_base_position"
FIRMWARE = "update.garden_base_firmware"
STATION_DEVICE = (DOMAIN, f"base_station_{BASE_STATION_ID}")


@pytest.fixture
def with_base(fake_api: FakeMowDeck) -> FakeMowDeck:
    """A base station linked to the test mower, which reports it and can update it."""
    fake_api.base_stations = {BASE_STATION_ID: base_station()}
    fake_api.states[MOWER_ID] = mower_state(base_station=base_station_state())
    fake_api.capabilities[MOWER_ID]["base_station_firmware_update"] = True
    return fake_api


def state_of(hass: HomeAssistant, entity_id: str) -> Any:
    """The state object of an entity (asserting it exists)."""
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


def device_of(
    registry: dr.DeviceRegistry, entry: MockConfigEntry, identifier: tuple[str, str]
) -> dr.DeviceEntry | None:
    """The entry's device with the identifier."""
    return registry.async_get_device_by_identifier(identifier, entry.entry_id)


async def refresh_base_stations(entry: MockConfigEntry) -> None:
    """Read the base station list now."""
    await entry.runtime_data.base_stations.async_refresh()


@pytest.mark.usefixtures("with_base", "entity_registry_enabled_by_default")
async def test_device_and_values(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A device per base station behind its mower, with the values of the mower's report."""
    device = device_of(device_registry, loaded_entry, STATION_DEVICE)
    assert device is not None
    mower = device_of(device_registry, loaded_entry, (DOMAIN, MOWER_ID))
    assert mower is not None
    assert device.via_device_id == mower.id
    assert (device.name, device.model, device.sw_version) == (
        "Garden base",
        "RTK base station",
        "1.2.0",
    )
    assert device.configuration_url == f"{loaded_entry.data['url']}/mowers/{MOWER_ID}"

    assert state_of(hass, CORRECTION).state == "lora"
    assert state_of(hass, RTK_STATUS).state == "fixed"
    assert state_of(hass, RTK_STATUS).attributes["details_at"] is not None
    assert state_of(hass, SATELLITES).state == "31"
    assert state_of(hass, LORA).state == "17"
    assert state_of(hass, WIFI).state == "-61"
    assert state_of(hass, LINK).state == STATE_ON
    assert state_of(hass, MOVED).state == STATE_OFF
    tracker = state_of(hass, POSITION)
    assert (tracker.attributes["latitude"], tracker.attributes["longitude"]) == (
        48.1371,
        11.5751,
    )
    lora = entity_registry.async_get(LORA)
    assert lora is not None
    assert lora.entity_category is not None
    assert "details_at" not in state_of(hass, CORRECTION).attributes


@pytest.mark.usefixtures("with_base", "loaded_entry")
async def test_values_follow_the_mower(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """New mower states update the base station; unknown values read as unknown."""
    await fake_api.push_state(
        MOWER_ID,
        mower_state(
            base_station=base_station_state(
                correction_link="unknown",
                rtk_status="unknown",
                link_up=None,
                moved=True,
                satellites=None,
                position=None,
            )
        ),
    )
    await wait_for(hass, lambda: state_of(hass, MOVED).state == STATE_ON)
    assert state_of(hass, CORRECTION).state == STATE_UNKNOWN
    assert state_of(hass, RTK_STATUS).state == STATE_UNKNOWN
    assert state_of(hass, SATELLITES).state == STATE_UNKNOWN
    assert state_of(hass, LINK).state == STATE_UNKNOWN
    assert state_of(hass, POSITION).state == STATE_UNKNOWN

    # A report about another base station, or none at all, is not this one's.
    await fake_api.push_state(
        MOWER_ID, mower_state(base_station=base_station_state(base_station_id="other"))
    )
    await wait_for(hass, lambda: state_of(hass, MOVED).state == STATE_UNKNOWN)
    await fake_api.push_state(MOWER_ID, mower_state())
    await hass.async_block_till_done()
    assert state_of(hass, CORRECTION).state == STATE_UNKNOWN


@pytest.mark.usefixtures("with_base", "loaded_entry")
async def test_firmware_check_and_install(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """update_entity asks the vendor; install goes through the relaying mower."""
    update = state_of(hass, FIRMWARE)
    assert update.state == STATE_OFF
    assert update.attributes[ATTR_INSTALLED_VERSION] == "1.2.0"

    assert await async_setup_component(hass, "homeassistant", {})
    await hass.services.async_call(
        "homeassistant", "update_entity", {ATTR_ENTITY_ID: FIRMWARE}, blocking=True
    )
    await hass.async_block_till_done()
    update = state_of(hass, FIRMWARE)
    assert update.state == STATE_ON
    assert update.attributes[ATTR_LATEST_VERSION] == "1.3.0"
    assert update.attributes["release_summary"] == "Better LoRa range."

    await hass.services.async_call(
        "update", UPDATE_INSTALL, {ATTR_ENTITY_ID: FIRMWARE}, blocking=True
    )
    assert fake_api.base_station_updates == [
        {"station_id": BASE_STATION_ID, "version": "1.3.0", "mower_id": MOWER_ID}
    ]

    fake_api.relations = {"can_view", "can_control"}
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "update", UPDATE_INSTALL, {ATTR_ENTITY_ID: FIRMWARE}, blocking=True
        )
    assert err.value.translation_key == "action_forbidden"
    assert err.value.translation_placeholders == {
        "command": "base_station_firmware_update",
        "permission": "fleet.manage",
    }

    # A failed check is logged by update_entity; the entity keeps what it showed.
    fake_api.base_station_check_status = 409
    await hass.services.async_call(
        "homeassistant", "update_entity", {ATTR_ENTITY_ID: FIRMWARE}, blocking=True
    )
    assert state_of(hass, FIRMWARE).attributes[ATTR_LATEST_VERSION] == "1.3.0"


@pytest.mark.usefixtures("with_base", "loaded_entry")
async def test_firmware_progress_from_the_state(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """The state's firmware info shows an install in progress; without a relaying mower the
    entity cannot install."""
    info = {
        "installed": "1.2.0",
        "available": "1.3.0",
        "update_state": "installing",
        "progress": 40,
        "release_notes": "x" * 300,
        "checked_at": "2026-10-23T10:00:00Z",
        "failure_reason": None,
    }
    await fake_api.push_state(
        MOWER_ID, mower_state(base_station=base_station_state(firmware_info=info))
    )
    await wait_for(hass, lambda: state_of(hass, FIRMWARE).attributes["in_progress"] is True)
    update = state_of(hass, FIRMWARE)
    assert update.attributes["update_percentage"] == 40
    assert len(update.attributes["release_summary"]) == 255
    entity = hass.data["entity_components"]["update"].get_entity(FIRMWARE)
    assert entity is not None
    assert await entity.async_release_notes() == "x" * 300

    coordinator = hass.config_entries.async_entries(DOMAIN)[0].runtime_data.coordinator
    coordinator.data.capabilities[MOWER_ID]["base_station_firmware_update"] = False
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert state_of(hass, FIRMWARE).attributes["supported_features"] & 1 == 0

    await fake_api.push_state(
        MOWER_ID,
        mower_state(
            base_station=base_station_state(
                firmware_info={**info, "update_state": "idle", "progress": None},
                firmware=None,
            )
        ),
    )
    await wait_for(hass, lambda: state_of(hass, FIRMWARE).attributes["in_progress"] is False)
    assert state_of(hass, FIRMWARE).attributes["update_percentage"] is None
    assert state_of(hass, FIRMWARE).attributes[ATTR_LATEST_VERSION] == "1.2.0"


@pytest.mark.usefixtures("with_base")
async def test_base_station_comes_and_goes(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    loaded_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A renamed base station renames its device; a removed one takes its device along, and
    the device may then be deleted."""
    fake_api.base_stations = {BASE_STATION_ID: base_station(name="Hedge base", mower_ids=[])}
    await refresh_base_stations(loaded_entry)
    await hass.async_block_till_done()
    device = device_of(device_registry, loaded_entry, STATION_DEVICE)
    assert device is not None
    assert device.name == "Hedge base"
    assert device.via_device_id is None

    fake_api.base_stations = {}
    await refresh_base_stations(loaded_entry)
    await hass.async_block_till_done()
    assert device_of(device_registry, loaded_entry, STATION_DEVICE) is None
    assert await async_remove_config_entry_device(hass, loaded_entry, device)
    mower = device_of(device_registry, loaded_entry, (DOMAIN, MOWER_ID))
    assert mower is not None
    assert not await async_remove_config_entry_device(hass, loaded_entry, mower)

    # A new base station appears with its entities.
    fake_api.base_stations = {"second": base_station(id="second", name="Second base")}
    await refresh_base_stations(loaded_entry)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.second_base_moved") is not None


@pytest.mark.usefixtures("with_base")
async def test_mower_poll_keeps_the_base_station_device(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, device_registry: dr.DeviceRegistry
) -> None:
    """The mower list's stale device cleanup leaves base station devices alone; a listed base
    station's device cannot be deleted."""
    await loaded_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    device = device_of(device_registry, loaded_entry, STATION_DEVICE)
    assert device is not None
    assert not await async_remove_config_entry_device(hass, loaded_entry, device)


@pytest.mark.parametrize("problem", [None, 403])
async def test_no_base_stations(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    config_entry: MockConfigEntry,
    problem: int | None,
) -> None:
    """A MowDeck before 0.11 (404) or a token below the account (403): no base stations and
    no Repairs issue."""
    fake_api.base_stations = {BASE_STATION_ID: base_station()} if problem else None
    if problem:
        fake_api.forbidden_paths.add("/api/v1/fleet/base-stations")
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(MOVED) is None
    assert not config_entry.runtime_data.base_stations.data.available
    assert not ir.async_get(hass).issues
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("with_base")
async def test_diagnostics(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    """Diagnostics list the base stations without vendor ids or linked accounts."""
    diagnostics = await async_get_config_entry_diagnostics(hass, loaded_entry)
    stations = diagnostics["base_stations"]
    assert stations["available"] is True
    station = stations["stations"][BASE_STATION_ID]
    assert station["iot_id"] == "**REDACTED**"
    assert station["linked_account_id"] == "**REDACTED**"
    assert diagnostics["coordinator"]["states"][MOWER_ID]["base_station"]["position"] == (
        "**REDACTED**"
    )
