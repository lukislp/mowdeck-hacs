"""quality_scale.yaml claims Silver: every Bronze and Silver rule is done or exempt."""

from __future__ import annotations

from pathlib import Path

from homeassistant.util.yaml import load_yaml_dict

QUALITY_SCALE = Path(__file__).parent.parent / "custom_components/mowdeck/quality_scale.yaml"

BRONZE_AND_SILVER = {
    "action-setup",
    "appropriate-polling",
    "brands",
    "common-modules",
    "config-flow",
    "config-flow-test-coverage",
    "dependency-transparency",
    "docs-actions",
    "docs-high-level-description",
    "docs-installation-instructions",
    "docs-removal-instructions",
    "entity-event-setup",
    "entity-unique-id",
    "has-entity-name",
    "runtime-data",
    "test-before-configure",
    "test-before-setup",
    "unique-config-entry",
    "action-exceptions",
    "config-entry-unloading",
    "docs-configuration-parameters",
    "docs-installation-parameters",
    "entity-unavailable",
    "integration-owner",
    "log-when-unavailable",
    "parallel-updates",
    "reauthentication-flow",
    "test-coverage",
}


def test_silver_rules_are_met() -> None:
    """No Bronze or Silver rule is todo; exemptions carry a reason."""
    rules = load_yaml_dict(str(QUALITY_SCALE))["rules"]
    for rule in BRONZE_AND_SILVER:
        value = rules[rule]
        status = value if isinstance(value, str) else value["status"]
        assert status in ("done", "exempt"), rule
        if status == "exempt":
            assert value["comment"], rule
