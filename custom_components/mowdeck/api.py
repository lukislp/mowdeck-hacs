"""Small async client of the public MowDeck API.

Deliberately self-contained: HACS installs only this folder, so nothing here may depend on
the MowDeck Python libraries. Field names follow the OpenAPI documents of the services
(``apps/web/openapi/*.json`` in lukislp/mowdeck).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp

_LOGGER = logging.getLogger(__name__)

IDENTITY = "/api/v1/identity"
FLEET = "/api/v1/fleet"
TELEMETRY = "/api/v1/telemetry"
REALTIME_WS = "/api/v1/realtime/ws"

# Questions per ``POST /identity/me/permissions`` (the API accepts up to 100).
PERMISSION_BATCH = 100

# Statuses worth retrying for idempotent requests.
_RETRY_STATUSES = frozenset({502, 503, 504})


@dataclass(frozen=True, slots=True)
class Problem:
    """An RFC 9457 problem document (``application/problem+json``)."""

    status: int
    title: str | None = None
    detail: str | None = None
    type: str | None = None
    correlation_id: str | None = None

    @classmethod
    def from_body(cls, status: int, body: Any) -> Problem:
        """Read the fields we use from a decoded problem document (or anything else)."""
        if not isinstance(body, Mapping):
            return cls(status=status)

        def text(key: str) -> str | None:
            value = body.get(key)
            return value if isinstance(value, str) else None

        return cls(
            status=status,
            title=text("title"),
            detail=text("detail"),
            type=text("type"),
            correlation_id=text("correlation_id"),
        )

    def __str__(self) -> str:
        """Human readable summary for logs and errors."""
        message = self.detail or self.title or "error"
        suffix = f" (correlation id {self.correlation_id})" if self.correlation_id else ""
        return f"HTTP {self.status}: {message}{suffix}"


@dataclass(frozen=True, slots=True)
class WhepResponse:
    """The parts of a WHEP answer the integration reads (never the ticket)."""

    status: int
    location: str | None = None
    body: str = ""


class MowDeckError(Exception):
    """Base error of the MowDeck client."""


class MowDeckConnectionError(MowDeckError):
    """The API could not be reached or answered with a server error."""


class MowDeckApiError(MowDeckError):
    """The API answered with a problem document."""

    def __init__(self, problem: Problem) -> None:
        """Keep the problem for callers."""
        super().__init__(str(problem))
        self.problem = problem

    @property
    def status(self) -> int:
        """The HTTP status."""
        return self.problem.status


class MowDeckAuthError(MowDeckApiError):
    """The API token is invalid, expired or revoked (401, or 403 on the token exchange)."""


class MowDeckForbiddenError(MowDeckApiError):
    """The token lacks the permission for this request (403)."""


class MowDeckNotFoundError(MowDeckApiError):
    """Unknown or invisible resource (404; other accounts' resources look the same)."""


def normalize_base_url(url: str) -> str:
    """Strip whitespace, a trailing slash, query and fragment; require http(s) and a host."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        msg = f"not an http(s) URL: {url!r}"
        raise ValueError(msg)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def websocket_url(base_url: str) -> str:
    """The Realtime WebSocket URL for a base URL (``ws`` for http, ``wss`` for https)."""
    parts = urlsplit(base_url)
    scheme = "wss" if parts.scheme == "https" else "ws"
    return urlunsplit((scheme, parts.netloc, parts.path + REALTIME_WS, "", ""))


class MowDeckClient:
    """Async MowDeck API client authenticated with an API token.

    The API token is exchanged for a 10 minute access token (``POST /identity/token``), which
    is refreshed ``refresh_margin`` seconds before it expires and once more after a 401.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        api_token: str,
        *,
        retries: int = 3,
        backoff: float = 0.5,
        refresh_margin: float = 60.0,
        request_timeout: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create the client; no I/O happens before the first request."""
        self._session = session
        self.base_url = normalize_base_url(base_url)
        self._api_token = api_token
        self._retries = retries
        self._backoff = backoff
        self._refresh_margin = refresh_margin
        self._timeout = aiohttp.ClientTimeout(total=request_timeout)
        self._clock = clock
        self._access_token: str | None = None
        self._expires_at = 0.0
        self._token_lock = asyncio.Lock()
        self.account_id: str | None = None

    @property
    def session(self) -> aiohttp.ClientSession:
        """The aiohttp session (shared with Home Assistant)."""
        return self._session

    @property
    def websocket_url(self) -> str:
        """The Realtime WebSocket URL."""
        return websocket_url(self.base_url)

    @property
    def token_expires_in(self) -> float:
        """Seconds until the current access token expires (0 without a token)."""
        return max(0.0, self._expires_at - self._clock()) if self._access_token else 0.0

    @property
    def refresh_margin(self) -> float:
        """Seconds before expiry at which the access token is refreshed."""
        return self._refresh_margin

    async def async_get_access_token(self, *, force_refresh: bool = False) -> str:
        """A valid access token, exchanging the API token when needed."""
        async with self._token_lock:
            if (
                force_refresh
                or self._access_token is None
                or self._expires_at - self._clock() <= self._refresh_margin
            ):
                await self._exchange()
            assert self._access_token is not None  # noqa: S101 - set by _exchange
            return self._access_token

    async def _exchange(self) -> None:
        body = await self._send(
            "POST",
            f"{IDENTITY}/token",
            json={"grant_type": "api_token", "token": self._api_token},
            authenticated=False,
            idempotent=True,
        )
        if not isinstance(body, Mapping) or not isinstance(body.get("access_token"), str):
            msg = "token exchange returned no access token"
            raise MowDeckConnectionError(msg)
        self._access_token = body["access_token"]
        expires_in = body.get("expires_in")
        self._expires_at = self._clock() + (
            float(expires_in) if isinstance(expires_in, int | float) else 600.0
        )
        account_id = body.get("account_id")
        self.account_id = str(account_id) if account_id else None
        _LOGGER.debug("Exchanged the API token, access token valid for %ss", expires_in)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
        allow_not_modified: bool = False,
    ) -> Any:
        """An authenticated request; refreshes the access token once after a 401."""
        idempotent = method == "GET"
        try:
            return await self._send(
                method,
                path,
                json=json,
                headers=headers,
                idempotent=idempotent,
                allow_not_modified=allow_not_modified,
            )
        except MowDeckAuthError:
            # The access token may have been invalidated early (key rotation, clock skew).
            # A revoked API token fails this exchange with MowDeckAuthError again.
            await self.async_get_access_token(force_refresh=True)
            return await self._send(
                method,
                path,
                json=json,
                headers=headers,
                idempotent=idempotent,
                allow_not_modified=allow_not_modified,
            )

    async def _send(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
        authenticated: bool = True,
        idempotent: bool,
        allow_not_modified: bool = False,
    ) -> Any:
        attempt = 0
        while True:
            all_headers = {"Accept": "application/json, application/problem+json"}
            if headers:
                all_headers.update(headers)
            if authenticated:
                all_headers["Authorization"] = f"Bearer {await self.async_get_access_token()}"
            failure: MowDeckError
            try:
                async with self._session.request(
                    method,
                    self.base_url + path,
                    json=json,
                    headers=all_headers,
                    timeout=self._timeout,
                ) as response:
                    if response.status == 304 and allow_not_modified:
                        return None
                    if response.status == 204:
                        return None
                    try:
                        body = await response.json(content_type=None)
                    except ValueError:
                        body = None
                    if response.status < 400:
                        return body
                    problem = Problem.from_body(response.status, body)
                    if response.status in _RETRY_STATUSES and idempotent:
                        raise _RetryableStatusError(problem)
                    raise _error_for(problem, authenticated=authenticated)
            except _RetryableStatusError as err:
                failure = MowDeckConnectionError(str(err.problem))
            except aiohttp.ClientConnectorError as err:
                # Nothing reached the server: safe to retry even for commands.
                failure = MowDeckConnectionError(f"cannot connect to {self.base_url}: {err}")
            except (aiohttp.ClientError, TimeoutError) as err:
                failure = MowDeckConnectionError(f"request to {self.base_url} failed: {err!r}")
                if not idempotent:
                    # The command may have reached Fleet; never send it twice.
                    raise failure from err
            if attempt >= self._retries:
                raise failure
            delay = self._backoff * (2**attempt)
            attempt += 1
            _LOGGER.debug(
                "%s %s failed (%s), retry %s in %ss", method, path, failure, attempt, delay
            )
            await asyncio.sleep(delay)

    # Identity

    async def get_me(self) -> dict[str, Any]:
        """``GET /identity/me``: the token's subject, account and permissions."""
        return dict(await self._request("GET", f"{IDENTITY}/me"))

    async def check_permissions(self, checks: list[tuple[str, str]]) -> list[bool]:
        """``POST /identity/me/permissions``: may the token do ``relation`` on ``object``?

        ``checks`` are ``(relation, object)`` pairs such as ``("can_control", "mower:<id>")``;
        the answers come back in the same order. Asked in batches of ``PERMISSION_BATCH``.
        """
        answers: list[bool] = []
        for start in range(0, len(checks), PERMISSION_BATCH):
            batch = checks[start : start + PERMISSION_BATCH]
            body = await self._request(
                "POST",
                f"{IDENTITY}/me/permissions",
                json={"checks": [{"relation": r, "object": o} for r, o in batch]},
            )
            if not isinstance(body, list) or len(body) != len(batch):
                msg = "permission check returned an unexpected answer"
                raise MowDeckConnectionError(msg)
            answers.extend(bool(answer) for answer in body)
        return answers

    # Fleet

    async def list_mowers(self) -> list[dict[str, Any]]:
        """``GET /fleet/mowers``: the mowers the token may see."""
        body = await self._request("GET", f"{FLEET}/mowers")
        return [dict(item) for item in body.get("items", [])]

    async def send_command(
        self, mower_id: str, command: Mapping[str, Any], *, ttl_seconds: int | None = None
    ) -> dict[str, Any]:
        """``POST /fleet/mowers/{id}/commands`` with a ``CommandRequest``; the 202 body."""
        payload: dict[str, Any] = {"command": dict(command)}
        if ttl_seconds is not None:
            payload["ttl_seconds"] = ttl_seconds
        return dict(
            await self._request("POST", f"{FLEET}/mowers/{mower_id}/commands", json=payload)
        )

    async def check_firmware(self, mower_id: str) -> dict[str, Any]:
        """``POST /fleet/mowers/{id}/firmware/check``: ask the vendor for updates now; the fresh
        ``FirmwareInfo``. 409 when the connection cannot ask, 503 when the mower is offline."""
        return dict(await self._request("POST", f"{FLEET}/mowers/{mower_id}/firmware/check"))

    async def get_command(self, command_id: str) -> dict[str, Any]:
        """``GET /fleet/commands/{id}``: the command record with its status."""
        return dict(await self._request("GET", f"{FLEET}/commands/{command_id}"))

    # Camera relay (ADR 0045 in lukislp/mowdeck)

    async def open_camera_session(self, mower_id: str) -> dict[str, Any]:
        """``POST /fleet/mowers/{id}/camera/session`` with ``transport`` ``relay``: a new viewer
        lease of the token on the camera relay (WHEP URL per camera, ticket, lease id).

        ``relay`` and not ``auto``: the direct vendor path of ``auto``'s fallback is played by
        the vendor's browser SDK, which Home Assistant cannot do. Never retried (each request
        opens a lease)."""
        return dict(
            await self._request(
                "POST", f"{FLEET}/mowers/{mower_id}/camera/session", json={"transport": "relay"}
            )
        )

    async def renew_camera_session(self, mower_id: str, lease_id: str) -> dict[str, Any]:
        """``POST .../camera/session/{lease_id}/renew``: the lease's heartbeat with a fresh
        ticket; :class:`MowDeckNotFoundError` when the lease is gone."""
        return dict(
            await self._request(
                "POST", f"{FLEET}/mowers/{mower_id}/camera/session/{lease_id}/renew"
            )
        )

    async def close_camera_session(self, mower_id: str, lease_id: str) -> None:
        """``DELETE .../camera/session/{lease_id}``: end the lease (idempotent)."""
        await self._request("DELETE", f"{FLEET}/mowers/{mower_id}/camera/session/{lease_id}")

    def media_url(self, url: str) -> str:
        """An absolute URL for a WHEP URL of the relay: an absolute path is on the MowDeck
        origin, an ``http(s)`` URL stays as it is."""
        if url.startswith("/") and not url.startswith("//"):
            parts = urlsplit(self.base_url)
            return urlunsplit((parts.scheme, parts.netloc, url, "", ""))
        if urlsplit(url).scheme in ("http", "https"):
            return url
        msg = f"not a WHEP URL: {url!r}"
        raise ValueError(msg)

    async def whep_request(
        self,
        method: str,
        url: str,
        ticket: str,
        *,
        body: str | None = None,
        content_type: str | None = None,
    ) -> WhepResponse:
        """One WHEP request (RFC 9725) to the relay's media server with the ticket as bearer
        token; the status, ``Location`` and body come back as they are. Connection errors and
        timeouts raise :class:`MowDeckConnectionError`."""
        headers = {"Authorization": f"Bearer {ticket}"}
        if content_type:
            headers["Content-Type"] = content_type
        if method == "POST":
            headers["Accept"] = "application/sdp"
        try:
            async with self._session.request(
                method, url, data=body, headers=headers, timeout=self._timeout
            ) as response:
                text = await response.text() if response.status == 201 else ""
                return WhepResponse(response.status, response.headers.get("Location"), text)
        except (aiohttp.ClientError, TimeoutError) as err:
            msg = f"WHEP {method} failed: {type(err).__name__}"
            raise MowDeckConnectionError(msg) from err

    # Telemetry

    async def get_states(self) -> list[dict[str, Any]]:
        """``GET /telemetry/accounts/me/mowers/states``: latest state snapshots."""
        body = await self._request("GET", f"{TELEMETRY}/accounts/me/mowers/states")
        return [dict(item) for item in body.get("items", [])]

    async def get_state(self, mower_id: str) -> dict[str, Any] | None:
        """``GET /telemetry/mowers/{id}/state``; None while no state was stored."""
        try:
            return dict(await self._request("GET", f"{TELEMETRY}/mowers/{mower_id}/state"))
        except MowDeckNotFoundError:
            return None

    async def get_capabilities(self, mower_id: str) -> dict[str, Any] | None:
        """``GET /telemetry/mowers/{id}/capabilities``; None while unknown."""
        try:
            return dict(await self._request("GET", f"{TELEMETRY}/mowers/{mower_id}/capabilities"))
        except MowDeckNotFoundError:
            return None

    async def get_map(self, mower_id: str, *, revision: str | None = None) -> dict[str, Any] | None:
        """``GET /telemetry/mowers/{id}/map``; None when unchanged (304) or unknown (404)."""
        headers = {"If-None-Match": f'"{revision}"'} if revision else None
        try:
            body = await self._request(
                "GET",
                f"{TELEMETRY}/mowers/{mower_id}/map",
                headers=headers,
                allow_not_modified=True,
            )
        except MowDeckNotFoundError:
            return None
        return dict(body) if body is not None else None

    async def get_health(self, mower_id: str) -> dict[str, Any] | None:
        """``GET /telemetry/mowers/{id}/health``: running totals; None before any telemetry."""
        try:
            return dict(await self._request("GET", f"{TELEMETRY}/mowers/{mower_id}/health"))
        except MowDeckNotFoundError:
            return None

    async def reset_blade(self, mower_id: str, note: str | None = None) -> dict[str, Any]:
        """``POST /telemetry/mowers/{id}/health/blade-reset``: record a blade change; the new
        health totals (``can_manage_mowers``)."""
        body: dict[str, Any] = {"note": note} if note else {}
        return dict(
            await self._request(
                "POST", f"{TELEMETRY}/mowers/{mower_id}/health/blade-reset", json=body
            )
        )


class _RetryableStatusError(Exception):
    """Internal: a 502/503/504 answer to an idempotent request."""

    def __init__(self, problem: Problem) -> None:
        super().__init__(str(problem))
        self.problem = problem


def _error_for(problem: Problem, *, authenticated: bool) -> MowDeckError:
    if problem.status == 401 or (problem.status == 403 and not authenticated):
        # 401 anywhere, or 403 on the exchange (a disabled owner or account).
        return MowDeckAuthError(problem)
    if problem.status == 403:
        return MowDeckForbiddenError(problem)
    if problem.status == 404:
        return MowDeckNotFoundError(problem)
    if problem.status >= 500:
        return MowDeckConnectionError(str(problem))
    return MowDeckApiError(problem)
