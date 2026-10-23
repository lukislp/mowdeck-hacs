"""Spot mowing: the rules of MowDeck's spot sheet, for ``mowdeck.spot_mow``.

A spot run is planned by MowDeck's Planner (``POST /api/v1/planner/plans``) and then driven
by MowDeck itself with joystick frames (Fleet's ``spot_mow`` command, ADR 0039): the mower
runs no job of its own. This module ports what the web app's spot sheet decides before it
asks the Planner (``apps/web/src/lib/mower/spot-sheet.ts`` and ``libs/mowdeck-ui`` in
lukislp/mowdeck): the limits, the circle as a 32-gon in the map's local metres, the mapped area
that holds the whole spot, the Planner request and the start conditions. MowDeck enforces the
safety rules again (speed at most 0.3 m/s, blades only when confirmed and allowed while
driving, the gateway's RTK, link and dead-man checks).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, Final

from .geo import from_wgs84

# Limits of the web app's spot sheet (MowDeck itself takes circles up to 10 m).
RADIUS_MIN_M: Final = 0.5
RADIUS_MAX_M: Final = 3.0
RADIUS_DEFAULT_M: Final = 1.5
SPEED_MIN_MPS: Final = 0.1
SPEED_MAX_MPS: Final = 0.3  # MAX_SPOT_MOW_SPEED_MPS of the contracts
SPEED_DEFAULT_MPS: Final = 0.2
# Corners a circle is planned with (CIRCLE_CORNERS).
CIRCLE_CORNERS: Final = 32
# The path spacing is the cutting width minus this overlap; a mower without a cutting width
# counts as 400 mm wide (the Planner's own default).
OVERLAP_M: Final = 0.05
FALLBACK_CUTTING_WIDTH_M: Final = 0.4
# The Planner keeps this far from the area edge and the no-go zones.
SAFETY_MARGIN_M: Final = 0.4
# A transit start position is rounded to this grid.
TRANSIT_GRID_M: Final = 0.5
BATTERY_MIN_PERCENT: Final = 20
PATTERNS: Final = ["spiral", "stripes"]
TURN_STYLES: Final = ["in_place", "arc", "multi_point"]
DEFAULT_TURN_STYLE: Final = "arc"
# States of MowerState.spot_run; the last three are final.
RUN_STATES: Final = [
    "preparing",
    "undocking",
    "waiting_rtk",
    "transit",
    "spot_mowing",
    "paused_rtk",
    "returning",
    "stopping",
    "done",
    "aborted",
    "failed",
]
FINAL_RUN_STATES: Final = frozenset({"done", "aborted", "failed"})
# Activities of a running job (the mower is busy).
JOB_ACTIVITIES: Final = frozenset({"mowing", "paused", "returning"})

type Point = tuple[float, float]


def circle_polygon(center: Point, radius_m: float, corners: int = CIRCLE_CORNERS) -> list[Point]:
    """A regular polygon approximating the circle (counter-clockwise, first corner east,
    centimetre precision)."""
    return [
        (
            round(center[0] + radius_m * math.cos(2 * math.pi * i / corners), 2),
            round(center[1] + radius_m * math.sin(2 * math.pi * i / corners), 2),
        )
        for i in range(corners)
    ]


def point_in_polygon(x: float, y: float, polygon: Sequence[Sequence[float]]) -> bool:
    """Even-odd rule, as the web map does."""
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = float(polygon[i][0]), float(polygon[i][1])
        xj, yj = float(polygon[j][0]), float(polygon[j][1])
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def containing_area(polygon: list[Point], areas: list[dict[str, Any]]) -> str | None:
    """The first mapped area that holds every corner of the spot (the Planner clips to it)."""
    for area in areas:
        boundary = area.get("boundary") or []
        if len(boundary) >= 3 and all(point_in_polygon(x, y, boundary) for x, y in polygon):
            return str(area["id"])
    return None


def resolve_turn_style(
    requested: str | None, mower_default: str | None, drive_reverse: bool
) -> str:
    """The style the run turns with (``resolve_turn_style`` of the contracts): the requested
    one, else the mower's own, else ``arc``; ``multi_point`` needs a drive link that reverses."""
    style = requested or mower_default or DEFAULT_TURN_STYLE
    if style not in TURN_STYLES:
        style = DEFAULT_TURN_STYLE
    return "arc" if style == "multi_point" and not drive_reverse else style


def starts_from_dock(state: dict[str, Any], caps: dict[str, Any]) -> bool:
    """A docked mower whose connection can leave the dock by itself."""
    return bool(caps.get("undock")) and state.get("activity") in ("docked", "charging")


def blocking_condition(state: dict[str, Any], caps: dict[str, Any]) -> str | None:
    """Why a spot run cannot start now (the web sheet's red rows), or None: another run or a
    job is active, the mower is neither idle nor docked with ``undock``, the battery is at or
    below 20 %, or (away from the dock) there is no fixed RTK position."""
    run = state.get("spot_run")
    if isinstance(run, dict) and run.get("state") not in FINAL_RUN_STATES:
        return "spot_busy"
    from_dock = starts_from_dock(state, caps)
    if state.get("activity") in JOB_ACTIVITIES or (
        state.get("activity") != "idle" and not from_dock
    ):
        return "spot_busy"
    battery = state.get("battery_percent")
    if not isinstance(battery, int | float) or battery <= BATTERY_MIN_PERCENT:
        return "spot_battery"
    if not from_dock and (state.get("rtk_fix") != "fixed" or state.get("position_source") != "rtk"):
        return "spot_rtk"
    return None


def chain(pattern: str, angle_deg: float) -> list[dict[str, Any]]:
    """The Planner's pattern chain: an inward spiral, or stripes at an angle."""
    if pattern == "stripes":
        return [{"pattern": "stripes", "settings": {"angle_deg": angle_deg}}]
    return [{"pattern": "spiral", "settings": {"direction": "inward"}}]


def plan_request(
    *,
    mower_id: str,
    area_id: str,
    polygon: list[Point],
    pattern: str,
    angle_deg: float,
    speed_mps: float,
    caps: dict[str, Any],
    turn_style: str,
    transit: dict[str, Any] | None,
) -> dict[str, Any]:
    """The body of ``POST /planner/plans`` for a spot, as the web app builds it."""
    width = caps.get("cutting_width_mm")
    width_m = float(width) / 1000 if isinstance(width, int | float) and width > 0 else None
    cutting_width_m = width_m or FALLBACK_CUTTING_WIDTH_M
    body: dict[str, Any] = {
        "mower_id": mower_id,
        "area": {"area_id": area_id, "polygon": [[x, y] for x, y in polygon]},
        "chain": chain(pattern, angle_deg),
        "mower": {
            "cutting_width_m": round(cutting_width_m, 3),
            "overlap_m": OVERLAP_M,
            "max_speed_mps": speed_mps,
            "transfer_speed_mps": speed_mps,
            "safety_margin_m": SAFETY_MARGIN_M,
        },
        "wgs84": False,
        "turn_style": turn_style,
    }
    if transit is not None:
        body["transit"] = transit
    return body


def transit_from(
    state: dict[str, Any], caps: dict[str, Any], reference: dict[str, Any]
) -> dict[str, Any] | None:
    """Where the run's transit starts: the dock for a docked mower that can undock, else the
    mower's position rounded to the grid; None without either."""
    if starts_from_dock(state, caps):
        return {"from_dock": True}
    position = state.get("position")
    if not isinstance(position, dict) or position.get("lat") is None:
        return None
    x, y = from_wgs84(reference, float(position["lat"]), float(position["lon"]))
    return {
        "from_dock": False,
        "position": [
            round(x / TRANSIT_GRID_M) * TRANSIT_GRID_M,
            round(y / TRANSIT_GRID_M) * TRANSIT_GRID_M,
        ],
    }
