from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from scripts.maintenance.contracts import (
    ContractRefusal,
    validate_instance_manifest_v4,
    validate_layout_v4,
)
ROOT = Path(__file__).resolve().parents[2]


def test_all_operation_schemas_are_valid_draft_2020_12() -> None:
    for path in sorted((ROOT / "schemas/operations").glob("*.schema.json")):
        Draft202012Validator.check_schema(json.loads(path.read_text()))


def test_layout_v4_is_strict_and_matches_project_contract() -> None:
    value = json.loads((ROOT.parent / "project-layout.json").read_text())
    assert validate_layout_v4(value)["schema_version"] == 4
    value["runtime"] = {"root": "runtime"}
    with pytest.raises(ContractRefusal, match="LAYOUT_V4_INVALID"):
        validate_layout_v4(value)


def test_instance_manifest_v4_has_no_release_or_operator_gateway() -> None:
    value = {
        "schemaVersion": "instance-manifest.v4",
        "instanceId": "research-one",
        "profile": "research",
        "composeProject": "groundwater-v6-research-one",
        "dataRoot": "/data/research-one",
        "stateRoot": "/state/research-one",
        "ports": {"gateway": 28186},
        "assemblyDigest": "sha256:" + "a" * 64,
        "createdAt": "2026-11-03T00:00:00Z",
        "status": "stopped",
    }
    assert validate_instance_manifest_v4(value) == value
    for retired in (
        value | {"releaseId": "sha256-" + "b" * 64},
        value | {"ports": {"gateway": 28186, "operator": 28187}},
    ):
        with pytest.raises(ContractRefusal, match="INSTANCE_MANIFEST_V4_INVALID"):
            validate_instance_manifest_v4(retired)
