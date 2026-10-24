"""Coordinator of one MowDeck config entry: mowers, states, capabilities, maps, permissions.

States arrive by push over the Realtime WebSocket (``async_push_state``); polling is the
fallback (every ``poll_interval`` seconds while the socket is down) and discovers new mowers
(every ``PUSH_POLL_INTERVAL`` while it is up). Capabilities, maps and the token's permissions
on each mower are refetched every ``STATIC_REFRESH``. Slower data (the health totals) lives in
its own coordinator, so one failing service never blocks the others.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import MowDeckAuthError, MowDeckClient, MowDeckError, MowDeckForbiddenError
from .const import (
    AREA_FALLBACK,
    CONF_POLL_INTERVAL,
    DEFAULT_PATTERN,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    ISSUE_FORBIDDEN,
    PUSH_POLL_INTERVAL,
    RELATION_PERMISSION,
    STALE_AFTER,
    STATIC_REFRESH,
    TOKEN_PAGE,
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
    # Relations the token holds on each mower (keys of RELATION_PERMISSION); a mower is
    # missing while unknown (MowDeck without the permission endpoint, or the check failed).
    permissions: dict[str, frozenset[str]] = field(default_factory=dict)


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
        # Areas the next start mows per mower, in any order; None (or missing) means every
        # area of the map. The area select and the area switches both edit it.
        self.area_selection: dict[str, list[str] | None] = {}
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
            self._async_remove_stale_devices(set(data.mowers))
            due = [
                mower_id
                for mower_id in data.mowers
                if (fetched := self._static_fetched_at.get(mower_id)) is None
                or now - fetched >= STATIC_REFRESH
            ]
            for mower_id in due:
                await self._async_fetch_static(data, mower_id)
                self._static_fetched_at[mower_id] = now
            if due:
                await self._async_fetch_permissions(data, due)
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

    async def _async_fetch_permissions(self, data: MowDeckData, mower_ids: list[str]) -> None:
        """Ask which relations the token holds on these mowers.

        A failure leaves the permissions unknown, which never hides an entity: the API still
        refuses what the token may not do, with an error that names the permission."""
        relations = list(RELATION_PERMISSION)
        checks = [(relation, f"mower:{m}") for m in mower_ids for relation in relations]
        try:
            answers = await self.client.check_permissions(checks)
        except MowDeckAuthError:
            raise
        except MowDeckError as err:
            _LOGGER.debug("Permission check failed, permissions stay unknown: %s", err)
            return
        for index, mower_id in enumerate(mower_ids):
            held = answers[index * len(relations) : (index + 1) * len(relations)]
            data.permissions[mower_id] = frozenset(
                relation for relation, ok in zip(relations, held, strict=True) if ok
            )

    @callback
    def _async_remove_stale_devices(self, mower_ids: set[str]) -> None:
        """Detach the devices of mowers the token no longer sees (only after a listing)."""
        registry = dr.async_get(self.hass)
        entry_id = self.config_entry.entry_id
        for device in dr.async_entries_for_config_entry(registry, entry_id):
            # Base station devices ("base_station_<id>") follow the base station list instead.
            ids = {
                identifier
                for domain, identifier in device.identifiers
                if domain == DOMAIN and not identifier.startswith("base_station_")
            }
            if ids and not ids & mower_ids:
                _LOGGER.debug("Removing the device of %s: no longer visible to the token", ids)
                registry.async_update_device(device.id, remove_config_entry_id=entry_id)

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

    @callback
    def async_merge_firmware(self, mower_id: str, info: dict[str, Any]) -> None:
        """A fresh ``FirmwareInfo`` from a firmware check: show it before the next state."""
        state = self.data.states.get(mower_id)
        if state is None:
            return
        self.data.states[mower_id] = {**state, "firmware_info": dict(info)}
        self.async_update_listeners()

    # Read helpers for the entities

    def state(self, mower_id: str) -> dict[str, Any] | None:
        """The newest state of a mower, if any."""
        return self.data.states.get(mower_id)

    def permitted(self, mower_id: str, relation: str) -> bool:
        """The token holds ``relation`` on the mower, or that is unknown (never hide on doubt)."""
        held = self.data.permissions.get(mower_id)
        return held is None or relation in held

    def fingerprint(self, mower_id: str) -> tuple[Any, ...]:
        """What decides which entities a mower has: capabilities, permissions and map areas.

        The platforms re-evaluate a mower's entities whenever this changes."""
        caps = self.data.capabilities.get(mower_id) or {}
        return (
            repr(sorted(caps.items())),
            self.data.permissions.get(mower_id),
            tuple(str(area["id"]) for area in self.areas(mower_id)),
        )

    def model(self, mower_id: str) -> str | None:
        """The model name: from the capabilities, else from Fleet."""
        caps = self.data.capabilities.get(mower_id) or {}
        mower = self.data.mowers.get(mower_id) or {}
        model = caps.get("model") or mower.get("model")
        return str(model) if model else None

    def firmware_version(self, mower_id: str) -> str | None:
        """The firmware running on the mower, if reported."""
        state = self.data.states.get(mower_id) or {}
        info = state.get("firmware_info")
        installed = info.get("installed") if isinstance(info, dict) else None
        version = state.get("firmware") or installed
        return str(version) if version else None

    def is_online(self, mower_id: str) -> bool:
        """The gateway holds a live link to the mower (ADR 0040 in lukislp/mowdeck).

        Offline when there is no state, Telemetry flags it stale, the gateway published its
        link-lost state (``connected`` false), no state arrived for ``STALE_AFTER`` (the gateway
        repeats the state every 10 s as a heartbeat) or the mower reports itself offline.
        """
        state = self.data.states.get(mower_id)
        received = self.data.received_at.get(mower_id)
        if state is None or received is None or self.data.stale.get(mower_id, False):
            return False
        if state.get("connected") is False:
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
        """The selected areas in map order (every area while all are selected); empty when the
        switches deselected every area."""
        all_ids = [str(area["id"]) for area in self.areas(mower_id)]
        selection = self.area_selection.get(mower_id)
        if selection is None:
            return all_ids
        return [area_id for area_id in all_ids if area_id in selection]

    def area_selected(self, mower_id: str, area_id: str) -> bool:
        """The area is part of the next start."""
        selection = self.area_selection.get(mower_id)
        return selection is None or area_id in selection

    @callback
    def async_select_areas(self, mower_id: str, area_ids: list[str] | None) -> None:
        """Set the areas of the next start (None: every area) and update the entities."""
        all_ids = [str(area["id"]) for area in self.areas(mower_id)]
        if area_ids is not None and all_ids and set(all_ids) <= set(area_ids):
            area_ids = None
        self.area_selection[mower_id] = list(area_ids) if area_ids is not None else None
        self.async_update_listeners()

    @callback
    def async_toggle_area(self, mower_id: str, area_id: str, selected: bool) -> None:
        """Add an area to the next start or take it out."""
        current = self.area_selection.get(mower_id)
        chosen = [str(area["id"]) for area in self.areas(mower_id)] if current is None else current
        chosen = [a for a in chosen if a != area_id] + ([area_id] if selected else [])
        self.async_select_areas(mower_id, chosen)

    def mow_pattern(self, mower_id: str) -> dict[str, Any]:
        """The mower's mowing defaults (``MowerOut.mow_defaults.pattern``), else MowDeck's."""
        defaults = (self.data.mowers.get(mower_id) or {}).get("mow_defaults")
        pattern = defaults.get("pattern") if isinstance(defaults, dict) else None
        return (
            {**DEFAULT_PATTERN, **pattern} if isinstance(pattern, dict) else dict(DEFAULT_PATTERN)
        )

    def has_mow_defaults(self, mower_id: str) -> bool:
        """The mower has saved mowing defaults."""
        defaults = (self.data.mowers.get(mower_id) or {}).get("mow_defaults")
        return isinstance(defaults, dict) and isinstance(defaults.get("pattern"), dict)

    @callback
    def async_set_mower(self, mower: dict[str, Any]) -> None:
        """A mower as Fleet answered an action (``MowerOut``)."""
        mower_id = str(mower.get("id"))
        if mower_id in self.data.mowers:
            self.data.mowers[mower_id] = mower
            self.async_update_listeners()


class MowDeckServiceCoordinator[T](DataUpdateCoordinator[T]):
    """Polls one further MowDeck service (health, scheduler, ...) next to the state coordinator.

    Failures stay with the service: its entities turn unavailable, the rest keeps working. A
    403 raises a Repairs issue ``<service>_forbidden`` that names the missing permission and
    disappears with the next successful poll; a 401 starts the reauth flow as everywhere.
    """

    config_entry: MowDeckConfigEntry
    service: str
    permission: str

    def __init__(
        self,
        hass: HomeAssistant,
        entry: MowDeckConfigEntry,
        state: MowDeckCoordinator,
        *,
        update_interval: timedelta | None,
    ) -> None:
        """Create the coordinator; ``state`` provides the client and the mower list."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {self.service}",
            update_interval=update_interval,
        )
        self.state = state
        self.client = state.client
        self.forbidden = False

    @property
    def issue_id(self) -> str:
        """Repairs issue id of this service and entry."""
        return f"{ISSUE_FORBIDDEN.format(service=self.service)}_{self.config_entry.entry_id}"

    async def _async_fetch(self) -> T:  # pragma: no cover - every service overrides it
        """Fetch the service's data (raises MowDeck errors)."""
        raise NotImplementedError

    async def _async_update_data(self) -> T:
        try:
            data = await self._async_fetch()
        except MowDeckAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MowDeckForbiddenError as err:
            self._async_set_forbidden(True)
            raise UpdateFailed(str(err)) from err
        except MowDeckError as err:
            raise UpdateFailed(str(err)) from err
        self._async_set_forbidden(False)
        return data

    @callback
    def _async_set_forbidden(self, forbidden: bool) -> None:
        if forbidden == self.forbidden:
            return
        self.forbidden = forbidden
        if not forbidden:
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
            return
        _LOGGER.warning(
            "MowDeck refuses the %s data to this API token (403); grant %s",
            self.service,
            self.permission,
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self.issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            learn_more_url=f"{self.client.base_url}{TOKEN_PAGE}",
            translation_key=ISSUE_FORBIDDEN.format(service=self.service),
            translation_placeholders={
                "url": self.client.base_url,
                "permission": self.permission,
            },
        )
