"""Image entities of a MowDeck mower: its map, its heatmap layers and the pictures of its
newest Vision (AI assist) proposal.

``image.<mower>_map`` shows the areas, no-go zones, obstacles, transfer paths, the dock, the
mower with its heading and, while a job runs, the planned path with the mowed part, rendered as
SVG in Home Assistant (``render.py``). It is rendered again only when what it shows changes
(the render key: map revision, the mower's position to 25 cm and heading to 5 degrees, the path
and its progress), and at most every ``MAP_MIN_INTERVAL``; the path is read at most every
``PATH_REFRESH`` while a job runs.

``image.<mower>_heatmap_<layer>`` (coverage, RTK, Wi-Fi, mobile network; disabled by default)
shows a layer of the last 7 days from the heatmap coordinator.

``image.<mower>_ai_recovery_image`` is the camera frame the Vision model saw (the first one,
the left camera of a stereo pair), ``image.<mower>_ai_recovery_map`` the top-down map rendering
Vision adds (disabled by default). Both come from ``GET /vision/proposals/{id}/images/{index}``
and are fetched once per proposal; MowDeck purges them after the proposal's TTL.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.image import ImageEntity, ImageEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .api import MowDeckError, MowDeckNotFoundError
from .const import HEATMAP_LAYERS, MAP_MIN_INTERVAL, PATH_REFRESH
from .entity import MowDeckEntity, async_add_mower_entities
from .geo import from_wgs84, local_angle
from .render import render_heatmap, render_map
from .vision import CAMERA_SOURCES, MAP_SOURCE, VisionCoordinator, frame_index

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator
    from .heatmap import HeatmapCoordinator

PARALLEL_UPDATES = 0

SVG = "image/svg+xml"
# Activities in which the mower follows a planned path.
JOB_ACTIVITIES = frozenset({"mowing", "paused", "returning"})

FRAME = ImageEntityDescription(key="ai_recovery_image")
MAP_FRAME = ImageEntityDescription(key="ai_recovery_map", entity_registry_enabled_default=False)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MowDeckConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """The map image of every mower with a map and its heatmap images; the proposal images of
    every mower where MowDeck runs Vision."""
    heatmap = entry.runtime_data.heatmap

    def entities(coordinator: MowDeckCoordinator, mower_id: str) -> list[ImageEntity]:
        if not coordinator.data.maps.get(mower_id):
            return []
        images: list[ImageEntity] = [MowerMapImage(coordinator, mower_id)]
        images.extend(
            HeatmapImage(coordinator, mower_id, heatmap, layer) for layer in HEATMAP_LAYERS
        )
        return images

    async_add_mower_entities(
        entry,
        async_add_entities,
        entities,
        fingerprint=lambda m: bool(entry.runtime_data.coordinator.data.maps.get(m)),
    )

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


class _SvgImage(MowDeckEntity, ImageEntity):
    """An SVG rendered on demand and kept until what it shows changes."""

    _attr_content_type = SVG

    def __init__(
        self,
        coordinator: MowDeckCoordinator,
        mower_id: str,
        key: str,
        service: DataUpdateCoordinator[Any] | None = None,
    ) -> None:
        super().__init__(coordinator, mower_id, key, service=service)
        ImageEntity.__init__(self, coordinator.hass)
        self._svg: bytes | None = None

    def _render(self) -> bytes | None:  # pragma: no cover - every image overrides it
        raise NotImplementedError

    async def async_image(self) -> bytes | None:
        """The SVG, rendered once per change."""
        if self._svg is None:
            self._svg = self._render()
        return self._svg

    @property
    def mower_map(self) -> dict[str, Any]:
        """The mower's map (empty while unknown)."""
        return self.coordinator.data.maps.get(self.mower_id) or {}


class MowerMapImage(_SvgImage):
    """The mower's map with the mower, the dock and the planned path of the running job."""

    entity_description = ImageEntityDescription(key="map")

    def __init__(self, coordinator: MowDeckCoordinator, mower_id: str) -> None:
        """Create the image."""
        super().__init__(coordinator, mower_id, "map")
        self._key: tuple[Any, ...] | None = None
        self._rendered_at: datetime | None = None
        self._pending: Any = None
        self._path: dict[str, Any] | None = None
        self._path_read_at: datetime | None = None
        self._path_task = False

    async def async_added_to_hass(self) -> None:
        """Render the first picture."""
        await super().async_added_to_hass()
        self._follow()
        self.async_on_remove(self._cancel_pending)

    @callback
    def _cancel_pending(self) -> None:
        if self._pending is not None:
            self._pending()
            self._pending = None

    def _mower(self) -> tuple[float, float, float | None] | None:
        """The mower in local metres with its local heading, if it reports a position."""
        reference = self.mower_map.get("reference")
        position = self.mower_state.get("position")
        if not isinstance(reference, dict) or not isinstance(position, dict):
            return None
        try:
            x, y = from_wgs84(reference, float(position["lat"]), float(position["lon"]))
        except (KeyError, TypeError, ValueError):
            return None
        heading = position.get("heading")
        angle = local_angle(float(heading), reference) if heading is not None else None
        return x, y, angle

    def _render_key(self) -> tuple[Any, ...]:
        mower = self._mower()
        where = None
        if mower is not None:
            x, y, angle = mower
            heading = round(angle * 36 / 3.14159265) if angle is not None else None
            where = (round(x * 4), round(y * 4), heading)
        path = self._path or {}
        progress = path.get("progress") or {}
        return (
            self.coordinator.data.map_revisions.get(self.mower_id),
            where,
            path.get("path_hash"),
            progress.get("index") if isinstance(progress, dict) else None,
        )

    def _render(self) -> bytes:
        return render_map(self.mower_map, mower=self._mower(), path=self._path)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Re-render when what the map shows changed, at most every ``MAP_MIN_INTERVAL``."""
        self._follow()
        super()._handle_coordinator_update()

    @callback
    def _follow(self, *, due: bool = False) -> None:
        """Take a changed render key, unless the last render is younger than the interval
        (then once the interval is over; ``due`` marks that moment)."""
        self._maybe_read_path()
        key = self._render_key()
        if key == self._key:
            return
        now = dt_util.utcnow()
        last = self._rendered_at
        if last is not None and now - last < MAP_MIN_INTERVAL and not due:
            if self._pending is None:
                wait = (last + MAP_MIN_INTERVAL - now).total_seconds()
                self._pending = async_call_later(self.hass, wait, self._deferred)
            return
        self._key = key
        self._rendered_at = now
        self._svg = None
        self._attr_image_last_updated = now

    @callback
    def _deferred(self, _now: datetime) -> None:
        self._pending = None
        self._follow(due=True)
        self.async_write_ha_state()

    @callback
    def _maybe_read_path(self) -> None:
        """While a job runs, read its planned path at most every ``PATH_REFRESH``."""
        if self.mower_state.get("activity") not in JOB_ACTIVITIES:
            self._path = None
            return
        now = dt_util.utcnow()
        if self._path_task or (
            self._path_read_at is not None and now - self._path_read_at < PATH_REFRESH
        ):
            return
        self._path_task = True
        self._path_read_at = now
        self.coordinator.config_entry.async_create_background_task(
            self.hass, self._async_read_path(), "mowdeck map path"
        )

    async def _async_read_path(self) -> None:
        try:
            answer = await self.coordinator.client.get_path(self.mower_id)
        except MowDeckError as err:
            self.coordinator.logger.debug("No path for %s: %s", self.mower_id, err)
            answer = None
        finally:
            self._path_task = False
        path = answer.get("path") if isinstance(answer, dict) else None
        self._path = path if isinstance(path, dict) else None
        self._follow()
        self.async_write_ha_state()


class HeatmapImage(_SvgImage):
    """A heatmap layer of the last 7 days over the map (disabled by default; only an enabled
    entity makes the coordinator read its layer)."""

    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: MowDeckCoordinator,
        mower_id: str,
        heatmap: HeatmapCoordinator,
        layer: str,
    ) -> None:
        """Create the image of ``layer``."""
        super().__init__(coordinator, mower_id, f"heatmap_{layer}", service=heatmap)
        self._heatmap = heatmap
        self._layer = layer
        self._digest: str | None = None

    async def async_added_to_hass(self) -> None:
        """Ask the coordinator for the layer while the entity exists."""
        await super().async_added_to_hass()
        self.async_on_remove(self._heatmap.async_want(self.mower_id, self._layer))
        self._follow()

    def _data(self) -> dict[str, Any] | None:
        return self._heatmap.layer(self.mower_id, self._layer)

    @callback
    def _follow(self) -> None:
        data = self._data()
        if data is None:
            self._digest = None
            self._attr_image_last_updated = None
            self._svg = None
            return
        content = {k: data.get(k) for k in ("cells", "gaps", "cell_m", "legend")}
        digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
        if digest != self._digest:
            self._digest = digest
            self._svg = None
            self._attr_image_last_updated = dt_util.utcnow()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._follow()
        super()._handle_coordinator_update()

    def _render(self) -> bytes | None:
        data = self._data()
        return render_heatmap(self.mower_map, data) if data is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The range and the legend of the layer."""
        data = self._data()
        if data is None:
            return None
        legend = data.get("legend") or {}
        return {
            "from": data.get("from"),
            "to": data.get("to"),
            "cell_m": data.get("cell_m"),
            "legend": legend.get("kind"),
            "legend_min": legend.get("min"),
            "legend_max": legend.get("max"),
            "samples": data.get("samples"),
            "truncated": data.get("truncated"),
        }


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
