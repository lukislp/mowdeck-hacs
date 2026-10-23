"""Event entities of a MowDeck mower: its notifications and its command results.

``event.<mower>_command`` fires for every status change of a command (the Realtime
``command_result`` message, also the bus event ``mowdeck_command_result``).
``event.<mower>_ai_recovery`` fires for every realtime ``vision_proposal`` message of the
mower (``created``, ``updated``; also the bus event ``mowdeck_vision_proposal``).
``event.<mower>_notification`` fires for every MowDeck notification of the mower that the
Realtime WebSocket pushes (also the bus event ``mowdeck_notification``), see
``notifications.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.event import EventEntity, EventEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import COMMAND_STATES, NOTIFICATION_KINDS
from .entity import MowDeckEntity, async_add_mower_entities
from .notifications import notification_signal
from .realtime import command_signal
from .vision import PROPOSAL_EVENTS, vision_signal

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import MowDeckConfigEntry
    from .coordinator import MowDeckCoordinator

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MowDeckConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """A command event and a notification event per mower; a Vision event per mower where
    MowDeck runs Vision."""
    vision = entry.runtime_data.vision
    async_add_mower_entities(
        entry,
        async_add_entities,
        lambda coordinator, m: [VisionEvent(coordinator, m)] if vision.data.available else [],
        fingerprint=lambda m: vision.data.available,
        listen=[vision],
    )
    async_add_mower_entities(
        entry,
        async_add_entities,
        lambda coordinator, m: [CommandEvent(coordinator, m), NotificationEvent(coordinator, m)],
    )


class _DispatchedEvent(MowDeckEntity, EventEntity):
    """An event entity fed by a dispatcher signal of its mower."""

    def _signal(self) -> str:  # pragma: no cover - every event entity overrides it
        raise NotImplementedError

    def _event(  # pragma: no cover - every event entity overrides it
        self, payload: dict[str, Any]
    ) -> tuple[str, dict[str, Any]] | None:
        raise NotImplementedError

    async def async_added_to_hass(self) -> None:
        """Listen to the mower's signal."""
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, self._signal(), self._fire))

    @callback
    def _fire(self, payload: dict[str, Any]) -> None:
        event = self._event(payload)
        if event is None:
            return
        self._trigger_event(*event)
        self.async_write_ha_state()


class CommandEvent(_DispatchedEvent):
    """Status changes of the mower's commands: the event type is the new status."""

    entity_description = EventEntityDescription(key="command", event_types=COMMAND_STATES)

    def __init__(self, coordinator: MowDeckCoordinator, mower_id: str) -> None:
        """Create the event entity."""
        super().__init__(coordinator, mower_id, "command")

    def _signal(self) -> str:
        return command_signal(self.coordinator.config_entry.entry_id, self.mower_id)

    def _event(self, payload: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        status = payload.get("status")
        if status not in COMMAND_STATES:
            return None
        return str(status), {k: v for k, v in payload.items() if k not in ("status", "mower_id")}


class NotificationEvent(_DispatchedEvent):
    """The mower's MowDeck notifications: the event type is the notification kind."""

    entity_description = EventEntityDescription(key="notification", event_types=NOTIFICATION_KINDS)

    def __init__(self, coordinator: MowDeckCoordinator, mower_id: str) -> None:
        """Create the event entity."""
        super().__init__(coordinator, mower_id, "notification")

    def _signal(self) -> str:
        return notification_signal(self.coordinator.config_entry.entry_id, self.mower_id)

    def _event(self, payload: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        kind = payload.get("kind")
        if kind not in NOTIFICATION_KINDS:
            return None
        keys = ("notification_id", "scope", "severity", "title", "body", "url", "occurred_at")
        return str(kind), {k: payload.get(k) for k in keys}


class VisionEvent(_DispatchedEvent):
    """The mower's Vision proposals: ``created`` for a new one, ``updated`` for every later
    state change; the attributes say which proposal and where it stands."""

    entity_description = EventEntityDescription(key="ai_recovery", event_types=PROPOSAL_EVENTS)

    def __init__(self, coordinator: MowDeckCoordinator, mower_id: str) -> None:
        """Create the event entity."""
        super().__init__(coordinator, mower_id, "ai_recovery")

    def _signal(self) -> str:
        return vision_signal(self.coordinator.config_entry.entry_id, self.mower_id)

    def _event(self, payload: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        event = payload.get("event")
        if event not in PROPOSAL_EVENTS:
            return None
        keys = ("proposal_id", "state", "action", "final", "link")
        return str(event), {k: payload.get(k) for k in keys}
