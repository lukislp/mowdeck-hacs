"""What ``mowdeck.start_mow`` accepts on one mower, from the mower's capabilities.

Every parameter of the action is checked against the capabilities of the mower it targets
(``native_patterns``, ``path_spacing_range_mm``, ``blade_height_range_mm``, the speed range and
``start_options``, ADR 0030 in lukislp/mowdeck); ``mowdeck.get_start_options`` answers the same
description, and the number entities take their ranges from here.

A parameter is described as ``{"options": [...]}`` or ``{"min": ..., "max": ..., "step": ...}``;
a parameter the mower does not support is left out.
"""

from __future__ import annotations

from typing import Any, Final

from .const import (
    API_ANGLE_MAX_DEG,
    API_BLADE_HEIGHT_MAX_MM,
    API_BLADE_HEIGHT_MIN_MM,
    API_BORDER_LAPS_MAX,
    API_SPACING_MAX_M,
    API_SPACING_MIN_M,
    API_SPEED_MAX_MPS,
    DEFAULT_MIN_SPEED_MPS,
    PATTERNS,
    SPEED_DECIMALS,
    SPEED_STEP_MPS,
)

# ObstacleDetection and PathOrder of MowDeck's MowPattern (apps/web/openapi/fleet.json).
OBSTACLE_DETECTION: Final = ["off", "slow_touch", "less_touch", "standard", "sensitive"]
PATH_ORDERS: Final = ["border_first", "grid_first"]
API_OBSTACLE_LAPS_MAX: Final = 4
API_START_PROGRESS_MAX_PCT: Final = 99
# The job options of MowPattern (``start_options``); start progress is never a default.
JOB_OPTIONS: Final = ("obstacle_detection", "obstacle_laps", "order", "start_progress_pct")
DEFAULT_JOB_OPTIONS: Final = ("obstacle_detection", "obstacle_laps", "order")

# The start parameters in the order the action lists them.
PARAMETERS: Final = (
    "pattern",
    "angle_deg",
    "border_laps",
    "spacing_cm",
    "blade_height_mm",
    "speed_mps",
    "obstacle_detection",
    "obstacle_laps",
    "order",
    "start_progress_pct",
)
# Path spacing in centimetres, the unit people set it in; MowDeck stores metres.
CM_PER_M: Final = 100


def _number(value: Any) -> float | None:
    """A number, None for anything else (booleans included)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _pair(value: Any) -> tuple[float, float] | None:
    """A declared ``(min, max)`` pair of numbers, None otherwise."""
    if not isinstance(value, list | tuple) or len(value) != 2:
        return None
    low, high = _number(value[0]), _number(value[1])
    if low is None or high is None or low > high:
        return None
    return low, high


def native_patterns(caps: dict[str, Any]) -> list[str]:
    """The mower's native patterns that MowDeck's mowing defaults know, in MowDeck's order."""
    offered = {str(p) for p in caps.get("native_patterns") or []}
    return [pattern for pattern in PATTERNS if pattern in offered]


def spacing_range_cm(caps: dict[str, Any]) -> tuple[float, float] | None:
    """The model's path spacing range in cm, within MowDeck's limits; None when not settable."""
    bounds = _pair(caps.get("path_spacing_range_mm"))
    if bounds is None:
        return None
    low = max(API_SPACING_MIN_M * CM_PER_M, bounds[0] / 10)
    high = min(API_SPACING_MAX_M * CM_PER_M, bounds[1] / 10)
    return (low, high) if low <= high else None


def blade_height_range(caps: dict[str, Any]) -> tuple[int, int]:
    """The blade height range in mm from the capabilities, within the API's limits."""
    bounds = _pair(caps.get("blade_height_range_mm"))
    if bounds is not None:
        low = max(API_BLADE_HEIGHT_MIN_MM, int(bounds[0]))
        high = min(API_BLADE_HEIGHT_MAX_MM, int(bounds[1]))
        if low <= high:
            return low, high
    return API_BLADE_HEIGHT_MIN_MM, API_BLADE_HEIGHT_MAX_MM


def speed(value: Any) -> float | None:
    """A speed in m/s with two decimals; None for anything that is not a number."""
    number = _number(value)
    return None if number is None else round(number, SPEED_DECIMALS)


def speed_range(caps: dict[str, Any]) -> tuple[float, float]:
    """The mowing speed range in m/s: the capabilities' bounds within the API's limit (0.1 m/s
    at the bottom when they name none)."""
    top = speed(caps.get("max_speed_mps"))
    high = min(top, API_SPEED_MAX_MPS) if top is not None and top > 0 else API_SPEED_MAX_MPS
    bottom = speed(caps.get("min_speed_mps"))
    low = bottom if bottom is not None and 0 < bottom <= high else DEFAULT_MIN_SPEED_MPS
    return min(low, high), high


def declared_start_options(caps: dict[str, Any]) -> dict[str, Any]:
    """``MowerCapabilities.start_options`` (empty while the mower declares none)."""
    options = caps.get("start_options")
    return options if isinstance(options, dict) else {}


def offered_options(caps: dict[str, Any], key: str, known: list[str]) -> list[str]:
    """The declared values of a list option that MowDeck knows, in the declared order."""
    declared = declared_start_options(caps).get(key)
    if not isinstance(declared, list):
        return []
    return [str(value) for value in declared if str(value) in known]


def declared_range(caps: dict[str, Any], key: str, high: int) -> tuple[int, int] | None:
    """A declared ``(min, max)`` range of whole numbers within ``0..high``, None when not
    settable."""
    bounds = _pair(declared_start_options(caps).get(key))
    if bounds is None:
        return None
    low, top = max(0, int(bounds[0])), min(high, int(bounds[1]))
    return (low, top) if low <= top else None


def _range(low: float, high: float, step: float) -> dict[str, float]:
    return {"min": low, "max": high, "step": step}


def start_parameters(caps: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The start parameters a mower accepts, each with its options or range."""
    params: dict[str, dict[str, Any]] = {}
    if patterns := native_patterns(caps):
        params["pattern"] = {"options": patterns}
        params["angle_deg"] = _range(0, API_ANGLE_MAX_DEG, 1)
        params["border_laps"] = _range(0, API_BORDER_LAPS_MAX, 1)
        if (spacing := spacing_range_cm(caps)) is not None:
            params["spacing_cm"] = _range(spacing[0], spacing[1], 1)
    blade = blade_height_range(caps)
    params["blade_height_mm"] = _range(blade[0], blade[1], 1)
    low, high = speed_range(caps)
    params["speed_mps"] = _range(low, high, SPEED_STEP_MPS)
    if detection := offered_options(caps, "obstacle_detection", OBSTACLE_DETECTION):
        params["obstacle_detection"] = {"options": detection}
    if (laps := declared_range(caps, "obstacle_laps_range", API_OBSTACLE_LAPS_MAX)) is not None:
        params["obstacle_laps"] = _range(laps[0], laps[1], 1)
    if orders := offered_options(caps, "orders", PATH_ORDERS):
        params["order"] = {"options": orders}
    progress = declared_range(caps, "start_progress_range_pct", API_START_PROGRESS_MAX_PCT)
    if progress is not None:
        params["start_progress_pct"] = _range(progress[0], progress[1], 1)
    return params


def problem(spec: dict[str, Any] | None, value: Any) -> str | None:
    """Why ``value`` does not fit a parameter described by ``spec``: ``unsupported`` (the mower
    does not offer the parameter) or ``invalid`` (not one of its options or out of its range);
    None when it fits."""
    if spec is None:
        return "unsupported"
    if "options" in spec:
        return None if value in spec["options"] else "invalid"
    return None if spec["min"] <= value <= spec["max"] else "invalid"


def describe(spec: dict[str, Any] | None) -> str:
    """The allowed values of a parameter for an error message."""
    if spec is None:
        return "-"
    if "options" in spec:
        return ", ".join(str(option) for option in spec["options"])
    return f"{spec['min']:g} - {spec['max']:g}"
