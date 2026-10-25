"""The mower's own settings (switch, number, select, time, button) and its rain sensor."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.components.number import ATTR_VALUE, SERVICE_SET_VALUE
from homeassistant.components.select import ATTR_OPTION, SERVICE_SELECT_OPTION
from homeassistant.components.time import ATTR_TIME
from homeassistant.components.time import SERVICE_SET_VALUE as SERVICE_SET_TIME
from homeassistant.const import (
    ATTR_ENTITY_ID,
    PERCENTAGE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mowdeck import commands
from custom_components.mowdeck.realtime import command_signal
from custom_components.mowdeck.settings import format_clock, parse_clock, shift_clock

from .conftest import wait_for
from .fake_mowdeck import MOWER_ID, FakeMowDeck, mower_state

RAIN_DETECTION = "switch.luba_rain_detection"
NIGHT_LIGHT = "switch.luba_night_light"
VOLUME = "number.luba_voice_volume"
RECHARGE = "number.luba_return_to_charge_at"
TURN_STYLE = "select.luba_turning_style"
BLADE_SPEED = "select.luba_blade_speed"
WILDLIFE = "select.luba_wildlife_protection"
QUIET_START = "time.luba_non_working_hours_start"
QUIET_END = "time.luba_non_working_hours_end"
QUIET_OFF = "button.luba_turn_off_non_working_hours"
RAIN = "binary_sensor.luba_rain"
QUIET = "non_work_hours"

# MowerCapabilities.settings of a LUBA 2 on firmware 2.1.1.5 (ADR 0046), plus a key this
# integration has no name for and a known key with an unexpected type.
SETTINGS: list[dict[str, Any]] = [
    {"key": "rain_detection", "type": "bool", "group": "weather"},
    {
        "key": "turn_style",
        "type": "enum",
        "group": "driving",
        "options": ["in_place", "multi_point"],
    },
    {
        "key": "blade_speed",
        "type": "enum",
        "group": "mowing",
        "options": ["slow", "normal", "fast"],
    },
    {
        "key": "wildlife_safety",
        "type": "enum",
        "group": "safety",
        "options": ["off", "stop_mowing", "low_speed_mowing"],
    },
    {"key": "night_light", "type": "bool", "group": "lights"},
    {
        "key": "recharge_level",
        "type": "int",
        "group": "battery",
        "min": 15,
        "max": 30,
        "step": 1,
        "unit": "%",
    },
    {
        "key": "voice_volume",
        "type": "int",
        "group": "voice",
        "min": 0,
        "max": 100,
        "step": 5,
        "unit": "%",
    },
    {"key": "non_work_hours", "type": "time_window", "group": "schedule"},
    {"key": "edge_trim", "type": "bool", "group": "driving"},
    {"key": "side_led", "type": "enum", "group": "lights", "options": ["on", "off"]},
    {"key": "quiet_minutes", "type": "int", "group": "schedule", "unit": "min"},
    {"key": "eco_window", "type": "time_window", "group": "schedule"},
]
VALUES: dict[str, Any] = {
    "rain_detection": True,
    "turn_style": "multi_point",
    "wildlife_safety": "off",
    "blade_speed": "normal",
    "night_light": False,
    "recharge_level": 20,
    "voice_volume": 60,
    "non_work_hours": {"start": "22:00", "end": "06:00"},
}


@pytest.fixture
def settings_mower(fake_api: FakeMowDeck) -> FakeMowDeck:
    """A mower that declares settings, has reported their values and its rain sensor."""
    fake_api.capabilities[MOWER_ID]["settings"] = SETTINGS
    fake_api.states[MOWER_ID] = mower_state(settings=dict(VALUES), rain_detected=False)
    return fake_api


@pytest.fixture
def fast_results() -> Iterator[None]:
    """Wait only briefly for command results nobody sends."""
    with patch.object(commands, "COMMAND_RESULT_TIMEOUT", 0.2):
        yield


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


def sent(fake_api: FakeMowDeck) -> list[dict[str, Any]]:
    """The command bodies the fake Fleet received."""
    return [c["command"] for c in fake_api.commands]


async def refresh_static(entry: MockConfigEntry) -> None:
    """Refetch capabilities, maps and permissions now."""
    coordinator = entry.runtime_data.coordinator
    coordinator._static_fetched_at.clear()
    await coordinator.async_refresh()


@pytest.mark.usefixtures("settings_mower", "loaded_entry")
async def test_entities_follow_the_declaration(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """One configuration entity per declared setting, with the reported values, ranges and
    options; unknown keys and known keys of another type get a generic name."""
    assert state_of(hass, RAIN_DETECTION).state == STATE_ON
    assert state_of(hass, NIGHT_LIGHT).state == STATE_OFF
    volume = state_of(hass, VOLUME)
    assert volume.state == "60"
    assert (volume.attributes["min"], volume.attributes["max"]) == (0, 100)
    assert volume.attributes["step"] == 5
    assert volume.attributes["unit_of_measurement"] == PERCENTAGE
    recharge = state_of(hass, RECHARGE)
    assert (recharge.state, recharge.attributes["min"], recharge.attributes["max"]) == (
        "20",
        15,
        30,
    )
    turn = state_of(hass, TURN_STYLE)
    assert turn.state == "multi_point"
    assert turn.attributes["options"] == ["in_place", "multi_point"]
    assert state_of(hass, WILDLIFE).state == "off"
    blade = state_of(hass, BLADE_SPEED)
    assert blade.state == "normal"
    assert blade.attributes["options"] == ["slow", "normal", "fast"]
    assert state_of(hass, QUIET_START).state == "22:00:00"
    assert state_of(hass, QUIET_END).state == "06:00:00"
    assert state_of(hass, QUIET_OFF).state == STATE_UNKNOWN

    # Unknown keys, and a known key declared with another type.
    assert state_of(hass, "switch.luba_setting_edge_trim").state == STATE_UNKNOWN
    assert state_of(hass, "select.luba_setting_side_led").attributes["options"] == ["on", "off"]
    quiet = state_of(hass, "number.luba_setting_quiet_minutes")
    assert quiet.attributes["unit_of_measurement"] == "min"
    assert (quiet.attributes["min"], quiet.attributes["max"], quiet.attributes["step"]) == (
        0,
        100,
        1,
    )
    assert state_of(hass, "time.luba_setting_eco_window_start").state == STATE_UNKNOWN
    assert hass.states.get("button.luba_turn_off_setting_eco_window") is not None

    for entity_id in (RAIN_DETECTION, VOLUME, TURN_STYLE, QUIET_START, QUIET_OFF):
        entry = entity_registry.async_get(entity_id)
        assert entry is not None
        assert entry.entity_category is EntityCategory.CONFIG
        assert entry.unique_id.startswith(f"{MOWER_ID}_setting_")


@pytest.mark.usefixtures("settings_mower", "loaded_entry")
async def test_changes_send_set_setting(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """Each entity sends set_setting with the value of its type; the reported value follows."""
    await call(hass, "switch", SERVICE_TURN_OFF, RAIN_DETECTION)
    await call(hass, "switch", SERVICE_TURN_ON, NIGHT_LIGHT)
    await call(hass, "number", SERVICE_SET_VALUE, VOLUME, **{ATTR_VALUE: 75.0})
    await call(hass, "select", SERVICE_SELECT_OPTION, TURN_STYLE, **{ATTR_OPTION: "in_place"})
    await call(hass, "select", SERVICE_SELECT_OPTION, BLADE_SPEED, **{ATTR_OPTION: "fast"})
    await call(hass, "time", SERVICE_SET_TIME, QUIET_START, **{ATTR_TIME: "21:30:45"})
    await call(hass, "time", SERVICE_SET_TIME, QUIET_END, **{ATTR_TIME: "07:00:00"})
    assert sent(fake_api) == [
        {"kind": "set_setting", "key": "rain_detection", "value": False},
        {"kind": "set_setting", "key": "night_light", "value": True},
        {"kind": "set_setting", "key": "voice_volume", "value": 75},
        {"kind": "set_setting", "key": "turn_style", "value": "in_place"},
        {"kind": "set_setting", "key": "blade_speed", "value": "fast"},
        {
            "kind": "set_setting",
            "key": "non_work_hours",
            "value": {"start": "21:30", "end": "06:00"},
        },
        {
            "kind": "set_setting",
            "key": "non_work_hours",
            "value": {"start": "21:30", "end": "07:00"},
        },
    ]
    await wait_for(hass, lambda: state_of(hass, QUIET_END).state == "07:00:00")
    assert state_of(hass, RAIN_DETECTION).state == STATE_OFF
    assert state_of(hass, NIGHT_LIGHT).state == STATE_ON
    assert state_of(hass, VOLUME).state == "75"
    assert state_of(hass, TURN_STYLE).state == "in_place"
    assert state_of(hass, BLADE_SPEED).state == "fast"
    assert state_of(hass, QUIET_START).state == "21:30:00"


@pytest.mark.usefixtures("settings_mower", "loaded_entry")
async def test_time_window_off_and_on(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """The button switches the window off; a bound switches it on with the last window, and
    without one one hour apart. Equal bounds are refused before anything is sent."""
    await call(hass, "button", SERVICE_PRESS, QUIET_OFF)
    await wait_for(hass, lambda: state_of(hass, QUIET_START).state == STATE_UNKNOWN)
    assert state_of(hass, QUIET_END).state == STATE_UNKNOWN
    await call(hass, "time", SERVICE_SET_TIME, QUIET_END, **{ATTR_TIME: "05:00:00"})
    assert sent(fake_api)[-2:] == [
        {"kind": "set_setting", "key": "non_work_hours", "value": None},
        {
            "kind": "set_setting",
            "key": "non_work_hours",
            "value": {"start": "22:00", "end": "05:00"},
        },
    ]

    # A window never seen: one hour apart, across midnight where needed.
    await call(hass, "time", SERVICE_SET_TIME, "time.luba_setting_eco_window_start", time="23:30")
    await call(hass, "time", SERVICE_SET_TIME, "time.luba_setting_eco_window_end", time="00:15")
    assert sent(fake_api)[-2:] == [
        {"kind": "set_setting", "key": "eco_window", "value": {"start": "23:30", "end": "00:30"}},
        {"kind": "set_setting", "key": "eco_window", "value": {"start": "23:30", "end": "00:15"}},
    ]
    await wait_for(
        hass, lambda: state_of(hass, "time.luba_setting_eco_window_end").state == "00:15:00"
    )

    count = len(fake_api.commands)
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "time", SERVICE_SET_TIME, QUIET_END, time="22:00")
    assert err.value.translation_key == "time_window_empty"
    assert err.value.translation_placeholders == {"time": "22:00"}
    assert len(fake_api.commands) == count

    # Without any window seen, the end alone gives a start one hour earlier.
    eco_end = hass.data["entity_components"]["time"].get_entity("time.luba_setting_eco_window_end")
    assert eco_end is not None
    eco_end._last_window = None
    fake_api.states[MOWER_ID]["settings"].pop("eco_window")
    await fake_api.push_state(MOWER_ID, fake_api.states[MOWER_ID])
    await wait_for(
        hass, lambda: state_of(hass, "time.luba_setting_eco_window_end").state == STATE_UNKNOWN
    )
    await call(hass, "time", SERVICE_SET_TIME, "time.luba_setting_eco_window_end", time="00:15")
    assert sent(fake_api)[-1] == {
        "kind": "set_setting",
        "key": "eco_window",
        "value": {"start": "23:15", "end": "00:15"},
    }


@pytest.mark.usefixtures("settings_mower", "loaded_entry")
async def test_refusal_names_the_reason(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """A setting the controller refuses raises with MowDeck's reason; the value stays."""
    fake_api.command_results["set_setting"] = ("rejected", "voice_volume: 77 is not on the grid")
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "number", SERVICE_SET_VALUE, VOLUME, **{ATTR_VALUE: 77})
    assert err.value.translation_key == "command_refused"
    assert err.value.translation_placeholders == {
        "command": "set_setting",
        "status": "rejected",
        "reason": "voice_volume: 77 is not on the grid",
    }
    assert state_of(hass, VOLUME).state == "60"

    fake_api.command_results["set_setting"] = ("failed", None)
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "switch", SERVICE_TURN_OFF, RAIN_DETECTION)
    assert err.value.translation_placeholders is not None
    assert err.value.translation_placeholders["reason"] == "-"

    fake_api.command_results["set_setting"] = ("accepted", None)
    await call(hass, "switch", SERVICE_TURN_OFF, RAIN_DETECTION)
    await wait_for(hass, lambda: state_of(hass, RAIN_DETECTION).state == STATE_OFF)


@pytest.mark.usefixtures("settings_mower", "fast_results")
async def test_no_result_counts_as_sent(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """Another command's verdict is ignored; without a verdict in time the change counts as
    sent."""
    client = loaded_entry.runtime_data.client
    original = client.send_command
    signal = command_signal(loaded_entry.entry_id, MOWER_ID)

    async def _send(mower_id: str, command: Any, **kwargs: Any) -> dict[str, Any]:
        accepted = await original(mower_id, command, **kwargs)
        async_dispatcher_send(hass, signal, {"command_id": "another", "status": "rejected"})
        return accepted

    with patch.object(client, "send_command", _send):
        await call(hass, "number", SERVICE_SET_VALUE, VOLUME, **{ATTR_VALUE: 80})
    assert sent(fake_api)[-1] == {"kind": "set_setting", "key": "voice_volume", "value": 80}


@pytest.mark.usefixtures("settings_mower", "loaded_entry")
async def test_without_realtime_no_wait(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """While the socket is down the command is not waited for (no result could arrive)."""
    entry = hass.config_entries.async_entries("mowdeck")[0]
    entry.runtime_data.coordinator.realtime_connected = False
    fake_api.command_results["set_setting"] = ("rejected", "would be refused")
    await call(hass, "switch", SERVICE_TURN_OFF, NIGHT_LIGHT)
    assert sent(fake_api)[-1] == {"kind": "set_setting", "key": "night_light", "value": False}


@pytest.mark.usefixtures("settings_mower")
async def test_permissions_and_declaration_changes(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    loaded_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A 403 names fleet.manage; without can_manage_mowers, or while the mower does not declare
    a setting, its entities stay registered and are unavailable. A declaration that comes back
    with other bounds or options brings them along."""
    fake_api.forbid_commands = True
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "switch", SERVICE_TURN_ON, NIGHT_LIGHT)
    assert err.value.translation_key == "command_forbidden"
    assert err.value.translation_placeholders == {
        "command": "set_setting",
        "permission": "fleet.manage",
    }
    fake_api.forbid_commands = False
    entity_registry.async_update_entity(NIGHT_LIGHT, name="Night lamp")

    fake_api.capabilities[MOWER_ID]["settings"] = [
        s for s in SETTINGS if s["type"] not in ("bool", "enum")
    ]
    await refresh_static(loaded_entry)
    await hass.async_block_till_done()
    for entity_id in (NIGHT_LIGHT, TURN_STYLE):
        assert entity_registry.async_get(entity_id) is not None, entity_id
        assert state_of(hass, entity_id).state == STATE_UNAVAILABLE, entity_id
    assert state_of(hass, VOLUME).state == "60"

    changed = [{**s, "max": 80, "step": 10} if s["key"] == "voice_volume" else s for s in SETTINGS]
    changed = [
        {**s, "options": ["in_place", "multi_point", "zero_turn"]}
        if s["key"] == "turn_style"
        else s
        for s in changed
    ]
    fake_api.capabilities[MOWER_ID]["settings"] = changed
    await refresh_static(loaded_entry)
    await hass.async_block_till_done()
    assert state_of(hass, NIGHT_LIGHT).state == STATE_OFF
    assert state_of(hass, NIGHT_LIGHT).attributes["friendly_name"].endswith("Night lamp")
    assert state_of(hass, TURN_STYLE).attributes["options"] == [
        "in_place",
        "multi_point",
        "zero_turn",
    ]
    assert state_of(hass, VOLUME).attributes["max"] == 80
    assert state_of(hass, VOLUME).attributes["step"] == 10

    fake_api.relations = {"can_view", "can_control", "can_drive"}
    await refresh_static(loaded_entry)
    await hass.async_block_till_done()
    for entity_id in (VOLUME, TURN_STYLE, QUIET_START, QUIET_END, QUIET_OFF):
        assert entity_registry.async_get(entity_id) is not None, entity_id
        assert state_of(hass, entity_id).state == STATE_UNAVAILABLE, entity_id
    # The rain sensor only reads: it stays available.
    assert hass.states.get(RAIN) is not None
    assert state_of(hass, RAIN).state != STATE_UNAVAILABLE


@pytest.mark.usefixtures("settings_mower", "loaded_entry")
async def test_undeclared_setting_is_unavailable(hass: HomeAssistant) -> None:
    """Between a declaration change and the entity sync the entity is unavailable, and values
    of the wrong type read as unknown."""
    coordinator = hass.config_entries.async_entries("mowdeck")[0].runtime_data.coordinator
    coordinator.data.states[MOWER_ID] = {
        **coordinator.data.states[MOWER_ID],
        "settings": {
            "voice_volume": True,
            "turn_style": "sideways",
            "rain_detection": "yes",
            "non_work_hours": {"start": "25:00", "end": "06:00"},
        },
    }
    coordinator.data.capabilities[MOWER_ID] = {
        **coordinator.data.capabilities[MOWER_ID],
        "settings": [s for s in SETTINGS if s["key"] != "night_light"],
    }
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert state_of(hass, VOLUME).state == STATE_UNKNOWN
    assert state_of(hass, TURN_STYLE).state == STATE_UNKNOWN
    assert state_of(hass, RAIN_DETECTION).state == STATE_UNKNOWN
    assert state_of(hass, QUIET_START).state == STATE_UNKNOWN


async def test_rain_sensor(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """Created once the mower reports its rain sensor (or declares rain detection); on with
    rain, unavailable while the mower does not report it."""
    assert hass.states.get(RAIN) is None
    await fake_api.push_state(MOWER_ID, mower_state(rain_detected=True))
    await wait_for(hass, lambda: hass.states.get(RAIN) is not None)
    assert state_of(hass, RAIN).state == STATE_ON
    assert state_of(hass, RAIN).attributes["device_class"] == "moisture"
    await fake_api.push_state(MOWER_ID, mower_state(rain_detected=False))
    await wait_for(hass, lambda: state_of(hass, RAIN).state == STATE_OFF)
    # Rain detection switched off: the mower stops reporting the sensor.
    await fake_api.push_state(MOWER_ID, mower_state(rain_detected=None))
    await wait_for(hass, lambda: state_of(hass, RAIN).state == STATE_UNAVAILABLE)


@pytest.mark.usefixtures("loaded_entry")
async def test_rain_sensor_from_the_declaration(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """A mower that declares rain detection gets the sensor before it reports it."""
    fake_api.capabilities[MOWER_ID]["settings"] = SETTINGS[:1]
    entry = hass.config_entries.async_entries("mowdeck")[0]
    await refresh_static(entry)
    await hass.async_block_till_done()
    assert state_of(hass, RAIN).state == STATE_UNAVAILABLE


def test_clock_helpers() -> None:
    """HH:MM parsing, formatting and shifting across midnight."""
    clock = parse_clock("23:30")
    assert clock is not None
    assert format_clock(clock) == "23:30"
    assert format_clock(shift_clock(clock, timedelta(hours=1))) == "00:30"
    assert parse_clock("24:00") is None
    assert parse_clock(None) is None
