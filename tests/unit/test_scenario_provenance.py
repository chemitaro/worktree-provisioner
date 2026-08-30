from __future__ import annotations

import json
import re
from pathlib import Path

MANIFEST = Path(__file__).parents[1] / "scenario_provenance.json"
ALL_ACCEPTANCE_CRITERIA = {f"WTP-AC-{index:03d}" for index in range(1, 21)}
REPOSITORY_ROOT = Path(__file__).parents[2]
THREAT_BOUNDARY_DOCS = (
    REPOSITORY_ROOT / "docs/specification/requirement.md",
    REPOSITORY_ROOT / "docs/specification/design.md",
    REPOSITORY_ROOT / "docs/specification/plan.md",
    REPOSITORY_ROOT / "README.md",
    REPOSITORY_ROOT / "skills/worktree-provisioner/SKILL.md",
)


def test_p1_manifest_maps_every_acceptance_criterion() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    mappings = manifest["acceptance_criteria_mapping"]

    assert set(mappings) == ALL_ACCEPTANCE_CRITERIA
    scenario_ids = {scenario["id"] for scenario in manifest["scenarios"]}
    assert scenario_ids
    for acceptance_criterion, scenarios in mappings.items():
        assert scenarios, acceptance_criterion
        assert set(scenarios) <= scenario_ids


def test_p1_manifest_records_non_compatibility_boundaries() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    forbidden = set(manifest["forbidden_acceptance_assumptions"])

    assert "SpecDock executable availability" in forbidden
    assert "legacy success through SPEC_DOCK_WORKTREE_ROOT" in forbidden
    assert "bootstrap failure exit 0" in forbidden
    assert "external remove success" in forbidden
    assert "double force or implicit unlock" in forbidden


def test_p1_manifest_test_references_exist_and_are_traceable() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    for scenario in manifest["scenarios"]:
        test_reference = scenario["test"]
        test_path_text, separator, symbol = test_reference.partition("::")
        assert separator, test_reference
        test_path = REPOSITORY_ROOT / test_path_text
        assert test_path.is_file(), test_reference
        source = test_path.read_text(encoding="utf-8")
        assert re.search(rf"^\s*(?:async\s+)?def\s+{re.escape(symbol)}\(", source, re.MULTILINE), test_reference

    mappings = manifest["acceptance_criteria_mapping"]
    assert mappings["WTP-AC-015"] == ["P1-S17"]
    assert mappings["WTP-AC-018"] == ["P1-S18"]


def test_namespace_ancestor_race_boundary_is_documented() -> None:
    documents = [path.read_text(encoding="utf-8") for path in THREAT_BOUNDARY_DOCS]
    required_in_every_document = (
        "WTP-THREAT-001",
        "外部・非協調",
        "final syscall window",
        "atomic prevention",
        "descriptor",
        "no-follow",
        "partial",
    )

    for content in documents:
        for fragment in required_in_every_document:
            assert fragment in content, fragment

    requirement = documents[0]
    design = documents[1]
    plan = documents[2]
    assert "WTP-NFR-001" in requirement
    assert "WTP-THREAT-001" in design and "INV-019" in design
    assert "R6" in plan and "threat-boundary documentation test" in plan
