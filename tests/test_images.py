"""The map image, the heatmap images, the missed area sensor and the SVG renderer."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components.image import async_get_image
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.mowdeck import render
from custom_components.mowdeck.const import DOMAIN
from custom_components.mowdeck.diagnostics import async_get_config_entry_diagnostics
from custom_components.mowdeck.geo import from_wgs84, local_angle

from .conftest import wait_for
from .fake_mowdeck import MOWER_ID, FakeMowDeck, mower_state

MAP = "image.luba_map"
COVERAGE = "image.luba_coverage_heatmap"
HEATMAPS = (
    COVERAGE,
    "image.luba_rtk_heatmap",
    "image.luba_wi_fi_heatmap",
    "image.luba_mobile_network_heatmap",
)
MISSED = "sensor.luba_missed_area"
GAPS = [
    {"area_m2": 1.25, "polygon": [[1, 1], [2, 1], [2, 2]]},
    {"area_m2": 2.5, "polygon": [[4, 4], [6, 4], [6, 6]]},
]


def state_of(hass: HomeAssistant, entity_id: str) -> Any:
    """The state object of an entity (asserting it exists)."""
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


async def svg(hass: HomeAssistant, entity_id: str) -> str:
    """The picture of an image entity as text (asserting it is an SVG)."""
    image = await async_get_image(hass, entity_id)
    assert image.content_type == "image/svg+xml"
    text = image.content.decode()
    assert text.startswith("<svg") and text.endswith("</svg>")
    return text


@pytest.fixture
def full_map(fake_api: FakeMowDeck) -> FakeMowDeck:
    """A map with a dock, a no-go zone, an obstacle and a transfer path; missed spots."""
    fake_api.maps[MOWER_ID].update(
        {
            "dock": {"x": -1.0, "y": -1.0, "heading_deg": 90.0},
            "no_go_zones": [{"id": "ng", "boundary": [[3, 3], [4, 3], [4, 4]]}],
            "obstacles": [{"id": "ob", "boundary": [[6, 1], [7, 1], [7, 2], "bad"]}],
            "paths": [{"id": "p", "points": [[0, 0], [10, 10]]}],
        }
    )
    fake_api.heatmap_gaps = GAPS
    return fake_api


@pytest.mark.usefixtures("full_map", "loaded_entry")
async def test_map_image(hass: HomeAssistant, entity_registry: er.EntityRegistry) -> None:
    """The map with areas, no-go zone, obstacle, transfer path, dock and the mower (an arrow
    at the origin, north up); the heatmaps are disabled by default."""
    text = await svg(hass, MAP)
    assert 'd="M0 0L10 0L10 -10Z' in text  # the front area, y pointing up
    assert render.GOOD in text and render.BAD in text and render.CHARGING in text
    assert 'stroke-dasharray="6 4"' in text  # the transfer path
    assert "<circle" not in text  # the mower has a heading: an arrow
    assert state_of(hass, MAP).state != "unknown"
    for entity_id in HEATMAPS:
        entry = entity_registry.async_get(entity_id)
        assert entry is not None, entity_id
        assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION


@pytest.mark.usefixtures("loaded_entry")
async def test_map_image_follows_the_mower_at_most_every_interval(
    hass: HomeAssistant, fake_api: FakeMowDeck
) -> None:
    """A new position renders again, but not more often than every 10 s; the same position
    renders nothing new."""
    first = state_of(hass, MAP).state
    await fake_api.push_state(MOWER_ID, mower_state(battery_percent=50))
    await wait_for(hass, lambda: state_of(hass, "sensor.luba_battery").state == "50")
    assert state_of(hass, MAP).state == first

    position = {"lat": 48.13702, "lon": 11.57502, "heading": None}
    await fake_api.push_state(MOWER_ID, mower_state(position=position))
    await wait_for(hass, lambda: state_of(hass, "sensor.luba_battery").state == "87")
    assert state_of(hass, MAP).state == first
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done()
    await wait_for(hass, lambda: state_of(hass, MAP).state != first)
    assert "<circle" in await svg(hass, MAP)  # no heading: a dot


@pytest.mark.usefixtures("loaded_entry")
async def test_map_image_with_the_planned_path(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """While a job runs the planned path is read (at most every 30 s) and drawn with its
    mowed part; after the job it is gone."""
    fake_api.paths[MOWER_ID] = {
        "path_hash": "abc",
        "planned_at": "2026-10-22T10:00:00Z",
        "points_total": 6,
        "area_ids": ["zone-front"],
        "polylines": [[[0, 0], [5, 0], [5, 1]], [[5, 2], [0, 2], "x", [0, 3]]],
        "progress": {"index": 4, "direction": 0},
    }
    with patch("custom_components.mowdeck.image.MAP_MIN_INTERVAL", timedelta(0)):
        await fake_api.push_state(MOWER_ID, mower_state(activity="mowing", charging=False))
        await wait_for(hass, lambda: any(r.endswith("/path") for r in fake_api.requests))
        await hass.async_block_till_done()
        text = await svg(hass, MAP)
        assert 'stroke-opacity="0.35"' in text
        # Points before index 4 are mowed: the first polyline (the next point alone draws
        # nothing); the second polyline is ahead.
        assert 'd="M0 0L5 0L5 -1" fill="none" stroke="#1f7a35" stroke-width="2"' in text
        assert 'd="M5 -2L0 -2L0 -3" fill="none"' in text
        await fake_api.push_state(MOWER_ID, mower_state(activity="mowing", battery_percent=80))
        await wait_for(hass, lambda: state_of(hass, "sensor.luba_battery").state == "80")
        assert len([r for r in fake_api.requests if r.endswith("/path")]) == 1

        await fake_api.push_state(MOWER_ID, mower_state())
        await wait_for(hass, lambda: state_of(hass, "sensor.luba_battery").state == "87")
        assert 'stroke-opacity="0.35"' not in await svg(hass, MAP)


@pytest.mark.usefixtures("loaded_entry")
async def test_map_image_without_a_path(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """A job whose path is not known yet (404) or cannot be read draws no path."""
    fake_api.problem_paths[f"/api/v1/telemetry/mowers/{MOWER_ID}/path"] = 500
    await fake_api.push_state(MOWER_ID, mower_state(activity="mowing", charging=False))
    await wait_for(hass, lambda: any(r.endswith("/path") for r in fake_api.requests))
    await hass.async_block_till_done()
    assert 'stroke-opacity="0.35"' not in await svg(hass, MAP)


@pytest.mark.usefixtures("full_map", "entity_registry_enabled_by_default", "loaded_entry")
async def test_heatmap_images_and_missed_area(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """Enabled heatmap images make the coordinator read their layers; each is rendered with
    its legend's colours; the missed area sums the coverage gaps."""
    await wait_for(
        hass,
        lambda: (
            {q["layer"] for q in fake_api.heatmap_queries} >= {"coverage", "rtk", "wifi", "mnet"}
        ),
    )
    await wait_for(hass, lambda: all(state_of(hass, e).state != "unknown" for e in HEATMAPS))
    coverage = await svg(hass, COVERAGE)
    assert render.heat_color("ratio", 0.8) in coverage
    assert 'fill-opacity="0.25"' in coverage  # the missed spots
    assert render.GOOD in await svg(hass, "image.luba_rtk_heatmap")
    assert render.heat_color("dbm", -61) in await svg(hass, "image.luba_wi_fi_heatmap")
    attributes = state_of(hass, "image.luba_wi_fi_heatmap").attributes
    assert attributes["legend"] == "dbm"
    assert attributes["cell_m"] == 1.0

    missed = state_of(hass, MISSED)
    assert float(missed.state) == pytest.approx(3.8)
    assert missed.attributes["unit_of_measurement"] == "m²"
    assert missed.attributes["spots"] == 2

    # The same layer again changes nothing; new gaps do.
    heatmap = hass.config_entries.async_entries(DOMAIN)[0].runtime_data.heatmap
    before = state_of(hass, COVERAGE).state
    await heatmap.async_refresh()
    await hass.async_block_till_done()
    assert state_of(hass, COVERAGE).state == before
    fake_api.heatmap_gaps = GAPS[:1]
    await heatmap.async_refresh()
    await hass.async_block_till_done()
    assert state_of(hass, MISSED).state == "1.2"
    assert state_of(hass, COVERAGE).state != before


async def test_heatmaps_forbidden_and_without_map(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    loaded_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A 403 raises the heatmap Repairs issue and makes the sensor unavailable; a mower without
    a map has no layers."""
    heatmap = loaded_entry.runtime_data.heatmap
    await wait_for(hass, lambda: heatmap.layer(MOWER_ID, "coverage") is not None)
    diagnostics = await async_get_config_entry_diagnostics(hass, loaded_entry)
    assert diagnostics["heatmap"]["layers"][f"{MOWER_ID} coverage"]["cells"] == 1

    fake_api.forbidden_paths.add(f"/api/v1/telemetry/mowers/{MOWER_ID}/heatmap")
    await heatmap.async_refresh()
    await hass.async_block_till_done()
    assert issue_registry.async_get_issue(DOMAIN, f"heatmap_forbidden_{loaded_entry.entry_id}")
    assert state_of(hass, MISSED).state == "unavailable"

    fake_api.forbidden_paths.clear()
    fake_api.maps.clear()
    await heatmap.async_refresh()
    await hass.async_block_till_done()
    assert heatmap.layer(MOWER_ID, "coverage") is None
    assert state_of(hass, MISSED).state == "unknown"


async def test_no_map_no_images(
    hass: HomeAssistant, fake_api: FakeMowDeck, config_entry: MockConfigEntry
) -> None:
    """A mower without a map has no map image, heatmaps or missed area."""
    fake_api.maps.clear()
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(MAP) is None
    assert hass.states.get(MISSED) is None
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


def test_renderer() -> None:
    """Colour scales as in the web app, the local frame and an empty picture."""
    assert render.mix("#000000", 25, "#ffffff") == "#bfbfbf"
    assert render.heat_color("ratio", 1.0) == render.GOOD
    assert render.heat_color("ratio", 0.0) == render.INK_MUTED
    assert render.heat_color("fix", 0.4) == render.BAD
    assert render.heat_color("fix", 0.7) == render.WARN
    assert render.heat_color("dbm", -95) == render.BAD
    assert render.heat_color("dbm", -30) == render.GOOD
    reference = {"origin_lat": 48.137, "origin_lon": 11.575}
    assert from_wgs84(reference, 48.137, 11.575) == (0.0, 0.0)
    assert local_angle(0, reference) == pytest.approx(1.5708, abs=1e-4)  # north is +y
    empty = render.render_map({}).decode()
    assert 'viewBox="-7 -7 14 14"' in empty
    odd = render.render_heatmap({}, {"cells": [{"x": "a"}, "bad"], "gaps": [{"polygon": []}]})
    assert odd.decode().count("<path") == 0
