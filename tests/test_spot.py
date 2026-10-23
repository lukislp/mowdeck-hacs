"""Spot mowing: mowdeck.spot_mow (Planner request, spot_mow command, validation), the spot run
sensor and the stop button."""

from __future__ import annotations

import math
from typing import Any

import pytest
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mowdeck import spot
from custom_components.mowdeck.const import DOMAIN

from .conftest import wait_for
from .fake_mowdeck import MOWER_ID, FakeMowDeck, mower_state

MOWER = "lawn_mower.luba"
SENSOR = "sensor.luba_spot_run"
STOP = "button.luba_stop_spot_run"
ORIGIN = (48.137, 11.575)
EARTH = 6_378_137.0


def at(east: float, north: float) -> tuple[float, float]:
    """``(lat, lon)`` this many metres east and north of the map origin."""
    lat = ORIGIN[0] + math.degrees(north / EARTH)
    lon = ORIGIN[1] + math.degrees(east / (EARTH * math.cos(math.radians(ORIGIN[0]))))
    return lat, lon


def sent(fake_api: FakeMowDeck) -> list[dict[str, Any]]:
    """The command bodies the fake Fleet received."""
    return [c["command"] for c in fake_api.commands]


def state_of(hass: HomeAssistant, entity_id: str) -> Any:
    """The state object of an entity (asserting it exists)."""
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


async def spot_mow(hass: HomeAssistant, **data: Any) -> Any:
    """Call mowdeck.spot_mow on the mower and return its response."""
    return await hass.services.async_call(
        DOMAIN,
        "spot_mow",
        {ATTR_ENTITY_ID: MOWER, **data},
        blocking=True,
        return_response=True,
    )


@pytest.fixture
def drivable(fake_api: FakeMowDeck) -> FakeMowDeck:
    """A mower MowDeck can drive, idle with an RTK fix 2 m east of the origin, on a map whose
    front area is a 40 m square around the origin."""
    fake_api.capabilities[MOWER_ID].update(
        {"manual_drive": True, "manual_drive_blades": False, "cutting_width_mm": 360}
    )
    square = [[-20, -20], [20, -20], [20, 20], [-20, 20]]
    fake_api.maps[MOWER_ID]["areas"][0]["boundary"] = square
    lat, lon = at(2.2, 0.1)
    fake_api.states[MOWER_ID] = mower_state(
        activity="idle", charging=False, position={"lat": lat, "lon": lon, "heading": 0.0}
    )
    return fake_api


@pytest.mark.usefixtures("drivable", "loaded_entry")
async def test_spot_mow_at_a_point(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """A point: the circle inside the front area, planned like the web app, then spot_mow
    with the plan; a dry run by default."""
    lat, lon = at(5, 5)
    answer = await spot_mow(hass, latitude=lat, longitude=lon, radius=1)
    [request] = fake_api.plan_requests
    assert request["mower_id"] == MOWER_ID
    assert request["area"]["area_id"] == "zone-front"
    polygon = request["area"]["polygon"]
    assert len(polygon) == 32
    assert polygon[0] == pytest.approx([6.0, 5.0], abs=0.02)
    assert request["chain"] == [{"pattern": "spiral", "settings": {"direction": "inward"}}]
    assert request["mower"] == {
        "cutting_width_m": 0.36,
        "overlap_m": 0.05,
        "max_speed_mps": 0.2,
        "transfer_speed_mps": 0.2,
        "safety_margin_m": 0.4,
    }
    assert request["turn_style"] == "arc"
    assert request["transit"] == {"from_dock": False, "position": [2.0, 0.0]}
    assert request["wgs84"] is False

    [command] = sent(fake_api)
    assert command["kind"] == "spot_mow"
    assert command["plan"]["plan_hash"] == "f" * 64
    assert (command["blades"], command["confirm_blades"]) == (False, False)
    assert command["speed_mps"] == 0.2
    assert command["spot"] == {"center_wgs84": [lat, lon], "radius_m": 1.0}
    assert command["turn_style"] is None
    assert command["return_to_dock"] is True
    assert answer[MOWER]["area_id"] == "zone-front"
    assert answer[MOWER]["blades"] is False
    assert answer[MOWER]["estimated_duration_s"] == 95.0
    assert answer[MOWER]["command_id"]


@pytest.mark.usefixtures("drivable")
async def test_spot_mow_at_a_zone_from_the_dock_with_blades(
    hass: HomeAssistant, fake_api: FakeMowDeck, config_entry: MockConfigEntry
) -> None:
    """A zone (its centre and radius), a docked mower that can undock (the transit starts at
    the dock), confirmed blades on a mower that allows them, stripes and three-point turns."""
    fake_api.capabilities[MOWER_ID].update(
        {"manual_drive_blades": True, "undock": True, "drive_reverse": True}
    )
    fake_api.states[MOWER_ID] = mower_state(rtk_fix="float", turn_style="in_place")
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    lat, lon = at(-4, 3)
    hass.states.async_set("zone.flower_bed", "0", {"latitude": lat, "longitude": lon, "radius": 2})

    await spot_mow(
        hass,
        zone="zone.flower_bed",
        pattern="stripes",
        angle=30,
        speed=0.3,
        blades=True,
        confirm_blades=True,
        return_to_dock=False,
        turn_style="multi_point",
    )
    [request] = fake_api.plan_requests
    assert request["transit"] == {"from_dock": True}
    assert request["chain"] == [{"pattern": "stripes", "settings": {"angle_deg": 30.0}}]
    assert request["turn_style"] == "multi_point"
    assert request["mower"]["max_speed_mps"] == 0.3
    [command] = sent(fake_api)
    assert (command["blades"], command["confirm_blades"]) == (True, True)
    assert command["spot"]["radius_m"] == 2.0
    assert command["turn_style"] == "multi_point"
    assert command["return_to_dock"] is False

    # Without reverse driving, three-point turns fall back to arcs.
    fake_api.plan_requests.clear()
    fake_api.capabilities[MOWER_ID]["drive_reverse"] = False
    coordinator = config_entry.runtime_data.coordinator
    coordinator._static_fetched_at.clear()
    await coordinator.async_refresh()
    await spot_mow(hass, zone="zone.flower_bed", radius=1.5)
    assert fake_api.plan_requests[0]["turn_style"] == "in_place"
    await spot_mow(hass, zone="zone.flower_bed", radius=1.5, turn_style="multi_point")
    assert fake_api.plan_requests[1]["turn_style"] == "arc"
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    ("data", "state", "key"),
    [
        ({}, {}, "spot_location"),
        ({"latitude": 48.1}, {}, "spot_location"),
        ({"zone": "zone.flower_bed", "latitude": 48.1}, {}, "spot_location"),
        ({"zone": "zone.missing"}, {}, "spot_zone_unknown"),
        ({"zone": "zone.flower_bed"}, {}, "spot_radius"),
        ({"at": (5, 5), "blades": True}, {}, "spot_blades_unconfirmed"),
        ({"at": (5, 5), "blades": True, "confirm_blades": True}, {}, "spot_blades_unsupported"),
        ({"at": (400, 0)}, {}, "spot_outside_areas"),
        ({"at": (5, 5)}, {"activity": "mowing"}, "spot_busy"),
        ({"at": (5, 5)}, {"activity": "docked"}, "spot_busy"),
        ({"at": (5, 5)}, {"spot_run": {"run_id": "r", "state": "transit"}}, "spot_busy"),
        ({"at": (5, 5)}, {"battery_percent": 20}, "spot_battery"),
        ({"at": (5, 5)}, {"rtk_fix": "float"}, "spot_rtk"),
        ({"at": (5, 5)}, {"position_source": "gnss"}, "spot_rtk"),
    ],
)
@pytest.mark.usefixtures("drivable")
async def test_spot_mow_is_refused(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    config_entry: MockConfigEntry,
    data: dict[str, Any],
    state: dict[str, Any],
    key: str,
) -> None:
    """Every start condition of the web app's spot sheet, before anything is sent."""
    fake_api.states[MOWER_ID].update(state)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    bed = {"latitude": 48.137, "longitude": 11.575, "radius": 100}
    hass.states.async_set("zone.flower_bed", "0", bed)
    data = dict(data)
    if "at" in data:
        lat, lon = at(*data.pop("at"))
        data.update(latitude=lat, longitude=lon)
    with pytest.raises(ServiceValidationError) as err:
        await spot_mow(hass, **data)
    assert err.value.translation_key == key
    assert fake_api.plan_requests == []
    assert fake_api.commands == []
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("drivable", "loaded_entry")
async def test_spot_mow_without_route_or_map(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """No route to the spot: nothing is sent; the reason is shown."""
    fake_api.plan_route_found = False
    lat, lon = at(5, 5)
    with pytest.raises(ServiceValidationError) as err:
        await spot_mow(hass, latitude=lat, longitude=lon)
    assert err.value.translation_key == "spot_no_route"
    assert err.value.translation_placeholders == {"reason": "no_route: blocked by a no-go zone"}
    assert fake_api.commands == []


@pytest.mark.usefixtures("drivable")
async def test_spot_mow_needs_drive_and_a_map(
    hass: HomeAssistant, fake_api: FakeMowDeck, config_entry: MockConfigEntry
) -> None:
    """A mower without a map, or one MowDeck cannot drive, is refused."""
    del fake_api.maps[MOWER_ID]
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    lat, lon = at(5, 5)
    with pytest.raises(ServiceValidationError) as err:
        await spot_mow(hass, latitude=lat, longitude=lon)
    assert err.value.translation_key == "no_map"
    fake_api.capabilities[MOWER_ID]["manual_drive"] = False
    coordinator = config_entry.runtime_data.coordinator
    coordinator._static_fetched_at.clear()
    await coordinator.async_refresh()
    with pytest.raises(ServiceValidationError) as err:
        await spot_mow(hass, latitude=lat, longitude=lon)
    assert err.value.translation_key == "spot_unsupported"
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("drivable", "loaded_entry")
async def test_spot_mow_permissions(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """The Planner needs mower.control, the spot_mow command mower.drive."""
    lat, lon = at(5, 5)
    fake_api.forbidden_paths.add("/api/v1/planner/plans")
    with pytest.raises(HomeAssistantError) as err:
        await spot_mow(hass, latitude=lat, longitude=lon)
    assert err.value.translation_placeholders == {
        "command": "spot_mow",
        "permission": "mower.control",
    }
    fake_api.forbidden_paths.clear()
    fake_api.forbid_commands = True
    with pytest.raises(HomeAssistantError) as err:
        await spot_mow(hass, latitude=lat, longitude=lon)
    assert err.value.translation_placeholders == {
        "command": "spot_mow",
        "permission": "mower.drive",
    }


@pytest.mark.usefixtures("drivable", "loaded_entry")
async def test_spot_run_sensor_and_stop(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """The run's state and progress; the stop button sends spot_stop."""
    assert state_of(hass, SENSOR).state == "unknown"
    run = {
        "run_id": "4d2c1b0a-0000-4000-8000-000000000001",
        "state": "spot_mowing",
        "progress_percent": 40,
        "driven_points": 12,
        "planned_points": 30,
        "segment_index": 1,
        "segments_total": 3,
        "dry_run": True,
        "reason": None,
    }
    await fake_api.push_state(MOWER_ID, mower_state(activity="idle", spot_run=run))
    await wait_for(hass, lambda: state_of(hass, SENSOR).state == "spot_mowing")
    attributes = state_of(hass, SENSOR).attributes
    assert attributes["progress_percent"] == 40
    assert attributes["dry_run"] is True
    assert attributes["segments_total"] == 3

    await hass.services.async_call("button", SERVICE_PRESS, {ATTR_ENTITY_ID: STOP}, blocking=True)
    assert sent(fake_api) == [{"kind": "spot_stop"}]

    await fake_api.push_state(
        MOWER_ID, mower_state(spot_run={**run, "state": "aborted", "reason": "rtk_lost"})
    )
    await wait_for(hass, lambda: state_of(hass, SENSOR).state == "aborted")
    assert state_of(hass, SENSOR).attributes["reason"] == "rtk_lost"


@pytest.mark.usefixtures("loaded_entry")
async def test_no_spot_entities_without_manual_drive(hass: HomeAssistant) -> None:
    """Mowers MowDeck cannot drive have no spot run sensor and no stop button."""
    assert hass.states.get(SENSOR) is None
    assert hass.states.get(STOP) is None


def test_geometry_matches_the_web_app() -> None:
    """The local frame (with rotation), the circle and the turn style rules."""
    reference = {"origin_lat": 48.137, "origin_lon": 11.575, "rotation_deg": 90}
    lat, lon = at(0, 10)
    x, y = spot.from_wgs84(reference, lat, lon)
    assert (x, y) == pytest.approx((10.0, 0.0), abs=1e-6)
    ring = spot.circle_polygon((1.0, 2.0), 1.5)
    assert ring[0] == (2.5, 2.0)
    assert ring[8] == (1.0, 3.5)
    assert spot.resolve_turn_style(None, None, False) == "arc"
    assert spot.resolve_turn_style("bogus", None, True) == "arc"
    assert spot.transit_from({"position": None}, {}, reference) is None
