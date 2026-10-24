"""Mowing defaults, area switches, today's totals and the map and heatmap actions."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.components.lawn_mower import SERVICE_START_MOWING
from homeassistant.components.number import ATTR_VALUE, SERVICE_SET_VALUE
from homeassistant.components.select import ATTR_OPTION, SERVICE_SELECT_OPTION
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.mowdeck.const import DOMAIN

from .fake_mowdeck import MOWER_ID, FakeMowDeck

MOWER = "lawn_mower.luba"
AREA = "select.luba_area"
FRONT = "switch.luba_mow_front"
BACK = "switch.luba_mow_back"
PATTERN = "select.luba_mowing_pattern"
ANGLE = "number.luba_mowing_angle"
LAPS = "number.luba_border_laps"
SPACING = "number.luba_path_spacing"


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
    await hass.async_block_till_done()


async def start(hass: HomeAssistant) -> None:
    """Press start on the lawn mower."""
    await call(hass, "lawn_mower", SERVICE_START_MOWING, MOWER)


@pytest.fixture
def patterns(fake_api: FakeMowDeck) -> FakeMowDeck:
    """A mower with three native patterns and a settable path spacing."""
    fake_api.capabilities[MOWER_ID].update(
        {
            "native_patterns": ["stripes", "edge_only", "diagonal", "spiral"],
            "path_spacing_range_mm": [200, 350],
        }
    )
    return fake_api


# Areas


@pytest.mark.usefixtures("loaded_entry")
async def test_area_switches_and_select(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """The switches make the selection, the select shows and shortcuts it."""
    assert state_of(hass, FRONT).state == "on"
    assert state_of(hass, BACK).state == "on"
    assert state_of(hass, AREA).state == "all"

    await call(hass, "switch", SERVICE_TURN_OFF, FRONT)
    assert state_of(hass, AREA).state == "Back"
    await start(hass)

    await call(hass, "select", SERVICE_SELECT_OPTION, AREA, **{ATTR_OPTION: "Front"})
    assert (state_of(hass, FRONT).state, state_of(hass, BACK).state) == ("on", "off")
    await call(hass, "switch", SERVICE_TURN_ON, BACK)
    assert state_of(hass, AREA).state == "all"

    await call(hass, "switch", SERVICE_TURN_OFF, BACK)
    await call(hass, "switch", SERVICE_TURN_OFF, FRONT)
    assert state_of(hass, AREA).state == "selection"
    with pytest.raises(ServiceValidationError) as err:
        await start(hass)
    assert err.value.translation_key == "no_area_selected"
    # Choosing "selection" keeps the switches as they are.
    await call(hass, "select", SERVICE_SELECT_OPTION, AREA, **{ATTR_OPTION: "selection"})
    assert state_of(hass, FRONT).state == "off"
    assert sent(fake_api) == [{"kind": "start_mow", "area_ids": ["zone-back"]}]


async def test_removed_area_takes_its_switch_along(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    loaded_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """An area that is no longer on the mower's map is gone for good: its switch leaves the
    registry, the other areas keep theirs."""
    assert entity_registry.async_get(BACK) is not None
    fake_api.maps[MOWER_ID]["areas"] = fake_api.maps[MOWER_ID]["areas"][:1]
    fake_api.maps[MOWER_ID]["revision"] = "rev-2"
    coordinator = loaded_entry.runtime_data.coordinator
    coordinator._static_fetched_at.clear()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert entity_registry.async_get(BACK) is None
    assert state_of(hass, FRONT).state == "on"


async def test_three_areas_two_selected(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """Several of more areas start in map order; a new area adds a switch."""
    fake_api.maps[MOWER_ID]["areas"].append({"id": "zone-side", "name": "Side", "boundary": []})
    fake_api.maps[MOWER_ID]["revision"] = "rev-3"
    coordinator = loaded_entry.runtime_data.coordinator
    coordinator._static_fetched_at.clear()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    await call(hass, "switch", SERVICE_TURN_OFF, "switch.luba_mow_side")
    await call(hass, "switch", SERVICE_TURN_OFF, FRONT)
    await call(hass, "switch", SERVICE_TURN_ON, "switch.luba_mow_side")
    assert state_of(hass, AREA).state == "selection"
    await start(hass)
    assert sent(fake_api) == [{"kind": "start_mow", "area_ids": ["zone-back", "zone-side"]}]


async def test_selection_is_restored(
    hass: HomeAssistant, fake_api: FakeMowDeck, config_entry: MockConfigEntry
) -> None:
    """The select restores the whole selection after a restart."""
    mock_restore_cache_with_extra_data(
        hass, [(State(AREA, "selection"), {"area_ids": ["zone-back"]})]
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert state_of(hass, FRONT).state == "off"
    assert state_of(hass, AREA).state == "Back"
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_old_selection_is_restored(
    hass: HomeAssistant, fake_api: FakeMowDeck, config_entry: MockConfigEntry
) -> None:
    """A selection stored by an older version (only the label) still restores."""
    mock_restore_cache_with_extra_data(hass, [(State(AREA, "Front"), {})])
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert state_of(hass, BACK).state == "off"
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


# Mowing defaults


@pytest.mark.usefixtures("patterns", "loaded_entry")
async def test_mowing_defaults(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """Pattern, angle, border laps and spacing are saved as the mowing defaults; a start uses
    them once saved."""
    pattern = state_of(hass, PATTERN)
    assert pattern.attributes["options"] == ["stripes", "diagonal", "edge_only"]
    assert pattern.state == "stripes"
    assert state_of(hass, ANGLE).state == "0.0"
    assert state_of(hass, LAPS).state == "1.0"
    spacing = state_of(hass, SPACING)
    assert spacing.state == "unknown"
    assert (spacing.attributes["min"], spacing.attributes["max"]) == (20.0, 35.0)

    await start(hass)
    assert "pattern" not in sent(fake_api)[0]

    await call(hass, "select", SERVICE_SELECT_OPTION, PATTERN, **{ATTR_OPTION: "diagonal"})
    await call(hass, "number", SERVICE_SET_VALUE, ANGLE, **{ATTR_VALUE: 30})
    await call(hass, "number", SERVICE_SET_VALUE, LAPS, **{ATTR_VALUE: 2})
    await call(hass, "number", SERVICE_SET_VALUE, SPACING, **{ATTR_VALUE: 28})
    expected = {"name": "diagonal", "angle_deg": 30.0, "border_laps": 2, "spacing_m": 0.28}
    assert fake_api.mowers[0]["mow_defaults"] == {"pattern": expected}
    assert state_of(hass, PATTERN).state == "diagonal"
    assert state_of(hass, SPACING).state == "28.0"

    await start(hass)
    assert sent(fake_api)[-1]["pattern"] == expected


async def test_mowing_defaults_need_control(
    hass: HomeAssistant,
    patterns: FakeMowDeck,
    loaded_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A 403 names mower.control; without can_control the entities stay, unavailable."""
    patterns.relations = {"can_view"}
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "number", SERVICE_SET_VALUE, ANGLE, **{ATTR_VALUE: 10})
    assert err.value.translation_placeholders == {
        "command": "mow_defaults",
        "permission": "mower.control",
    }
    coordinator = loaded_entry.runtime_data.coordinator
    coordinator._static_fetched_at.clear()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    for entity_id in (PATTERN, ANGLE, LAPS, SPACING):
        assert entity_registry.async_get(entity_id) is not None, entity_id
        assert state_of(hass, entity_id).state == "unavailable", entity_id


@pytest.mark.usefixtures("loaded_entry")
async def test_no_spacing_without_range(hass: HomeAssistant) -> None:
    """Without path_spacing_range_mm there is no spacing number; one pattern still counts."""
    assert hass.states.get(SPACING) is None
    assert state_of(hass, PATTERN).attributes["options"] == ["stripes"]


# Today's totals


async def test_today_totals(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """Mowing time, distance and jobs of the current UTC day; zero without a record."""
    assert state_of(hass, "sensor.luba_mowing_time_today").state == "0.0"
    assert state_of(hass, "sensor.luba_jobs_today").state == "0"
    today = dt_util.utcnow().date().isoformat()
    fake_api.daily[MOWER_ID] = [
        {
            "day": today,
            "mowing_hours": 1.5,
            "distance_m": 2345.67,
            "jobs": 2,
            "online_hours": 12.0,
            "blade_hours": 1.5,
            "battery_cycles": 1,
            "charge_sessions": 1,
            "errors": {},
        }
    ]
    await loaded_entry.runtime_data.health.async_refresh()
    await hass.async_block_till_done()
    mowing = state_of(hass, "sensor.luba_mowing_time_today")
    assert mowing.attributes["unit_of_measurement"] == "min"
    assert float(mowing.state) == pytest.approx(90)
    assert float(state_of(hass, "sensor.luba_distance_today").state) == pytest.approx(2345.7)
    assert state_of(hass, "sensor.luba_jobs_today").state == "2"

    fake_api.health.clear()
    await loaded_entry.runtime_data.health.async_refresh()
    await hass.async_block_till_done()
    assert state_of(hass, "sensor.luba_jobs_today").state == "2"


# Actions with responses


@pytest.mark.usefixtures("loaded_entry")
async def test_get_map(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """mowdeck.get_map answers the map with MowDeck's area names."""
    fake_api.mowers[0]["area_names"] = {"zone-back": "Hinten"}
    await hass.config_entries.async_reload(hass.config_entries.async_entries(DOMAIN)[0].entry_id)
    await hass.async_block_till_done()
    response: Any = await hass.services.async_call(
        DOMAIN, "get_map", {ATTR_ENTITY_ID: MOWER}, blocking=True, return_response=True
    )
    answer: Any = response[MOWER]
    assert isinstance(answer, dict)
    assert answer["revision"] == "rev-1"
    names = [a["display_name"] for a in answer["map"]["areas"]]
    assert names == ["Front", "Hinten"]

    fake_api.maps.clear()
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "get_map", {ATTR_ENTITY_ID: MOWER}, blocking=True, return_response=True
        )
    assert err.value.translation_key == "no_map"


@pytest.mark.usefixtures("loaded_entry")
async def test_get_heatmap(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """mowdeck.get_heatmap passes layer, range and cell size and answers the layer."""
    response: Any = await hass.services.async_call(
        DOMAIN,
        "get_heatmap",
        {
            ATTR_ENTITY_ID: MOWER,
            "layer": "rtk",
            "start": "2026-10-01T00:00:00+00:00",
            "cell": "2",
        },
        blocking=True,
        return_response=True,
    )
    answer: Any = response[MOWER]
    assert isinstance(answer, dict)
    assert answer["layer"] == "rtk"
    [query] = [q for q in fake_api.heatmap_queries if q["layer"] == "rtk"]
    assert query["layer"] == "rtk"
    assert query["cell"] == "2.0"
    assert query["from"].startswith("2026-10-01T00:00:00")
    assert "to" not in query

    fake_api.maps.clear()
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            "get_heatmap",
            {ATTR_ENTITY_ID: MOWER, "layer": "coverage", "end": "2026-10-02T00:00:00+00:00"},
            blocking=True,
            return_response=True,
        )
    assert err.value.translation_key == "action_failed"
