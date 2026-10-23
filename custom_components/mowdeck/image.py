"""Image entities of a MowDeck mower: the pictures of its newest Vision (AI assist) proposal.

``image.<mower>_ai_recovery_image`` is the camera frame the model saw (the first one, the left
camera of a stereo pair), ``image.<mower>_ai_recovery_map`` the top-down map rendering Vision
adds (disabled by default). Both come from ``GET /vision/proposals/{id}/images/{index}`` and
are fetched once per proposal; MowDeck purges them after the proposal's TTL.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.image import ImageEntity, ImageEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .api import MowDeckError, MowDeckNotFoundError
from .entity import MowDeckEntity, async_add_mower_entities
from .vision import CAMERA_SOURCES, MAP_SOURCE, VisionCoordinator, frame_index

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator

PARALLEL_UPDATES = 0

FRAME = ImageEntityDescription(key="ai_recovery_image")
MAP_FRAME = ImageEntityDescription(key="ai_recovery_map", entity_registry_enabled_default=False)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MowDeckConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """The proposal images of every mower, where MowDeck runs Vision."""
    vision = entry.runtime_data.vision
    async_add_mower_entities(
        entry,
        async_add_entities,
        lambda coordinator, m: (
            [
                VisionImage(coordinator, m, vision, FRAME, CAMERA_SOURCES),
                VisionImage(coordinator, m, vision, MAP_FRAME, (MAP_SOURCE,)),
            ]
            if vision.data.available
            else []
        ),
        fingerprint=lambda m: vision.data.available,
        listen=[vision],
    )


class VisionImage(MowDeckEntity, ImageEntity):
    """One picture of the mower's newest proposal; unknown while there is none."""

    _attr_content_type = "image/jpeg"

    def __init__(
        self,
        coordinator: MowDeckCoordinator,
        mower_id: str,
        vision: VisionCoordinator,
        description: ImageEntityDescription,
        sources: tuple[str, ...],
    ) -> None:
        """Create the image of the frames from ``sources``."""
        super().__init__(coordinator, mower_id, description.key, service=vision)
        ImageEntity.__init__(self, coordinator.hass)
        self.entity_description = description
        self._vision = vision
        self._sources = sources
        self._shown: tuple[str, int] | None = None
        self._cached: tuple[tuple[str, int], bytes] | None = None
        self._follow()

    def _frame(self) -> tuple[str, int] | None:
        """``(proposal id, image index)`` of the picture to show, if any."""
        proposal = self._vision.proposal(self.mower_id)
        index = frame_index(proposal, *self._sources)
        if index is None or not proposal.get("id"):
            return None
        return str(proposal["id"]), index

    def _follow(self) -> None:
        frame = self._frame()
        if frame == self._shown:
            return
        self._shown = frame
        self._cached = None
        if frame is None:
            self._attr_image_last_updated = None
            return
        proposal = self._vision.proposal(self.mower_id)
        created = dt_util.parse_datetime(str(proposal.get("analysed_at") or ""))
        self._attr_image_last_updated = created or dt_util.utcnow()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Show the picture of a new proposal."""
        self._follow()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        """The picture, read once per proposal; None once MowDeck purged it."""
        frame = self._shown
        if frame is None:
            return None
        if self._cached is not None and self._cached[0] == frame:
            return self._cached[1]
        try:
            blob = await self.coordinator.client.get_proposal_image(*frame)
        except MowDeckNotFoundError:
            return None
        except MowDeckError as err:
            self.coordinator.logger.debug("Vision image %s not readable: %s", frame, err)
            return None
        if blob.content_type.startswith("image/"):
            self._attr_content_type = blob.content_type
        self._cached = (frame, blob.content)
        return blob.content
