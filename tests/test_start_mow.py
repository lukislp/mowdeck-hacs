"""mowdeck.start_mow with parameters, mowdeck.get_start_options and the job option defaults."""

from __future__ import annotations

from typing import Any

import probatio as vol
import pytest
from homeassistant.components.number import ATTR_VALUE, SERVICE_SET_VALUE
from homeassistant.components.select import ATTR_OPTION, SERVICE_SELECT_OPTION
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mowdeck.const import DOMAIN
from custom_components.mowdeck.start_options import describe, problem, start_parameters

from .fake_mowdeck import MOWER_ID, FakeMowDeck, mower_state

LUBA = "lawn_mower.luba"
YUKA = "lawn_mower.yuka"
YUKA_ID = "1b2c3d4e-5f60-4718-8a9b-0c1d2e3f4a5b"
DETECTION = "select.luba_obstacle_detection"
ORDER = "select.luba_path_order"
OBSTACLE_LAPS = "number.luba_laps_around_no_go_zones"
PATTERN = "select.luba_mowing_pattern"

# What MowDeck declares for a LUBA 2 AWD X (mowdeck-control-mammotion's start_options_of).
AWD_X_START_OPTIONS = {
    "obstacle_detection": ["off", "standard", "sensitive"],
    "obstacle_laps_range": [0, 4],
    "orders": ["border_first", "grid_first"],
    "start_progress_range_pct": [0, 99],
}


def sent(fake_api: FakeMowDeck) -> list[dict[str, Any]]:
    """The command bodies the fake Fleet received."""
    return [c["command"] for c in fake_api.commands]


def state_of(hass: HomeAssistant, entity_id: str) -> Any:
    """The state object of an entity (asserting it exists)."""
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


async def start_mow(hass: HomeAssistant, entity_id: str | list[str], **data: Any) -> Any:
    """Call mowdeck.start_mow and answer its response."""
    return await hass.services.async_call(
        DOMAIN,
        "start_mow",
        {ATTR_ENTITY_ID: entity_id, **data},
        blocking=True,
        return_response=True,
    )


async def call(hass: HomeAssistant, domain: str, service: str, entity_id: str, **data: Any) -> None:
    """Call a service on one entity and wait."""
    await hass.services.async_call(
        domain, service, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True
    )
    await hass.async_block_till_done()


async def refresh_capabilities(entry: MockConfigEntry, hass: HomeAssistant) -> None:
    """Fetch capabilities, maps and permissions again."""
    coordinator = entry.runtime_data.coordinator
    coordinator._static_fetched_at.clear()
    await coordinator.async_refresh()
    await hass.async_block_till_done()


@pytest.fixture
def awd_x(fake_api: FakeMowDeck) -> FakeMowDeck:
    """The Luba declares native patterns, a path spacing range and the AWD X job options."""
    fake_api.capabilities[MOWER_ID].update(
        {
            "native_patterns": ["stripes", "diagonal", "edge_only"],
            "path_spacing_range_mm": [200, 350],
            "min_speed_mps": 0.2,
            "start_options": AWD_X_START_OPTIONS,
        }
    )
    return fake_api


@pytest.fixture
def yuka(awd_x: FakeMowDeck) -> FakeMowDeck:
    """A second mower, Yuka, with stripes only, no spacing range and no job options; its map
    has its own Front and a Side area."""
    fake_api = awd_x
    fake_api.mowers.append(dict(fake_api.mowers[0], id=YUKA_ID, name="Yuka", serial="SIM-0002"))
    fake_api.states[YUKA_ID] = mower_state(mower_id=YUKA_ID)
    fake_api.capabilities[YUKA_ID] = {
        "model": "Yuka (simulated)",
        "blade_height_range_mm": [30, 60],
        "max_speed_mps": 0.6,
        "native_patterns": ["stripes"],
        "connection_kinds": ["simulated"],
        "rtk_sources": ["base_station"],
    }
    fake_api.maps[YUKA_ID] = {
        "reference": {"origin_lat": 48.137, "origin_lon": 11.575},
        "areas": [
            {"id": "yuka-front", "name": "Front", "boundary": []},
            {"id": "yuka-side", "name": "Side", "boundary": []},
        ],
        "revision": "rev-1",
    }
    return fake_api


# The parameters of one mower


def test_start_parameters_follow_the_capabilities() -> None:
    """Options and ranges come from the capabilities; undeclared parameters are left out."""
    params = start_parameters(
        {
            "native_patterns": ["checkerboard", "stripes", "spiral"],
            "path_spacing_range_mm": [100, 50],
            "blade_height_range_mm": [5, 200],
            "max_speed_mps": 3,
            "min_speed_mps": True,
            "start_options": {
                "obstacle_detection": ["standard", "unknown"],
                "obstacle_laps_range": [1, 9],
                "orders": "border_first",
                "start_progress_range_pct": [50, 10],
            },
        }
    )
    assert params == {
        "pattern": {"options": ["stripes", "checkerboard"]},
        "angle_deg": {"min": 0, "max": 180, "step": 1},
        "border_laps": {"min": 0, "max": 4, "step": 1},
        "blade_height_mm": {"min": 10, "max": 120, "step": 1},
        "speed_mps": {"min": 0.1, "max": 2.0, "step": 0.01},
        "obstacle_detection": {"options": ["standard"]},
        "obstacle_laps": {"min": 1, "max": 4, "step": 1},
    }
    assert start_parameters({"start_options": "none"}) == {
        "blade_height_mm": {"min": 10, "max": 120, "step": 1},
        "speed_mps": {"min": 0.1, "max": 2.0, "step": 0.01},
    }
    assert problem(None, 1) == "unsupported"
    assert problem({"options": ["a"]}, "b") == "invalid"
    assert problem({"min": 1, "max": 2}, 3) == "invalid"
    assert describe(None) == "-"
    assert describe({"min": 0.2, "max": 1.0}) == "0.2 - 1"


# Starting with parameters


@pytest.mark.usefixtures("awd_x", "loaded_entry")
async def test_start_with_every_parameter(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """Blade height and speed go first, then the start with the pattern and job options; the
    answer names every command."""
    response = await start_mow(
        hass,
        LUBA,
        areas=["front"],
        pattern="diagonal",
        angle_deg=30,
        border_laps=2,
        spacing_cm=28,
        blade_height_mm=25,
        speed_mps=0.456,
        obstacle_detection="sensitive",
        obstacle_laps=2,
        order="border_first",
        start_progress_pct=10,
    )
    pattern = {
        "name": "diagonal",
        "angle_deg": 30.0,
        "border_laps": 2,
        "spacing_m": 0.28,
        "obstacle_detection": "sensitive",
        "obstacle_laps": 2,
        "order": "border_first",
        "start_progress_pct": 10,
    }
    assert sent(fake_api) == [
        {"kind": "set_blade_height", "mm": 25},
        {"kind": "set_speed", "mps": 0.46},
        {"kind": "start_mow", "area_ids": ["zone-front"], "pattern": pattern},
    ]
    answer = response[LUBA]
    assert [c["kind"] for c in answer["commands"]] == ["set_blade_height", "set_speed", "start_mow"]
    assert answer["command_id"] == answer["commands"][-1]["command_id"]
    assert answer["area_ids"] == ["zone-front"]
    assert answer["pattern"] == pattern
    assert answer["ignored"] == []


@pytest.mark.usefixtures("awd_x", "loaded_entry")
async def test_defaults_fill_what_is_left_out(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """The job option defaults are saved through mow-defaults; a start takes the saved
    defaults and puts the given parameters on top (MowDeck's start does not fill them in)."""
    assert state_of(hass, DETECTION).attributes["options"] == ["off", "standard", "sensitive"]
    assert state_of(hass, DETECTION).state == "unknown"
    assert state_of(hass, ORDER).attributes["options"] == ["border_first", "grid_first"]
    laps = state_of(hass, OBSTACLE_LAPS)
    assert (laps.attributes["min"], laps.attributes["max"], laps.state) == (0, 4, "unknown")

    await call(hass, "select", SERVICE_SELECT_OPTION, PATTERN, **{ATTR_OPTION: "edge_only"})
    await call(hass, "select", SERVICE_SELECT_OPTION, DETECTION, **{ATTR_OPTION: "standard"})
    await call(hass, "select", SERVICE_SELECT_OPTION, ORDER, **{ATTR_OPTION: "grid_first"})
    await call(hass, "number", SERVICE_SET_VALUE, OBSTACLE_LAPS, **{ATTR_VALUE: 3})
    saved = fake_api.mowers[0]["mow_defaults"]["pattern"]
    assert saved["obstacle_detection"] == "standard"
    assert saved["order"] == "grid_first"
    assert saved["obstacle_laps"] == 3
    assert state_of(hass, DETECTION).state == "standard"
    assert state_of(hass, ORDER).state == "grid_first"
    assert state_of(hass, OBSTACLE_LAPS).state == "3.0"

    await start_mow(hass, LUBA, obstacle_detection="sensitive")
    assert sent(fake_api)[-1]["pattern"] == {
        "name": "edge_only",
        "angle_deg": 0.0,
        "border_laps": 1,
        "obstacle_detection": "sensitive",
        "obstacle_laps": 3,
        "order": "grid_first",
    }
    # Without parameters the saved defaults go as they are.
    await start_mow(hass, LUBA)
    assert sent(fake_api)[-1]["pattern"]["obstacle_detection"] == "standard"


@pytest.mark.usefixtures("awd_x", "loaded_entry")
async def test_no_defaults_no_parameters_no_pattern(
    hass: HomeAssistant, fake_api: FakeMowDeck
) -> None:
    """Without saved defaults and parameters the mower's own settings apply (no pattern); a
    parameter alone goes on MowDeck's defaults (stripes, 0 degrees, one border lap). A start
    progress of 0 is the beginning and not sent."""
    await start_mow(hass, LUBA, start_progress_pct=0)
    assert sent(fake_api)[-1] == {"kind": "start_mow", "area_ids": ["zone-front", "zone-back"]}
    await start_mow(hass, LUBA, order="grid_first")
    assert sent(fake_api)[-1]["pattern"] == {
        "name": "stripes",
        "angle_deg": 0.0,
        "border_laps": 1,
        "order": "grid_first",
    }


@pytest.mark.usefixtures("awd_x", "loaded_entry")
async def test_older_fields_still_work(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """area_ids and a pattern object (the older form) work as before; given parameters go on
    top of the object."""
    await hass.services.async_call(
        DOMAIN,
        "start_mow",
        {ATTR_ENTITY_ID: LUBA, "area_ids": ["zone-back"], "pattern": {"name": "stripes"}},
        blocking=True,
    )
    assert sent(fake_api)[-1] == {
        "kind": "start_mow",
        "area_ids": ["zone-back"],
        "pattern": {"name": "stripes"},
    }
    await start_mow(
        hass, LUBA, area_ids="zone-back", pattern={"name": "stripes", "angle_deg": 45}, angle_deg=10
    )
    assert sent(fake_api)[-1]["pattern"] == {"name": "stripes", "angle_deg": 10.0}


# Validation against the mower


@pytest.mark.usefixtures("loaded_entry")
async def test_parameters_the_mower_lacks_fail(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """A parameter the mower does not offer or a value outside its options or range fails
    with the mower, the parameter and what it allows; nothing is sent."""
    with pytest.raises(ServiceValidationError) as err:
        await start_mow(hass, LUBA, obstacle_detection="sensitive")
    assert err.value.translation_key == "start_parameter_unsupported"
    assert err.value.translation_placeholders == {
        "mower": "Luba",
        "parameter": "obstacle_detection",
    }
    assert "Luba does not support obstacle_detection" in str(err.value)

    with pytest.raises(ServiceValidationError) as err:
        await start_mow(hass, LUBA, blade_height_mm=80, pattern="stripes")
    assert err.value.translation_key == "start_parameter_invalid"
    assert err.value.translation_placeholders == {
        "mower": "Luba",
        "parameter": "blade_height_mm",
        "value": "80",
        "allowed": "25 - 70",
    }
    assert str(err.value) == "blade_height_mm 80 does not fit Luba. Allowed: 25 - 70"

    with pytest.raises(ServiceValidationError) as err:
        await start_mow(hass, LUBA, pattern="diagonal")
    assert (err.value.translation_placeholders or {})["allowed"] == "stripes"

    for parameter, value in (
        ("spacing_cm", 30),
        ("start_progress_pct", 50),
        ("order", "grid_first"),
    ):
        with pytest.raises(ServiceValidationError) as err:
            await start_mow(hass, LUBA, **{parameter: value})
        assert err.value.translation_key == "start_parameter_unsupported", parameter
    assert sent(fake_api) == []


@pytest.mark.usefixtures("loaded_entry")
async def test_schema_limits(hass: HomeAssistant) -> None:
    """Values no mower takes fail in the schema already."""
    for data in (
        {"obstacle_detection": "eager"},
        {"order": "random"},
        {"pattern": "spiral"},
        {"start_progress_pct": 100},
        {"blade_height_mm": 5},
    ):
        with pytest.raises(vol.Invalid):
            await start_mow(hass, LUBA, **data)


@pytest.mark.usefixtures("loaded_entry")
async def test_ignore_unsupported(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """With ignore_unsupported what does not fit is left out and reported."""
    response = await start_mow(
        hass,
        LUBA,
        obstacle_detection="sensitive",
        blade_height_mm=80,
        speed_mps=0.5,
        areas=["Front", "Pond"],
        ignore_unsupported=True,
    )
    answer = response[LUBA]
    assert sent(fake_api) == [
        {"kind": "set_speed", "mps": 0.5},
        {"kind": "start_mow", "area_ids": ["zone-front"]},
    ]
    assert answer["ignored"] == [
        {
            "parameter": "blade_height_mm",
            "value": 80,
            "reason": "invalid",
            "allowed": {"min": 25, "max": 70, "step": 1},
            "source": "action",
        },
        {
            "parameter": "obstacle_detection",
            "value": "sensitive",
            "reason": "unsupported",
            "allowed": None,
            "source": "action",
        },
        {"parameter": "areas", "value": "Pond", "reason": "invalid", "source": "action"},
    ]


async def test_defaults_the_mower_no_longer_offers(
    hass: HomeAssistant,
    awd_x: FakeMowDeck,
    loaded_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """When the mower stops declaring the job options their entities stay, unavailable, and a
    start leaves the saved options out (reported with source defaults)."""
    await call(hass, "select", SERVICE_SELECT_OPTION, DETECTION, **{ATTR_OPTION: "sensitive"})
    await call(hass, "number", SERVICE_SET_VALUE, OBSTACLE_LAPS, **{ATTR_VALUE: 2})
    del awd_x.capabilities[MOWER_ID]["start_options"]
    await refresh_capabilities(loaded_entry, hass)
    for entity_id in (DETECTION, ORDER, OBSTACLE_LAPS):
        assert entity_registry.async_get(entity_id) is not None, entity_id
        assert state_of(hass, entity_id).state == "unavailable", entity_id

    response = await start_mow(hass, LUBA)
    assert sent(awd_x)[-1]["pattern"] == {"name": "stripes", "angle_deg": 0.0, "border_laps": 1}
    assert [(i["parameter"], i["source"]) for i in response[LUBA]["ignored"]] == [
        ("obstacle_detection", "defaults"),
        ("obstacle_laps", "defaults"),
    ]

    awd_x.capabilities[MOWER_ID]["start_options"] = AWD_X_START_OPTIONS
    await refresh_capabilities(loaded_entry, hass)
    assert state_of(hass, DETECTION).state == "sensitive"


@pytest.mark.usefixtures("loaded_entry")
async def test_no_job_option_entities_without_declaration(hass: HomeAssistant) -> None:
    """A mower without start_options gets no job option entities."""
    for entity_id in (DETECTION, ORDER, OBSTACLE_LAPS):
        assert hass.states.get(entity_id) is None, entity_id


# Areas


@pytest.mark.usefixtures("yuka", "loaded_entry")
async def test_areas_by_switch_name_and_id(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """Areas take area switches, names and ids; each mower takes its own switches and skips
    the other mower's."""
    response = await start_mow(
        hass,
        [LUBA, YUKA],
        areas=["switch.luba_mow_back", "switch.yuka_mow_side", "zone-front", "yuka-front"],
        ignore_unsupported=True,
    )
    started = {c["mower_id"]: c["command"]["area_ids"] for c in fake_api.commands}
    assert started == {MOWER_ID: ["zone-back", "zone-front"], YUKA_ID: ["yuka-side", "yuka-front"]}
    assert response[LUBA]["ignored"] == [
        {"parameter": "areas", "value": "yuka-front", "reason": "invalid", "source": "action"}
    ]

    with pytest.raises(ServiceValidationError) as err:
        await start_mow(hass, LUBA, areas=["Pond"])
    assert err.value.translation_key == "start_area_unknown"
    assert err.value.translation_placeholders == {
        "mower": "Luba",
        "area": "Pond",
        "allowed": "Front, Back",
    }
    with pytest.raises(ServiceValidationError) as err:
        await start_mow(hass, LUBA, areas=["switch.luba_camera_light"])
    assert err.value.translation_key == "start_area_unknown"
    with pytest.raises(ServiceValidationError) as err:
        await start_mow(hass, LUBA, areas=["switch.yuka_mow_side"])
    assert err.value.translation_key == "start_no_area"
    assert err.value.translation_placeholders == {"mower": "Luba"}


# Several mowers


@pytest.mark.usefixtures("yuka", "loaded_entry")
async def test_several_mowers_each_checked(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """Each targeted mower checks the parameters against its own capabilities; the error
    names the mower that does not fit. With ignore_unsupported each leaves out its own."""
    with pytest.raises(ServiceValidationError) as err:
        await start_mow(hass, [LUBA, YUKA], areas=["Front"], obstacle_detection="sensitive")
    assert err.value.translation_placeholders == {
        "mower": "Yuka",
        "parameter": "obstacle_detection",
    }
    # Home Assistant runs every targeted mower: Luba, which fits, started; Yuka sent nothing.
    assert [c["mower_id"] for c in fake_api.commands] == [MOWER_ID]
    with pytest.raises(ServiceValidationError) as err:
        await start_mow(hass, YUKA, blade_height_mm=65)
    assert (err.value.translation_placeholders or {})["allowed"] == "30 - 60"

    fake_api.commands.clear()
    response = await start_mow(
        hass,
        [LUBA, YUKA],
        areas=["Front"],
        pattern="diagonal",
        obstacle_detection="sensitive",
        ignore_unsupported=True,
    )
    starts = {c["mower_id"]: c["command"] for c in fake_api.commands}
    assert starts[MOWER_ID]["area_ids"] == ["zone-front"]
    assert starts[MOWER_ID]["pattern"]["obstacle_detection"] == "sensitive"
    assert starts[YUKA_ID]["area_ids"] == ["yuka-front"]
    # Nothing left for Yuka and no saved defaults: its own settings apply.
    assert "pattern" not in starts[YUKA_ID]
    assert [i["parameter"] for i in response[YUKA]["ignored"]] == [
        "pattern",
        "obstacle_detection",
    ]
    assert response[LUBA]["ignored"] == []


@pytest.mark.usefixtures("yuka", "loaded_entry")
async def test_get_start_options(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """mowdeck.get_start_options answers per mower what start_mow accepts."""
    fake_api.states[MOWER_ID] = mower_state(blade_height_mm=40)
    response: Any = await hass.services.async_call(
        DOMAIN,
        "get_start_options",
        {ATTR_ENTITY_ID: [LUBA, YUKA]},
        blocking=True,
        return_response=True,
    )
    luba, yuka = response[LUBA], response[YUKA]
    assert luba["name"] == "Luba"
    assert luba["model"] == "Luba 2 AWD (simulated)"
    assert luba["unsupported"] == []
    assert luba["parameters"]["obstacle_detection"] == {"options": ["off", "standard", "sensitive"]}
    assert luba["parameters"]["spacing_cm"] == {"min": 20.0, "max": 35.0, "step": 1}
    assert luba["parameters"]["speed_mps"] == {"min": 0.2, "max": 1.2, "step": 0.01}
    assert luba["areas"] == [
        {"id": "zone-front", "name": "Front"},
        {"id": "zone-back", "name": "Back"},
    ]
    assert luba["defaults"] == {
        "saved": False,
        "pattern": {"name": "stripes", "angle_deg": 0.0, "border_laps": 1, "spacing_m": None},
    }
    assert set(luba["current"]) == {"blade_height_mm", "speed_mps"}
    assert yuka["model"] == "Yuka (simulated)"
    assert yuka["parameters"]["pattern"] == {"options": ["stripes"]}
    assert yuka["parameters"]["blade_height_mm"] == {"min": 30, "max": 60, "step": 1}
    assert yuka["unsupported"] == [
        "spacing_cm",
        "obstacle_detection",
        "obstacle_laps",
        "order",
        "start_progress_pct",
    ]
