"""Vision (AI assist): start button, proposal sensor, images, event entity, realtime updates."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.components.image import async_get_image
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.mowdeck import logbook
from custom_components.mowdeck.const import DOMAIN, EVENT_VISION_PROPOSAL
from custom_components.mowdeck.diagnostics import async_get_config_entry_diagnostics

from .conftest import wait_for
from .fake_mowdeck import (
    JPEG,
    MOWER_ID,
    PNG,
    PROPOSAL_ID,
    FakeMowDeck,
    proposal,
    proposal_summary,
)

BUTTON = "button.luba_start_ai_analysis"
SENSOR = "sensor.luba_ai_recovery"
IMAGE = "image.luba_ai_recovery_image"
MAP_IMAGE = "image.luba_ai_recovery_map"
EVENT = "event.luba_ai_recovery"


def state_of(hass: HomeAssistant, entity_id: str) -> Any:
    """The state object of an entity (asserting it exists)."""
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


@pytest.fixture
def with_proposal(fake_api: FakeMowDeck) -> FakeMowDeck:
    """A waiting proposal with its camera frame (JPEG) and map rendering (PNG)."""
    fake_api.proposals[PROPOSAL_ID] = proposal()
    fake_api.proposal_images[(PROPOSAL_ID, 0)] = (JPEG, "image/jpeg")
    fake_api.proposal_images[(PROPOSAL_ID, 1)] = (PNG, "image/png")
    return fake_api


@pytest.mark.usefixtures("loaded_entry")
async def test_no_proposal_yet(hass: HomeAssistant) -> None:
    """Without a proposal the sensor and the image are unknown; the entities exist."""
    assert state_of(hass, SENSOR).state == "unknown"
    assert state_of(hass, IMAGE).state == "unknown"
    assert state_of(hass, BUTTON).state == "unknown"
    assert state_of(hass, EVENT).state == "unknown"
    with pytest.raises(HomeAssistantError):
        await async_get_image(hass, IMAGE)


@pytest.mark.usefixtures("with_proposal", "loaded_entry")
async def test_proposal_sensor_and_image(
    hass: HomeAssistant, fake_api: FakeMowDeck, entity_registry: er.EntityRegistry
) -> None:
    """The newest proposal: its state, what it proposes and the link to approve it in MowDeck;
    the camera frame as image, the map rendering disabled by default."""
    sensor = state_of(hass, SENSOR)
    assert sensor.state == "proposed"
    attributes = sensor.attributes
    assert attributes["proposal_id"] == PROPOSAL_ID
    assert attributes["action"] == "drive"
    assert attributes["step_count"] == 2
    assert attributes["drive_seconds"] == 2.2
    assert attributes["steps"][0] == {
        "linear": -0.5,
        "angular": 0.0,
        "seconds": 1.5,
        "outcome": "pending",
    }
    assert attributes["confidence"] == 0.8
    assert attributes["situation"] == "stuck_on_obstacle"
    assert attributes["rationale"].startswith("A root")
    assert attributes["expires_at"] is not None
    assert attributes["link"] == f"{fake_api.base_url}/mowers/{MOWER_ID}?assist={PROPOSAL_ID}"
    assert "proposed" in attributes["options"]

    assert state_of(hass, IMAGE).state != "unknown"
    image = await async_get_image(hass, IMAGE)
    assert (image.content, image.content_type) == (JPEG, "image/jpeg")
    # Read once per proposal.
    await async_get_image(hass, IMAGE)
    reads = [r for r in fake_api.requests if r.endswith(f"{PROPOSAL_ID}/images/0")]
    assert len(reads) == 1

    entry = entity_registry.async_get(MAP_IMAGE)
    assert entry is not None
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION


@pytest.mark.usefixtures("with_proposal", "entity_registry_enabled_by_default", "loaded_entry")
async def test_map_image_and_purged_frames(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """The map rendering is the second image; a purged image gives no picture."""
    image = await async_get_image(hass, MAP_IMAGE)
    assert (image.content, image.content_type) == (PNG, "image/png")
    fake_api.proposal_images.clear()
    fake_api.proposals[PROPOSAL_ID] = proposal(
        id="other", frames=[{"index": 0, "source": "upload"}]
    )
    await fake_api.push(
        {
            "type": "vision_proposal",
            "event": "created",
            "mower_id": MOWER_ID,
            "proposal": proposal_summary(proposal(id="other")),
        }
    )
    await wait_for(hass, lambda: state_of(hass, SENSOR).attributes["proposal_id"] == "other")
    with pytest.raises(HomeAssistantError):
        await async_get_image(hass, IMAGE)


async def test_start_analysis_and_live_updates(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """The button starts an analysis; the realtime proposal events update the sensor at once,
    fire the event entity and the bus event, and the full proposal is read afterwards."""
    events: list[Event] = async_capture_events(hass, EVENT_VISION_PROPOSAL)
    await hass.services.async_call("button", SERVICE_PRESS, {ATTR_ENTITY_ID: BUTTON}, blocking=True)
    assert fake_api.vision_starts == [{"mower_id": MOWER_ID, "task": "recover"}]
    assert state_of(hass, SENSOR).state == "analysing"
    started = next(iter(fake_api.proposals.values()))
    pid = started["id"]

    await fake_api.push(
        {
            "type": "vision_proposal",
            "event": "created",
            "mower_id": MOWER_ID,
            "proposal": proposal_summary(started),
        }
    )
    await wait_for(hass, lambda: len(events) == 1)
    assert state_of(hass, EVENT).attributes["event_type"] == "created"

    # The analysis decided: the summary arrives first, then the full proposal is read.
    done = proposal(id=pid)
    fake_api.proposals[pid] = done
    fake_api.proposal_images[(pid, 0)] = (JPEG, "image/jpeg")
    await fake_api.push(
        {
            "type": "vision_proposal",
            "event": "updated",
            "mower_id": MOWER_ID,
            "proposal": proposal_summary(done),
        }
    )
    await wait_for(hass, lambda: state_of(hass, SENSOR).state == "proposed")
    await wait_for(hass, lambda: bool(state_of(hass, SENSOR).attributes["steps"]))
    event = state_of(hass, EVENT)
    assert event.attributes["event_type"] == "updated"
    assert event.attributes["state"] == "proposed"
    assert event.attributes["action"] == "drive"
    assert events[1].data["proposal_id"] == pid
    assert events[1].data["mower_id"] == MOWER_ID
    image = await async_get_image(hass, IMAGE)
    assert image.content == JPEG

    # Approved and driven in MowDeck: the steps stay.
    fake_api.proposals[pid] = {**done, "state": "recovered", "final": True}
    await fake_api.push(
        {
            "type": "vision_proposal",
            "event": "updated",
            "mower_id": MOWER_ID,
            "proposal": {**proposal_summary(done), "state": "recovered", "final": True},
        }
    )
    await wait_for(hass, lambda: state_of(hass, SENSOR).state == "recovered")
    # Garbage and other mowers' messages change nothing.
    await fake_api.push({"type": "vision_proposal", "event": "updated", "mower_id": MOWER_ID})
    await fake_api.push(
        {"type": "vision_proposal", "event": "odd", "mower_id": MOWER_ID, "proposal": {"id": pid}}
    )
    await wait_for(hass, lambda: len(events) == 4)
    assert state_of(hass, EVENT).attributes["state"] == "recovered"

    diagnostics = await async_get_config_entry_diagnostics(hass, loaded_entry)
    assert diagnostics["vision"]["proposals"][MOWER_ID]["id"] == pid


@pytest.mark.usefixtures("loaded_entry")
async def test_start_refused(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """MowDeck's refusal (an open proposal, no provider, the quota) reaches the user."""
    fake_api.vision_start_problem = (409, "vision-provider-missing")
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "button", SERVICE_PRESS, {ATTR_ENTITY_ID: BUTTON}, blocking=True
        )
    assert err.value.translation_key == "action_failed"
    assert "vision-provider-missing" in str(err.value.translation_placeholders)


async def test_start_needs_drive_permission(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """Without can_drive the start button is unavailable; a 403 names mower.drive."""
    assert hass.states.get(BUTTON) is not None
    fake_api.relations = {"can_view", "can_control"}
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "button", SERVICE_PRESS, {ATTR_ENTITY_ID: BUTTON}, blocking=True
        )
    assert err.value.translation_placeholders == {
        "command": "start_ai_analysis",
        "permission": "mower.drive",
    }
    coordinator = loaded_entry.runtime_data.coordinator
    coordinator._static_fetched_at.clear()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(BUTTON).state == "unavailable"  # type: ignore[union-attr]


async def test_mowdeck_without_vision(
    hass: HomeAssistant, fake_api: FakeMowDeck, config_entry: MockConfigEntry
) -> None:
    """A MowDeck without the Vision service has no Vision entities and no error."""
    fake_api.vision_enabled = False
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.runtime_data.vision.data.available is False
    for entity_id in (BUTTON, SENSOR, IMAGE, EVENT):
        assert hass.states.get(entity_id) is None
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_unreadable_details_keep_the_summary(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """When the full proposal cannot be read the realtime summary stays."""
    summary = proposal_summary(proposal(id="ghost", state="analysing"))
    await fake_api.push(
        {"type": "vision_proposal", "event": "created", "mower_id": MOWER_ID, "proposal": summary}
    )
    await wait_for(hass, lambda: state_of(hass, SENSOR).state == "analysing")
    await hass.async_block_till_done()
    assert state_of(hass, SENSOR).attributes["proposal_id"] == "ghost"
    assert state_of(hass, SENSOR).attributes["step_count"] == 2


@pytest.mark.usefixtures("loaded_entry")
async def test_logbook(hass: HomeAssistant) -> None:
    """The bus event reads well in the logbook."""
    described: dict[str, Any] = {}

    def register(domain: str, event: str, describe: Any) -> None:
        assert domain == DOMAIN
        described[event] = describe

    logbook.async_describe_events(hass, register)
    entry = described[EVENT_VISION_PROPOSAL](
        Event(EVENT_VISION_PROPOSAL, {"mower_id": MOWER_ID, "state": "proposed", "action": "drive"})
    )
    assert entry == {"name": "Luba", "message": "Vision proposal is proposed (drive)"}
    bare = described[EVENT_VISION_PROPOSAL](Event(EVENT_VISION_PROPOSAL, {"mower_id": MOWER_ID}))
    assert bare["message"] == "Vision proposal is unknown"


@pytest.mark.usefixtures("with_proposal")
async def test_image_fetch_errors(
    hass: HomeAssistant, fake_api: FakeMowDeck, loaded_entry: MockConfigEntry
) -> None:
    """An expired access token is renewed once; server errors and broken connections give no
    picture (and are not cached)."""
    client = loaded_entry.runtime_data.client
    fake_api.expire_access_tokens()
    blob = await client.get_proposal_image(PROPOSAL_ID, 1)
    assert (blob.content, blob.content_type) == (PNG, "image/png")

    fake_api.problem_paths["/api/v1/vision/proposals/"] = 503
    with pytest.raises(HomeAssistantError):
        await async_get_image(hass, IMAGE)
    fake_api.problem_paths.clear()
    fake_api.disconnect_paths.add("/api/v1/vision/proposals/")
    with pytest.raises(HomeAssistantError):
        await async_get_image(hass, IMAGE)
    fake_api.disconnect_paths.clear()
    image = await async_get_image(hass, IMAGE)
    assert image.content == JPEG
