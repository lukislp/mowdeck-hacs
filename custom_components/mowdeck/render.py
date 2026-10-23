"""SVG pictures of a mower's map and heatmaps, rendered in Home Assistant without any extra
package: areas, no-go zones, obstacles, transfer paths, the dock, the mower with its heading,
the planned path of the running job with its progress, and the heatmap layers with the missed
spots. Everything is drawn in the map's local metres (north up) with MowDeck's light colour
tokens (``libs/mowdeck-ui/src/lib/styles/tokens.css``) and the colour scales of its heatmap
legend (``heat.ts``). The output contains numbers only, never names, so nothing needs escaping.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Any, Final

# MowDeck's light tokens.
BACKGROUND: Final = "#f4f5f2"
LINE: Final = "#c9cdc4"
LINE_STRONG: Final = "#8c928a"
INK: Final = "#111412"
INK_MUTED: Final = "#4a514b"
GOOD: Final = "#1f7a35"  # --md-state-mowing, --md-rtk-fixed
GOOD_BG: Final = "#e3f1e6"
WARN: Final = "#8a5200"  # --md-state-paused, --md-rtk-float
BAD: Final = "#b3261e"  # --md-state-error, --md-rtk-none
BAD_BG: Final = "#f8e3e1"
CHARGING: Final = "#2f55b8"

# The picture's longer side in pixels and the margin around the drawing in metres.
SIZE_PX: Final = 800
MARGIN_M: Final = 2.0
# heat.ts: the fixed share bands and the dBm span.
FIX_WEAK: Final = 0.5
FIX_GOOD: Final = 0.9
DBM_MIN: Final = -90.0
DBM_MAX: Final = -40.0

type Point = tuple[float, float]


def mix(a: str, share: float, b: str) -> str:
    """``color-mix(in srgb, a share%, b)``."""
    t = max(0.0, min(100.0, share)) / 100
    ca = [int(a[i : i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x * t + y * (1 - t)):02x}" for x, y in zip(ca, cb, strict=True))


def heat_color(kind: str, value: float) -> str:
    """The colour of a heatmap value by its legend kind, as the web app's scales: coverage in
    five steps from ink to green, RTK fixed share in three bands, dBm in five steps from red
    through amber to green between -90 and -40."""
    if kind == "fix":
        return BAD if value < FIX_WEAK else WARN if value < FIX_GOOD else GOOD
    if kind == "dbm":
        step = (DBM_MAX - DBM_MIN) / 5
        index = min(4, max(0, math.floor((value - DBM_MIN) / step)))
        return (BAD, mix(BAD, 50, WARN), WARN, mix(WARN, 50, GOOD), GOOD)[index]
    index = min(4, max(0, math.floor(value / 0.2)))
    return mix(GOOD, index * 25, INK_MUTED)


def _points(raw: Any) -> list[Point]:
    """``[[x, y], ...]`` as floats; anything malformed is skipped."""
    out: list[Point] = []
    for point in raw or []:
        if isinstance(point, list | tuple) and len(point) >= 2:
            try:
                out.append((float(point[0]), float(point[1])))
            except (TypeError, ValueError):
                continue
    return out


def _n(value: float) -> str:
    """A coordinate with centimetre precision and no trailing zeros."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _ring(points: Sequence[Point]) -> str:
    """An SVG path of a closed ring (y up becomes y down)."""
    return "M" + "L".join(f"{_n(x)} {_n(-y)}" for x, y in points) + "Z"


def _line(points: Sequence[Point]) -> str:
    return "M" + "L".join(f"{_n(x)} {_n(-y)}" for x, y in points)


class _Bounds:
    """The extent of everything drawn, in local metres."""

    def __init__(self) -> None:
        self.min_x = self.min_y = math.inf
        self.max_x = self.max_y = -math.inf

    def add(self, points: Iterable[Point]) -> None:
        for x, y in points:
            self.min_x, self.max_x = min(self.min_x, x), max(self.max_x, x)
            self.min_y, self.max_y = min(self.min_y, y), max(self.max_y, y)

    @property
    def empty(self) -> bool:
        return self.min_x > self.max_x


def _stroke(color: str, px: float) -> str:
    """Stroke attributes in screen pixels, whatever the size of the garden."""
    return f'stroke="{color}" stroke-width="{px:g}" vector-effect="non-scaling-stroke"'


def _unit(bounds: _Bounds) -> float:
    """Metres per pixel of the picture (markers are sized in pixels)."""
    if bounds.empty:
        return (10.0 + 2 * MARGIN_M) / SIZE_PX
    width = bounds.max_x - bounds.min_x + 2 * MARGIN_M
    height = bounds.max_y - bounds.min_y + 2 * MARGIN_M
    return max(width, height) / SIZE_PX


def _document(bounds: _Bounds, body: list[str]) -> bytes:
    if bounds.empty:
        bounds.add([(-5.0, -5.0), (5.0, 5.0)])
    min_x, max_x = bounds.min_x - MARGIN_M, bounds.max_x + MARGIN_M
    min_y, max_y = bounds.min_y - MARGIN_M, bounds.max_y + MARGIN_M
    width, height = max_x - min_x, max_y - min_y
    scale = SIZE_PX / max(width, height)
    px_w, px_h = max(1, round(width * scale)), max(1, round(height * scale))
    view = f"{_n(min_x)} {_n(-max_y)} {_n(width)} {_n(height)}"
    head = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{px_w}" height="{px_h}" '
        f'viewBox="{view}" stroke-linejoin="round" stroke-linecap="round">'
        f'<rect x="{_n(min_x)}" y="{_n(-max_y)}" width="{_n(width)}" height="{_n(height)}" '
        f'fill="{BACKGROUND}"/>'
    )
    return (head + "".join(body) + "</svg>").encode()


def _zones(mower_map: dict[str, Any], key: str) -> list[list[Point]]:
    return [
        ring
        for zone in mower_map.get(key) or []
        if isinstance(zone, dict) and len(ring := _points(zone.get("boundary"))) >= 3
    ]


def _base(mower_map: dict[str, Any], bounds: _Bounds, *, filled: bool = True) -> list[str]:
    """Areas, obstacles, no-go zones and transfer paths."""
    body: list[str] = []
    areas = _zones(mower_map, "areas")
    for ring in areas:
        bounds.add(ring)
    if areas:
        fill = GOOD_BG if filled else "none"
        rings = "".join(_ring(r) for r in areas)
        body.append(f'<path d="{rings}" fill="{fill}" {_stroke(GOOD, 2.5)}/>')
    for key, fill, stroke in (("obstacles", LINE, LINE_STRONG), ("no_go_zones", BAD_BG, BAD)):
        zones = _zones(mower_map, key)
        for ring in zones:
            bounds.add(ring)
        if zones:
            rings = "".join(_ring(r) for r in zones)
            opacity = ' fill-opacity="0.8"' if not filled else ""
            body.append(f'<path d="{rings}" fill="{fill}"{opacity} {_stroke(stroke, 2)}/>')
    lines = [
        line
        for path in mower_map.get("paths") or []
        if isinstance(path, dict) and len(line := _points(path.get("points"))) >= 2
    ]
    for line in lines:
        bounds.add(line)
    if lines:
        d = "".join(_line(line) for line in lines)
        body.append(f'<path d="{d}" fill="none" {_stroke(LINE_STRONG, 2)} stroke-dasharray="6 4"/>')
    return body


def _arrow(x: float, y: float, angle: float, size: float) -> list[Point]:
    """A triangle pointing at ``angle`` (radians, counter-clockwise from +x)."""
    tip = (x + size * math.cos(angle), y + size * math.sin(angle))
    left = (x + size * 0.6 * math.cos(angle + 2.4), y + size * 0.6 * math.sin(angle + 2.4))
    right = (x + size * 0.6 * math.cos(angle - 2.4), y + size * 0.6 * math.sin(angle - 2.4))
    return [tip, left, right]


def render_map(
    mower_map: dict[str, Any],
    *,
    mower: tuple[float, float, float | None] | None = None,
    path: dict[str, Any] | None = None,
) -> bytes:
    """The map with the dock, the mower (local x, y and local heading in radians, or None)
    and the planned path of the running job (``MowPath``: mowed part solid, the rest faint)."""
    bounds = _Bounds()
    body = _base(mower_map, bounds)
    if path:
        body.extend(_planned_path(path))
    dock = mower_map.get("dock")
    has_dock = isinstance(dock, dict) and dock.get("x") is not None
    if isinstance(dock, dict) and has_dock:
        bounds.add([(float(dock["x"]), float(dock["y"]))])
    if mower is not None:
        bounds.add([(mower[0], mower[1])])
    unit = _unit(bounds)
    if isinstance(dock, dict) and has_dock:
        x, y = float(dock["x"]), float(dock["y"])
        heading = math.radians(float(dock.get("heading_deg") or 0.0))
        half = 7 * unit
        square = [
            (x - half, y - half),
            (x + half, y - half),
            (x + half, y + half),
            (x - half, y + half),
        ]
        tick = [(x, y), (x + 4 * half * math.cos(heading), y + 4 * half * math.sin(heading))]
        body.append(f'<path d="{_ring(square)}" fill="{CHARGING}" {_stroke("#ffffff", 1.5)}/>')
        body.append(f'<path d="{_line(tick)}" {_stroke(CHARGING, 2.5)}/>')
    if mower is not None:
        x, y, angle = mower
        if angle is None:
            body.append(
                f'<circle cx="{_n(x)}" cy="{_n(-y)}" r="{_n(8 * unit)}" fill="{INK}" '
                f"{_stroke('#ffffff', 2)}/>"
            )
        else:
            body.append(
                f'<path d="{_ring(_arrow(x, y, angle, 16 * unit))}" fill="{INK}" '
                f"{_stroke('#ffffff', 2)}/>"
            )
    return _document(bounds, body)


def _planned_path(path: dict[str, Any]) -> list[str]:
    """The route's polylines: points before ``progress.index`` (flat over all polylines) are
    mowed and drawn solid, the rest faint."""
    progress = path.get("progress")
    done = int(progress.get("index") or 0) if isinstance(progress, dict) else 0
    mowed: list[list[Point]] = []
    ahead: list[list[Point]] = []
    seen = 0
    for raw in path.get("polylines") or []:
        line = _points(raw)
        cut = max(0, min(len(line), done - seen))
        if cut >= 2:
            mowed.append(line[:cut])
        rest = line[max(0, cut - 1) :]
        if len(rest) >= 2:
            ahead.append(rest)
        seen += len(line)
    body: list[str] = []
    if ahead:
        d = "".join(_line(line) for line in ahead)
        body.append(f'<path d="{d}" fill="none" {_stroke(GOOD, 1.5)} stroke-opacity="0.35"/>')
    if mowed:
        d = "".join(_line(line) for line in mowed)
        body.append(f'<path d="{d}" fill="none" {_stroke(GOOD, 2)}/>')
    return body


def render_heatmap(mower_map: dict[str, Any], heatmap: dict[str, Any]) -> bytes:
    """A heatmap layer over the map's outlines: one square per cell coloured by the legend's
    scale, and for coverage the missed spots outlined in red."""
    bounds = _Bounds()
    cell = float(heatmap.get("cell_m") or 1.0)
    kind = str((heatmap.get("legend") or {}).get("kind") or "ratio")
    by_color: dict[str, list[str]] = {}
    for item in heatmap.get("cells") or []:
        if not isinstance(item, dict):
            continue
        try:
            x0, y0 = int(item["x"]) * cell, int(item["y"]) * cell
            value = float(item["value"])
        except (KeyError, TypeError, ValueError):
            continue
        bounds.add([(x0, y0), (x0 + cell, y0 + cell)])
        by_color.setdefault(heat_color(kind, value), []).append(
            f"M{_n(x0)} {_n(-(y0 + cell))}h{_n(cell)}v{_n(cell)}h{_n(-cell)}z"
        )
    body = _base(mower_map, bounds, filled=False)
    cells = [f'<path d="{"".join(d)}" fill="{color}"/>' for color, d in sorted(by_color.items())]
    gaps = [
        ring
        for gap in heatmap.get("gaps") or []
        if isinstance(gap, dict) and len(ring := _points(gap.get("polygon"))) >= 3
    ]
    for ring in gaps:
        bounds.add(ring)
    outline = []
    if gaps:
        rings = "".join(_ring(r) for r in gaps)
        outline.append(f'<path d="{rings}" fill="{BAD}" fill-opacity="0.25" {_stroke(BAD, 2)}/>')
    return _document(bounds, cells + body + outline)
