"""WebRTC signalling for MowDeck's camera relay (ADR 0045 in lukislp/mowdeck).

Home Assistant's frontend makes the WebRTC offer; this module is the WHEP client in between,
the same way the MowDeck web app plays a relay camera (``apps/web/src/lib/camera``):

1. Open a viewer lease of the API token: ``POST /fleet/mowers/{id}/camera/session`` with
   ``transport`` ``relay``. The answer names a WHEP URL per camera, a ticket (about 60 s) and the
   lease with its heartbeat.
2. POST the browser's offer to the camera's WHEP URL with the ticket as bearer token. A 404 means
   that the camera's source has not published yet (the mower starts its stream); 408, 429, 5xx
   and connection errors are a media plane that is away for a moment. Both are offered again
   with a growing delay (0.5 s to 3 s) until :data:`OFFER_DEADLINE` has passed. Any other 4xx
   is a refusal. The ``201`` answer goes back to the browser; its ``Location`` names the WHEP
   session (the media server sits below ``/media/whep``, the prefix is put back).
3. Browser ICE candidates are sent to the WHEP session with ``PATCH``
   (``application/trickle-ice-sdpfrag``); candidates that arrive before the answer wait.
4. While Home Assistant keeps the WebRTC session open, the lease is renewed every
   ``heartbeat_seconds``; each renewal brings a fresh ticket, so every offer and PATCH uses a
   valid one. A renewal answered 404 means that the lease is gone (it expired, the relay
   restarted): the picture cannot continue on it, the viewer reports the loss and ends.
5. Closing (the frontend closes the stream, the dashboard is left, the browser goes away, the
   entity is removed) deletes the WHEP session and the lease at once, so the relay stops the
   mower's stream after its grace (15 s). Nothing here opens a lease without an offer.

The media itself never passes Home Assistant: the browser connects over ICE to the relay's media
port (``RELAY_ICE_HOSTS``, UDP/TCP 8189), exactly like the MowDeck web app.

Tickets and access tokens are secrets: they are never logged and never stored.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

from .api import (
    MowDeckApiError,
    MowDeckClient,
    MowDeckConnectionError,
    MowDeckError,
    MowDeckForbiddenError,
    MowDeckNotFoundError,
)

_LOGGER = logging.getLogger(__name__)

# How long an offer may wait for a starting camera (the relay's start timeout is 30 s; a
# LUBA's first key frame can take a few seconds more).
OFFER_DEADLINE = 40.0
# Retry delays of an offer that was answered "later" (the web app's whepRetryDelayMs).
RETRY_FIRST = 0.5
RETRY_MAX = 3.0
RETRY_FACTOR = 1.5
# Never renew more often than this, whatever the session says.
MIN_HEARTBEAT = 5.0
DEFAULT_HEARTBEAT = 15.0
# Best-effort cleanup requests (WHEP DELETE, lease DELETE) give up after this.
CLEANUP_TIMEOUT = 10.0

SDP = "application/sdp"
SDP_FRAGMENT = "application/trickle-ice-sdpfrag"

# Statuses of a WHEP offer that mean "offer again later" (the web app's rule).
_LATER = frozenset({404, 408, 429})


class CameraRelayError(MowDeckError):
    """The camera could not be played; ``reason`` names why (a translation key)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        """Keep the reason for the user message."""
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True, slots=True)
class RelayTile:
    """One camera of a relay session."""

    camera_id: str
    whep_url: str
    available: bool | None


@dataclass(frozen=True, slots=True)
class CameraDescription:
    """A camera of the mower as the session lists it (no secrets)."""

    camera_id: str
    name: str
    placement: str | None
    available: bool | None


@dataclass(frozen=True, slots=True)
class RelaySession:
    """The parts of a relay ``CameraSession`` the viewer needs; ``ticket`` is secret."""

    lease_id: str
    ticket: str = field(repr=False)
    heartbeat_seconds: float
    tiles: tuple[RelayTile, ...]
    cameras: tuple[CameraDescription, ...]

    @classmethod
    def parse(cls, body: Mapping[str, Any]) -> RelaySession:
        """Read a ``CameraSession`` of provider ``relay``; anything else is refused."""
        relay = body.get("relay")
        if body.get("provider") != "relay" or not isinstance(relay, Mapping):
            raise CameraRelayError("relay_unsupported", "the session is not a relay session")
        lease_id = relay.get("lease_id")
        ticket = relay.get("ticket")
        if not isinstance(lease_id, str) or not isinstance(ticket, str) or not ticket:
            raise CameraRelayError("failed", "the relay session has no lease or ticket")
        tiles = tuple(
            RelayTile(
                camera_id=str(tile["camera_id"]),
                whep_url=str(tile["whep_url"]),
                available=_flag(tile.get("available")),
            )
            for tile in relay.get("tiles") or []
            if isinstance(tile, Mapping) and tile.get("camera_id") and tile.get("whep_url")
        )
        available = {tile.camera_id: tile.available for tile in tiles}
        cameras = tuple(
            CameraDescription(
                camera_id=str(camera["id"]),
                name=str(camera.get("name") or camera["id"]),
                placement=str(camera["placement"]) if camera.get("placement") else None,
                available=available.get(str(camera["id"])),
            )
            for camera in body.get("cameras") or []
            if isinstance(camera, Mapping) and camera.get("id") and str(camera["id"]) in available
        )
        heartbeat = relay.get("heartbeat_seconds")
        return cls(
            lease_id=lease_id,
            ticket=ticket,
            heartbeat_seconds=(
                float(heartbeat) if isinstance(heartbeat, int | float) else DEFAULT_HEARTBEAT
            ),
            tiles=tiles,
            cameras=cameras,
        )

    def tile_for(self, camera_id: str | None) -> RelayTile | None:
        """The tile of ``camera_id``; without one the web app's default: the first camera known
        to deliver, else the first not known to be silent, else the first."""
        if camera_id is not None:
            return next((t for t in self.tiles if t.camera_id == camera_id), None)
        return (
            next((t for t in self.tiles if t.available is True), None)
            or next((t for t in self.tiles if t.available is not False), None)
            or (self.tiles[0] if self.tiles else None)
        )


def _flag(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def resolve_location(location: str, endpoint: str) -> str:
    """The WHEP session URL from an answer's ``Location`` (the web app's ``resolveLocation``).

    An absolute URL stays. A root-relative one that repeats the end of the endpoint's path
    (MediaMTX behind the gateway's prefix: endpoint ``/media/whep/r/a/cam/whep``, Location
    ``/r/a/cam/whep/s``) gets the endpoint's prefix back; anything else resolves like a link.
    """
    if urlsplit(location).scheme:
        return location
    if location.startswith("/") and not location.startswith("//"):
        path = urlsplit(endpoint).path
        for at, char in enumerate(path):
            if char != "/":
                continue
            tail = path[at:]
            prefix = tail if tail.endswith("/") else f"{tail}/"
            if location == tail or location.startswith(prefix):
                return urljoin(endpoint, path[:at] + location)
    return urljoin(endpoint, location)


@dataclass(frozen=True, slots=True)
class OfferDescription:
    """What a trickle ICE fragment needs from the offer: ICE credentials and the m-lines."""

    ice_ufrag: str | None
    ice_pwd: str | None
    medias: tuple[tuple[str, str], ...]
    """``(m-line without "m=", mid)`` per media section, in order."""

    @classmethod
    def parse(cls, sdp: str) -> OfferDescription:
        """Read the offer's ``ice-ufrag``, ``ice-pwd``, m-lines and mids."""
        ufrag = pwd = None
        medias: list[list[str]] = []
        for raw in sdp.splitlines():
            line = raw.strip()
            if line.startswith("m="):
                medias.append([line[2:], str(len(medias))])
            elif line.startswith("a=mid:") and medias:
                medias[-1][1] = line[6:]
            elif line.startswith("a=ice-ufrag:") and ufrag is None:
                ufrag = line[12:]
            elif line.startswith("a=ice-pwd:") and pwd is None:
                pwd = line[10:]
        return cls(ufrag, pwd, tuple((m, mid) for m, mid in medias))

    def fragment(self, candidates: Sequence[Any]) -> str | None:
        """An SDP fragment (RFC 8840) with ``candidates`` grouped by their media section, as
        MediaMTX's own WHEP reader builds it; None when no candidate fits a section."""
        by_mid: dict[str, list[str]] = {}
        for candidate in candidates:
            text = str(getattr(candidate, "candidate", "") or "").strip()
            if not text:
                continue  # end of candidates
            mid = getattr(candidate, "sdp_mid", None)
            index = getattr(candidate, "sdp_m_line_index", None)
            if mid is None and isinstance(index, int) and 0 <= index < len(self.medias):
                mid = self.medias[index][1]
            if mid is None and self.medias:
                mid = self.medias[0][1]
            if mid is None:
                continue
            line = text if text.startswith("a=") else f"a={text}"
            by_mid.setdefault(str(mid), []).append(line)
        if not by_mid:
            return None
        lines = []
        if self.ice_ufrag:
            lines.append(f"a=ice-ufrag:{self.ice_ufrag}")
        if self.ice_pwd:
            lines.append(f"a=ice-pwd:{self.ice_pwd}")
        for media, mid in self.medias:
            if mid in by_mid:
                lines.extend((f"m={media}", f"a=mid:{mid}", *by_mid[mid]))
        return "\r\n".join(lines) + "\r\n"


def _reason_of(err: MowDeckError) -> str:
    """The translation key of a failed session request."""
    if isinstance(err, CameraRelayError):
        return err.reason
    if isinstance(err, MowDeckForbiddenError):
        return "camera_forbidden"
    if isinstance(err, MowDeckNotFoundError):
        return "camera_not_found"
    if isinstance(err, MowDeckApiError):
        slug = (err.problem.type or "").rsplit(":", 1)[-1]
        return {
            "camera-relay-unsupported": "relay_unsupported",
            "camera-unsupported": "camera_unsupported",
            "camera-budget": "camera_budget",
        }.get(slug, "failed")
    return "unavailable"


class RelayViewer:
    """One Home Assistant WebRTC session of one camera: a lease, a WHEP session, a heartbeat.

    ``on_session`` sees every session the relay answers (for the camera list); ``on_lost`` is
    called once when the lease was lost after the answer. Both are plain callbacks.
    """

    def __init__(
        self,
        client: MowDeckClient,
        mower_id: str,
        camera_id: str | None,
        *,
        fallback: bool = False,
        on_session: Callable[[RelaySession], None] | None = None,
        on_lost: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create the viewer; nothing happens before :meth:`async_offer`."""
        self._client = client
        self.mower_id = mower_id
        self.camera_id = camera_id
        self._fallback = fallback
        self._on_session = on_session
        self._on_lost = on_lost
        self._clock = clock
        self._session: RelaySession | None = None
        self._offer: OfferDescription | None = None
        self._location: str | None = None
        self._pending: list[Any] = []
        self._heartbeat: asyncio.Task[None] | None = None
        self._closed = asyncio.Event()
        self._torn_down = False
        self._cleanup: asyncio.Task[None] | None = None

    @property
    def lease_id(self) -> str | None:
        """The lease this viewer holds (None before it was opened)."""
        return self._session.lease_id if self._session else None

    @property
    def closed(self) -> bool:
        """The viewer was closed or lost its lease."""
        return self._closed.is_set()

    def _tile(self, session: RelaySession) -> RelayTile | None:
        tile = session.tile_for(self.camera_id)
        if tile is None and self._fallback:
            tile = session.tile_for(None)
        return tile

    # --- Offer ---------------------------------------------------------------------------------

    async def async_offer(self, offer_sdp: str) -> str:
        """Open the lease, offer to WHEP and return the SDP answer.

        Raises :class:`CameraRelayError` (with a reason) when the camera cannot be played; the
        lease is released before.
        """
        self._offer = OfferDescription.parse(offer_sdp)
        try:
            body = await self._client.open_camera_session(self.mower_id)
            session = RelaySession.parse(body)
        except MowDeckError as err:
            _LOGGER.debug("Camera session of %s refused: %s", self.mower_id, err)
            raise CameraRelayError(_reason_of(err), str(err)) from err
        self._session = session
        if self.closed:
            await self._teardown()
            raise CameraRelayError("closed")
        self._notify(session)
        self._heartbeat = asyncio.create_task(self._beat(), name="mowdeck camera heartbeat")
        try:
            tile = self._tile(session)
            if tile is None:
                raise CameraRelayError("camera_missing", f"camera {self.camera_id} not offered")
            answer = await self._post_offer(tile, offer_sdp)
        except BaseException:
            await self.async_close()
            raise
        await self._flush_candidates()
        return answer

    async def _post_offer(self, tile: RelayTile, offer_sdp: str) -> str:
        endpoint = self._client.media_url(tile.whep_url)
        deadline = self._clock() + OFFER_DEADLINE
        delay = RETRY_FIRST
        last = "no answer"
        while True:
            if self.closed:
                raise CameraRelayError("closed")
            session = self._session
            assert session is not None  # noqa: S101 - set before the offer
            try:
                response = await self._client.whep_request(
                    "POST", endpoint, session.ticket, body=offer_sdp, content_type=SDP
                )
            except MowDeckConnectionError as err:
                status, last = 0, str(err)
            else:
                status = response.status
                if status == 201 and response.body:
                    location = (
                        resolve_location(response.location, endpoint) if response.location else None
                    )
                    if self.closed:
                        await self._delete_whep(location)
                        raise CameraRelayError("closed")
                    self._location = location
                    return response.body
                if 400 <= status < 500 and status not in _LATER:
                    raise CameraRelayError("refused", f"WHEP offer refused with HTTP {status}")
                last = f"HTTP {status}"
            if self._clock() + delay > deadline:
                reason = "starting_timeout" if status == 404 else "unavailable"
                raise CameraRelayError(reason, f"no WHEP answer in time ({last})")
            _LOGGER.debug("WHEP offer of %s: %s, again in %ss", self.mower_id, last, delay)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._closed.wait(), delay)
            delay = min(RETRY_MAX, delay * RETRY_FACTOR)

    # --- Candidates ----------------------------------------------------------------------------

    async def async_add_candidate(self, candidate: Any) -> None:
        """Forward a browser ICE candidate (queued until the WHEP session exists)."""
        if self.closed:
            return
        self._pending.append(candidate)
        if self._location is not None:
            await self._flush_candidates()

    async def _flush_candidates(self) -> None:
        if self._location is None or self._offer is None or self._session is None:
            return
        candidates, self._pending = self._pending, []
        fragment = self._offer.fragment(candidates)
        if fragment is None:
            return
        try:
            response = await self._client.whep_request(
                "PATCH",
                self._location,
                self._session.ticket,
                body=fragment,
                content_type=SDP_FRAGMENT,
            )
        except MowDeckConnectionError as err:
            _LOGGER.debug("ICE candidates of %s not sent: %s", self.mower_id, err)
            return
        if response.status >= 300:
            # Not fatal: the media server learns the browser's address from its ICE checks.
            _LOGGER.debug("ICE candidates of %s: HTTP %s", self.mower_id, response.status)

    # --- Heartbeat -----------------------------------------------------------------------------

    async def _beat(self) -> None:
        while not self.closed:
            session = self._session
            assert session is not None  # noqa: S101 - started after the session
            interval = max(MIN_HEARTBEAT, session.heartbeat_seconds)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._closed.wait(), interval)
            if self.closed:
                return
            try:
                fresh = RelaySession.parse(
                    await self._client.renew_camera_session(self.mower_id, session.lease_id)
                )
            except MowDeckNotFoundError:
                _LOGGER.info("Camera lease of %s is gone; the stream ends", self.mower_id)
                await self._lose("lease_lost")
                return
            except (MowDeckApiError, CameraRelayError) as err:
                _LOGGER.warning("Camera lease of %s refused its renewal: %s", self.mower_id, err)
                await self._lose("lease_lost")
                return
            except MowDeckError as err:
                # A moment without the API: the lease lives 45 s, the next beat tries again.
                _LOGGER.debug("Camera heartbeat of %s failed: %s", self.mower_id, err)
                continue
            if fresh.lease_id != session.lease_id:  # pragma: no cover - the API keeps it
                await self._lose("lease_lost")
                return
            self._session = fresh
            self._notify(fresh)

    async def _lose(self, reason: str) -> None:
        self._closed.set()
        lost = self._on_lost
        self._on_lost = None
        await self._teardown(detach=False)
        if lost is not None:
            lost(reason)

    def _notify(self, session: RelaySession) -> None:
        if self._on_session is None:
            return
        try:
            self._on_session(session)
        except Exception:  # pragma: no cover - a listener bug never ends the stream
            _LOGGER.exception("Camera session listener failed")

    # --- Close ---------------------------------------------------------------------------------

    async def async_close(self) -> None:
        """End the viewer: delete the WHEP session and the lease. Idempotent; finishes even
        when the caller is cancelled."""
        self._closed.set()
        self._on_lost = None
        if self._cleanup is None or (self._cleanup.done() and not self._torn_down):
            self._cleanup = asyncio.create_task(self._teardown(), name="mowdeck camera close")
        await asyncio.shield(self._cleanup)

    async def _teardown(self, *, detach: bool = True) -> None:
        self._closed.set()
        if self._torn_down or self._session is None:
            # Without a session there is nothing to end yet; an offer whose session arrives
            # after the close tears down itself.
            return
        self._torn_down = True
        heartbeat, self._heartbeat = self._heartbeat, None
        if heartbeat is not None and heartbeat is not asyncio.current_task():
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        location, self._location = self._location, None
        await self._delete_whep(location)
        session = self._session
        if session is not None and detach:
            try:
                async with asyncio.timeout(CLEANUP_TIMEOUT):
                    await self._client.close_camera_session(self.mower_id, session.lease_id)
            except (MowDeckError, TimeoutError) as err:
                # The relay ends a lease without heartbeat after 45 s anyway.
                _LOGGER.debug("Camera lease of %s not released: %s", self.mower_id, err)
        self._pending.clear()

    async def _delete_whep(self, location: str | None) -> None:
        session = self._session
        if location is None or session is None:
            return
        try:
            async with asyncio.timeout(CLEANUP_TIMEOUT):
                await self._client.whep_request("DELETE", location, session.ticket)
        except (MowDeckError, TimeoutError) as err:
            # The relay notices a reader that is gone by itself.
            _LOGGER.debug("WHEP session of %s not deleted: %s", self.mower_id, err)
