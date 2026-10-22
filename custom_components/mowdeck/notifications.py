"""MowDeck notifications in Home Assistant: a bus event, an event entity per mower and, by
option, persistent notifications.

The source is the Realtime WebSocket: MowDeck pushes a ``notification`` message for every
notification of a subscribed mower to the account's API tokens that hold
``notifications.view`` (``scope`` ``account``, rendered in English; an account-wide
``mower.view`` implies the key, Vision kinds also need ``mower.drive`` on the mower). An API
token has no inbox (``GET /notifications/inbox`` answers 403), so nothing is polled: account
notifications cannot be marked read or acknowledged, and MowDeck replays nothing after a
reconnect. Every notification fires once; the delivered ids survive a restart.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .const import (
    CONF_PERSISTENT_SEVERITY,
    DEFAULT_PERSISTENT_SEVERITY,
    DOMAIN,
    EVENT_NOTIFICATION,
    SEVERITIES,
)

if TYPE_CHECKING:
    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator

# Notification ids remembered as delivered.
SEEN_LIMIT = 500
STORAGE_VERSION = 1


def storage_key(entry_id: str) -> str:
    """Storage key of the delivered notification ids of an entry."""
    return f"{DOMAIN}.{entry_id}.notifications"


def notification_signal(entry_id: str, mower_id: str) -> str:
    """Dispatcher signal of a mower's notifications."""
    return f"{DOMAIN}_notification_{entry_id}_{mower_id}"


class NotificationHub:
    """Turns ``notification`` messages of the Realtime WebSocket into Home Assistant events."""

    def __init__(
        self, hass: HomeAssistant, entry: MowDeckConfigEntry, state: MowDeckCoordinator
    ) -> None:
        """Create the hub; ``async_load`` reads the delivered ids first."""
        self.hass = hass
        self.entry = entry
        self.state = state
        self.received = 0
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, storage_key(entry.entry_id)
        )
        self._seen: list[str] = []
        self._dirty = False

    @property
    def delivered_count(self) -> int:
        """How many notification ids are remembered as delivered."""
        return len(self._seen)

    async def async_load(self) -> None:
        """Read the ids delivered before a restart."""
        stored = await self._store.async_load()
        if stored is not None:
            self._seen = [str(i) for i in stored.get("seen", [])][-SEEN_LIMIT:]

    async def async_save(self) -> None:
        """Write the delivered ids now (on unload, so a reload replays nothing)."""
        if self._dirty:
            self._dirty = False
            await self._store.async_save(self._data())

    def _data(self) -> dict[str, Any]:
        return {"seen": list(self._seen)}

    @callback
    def async_push(self, item: Any) -> None:
        """A ``notification`` message of the Realtime WebSocket; each notification fires
        once, malformed ones are dropped."""
        if not isinstance(item, dict) or not item.get("id"):
            return
        notification_id = str(item["id"])
        if notification_id in self._seen:
            return
        self.received += 1
        self._seen.append(notification_id)
        del self._seen[:-SEEN_LIMIT]
        self._dirty = True
        self._store.async_delay_save(self._data, 5)
        self._async_deliver(item)

    @callback
    def _async_deliver(self, item: dict[str, Any]) -> None:
        mower_id = str(item["mower_id"]) if item.get("mower_id") else None
        mower = self.state.data.mowers.get(mower_id or "") or {}
        payload = {
            "config_entry_id": self.entry.entry_id,
            "mower_id": mower_id,
            "mower_name": mower.get("name"),
            "notification_id": str(item["id"]),
            "scope": item.get("scope"),
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
                self.hass, notification_signal(self.entry.entry_id, mower_id), payload
            )
        if _mirrors(self.entry.options, payload["severity"]):
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
