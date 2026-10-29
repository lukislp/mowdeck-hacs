"""The error lexicon of MowDeck (ADR 0067): the new error categories, unknown categories of a
newer MowDeck, and the current error in plain words on the error category sensor."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mowdeck.const import ERROR_CATEGORIES
from custom_components.mowdeck.diagnostics import async_get_config_entry_diagnostics

from .conftest import wait_for
from .fake_mowdeck import MOWER_ID, FakeMowDeck, mower_state

CATEGORY = "sensor.luba_error_category"
CODE = "sensor.luba_error_code"

STUCK_EN = {
    "title": "Mower is stuck",
    "description": "The mower cannot move on.",
    "cause": "A wheel is blocked.",
    "steps": ["Free the wheels.", "Press start."],
    "severity": "critical",
    "category": "trapped",
    "user_action": True,
}
STUCK_DE = {
    **STUCK_EN,
    "title": "Mäher steckt fest",
    "description": "Der Mäher kommt nicht weiter.",
    "steps": ["Räder befreien.", "Start drücken."],
}


def attributes(hass: HomeAssistant, entity_id: str = CATEGORY) -> dict[str, Any]:
    """The attributes of an entity (asserting it exists)."""
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return dict(state.attributes)


def value(hass: HomeAssistant, entity_id: str = CATEGORY) -> str:
    """The state of an entity (asserting it exists)."""
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state.state


def in_error(code: str = "1201", category: str | None = "trapped") -> dict[str, Any]:
    """A state of the test mower in error."""
    return mower_state(activity="error", charging=False, error_code=code, error_category=category)


def test_new_categories_are_options() -> None:
    """The categories of ADR 0067 are part of the enum, "other" stays last."""
    for category in ("communication", "docking", "firmware"):
        assert category in ERROR_CATEGORIES
    assert ERROR_CATEGORIES[-1] == "other"


@pytest.mark.usefixtures("loaded_entry")
@pytest.mark.parametrize("category", ["communication", "docking", "firmware"])
async def test_new_category_state(
    hass: HomeAssistant, fake_api: FakeMowDeck, category: str
) -> None:
    """A fault of a new category shows as that category on both error sensors."""
    await fake_api.push_state(MOWER_ID, in_error("77", category))
    await wait_for(hass, lambda: value(hass) == category)
    assert category in attributes(hass)["options"]
    assert attributes(hass, CODE)["error_category"] == category


@pytest.mark.usefixtures("loaded_entry")
async def test_unknown_category_logged_once(
    hass: HomeAssistant, fake_api: FakeMowDeck, caplog: pytest.LogCaptureFixture
) -> None:
    """A category of a newer MowDeck becomes "other" and is logged once at debug level."""
    caplog.set_level(logging.DEBUG, logger="custom_components.mowdeck.sensor")
    await fake_api.push_state(MOWER_ID, in_error("5", "quantum_tunnelling"))
    await wait_for(hass, lambda: value(hass) == "other")
    await fake_api.push_state(MOWER_ID, in_error("6", "quantum_tunnelling"))
    await wait_for(hass, lambda: value(hass, CODE) == "6")
    assert value(hass) == "other"
    logged = [r for r in caplog.records if "quantum_tunnelling" in r.getMessage()]
    assert len(logged) == 1
    assert logged[0].levelno == logging.DEBUG


@pytest.mark.usefixtures("loaded_entry")
async def test_error_explained(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """While the mower is in error, the category sensor carries the error in plain words; it
    is read once per error code and gone with the error."""
    fake_api.error_lexicon = {"1201": {"en": STUCK_EN, "de": STUCK_DE}}
    assert attributes(hass).get("title") is None
    await fake_api.push_state(MOWER_ID, in_error())
    await wait_for(hass, lambda: attributes(hass).get("title") == "Mower is stuck")
    explained = attributes(hass)
    assert value(hass) == "trapped"
    assert explained["description"] == "The mower cannot move on."
    assert explained["cause"] == "A wheel is blocked."
    assert explained["steps"] == ["Free the wheels.", "Press start."]
    assert explained["severity"] == "critical"
    assert explained["user_action"] is True
    assert fake_api.error_queries == [{"mower_id": MOWER_ID, "locale": "en"}]

    # Repeated states of the same error (the gateway's heartbeat) read nothing again.
    for _ in range(3):
        await fake_api.push_state(MOWER_ID, in_error())
    await hass.async_block_till_done()
    assert len(fake_api.error_queries) == 1

    # A cleared error drops the attributes; no further reads while the mower has none.
    await fake_api.push_state(MOWER_ID, mower_state())
    await wait_for(hass, lambda: value(hass) == "unknown")
    assert "title" not in attributes(hass)
    await fake_api.push_state(MOWER_ID, mower_state(activity="mowing", charging=False))
    await hass.async_block_till_done()
    assert len(fake_api.error_queries) == 1


@pytest.mark.usefixtures("loaded_entry")
async def test_error_in_home_assistant_language(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """The texts follow Home Assistant's language; a language without texts gets English."""
    fake_api.error_lexicon = {"1201": {"en": STUCK_EN, "de": STUCK_DE}}
    hass.config.language = "de-CH"
    await fake_api.push_state(MOWER_ID, in_error())
    await wait_for(hass, lambda: attributes(hass).get("title") == "Mäher steckt fest")
    assert attributes(hass)["steps"] == ["Räder befreien.", "Start drücken."]
    assert fake_api.error_queries[-1]["locale"] == "de"

    hass.config.language = "fr"
    await fake_api.push_state(MOWER_ID, in_error())
    await wait_for(hass, lambda: attributes(hass).get("title") == "Mower is stuck")
    assert fake_api.error_queries[-1]["locale"] == "fr"


@pytest.mark.usefixtures("loaded_entry")
async def test_new_error_code_read_again(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """A different error code is read again; an unknown code keeps MowDeck's generic title."""
    fake_api.error_lexicon = {"1201": {"en": STUCK_EN}}
    await fake_api.push_state(MOWER_ID, in_error())
    await wait_for(hass, lambda: attributes(hass).get("title") == "Mower is stuck")
    await fake_api.push_state(MOWER_ID, in_error("4711", "other"))
    await wait_for(hass, lambda: attributes(hass).get("title") == "Error 4711")
    assert attributes(hass)["steps"] == []
    assert attributes(hass)["description"] is None
    assert [q["mower_id"] for q in fake_api.error_queries] == [MOWER_ID, MOWER_ID]


@pytest.mark.usefixtures("loaded_entry")
async def test_older_mowdeck_without_lexicon(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """A MowDeck before the error lexicon answers 404: the category still shows, the text
    attributes stay empty, and the same error is not asked about again."""
    assert fake_api.error_lexicon is None
    await fake_api.push_state(MOWER_ID, in_error("1201", "docking"))
    await wait_for(hass, lambda: value(hass) == "docking")
    await wait_for(
        hass, lambda: "GET /api/v1/telemetry/mowers/" + MOWER_ID + "/error" in fake_api.requests
    )
    await hass.async_block_till_done()
    explained = attributes(hass)
    assert explained["title"] is None
    assert explained["steps"] == []
    assert explained["severity"] is None
    before = fake_api.requests.count(f"GET /api/v1/telemetry/mowers/{MOWER_ID}/error")
    for _ in range(3):
        await fake_api.push_state(MOWER_ID, in_error("1201", "docking"))
    await hass.async_block_till_done()
    assert fake_api.requests.count(f"GET /api/v1/telemetry/mowers/{MOWER_ID}/error") == before


async def test_failed_read_retried_later(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """A failed read (MowDeck unavailable) is repeated only after a while."""
    fake_api.error_lexicon = {"1201": {"en": STUCK_EN}}
    path = f"/api/v1/telemetry/mowers/{MOWER_ID}/error"
    fake_api.problem_paths[path] = 500
    await fake_api.push_state(MOWER_ID, in_error())
    await wait_for(hass, lambda: f"GET {path}" in fake_api.requests)
    await hass.async_block_till_done()
    assert attributes(hass)["title"] is None
    fake_api.problem_paths.clear()
    await fake_api.push_state(MOWER_ID, in_error())
    await hass.async_block_till_done()
    assert fake_api.requests.count(f"GET {path}") == 1

    # Once the retry time has passed, the next state reads it again.
    coordinator = loaded_entry.runtime_data.coordinator
    key, retry_at = coordinator._error_reads[MOWER_ID]
    assert retry_at is not None
    assert retry_at - dt_util.utcnow() > timedelta(minutes=4)
    coordinator._error_reads[MOWER_ID] = (key, dt_util.utcnow() - timedelta(seconds=1))
    await fake_api.push_state(MOWER_ID, in_error())
    await wait_for(hass, lambda: attributes(hass).get("title") == "Mower is stuck")
    assert fake_api.requests.count(f"GET {path}") == 2


async def test_diagnostics_show_explained_error(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """Diagnostics carry the explained error with its category and severity."""
    fake_api.error_lexicon = {"1201": {"en": STUCK_EN}}
    await fake_api.push_state(MOWER_ID, in_error())
    await wait_for(hass, lambda: attributes(hass).get("title") == "Mower is stuck")
    diagnostics = await async_get_config_entry_diagnostics(hass, loaded_entry)
    error = diagnostics["coordinator"]["errors"][MOWER_ID]
    assert error["code"] == "1201"
    assert error["category"] == "trapped"
    assert error["severity"] == "critical"
