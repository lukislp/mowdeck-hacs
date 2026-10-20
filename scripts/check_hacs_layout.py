"""Offline check of the HACS repository layout of this folder.

The HACS action validates a GitHub repository through the GitHub API, so in the monorepo it
can only run against the dedicated repository. This script checks, without network, what the
action checks on the files: hacs.json, the manifest keys HACS requires, one integration under
custom_components, and the brand icon. Usage: ``uv run python scripts/check_hacs_layout.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_KEYS = ("domain", "name", "documentation", "issue_tracker", "codeowners", "version")
HACS_KEYS = {"name", "content_in_root", "homeassistant", "render_readme"}


def main() -> int:
    """Print every problem; exit 1 when there is one."""
    problems: list[str] = []
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    if missing := HACS_KEYS - hacs.keys():
        problems.append(f"hacs.json lacks {sorted(missing)}")
    if hacs.get("content_in_root") is not False:
        problems.append("hacs.json: content_in_root must be false (custom_components/ layout)")
    integrations = [p for p in (ROOT / "custom_components").iterdir() if p.is_dir()]
    if len(integrations) != 1:
        problems.append(f"exactly one integration expected, found {[p.name for p in integrations]}")
    for folder in integrations:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        if missing_keys := [k for k in MANIFEST_KEYS if not manifest.get(k)]:
            problems.append(f"{folder.name}/manifest.json lacks {missing_keys}")
        if manifest.get("domain") != folder.name:
            problems.append(f"{folder.name}: domain {manifest.get('domain')!r} != folder name")
        if not (folder / "brand" / "icon.png").is_file():
            problems.append(f"{folder.name}/brand/icon.png is missing")
        for language in ("en", "de"):
            if not (folder / "translations" / f"{language}.json").is_file():
                problems.append(f"{folder.name}/translations/{language}.json is missing")
    if not (ROOT / "README.md").is_file():
        problems.append("README.md is missing (render_readme)")
    for problem in problems:
        print(f"HACS layout: {problem}")
    if not problems:
        print("HACS layout: ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
