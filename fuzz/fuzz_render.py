"""Atheris fuzz harness for the SVG renderer and the local-frame geometry.

`custom_components/mowdeck/render.py` and `geo.py` turn the JSON the MowDeck server sends (map
outlines, planned path, heatmap cells) into SVG pictures. Both are pure standard-library
modules, so the harness loads them straight from their files and needs no Home Assistant.

Contract under test: for any JSON-shaped map, path, heatmap and mower position (entries of
the wrong shape included, the renderer skips what it cannot read), render_map() and
render_heatmap() return a well-formed SVG document with finite numbers only and never raise;
the geometry helpers return finite numbers for finite input.

Run locally (Linux, Python 3.12 or 3.13 - the atheris wheel does not exist for 3.14):
    uv venv --python 3.12 .fuzz-venv
    uv pip install --python .fuzz-venv --require-hashes --only-binary=:all: -r fuzz/requirements.txt
    .fuzz-venv/bin/python fuzz/fuzz_render.py -max_total_time=60
CI runs the same harness for a short, fixed time budget (see .github/workflows/ci.yml).
"""

from __future__ import annotations

import importlib.util
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import ModuleType
from typing import Any

import atheris

_COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "mowdeck"


def _load(name: str) -> ModuleType:
    # custom_components/mowdeck/__init__.py imports Home Assistant, so load the pure module
    # by path instead of through the package.
    spec = importlib.util.spec_from_file_location(f"mowdeck_{name}", _COMPONENT / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


render = _load("render")
geo = _load("geo")


def _number(fdp: atheris.FuzzedDataProvider) -> float:
    """A finite coordinate (JSON from the server has no NaN or infinity)."""
    value = fdp.ConsumeFloatInRange(-1.0e6, 1.0e6)
    return float(round(value)) if fdp.ConsumeBool() else value


def _point(fdp: atheris.FuzzedDataProvider) -> Any:
    kind = fdp.ConsumeIntInRange(0, 9)
    if kind == 0:
        return None
    if kind == 1:
        return fdp.ConsumeUnicodeNoSurrogates(4)
    if kind == 2:
        return [_number(fdp)]
    if kind == 3:
        return [fdp.ConsumeUnicodeNoSurrogates(3), _number(fdp)]
    return [_number(fdp), _number(fdp)]


def _points(fdp: atheris.FuzzedDataProvider, limit: int = 6) -> list[Any]:
    return [_point(fdp) for _ in range(fdp.ConsumeIntInRange(0, limit))]


def _zones(fdp: atheris.FuzzedDataProvider, key: str) -> list[Any]:
    zones: list[Any] = []
    for _ in range(fdp.ConsumeIntInRange(0, 3)):
        zones.append(
            {key: _points(fdp)}
            if fdp.ConsumeIntInRange(0, 7)
            else fdp.ConsumeUnicodeNoSurrogates(2)
        )
    return zones


def _mower_map(fdp: atheris.FuzzedDataProvider) -> dict[str, Any]:
    mower_map: dict[str, Any] = {
        "areas": _zones(fdp, "boundary"),
        "obstacles": _zones(fdp, "boundary"),
        "no_go_zones": _zones(fdp, "boundary"),
        "paths": [{"points": _points(fdp)} for _ in range(fdp.ConsumeIntInRange(0, 2))],
    }
    if fdp.ConsumeBool():
        mower_map["dock"] = {
            "x": _number(fdp),
            "y": _number(fdp),
            "heading_deg": _number(fdp),
        }
    return mower_map


def _check_svg(document: bytes) -> None:
    if not isinstance(document, bytes):
        raise AssertionError("the renderer did not return bytes")
    text = document.decode()
    lowered = text.lower()
    if "nan" in lowered or "inf" in lowered:
        raise AssertionError(f"non-finite number in the SVG: {text[:200]!r}")
    root = ET.fromstring(text)  # noqa: S314 - the renderer's own output
    if not root.tag.endswith("svg"):
        raise AssertionError(f"not an SVG document: {root.tag}")


def test_one_input(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    mower_map = _mower_map(fdp)

    mower = None
    if fdp.ConsumeBool():
        angle = _number(fdp) if fdp.ConsumeBool() else None
        mower = (_number(fdp), _number(fdp), angle)
    path = None
    if fdp.ConsumeBool():
        path = {
            "progress": {"index": fdp.ConsumeIntInRange(-3, 30)},
            "polylines": [_points(fdp) for _ in range(fdp.ConsumeIntInRange(0, 3))],
        }
    _check_svg(render.render_map(mower_map, mower=mower, path=path))

    cells = [
        {
            "x": fdp.ConsumeIntInRange(-500, 500),
            "y": fdp.ConsumeIntInRange(-500, 500),
            "value": _number(fdp) / 1.0e6 if fdp.ConsumeBool() else _number(fdp),
        }
        for _ in range(fdp.ConsumeIntInRange(0, 6))
    ]
    heatmap = {
        "cell_m": fdp.ConsumeFloatInRange(0.1, 5.0),
        "legend": {"kind": fdp.PickValueInList(["ratio", "fix", "dbm"])},
        "cells": cells,
        "gaps": [{"polygon": _points(fdp)} for _ in range(fdp.ConsumeIntInRange(0, 2))],
    }
    _check_svg(render.render_heatmap(mower_map, heatmap))

    reference = {
        "origin_lat": fdp.ConsumeFloatInRange(-89.0, 89.0),
        "origin_lon": fdp.ConsumeFloatInRange(-180.0, 180.0),
        "rotation_deg": fdp.ConsumeFloatInRange(-720.0, 720.0),
    }
    x, y = geo.from_wgs84(
        reference, fdp.ConsumeFloatInRange(-90.0, 90.0), fdp.ConsumeFloatInRange(-180.0, 180.0)
    )
    angle = geo.local_angle(fdp.ConsumeFloatInRange(-720.0, 720.0), reference)
    if not all(math.isfinite(value) for value in (x, y, angle)):
        raise AssertionError("the geometry helpers returned a non-finite number")


if __name__ == "__main__":
    # instrument_all() instead of instrument_imports(): the modules are loaded by path, which
    # the import hook does not see (no coverage feedback).
    atheris.instrument_all()
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()
