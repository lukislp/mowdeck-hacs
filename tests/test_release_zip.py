"""The HACS release zip (scripts/build_release_zip.py)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from scripts.build_release_zip import COMPONENT, build, main, stamp_manifest


def test_zip_holds_the_integration_with_the_version(tmp_path: Path) -> None:
    """The archive is the content of custom_components/mowdeck with the stamped version."""
    out = tmp_path / "assets" / "mowdeck.zip"
    names = build("0.4.2", out)
    assert "manifest.json" in names
    assert "__init__.py" in names
    assert "translations/de.json" in names
    assert "brand/icon.png" in names
    assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc")]
    with zipfile.ZipFile(out) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["version"] == "0.4.2"
        assert manifest["domain"] == "mowdeck"
    # The repository's own manifest is untouched.
    original = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert original["version"] == "0.0.0"


def test_zip_is_reproducible(tmp_path: Path) -> None:
    """Two builds of the same version are byte for byte equal."""
    first, second = tmp_path / "a.zip", tmp_path / "b.zip"
    build("1.2.3", first)
    build("1.2.3", second)
    assert first.read_bytes() == second.read_bytes()


def test_stamp_changes_only_the_version_line() -> None:
    """The manifest keeps its formatting; only the version changes."""
    text = '{\n  "domain": "mowdeck",\n  "codeowners": ["@lukislp"],\n  "version": "0.0.0"\n}\n'
    assert stamp_manifest(text, "2.0.1") == text.replace("0.0.0", "2.0.1")
    with pytest.raises(ValueError, match="no version"):
        stamp_manifest('{"domain": "mowdeck"}', "1.0.0")


def test_rejects_a_non_release_version(tmp_path: Path) -> None:
    """Only plain x.y.z versions are released."""
    with pytest.raises(ValueError, match="not a release version"):
        build("v1.0.0", tmp_path / "x.zip")


def test_command_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The workflow's invocation."""
    out = tmp_path / "mowdeck.zip"
    assert main(["--version", "0.1.0", "--out", str(out)]) == 0
    assert out.is_file()
    assert "version 0.1.0" in capsys.readouterr().out
