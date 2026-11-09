"""Regenerate the executable CSV statistics domain pack from one wheel.

The wheel is built by uv.  This script only freezes its digest into ordinary
V6 declarations; it does not install, import, or authorize the capability.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path

from platform_lightweight_agent import (
    NodeContract,
    ToolRecoveryCapabilities,
    ToolReference,
    ValidationReportV1,
    ValueBinding,
    approval_step,
    condition_step,
    tool_step,
    workflow_v4,
)
from platform_lightweight_agent.spec import canonical_json

DIALECT = "https://json-schema.org/draft/2020-12/schema"
VERSION = "1.0.0"
RULE_REF = "csv-statistics.input-v1"
DISTRIBUTION = "v6-csv-statistics-capability"
ENTRY_POINT_GROUP = "platform_lightweight_agent.tools"
CAPABILITIES = {
    "validate": ToolRecoveryCapabilities(external_side_effects=False),
    "calculate": ToolRecoveryCapabilities(
        external_side_effects=True,
        accepts_idempotency_key=True,
    ),
    "blocked": ToolRecoveryCapabilities(external_side_effects=False),
}


def _schema(properties: dict[str, object], required: list[str]) -> dict[str, object]:
    return {
        "$schema": DIALECT,
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _file_schema(*, nullable: bool = False) -> dict[str, object]:
    return {
        "type": ["object", "null"] if nullable else "object",
        "properties": {
            "logical_id": {"type": "string", "minLength": 1, "maxLength": 1024},
            "sha256": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
            "size_bytes": {"type": "integer", "minimum": 0},
        },
        "required": ["logical_id", "sha256", "size_bytes"],
        "additionalProperties": False,
    }


def _profile_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "max_input_bytes": {"type": "integer", "minimum": 1},
            "max_rows": {"type": "integer", "minimum": 1},
            "round_digits": {"type": "integer", "minimum": 0, "maximum": 12},
        },
        "required": ["max_input_bytes", "max_rows", "round_digits"],
        "additionalProperties": False,
    }


def _summary_schema(*, nullable: bool = False) -> dict[str, object]:
    return {
        "type": ["object", "null"] if nullable else "object",
        "properties": {
            "count": {"type": "integer", "minimum": 1},
            "minimum": {"type": "number"},
            "maximum": {"type": "number"},
            "mean": {"type": "number"},
        },
        "required": ["count", "minimum", "maximum", "mean"],
        "additionalProperties": False,
    }


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")


def _manifest_name(reference: ToolReference) -> str:
    token = hashlib.sha256(
        canonical_json(reference.to_mapping()).encode("utf-8")
    ).hexdigest()[:24]
    return f"tool-manifest-{token}.json"


def _build_workflow(references: dict[str, ToolReference]):
    report = ValidationReportV1.schema(RULE_REF)
    profile = _profile_schema()
    workflow_input = _schema(
        {
            "file": _file_schema(),
            "column": {"type": "string", "minLength": 1, "maxLength": 256},
        },
        ["file", "column"],
    )
    checked_input = _schema(
        {"file": _file_schema(), "column": {"type": "string"}, "profile": profile},
        ["file", "column", "profile"],
    )
    final_output = _schema(
        {
            "business_status": {"enum": ["APPROVED", "BLOCKED"]},
            "reason_code": {"type": ["string", "null"]},
            "result_file": _file_schema(nullable=True),
            "summary": _summary_schema(nullable=True),
            "validation": report,
        },
        ["business_status", "reason_code", "result_file", "summary", "validation"],
    )
    check_bindings = {
        "/file": ValueBinding("workflow_input", pointer="/file"),
        "/column": ValueBinding("workflow_input", pointer="/column"),
        "/profile": ValueBinding(
            "literal", value={"$domain_parameter_profile": True}
        ),
    }
    validate = tool_step(
        "validate",
        references["validate"],
        approval_required=False,
        capabilities=CAPABILITIES["validate"],
        contract=NodeContract(checked_input, report, check_bindings),
    )
    approval = approval_step(
        "approve",
        "Review the validated CSV selection before publishing statistics.",
        contract=NodeContract(
            _schema({}, []),
            _schema({"approved": {"type": "boolean"}}, ["approved"]),
        ),
    )
    calculate = tool_step(
        "calculate",
        references["calculate"],
        approval_required=False,
        capabilities=CAPABILITIES["calculate"],
        contract=NodeContract(
            _schema(
                {
                    "file": _file_schema(),
                    "column": {"type": "string"},
                    "profile": profile,
                    "validation": report,
                },
                ["file", "column", "profile", "validation"],
            ),
            final_output,
            {
                **check_bindings,
                "/validation": ValueBinding(
                    "step_output", step_id="validate", pointer=""
                ),
            },
        ),
    )
    blocked = tool_step(
        "blocked",
        references["blocked"],
        approval_required=False,
        capabilities=CAPABILITIES["blocked"],
        contract=NodeContract(
            _schema({"validation": report}, ["validation"]),
            final_output,
            {
                "/validation": ValueBinding(
                    "step_output", step_id="validate", pointer=""
                )
            },
        ),
    )
    gate = condition_step(
        "gate",
        "/status",
        "PASS",
        (approval, calculate),
        (blocked,),
        contract=NodeContract(
            _schema(
                {"status": {"enum": ["PASS", "FAIL", "UNKNOWN"]}},
                ["status"],
            ),
            final_output,
            {
                "/status": ValueBinding(
                    "step_output", step_id="validate", pointer="/status"
                )
            },
        ),
    )
    return workflow_v4(
        "csv-statistics.analysis",
        VERSION,
        (validate, gate),
        input_schema=workflow_input,
        output_schema=final_output,
        output_bindings={
            f"/{field}": ValueBinding(
                "step_output", step_id="gate", pointer=f"/{field}"
            )
            for field in (
                "business_status",
                "reason_code",
                "result_file",
                "summary",
                "validation",
            )
        },
    )


def generate(root: Path, wheel: Path) -> None:
    wheel = wheel.resolve()
    if wheel.is_symlink() or not wheel.is_file() or wheel.suffix != ".whl":
        raise SystemExit("--wheel must name one built regular wheel")
    artifacts = root / "artifacts"
    artifacts.mkdir(exist_ok=True)
    for stale_manifest in artifacts.glob("tool-manifest-*.json"):
        if stale_manifest.is_symlink() or not stale_manifest.is_file():
            raise SystemExit("artifact manifest directory contains an unsafe entry")
        stale_manifest.unlink()
    destination_wheel = artifacts / wheel.name
    if wheel != destination_wheel.resolve():
        shutil.copyfile(wheel, destination_wheel)
    wheel_digest = _digest_bytes(destination_wheel.read_bytes())
    references = {
        name: ToolReference(f"csv-statistics.{name}", VERSION, wheel_digest)
        for name in ("validate", "calculate", "blocked")
    }
    permissions = {
        "validate": ["files.read"],
        "calculate": ["files.read", "files.write"],
        "blocked": [],
    }
    manifests = [
        {
            **references[name].to_mapping(),
            "permissions": permissions[name],
            "capabilities": CAPABILITIES[name].to_mapping(),
        }
        for name in ("validate", "calculate", "blocked")
    ]
    _write_json(root / "tools.json", manifests)
    for name, reference in references.items():
        _write_json(
            artifacts / _manifest_name(reference),
            {
                **reference.to_mapping(),
                "permissions": permissions[name],
                "capabilities": CAPABILITIES[name].to_mapping(),
            },
        )

    workflow = _build_workflow(references)
    _write_json(root / "workflows" / "analysis.json", workflow.to_mapping())
    method = root / "resources" / "method.md"
    cases = root / "tests" / "cases.json"
    capability_lock = root / "capability" / "pylock.toml"
    if not capability_lock.is_file():
        raise SystemExit("run uv lock/export in capability/ before generating release")
    shutil.copyfile(capability_lock, root / "pylock.toml")
    pack = {
        "schema_version": "v6-domain-pack-v1",
        "domain_id": "csv-statistics",
        "version": VERSION,
        "entry_workflows": [
            {
                "name": "analysis",
                "workflow": "workflows/analysis.json",
                "workflow_id": workflow.workflow_id,
                "version": workflow.version,
                "definition_digest": workflow.definition_digest,
                "required_checks": [
                    {
                        "rule_ref": RULE_REF,
                        "step_id": "validate",
                        "condition_step_id": "gate",
                        "protected_steps": ["calculate"],
                    }
                ],
            }
        ],
        "subworkflows": [],
        "tool_catalog": "tools.json",
        "dependency_lock": "pylock.toml",
        "tools": [reference.to_mapping() for reference in references.values()],
        "requested_permissions": ["files.read", "files.write"],
        "description": {
            "terms": {
                "file": "A host-staged, content-addressed CSV input.",
                "column": "The named finite-numeric column to summarize.",
                "result_file": "An immutable action-owned JSON statistics artifact.",
            },
            "applicability": [
                "Local UTF-8 CSV files with a unique header and finite numeric values."
            ],
            "limitations": [
                "This example computes unweighted descriptive statistics only.",
                "It intentionally has no network, model, or credential access.",
            ],
        },
        "checks": [
            {
                "rule_ref": RULE_REF,
                "required": True,
                "description": "The staged CSV, selected column, and frozen limits must validate before calculation.",
                "input_paths": ["/file", "/column"],
                "argument_paths": ["/file", "/column", "/profile"],
            }
        ],
        "parameter_profiles": [
            {
                "name": "standard",
                "defaults": {
                    "max_input_bytes": 8_388_608,
                    "max_rows": 100_000,
                    "round_digits": 6,
                },
            },
            {
                "name": "small",
                "defaults": {
                    "max_input_bytes": 1_048_576,
                    "max_rows": 10_000,
                    "round_digits": 3,
                },
            },
        ],
        "resources": [
            {
                "path": "resources/method.md",
                "sha256": _digest_bytes(method.read_bytes()),
                "role": "method",
            }
        ],
        "tests": [
            {
                "path": "tests/cases.json",
                "sha256": _digest_bytes(cases.read_bytes()),
                "role": "scenario",
            }
        ],
        "evaluation_cases": "tests/cases.json",
        "context_policy": {
            "schema_version": "v6-context-selection-policy-v1",
            "strategy": "declared-step-inputs-v1",
            "resources": ["resources/method.md"],
        },
        "driver_capabilities": [],
        "agent_instructions": {},
    }
    _write_json(root / "domain.json", pack)
    delivery = {
        name: {
            "tool": reference.to_mapping(),
            "mode": "release",
            "distribution": DISTRIBUTION,
            "entry_point": f"{ENTRY_POINT_GROUP}:csv-statistics.{name}",
            "wheel_path": str(Path("artifacts") / destination_wheel.name),
            "development_callable": None,
        }
        for name, reference in references.items()
    }
    _write_json(root / "release-deliveries.json", delivery)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", required=True, type=Path)
    args = parser.parse_args()
    generate(Path(__file__).resolve().parent, args.wheel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
