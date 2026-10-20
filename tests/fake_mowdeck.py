"""An in-process fake of the public MowDeck API (HTTP and the Realtime WebSocket).

Only what the integration uses, with the field names of the OpenAPI documents in
``apps/web/openapi``; errors are problem+json documents like the services return.
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from aiohttp import WSCloseCode, WSMsgType, web
from aiohttp.test_utils import TestServer

API_TOKEN = "mdk_test_token"  # noqa: S105 - test fixture
ACCOUNT_ID = "4f9c3a0e-7d1b-4c55-9a51-0b7d6f1e2a10"
OTHER_ACCOUNT_ID = "0e2a9c51-1b7d-4f6e-8a10-5c3d7f9a0b11"
MOWER_ID = "9a5c2d6e-3f10-4b8a-9c7e-1d2f3a4b5c6d"


def now_iso() -> str:
    """The current UTC time in the API's format."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def problem(status: int, title: str, detail: str) -> web.Response:
    """A problem+json response."""
    return web.json_response(
        {
            "type": f"urn:mowdeck:problem:{title.lower().replace(' ', '-')}",
            "title": title,
            "status": status,
            "detail": detail,
            "correlation_id": "test-correlation",
        },
        status=status,
        content_type="application/problem+json",
    )


def mower_state(**overrides: Any) -> dict[str, Any]:
    """A MowerState as Telemetry and Realtime deliver it."""
    state: dict[str, Any] = {
        "mower_id": MOWER_ID,
        "account_id": ACCOUNT_ID,
        "observed_at": now_iso(),
        "activity": "docked",
        "battery_percent": 87,
        "charging": True,
        "connection": "simulated",
        "rtk_fix": "fixed",
        "rtk_source": "base_station",
        "blade_height_mm": 50,
        "speed_mps": 0.0,
        "position": {"lat": 48.137, "lon": 11.575, "heading": 90.0},
        "firmware": "sim-1.0",
        "light_on": False,
        "error_code": None,
        "error_message": None,
        "raw": {},
    }
    state.update(overrides)
    return state


@dataclass
class FakeMowDeck:
    """State and behaviour of the fake API; tests change the fields to steer it."""

    valid_api_tokens: dict[str, str] = field(
        default_factory=lambda: {API_TOKEN: ACCOUNT_ID, "other-token": OTHER_ACCOUNT_ID}
    )
    expires_in: int = 600
    mowers: list[dict[str, Any]] = field(default_factory=list)
    states: dict[str, dict[str, Any]] = field(default_factory=dict)
    capabilities: dict[str, dict[str, Any]] = field(default_factory=dict)
    maps: dict[str, dict[str, Any]] = field(default_factory=dict)
    commands: list[dict[str, Any]] = field(default_factory=list)
    forbid_commands: bool = False
    fail_requests: int = 0
    exchanges: int = 0
    requests: list[str] = field(default_factory=list)
    ws_auth_tokens: list[str] = field(default_factory=list)
    ws_subscriptions: list[list[str]] = field(default_factory=list)
    ws_reject_auth: bool = False
    ws_connections: int = 0
    _access_tokens: dict[str, str] = field(default_factory=dict)
    _sockets: list[web.WebSocketResponse] = field(default_factory=list)
    _counter: itertools.count[int] = field(default_factory=itertools.count)
    server: TestServer | None = None

    def __post_init__(self) -> None:
        """One simulated mower with a map of two areas."""
        self.mowers.append(
            {
                "id": MOWER_ID,
                "name": "Luba",
                "model": "simulator",
                "serial": "SIM-0001",
                "connection_kind": "simulated",
                "connection_options": {"time_scale": 30},
                "linked_account_id": None,
                "iot_id": None,
                "site_id": str(uuid.uuid4()),
                "group_ids": [],
                "created_by": str(uuid.uuid4()),
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
        )
        self.states[MOWER_ID] = mower_state()
        self.capabilities[MOWER_ID] = {
            "model": "Luba 2 AWD (simulated)",
            "blade_height_range_mm": [25, 70],
            "max_speed_mps": 1.2,
            "native_patterns": ["stripes"],
            "light": True,
            "connection_kinds": ["simulated"],
            "rtk_sources": ["base_station"],
        }
        square = [[0, 0], [10, 0], [10, 10]]
        self.maps[MOWER_ID] = {
            "reference": {"origin_lat": 48.137, "origin_lon": 11.575},
            "areas": [
                {"id": "zone-front", "name": "Front", "boundary": square},
                {"id": "zone-back", "name": "Back", "boundary": square},
            ],
            "revision": "rev-1",
        }

    @property
    def base_url(self) -> str:
        """URL of the running server (no trailing slash)."""
        assert self.server is not None
        return str(self.server.make_url("")).rstrip("/")

    def revoke(self, api_token: str = API_TOKEN) -> None:
        """Revoke an API token and every access token issued for it."""
        self.valid_api_tokens.pop(api_token, None)
        self._access_tokens = {k: v for k, v in self._access_tokens.items() if v != api_token}

    def expire_access_tokens(self) -> None:
        """Forget every access token (as if they expired)."""
        self._access_tokens.clear()

    # Server

    def app(self) -> web.Application:
        """The aiohttp application."""
        app = web.Application(middlewares=[self._middleware])
        app.router.add_post("/api/v1/identity/token", self._token)
        app.router.add_get("/api/v1/identity/me", self._me)
        app.router.add_get("/api/v1/fleet/mowers", self._mowers)
        app.router.add_post("/api/v1/fleet/mowers/{mower_id}/commands", self._command)
        app.router.add_get("/api/v1/fleet/commands/{command_id}", self._get_command)
        app.router.add_get("/api/v1/telemetry/accounts/me/mowers/states", self._states)
        app.router.add_get("/api/v1/telemetry/mowers/{mower_id}/state", self._state)
        app.router.add_get("/api/v1/telemetry/mowers/{mower_id}/capabilities", self._caps)
        app.router.add_get("/api/v1/telemetry/mowers/{mower_id}/map", self._map)
        app.router.add_get("/api/v1/realtime/ws", self._ws)
        return app

    @web.middleware
    async def _middleware(self, request: web.Request, handler: Any) -> web.StreamResponse:
        self.requests.append(f"{request.method} {request.path}")
        if self.fail_requests > 0 and request.path != "/api/v1/realtime/ws":
            self.fail_requests -= 1
            return problem(503, "Service unavailable", "try again")
        public = request.path in ("/api/v1/identity/token", "/api/v1/realtime/ws")
        if not public:
            auth = request.headers.get("Authorization", "")
            token = auth.removeprefix("Bearer ")
            if token not in self._access_tokens:
                return problem(401, "Unauthorized", "invalid or expired access token")
            request["api_token"] = self._access_tokens[token]
        response: web.StreamResponse = await handler(request)
        return response

    def _account(self, request: web.Request) -> str:
        return self.valid_api_tokens[request["api_token"]]

    async def _token(self, request: web.Request) -> web.Response:
        body = await request.json()
        api_token = body.get("token")
        self.exchanges += 1
        if api_token not in self.valid_api_tokens:
            return problem(401, "Unauthorized", "invalid api token")
        access = f"at-{next(self._counter)}"
        self._access_tokens[access] = api_token
        expires_at = datetime.now(UTC) + timedelta(seconds=self.expires_in)
        return web.json_response(
            {
                "access_token": access,
                "token_type": "Bearer",
                "expires_in": self.expires_in,
                "expires_at": expires_at.isoformat(),
                "account_id": self.valid_api_tokens[api_token],
                "refresh_token": None,
                "session_id": None,
            }
        )

    async def _me(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "subject": str(uuid.uuid4()),
                "kind": "api_token",
                "account_id": self._account(request),
                "display_name": "Home Assistant",
                "username": None,
                "roles": [],
                "permissions": ["mower.view", "mower.control"],
                "plan": None,
                "session_id": None,
                "memberships": [],
            }
        )

    def _visible(self, request: web.Request) -> list[dict[str, Any]]:
        return self.mowers if self._account(request) == ACCOUNT_ID else []

    async def _mowers(self, request: web.Request) -> web.Response:
        return web.json_response({"items": self._visible(request)})

    async def _command(self, request: web.Request) -> web.Response:
        mower_id = request.match_info["mower_id"]
        if mower_id not in {m["id"] for m in self._visible(request)}:
            return problem(404, "Not found", "mower not found")
        if self.forbid_commands:
            return problem(403, "Forbidden", "missing permission mower.control")
        body = await request.json()
        self.commands.append({"mower_id": mower_id, **body})
        command_id = str(uuid.uuid4())
        kind = body["command"]["kind"]
        if kind == "start_mow":
            await self.push_state(mower_id, mower_state(activity="mowing", charging=False))
        return web.json_response(
            {
                "command_id": command_id,
                "mower_id": mower_id,
                "kind": kind,
                "priority": "emergency" if kind == "emergency_stop" else "normal",
                "issued_at": now_iso(),
                "expires_at": now_iso(),
                "correlation_id": "test",
                "status": "accepted",
                "status_url": f"/api/v1/fleet/commands/{command_id}",
            },
            status=202,
        )

    async def _get_command(self, request: web.Request) -> web.Response:
        return web.json_response(
            {"command_id": request.match_info["command_id"], "status": "executed"}
        )

    async def _states(self, request: web.Request) -> web.Response:
        items = [
            {
                "mower_id": m["id"],
                "state": self.states[m["id"]],
                "received_at": now_iso(),
                "stale": False,
            }
            for m in self._visible(request)
            if m["id"] in self.states
        ]
        return web.json_response({"items": items, "truncated": False})

    async def _state(self, request: web.Request) -> web.Response:
        mower_id = request.match_info["mower_id"]
        if mower_id not in self.states:
            return problem(404, "Not found", "no state")
        return web.json_response(
            {
                "mower_id": mower_id,
                "state": self.states[mower_id],
                "received_at": now_iso(),
                "stale": False,
            }
        )

    async def _caps(self, request: web.Request) -> web.Response:
        mower_id = request.match_info["mower_id"]
        if mower_id not in self.capabilities:
            return problem(404, "Not found", "no capabilities")
        return web.json_response(
            {
                "mower_id": mower_id,
                "capabilities": self.capabilities[mower_id],
                "observed_at": now_iso(),
                "received_at": now_iso(),
            }
        )

    async def _map(self, request: web.Request) -> web.Response:
        mower_id = request.match_info["mower_id"]
        if mower_id not in self.maps:
            return problem(404, "Not found", "no map")
        revision = self.maps[mower_id]["revision"]
        if request.headers.get("If-None-Match") == f'"{revision}"':
            return web.Response(status=304)
        return web.json_response(
            {
                "mower_id": mower_id,
                "revision": revision,
                "map": self.maps[mower_id],
                "observed_at": now_iso(),
                "received_at": now_iso(),
            },
            headers={"ETag": f'"{revision}"'},
        )

    # Realtime

    async def _ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.ws_connections += 1
        first = await ws.receive_json()
        token = first.get("token") if first.get("type") == "auth" else None
        if self.ws_reject_auth or token not in self._access_tokens:
            await ws.close(code=4401, message=b"unauthorized")
            return ws
        self.ws_auth_tokens.append(token)
        await ws.send_json({"type": "ready", "token_expires_at": now_iso()})
        self._sockets.append(ws)
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    break
                data = msg.json()
                if data["type"] == "auth":
                    self.ws_auth_tokens.append(data["token"])
                    await ws.send_json({"type": "ready"})
                elif data["type"] == "subscribe":
                    self.ws_subscriptions.append(list(data["mower_ids"]))
                    await ws.send_json(
                        {"type": "subscribed", "mower_ids": data["mower_ids"], "denied": []}
                    )
        finally:
            if ws in self._sockets:
                self._sockets.remove(ws)
        return ws

    async def push(self, message: dict[str, Any]) -> None:
        """Send a server message to every authenticated socket."""
        for ws in list(self._sockets):
            await ws.send_json(message)

    async def push_state(self, mower_id: str, state: dict[str, Any]) -> None:
        """Store a state and push it like Realtime does."""
        self.states[mower_id] = state
        await self.push({"type": "state", "mower_id": mower_id, "state": state})

    async def close_sockets(self, code: int = WSCloseCode.GOING_AWAY) -> None:
        """Close every socket with a close code."""
        for ws in list(self._sockets):
            await ws.close(code=code)

    @property
    def open_sockets(self) -> int:
        """Authenticated sockets currently open."""
        return len(self._sockets)
