"""Repairs issues of the MowDeck integration that are not tied to one polled service.

- ``ble_bridge_outdated_<mower>``: the mower's Bluetooth bridge runs firmware older than what
  MowDeck expects (``MowerState.links.ble_bridge.outdated``); it disappears with the first
  state that reports the bridge up to date (or no bridge).
- ``missing_permission_<key>``: the API token lacks an account key a feature needs (today
  ``notifications.view``, which only an account-wide ``mower.view`` implies, read from
  ``GET /identity/me``). Asked at setup and every ``STATIC_REFRESH``; it links to MowDeck's
  API token page and disappears once a token with the key is in use.

The 403 issues of the polled services (``<service>_forbidden``) live in ``coordinator.py``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval

from .api import MowDeckAuthError, MowDeckError
from .const import DOMAIN, STATIC_REFRESH, TOKEN_PAGE

if TYPE_CHECKING:
    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator

_LOGGER = logging.getLogger(__name__)

ISSUE_BRIDGE_OUTDATED: Final = "ble_bridge_outdated"
ISSUE_MISSING_PERMISSION: Final = "missing_permission"
# Account keys a feature needs, with what it is needed for (a translation key of the issue).
REQUIRED_KEYS: Final = {"notifications.view": "notifications"}


def token_page(base_url: str) -> str:
    """MowDeck's API token page."""
    return f"{base_url}{TOKEN_PAGE}"


class IssueMonitor:
    """Raises and clears the bridge firmware and the token permission issues of an entry."""

    def __init__(
        self, hass: HomeAssistant, entry: MowDeckConfigEntry, coordinator: MowDeckCoordinator
    ) -> None:
        """Create the monitor; ``async_start`` checks once and starts following."""
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self._bridges: set[str] = set()
        self._missing: set[str] = set()
        self._stopped = False

    async def async_start(self) -> Callable[[], None]:
        """Check now, then follow the states and ask for the permissions periodically.
        Returns the function that stops following."""
        await self.async_check_permissions()
        self.async_check_bridges()
        remove_listener = self.coordinator.async_add_listener(self.async_check_bridges)
        remove_timer = async_track_time_interval(
            self.hass, self._async_tick, STATIC_REFRESH, name="mowdeck token permissions"
        )

        @callback
        def stop() -> None:
            self._stopped = True
            remove_listener()
            remove_timer()
            for mower_id in self._bridges:
                ir.async_delete_issue(
                    self.hass, DOMAIN, self._issue_id(ISSUE_BRIDGE_OUTDATED, mower_id)
                )
            for key in self._missing:
                ir.async_delete_issue(
                    self.hass,
                    DOMAIN,
                    self._issue_id(ISSUE_MISSING_PERMISSION, key.replace(".", "_")),
                )

        return stop

    async def _async_tick(self, _now: datetime) -> None:
        await self.async_check_permissions()

    def _issue_id(self, kind: str, suffix: str) -> str:
        return f"{kind}_{suffix}_{self.entry.entry_id}"

    async def async_check_permissions(self) -> None:
        """Ask MowDeck which account keys the token holds; unknown on failure (no change)."""
        try:
            me = await self.coordinator.client.get_me()
        except MowDeckAuthError:
            return  # the coordinators start the reauth flow
        except MowDeckError as err:
            _LOGGER.debug("Token permissions not readable: %s", err)
            return
        held = me.get("permissions")
        if self._stopped or not isinstance(held, list):
            return
        url = self.coordinator.client.base_url
        for key, feature in REQUIRED_KEYS.items():
            issue_id = self._issue_id(ISSUE_MISSING_PERMISSION, key.replace(".", "_"))
            if key in held:
                if key in self._missing:
                    self._missing.discard(key)
                    ir.async_delete_issue(self.hass, DOMAIN, issue_id)
                continue
            if key not in self._missing:
                _LOGGER.warning("The MowDeck API token lacks %s (%s)", key, feature)
            self._missing.add(key)
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                learn_more_url=token_page(url),
                translation_key=f"{ISSUE_MISSING_PERMISSION}_{feature}",
                translation_placeholders={"permission": key, "url": token_page(url)},
            )

    @callback
    def async_check_bridges(self) -> None:
        """An issue per mower whose Bluetooth bridge reports outdated firmware."""
        outdated: dict[str, dict[str, Any]] = {}
        for mower_id in self.coordinator.data.mowers:
            state = self.coordinator.data.states.get(mower_id) or {}
            links = state.get("links")
            link = links.get("ble_bridge") if isinstance(links, dict) else None
            if isinstance(link, dict) and link.get("outdated"):
                outdated[mower_id] = link
        for mower_id in self._bridges - set(outdated):
            ir.async_delete_issue(
                self.hass, DOMAIN, self._issue_id(ISSUE_BRIDGE_OUTDATED, mower_id)
            )
        for mower_id, link in outdated.items():
            if mower_id in self._bridges:
                continue
            mower = self.coordinator.data.mowers.get(mower_id) or {}
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._issue_id(ISSUE_BRIDGE_OUTDATED, mower_id),
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                learn_more_url=f"{self.coordinator.client.base_url}/mowers/{mower_id}",
                translation_key=ISSUE_BRIDGE_OUTDATED,
                translation_placeholders={
                    "mower": str(mower.get("name") or mower_id),
                    "reason": str(link.get("outdated_reason") or "unknown"),
                    "esphome_version": str(link.get("esphome_version") or "unknown"),
                    "project_version": str(link.get("project_version") or "none"),
                },
            )
        self._bridges = set(outdated)
