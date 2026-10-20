"""Coordinator of one MowDeck config entry: mowers, states, capabilities and maps.

States arrive by push over the Realtime WebSocket (``async_push_state``); polling is the
fallback (every ``poll_interval`` seconds while the socket is down) and discovers new mowers
(every ``PUSH_POLL_INTERVAL`` while it is up).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import MowDeckAuthError, MowDeckClient, MowDeckError
from .const import (
    ALL_AREAS,
    AREA_FALLBACK,
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    PUSH_POLL_INTERVAL,
    STALE_AFTER,
    STATIC_REFRESH,
)

if TYPE_CHECKING:
    from . import MowDeckConfigEntry

_LOGGER = logging.getLogger(__name__)

# A vendor area name that is only a long number (a hash) says nothing.
_HASH_NAME = re.compile(r"^\d{8,}$")


def area_display_name(
    area: dict[str, Any], index: int, custom: dict[str, str], language: str = "en"
) -> str:
    """The name MowDeck shows for an area: the custom name from Fleet, else the mower's own
    name unless it is empty or only a long number, else "Area N" with N = ``index + 1``."""
    override = str(custom.get(str(area.get("id")), "")).strip()
    if override:
        return override
    own = str(area.get("name") or "").strip()
    if own and not _HASH_NAME.match(own):
        return own
    template = AREA_FALLBACK.get(language.split("-", 1)[0].lower(), AREA_FALLBACK["en"])
    return template.format(n=index + 1)


@dataclass
class MowDeckData:
    """Everything the entities read, keyed by mower id."""

    mowers: dict[str, dict[str, Any]] = field(default_factory=dict)
    states: dict[str, dict[str, Any]] = field(default_factory=dict)
    received_at: dict[str, datetime] = field(default_factory=dict)
    stale: dict[str, bool] = field(default_factory=dict)
    capabilities: dict[str, dict[str, Any]] = field(default_factory=dict)
    maps: dict[str, dict[str, Any]] = field(default_factory=dict)
    map_revisions: dict[str, str] = field(default_factory=dict)


class MowDeckCoordinator(DataUpdateCoordinator[MowDeckData]):
    """Polls the MowDeck API and receives pushed states."""

    config_entry: MowDeckConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: MowDeckConfigEntry, client: MowDeckClient
    ) -> None:
        """Create the coordinator; the first refresh happens in ``async_setup_entry``."""
        self.poll_interval = timedelta(
            seconds=entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=self.poll_interval,
        )
        self.client = client
        self.realtime_connected = False
        # Area chosen in each mower's select entity (ALL_AREAS when none is chosen).
        self.selected_area: dict[str, str] = {}
        self._static_fetched_at: dict[str, datetime] = {}
        self.data = MowDeckData()

    async def _async_update_data(self) -> MowDeckData:
        data = self.data
        try:
            mowers = await self.client.list_mowers()
            snapshots = await self.client.get_states()
            now = dt_util.utcnow()
            data.mowers = {str(m["id"]): m for m in mowers}
            for snapshot in snapshots:
                mower_id = str(snapshot["mower_id"])
                received = dt_util.parse_datetime(str(snapshot.get("received_at", ""))) or now
                # A pushed state may be newer than the stored snapshot.
                if received >= data.received_at.get(mower_id, received):
                    data.states[mower_id] = snapshot["state"]
                    data.received_at[mower_id] = received
                    data.stale[mower_id] = bool(snapshot.get("stale", False))
            for mower_id in data.mowers:
                fetched = self._static_fetched_at.get(mower_id)
                if fetched is None or now - fetched >= STATIC_REFRESH:
                    await self._async_fetch_static(data, mower_id)
                    self._static_fetched_at[mower_id] = now
        except MowDeckAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MowDeckError as err:
            raise UpdateFailed(str(err)) from err
        for mower_id in set(data.states) - set(data.mowers):
            data.states.pop(mower_id, None)
        return data

    async def _async_fetch_static(self, data: MowDeckData, mower_id: str) -> None:
        caps = await self.client.get_capabilities(mower_id)
        if caps is not None:
            data.capabilities[mower_id] = dict(caps.get("capabilities") or {})
        mower_map = await self.client.get_map(mower_id, revision=data.map_revisions.get(mower_id))
        if mower_map is not None:
            data.maps[mower_id] = dict(mower_map.get("map") or {})
            data.map_revisions[mower_id] = str(mower_map.get("revision", ""))

    @callback
    def async_push_state(self, mower_id: str, state: dict[str, Any]) -> None:
        """A state from the WebSocket: store it and update the entities at once."""
        if self.data is None:  # pragma: no cover - only before the first refresh
            return
        self.data.states[mower_id] = state
        self.data.received_at[mower_id] = dt_util.utcnow()
        self.data.stale[mower_id] = False
        # Not async_set_updated_data: that would postpone the next discovery poll forever
        # while states keep coming.
        self.async_update_listeners()

    @callback
    def async_mower_revoked(self, mower_ids: list[str]) -> None:
        """The token lost access to these mowers: drop them until a poll says otherwise."""
        for mower_id in mower_ids:
            self.data.mowers.pop(mower_id, None)
            self.data.states.pop(mower_id, None)
        self.async_update_listeners()

    @callback
    def async_set_realtime_connected(self, connected: bool) -> None:
        """Slow polling down while push works, speed it up when the socket is gone."""
        self.realtime_connected = connected
        self.update_interval = PUSH_POLL_INTERVAL if connected else self.poll_interval
        self.async_update_listeners()

    # Read helpers for the entities

    def state(self, mower_id: str) -> dict[str, Any] | None:
        """The newest state of a mower, if any."""
        return self.data.states.get(mower_id)

    def is_online(self, mower_id: str) -> bool:
        """A fresh state exists and the mower does not report itself offline."""
        state = self.data.states.get(mower_id)
        received = self.data.received_at.get(mower_id)
        if state is None or received is None or self.data.stale.get(mower_id, False):
            return False
        if dt_util.utcnow() - received > STALE_AFTER:
            return False
        return state.get("activity") not in ("offline", "unknown")

    def areas(self, mower_id: str) -> list[dict[str, Any]]:
        """Map areas of a mower (id and name), in map order."""
        return [
            area
            for area in (self.data.maps.get(mower_id) or {}).get("areas", [])
            if isinstance(area, dict) and area.get("id")
        ]

    def area_names(self, mower_id: str) -> dict[str, str]:
        """Custom area names of a mower from Fleet (``MowerOut.area_names``)."""
        names = (self.data.mowers.get(mower_id) or {}).get("area_names") or {}
        return {str(k): str(v) for k, v in names.items()} if isinstance(names, dict) else {}

    def area_labels(self, mower_id: str) -> list[tuple[str, str]]:
        """``(area id, display name)`` of every map area, in map order."""
        custom = self.area_names(mower_id)
        language = self.hass.config.language or "en"
        return [
            (str(area["id"]), area_display_name(area, index, custom, language))
            for index, area in enumerate(self.areas(mower_id))
        ]

    def area_ids_to_mow(self, mower_id: str) -> list[str]:
        """The selected area, or every area of the map when none is selected."""
        selected = self.selected_area.get(mower_id, ALL_AREAS)
        all_ids = [str(area["id"]) for area in self.areas(mower_id)]
        if selected != ALL_AREAS and selected in all_ids:
            return [selected]
        return all_ids
