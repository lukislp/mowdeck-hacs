"""Diagnostics of a MowDeck config entry.

Redacted: the API token and connection secrets, serials, vendor ids, positions and every
coordinate, area names, and the titles and bodies of notifications."""

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
    "lat",
    "lon",
    "latitude",
    "longitude",
    "polygon",
    "area_names",
    "title",
    "body",
    "owner",
}


def _map_summary(mower_map: dict[str, Any]) -> dict[str, Any]:
    """A map without names and geometry: what it contains, not where or what it is called."""
    return {
        "revision": mower_map.get("revision"),
        "areas": len(mower_map.get("areas") or []),
        "paths": len(mower_map.get("paths") or []),
        "no_go_zones": len(mower_map.get("no_go_zones") or []),
        "obstacles": len(mower_map.get("obstacles") or []),
        "dock": mower_map.get("dock") is not None,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MowDeckConfigEntry
) -> dict[str, Any]:
    """Entry data, options, realtime status and the coordinator data."""
    runtime = entry.runtime_data
    data = asdict(runtime.coordinator.data)
    data["received_at"] = {k: v.isoformat() for k, v in data["received_at"].items()}
    data["maps"] = {k: _map_summary(v) for k, v in data["maps"].items()}
    data["permissions"] = {k: sorted(v) for k, v in data["permissions"].items()}
    return {
        "entry": async_redact_data(
            {"data": dict(entry.data), "options": dict(entry.options)}, TO_REDACT
        ),
        "realtime": {
            "connected": runtime.realtime.connected,
            "last_close_code": runtime.realtime.last_close_code,
        },
        "coordinator": async_redact_data(data, TO_REDACT),
        "health": {
            "last_update_success": runtime.health.last_update_success,
            "forbidden": runtime.health.forbidden,
            "totals": async_redact_data(runtime.health.data.totals, TO_REDACT),
        },
        "scheduler": {
            "last_update_success": runtime.scheduler.last_update_success,
            "forbidden": runtime.scheduler.forbidden,
            "available": runtime.scheduler.data.available,
            "schedules": async_redact_data(runtime.scheduler.data.schedules, TO_REDACT),
            "runs": async_redact_data(runtime.scheduler.data.runs, TO_REDACT),
            "weather": async_redact_data(runtime.scheduler.data.weather, TO_REDACT),
            "permissions": {k: sorted(v) for k, v in runtime.scheduler.data.permissions.items()},
        },
        "notifications": {
            "received": runtime.notifications.received,
            "delivered": runtime.notifications.delivered_count,
        },
    }
