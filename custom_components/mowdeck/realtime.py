"""Realtime WebSocket client: pushes mower states into the coordinator.

Protocol (services/realtime/README.md in the monorepo): send ``auth`` with an access token as
the first message, wait for ``ready``, ``subscribe`` to the mower ids, then receive ``state``,
``command_result``, ``notification`` and ``revoked`` messages (``notification`` carries the
account's notifications to API tokens with ``notifications.view``; they go to the notification
hub). Before the access token expires a fresh one is sent with another ``auth``. The socket
reconnects with exponential backoff; close code 4401 (unauthorized) triggers a token refresh,
and when even a fresh token is refused (or the API token itself is rejected) a Repairs issue
is raised that stays until a connection succeeds.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .api import MowDeckAuthError, MowDeckClient, MowDeckError
from .const import DOMAIN, EVENT_COMMAND_RESULT, ISSUE_REALTIME_UNAUTHORIZED, TOKEN_PAGE
from .coordinator import MowDeckCoordinator

_LOGGER = logging.getLogger(__name__)

CLOSE_UNAUTHORIZED = 4401
CLOSE_LIMIT = 4429
CLOSE_SLOW_CONSUMER = 4430

MIN_BACKOFF = 1.0
MAX_BACKOFF = 60.0
LIMIT_BACKOFF = 120.0
UNAUTHORIZED_BACKOFF = 300.0
READY_TIMEOUT = 10.0
# Consecutive 4401 closes with a freshly exchanged token before a Repairs issue is raised.
UNAUTHORIZED_THRESHOLD = 2


def command_signal(entry_id: str, mower_id: str) -> str:
    """Dispatcher signal of a mower's command results."""
    return f"{DOMAIN}_command_{entry_id}_{mower_id}"


class _ClosedError(Exception):
    """The server closed the socket (or the connection broke)."""

    def __init__(self, code: int | None) -> None:
        super().__init__(f"closed with {code}")
        self.code = code


class RealtimeClient:
    """Keeps one WebSocket to the MowDeck Realtime service open."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: MowDeckClient,
        coordinator: MowDeckCoordinator,
        *,
        entry_id: str,
        on_auth_failed: Callable[[], None],
        on_notification: Callable[[Any], None] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Create the runner; ``run`` connects."""
        self._hass = hass
        self._client = client
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._on_auth_failed = on_auth_failed
        self._on_notification = on_notification
        self._sleep = sleep
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._subscribed: set[str] = set()
        self._stopped = False
        self._unauthorized = 0
        self._remove_listener: Callable[[], None] | None = None
        self.connected = False
        self.last_close_code: int | None = None

    @property
    def issue_id(self) -> str:
        """Repairs issue id of this entry."""
        return f"{ISSUE_REALTIME_UNAUTHORIZED}_{self._entry_id}"

    async def run(self) -> None:
        """Connect, receive and reconnect until ``stop``."""
        self._remove_listener = self._coordinator.async_add_listener(self._on_coordinator_update)
        backoff = MIN_BACKOFF
        try:
            while not self._stopped:
                try:
                    delay = await self._attempt(backoff)
                except MowDeckAuthError:
                    _LOGGER.warning("The MowDeck API token was rejected; realtime stops")
                    self._raise_issue()
                    self._on_auth_failed()
                    return
                finally:
                    self._set_connected(False)
                if self._stopped:
                    break
                # Jitter keeps many Home Assistant instances from reconnecting in lockstep.
                await self._sleep(delay * random.uniform(0.8, 1.2))  # noqa: S311
                backoff = min(MAX_BACKOFF, max(MIN_BACKOFF, delay) * 2)
        finally:
            if self._remove_listener is not None:
                self._remove_listener()
                self._remove_listener = None

    async def stop(self) -> None:
        """Close the socket and end ``run``."""
        self._stopped = True
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()

    async def _attempt(self, backoff: float) -> float:
        """One connection; returns the delay before the next one.

        Raises MowDeckAuthError when the API token itself is rejected.
        """
        try:
            await self._connect_and_listen()
        except _ClosedError as closed:
            self.last_close_code = closed.code
            try:
                return await self._handle_close(closed.code, backoff)
            except MowDeckAuthError:
                raise
            except MowDeckError as err:
                _LOGGER.debug("Token refresh after close failed: %r", err)
                return backoff
        except MowDeckAuthError:
            raise
        except (aiohttp.ClientError, TimeoutError, MowDeckError, OSError) as err:
            _LOGGER.debug("Realtime connection failed: %r", err)
            return backoff
        return MIN_BACKOFF

    async def _handle_close(self, code: int | None, backoff: float) -> float:
        """Decide how long to wait before reconnecting after a close."""
        if code == CLOSE_UNAUTHORIZED:
            self._unauthorized += 1
            # Expiry without refresh also ends in 4401: a fresh token usually fixes it.
            await self._client.async_get_access_token(force_refresh=True)
            if self._unauthorized >= UNAUTHORIZED_THRESHOLD:
                _LOGGER.warning("MowDeck realtime refuses a fresh token (4401); polling only")
                self._raise_issue()
                return UNAUTHORIZED_BACKOFF
            return MIN_BACKOFF
        if code == CLOSE_LIMIT:
            _LOGGER.warning("MowDeck realtime connection limit reached (4429)")
            return LIMIT_BACKOFF
        _LOGGER.debug("MowDeck realtime closed with %s, reconnecting", code)
        return backoff

    async def _connect_and_listen(self) -> None:
        token = await self._client.async_get_access_token()
        async with self._client.session.ws_connect(
            self._client.websocket_url, heartbeat=None, autoping=True
        ) as ws:
            self._ws = ws
            try:
                await ws.send_json({"type": "auth", "token": token})
                await self._wait_ready(ws)
                self._unauthorized = 0
                ir.async_delete_issue(self._hass, DOMAIN, self.issue_id)
                self._set_connected(True)
                self._subscribed = set()
                await self._subscribe(ws)
                refresher = asyncio.create_task(self._refresh_loop(ws))
                try:
                    await self._listen(ws)
                finally:
                    refresher.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await refresher
            finally:
                self._ws = None

    async def _wait_ready(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async with asyncio.timeout(READY_TIMEOUT):
            while True:
                message = await self._receive(ws)
                if message.get("type") == "ready":
                    return
                if message.get("type") == "error":
                    _LOGGER.debug("Realtime error before ready: %s", message)

    async def _receive(self, ws: aiohttp.ClientWebSocketResponse) -> dict[str, Any]:
        msg = await ws.receive()
        if msg.type == aiohttp.WSMsgType.TEXT:
            try:
                decoded = json.loads(msg.data)
            except ValueError:
                return {}
            return decoded if isinstance(decoded, dict) else {}
        if msg.type in (
            aiohttp.WSMsgType.CLOSE,
            aiohttp.WSMsgType.CLOSING,
            aiohttp.WSMsgType.CLOSED,
            aiohttp.WSMsgType.ERROR,
        ):
            raise _ClosedError(ws.close_code)
        return {}

    async def _listen(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        while True:
            message = await self._receive(ws)
            kind = message.get("type")
            if kind == "state":
                mower_id, state = message.get("mower_id"), message.get("state")
                if isinstance(mower_id, str) and isinstance(state, dict):
                    self._coordinator.async_push_state(mower_id, state)
            elif kind == "command_result":
                result = {"mower_id": message.get("mower_id"), **(message.get("result") or {})}
                self._hass.bus.async_fire(EVENT_COMMAND_RESULT, result)
                if isinstance(result["mower_id"], str):
                    async_dispatcher_send(
                        self._hass, command_signal(self._entry_id, result["mower_id"]), result
                    )
            elif kind == "notification" and self._on_notification is not None:
                self._on_notification(message.get("notification"))
            elif kind == "revoked":
                mower_ids = [str(m) for m in message.get("mower_ids", [])]
                self._subscribed -= set(mower_ids)
                self._coordinator.async_mower_revoked(mower_ids)
            elif kind == "subscribed" and message.get("denied"):
                _LOGGER.debug("Realtime denied subscriptions: %s", message["denied"])
            elif kind == "error":
                _LOGGER.debug("Realtime error: %s", message)

    async def _subscribe(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        wanted = set(self._coordinator.data.mowers) - self._subscribed
        if not wanted:
            return
        self._subscribed |= wanted
        await ws.send_json({"type": "subscribe", "mower_ids": sorted(wanted)})

    def _on_coordinator_update(self) -> None:
        """Subscribe to mowers that a poll discovered."""
        ws = self._ws
        if ws is None or ws.closed or not self.connected:
            return
        if set(self._coordinator.data.mowers) - self._subscribed:
            self._hass.async_create_background_task(
                self._subscribe(ws), "mowdeck realtime subscribe", eager_start=False
            )

    async def _refresh_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Send a fresh token before the current one expires (same subject and account)."""
        while True:
            wait = self._client.token_expires_in - self._client.refresh_margin
            await self._sleep(max(1.0, wait))
            token = await self._client.async_get_access_token()
            await ws.send_json({"type": "auth", "token": token})

    def _set_connected(self, connected: bool) -> None:
        if self.connected != connected:
            self.connected = connected
            self._coordinator.async_set_realtime_connected(connected)

    def _raise_issue(self) -> None:
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            self.issue_id,
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.ERROR,
            learn_more_url=f"{self._client.base_url}{TOKEN_PAGE}",
            translation_key=ISSUE_REALTIME_UNAUTHORIZED,
            translation_placeholders={"url": self._client.base_url},
        )
