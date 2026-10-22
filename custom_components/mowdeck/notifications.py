"""MowDeck notifications in Home Assistant: a bus event, an event entity per mower and, by
option, persistent notifications.

Every notification enters through ``NotificationCoordinator.async_ingest``, whatever its
source. Today the source is the inbox (``GET /notifications/inbox?unread=true``, polled every
``INBOX_REFRESH``); once MowDeck pushes notifications over the Realtime WebSocket
(``notification`` messages), the socket feeds the same method and the polling only fills gaps.

MowDeck's inbox belongs to persons: an API token is answered 403 (and a MowDeck without the
Notifications service 404). Then the inbox counts as unsupported, no Repairs issue is raised
(the user cannot fix it), the inbox is asked again only every ``INBOX_RECHECK``, and the
notification event entities appear as soon as a source delivers.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .api import MowDeckForbiddenError, MowDeckNotFoundError
from .const import (
    CONF_PERSISTENT_SEVERITY,
    DEFAULT_PERSISTENT_SEVERITY,
    DOMAIN,
    EVENT_NOTIFICATION,
    INBOX_RECHECK,
    INBOX_REFRESH,
    SEVERITIES,
)
from .coordinator import MowDeckCoordinator, MowDeckServiceCoordinator

if TYPE_CHECKING:
    from . import MowDeckConfigEntry

_LOGGER = logging.getLogger(__name__)

# Notification ids remembered as delivered (the inbox keeps the newest 200 per user).
SEEN_LIMIT = 500
STORAGE_VERSION = 1
INBOX_LIMIT = 50


def storage_key(entry_id: str) -> str:
    """Storage key of the delivered notification ids of an entry."""
    return f"{DOMAIN}.{entry_id}.notifications"


def notification_signal(entry_id: str, mower_id: str) -> str:
    """Dispatcher signal of a mower's notifications."""
    return f"{DOMAIN}_notification_{entry_id}_{mower_id}"


@dataclass
class NotificationData:
    """Where notifications come from."""

    # None until the inbox answered once; False when MowDeck refuses it to the token.
    inbox: bool | None = None
    # A notification arrived over the Realtime WebSocket.
    realtime: bool = False

    @property
    def available(self) -> bool:
        """Some source delivers notifications."""
        return bool(self.inbox) or self.realtime


class NotificationCoordinator(MowDeckServiceCoordinator[NotificationData]):
    """Polls the inbox and turns new notifications into Home Assistant events."""

    service = "notifications"
    permission = "mower.view"

    def __init__(
        self, hass: HomeAssistant, entry: MowDeckConfigEntry, state: MowDeckCoordinator
    ) -> None:
        """Create the coordinator; ``async_load`` reads the delivered ids first."""
        super().__init__(hass, entry, state, update_interval=INBOX_REFRESH)
        self.data = NotificationData()
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, storage_key(entry.entry_id)
        )
        self._seen: list[str] = []
        # Without stored ids the current unread backlog is taken as delivered, not replayed.
        self._seeded = False

    @property
    def delivered_count(self) -> int:
        """How many notification ids are remembered as delivered."""
        return len(self._seen)

    async def async_load(self) -> None:
        """Read the ids delivered before a restart."""
        stored = await self._store.async_load()
        if stored is not None:
            self._seen = [str(i) for i in stored.get("seen", [])][-SEEN_LIMIT:]
            self._seeded = True

    async def _async_fetch(self) -> NotificationData:
        data = NotificationData(inbox=self.data.inbox, realtime=self.data.realtime)
        try:
            items = await self.client.get_inbox(unread=True, limit=INBOX_LIMIT)
        except (MowDeckForbiddenError, MowDeckNotFoundError) as err:
            if data.inbox is not False:
                _LOGGER.info(
                    "MowDeck does not give this API token an inbox (%s); notifications only "
                    "arrive once MowDeck pushes them over the live connection",
                    err,
                )
            data.inbox = False
            self.update_interval = INBOX_RECHECK
            return data
        data.inbox = True
        self.update_interval = INBOX_REFRESH
        # Oldest first, so events keep their order.
        self.async_ingest(reversed(items), replay=self._seeded)
        return data

    @callback
    def async_ingest(self, items: Iterable[Any], *, replay: bool = True) -> None:
        """New notifications (``InboxItemOut``) from any source; each fires once.

        ``replay`` False only records them as delivered (the backlog of a first start)."""
        fresh = False
        for item in items:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            notification_id = str(item["id"])
            if notification_id in self._seen:
                continue
            self._seen.append(notification_id)
            fresh = True
            if replay:
                self._async_deliver(item)
        self._seeded = True
        if fresh:
            del self._seen[:-SEEN_LIMIT]
            self._store.async_delay_save(lambda: {"seen": list(self._seen)}, 5)

    @callback
    def async_push(self, item: Any) -> None:
        """A ``notification`` message of the Realtime WebSocket."""
        if not self.data.realtime:
            self.data.realtime = True
            self.async_update_listeners()
        self.async_ingest([item])

    @callback
    def _async_deliver(self, item: dict[str, Any]) -> None:
        mower_id = str(item["mower_id"]) if item.get("mower_id") else None
        mower = self.state.data.mowers.get(mower_id or "") or {}
        payload = {
            "config_entry_id": self.config_entry.entry_id,
            "mower_id": mower_id,
            "mower_name": mower.get("name"),
            "notification_id": str(item["id"]),
            "kind": item.get("kind"),
            "severity": item.get("severity"),
            "title": item.get("title"),
            "body": item.get("body"),
            "url": item.get("url"),
            "occurred_at": item.get("occurred_at"),
        }
        self.hass.bus.async_fire(EVENT_NOTIFICATION, payload)
        if mower_id is not None:
            async_dispatcher_send(
                self.hass, notification_signal(self.config_entry.entry_id, mower_id), payload
            )
        if _mirrors(self.config_entry.options, payload["severity"]):
            persistent_notification.async_create(
                self.hass,
                str(item.get("body") or ""),
                title=str(item.get("title") or "MowDeck"),
                notification_id=f"{DOMAIN}_{item['id']}",
            )


def _mirrors(options: Any, severity: Any) -> bool:
    """The options ask for a persistent notification at this severity."""
    minimum = options.get(CONF_PERSISTENT_SEVERITY, DEFAULT_PERSISTENT_SEVERITY)
    if minimum not in SEVERITIES or severity not in SEVERITIES:
        return False
    return SEVERITIES.index(severity) >= SEVERITIES.index(minimum)
