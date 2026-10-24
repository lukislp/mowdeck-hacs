"""Cameras over MowDeck's camera relay: entities, WebRTC signalling and the lease lifecycle."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components.camera import StreamType, async_get_image
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.mowdeck import camera, camera_relay
from custom_components.mowdeck.camera_relay import (
    OfferDescription,
    RelaySession,
    resolve_location,
)

from .conftest import wait_for
from .fake_mowdeck import ANSWER_SDP, MOWER_ID, FakeMowDeck

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16 + b"\xff\xd9"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

CAMERA = "camera.luba_camera"
FRONT_RIGHT = "camera.luba_front_right_camera"

OFFER = (
    "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\na=group:BUNDLE 0 1\r\n"
    "a=ice-ufrag:uf01\r\na=ice-pwd:pw0123456789\r\n"
    "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=mid:0\r\na=recvonly\r\n"
    "m=video 9 UDP/TLS/RTP/SAVPF 96\r\na=mid:1\r\na=recvonly\r\n"
)
CANDIDATE = "candidate:1 1 udp 2122260223 192.168.5.20 50000 typ host"


@pytest.fixture(autouse=True)
def fast_camera() -> Iterator[None]:
    """Short offer retries and heartbeats."""
    with (
        patch.object(camera_relay, "RETRY_FIRST", 0.01),
        patch.object(camera_relay, "RETRY_MAX", 0.02),
        patch.object(camera_relay, "MIN_HEARTBEAT", 0.01),
    ):
        yield


@pytest.fixture
async def camera_entry(
    hass: HomeAssistant, config_entry: MockConfigEntry, fake_api: FakeMowDeck
) -> AsyncIterator[MockConfigEntry]:
    """The entry with a mower whose capabilities name a cloud camera."""
    fake_api.capabilities[MOWER_ID]["camera"] = "cloud"
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    yield config_entry
    if config_entry.state is ConfigEntryState.LOADED:
        await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()


class Stream:
    """One WebRTC stream of the frontend over Home Assistant's WebSocket API."""

    def __init__(self, client: Any, entity_id: str) -> None:
        self.client = client
        self.entity_id = entity_id
        self.subscription = 0
        self.session_id = ""

    async def offer(self, offer: str = OFFER) -> None:
        await self.client.send_json_auto_id(
            {"type": "camera/webrtc/offer", "entity_id": self.entity_id, "offer": offer}
        )
        result = await self.client.receive_json()
        assert result["success"], result
        self.subscription = result["id"]
        event = await self.client.receive_json()
        assert event["event"]["type"] == "session"
        self.session_id = event["event"]["session_id"]

    async def event(self) -> dict[str, Any]:
        message = await self.client.receive_json()
        assert message["id"] == self.subscription, message
        event: dict[str, Any] = message["event"]
        return event

    async def candidate(self, candidate: str, mid: str | None = None, index: int = 1) -> None:
        body: dict[str, Any] = {"candidate": candidate, "sdpMLineIndex": index}
        if mid is not None:
            body["sdpMid"] = mid
        await self.client.send_json_auto_id(
            {
                "type": "camera/webrtc/candidate",
                "entity_id": self.entity_id,
                "session_id": self.session_id,
                "candidate": body,
            }
        )
        result = await self.client.receive_json()
        assert result["success"], result

    async def close(self) -> None:
        await self.client.send_json_auto_id(
            {"type": "unsubscribe_events", "subscription": self.subscription}
        )
        result = await self.client.receive_json()
        assert result["success"], result


def state_of(hass: HomeAssistant, entity_id: str) -> State:
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


async def open_stream(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, entity_id: str = CAMERA
) -> Stream:
    stream = Stream(await hass_ws_client(hass), entity_id)
    await stream.offer()
    return stream


# --- Entities ------------------------------------------------------------------------------------


@pytest.mark.usefixtures("loaded_entry")
async def test_no_camera_without_capability(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """A mower without a camera capability gets no camera entity."""
    assert hass.states.get(CAMERA) is None
    assert not fake_api.lease_history


@pytest.mark.usefixtures("camera_entry")
async def test_camera_entity_without_a_stream(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, fake_api: FakeMowDeck
) -> None:
    """One camera entity per camera-capable mower, WebRTC only, and no lease without a viewer."""
    state = hass.states.get(CAMERA)
    assert state is not None
    assert state.state == "idle"
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "camera/capabilities", "entity_id": CAMERA})
    result = await client.receive_json()
    assert result["result"] == {"frontend_stream_types": [StreamType.WEB_RTC]}
    assert not any("camera/session" in r for r in fake_api.requests)
    assert not fake_api.lease_history


async def test_camera_outlives_a_lost_capability(
    hass: HomeAssistant,
    fake_api: FakeMowDeck,
    camera_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A link that stops naming the camera leaves the entity registered and unavailable; it is
    back with the capability."""
    fake_api.capabilities[MOWER_ID]["camera"] = None
    coordinator = camera_entry.runtime_data.coordinator
    coordinator._static_fetched_at.clear()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert entity_registry.async_get(CAMERA) is not None
    assert state_of(hass, CAMERA).state == "unavailable"

    fake_api.capabilities[MOWER_ID]["camera"] = "cloud"
    coordinator._static_fetched_at.clear()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert state_of(hass, CAMERA).state == "idle"


@pytest.mark.usefixtures("camera_entry")
async def test_no_still_image_before_mowdeck_0_8(
    hass: HomeAssistant, hass_client: Any, fake_api: FakeMowDeck
) -> None:
    """A MowDeck without the snapshot endpoint (404): no picture, asked again only later."""
    client = await hass_client()
    response = await client.get(f"/api/camera_proxy/{CAMERA}")
    assert response.status >= 400
    await client.get(f"/api/camera_proxy/{CAMERA}")
    assert fake_api.snapshot_requests == 1
    assert not fake_api.lease_history


@pytest.mark.usefixtures("camera_entry")
async def test_still_image(hass: HomeAssistant, fake_api: FakeMowDeck) -> None:
    """MowDeck's still image: read once, kept for five minutes, then refreshed in the
    background while the kept one is shown; no stream is opened for it."""
    fake_api.snapshot = (JPEG, "image/jpeg")
    image = await async_get_image(hass, CAMERA)
    assert (image.content, image.content_type) == (JPEG, "image/jpeg")
    await async_get_image(hass, CAMERA)
    assert fake_api.snapshot_requests == 1
    assert not fake_api.lease_history

    fake_api.snapshot = (PNG, "image/png")
    with patch("custom_components.mowdeck.camera.STILL_MAX_AGE", timedelta(0)):
        stale = await async_get_image(hass, CAMERA)
        assert stale.content == JPEG  # the kept image while the new one is fetched
        await wait_for(hass, lambda: fake_api.snapshot_requests == 2)
        await hass.async_block_till_done()
    fresh = await async_get_image(hass, CAMERA)
    assert (fresh.content, fresh.content_type) == (PNG, "image/png")


@pytest.mark.parametrize(
    ("status", "retry"), [(409, "STILL_UNSUPPORTED_RETRY"), (503, "STILL_RETRY")]
)
@pytest.mark.usefixtures("camera_entry")
async def test_still_image_refused(
    hass: HomeAssistant, fake_api: FakeMowDeck, status: int, retry: str
) -> None:
    """A mower that cannot take photos (409) is asked again after an hour, an offline or
    sleeping one (503) after a minute."""
    fake_api.snapshot = (JPEG, "image/jpeg")
    fake_api.snapshot_status = status
    with pytest.raises(HomeAssistantError):
        await async_get_image(hass, CAMERA)
    with pytest.raises(HomeAssistantError):
        await async_get_image(hass, CAMERA)
    assert fake_api.snapshot_requests == 1
    fake_api.snapshot_status = None
    later = dt_util.utcnow() + getattr(camera, retry) + timedelta(seconds=1)
    with patch("custom_components.mowdeck.camera.dt_util.utcnow", return_value=later):
        image = await async_get_image(hass, CAMERA)
    assert image.content == JPEG
    assert fake_api.snapshot_requests == 2


# --- Offer and answer ----------------------------------------------------------------------------


@pytest.mark.usefixtures("camera_entry")
async def test_offer_answer_heartbeat_and_close(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, fake_api: FakeMowDeck
) -> None:
    """The offer opens a relay lease, goes to WHEP with the ticket and its answer comes back;
    the lease is renewed while the stream is open and released with the WHEP session at once
    when the frontend closes it."""
    fake_api.heartbeat_seconds = 0.02
    stream = await open_stream(hass, hass_ws_client)
    event = await stream.event()
    assert event == {"type": "answer", "answer": ANSWER_SDP}

    assert len(fake_api.lease_history) == 1
    lease_id = fake_api.lease_history[0]
    offer = fake_api.whep_offers[0]
    relay_id = fake_api.leases[lease_id]["relay_id"]
    assert offer["path"] == f"/media/whep/r/{relay_id}/0/whep"  # the front left camera
    assert offer["content_type"] == "application/sdp"
    assert offer["offer"] == OFFER
    assert offer["authorization"].startswith("Bearer ticket-")
    assert state_of(hass, CAMERA).state == "streaming"

    await wait_for(hass, lambda: fake_api.renewals >= 2)
    assert list(fake_api.leases) == [lease_id]

    await stream.close()
    await wait_for(hass, lambda: not fake_api.leases)
    assert len(fake_api.whep_deletes) == 1
    assert not fake_api.whep_sessions
    assert state_of(hass, CAMERA).state == "idle"
    renewals = fake_api.renewals
    await wait_for(hass, lambda: True)
    assert fake_api.renewals == renewals


@pytest.mark.usefixtures("camera_entry")
async def test_starting_camera_is_offered_again(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, fake_api: FakeMowDeck
) -> None:
    """A 404 (the camera's source has not published yet) is offered again until it answers."""
    fake_api.whep_starting = 3
    stream = await open_stream(hass, hass_ws_client)
    assert (await stream.event())["type"] == "answer"
    assert len(fake_api.whep_offers) == 4
    assert len(fake_api.lease_history) == 1


@pytest.mark.usefixtures("camera_entry")
async def test_starting_too_long_releases_the_lease(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, fake_api: FakeMowDeck
) -> None:
    """A camera that does not start within the deadline is an error and its lease ends."""
    fake_api.whep_starting = 10_000
    with patch.object(camera_relay, "OFFER_DEADLINE", 0.1):
        stream = await open_stream(hass, hass_ws_client)
        event = await stream.event()
    assert event["type"] == "error"
    assert event["code"] == "webrtc_offer_failed"
    assert "did not start in time" in event["message"]
    assert not fake_api.leases
    assert state_of(hass, CAMERA).state == "idle"


@pytest.mark.usefixtures("camera_entry")
async def test_refused_offer_releases_the_lease(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, fake_api: FakeMowDeck
) -> None:
    """A WHEP refusal (403) is not retried; the lease ends."""
    fake_api.whep_status = 403
    stream = await open_stream(hass, hass_ws_client)
    event = await stream.event()
    assert event["type"] == "error"
    assert len(fake_api.whep_offers) == 1
    assert not fake_api.leases


@pytest.mark.parametrize(
    ("problem", "words"),
    [
        ((409, "camera-relay-unsupported"), "camera relay cannot serve"),
        ((409, "camera-budget"), "camera limit"),
        ((403, "feature-not-in-plan"), "plan does not include"),
        ((503, "service-unavailable"), "not reachable"),
    ],
)
@pytest.mark.usefixtures("camera_entry")
async def test_session_refused(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    fake_api: FakeMowDeck,
    problem: tuple[int, str],
    words: str,
) -> None:
    """A refused camera session reaches the frontend as a readable error."""
    fake_api.camera_problem = problem
    stream = await open_stream(hass, hass_ws_client)
    event = await stream.event()
    assert event["type"] == "error"
    assert words in event["message"]
    assert not fake_api.whep_offers


# --- Candidates ----------------------------------------------------------------------------------


@pytest.mark.usefixtures("camera_entry")
async def test_candidates_are_trickled_to_whep(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, fake_api: FakeMowDeck
) -> None:
    """Candidates that arrive before the answer wait; then each goes to the WHEP session as an
    SDP fragment with the offer's ICE credentials and its media section."""
    fake_api.whep_starting = 2
    stream = await open_stream(hass, hass_ws_client)
    await stream.candidate(CANDIDATE, mid="1")
    assert (await stream.event())["type"] == "answer"
    await wait_for(hass, lambda: len(fake_api.whep_patches) == 1)
    await stream.candidate("candidate:2 1 tcp 1518280447 192.168.5.20 9 typ host", index=1)
    await stream.candidate("")  # end of candidates: nothing to send
    await wait_for(hass, lambda: len(fake_api.whep_patches) == 2)

    first = fake_api.whep_patches[0]
    assert first["content_type"] == "application/trickle-ice-sdpfrag"
    assert first["authorization"].startswith("Bearer ticket-")
    assert first["fragment"] == (
        "a=ice-ufrag:uf01\r\na=ice-pwd:pw0123456789\r\n"
        f"m=video 9 UDP/TLS/RTP/SAVPF 96\r\na=mid:1\r\na={CANDIDATE}\r\n"
    )
    assert "typ host" in fake_api.whep_patches[1]["fragment"]
    assert "a=mid:1" in fake_api.whep_patches[1]["fragment"]
    await stream.close()


# --- Lease loss and cleanup ----------------------------------------------------------------------


@pytest.mark.usefixtures("camera_entry")
async def test_lost_lease_ends_the_stream(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, fake_api: FakeMowDeck
) -> None:
    """A heartbeat answered 404 (the lease is gone) ends the stream with an error; a new offer
    opens a new lease."""
    fake_api.heartbeat_seconds = 0.02
    stream = await open_stream(hass, hass_ws_client)
    assert (await stream.event())["type"] == "answer"
    fake_api.drop_lease(fake_api.lease_history[0])
    event = await stream.event()
    assert event["type"] == "error"
    assert "ended on the MowDeck side" in event["message"]
    assert state_of(hass, CAMERA).state == "idle"

    again = await open_stream(hass, hass_ws_client)
    assert (await again.event())["type"] == "answer"
    assert len(fake_api.lease_history) == 2
    await again.close()
    await wait_for(hass, lambda: not fake_api.leases)


@pytest.mark.usefixtures("camera_entry")
async def test_close_while_starting_releases_everything(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, fake_api: FakeMowDeck
) -> None:
    """Closing the stream while the camera still starts ends the offer and the lease."""
    fake_api.whep_starting = 10_000
    stream = await open_stream(hass, hass_ws_client)
    await wait_for(hass, lambda: len(fake_api.whep_offers) >= 2)
    await stream.close()
    await wait_for(hass, lambda: not fake_api.leases)
    offers = len(fake_api.whep_offers)
    await wait_for(hass, lambda: True)
    assert len(fake_api.whep_offers) <= offers + 1
    assert state_of(hass, CAMERA).state == "idle"


async def test_unload_releases_open_streams(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    fake_api: FakeMowDeck,
    camera_entry: MockConfigEntry,
) -> None:
    """Unloading the entry releases every open stream."""
    stream = await open_stream(hass, hass_ws_client)
    assert (await stream.event())["type"] == "answer"
    assert fake_api.leases
    assert await hass.config_entries.async_unload(camera_entry.entry_id)
    await hass.async_block_till_done()
    await wait_for(hass, lambda: not fake_api.leases)
    assert fake_api.whep_deletes


# --- Further cameras -----------------------------------------------------------------------------


async def test_further_cameras_after_the_first_session(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    fake_api: FakeMowDeck,
    camera_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """The first session lists the cameras: a further one that delivers gets an entity (a
    silent one does not), it plays its own camera, and it comes back after a restart."""
    stream = await open_stream(hass, hass_ws_client)
    assert (await stream.event())["type"] == "answer"
    await stream.close()
    await wait_for(hass, lambda: hass.states.get(FRONT_RIGHT) is not None)
    assert hass.states.get("camera.luba_rear_camera") is None
    assert state_of(hass, CAMERA).attributes["camera_id"] == "0"
    assert state_of(hass, FRONT_RIGHT).attributes["placement"] == "front_right"
    entry = entity_registry.async_get(FRONT_RIGHT)
    assert entry is not None
    assert entry.unique_id == f"{MOWER_ID}_camera_1"

    right = await open_stream(hass, hass_ws_client, FRONT_RIGHT)
    assert (await right.event())["type"] == "answer"
    relay_id = fake_api.leases[fake_api.lease_history[-1]]["relay_id"]
    assert fake_api.whep_offers[-1]["path"] == f"/media/whep/r/{relay_id}/1/whep"
    await right.close()
    await wait_for(hass, lambda: not fake_api.leases)

    # The relay now reports the front right camera as silent: unavailable, not removed.
    fake_api.camera_available["1"] = False
    again = await open_stream(hass, hass_ws_client)
    assert (await again.event())["type"] == "answer"
    await again.close()
    await wait_for(hass, lambda: state_of(hass, FRONT_RIGHT).state == "unavailable")
    fake_api.camera_available["1"] = True

    # Kept in storage: the entity is back after a reload, before any session.
    await hass.config_entries.async_reload(camera_entry.entry_id)
    await hass.async_block_till_done()
    sessions = len(fake_api.lease_history)
    assert hass.states.get(FRONT_RIGHT) is not None
    assert len(fake_api.lease_history) == sessions


# --- Pure helpers --------------------------------------------------------------------------------


def test_resolve_location() -> None:
    """The WHEP session URL gets the gateway's prefix back, like the web app's resolver."""
    endpoint = "https://mow.example/media/whep/r/abc/0/whep"
    assert (
        resolve_location("/r/abc/0/whep/s1", endpoint)
        == "https://mow.example/media/whep/r/abc/0/whep/s1"
    )
    assert resolve_location("https://other/x", endpoint) == "https://other/x"
    assert resolve_location("s2", endpoint) == "https://mow.example/media/whep/r/abc/0/s2"
    assert resolve_location("/elsewhere", endpoint) == "https://mow.example/elsewhere"


def test_session_tile_choice() -> None:
    """Without a camera the first one known to deliver, else the first not known silent."""

    def session(available: list[bool | None]) -> RelaySession:
        return RelaySession.parse(
            {
                "provider": "relay",
                "cameras": [{"id": str(i), "name": f"C{i}"} for i in range(len(available))],
                "relay": {
                    "lease_id": "l",
                    "ticket": "t",
                    "tiles": [
                        {"camera_id": str(i), "whep_url": f"/w/{i}", "available": a}
                        for i, a in enumerate(available)
                    ],
                },
            }
        )

    assert session([False, None, True]).tile_for(None).camera_id == "2"  # type: ignore[union-attr]
    assert session([False, None, None]).tile_for(None).camera_id == "1"  # type: ignore[union-attr]
    assert session([False, False]).tile_for(None).camera_id == "0"  # type: ignore[union-attr]
    assert session([True]).tile_for("9") is None
    assert repr(session([True])).count("ticket") == 0


def test_session_must_be_relay() -> None:
    """A direct (vendor) session cannot be played here."""
    with pytest.raises(camera_relay.CameraRelayError) as err:
        RelaySession.parse({"provider": "agora", "relay": None})
    assert err.value.reason == "relay_unsupported"


def test_fragment_without_media_or_candidates() -> None:
    """No fragment without a usable candidate; a candidate without mid uses its m-line."""
    offer = OfferDescription.parse(OFFER)
    assert offer.fragment([]) is None
    candidate = type("C", (), {"candidate": CANDIDATE, "sdp_mid": None, "sdp_m_line_index": 0})
    fragment = offer.fragment([candidate()])
    assert fragment is not None
    assert "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=mid:0\r\n" in fragment


@pytest.mark.usefixtures("camera_entry")
async def test_heartbeat_survives_a_short_outage(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, fake_api: FakeMowDeck
) -> None:
    """A renewal that fails for a moment (503) is tried again at the next beat."""
    fake_api.heartbeat_seconds = 0.02
    stream = await open_stream(hass, hass_ws_client)
    assert (await stream.event())["type"] == "answer"
    fake_api.fail_requests = 2
    await wait_for(hass, lambda: fake_api.fail_requests == 0)
    renewals = fake_api.renewals
    await wait_for(hass, lambda: fake_api.renewals > renewals)
    assert state_of(hass, CAMERA).state == "streaming"
    await stream.close()
    await wait_for(hass, lambda: not fake_api.leases)


async def test_removing_the_entry_forgets_the_cameras(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    hass_storage: dict[str, Any],
    fake_api: FakeMowDeck,
    camera_entry: MockConfigEntry,
) -> None:
    """The stored camera list goes with the entry."""
    stream = await open_stream(hass, hass_ws_client)
    assert (await stream.event())["type"] == "answer"
    await stream.close()
    await wait_for(hass, lambda: not fake_api.leases)
    key = f"mowdeck.{camera_entry.entry_id}.cameras"
    await hass.config_entries.async_unload(camera_entry.entry_id)
    await hass.async_block_till_done()
    assert hass_storage[key]["data"]["mowers"][MOWER_ID]["primary"] == "0"
    assert "ticket" not in str(hass_storage[key])
    await hass.config_entries.async_remove(camera_entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage
