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
ANSWER_SDP = "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n"


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
    """A MowerState as Telemetry and Realtime deliver it: every field of the contract
    (``MOWER_STATE_FIELDS``), a docked mower with RTK fix on Wi-Fi and no job."""
    state: dict[str, Any] = {
        "mower_id": MOWER_ID,
        "account_id": ACCOUNT_ID,
        "observed_at": now_iso(),
        "heartbeat_at": now_iso(),
        "connected": True,
        "activity": "docked",
        "battery_percent": 87,
        "charging": True,
        "connection": "simulated",
        "rtk_fix": "fixed",
        "rtk_source": "base_station",
        "blade_height_mm": 50,
        "speed_mps": 0.0,
        "drive_speed_mps": None,
        "position": {"lat": 48.137, "lon": 11.575, "heading": 90.0},
        "position_source": "rtk",
        "firmware": "sim-1.0",
        "firmware_info": None,
        "light_on": False,
        "error_code": None,
        "error_message": None,
        "error_category": None,
        "drive_recoverable": False,
        "job": None,
        "gnss": {"satellites": 28, "correction_age_s": 1.0, "rtk_status": "fixed"},
        "signal": {"wifi_dbm": -58, "mobile_dbm": None, "network": "wifi"},
        "self_check": {"result": "ok", "detail": None},
        "links": {},
        "spot_run": None,
        "turn_style": None,
        "drive_calibration": None,
        "raw": {},
    }
    state.update(overrides)
    return state


# The properties of MowerState in apps/web/openapi/telemetry.json of lukislp/mowdeck (v0.7.0).
MOWER_STATE_FIELDS = frozenset(
    {
        "account_id",
        "activity",
        "battery_percent",
        "blade_height_mm",
        "charging",
        "connected",
        "connection",
        "drive_calibration",
        "drive_recoverable",
        "drive_speed_mps",
        "error_category",
        "error_code",
        "error_message",
        "firmware",
        "firmware_info",
        "gnss",
        "heartbeat_at",
        "job",
        "light_on",
        "links",
        "mower_id",
        "observed_at",
        "position",
        "position_source",
        "raw",
        "rtk_fix",
        "rtk_source",
        "self_check",
        "signal",
        "speed_mps",
        "spot_run",
        "turn_style",
    }
)


def notification(**overrides: Any) -> dict[str, Any]:
    """The ``notification`` of a Realtime ``notification`` message to an API token: an
    account notification of the test mower (English, no inbox item behind it)."""
    item: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "scope": "account",
        "kind": "mower_stuck",
        "severity": "critical",
        "title": "Luba is stuck",
        "body": "The robot is stuck near the hedge.",
        "url": f"/mowers/{MOWER_ID}",
        "mower_id": MOWER_ID,
        "read": False,
        "read_at": None,
        "acked": False,
        "acked_at": None,
        "occurred_at": now_iso(),
        "created_at": now_iso(),
    }
    item.update(overrides)
    return item


SCHEDULE_ID = "5e1d7c3a-2b4f-4e8a-9d6c-7f0a1b2c3d4e"
PROPOSAL_ID = "7c1e2d3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f"
# The smallest valid JPEG and PNG headers are enough for Home Assistant's image entity.
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16 + b"\xff\xd9"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def proposal(**overrides: Any) -> dict[str, Any]:
    """A full ProposalOut of the test mower: proposed, drive two steps, with a camera frame
    and the map rendering."""
    item: dict[str, Any] = {
        "id": PROPOSAL_ID,
        "mower_id": MOWER_ID,
        "chain_id": str(uuid.uuid4()),
        "task": "recover",
        "round": 1,
        "rounds_left": 2,
        "can_retry": False,
        "state": "proposed",
        "final": False,
        "action": "drive",
        "fallback": False,
        "trigger": "manual",
        "trigger_error_code": None,
        "assessment": {
            "action": "drive",
            "confidence": 0.8,
            "situation": "stuck_on_obstacle",
            "rationale": "A root blocks the left wheel; reverse, then turn right.",
            "risk": [],
            "abort_reasons": [],
        },
        "steps": [
            {"index": 0, "linear": -0.5, "angular": 0.0, "seconds": 1.5, "outcome": "pending"},
            {"index": 1, "linear": 0.0, "angular": -0.6, "seconds": 0.75, "outcome": "pending"},
        ],
        "frames": [
            {"index": 0, "source": "mower", "camera": "0", "side": "left", "captured_at": None},
            {"index": 1, "source": "map", "camera": None, "side": None, "captured_at": None},
        ],
        "image_count": 2,
        "stereo": False,
        "light_used": False,
        "hosted": True,
        "provider_family": "custom",
        "provider_model": "model",
        "job": None,
        "detail": None,
        "requested_by": {"kind": "api_token", "subject": str(uuid.uuid4()), "display_name": "HA"},
        "approved_by": None,
        "approved_at": None,
        "decided_by": None,
        "decided_at": None,
        "drive_session_id": None,
        "return_command_id": None,
        "created_at": now_iso(),
        "analysed_at": now_iso(),
        "started_at": None,
        "finished_at": None,
        "expires_at": in_hours(0.1),
        "updated_at": now_iso(),
        "link": f"/mowers/{MOWER_ID}?assist={PROPOSAL_ID}",
    }
    item.update(overrides)
    return item


def proposal_summary(full: dict[str, Any]) -> dict[str, Any]:
    """The proposal of a Realtime ``vision_proposal`` message (no frames, steps counted)."""
    keys: tuple[str, ...] = ("id", "mower_id", "task", "round", "state", "final", "action")
    keys += ("fallback", "trigger", "expires_at", "link", "updated_at")
    summary = {k: full.get(k) for k in keys}
    assessment = full.get("assessment") or {}
    summary["steps"] = len(full.get("steps") or [])
    summary["confidence"] = assessment.get("confidence")
    summary["situation"] = assessment.get("situation")
    summary["error_code"] = full.get("trigger_error_code")
    return summary


def in_hours(hours: float) -> str:
    """A UTC timestamp ``hours`` from now in the API's format."""
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def schedule(**overrides: Any) -> dict[str, Any]:
    """A ScheduleOut for the test mower: weekly, enabled, next run in 20 hours."""
    item: dict[str, Any] = {
        "id": SCHEDULE_ID,
        "name": "Morning",
        "kind": "weekly",
        "days_of_week": ["mon", "wed", "fri"],
        "interval_days": None,
        "anchor_date": None,
        "start_time": "09:00:00",
        "duration_minutes": 120,
        "timezone": "Europe/Berlin",
        "timezone_override": None,
        "target_type": "mower",
        "target_id": MOWER_ID,
        "area_ids": [],
        "pattern": None,
        "rotation_angles": [],
        "rotation_index": 0,
        "enabled": True,
        "disabled_reason": None,
        "pause_running_jobs_on_rain": True,
        "return_to_dock_at_window_end": False,
        "rain_pause": {
            "enabled": True,
            "probability_threshold": 70,
            "precipitation_mm_threshold": 0.5,
            "lookahead_hours": 3,
            "cooldown_hours": 2,
            "run_when_unknown": True,
        },
        "permission_skips": 0,
        "skip_until": None,
        "next_run_at": in_hours(20),
        "last_run_at": in_hours(-20),
        "owner": {"kind": "user", "subject": str(uuid.uuid4()), "display_name": "Anna"},
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    item.update(overrides)
    return item


def schedule_run(
    *, outcome: str, hours_ago: float, trigger: str = "schedule", mower_id: str | None = MOWER_ID
) -> dict[str, Any]:
    """A RunOut of the test schedule."""
    return {
        "id": str(uuid.uuid4()),
        "schedule_id": SCHEDULE_ID,
        "mower_id": mower_id,
        "trigger": trigger,
        "outcome": outcome,
        "scheduled_for": in_hours(-hours_ago),
        "started_at": in_hours(-hours_ago),
        "window_end_at": in_hours(2 - hours_ago),
        "command_id": None,
        "details": "rain expected" if outcome == "skipped_rain" else None,
        "followup": None,
        "followup_at": None,
    }


def rain_decision(**overrides: Any) -> dict[str, Any]:
    """A RainDecisionOut that holds: rain in the forecast, the lawn wet for three hours."""
    decision: dict[str, Any] = {
        "verdict": "hold",
        "holds": True,
        "reason": "rain expected within 3 h",
        "provider": "open-meteo",
        "attribution": "Weather data by Open-Meteo.com",
        "latitude": 48.137,
        "longitude": 11.575,
        "location_source": "site",
        "hours": [],
        "recent_mm_6h": 1.2,
        "recent_mm_24h": 4.5,
        "settings": {},
        "pause_until": in_hours(2),
        "wet": True,
        "wet_until": in_hours(3),
    }
    decision.update(overrides)
    return decision


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
    health: dict[str, dict[str, Any]] = field(default_factory=dict)
    commands: list[dict[str, Any]] = field(default_factory=list)
    forbid_commands: bool = False
    # Relations POST /identity/me/permissions grants on every visible mower (None: the
    # endpoint answers 404 like a MowDeck without it).
    relations: set[str] | None = field(
        default_factory=lambda: {
            "can_view",
            "can_control",
            "can_drive",
            "can_manage_mowers",
            "can_edit_schedule",
        }
    )
    permission_checks: int = 0
    # Paths (prefix match) answered with 403, as for a token without the permission.
    forbidden_paths: set[str] = field(default_factory=set)
    # Paths (prefix match) answered with another problem status, or dropped mid-request.
    problem_paths: dict[str, int] = field(default_factory=dict)
    disconnect_paths: set[str] = field(default_factory=set)
    me_overrides: dict[str, Any] = field(default_factory=dict)
    token_overrides: dict[str, Any] = field(default_factory=dict)
    permission_answer: Any = None
    ws_error_before_ready: bool = False
    # Scheduler (None: this MowDeck runs no Scheduler, every path answers a plain 404).
    schedules: dict[str, dict[str, Any]] | None = field(default_factory=dict)
    schedule_runs: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    schedule_patches: list[dict[str, Any]] = field(default_factory=list)
    weather: dict[str, dict[str, Any]] = field(default_factory=dict)
    weather_queries: list[dict[str, str]] = field(default_factory=list)
    daily: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    heatmap_queries: list[dict[str, str]] = field(default_factory=list)
    # Vision (False: this MowDeck runs no Vision service, every path answers a plain 404).
    vision_enabled: bool = True
    proposals: dict[str, dict[str, Any]] = field(default_factory=dict)
    proposal_images: dict[tuple[str, int], tuple[bytes, str]] = field(default_factory=dict)
    vision_starts: list[dict[str, Any]] = field(default_factory=list)
    vision_start_problem: tuple[int, str] | None = None
    # Planner: the bodies of POST /planner/plans and whether the transit finds a route.
    plan_requests: list[dict[str, Any]] = field(default_factory=list)
    plan_route_found: bool = True
    firmware_check_status: int | None = None
    blade_resets: list[dict[str, Any]] = field(default_factory=list)
    # MowDeck 0.8: the per-mower blade threshold and the camera still image (None: a MowDeck
    # before 0.8, the routes answer a plain 404).
    blade_thresholds: list[dict[str, Any]] | None = None
    snapshot: tuple[bytes, str] | None = None
    snapshot_status: int | None = None
    snapshot_requests: int = 0
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
    # Camera relay (Fleet's camera session API and MediaMTX's WHEP behind /media/whep).
    cameras: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"id": "0", "name": "Front left", "uid": 1, "placement": "front_left"},
            {"id": "1", "name": "Front right", "uid": 2, "placement": "front_right"},
            {"id": "2", "name": "Rear", "uid": 3, "placement": "rear"},
        ]
    )
    camera_available: dict[str, bool | None] = field(
        default_factory=lambda: {"0": True, "1": True, "2": False}
    )
    heartbeat_seconds: float = 15
    camera_problem: tuple[int, str] | None = None
    leases: dict[str, dict[str, Any]] = field(default_factory=dict)
    lease_history: list[str] = field(default_factory=list)
    renewals: int = 0
    whep_starting: int = 0
    whep_status: int | None = None
    whep_offers: list[dict[str, Any]] = field(default_factory=list)
    whep_sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    whep_patches: list[dict[str, Any]] = field(default_factory=list)
    whep_deletes: list[str] = field(default_factory=list)
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
        self.health[MOWER_ID] = {
            "mower_id": MOWER_ID,
            "since": now_iso(),
            "blade_hours": 41.2,
            "blade_hours_since_change": 12.345678,
            "blade_changed_at": None,
            "blade_hours_threshold": 50.0,
            "device_blade_hours": None,
            "device_blade_warn_hours": None,
            "blade_source": "telemetry",
            "mowing_hours": 40.00000000001,
            "distance_m": 12345.6,
            "battery_cycles": 17,
            "charge_sessions": 20,
            "errors": {},
            "updated_at": now_iso(),
        }
        self.schedules = {SCHEDULE_ID: schedule()}
        self.schedule_runs[SCHEDULE_ID] = [
            schedule_run(outcome="skipped_rain", hours_ago=20),
            schedule_run(outcome="started", hours_ago=44),
        ]
        self.weather[MOWER_ID] = rain_decision()
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
        app.router.add_post("/api/v1/identity/me/permissions", self._my_permissions)
        app.router.add_post("/api/v1/fleet/mowers/{mower_id}/firmware/check", self._firmware)
        app.router.add_post(
            "/api/v1/telemetry/mowers/{mower_id}/health/blade-reset", self._blade_reset
        )
        app.router.add_get("/api/v1/fleet/mowers", self._mowers)
        app.router.add_get("/api/v1/fleet/mowers/{mower_id}/camera/snapshot", self._snapshot)
        app.router.add_put(
            "/api/v1/telemetry/mowers/{mower_id}/health/blade-threshold", self._blade_threshold
        )
        app.router.add_post("/api/v1/fleet/mowers/{mower_id}/commands", self._command)
        app.router.add_get("/api/v1/fleet/commands/{command_id}", self._get_command)
        app.router.add_get("/api/v1/telemetry/accounts/me/mowers/states", self._states)
        app.router.add_get("/api/v1/telemetry/mowers/{mower_id}/state", self._state)
        app.router.add_get("/api/v1/telemetry/mowers/{mower_id}/capabilities", self._caps)
        app.router.add_get("/api/v1/telemetry/mowers/{mower_id}/map", self._map)
        app.router.add_get("/api/v1/telemetry/mowers/{mower_id}/health", self._health)
        app.router.add_get("/api/v1/realtime/ws", self._ws)
        sched = "/api/v1/scheduler/schedules"
        app.router.add_get(sched, self._schedules)
        app.router.add_patch(sched + "/{schedule_id}", self._patch_schedule)
        app.router.add_post(sched + "/{schedule_id}/run-now", self._run_now)
        app.router.add_post(sched + "/{schedule_id}/skip-next", self._skip_next)
        app.router.add_get(sched + "/{schedule_id}/runs", self._runs)
        app.router.add_get(sched + "/{schedule_id}/preview", self._preview)
        app.router.add_get("/api/v1/scheduler/weather/{mower_id}", self._weather)
        app.router.add_put("/api/v1/fleet/mowers/{mower_id}/mow-defaults", self._mow_defaults)
        app.router.add_get("/api/v1/telemetry/mowers/{mower_id}/health/daily", self._daily)
        app.router.add_get("/api/v1/telemetry/mowers/{mower_id}/heatmap", self._heatmap)
        vision = "/api/v1/vision"
        app.router.add_post(vision + "/mowers/{mower_id}/recover", self._vision_recover)
        app.router.add_get(vision + "/mowers/{mower_id}/proposals", self._vision_list)
        app.router.add_get(vision + "/proposals/{proposal_id}", self._vision_get)
        app.router.add_get(vision + "/proposals/{proposal_id}/images/{index}", self._vision_image)
        app.router.add_post("/api/v1/planner/plans", self._plan)
        camera = "/api/v1/fleet/mowers/{mower_id}/camera/session"
        app.router.add_post(camera, self._camera_open)
        app.router.add_post(camera + "/{lease_id}/renew", self._camera_renew)
        app.router.add_delete(camera + "/{lease_id}", self._camera_close)
        whep = "/media/whep/r/{relay_id}/{camera}/whep"
        app.router.add_post(whep, self._whep_offer)
        app.router.add_patch(whep + "/{secret}", self._whep_patch)
        app.router.add_delete(whep + "/{secret}", self._whep_delete)
        return app

    @web.middleware
    async def _middleware(self, request: web.Request, handler: Any) -> web.StreamResponse:
        self.requests.append(f"{request.method} {request.path}")
        if self.fail_requests > 0 and request.path != "/api/v1/realtime/ws":
            self.fail_requests -= 1
            return problem(503, "Service unavailable", "try again")
        public = request.path in (
            "/api/v1/identity/token",
            "/api/v1/realtime/ws",
        ) or request.path.startswith("/media/whep/")
        if not public:
            auth = request.headers.get("Authorization", "")
            token = auth.removeprefix("Bearer ")
            if token not in self._access_tokens:
                return problem(401, "Unauthorized", "invalid or expired access token")
            request["api_token"] = self._access_tokens[token]
            if any(request.path.startswith(path) for path in self.forbidden_paths):
                return problem(403, "Forbidden", "missing permission")
            for path, status in self.problem_paths.items():
                if request.path.startswith(path):
                    return problem(status, "Problem", f"answered {status}")
            if any(request.path.startswith(path) for path in self.disconnect_paths):
                assert request.transport is not None
                request.transport.close()
                return web.Response(status=500)
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
        answer = {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": self.expires_in,
            "expires_at": expires_at.isoformat(),
            "account_id": self.valid_api_tokens[api_token],
            "refresh_token": None,
            "session_id": None,
        }
        return web.json_response({**answer, **self.token_overrides})

    async def _me(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "subject": str(uuid.uuid4()),
                "kind": "api_token",
                "account_id": self._account(request),
                "display_name": "Home Assistant",
                "username": None,
                "roles": [],
                # An account-wide mower.view implies notifications.view (MowDeck 0.7).
                "permissions": ["mower.control", "mower.view", "notifications.view"],
                "plan": None,
                "session_id": None,
                "memberships": [],
                **self.me_overrides,
            }
        )

    async def _my_permissions(self, request: web.Request) -> web.Response:
        if self.relations is None:
            return web.Response(status=404, text="404: Not Found")
        self.permission_checks += 1
        body = await request.json()
        if self.permission_answer is not None:
            return web.json_response(self.permission_answer)
        visible = {f"mower:{m['id']}" for m in self._visible(request)}
        visible |= {f"group:{g}" for m in self._visible(request) for g in m["group_ids"]}
        return web.json_response(
            [
                check["object"] in visible and check["relation"] in self.relations
                for check in body["checks"]
            ]
        )

    async def _firmware(self, request: web.Request) -> web.Response:
        mower_id = request.match_info["mower_id"]
        if mower_id not in {m["id"] for m in self._visible(request)}:
            return problem(404, "Not found", "mower not found")
        if self.firmware_check_status == 409:
            return problem(409, "Conflict", "firmware-check-unsupported")
        if self.firmware_check_status is not None:
            return problem(self.firmware_check_status, "Unavailable", "the mower is offline")
        return web.json_response(
            {
                "installed": "1.10.4",
                "available": "1.12.0",
                "update_state": "available",
                "progress": None,
                "release_notes": "Checked just now.",
                "checked_at": now_iso(),
                "failure_reason": None,
            }
        )

    async def _blade_reset(self, request: web.Request) -> web.Response:
        mower_id = request.match_info["mower_id"]
        if mower_id not in {m["id"] for m in self._visible(request)}:
            return problem(404, "Not found", "mower not found")
        if self.relations is not None and "can_manage_mowers" not in self.relations:
            return problem(403, "Forbidden", "missing can_manage_mowers on the mower")
        self.blade_resets.append(await request.json())
        health = self.health.setdefault(mower_id, {"mower_id": mower_id})
        health.update(
            {
                "blade_hours_since_change": 0.0,
                "blade_changed_at": now_iso(),
                "blade_source": "telemetry",
            }
        )
        return web.json_response(health)

    async def _snapshot(self, request: web.Request) -> web.Response:
        self.snapshot_requests += 1
        if self.snapshot is None:
            return web.Response(status=404, text="404: Not Found")
        if self.snapshot_status is not None:
            return problem(self.snapshot_status, "Problem", "camera-unsupported")
        content, content_type = self.snapshot
        return web.Response(body=content, content_type=content_type)

    async def _blade_threshold(self, request: web.Request) -> web.Response:
        if self.blade_thresholds is None:
            return web.Response(status=404, text="404: Not Found")
        mower_id = request.match_info["mower_id"]
        if self.relations is not None and "can_manage_mowers" not in self.relations:
            return problem(403, "Forbidden", "missing can_manage_mowers on the mower")
        body = await request.json()
        self.blade_thresholds.append(body)
        health = self.health.setdefault(mower_id, {"mower_id": mower_id})
        hours = body["hours"]
        health["blade_hours_threshold"] = 50.0 if hours is None else hours
        health["blade_hours_threshold_custom"] = hours is not None
        return web.json_response(health)

    # Scheduler

    def _schedule(self, request: web.Request) -> dict[str, Any] | web.Response:
        if self.schedules is None:
            return web.Response(status=404, text="404 page not found")
        found = self.schedules.get(request.match_info["schedule_id"])
        if found is None:
            return problem(404, "Not found", "schedule not found")
        return found

    async def _schedules(self, request: web.Request) -> web.Response:
        if self.schedules is None:
            return web.Response(status=404, text="404 page not found")
        return web.json_response({"items": list(self.schedules.values())})

    async def _patch_schedule(self, request: web.Request) -> web.Response:
        found = self._schedule(request)
        if isinstance(found, web.Response):
            return found
        if self.relations is not None and "can_edit_schedule" not in self.relations:
            return problem(403, "Forbidden", "missing can_edit_schedule on the mower")
        changes = await request.json()
        self.schedule_patches.append({"id": found["id"], **changes})
        found.update(changes)
        if changes.get("enabled") is False:
            found["next_run_at"] = None
        elif changes.get("enabled") is True:
            found["next_run_at"] = in_hours(20)
        return web.json_response(found)

    async def _run_now(self, request: web.Request) -> web.Response:
        found = self._schedule(request)
        if isinstance(found, web.Response):
            return found
        assert request.query.get("respect_rain") == "false"
        run = schedule_run(outcome="started", hours_ago=0, trigger="manual")
        self.schedule_runs.setdefault(found["id"], []).insert(0, run)
        return web.json_response({"items": [run]}, status=202)

    async def _skip_next(self, request: web.Request) -> web.Response:
        found = self._schedule(request)
        if isinstance(found, web.Response):
            return found
        if not found["enabled"] or found["next_run_at"] is None:
            return problem(409, "Conflict", "the schedule has no next run")
        found["skip_until"] = found["next_run_at"]
        return web.json_response(found)

    async def _runs(self, request: web.Request) -> web.Response:
        found = self._schedule(request)
        if isinstance(found, web.Response):
            return found
        limit = int(request.query.get("limit", "50"))
        return web.json_response({"items": self.schedule_runs.get(found["id"], [])[:limit]})

    async def _preview(self, request: web.Request) -> web.Response:
        found = self._schedule(request)
        if isinstance(found, web.Response):
            return found
        first = datetime.fromisoformat(found["next_run_at"].replace("Z", "+00:00"))
        items = [
            {
                "at": (first + timedelta(days=i)).isoformat(),
                "window_end": (first + timedelta(days=i, hours=2)).isoformat(),
                "skipped": i == 0 and found.get("skip_until") is not None,
            }
            for i in range(int(request.query.get("count", "5")))
        ]
        return web.json_response({"items": items, "weather": None, "weather_mower_id": None})

    async def _weather(self, request: web.Request) -> web.Response:
        if self.schedules is None:
            return web.Response(status=404, text="404 page not found")
        mower_id = request.match_info["mower_id"]
        if mower_id not in self.weather:
            return problem(404, "Not found", "mower not found")
        self.weather_queries.append(dict(request.query))
        return web.json_response(
            {"mower_id": mower_id, "at": now_iso(), "decision": self.weather[mower_id]}
        )

    async def _mow_defaults(self, request: web.Request) -> web.Response:
        mower_id = request.match_info["mower_id"]
        mower = next((m for m in self._visible(request) if m["id"] == mower_id), None)
        if mower is None:
            return problem(404, "Not found", "mower not found")
        if self.relations is not None and "can_control" not in self.relations:
            return problem(403, "Forbidden", "missing can_control on the mower")
        body = await request.json()
        pattern = body["pattern"]
        if pattern is not None:
            allowed = {"name", "angle_deg", "border_laps", "spacing_m"}
            if set(pattern) - allowed or not 0 <= pattern.get("angle_deg", 0) <= 180:
                return problem(422, "Unprocessable", "invalid pattern")
        mower["mow_defaults"] = {"pattern": pattern} if pattern is not None else None
        return web.json_response(mower)

    async def _daily(self, request: web.Request) -> web.Response:
        mower_id = request.match_info["mower_id"]
        if mower_id not in self.daily:
            return problem(404, "Not found", "no telemetry yet")
        day = request.query["from"]
        assert request.query["to"] == day
        days = [d for d in self.daily[mower_id] if d["day"] == day]
        return web.json_response({"mower_id": mower_id, "from": day, "to": day, "days": days})

    async def _heatmap(self, request: web.Request) -> web.Response:
        mower_id = request.match_info["mower_id"]
        if mower_id not in self.maps:
            return problem(404, "Not found", "no-map-yet")
        self.heatmap_queries.append(dict(request.query))
        return web.json_response(
            {
                "mower_id": mower_id,
                "layer": request.query["layer"],
                "cell_m": float(request.query.get("cell", "1")),
                "cells": [{"x": 1, "y": 2, "n": 5, "value": 0.8}],
                "gaps": [],
                "legend": {"kind": "ratio", "min": 0, "max": 1},
                "reference": {"origin_lat": 48.137, "origin_lon": 11.575, "rotation_deg": 0},
                "samples": 5,
                "truncated": False,
                "from": now_iso(),
                "to": now_iso(),
            }
        )

    # Vision

    async def _vision_recover(self, request: web.Request) -> web.Response:
        if not self.vision_enabled:
            return web.Response(status=404, text="404 page not found")
        mower_id = request.match_info["mower_id"]
        if mower_id not in {m["id"] for m in self._visible(request)}:
            return problem(404, "Not found", "mower not found")
        if self.relations is not None and "can_drive" not in self.relations:
            return problem(403, "Forbidden", "missing can_drive on the mower")
        if self.vision_start_problem is not None:
            status, slug = self.vision_start_problem
            return problem(status, "Problem", slug)
        self.vision_starts.append({"mower_id": mower_id, **await request.json()})
        started = proposal(
            id=str(uuid.uuid4()),
            state="analysing",
            action=None,
            assessment=None,
            steps=[],
            frames=[],
            analysed_at=None,
            expires_at=None,
        )
        self.proposals[started["id"]] = started
        return web.json_response(started, status=202)

    async def _vision_list(self, request: web.Request) -> web.Response:
        if not self.vision_enabled:
            return web.Response(status=404, text="404 page not found")
        mower_id = request.match_info["mower_id"]
        if mower_id not in {m["id"] for m in self._visible(request)}:
            return problem(404, "Not found", "mower not found")
        items = [p for p in self.proposals.values() if p["mower_id"] == mower_id]
        items.sort(key=lambda p: p["created_at"], reverse=True)
        limit = int(request.query.get("limit", "20"))
        return web.json_response({"items": items[:limit], "next_before": None})

    async def _vision_get(self, request: web.Request) -> web.Response:
        found = self.proposals.get(request.match_info["proposal_id"])
        if not self.vision_enabled or found is None:
            return problem(404, "Not found", "proposal not found")
        return web.json_response(found)

    async def _vision_image(self, request: web.Request) -> web.Response:
        key = (request.match_info["proposal_id"], int(request.match_info["index"]))
        if key not in self.proposal_images:
            return problem(404, "Not found", "image purged")
        content, content_type = self.proposal_images[key]
        return web.Response(body=content, content_type=content_type)

    async def _plan(self, request: web.Request) -> web.Response:
        body = await request.json()
        if body["mower_id"] not in {m["id"] for m in self._visible(request)}:
            return problem(404, "Not found", "mower not found")
        if self.relations is not None and "can_control" not in self.relations:
            return problem(403, "Forbidden", "missing can_control on the mower")
        self.plan_requests.append(body)
        spot_plan = {
            "pattern_chain": [step["pattern"] for step in body["chain"]],
            "segments": [{"path": "_p~iF~ps|U_ulLnnqC", "blades_on": True, "speed_mps": 0.2}],
            "plan_hash": "f" * 64,
            "covered_area_m2": 6.1,
            "estimated_duration_s": 95.0,
            "native": None,
        }
        transit = None
        if "transit" in body:
            transit = {
                "found": self.plan_route_found,
                "from_dock": body["transit"]["from_dock"],
                "length_m": 4.2 if self.plan_route_found else 0.0,
                "route": [[0, 0], [1, 1]] if self.plan_route_found else [],
                "reason": None if self.plan_route_found else "no_route: blocked by a no-go zone",
            }
        return web.json_response(
            {
                "spot_plan": spot_plan,
                "plan_hash": spot_plan["plan_hash"],
                "estimated_duration_s": 95.0,
                "covered_area_m2": 6.1,
                "area_m2": 7.0,
                "transit": transit,
                "turn_style": body.get("turn_style"),
                "warnings": [],
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

    async def _health(self, request: web.Request) -> web.Response:
        mower_id = request.match_info["mower_id"]
        if mower_id not in self.health:
            return problem(404, "Not found", "no telemetry yet")
        return web.json_response(self.health[mower_id])

    # Camera relay

    def _relay_session(self, lease_id: str) -> dict[str, Any]:
        lease = self.leases[lease_id]
        lease["ticket"] = f"ticket-{next(self._counter)}"
        relay_id = lease["relay_id"]
        return {
            "provider": "relay",
            "app_id": "",
            "channel": "",
            "token": "",
            "uid": 0,
            "expires_at": now_iso(),
            "cameras": self.cameras,
            "relay": {
                "tiles": [
                    {
                        "camera_id": c["id"],
                        "whep_url": f"/media/whep/r/{relay_id}/{c['id']}/whep",
                        "available": self.camera_available.get(c["id"]),
                    }
                    for c in self.cameras
                ],
                "ticket": lease["ticket"],
                "lease_id": lease_id,
                "heartbeat_seconds": self.heartbeat_seconds,
            },
        }

    def _own_lease(self, request: web.Request) -> dict[str, Any] | None:
        lease = self.leases.get(request.match_info["lease_id"])
        if lease is None or lease["api_token"] != request["api_token"]:
            return None
        if lease["mower_id"] != request.match_info["mower_id"]:
            return None
        return lease

    async def _camera_open(self, request: web.Request) -> web.Response:
        mower_id = request.match_info["mower_id"]
        if mower_id not in {m["id"] for m in self._visible(request)}:
            return problem(404, "Not found", "mower not found")
        body = await request.json()
        assert body == {"transport": "relay"}
        if self.camera_problem is not None:
            status, slug = self.camera_problem
            return problem(status, slug.replace("-", " ").capitalize(), slug)
        lease_id = str(uuid.uuid4())
        self.leases[lease_id] = {
            "api_token": request["api_token"],
            "mower_id": mower_id,
            "relay_id": uuid.uuid4().hex,
        }
        self.lease_history.append(lease_id)
        return web.json_response(self._relay_session(lease_id))

    async def _camera_renew(self, request: web.Request) -> web.Response:
        if self._own_lease(request) is None:
            return problem(404, "Not found", "camera session not found")
        self.renewals += 1
        return web.json_response(self._relay_session(request.match_info["lease_id"]))

    async def _camera_close(self, request: web.Request) -> web.Response:
        lease_id = request.match_info["lease_id"]
        if lease_id in self.leases and self._own_lease(request) is None:
            return problem(404, "Not found", "camera session not found")
        self.drop_lease(lease_id)
        return web.Response(status=204)

    def drop_lease(self, lease_id: str) -> None:
        """End a lease and its WHEP readers (as the relay does when it expires)."""
        self.leases.pop(lease_id, None)
        self.whep_sessions = {
            k: v for k, v in self.whep_sessions.items() if v["lease_id"] != lease_id
        }

    def _ticket_lease(self, request: web.Request) -> str | None:
        ticket = request.headers.get("Authorization", "").removeprefix("Bearer ")
        relay_id = request.match_info["relay_id"]
        for lease_id, lease in self.leases.items():
            if lease.get("ticket") == ticket and lease["relay_id"] == relay_id:
                return lease_id
        return None

    async def _whep_offer(self, request: web.Request) -> web.Response:
        offer = await request.text()
        self.whep_offers.append(
            {
                "path": request.path,
                "authorization": request.headers.get("Authorization"),
                "content_type": request.content_type,
                "offer": offer,
            }
        )
        lease_id = self._ticket_lease(request)
        if lease_id is None:
            return web.Response(status=401)
        if self.whep_status is not None:
            return web.Response(status=self.whep_status)
        if self.whep_starting > 0:
            self.whep_starting -= 1
            return web.Response(status=404, text="no stream is available on path")
        secret = str(uuid.uuid4())
        relay_id, camera = request.match_info["relay_id"], request.match_info["camera"]
        self.whep_sessions[secret] = {"lease_id": lease_id, "camera": camera}
        return web.Response(
            status=201,
            text=ANSWER_SDP,
            content_type="application/sdp",
            headers={"Location": f"/r/{relay_id}/{camera}/whep/{secret}"},
        )

    async def _whep_patch(self, request: web.Request) -> web.Response:
        secret = request.match_info["secret"]
        if secret not in self.whep_sessions:
            return web.Response(status=404)
        self.whep_patches.append(
            {
                "secret": secret,
                "content_type": request.content_type,
                "fragment": await request.text(),
                "authorization": request.headers.get("Authorization"),
            }
        )
        return web.Response(status=204)

    async def _whep_delete(self, request: web.Request) -> web.Response:
        secret = request.match_info["secret"]
        self.whep_deletes.append(secret)
        self.whep_sessions.pop(secret, None)
        return web.Response(status=200)

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
        if self.ws_error_before_ready:
            await ws.send_json({"type": "error", "code": "internal", "message": "warming up"})
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

    async def push_raw(self, data: str | bytes) -> None:
        """Send a raw text or binary frame to every authenticated socket."""
        for ws in list(self._sockets):
            if isinstance(data, bytes):
                await ws.send_bytes(data)
            else:
                await ws.send_str(data)

    async def push_notification(self, item: dict[str, Any]) -> None:
        """Push a notification like Realtime does for an API token."""
        await self.push({"type": "notification", "notification": item})

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
