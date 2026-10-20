"""Commands from the lawn mower, buttons, numbers, select and the entity services."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.components.lawn_mower import (
    SERVICE_DOCK,
    SERVICE_PAUSE,
    SERVICE_START_MOWING,
)
from homeassistant.components.number import ATTR_VALUE, SERVICE_SET_VALUE
from homeassistant.components.select import ATTR_OPTION, SERVICE_SELECT_OPTION
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mowdeck.const import DOMAIN
from custom_components.mowdeck.coordinator import area_display_name

from .conftest import wait_for
from .fake_mowdeck import MOWER_ID, FakeMowDeck, mower_state

MOWER = "lawn_mower.luba"


def sent(fake_api: FakeMowDeck) -> list[dict[str, Any]]:
    """The command bodies the fake Fleet received."""
    return [c["command"] for c in fake_api.commands]


async def call(hass: HomeAssistant, domain: str, service: str, entity_id: str, **data: Any) -> None:
    """Call a service on one entity and wait."""
    await hass.services.async_call(
        domain, service, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True
    )


@pytest.mark.usefixtures("loaded_entry")
async def test_start_pause_dock(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """start_mowing mows every area, pause and dock send their commands."""
    await call(hass, "lawn_mower", SERVICE_START_MOWING, MOWER)
    await call(hass, "lawn_mower", SERVICE_PAUSE, MOWER)
    await call(hass, "lawn_mower", SERVICE_DOCK, MOWER)
    assert sent(fake_api) == [
        {"kind": "start_mow", "area_ids": ["zone-front", "zone-back"]},
        {"kind": "pause_mow"},
        {"kind": "return_to_dock"},
    ]
    assert fake_api.commands[0]["mower_id"] == MOWER_ID


@pytest.mark.usefixtures("loaded_entry")
async def test_start_uses_selected_area(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """The area select narrows start_mowing to one area."""
    await call(hass, "select", SERVICE_SELECT_OPTION, "select.luba_area", **{ATTR_OPTION: "Back"})
    state = hass.states.get("select.luba_area")
    assert state is not None
    assert state.state == "Back"
    await call(hass, "lawn_mower", SERVICE_START_MOWING, MOWER)
    assert sent(fake_api) == [{"kind": "start_mow", "area_ids": ["zone-back"]}]


@pytest.mark.usefixtures("loaded_entry")
async def test_start_resumes_when_paused(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """start_mowing on a paused mower resumes the job."""
    await fake_api.push_state(MOWER_ID, mower_state(activity="paused", charging=False))
    await wait_for(hass, lambda: hass.states.get(MOWER).state == "paused")  # type: ignore[union-attr]
    await call(hass, "lawn_mower", SERVICE_START_MOWING, MOWER)
    assert sent(fake_api) == [{"kind": "resume_mow"}]


@pytest.mark.usefixtures("loaded_entry")
async def test_buttons(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """Emergency stop and return to dock buttons."""
    await call(hass, "button", SERVICE_PRESS, "button.luba_emergency_stop")
    await call(hass, "button", SERVICE_PRESS, "button.luba_return_to_dock")
    assert sent(fake_api) == [{"kind": "emergency_stop"}, {"kind": "return_to_dock"}]


@pytest.mark.usefixtures("loaded_entry")
async def test_numbers(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """Blade height and speed numbers send their commands."""
    await call(hass, "number", SERVICE_SET_VALUE, "number.luba_blade_height", **{ATTR_VALUE: 45})
    await call(hass, "number", SERVICE_SET_VALUE, "number.luba_mowing_speed", **{ATTR_VALUE: 0.6})
    assert sent(fake_api) == [
        {"kind": "set_blade_height", "mm": 45},
        {"kind": "set_speed", "mps": 0.6},
    ]


@pytest.mark.usefixtures("loaded_entry")
async def test_number_out_of_capability_range(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """Values outside the capabilities' range are refused by Home Assistant."""
    with pytest.raises(Exception, match=r"out of range|outside"):
        await call(
            hass, "number", SERVICE_SET_VALUE, "number.luba_blade_height", **{ATTR_VALUE: 90}
        )
    assert fake_api.commands == []


@pytest.mark.usefixtures("loaded_entry")
async def test_entity_services(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """mowdeck.start_mow with areas and pattern, emergency_stop, blade height and speed."""
    await call(
        hass,
        DOMAIN,
        "start_mow",
        MOWER,
        area_ids=["zone-back", "zone-front"],
        pattern={"name": "stripes", "angle_deg": 45},
    )
    await call(hass, DOMAIN, "emergency_stop", MOWER)
    await call(hass, DOMAIN, "set_blade_height", MOWER, mm=60)
    await call(hass, DOMAIN, "set_speed", MOWER, mps=0.8)
    await call(hass, DOMAIN, "start_mow", MOWER)
    assert sent(fake_api) == [
        {
            "kind": "start_mow",
            "area_ids": ["zone-back", "zone-front"],
            "pattern": {"name": "stripes", "angle_deg": 45},
        },
        {"kind": "emergency_stop"},
        {"kind": "set_blade_height", "mm": 60},
        {"kind": "set_speed", "mps": 0.8},
        {"kind": "start_mow", "area_ids": ["zone-front", "zone-back"]},
    ]


@pytest.mark.usefixtures("loaded_entry")
async def test_forbidden_command(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """A 403 from Fleet becomes a translated Home Assistant error."""
    fake_api.forbid_commands = True
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "lawn_mower", SERVICE_DOCK, MOWER)
    assert err.value.translation_key == "command_forbidden"


async def test_failed_command(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """A vanished mower (404) becomes command_failed."""
    fake_api.mowers.clear()
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "lawn_mower", SERVICE_PAUSE, MOWER)
    assert err.value.translation_key == "command_failed"


@pytest.mark.usefixtures("loaded_entry")
async def test_error_and_offline_states(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """Error activity, error code and offline map to the right entity states."""
    await fake_api.push_state(
        MOWER_ID,
        mower_state(activity="error", error_code="E42", error_message="stuck", charging=False),
    )
    await wait_for(hass, lambda: hass.states.get(MOWER).state == "error")  # type: ignore[union-attr]
    assert hass.states.get("binary_sensor.luba_error").state == "on"  # type: ignore[union-attr]
    code = hass.states.get("sensor.luba_error_code")
    assert code is not None
    assert code.state == "E42"
    assert code.attributes["error_message"] == "stuck"

    await fake_api.push_state(MOWER_ID, mower_state(activity="offline", position=None))
    await wait_for(
        hass,
        lambda: hass.states.get("binary_sensor.luba_online").state == "off",  # type: ignore[union-attr]
    )
    assert hass.states.get(MOWER).state == "unknown"  # type: ignore[union-attr]
    assert hass.states.get("device_tracker.luba_position").state == "unknown"  # type: ignore[union-attr]


@pytest.mark.usefixtures("loaded_entry")
async def test_light_switch(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """The light switch shows the reported state and sends set_light."""
    state = hass.states.get("switch.luba_camera_light")
    assert state is not None
    assert state.state == "off"
    await call(hass, "switch", "turn_on", "switch.luba_camera_light")
    await call(hass, "switch", "turn_off", "switch.luba_camera_light")
    assert sent(fake_api) == [{"kind": "set_light", "on": True}, {"kind": "set_light", "on": False}]
    await fake_api.push_state(MOWER_ID, mower_state(light_on=True))
    await wait_for(hass, lambda: hass.states.get("switch.luba_camera_light").state == "on")  # type: ignore[union-attr]


async def test_area_select_shows_mowdeck_names(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """Custom names from Fleet win; unnamed or hash-named areas read "Area N" in map order."""
    square = [[0, 0], [10, 0], [10, 10]]
    fake_api.maps[MOWER_ID] = {
        "reference": {"origin_lat": 48.137, "origin_lon": 11.575},
        "areas": [
            {"id": "zone-front", "name": "Front", "boundary": square},
            {"id": "4525245168006937683", "name": "4525245168006937683", "boundary": square},
            {"id": "zone-back", "name": None, "boundary": square},
        ],
        "revision": "rev-2",
    }
    fake_api.mowers[0]["area_names"] = {"zone-front": "Vorgarten"}
    coordinator = loaded_entry.runtime_data.coordinator
    coordinator._static_fetched_at.clear()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get("select.luba_area")
    assert state is not None
    assert state.attributes["options"] == ["all", "Vorgarten", "Area 2", "Area 3"]
    await call(hass, "select", SERVICE_SELECT_OPTION, "select.luba_area", **{ATTR_OPTION: "Area 3"})
    await call(hass, "lawn_mower", SERVICE_START_MOWING, MOWER)
    assert sent(fake_api) == [{"kind": "start_mow", "area_ids": ["zone-back"]}]

    hass.config.language = "de"
    fake_api.mowers[0]["area_names"] = {}
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get("select.luba_area")
    assert state is not None
    assert state.attributes["options"] == ["all", "Front", "Bereich 2", "Bereich 3"]
    # The selection follows the area id, not the label.
    assert state.state == "Bereich 3"


def test_area_display_name_rule() -> None:
    """Same rule as mowdeck_contracts.v1.area_display_name."""
    custom = {"a": "  Hof "}
    assert area_display_name({"id": "a", "name": "Front"}, 0, custom) == "Hof"
    assert area_display_name({"id": "b", "name": " Beet "}, 1, custom) == "Beet"
    assert area_display_name({"id": "c", "name": "12345678"}, 2, {}) == "Area 3"
    assert area_display_name({"id": "c", "name": "1234567"}, 2, {}) == "1234567"
    assert area_display_name({"id": "d"}, 3, {"d": " "}, "de-AT") == "Bereich 4"
    assert area_display_name({"id": "d"}, 0, {}, "fr") == "Area 1"
