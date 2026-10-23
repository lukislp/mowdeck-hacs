"""Commands whose refusal the user should see: send, then wait briefly for the mower's verdict.

Fleet answers a command with 202 before the mower's controller has checked it against its
state rules (a restart only at rest, a setting only within its declaration, no nudge while a
job runs, ...). The controller's verdict arrives as a ``command_result`` over the Realtime
WebSocket. For the commands sent here the integration waits up to ``COMMAND_RESULT_TIMEOUT``
for the first result of the command and turns a refusal into a translated
``HomeAssistantError`` that carries MowDeck's reason. Without a connected socket, or when no
result arrives in time, the command counts as sent.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN
from .realtime import command_signal

if TYPE_CHECKING:
    from .entity import MowDeckEntity

# Seconds to wait for the controller's verdict on a command.
COMMAND_RESULT_TIMEOUT = 10.0
# Command statuses that mean the mower did not (and will not) carry the command out.
REFUSED = frozenset({"rejected", "failed", "expired"})


async def async_send_confirmed(
    entity: MowDeckEntity, command: dict[str, Any], permission: str
) -> dict[str, Any]:
    """Send ``command`` for the entity's mower; raise when the mower refuses it.

    ``permission`` is the API token permission a 403 names. Results are collected from before
    the request on, so a verdict that arrives before Fleet's answer is not missed."""
    coordinator = entity.coordinator
    kind = str(command["kind"])
    results: list[dict[str, Any]] = []
    arrived = asyncio.Event()

    @callback
    def _collect(result: dict[str, Any]) -> None:
        results.append(result)
        arrived.set()

    signal = command_signal(coordinator.config_entry.entry_id, entity.mower_id)
    unsubscribe = async_dispatcher_connect(entity.hass, signal, _collect)
    try:
        accepted = await entity.async_call_api(
            coordinator.client.send_command(entity.mower_id, command),
            kind,
            permission,
            forbidden_key="command_forbidden",
            failed_key="command_failed",
        )
        command_id = accepted.get("command_id")
        if not command_id or not coordinator.realtime_connected:
            return accepted
        try:
            async with asyncio.timeout(COMMAND_RESULT_TIMEOUT):
                while True:
                    for result in results:
                        if result.get("command_id") == command_id:
                            _raise_if_refused(kind, result)
                            return accepted
                    arrived.clear()
                    await arrived.wait()
        except TimeoutError:
            return accepted
    finally:
        unsubscribe()


def _raise_if_refused(kind: str, result: dict[str, Any]) -> None:
    status = result.get("status")
    if status not in REFUSED:
        return
    reason = result.get("reason")
    raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="command_refused",
        translation_placeholders={
            "command": kind,
            "status": str(status),
            "reason": str(reason) if reason else "-",
        },
    )
