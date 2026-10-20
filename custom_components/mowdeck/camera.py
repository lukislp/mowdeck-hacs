"""Cameras of a MowDeck mower, played as WebRTC through MowDeck's camera relay.

Which cameras a mower has is known only from a camera session, and opening one starts the
mower's stream. So nothing is opened to find out: every mower whose capabilities name a camera
gets one entity, ``Camera``, at once; it plays the camera the MowDeck web app shows first (the
first one known to deliver, a front camera on a LUBA). The first time it plays, the session
lists the mower's cameras; every other camera that delivers video gets its own entity then
(``Front right camera`` and so on), and the list is kept in Home Assistant's storage so those
entities come back after a restart. Cameras the relay reports as silent (listed by the vendor
but never published, such as the LUBA 2 AWD X's rear camera) get no entity, and an entity whose
camera turned silent is unavailable until a later session finds it delivering again.

The signalling is :mod:`.camera_relay`: one lease and one WHEP session per open stream, the
lease renewed while the stream is open and released when it closes. There is no still image:
MowDeck offers no snapshot to API clients (the relay's frame tap is internal to MowDeck).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.camera import (
    Camera,
    CameraEntityDescription,
    CameraEntityFeature,
    WebRTCAnswer,
    WebRTCError,
    WebRTCSendMessage,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.helpers.translation import async_get_exception_message

from .camera_relay import CameraRelayError, RelaySession, RelayViewer
from .const import DOMAIN
from .entity import MowDeckEntity

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
    from webrtc_models import RTCIceCandidateInit

    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

STORAGE_VERSION = 1
# Placements with a translated entity name (CameraPlacement in MowDeck's contracts).
PLACEMENTS = frozenset({"front_left", "front_right", "front", "rear"})


def storage_key(entry_id: str) -> str:
    """The storage key of an entry's camera list."""
    return f"{DOMAIN}.{entry_id}.cameras"


def has_camera(capabilities: dict[str, Any]) -> bool:
    """The mower's capabilities name a camera stream (``camera`` ``cloud`` or ``local``)."""
    return capabilities.get("camera") in ("cloud", "local")


class CameraCatalog:
    """The cameras each mower's sessions listed, kept in storage (no secrets: ids, names,
    placements and whether the relay saw each deliver)."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Create the catalog; :meth:`async_load` reads the storage."""
        self._hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, storage_key(entry_id))
        self.signal = f"{DOMAIN}_{entry_id}_cameras"
        self._mowers: dict[str, dict[str, Any]] = {}
        self._dirty = False

    async def async_load(self) -> None:
        """Read the stored camera lists."""
        data = await self._store.async_load() or {}
        mowers = data.get("mowers")
        self._mowers = dict(mowers) if isinstance(mowers, dict) else {}

    def primary(self, mower_id: str) -> str | None:
        """The camera the mower's ``Camera`` entity plays (None until a session listed it)."""
        primary = self._mowers.get(mower_id, {}).get("primary")
        return primary if isinstance(primary, str) else None

    def cameras(self, mower_id: str) -> dict[str, dict[str, Any]]:
        """Every listed camera of the mower by id."""
        cameras = self._mowers.get(mower_id, {}).get("cameras")
        return dict(cameras) if isinstance(cameras, dict) else {}

    def extra_cameras(self, mower_id: str) -> list[str]:
        """The cameras besides the primary one that were seen delivering video."""
        primary = self.primary(mower_id)
        return [
            camera_id
            for camera_id, camera in self.cameras(mower_id).items()
            if camera_id != primary and camera.get("delivered")
        ]

    def silent(self, mower_id: str, camera_id: str) -> bool:
        """The last session reported the camera as silent."""
        return self.cameras(mower_id).get(camera_id, {}).get("available") is False

    def _data(self) -> dict[str, Any]:
        self._dirty = False
        return {"mowers": self._mowers}

    async def async_flush(self) -> None:
        """Write pending changes now (the entry unloads)."""
        if self._dirty:
            await self._store.async_save(self._data())

    @callback
    def async_update(self, mower_id: str, session: RelaySession) -> None:
        """Merge a session's camera list; announces new cameras that deliver."""
        record = self._mowers.setdefault(mower_id, {})
        cameras: dict[str, Any] = dict(record.get("cameras") or {})
        changed = False
        if not isinstance(record.get("primary"), str):
            first = session.tile_for(None)
            if first is not None:
                record["primary"] = first.camera_id
                changed = True
        for camera in session.cameras:
            old = cameras.get(camera.camera_id, {})
            new = {
                "name": camera.name,
                "placement": camera.placement,
                "available": camera.available,
                "delivered": bool(old.get("delivered")) or camera.available is True,
            }
            if new != old:
                cameras[camera.camera_id] = new
                changed = True
        if not changed:
            return
        record["cameras"] = cameras
        self._dirty = True
        self._store.async_delay_save(self._data, 1)
        async_dispatcher_send(self._hass, self.signal)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MowDeckConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One camera per camera-capable mower, and one per further camera a session found."""
    coordinator = entry.runtime_data.coordinator
    catalog = CameraCatalog(hass, entry.entry_id)
    await catalog.async_load()
    added: set[tuple[str, str | None]] = set()

    @callback
    def _add_new() -> None:
        new: list[MowDeckCamera] = []
        for mower_id in coordinator.data.mowers:
            if not has_camera(coordinator.data.capabilities.get(mower_id, {})):
                continue
            for camera_id in [None, *catalog.extra_cameras(mower_id)]:
                if (mower_id, camera_id) in added:
                    continue
                added.add((mower_id, camera_id))
                new.append(MowDeckCamera(coordinator, mower_id, catalog, camera_id))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))
    entry.async_on_unload(async_dispatcher_connect(hass, catalog.signal, _add_new))
    entry.async_on_unload(catalog.async_flush)


class MowDeckCamera(MowDeckEntity, Camera):
    """A mower camera over the relay; Home Assistant's frontend plays it with WebRTC."""

    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_is_streaming = False

    def __init__(
        self,
        coordinator: MowDeckCoordinator,
        mower_id: str,
        catalog: CameraCatalog,
        camera_id: str | None,
    ) -> None:
        """``camera_id`` None is the mower's ``Camera`` (the first camera of the web app)."""
        key = "camera" if camera_id is None else f"camera_{camera_id}"
        super().__init__(coordinator, mower_id, key)
        Camera.__init__(self)
        self._catalog = catalog
        self._camera_id = camera_id
        self._viewers: dict[str, RelayViewer] = {}
        info = catalog.cameras(mower_id).get(camera_id or "", {})
        placement = info.get("placement")
        if camera_id is None:
            self._attr_translation_key = "camera"
        elif placement in PLACEMENTS:
            self._attr_translation_key = f"camera_{placement}"
        else:
            self._attr_translation_key = "camera_other"
            self._attr_translation_placeholders = {"name": str(info.get("name") or camera_id)}
        self.entity_description = CameraEntityDescription(key=key)

    @property
    def camera_id(self) -> str | None:
        """The camera this entity plays (None: not known before the first session)."""
        return self._camera_id or self._catalog.primary(self.mower_id)

    @property
    def available(self) -> bool:
        """Available while the mower is visible and its camera is not known to be silent."""
        camera_id = self.camera_id
        if (
            self._camera_id is not None
            and camera_id
            and self._catalog.silent(self.mower_id, camera_id)
        ):
            return False
        return super().available

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Which camera this is."""
        camera_id = self.camera_id
        info = self._catalog.cameras(self.mower_id).get(camera_id or "", {})
        return {"camera_id": camera_id, "placement": info.get("placement")}

    async def async_added_to_hass(self) -> None:
        """Follow the camera list (availability of a silent camera)."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._catalog.signal, self.async_write_ha_state)
        )

    async def async_will_remove_from_hass(self) -> None:
        """Release every open stream of this entity."""
        viewers, self._viewers = list(self._viewers.values()), {}
        for viewer in viewers:
            await viewer.async_close()
        await super().async_will_remove_from_hass()

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """No still image: MowDeck offers no snapshot to API clients."""
        return None

    async def async_handle_async_webrtc_offer(
        self, offer_sdp: str, session_id: str, send_message: WebRTCSendMessage
    ) -> None:
        """Open a lease, offer to the relay's WHEP endpoint and send the answer back."""

        @callback
        def _lost(reason: str) -> None:
            if self._viewers.pop(session_id, None) is None:
                return
            self._set_streaming()
            send_message(WebRTCError("webrtc_offer_failed", self._message(reason)))

        viewer = RelayViewer(
            self.coordinator.client,
            self.mower_id,
            self.camera_id,
            fallback=self._camera_id is None,
            on_session=lambda session: self._catalog.async_update(self.mower_id, session),
            on_lost=_lost,
        )
        self._viewers[session_id] = viewer
        self._set_streaming()
        try:
            answer = await viewer.async_offer(offer_sdp)
        except CameraRelayError as err:
            self._viewers.pop(session_id, None)
            self._set_streaming()
            if err.reason == "closed":
                return
            _LOGGER.warning(
                "Camera of %s cannot be played: %s", self.mower_id, err.detail or err.reason
            )
            send_message(WebRTCError("webrtc_offer_failed", self._message(err.reason)))
            return
        if session_id not in self._viewers:  # closed while the answer came
            await viewer.async_close()
            return
        send_message(WebRTCAnswer(answer))

    async def async_on_webrtc_candidate(
        self, session_id: str, candidate: RTCIceCandidateInit
    ) -> None:
        """Forward a browser candidate to the WHEP session (ignored once it was closed)."""
        viewer = self._viewers.get(session_id)
        if viewer is not None:
            await viewer.async_add_candidate(candidate)

    @callback
    def close_webrtc_session(self, session_id: str) -> None:
        """Release the stream at once: WHEP session and lease (the relay's grace follows)."""
        viewer = self._viewers.pop(session_id, None)
        if viewer is None:
            return
        self._set_streaming()
        self.hass.async_create_background_task(
            viewer.async_close(), f"{DOMAIN} close camera {self.mower_id}"
        )

    @callback
    def _set_streaming(self) -> None:
        streaming = bool(self._viewers)
        if streaming != self._attr_is_streaming:
            self._attr_is_streaming = streaming
            if self.hass is not None and self.entity_id:
                self.async_write_ha_state()

    def _message(self, reason: str) -> str:
        key = f"camera_{reason}" if not reason.startswith("camera_") else reason
        return async_get_exception_message(DOMAIN, key)
