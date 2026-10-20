"""Build the HACS release asset: mowdeck.zip with the release version in manifest.json.

HACS installs the zip of a release (``zip_release`` in hacs.json) by unpacking it into
``custom_components/mowdeck``, so the archive holds the content of that folder, without
caches. The repository's manifest.json stays at 0.0.0; only the copy in the zip carries the
version. The archive is reproducible: sorted entries with a fixed timestamp.

Usage: ``python3 scripts/build_release_zip.py --version 0.1.0 --out assets/mowdeck.zip``
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "mowdeck"
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
# 1980-01-01, the earliest time a zip entry can carry.
FIXED_TIME = (1980, 1, 1, 0, 0, 0)
SKIPPED_PARTS = {"__pycache__"}
SKIPPED_SUFFIXES = {".pyc", ".pyo"}


def stamp_manifest(text: str, version: str) -> str:
    """The manifest with ``version`` replaced; only that line changes (no JSON reformatting)."""
    if json.loads(text).get("version") is None:
        msg = "manifest.json has no version field"
        raise ValueError(msg)
    updated, count = re.subn(r'("version"\s*:\s*)"[^"]*"', rf'\g<1>"{version}"', text, count=1)
    if count != 1:  # pragma: no cover - guarded by the json check above
        msg = "manifest.json has no version field"
        raise ValueError(msg)
    return updated


def files(component: Path) -> list[Path]:
    """Every file of the integration that belongs into the zip, sorted."""
    return sorted(
        path
        for path in component.rglob("*")
        if path.is_file()
        and not SKIPPED_PARTS.intersection(path.relative_to(component).parts)
        and path.suffix not in SKIPPED_SUFFIXES
    )


def build(version: str, out: Path, component: Path = COMPONENT) -> list[str]:
    """Write the zip; returns the archive names."""
    if not VERSION.match(version):
        msg = f"not a release version: {version!r}"
        raise ValueError(msg)
    out.parent.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files(component):
            name = path.relative_to(component).as_posix()
            data = path.read_bytes()
            if name == "manifest.json":
                data = stamp_manifest(data.decode("utf-8"), version).encode("utf-8")
            info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
            names.append(name)
    return names


def main(argv: list[str] | None = None) -> int:
    """Command line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    names = build(args.version, args.out)
    print(f"{args.out}: {len(names)} files, version {args.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
