"""MowDeck Vision (AI assist) proposals in their own coordinator.

Home Assistant only starts an analysis and shows what MowDeck proposes; approving or rejecting
a proposal stays in MowDeck (the ``link`` of a proposal opens it there). The newest proposal of
every mower is read at setup and every ``VISION_REFRESH``; changes arrive live as Realtime
``vision_proposal`` messages (``created``, ``updated``), after which the full proposal (frames,
assessment, steps) is read again. A MowDeck without the Vision service (404) simply has no
Vision entities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .api import MowDeckError, MowDeckNotFoundError
from .const import EVENT_VISION_PROPOSAL, VISION_REFRESH
from .coordinator import MowDeckCoordinator, MowDeckServiceCoordinator

if TYPE_CHECKING:
    from . import MowDeckConfigEntry

_LOGGER = logging.getLogger(__name__)

# ProposalState (apps/web/openapi/vision.json).
PROPOSAL_STATES = [
    "analysing",
    "proposed",
    "rejected_by_ai",
    "failed",
    "rejected",
    "expired",
    "executing",
    "verifying",
    "recovered",
    "not_recovered",
    "aborted",
]
# The realtime events of a proposal: the event types of the Vision event entity.
PROPOSAL_EVENTS = ["created", "updated"]
# Fields of the realtime summary that also mean the same in the full proposal.
SUMMARY_KEYS = ("state", "final", "action", "fallback", "round", "expires_at", "link", "updated_at")
# Where an image came from (FrameOut.source): the camera frames come before the map rendering.
CAMERA_SOURCES = ("mower", "upload")
MAP_SOURCE = "map"


def vision_signal(entry_id: str, mower_id: str) -> str:
    """Dispatcher signal of a mower's proposal events."""
    return f"mowdeck_vision_{entry_id}_{mower_id}"


def frame_index(proposal: dict[str, Any], *sources: str) -> int | None:
    """Index of the first image of the proposal from one of ``sources`` (camera frames are the
    main picture, the map rendering the second); None without such an image."""
    for frame in proposal.get("frames") or []:
        if isinstance(frame, dict) and frame.get("source") in sources:
            index = frame.get("index")
            if isinstance(index, int):
                return index
    return None


@dataclass
class VisionData:
    """The newest proposal per mower."""

    # False when this MowDeck runs no Vision service (the API answered 404).
    available: bool = True
    proposals: dict[str, dict[str, Any]] = field(default_factory=dict)


class VisionCoordinator(MowDeckServiceCoordinator[VisionData]):
    """Reads the newest proposal of every mower and follows the realtime proposal events."""

    service = "vision"
    permission = "mower.view"

    def __init__(
        self, hass: HomeAssistant, entry: MowDeckConfigEntry, state: MowDeckCoordinator
    ) -> None:
        """Create the coordinator (refreshed every ``VISION_REFRESH``)."""
        super().__init__(hass, entry, state, update_interval=VISION_REFRESH)
        self.data = VisionData()

    async def _async_fetch(self) -> VisionData:
        data = VisionData()
        for mower_id in list(self.state.data.mowers):
            try:
                latest = await self.client.latest_proposal(mower_id)
            except MowDeckNotFoundError:
                return VisionData(available=False)
            if latest is not None:
                data.proposals[mower_id] = await self._async_details(latest)
        return data

    async def _async_details(self, proposal: dict[str, Any]) -> dict[str, Any]:
        """The full proposal (frames, assessment, steps); the summary when it cannot be read."""
        try:
            return await self.client.get_proposal(str(proposal["id"]))
        except MowDeckError as err:
            _LOGGER.debug("Proposal %s not readable: %s", proposal.get("id"), err)
            return proposal

    @callback
    def async_push(self, message: dict[str, Any]) -> None:
        """A ``vision_proposal`` message of the Realtime WebSocket: show the summary at once,
        fire the events, then read the full proposal."""
        proposal = message.get("proposal")
        mower_id = message.get("mower_id")
        if (
            not isinstance(proposal, dict)
            or not proposal.get("id")
            or not isinstance(mower_id, str)
        ):
            return
        current = self.data.proposals.get(mower_id) or {}
        merged = {**current, **proposal} if current.get("id") == proposal["id"] else dict(proposal)
        self.data.proposals[mower_id] = merged
        event = message.get("event")
        payload = {
            "config_entry_id": self.config_entry.entry_id,
            "mower_id": mower_id,
            "event": event,
            "proposal_id": str(proposal["id"]),
            "state": proposal.get("state"),
            "action": proposal.get("action"),
            "final": proposal.get("final"),
            "link": self.absolute_link(proposal),
        }
        self.hass.bus.async_fire(EVENT_VISION_PROPOSAL, payload)
        async_dispatcher_send(
            self.hass, vision_signal(self.config_entry.entry_id, mower_id), payload
        )
        self.async_update_listeners()
        self.config_entry.async_create_background_task(
            self.hass, self._async_refresh_proposal(mower_id, merged), "mowdeck vision proposal"
        )

    async def _async_refresh_proposal(self, mower_id: str, summary: dict[str, Any]) -> None:
        details = await self._async_details(summary)
        current = self.data.proposals.get(mower_id) or {}
        if current.get("id") != summary.get("id"):
            return
        # A realtime summary as new as the details (or newer, arrived meanwhile) wins for the
        # fields it carries.
        if str(details.get("updated_at") or "") <= str(current.get("updated_at") or ""):
            details = {**details, **{k: current[k] for k in SUMMARY_KEYS if k in current}}
        self.data.proposals[mower_id] = details
        self.async_update_listeners()

    @callback
    def async_set_proposal(self, mower_id: str, proposal: dict[str, Any]) -> None:
        """A proposal an action answered (the start of an analysis)."""
        self.data.proposals[mower_id] = dict(proposal)
        self.async_update_listeners()

    def proposal(self, mower_id: str) -> dict[str, Any]:
        """The mower's newest proposal (empty without one)."""
        return self.data.proposals.get(mower_id) or {}

    def absolute_link(self, proposal: dict[str, Any]) -> str | None:
        """The proposal's page in the MowDeck web app (where it is approved or rejected)."""
        link = proposal.get("link")
        if not isinstance(link, str) or not link:
            return None
        if link.startswith("/") and not link.startswith("//"):
            return f"{self.client.base_url}{link}"
        return link if link.startswith(("http://", "https://")) else None
