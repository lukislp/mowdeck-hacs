"""Diagnostics of a MowDeck config entry (the API token and connection secrets redacted)."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_API_TOKEN

if TYPE_CHECKING:
    from . import MowDeckConfigEntry

TO_REDACT = {
    CONF_API_TOKEN,
    "access_token",
    "token",
    "connection_options",
    "serial",
    "iot_id",
    "linked_account_id",
    "position",
    "boundary",
    "points",
    "reference",
    "dock",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MowDeckConfigEntry
) -> dict[str, Any]:
    """Entry data, options, realtime status and the coordinator data."""
    runtime = entry.runtime_data
    data = asdict(runtime.coordinator.data)
    data["received_at"] = {k: v.isoformat() for k, v in data["received_at"].items()}
    return {
        "entry": async_redact_data(
            {"data": dict(entry.data), "options": dict(entry.options)}, TO_REDACT
        ),
        "realtime": {
            "connected": runtime.realtime.connected,
            "last_close_code": runtime.realtime.last_close_code,
        },
        "coordinator": async_redact_data(data, TO_REDACT),
    }
