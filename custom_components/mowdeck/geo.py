"""The map's local frame: WGS84 to local metres and back, an exact port of
``mowdeck_control.geo`` (local tangent plane, equirectangular) of lukislp/mowdeck.

The local point ``(0, 0)`` lies at the reference's ``origin_lat``/``origin_lon``; the local
axes are east and north rotated counter-clockwise by ``rotation_deg``.
"""

from __future__ import annotations

import math
from typing import Any, Final

# WGS84 equatorial radius (mowdeck_control.geo.EARTH_RADIUS_M).
EARTH_RADIUS_M: Final = 6_378_137.0


def from_wgs84(reference: dict[str, Any], lat: float, lon: float) -> tuple[float, float]:
    """``[lat, lon]`` in degrees to local metres ``(x, y)``."""
    origin_lat = float(reference["origin_lat"])
    north = math.radians(lat - origin_lat) * EARTH_RADIUS_M
    lon_scale = EARTH_RADIUS_M * math.cos(math.radians(origin_lat))
    east = math.radians(lon - float(reference["origin_lon"])) * lon_scale
    rotation = math.radians(float(reference.get("rotation_deg") or 0.0))
    x = east * math.cos(rotation) + north * math.sin(rotation)
    y = -east * math.sin(rotation) + north * math.cos(rotation)
    return x, y


def local_angle(heading_deg: float, reference: dict[str, Any]) -> float:
    """A compass heading (degrees, 0 = north, clockwise) as a local direction in radians
    (counter-clockwise from +x); the inverse of ``compassHeading`` of the web map."""
    rotation = float(reference.get("rotation_deg") or 0.0)
    return math.radians(90.0 - heading_deg - rotation)
