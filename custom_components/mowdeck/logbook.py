"""Logbook descriptions of the MowDeck bus events."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.logbook import LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, EVENT_COMMAND_RESULT, EVENT_NOTIFICATION


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, str]]], None],
) -> None:
    """Describe ``mowdeck_notification`` and ``mowdeck_command_result``."""

    def mower_name(data: dict[str, Any]) -> str:
        name = data.get("mower_name")
        if name:
            return str(name)
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            mower = entry.runtime_data.coordinator.data.mowers.get(str(data.get("mower_id")))
            if mower and mower.get("name"):
                return str(mower["name"])
        return "MowDeck"

    @callback
    def describe_notification(event: Event) -> dict[str, str]:
        data = dict(event.data)
        title = str(data.get("title") or data.get("kind") or "notification")
        body = str(data.get("body") or "")
        message = f"notified: {title}" + (f" ({body})" if body else "")
        return {LOGBOOK_ENTRY_NAME: mower_name(data), LOGBOOK_ENTRY_MESSAGE: message}

    @callback
    def describe_command(event: Event) -> dict[str, str]:
        data = dict(event.data)
        message = f"command {data.get('command_id', '')} is {data.get('status', 'unknown')}"
        if data.get("reason"):
            message += f": {data['reason']}"
        return {LOGBOOK_ENTRY_NAME: mower_name(data), LOGBOOK_ENTRY_MESSAGE: message}

    async_describe_event(DOMAIN, EVENT_NOTIFICATION, describe_notification)
    async_describe_event(DOMAIN, EVENT_COMMAND_RESULT, describe_command)
