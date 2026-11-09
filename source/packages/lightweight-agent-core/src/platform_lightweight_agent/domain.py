"""Declarative domain-pack authoring for the lightweight V6 runtime.

This module deliberately has two boundaries:

* ``load_domain_pack()`` and ``compile_domain_pack()`` are authoring
  operations.  They lazily use the already optional ``jsonschema`` package to
  validate an untrusted source declaration and never import tool code, invoke
  a model, install a distribution, or create a runtime.
* ``DomainLock`` is a small standard-library-only runtime record.  A compiled
  project can therefore run and resume with the ordinary V6 runtime extras;
  it does not need an authoring environment in production.

The compiler only emits the existing ``v6-agent-project-v1`` format.  It is
not another workflow engine, plug-in loader, credential store, or recovery
system.  Exact tool identities remain subject to ``PortableTarget`` and the
host-owned executor at runtime.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .portable import _normalize_tools, _require_declared_tools
from .portability import PROJECT_SCHEMA_V1, PortableProject, check_project
from .spec import (
    JsonValue,
    StepKind,
    ToolReference,
    ValueBinding,
    WorkflowSpec,
    WorkflowValidationError,
    canonical_json,
    contains_secret_name,
    digest,
    json_copy,
)

DOMAIN_PACK_SCHEMA_V1 = "v6-domain-pack-v1"
DOMAIN_LOCK_SCHEMA_V1 = "v6-domain-lock-v1"
DOMAIN_COMPILATION_SCHEMA_V1 = "v6-domain-compilation-v1"
DOMAIN_COMPILER_SCHEMA_V1 = "v6-domain-compiler-v1"
CONTEXT_SELECTION_POLICY_SCHEMA_V1 = "v6-context-selection-policy-v1"
CONTEXT_SELECTION_POLICY_SCHEMA_V2 = "v6-context-selection-policy-v2"
VALIDATION_REPORT_SCHEMA_V1 = "v6-validation-report-v1"

_DIALECT = "https://json-schema.org/draft/2020-12/schema"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_ERROR = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_MAX_ASSET_BYTES = 16 * 1024 * 1024
_MAX_TOTAL_ASSET_BYTES = 64 * 1024 * 1024
_MAX_DOMAIN_JSON_BYTES = 1 * 1024 * 1024
_MAX_DOMAIN_CONTRACT_BYTES = 256 * 1024
_MAX_DOMAIN_LOCK_BYTES = 256 * 1024
_MAX_INSTRUCTION_BYTES = 64 * 1024
_PARAMETER_PROFILE_SENTINEL = {"$domain_parameter_profile": True}
_DOMAIN_CONTEXT_SENTINEL = {"$domain_context": True}
_RESERVED_PROJECT_ROOTS = frozenset(
    {
        "domain-lock.json",
        "eval",
        "instructions",
        "pylock.toml",
        "pyproject.toml",
        "subworkflows",
        "tools.json",
        "workflow.json",
    }
)


# The schema intentionally describes only declarative source metadata.  Tool
# implementations, registries, credentials, endpoints and Python import
# paths have no place in this document.
_DOMAIN_PACK_JSON_SCHEMA: dict[str, Any] = {
    "$schema": _DIALECT,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "domain_id",
        "version",
        "entry_workflows",
        "subworkflows",
        "tool_catalog",
        "dependency_lock",
        "tools",
        "requested_permissions",
        "description",
        "checks",
        "parameter_profiles",
        "resources",
        "tests",
        "evaluation_cases",
        "context_policy",
        "driver_capabilities",
        "agent_instructions",
    ],
    "properties": {
        "schema_version": {"const": DOMAIN_PACK_SCHEMA_V1},
        "domain_id": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
        "version": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
        "entry_workflows": {
            "type": "array",
            "minItems": 1,
            "maxItems": 32,
            "items": {"$ref": "#/$defs/workflow_entry"},
        },
        "subworkflows": {
            "type": "array",
            "maxItems": 64,
            "items": {"$ref": "#/$defs/subworkflow"},
        },
        "tool_catalog": {"type": "string", "minLength": 1, "maxLength": 512},
        "dependency_lock": {"type": "string", "minLength": 1, "maxLength": 512},
        "tools": {
            "type": "array",
            "maxItems": 128,
            "items": {"$ref": "#/$defs/tool"},
        },
        "requested_permissions": {
            "type": "array",
            "maxItems": 128,
            "items": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
        },
        "description": {"$ref": "#/$defs/description"},
        "checks": {
            "type": "array",
            "maxItems": 128,
            "items": {"$ref": "#/$defs/check"},
        },
        "parameter_profiles": {
            "type": "array",
            "maxItems": 64,
            "items": {"$ref": "#/$defs/profile"},
        },
        "resources": {
            "type": "array",
            "maxItems": 256,
            "items": {"$ref": "#/$defs/asset"},
        },
        "tests": {
            "type": "array",
            "maxItems": 256,
            "items": {"$ref": "#/$defs/asset"},
        },
        "evaluation_cases": {"type": ["string", "null"], "maxLength": 512},
        "context_policy": {"$ref": "#/$defs/context_policy"},
        "driver_capabilities": {
            "type": "array",
            "maxItems": 32,
            "items": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
        },
        "agent_instructions": {
            "type": "object",
            "additionalProperties": {"type": "string", "minLength": 1, "maxLength": 512},
            "maxProperties": 128,
        },
    },
    "$defs": {
        "tool": {
            "type": "object",
            "additionalProperties": False,
            "required": ["tool_id", "version", "package_digest"],
            "properties": {
                "tool_id": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
                "version": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
                "package_digest": {"type": "string", "pattern": r"^sha256:[0-9a-f]{64}$"},
            },
        },
        "check_binding": {
            "type": "object",
            "additionalProperties": False,
            "required": ["rule_ref", "step_id", "condition_step_id", "protected_steps"],
            "properties": {
                "rule_ref": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
                "step_id": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
                "condition_step_id": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
                "protected_steps": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 128,
                    "uniqueItems": True,
                    "items": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
                },
            },
        },
        "workflow_entry": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "workflow", "workflow_id", "version", "definition_digest", "required_checks"],
            "properties": {
                "name": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
                "workflow": {"type": "string", "minLength": 1, "maxLength": 512},
                "workflow_id": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
                "version": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
                "definition_digest": {"type": "string", "pattern": r"^sha256:[0-9a-f]{64}$"},
                "required_checks": {"type": "array", "maxItems": 64, "items": {"$ref": "#/$defs/check_binding"}},
            },
        },
        "subworkflow": {
            "type": "object",
            "additionalProperties": False,
            "required": ["workflow", "workflow_id", "version", "definition_digest"],
            "properties": {
                "workflow": {"type": "string", "minLength": 1, "maxLength": 512},
                "workflow_id": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
                "version": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
                "definition_digest": {"type": "string", "pattern": r"^sha256:[0-9a-f]{64}$"},
            },
        },
        "description": {
            "type": "object",
            "additionalProperties": False,
            "required": ["terms", "applicability", "limitations"],
            "properties": {
                "terms": {"type": "object", "additionalProperties": {"type": "string", "minLength": 1, "maxLength": 4096}, "maxProperties": 128},
                "applicability": {"type": "array", "maxItems": 128, "items": {"type": "string", "minLength": 1, "maxLength": 4096}},
                "limitations": {"type": "array", "maxItems": 128, "items": {"type": "string", "minLength": 1, "maxLength": 4096}},
            },
        },
        "check": {
            "type": "object",
            "additionalProperties": False,
            "required": ["rule_ref", "required", "description", "input_paths", "argument_paths"],
            "properties": {
                "rule_ref": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
                "required": {"type": "boolean"},
                "description": {"type": "string", "minLength": 1, "maxLength": 4096},
                "input_paths": {"type": "array", "maxItems": 128, "items": {"type": "string", "maxLength": 1024}},
                "argument_paths": {"type": "array", "maxItems": 128, "items": {"type": "string", "maxLength": 1024}},
            },
        },
        "profile": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "defaults"],
            "properties": {
                "name": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
                "defaults": {"type": "object", "additionalProperties": True, "maxProperties": 256},
            },
        },
        "asset": {
            "type": "object",
            "additionalProperties": False,
            "required": ["path", "sha256", "role"],
            "properties": {
                "path": {"type": "string", "minLength": 1, "maxLength": 512},
                "sha256": {"type": "string", "pattern": r"^sha256:[0-9a-f]{64}$"},
                "role": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"},
            },
        },
        "context_policy": {
            "oneOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["schema_version", "strategy", "resources"],
                    "properties": {
                        "schema_version": {"const": CONTEXT_SELECTION_POLICY_SCHEMA_V1},
                        "strategy": {"const": "declared-step-inputs-v1"},
                        "resources": {"type": "array", "maxItems": 256, "items": {"type": "string", "minLength": 1, "maxLength": 512}},
                    },
                },
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "schema_version",
                        "strategy",
                        "resources",
                        "agent_steps",
                        "max_bytes",
                    ],
                    "properties": {
                        "schema_version": {"const": CONTEXT_SELECTION_POLICY_SCHEMA_V2},
                        "strategy": {"const": "declared-agent-resources-v1"},
                        "resources": {"type": "array", "maxItems": 256, "items": {"type": "string", "minLength": 1, "maxLength": 512}},
                        "agent_steps": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 128,
                            "uniqueItems": True,
                            "items": {"type": "string", "minLength": 1, "maxLength": 512},
                        },
                        "max_bytes": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 262144,
                        },
                    },
                },
            ]
        },
    },
}


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or _NAME.fullmatch(value) is None:
        raise WorkflowValidationError(f"{label} must be a portable identifier")
    if value.lower() == "latest":
        raise WorkflowValidationError(f"{label} must be fixed; latest is not portable")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise WorkflowValidationError(f"{label} must be a sha256 digest")
    return value


def _text(value: object, label: str, maximum: int = 4_096) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.encode("utf-8")) > maximum
    ):
        raise WorkflowValidationError(f"{label} must be a bounded non-empty string")
    return value


def _pointer(value: object, label: str) -> str:
    # ValueBinding already owns V6 JSON Pointer validation.  Reuse it instead
    # of creating a second pointer parser for domain metadata.
    return ValueBinding("workflow_input", pointer=value).pointer


def _json_object(value: object, label: str) -> dict[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise WorkflowValidationError(f"{label} must be an object")
    copied = json_copy(dict(value))
    if not isinstance(copied, dict):
        raise WorkflowValidationError(f"{label} must be an object")
    if contains_secret_name(copied):
        raise WorkflowValidationError(f"{label} must not contain credentials")
    return copied


def _sha256(path: Path) -> str:
    digest_value = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest_value.update(block)
    return "sha256:" + digest_value.hexdigest()


def _load_json(path: Path, label: str, *, maximum: int = _MAX_DOMAIN_JSON_BYTES) -> Any:
    try:
        if path.stat().st_size > maximum:
            raise WorkflowValidationError(f"{label} exceeds its size limit")
        raw = path.read_text(encoding="utf-8")
        return json.loads(raw)
    except WorkflowValidationError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkflowValidationError(f"{label} is not valid JSON") from error


def _source_file(root: Path, raw: object, label: str) -> tuple[Path, str]:
    """Resolve one source-pack file without accepting traversal or symlinks."""

    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise WorkflowValidationError(f"{label} path is invalid")
    relative = Path(raw)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise WorkflowValidationError(f"{label} path must be a relative file")
    candidate = root.joinpath(relative)
    # A pack is a signed-by-digest authoring input; accepting symlinks would
    # make a checked declaration silently reach outside that input boundary.
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise WorkflowValidationError(f"{label} path must not contain a symlink")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise WorkflowValidationError(f"{label} file is unavailable") from error
    if not resolved.is_file():
        raise WorkflowValidationError(f"{label} path must name a file")
    return resolved, relative.as_posix()


def _asset_path(root: Path, asset: "DomainAsset", label: str) -> Path:
    path, relative = _source_file(root, asset.path, label)
    if relative.split("/", 1)[0] in _RESERVED_PROJECT_ROOTS:
        raise WorkflowValidationError(f"{label} path conflicts with compiled project metadata")
    if path.stat().st_size > _MAX_ASSET_BYTES:
        raise WorkflowValidationError(f"{label} exceeds the per-file size limit")
    if _sha256(path) != asset.sha256:
        raise WorkflowValidationError(f"{label} digest does not match")
    return path


@dataclass(frozen=True)
class ValidationIssueV1:
    """One credential-free, actionable result from a domain check."""

    path: str
    code: str
    message: str
    remediation: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _pointer(self.path, "validation issue path"))
        if not isinstance(self.code, str) or _ERROR.fullmatch(self.code) is None:
            raise WorkflowValidationError("validation issue code is invalid")
        object.__setattr__(self, "message", _text(self.message, "validation issue message"))
        if self.remediation is not None:
            object.__setattr__(
                self,
                "remediation",
                _text(self.remediation, "validation issue remediation"),
            )

    @classmethod
    def from_mapping(cls, value: object) -> "ValidationIssueV1":
        if not isinstance(value, Mapping) or set(value) - {
            "path",
            "code",
            "message",
            "remediation",
        } or not {"path", "code", "message"} <= set(value):
            raise WorkflowValidationError("validation issue is malformed")
        return cls(
            value["path"],  # type: ignore[arg-type]
            value["code"],  # type: ignore[arg-type]
            value["message"],  # type: ignore[arg-type]
            value.get("remediation"),  # type: ignore[arg-type]
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "path": self.path,
            "code": self.code,
            "message": self.message,
        }
        if self.remediation is not None:
            result["remediation"] = self.remediation
        return result


@dataclass(frozen=True)
class ValidationReportV1:
    """Portable proof that a domain check evaluated these exact inputs.

    A report is an ordinary tool result, not a new workflow-state machine.  A
    calculation capability can call :meth:`matches` before relying on it, and
    a v3 condition can gate on ``/status == "PASS"``.
    """

    status: str
    rule_ref: str
    input_digest: str
    arguments_digest: str
    issues: tuple[ValidationIssueV1, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {"PASS", "FAIL", "UNKNOWN"}:
            raise WorkflowValidationError("validation report status is invalid")
        object.__setattr__(self, "rule_ref", _identifier(self.rule_ref, "rule_ref"))
        object.__setattr__(self, "input_digest", _digest(self.input_digest, "input_digest"))
        object.__setattr__(
            self,
            "arguments_digest",
            _digest(self.arguments_digest, "arguments_digest"),
        )
        issues = tuple(self.issues)
        if any(not isinstance(item, ValidationIssueV1) for item in issues) or len(issues) > 128:
            raise WorkflowValidationError("validation report issues are invalid")
        if self.status == "PASS" and issues:
            raise WorkflowValidationError("a passing validation report cannot contain issues")
        if self.status != "PASS" and not issues:
            raise WorkflowValidationError("a non-passing validation report requires an issue")
        object.__setattr__(self, "issues", issues)
        evidence = tuple(_text(item, "validation evidence reference", 1_024) for item in self.evidence_refs)
        if len(evidence) > 128 or len(set(evidence)) != len(evidence):
            raise WorkflowValidationError("validation evidence references are invalid")
        object.__setattr__(self, "evidence_refs", evidence)

    @classmethod
    def from_mapping(cls, value: object) -> "ValidationReportV1":
        raw = _json_object(value, "validation report")
        required = {
            "schema_version",
            "status",
            "rule_ref",
            "input_digest",
            "arguments_digest",
            "issues",
            "evidence_refs",
        }
        if set(raw) != required or raw.get("schema_version") != VALIDATION_REPORT_SCHEMA_V1:
            raise WorkflowValidationError("validation report is malformed")
        if not isinstance(raw["issues"], list) or not isinstance(raw["evidence_refs"], list):
            raise WorkflowValidationError("validation report collections are invalid")
        return cls(
            raw["status"],  # type: ignore[arg-type]
            raw["rule_ref"],  # type: ignore[arg-type]
            raw["input_digest"],  # type: ignore[arg-type]
            raw["arguments_digest"],  # type: ignore[arg-type]
            tuple(ValidationIssueV1.from_mapping(item) for item in raw["issues"]),
            tuple(raw["evidence_refs"]),  # type: ignore[arg-type]
        )

    @classmethod
    def passed(
        cls,
        rule_ref: str,
        inputs: Mapping[str, JsonValue],
        arguments: Mapping[str, JsonValue],
        *,
        evidence_refs: Sequence[str] = (),
    ) -> "ValidationReportV1":
        return cls("PASS", rule_ref, digest(inputs), digest(arguments), (), tuple(evidence_refs))

    @classmethod
    def failed(
        cls,
        status: str,
        rule_ref: str,
        inputs: Mapping[str, JsonValue],
        arguments: Mapping[str, JsonValue],
        issues: Sequence[ValidationIssueV1],
        *,
        evidence_refs: Sequence[str] = (),
    ) -> "ValidationReportV1":
        return cls(status, rule_ref, digest(inputs), digest(arguments), tuple(issues), tuple(evidence_refs))

    @staticmethod
    def schema(rule_ref: str | None = None) -> dict[str, JsonValue]:
        """Return the strict V3 tool-output schema for a validation receipt."""

        rule_schema: dict[str, JsonValue] = (
            {"const": _identifier(rule_ref, "rule_ref")}
            if rule_ref is not None
            else {
                "type": "string",
                "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
            }
        )
        return {
            "$schema": _DIALECT,
            "type": "object",
            "properties": {
                "schema_version": {"const": VALIDATION_REPORT_SCHEMA_V1},
                "status": {"enum": ["PASS", "FAIL", "UNKNOWN"]},
                "rule_ref": rule_schema,
                "input_digest": {"type": "string", "pattern": r"^sha256:[0-9a-f]{64}$"},
                "arguments_digest": {"type": "string", "pattern": r"^sha256:[0-9a-f]{64}$"},
                "issues": {
                    "type": "array",
                    "maxItems": 128,
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "maxLength": 1024},
                            "code": {"type": "string", "pattern": r"^[A-Z][A-Z0-9_]{0,127}$"},
                            "message": {"type": "string", "minLength": 1, "maxLength": 4096},
                            "remediation": {"type": "string", "minLength": 1, "maxLength": 4096},
                        },
                        "required": ["path", "code", "message"],
                        "additionalProperties": False,
                    },
                },
                "evidence_refs": {
                    "type": "array",
                    "maxItems": 128,
                    "items": {"type": "string", "minLength": 1, "maxLength": 1024},
                },
            },
            "required": [
                "schema_version",
                "status",
                "rule_ref",
                "input_digest",
                "arguments_digest",
                "issues",
                "evidence_refs",
            ],
            "additionalProperties": False,
        }

    def matches(
        self,
        *,
        rule_ref: str,
        inputs: Mapping[str, JsonValue],
        arguments: Mapping[str, JsonValue],
    ) -> bool:
        """Whether this is a PASS receipt for exactly these checked values."""

        return (
            self.status == "PASS"
            and self.rule_ref == _identifier(rule_ref, "rule_ref")
            and self.input_digest == digest(inputs)
            and self.arguments_digest == digest(arguments)
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "schema_version": VALIDATION_REPORT_SCHEMA_V1,
            "status": self.status,
            "rule_ref": self.rule_ref,
            "input_digest": self.input_digest,
            "arguments_digest": self.arguments_digest,
            "issues": [item.to_mapping() for item in self.issues],
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class DomainDescription:
    terms: Mapping[str, str]
    applicability: tuple[str, ...]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        normalized_terms = {
            _identifier(key, "domain term"): _text(value, "domain term definition")
            for key, value in self.terms.items()
        }
        if len(normalized_terms) > 128:
            raise WorkflowValidationError("domain terms exceed the limit")
        object.__setattr__(self, "terms", normalized_terms)
        for field_name in ("applicability", "limitations"):
            values = tuple(_text(item, f"domain {field_name}") for item in getattr(self, field_name))
            if len(values) > 128:
                raise WorkflowValidationError(f"domain {field_name} exceed the limit")
            object.__setattr__(self, field_name, values)

    @classmethod
    def from_mapping(cls, value: object) -> "DomainDescription":
        if not isinstance(value, Mapping) or set(value) != {
            "terms",
            "applicability",
            "limitations",
        }:
            raise WorkflowValidationError("domain description is malformed")
        if not isinstance(value["terms"], Mapping) or not all(
            isinstance(key, str) and isinstance(item, str)
            for key, item in value["terms"].items()
        ):
            raise WorkflowValidationError("domain terms are invalid")
        if any(
            not isinstance(value[field_name], list)
            or any(not isinstance(item, str) for item in value[field_name])
            for field_name in ("applicability", "limitations")
        ):
            raise WorkflowValidationError("domain description collections are invalid")
        return cls(
            dict(value["terms"]),
            tuple(value["applicability"]),  # type: ignore[arg-type]
            tuple(value["limitations"]),  # type: ignore[arg-type]
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "terms": dict(sorted(self.terms.items())),
            "applicability": list(self.applicability),
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class DomainCheckRequirement:
    rule_ref: str
    required: bool
    description: str
    input_paths: tuple[str, ...]
    argument_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_ref", _identifier(self.rule_ref, "rule_ref"))
        if not isinstance(self.required, bool):
            raise WorkflowValidationError("domain check required must be boolean")
        object.__setattr__(self, "description", _text(self.description, "domain check description"))
        for field_name in ("input_paths", "argument_paths"):
            values = tuple(_pointer(item, f"domain check {field_name}") for item in getattr(self, field_name))
            if len(values) > 128 or len(set(values)) != len(values):
                raise WorkflowValidationError(f"domain check {field_name} are invalid")
            object.__setattr__(self, field_name, values)

    @classmethod
    def from_mapping(cls, value: object) -> "DomainCheckRequirement":
        if not isinstance(value, Mapping) or set(value) != {
            "rule_ref",
            "required",
            "description",
            "input_paths",
            "argument_paths",
        }:
            raise WorkflowValidationError("domain check is malformed")
        if not isinstance(value["input_paths"], list) or not isinstance(value["argument_paths"], list):
            raise WorkflowValidationError("domain check paths are invalid")
        return cls(
            value["rule_ref"],  # type: ignore[arg-type]
            value["required"],  # type: ignore[arg-type]
            value["description"],  # type: ignore[arg-type]
            tuple(value["input_paths"]),  # type: ignore[arg-type]
            tuple(value["argument_paths"]),  # type: ignore[arg-type]
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "rule_ref": self.rule_ref,
            "required": self.required,
            "description": self.description,
            "input_paths": list(self.input_paths),
            "argument_paths": list(self.argument_paths),
        }


@dataclass(frozen=True)
class DomainCheckBinding:
    rule_ref: str
    step_id: str
    condition_step_id: str
    protected_steps: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_ref", _identifier(self.rule_ref, "rule_ref"))
        object.__setattr__(self, "step_id", _identifier(self.step_id, "check step_id"))
        object.__setattr__(
            self,
            "condition_step_id",
            _identifier(self.condition_step_id, "check condition_step_id"),
        )
        protected = tuple(
            _identifier(item, "protected step_id") for item in self.protected_steps
        )
        if not protected or len(protected) > 128 or len(set(protected)) != len(protected):
            raise WorkflowValidationError(
                "domain check protected_steps must be a non-empty unique list"
            )
        object.__setattr__(self, "protected_steps", protected)

    @classmethod
    def from_mapping(cls, value: object) -> "DomainCheckBinding":
        if not isinstance(value, Mapping) or set(value) != {
            "rule_ref",
            "step_id",
            "condition_step_id",
            "protected_steps",
        } or not isinstance(value["protected_steps"], list):
            raise WorkflowValidationError("domain check binding is malformed")
        return cls(
            value["rule_ref"],  # type: ignore[arg-type]
            value["step_id"],  # type: ignore[arg-type]
            value["condition_step_id"],  # type: ignore[arg-type]
            tuple(value["protected_steps"]),  # type: ignore[arg-type]
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "rule_ref": self.rule_ref,
            "step_id": self.step_id,
            "condition_step_id": self.condition_step_id,
            "protected_steps": list(self.protected_steps),
        }


@dataclass(frozen=True)
class DomainWorkflowDeclaration:
    workflow: str
    workflow_id: str
    version: str
    definition_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workflow", _text(self.workflow, "domain workflow path", 512))
        object.__setattr__(self, "workflow_id", _identifier(self.workflow_id, "workflow_id"))
        object.__setattr__(self, "version", _identifier(self.version, "workflow version"))
        object.__setattr__(self, "definition_digest", _digest(self.definition_digest, "workflow definition_digest"))

    @classmethod
    def from_mapping(cls, value: object) -> "DomainWorkflowDeclaration":
        if not isinstance(value, Mapping) or set(value) != {
            "workflow",
            "workflow_id",
            "version",
            "definition_digest",
        }:
            raise WorkflowValidationError("domain subworkflow declaration is malformed")
        return cls(
            value["workflow"],  # type: ignore[arg-type]
            value["workflow_id"],  # type: ignore[arg-type]
            value["version"],  # type: ignore[arg-type]
            value["definition_digest"],  # type: ignore[arg-type]
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "workflow": self.workflow,
            "workflow_id": self.workflow_id,
            "version": self.version,
            "definition_digest": self.definition_digest,
        }


@dataclass(frozen=True)
class DomainEntryWorkflow(DomainWorkflowDeclaration):
    name: str = ""
    required_checks: tuple[DomainCheckBinding, ...] = ()

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "name", _identifier(self.name, "domain entry name"))
        bindings = tuple(self.required_checks)
        if any(not isinstance(item, DomainCheckBinding) for item in bindings):
            raise WorkflowValidationError("domain entry checks are invalid")
        if len({item.rule_ref for item in bindings}) != len(bindings):
            raise WorkflowValidationError("domain entry check rules are duplicated")
        object.__setattr__(self, "required_checks", bindings)

    @classmethod
    def from_mapping(cls, value: object) -> "DomainEntryWorkflow":
        if not isinstance(value, Mapping) or set(value) != {
            "name",
            "workflow",
            "workflow_id",
            "version",
            "definition_digest",
            "required_checks",
        } or not isinstance(value["required_checks"], list):
            raise WorkflowValidationError("domain entry workflow is malformed")
        return cls(
            workflow=value["workflow"],  # type: ignore[arg-type]
            workflow_id=value["workflow_id"],  # type: ignore[arg-type]
            version=value["version"],  # type: ignore[arg-type]
            definition_digest=value["definition_digest"],  # type: ignore[arg-type]
            name=value["name"],  # type: ignore[arg-type]
            required_checks=tuple(
                DomainCheckBinding.from_mapping(item)
                for item in value["required_checks"]
            ),
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "name": self.name,
            **super().to_mapping(),
            "required_checks": [item.to_mapping() for item in self.required_checks],
        }


@dataclass(frozen=True)
class DomainParameterProfile:
    name: str
    defaults: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _identifier(self.name, "parameter profile name"))
        copied = _json_object(self.defaults, "parameter profile defaults")
        if len(canonical_json(copied).encode("utf-8")) > 64 * 1024:
            raise WorkflowValidationError("parameter profile defaults exceed 64 KiB")
        object.__setattr__(self, "defaults", copied)

    @classmethod
    def from_mapping(cls, value: object) -> "DomainParameterProfile":
        if not isinstance(value, Mapping) or set(value) != {"name", "defaults"}:
            raise WorkflowValidationError("parameter profile is malformed")
        return cls(value["name"], value["defaults"])  # type: ignore[arg-type]

    def to_mapping(self) -> dict[str, JsonValue]:
        return {"name": self.name, "defaults": json_copy(self.defaults)}


@dataclass(frozen=True)
class DomainAsset:
    path: str
    sha256: str
    role: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _text(self.path, "domain asset path", 512))
        object.__setattr__(self, "sha256", _digest(self.sha256, "domain asset sha256"))
        object.__setattr__(self, "role", _identifier(self.role, "domain asset role"))

    @classmethod
    def from_mapping(cls, value: object) -> "DomainAsset":
        if not isinstance(value, Mapping) or set(value) != {"path", "sha256", "role"}:
            raise WorkflowValidationError("domain asset is malformed")
        return cls(
            value["path"],  # type: ignore[arg-type]
            value["sha256"],  # type: ignore[arg-type]
            value["role"],  # type: ignore[arg-type]
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        return {"path": self.path, "sha256": self.sha256, "role": self.role}


@dataclass(frozen=True)
class ContextSelectionPolicy:
    """Frozen, deterministic context selection contract for one domain run."""

    resources: tuple[str, ...] = ()
    strategy: str = "declared-step-inputs-v1"
    agent_steps: tuple[str, ...] = ()
    max_bytes: int | None = None

    def __post_init__(self) -> None:
        if self.strategy not in {
            "declared-step-inputs-v1",
            "declared-agent-resources-v1",
        }:
            raise WorkflowValidationError("domain context strategy is unsupported")
        resources = tuple(_text(item, "domain context resource", 512) for item in self.resources)
        if len(resources) > 256 or len(set(resources)) != len(resources):
            raise WorkflowValidationError("domain context resources are invalid")
        object.__setattr__(self, "resources", resources)
        steps = tuple(
            _text(item, "domain context Agent address", 512)
            for item in self.agent_steps
        )
        if len(steps) > 128 or len(set(steps)) != len(steps):
            raise WorkflowValidationError("domain context Agent steps are invalid")
        object.__setattr__(self, "agent_steps", steps)
        if self.strategy == "declared-step-inputs-v1":
            if steps or self.max_bytes is not None:
                raise WorkflowValidationError(
                    "v1 domain context policy cannot inject Agent resources"
                )
            return
        if not resources or not steps:
            raise WorkflowValidationError(
                "Agent resource context requires resources and Agent steps"
            )
        if (
            not isinstance(self.max_bytes, int)
            or isinstance(self.max_bytes, bool)
            or not 1 <= self.max_bytes <= 256 * 1024
        ):
            raise WorkflowValidationError(
                "domain context max_bytes must be between 1 and 262144"
            )

    @classmethod
    def from_mapping(cls, value: object) -> "ContextSelectionPolicy":
        if not isinstance(value, Mapping):
            raise WorkflowValidationError("domain context policy is malformed")
        schema_version = value.get("schema_version")
        if schema_version == CONTEXT_SELECTION_POLICY_SCHEMA_V1:
            if set(value) != {"schema_version", "strategy", "resources"}:
                raise WorkflowValidationError("domain context policy is malformed")
            steps: tuple[str, ...] = ()
            maximum = None
        elif schema_version == CONTEXT_SELECTION_POLICY_SCHEMA_V2:
            if set(value) != {
                "schema_version",
                "strategy",
                "resources",
                "agent_steps",
                "max_bytes",
            } or not isinstance(value.get("agent_steps"), list):
                raise WorkflowValidationError("domain context policy is malformed")
            steps = tuple(value["agent_steps"])  # type: ignore[arg-type]
            maximum = value["max_bytes"]
        else:
            raise WorkflowValidationError("domain context policy is malformed")
        if not isinstance(value.get("resources"), list):
            raise WorkflowValidationError("domain context resources are invalid")
        return cls(
            tuple(value["resources"]),  # type: ignore[arg-type]
            value["strategy"],  # type: ignore[arg-type]
            steps,
            maximum,  # type: ignore[arg-type]
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "schema_version": (
                CONTEXT_SELECTION_POLICY_SCHEMA_V2
                if self.strategy == "declared-agent-resources-v1"
                else CONTEXT_SELECTION_POLICY_SCHEMA_V1
            ),
            "strategy": self.strategy,
            "resources": list(self.resources),
        }
        if self.strategy == "declared-agent-resources-v1":
            assert self.max_bytes is not None
            result["agent_steps"] = list(self.agent_steps)
            result["max_bytes"] = self.max_bytes
        return result


@dataclass(frozen=True)
class DomainPack:
    """A credential-free, source-only domain declaration."""

    domain_id: str
    version: str
    entry_workflows: tuple[DomainEntryWorkflow, ...]
    subworkflows: tuple[DomainWorkflowDeclaration, ...]
    tool_catalog: str
    dependency_lock: str
    tools: tuple[ToolReference, ...]
    requested_permissions: frozenset[str]
    description: DomainDescription
    checks: tuple[DomainCheckRequirement, ...]
    parameter_profiles: tuple[DomainParameterProfile, ...]
    resources: tuple[DomainAsset, ...]
    tests: tuple[DomainAsset, ...]
    evaluation_cases: str | None
    context_policy: ContextSelectionPolicy
    driver_capabilities: frozenset[str]
    agent_instructions: Mapping[str, str]
    source_root: Path = field(compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain_id", _identifier(self.domain_id, "domain_id"))
        object.__setattr__(self, "version", _identifier(self.version, "domain version"))
        entries = tuple(self.entry_workflows)
        if not entries or len(entries) > 32 or any(
            not isinstance(item, DomainEntryWorkflow) for item in entries
        ):
            raise WorkflowValidationError("domain entry workflows are invalid")
        if len({item.name for item in entries}) != len(entries):
            raise WorkflowValidationError("domain entry workflow names are duplicated")
        if len({(item.workflow_id, item.version) for item in entries}) != len(entries):
            raise WorkflowValidationError("domain entry workflow identities are duplicated")
        object.__setattr__(self, "entry_workflows", entries)
        children = tuple(self.subworkflows)
        if len(children) > 64 or any(
            not isinstance(item, DomainWorkflowDeclaration) for item in children
        ):
            raise WorkflowValidationError("domain subworkflows are invalid")
        if len({(item.workflow_id, item.version) for item in children}) != len(children):
            raise WorkflowValidationError("domain subworkflow identities are duplicated")
        object.__setattr__(self, "subworkflows", children)
        object.__setattr__(self, "tool_catalog", _text(self.tool_catalog, "tool_catalog", 512))
        object.__setattr__(self, "dependency_lock", _text(self.dependency_lock, "dependency_lock", 512))
        tools = tuple(self.tools)
        if len(tools) > 128 or any(not isinstance(item, ToolReference) for item in tools):
            raise WorkflowValidationError("domain tools are invalid")
        if len({(item.tool_id, item.version, item.package_digest) for item in tools}) != len(tools):
            raise WorkflowValidationError("domain tools are duplicated")
        object.__setattr__(self, "tools", tools)
        permissions = frozenset(_identifier(item, "domain requested permission") for item in self.requested_permissions)
        object.__setattr__(self, "requested_permissions", permissions)
        if not isinstance(self.description, DomainDescription):
            raise WorkflowValidationError("domain description is invalid")
        checks = tuple(self.checks)
        if len(checks) > 128 or any(not isinstance(item, DomainCheckRequirement) for item in checks):
            raise WorkflowValidationError("domain checks are invalid")
        if len({item.rule_ref for item in checks}) != len(checks):
            raise WorkflowValidationError("domain check rules are duplicated")
        object.__setattr__(self, "checks", checks)
        profiles = tuple(self.parameter_profiles)
        if len(profiles) > 64 or any(not isinstance(item, DomainParameterProfile) for item in profiles):
            raise WorkflowValidationError("domain parameter profiles are invalid")
        if len({item.name for item in profiles}) != len(profiles):
            raise WorkflowValidationError("domain parameter profiles are duplicated")
        object.__setattr__(self, "parameter_profiles", profiles)
        for field_name in ("resources", "tests"):
            assets = tuple(getattr(self, field_name))
            if len(assets) > 256 or any(not isinstance(item, DomainAsset) for item in assets):
                raise WorkflowValidationError(f"domain {field_name} are invalid")
            if len({item.path for item in assets}) != len(assets):
                raise WorkflowValidationError(f"domain {field_name} paths are duplicated")
            object.__setattr__(self, field_name, assets)
        all_assets = (*self.resources, *self.tests)
        if len({item.path for item in all_assets}) != len(all_assets):
            raise WorkflowValidationError("domain resource and test paths must not overlap")
        if self.evaluation_cases is not None:
            object.__setattr__(self, "evaluation_cases", _text(self.evaluation_cases, "evaluation_cases", 512))
        if not isinstance(self.context_policy, ContextSelectionPolicy):
            raise WorkflowValidationError("domain context policy is invalid")
        if not set(self.context_policy.resources) <= {item.path for item in self.resources}:
            raise WorkflowValidationError("domain context policy names an undeclared resource")
        capabilities = frozenset(_identifier(item, "domain driver capability") for item in self.driver_capabilities)
        object.__setattr__(self, "driver_capabilities", capabilities)
        instructions = {
            _text(key, "domain Agent address", 512): _text(value, "domain Agent instruction path", 512)
            for key, value in self.agent_instructions.items()
        }
        if len(instructions) > 128 or len(instructions) != len(self.agent_instructions):
            raise WorkflowValidationError("domain Agent instructions are invalid")
        object.__setattr__(self, "agent_instructions", instructions)
        root = Path(self.source_root)
        if root.is_symlink() or not root.is_dir():
            raise WorkflowValidationError("domain pack root must be a real directory")
        object.__setattr__(self, "source_root", root.resolve())
        if len(canonical_json(self.to_mapping()).encode("utf-8")) > _MAX_DOMAIN_CONTRACT_BYTES:
            raise WorkflowValidationError("domain pack contract exceeds 256 KiB")

    @property
    def definition_digest(self) -> str:
        return digest(self.to_mapping())

    @classmethod
    def from_mapping(cls, value: object, *, source_root: Path) -> "DomainPack":
        raw = _json_object(value, "domain pack")
        if raw.get("schema_version") != DOMAIN_PACK_SCHEMA_V1:
            raise WorkflowValidationError("domain pack schema is unsupported")
        lists = (
            "entry_workflows",
            "subworkflows",
            "tools",
            "requested_permissions",
            "checks",
            "parameter_profiles",
            "resources",
            "tests",
            "driver_capabilities",
        )
        if any(not isinstance(raw.get(field_name), list) for field_name in lists):
            raise WorkflowValidationError("domain pack list field is invalid")
        if not isinstance(raw.get("agent_instructions"), Mapping) or any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in raw["agent_instructions"].items()
        ):
            raise WorkflowValidationError("domain Agent instructions are invalid")
        return cls(
            domain_id=raw["domain_id"],  # type: ignore[arg-type]
            version=raw["version"],  # type: ignore[arg-type]
            entry_workflows=tuple(
                DomainEntryWorkflow.from_mapping(item) for item in raw["entry_workflows"]
            ),
            subworkflows=tuple(
                DomainWorkflowDeclaration.from_mapping(item) for item in raw["subworkflows"]
            ),
            tool_catalog=raw["tool_catalog"],  # type: ignore[arg-type]
            dependency_lock=raw["dependency_lock"],  # type: ignore[arg-type]
            tools=tuple(ToolReference.from_mapping(item) for item in raw["tools"]),
            requested_permissions=frozenset(raw["requested_permissions"]),  # type: ignore[arg-type]
            description=DomainDescription.from_mapping(raw["description"]),
            checks=tuple(DomainCheckRequirement.from_mapping(item) for item in raw["checks"]),
            parameter_profiles=tuple(DomainParameterProfile.from_mapping(item) for item in raw["parameter_profiles"]),
            resources=tuple(DomainAsset.from_mapping(item) for item in raw["resources"]),
            tests=tuple(DomainAsset.from_mapping(item) for item in raw["tests"]),
            evaluation_cases=raw["evaluation_cases"],  # type: ignore[arg-type]
            context_policy=ContextSelectionPolicy.from_mapping(raw["context_policy"]),
            driver_capabilities=frozenset(raw["driver_capabilities"]),  # type: ignore[arg-type]
            agent_instructions=dict(raw["agent_instructions"]),  # type: ignore[arg-type]
            source_root=source_root,
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "schema_version": DOMAIN_PACK_SCHEMA_V1,
            "domain_id": self.domain_id,
            "version": self.version,
            "entry_workflows": [item.to_mapping() for item in self.entry_workflows],
            "subworkflows": [item.to_mapping() for item in self.subworkflows],
            "tool_catalog": self.tool_catalog,
            "dependency_lock": self.dependency_lock,
            "tools": [item.to_mapping() for item in self.tools],
            "requested_permissions": sorted(self.requested_permissions),
            "description": self.description.to_mapping(),
            "checks": [item.to_mapping() for item in self.checks],
            "parameter_profiles": [item.to_mapping() for item in self.parameter_profiles],
            "resources": [item.to_mapping() for item in self.resources],
            "tests": [item.to_mapping() for item in self.tests],
            "evaluation_cases": self.evaluation_cases,
            "context_policy": self.context_policy.to_mapping(),
            "driver_capabilities": sorted(self.driver_capabilities),
            "agent_instructions": dict(sorted(self.agent_instructions.items())),
        }


@dataclass(frozen=True)
class DomainLock:
    """Immutable run-time identity emitted by the domain compiler."""

    domain_id: str
    domain_version: str
    domain_definition_digest: str
    entry: DomainEntryWorkflow
    capability_tools: tuple[ToolReference, ...]
    requested_permissions: frozenset[str]
    resources: tuple[DomainAsset, ...]
    tests: tuple[DomainAsset, ...]
    checks: tuple[DomainCheckRequirement, ...]
    parameter_profile: DomainParameterProfile | None
    context_policy: ContextSelectionPolicy
    driver_capabilities: frozenset[str]
    compiler: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain_id", _identifier(self.domain_id, "domain_id"))
        object.__setattr__(self, "domain_version", _identifier(self.domain_version, "domain version"))
        object.__setattr__(self, "domain_definition_digest", _digest(self.domain_definition_digest, "domain definition digest"))
        if not isinstance(self.entry, DomainEntryWorkflow):
            raise WorkflowValidationError("domain lock entry is invalid")
        tools = tuple(self.capability_tools)
        if any(not isinstance(item, ToolReference) for item in tools):
            raise WorkflowValidationError("domain lock tools are invalid")
        if len({(item.tool_id, item.version, item.package_digest) for item in tools}) != len(tools):
            raise WorkflowValidationError("domain lock tools are duplicated")
        object.__setattr__(self, "capability_tools", tools)
        object.__setattr__(
            self,
            "requested_permissions",
            frozenset(_identifier(item, "domain requested permission") for item in self.requested_permissions),
        )
        for field_name in ("resources", "tests"):
            values = tuple(getattr(self, field_name))
            if any(not isinstance(item, DomainAsset) for item in values):
                raise WorkflowValidationError(f"domain lock {field_name} are invalid")
            object.__setattr__(self, field_name, values)
        checks = tuple(self.checks)
        if any(not isinstance(item, DomainCheckRequirement) for item in checks):
            raise WorkflowValidationError("domain lock checks are invalid")
        object.__setattr__(self, "checks", checks)
        if self.parameter_profile is not None and not isinstance(
            self.parameter_profile, DomainParameterProfile
        ):
            raise WorkflowValidationError("domain lock parameter profile is invalid")
        if not isinstance(self.context_policy, ContextSelectionPolicy):
            raise WorkflowValidationError("domain lock context policy is invalid")
        object.__setattr__(
            self,
            "driver_capabilities",
            frozenset(_identifier(item, "domain driver capability") for item in self.driver_capabilities),
        )
        compiler = _json_object(self.compiler, "domain compiler identity")
        if compiler != {
            "schema_version": DOMAIN_COMPILER_SCHEMA_V1,
            "implementation": "platform-lightweight-agent-core",
        }:
            raise WorkflowValidationError("domain compiler identity is unsupported")
        object.__setattr__(self, "compiler", compiler)
        if len(canonical_json(self.to_mapping()).encode("utf-8")) > _MAX_DOMAIN_LOCK_BYTES:
            raise WorkflowValidationError("domain lock exceeds 256 KiB")

    @classmethod
    def from_mapping(cls, value: object) -> "DomainLock":
        raw = _json_object(value, "domain lock")
        required = {
            "schema_version",
            "compiler",
            "domain",
            "entry",
            "capability_tools",
            "requested_permissions",
            "resources",
            "tests",
            "checks",
            "parameter_profile",
            "context_policy",
            "driver_capabilities",
        }
        if set(raw) != required or raw.get("schema_version") != DOMAIN_LOCK_SCHEMA_V1:
            raise WorkflowValidationError("domain lock is malformed")
        domain = raw["domain"]
        if not isinstance(domain, Mapping) or set(domain) != {
            "domain_id",
            "version",
            "definition_digest",
        }:
            raise WorkflowValidationError("domain lock identity is malformed")
        collections = (
            "capability_tools",
            "requested_permissions",
            "resources",
            "tests",
            "checks",
            "driver_capabilities",
        )
        if any(not isinstance(raw[field_name], list) for field_name in collections):
            raise WorkflowValidationError("domain lock collection is invalid")
        entry = raw["entry"]
        if not isinstance(entry, Mapping):
            raise WorkflowValidationError("domain lock entry is malformed")
        return cls(
            domain_id=domain["domain_id"],  # type: ignore[arg-type]
            domain_version=domain["version"],  # type: ignore[arg-type]
            domain_definition_digest=domain["definition_digest"],  # type: ignore[arg-type]
            entry=DomainEntryWorkflow.from_mapping(entry),
            capability_tools=tuple(ToolReference.from_mapping(item) for item in raw["capability_tools"]),
            requested_permissions=frozenset(raw["requested_permissions"]),  # type: ignore[arg-type]
            resources=tuple(DomainAsset.from_mapping(item) for item in raw["resources"]),
            tests=tuple(DomainAsset.from_mapping(item) for item in raw["tests"]),
            checks=tuple(DomainCheckRequirement.from_mapping(item) for item in raw["checks"]),
            parameter_profile=(
                DomainParameterProfile.from_mapping(raw["parameter_profile"])
                if raw["parameter_profile"] is not None
                else None
            ),
            context_policy=ContextSelectionPolicy.from_mapping(raw["context_policy"]),
            driver_capabilities=frozenset(raw["driver_capabilities"]),  # type: ignore[arg-type]
            compiler=raw["compiler"],  # type: ignore[arg-type]
        )

    @classmethod
    def from_pack(
        cls,
        pack: DomainPack,
        entry: DomainEntryWorkflow,
        parameter_profile: DomainParameterProfile | None = None,
    ) -> "DomainLock":
        return cls(
            domain_id=pack.domain_id,
            domain_version=pack.version,
            domain_definition_digest=pack.definition_digest,
            entry=entry,
            capability_tools=pack.tools,
            requested_permissions=pack.requested_permissions,
            resources=pack.resources,
            tests=pack.tests,
            checks=pack.checks,
            parameter_profile=parameter_profile,
            context_policy=pack.context_policy,
            driver_capabilities=pack.driver_capabilities,
            compiler={
                "schema_version": DOMAIN_COMPILER_SCHEMA_V1,
                "implementation": "platform-lightweight-agent-core",
            },
        )

    @property
    def definition_digest(self) -> str:
        return digest(self.to_mapping())

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "schema_version": DOMAIN_LOCK_SCHEMA_V1,
            "compiler": json_copy(self.compiler),
            "domain": {
                "domain_id": self.domain_id,
                "version": self.domain_version,
                "definition_digest": self.domain_definition_digest,
            },
            "entry": self.entry.to_mapping(),
            "capability_tools": [item.to_mapping() for item in self.capability_tools],
            "requested_permissions": sorted(self.requested_permissions),
            "resources": [item.to_mapping() for item in self.resources],
            "tests": [item.to_mapping() for item in self.tests],
            "checks": [item.to_mapping() for item in self.checks],
            "parameter_profile": (
                self.parameter_profile.to_mapping()
                if self.parameter_profile is not None
                else None
            ),
            "context_policy": self.context_policy.to_mapping(),
            "driver_capabilities": sorted(self.driver_capabilities),
        }


@dataclass(frozen=True)
class DomainCompilation:
    """One atomically-published, checked collection of runnable entry projects."""

    pack: DomainPack
    root: Path
    projects: Mapping[str, PortableProject]
    locks: Mapping[str, DomainLock]

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "schema_version": DOMAIN_COMPILATION_SCHEMA_V1,
            "domain": {
                "domain_id": self.pack.domain_id,
                "version": self.pack.version,
                "definition_digest": self.pack.definition_digest,
            },
            "parameter_profile": (
                next(iter(self.locks.values())).parameter_profile.to_mapping()
                if self.locks
                and next(iter(self.locks.values())).parameter_profile is not None
                else None
            ),
            "entries": {
                name: {
                    "project": str(project.root.relative_to(self.root)),
                    "domain_lock_digest": lock.definition_digest,
                }
                for name, project in sorted(self.projects.items())
                for lock in (self.locks[name],)
            },
        }


def _require_authoring_jsonschema() -> Any:
    try:
        import jsonschema
    except ImportError as error:
        raise WorkflowValidationError(
            "domain pack authoring requires platform-lightweight-agent-core[authoring]"
        ) from error
    return jsonschema


def load_domain_pack(source: str | Path) -> DomainPack:
    """Load and schema-check a source-only ``v6-domain-pack-v1`` declaration."""

    path = Path(source)
    if path.is_file():
        if path.name != "domain.json":
            raise WorkflowValidationError("domain pack source must be a directory or domain.json")
        root = path.parent
    else:
        root = path
        path = root / "domain.json"
    if root.is_symlink() or not root.is_dir():
        raise WorkflowValidationError("domain pack root must be a real directory")
    root = root.resolve()
    declaration, _ = _source_file(root, "domain.json", "domain pack")
    raw = _load_json(declaration, "domain pack")
    if not isinstance(raw, Mapping):
        raise WorkflowValidationError("domain pack must be a JSON object")
    jsonschema = _require_authoring_jsonschema()
    try:
        validator = jsonschema.Draft202012Validator(_DOMAIN_PACK_JSON_SCHEMA)
        errors = sorted(validator.iter_errors(raw), key=lambda item: list(item.path))
    except jsonschema.exceptions.SchemaError as error:
        raise WorkflowValidationError("internal domain pack schema is invalid") from error
    if errors:
        issue = errors[0]
        location = "/" + "/".join(str(item) for item in issue.path)
        raise WorkflowValidationError(
            "domain pack schema is invalid at " + (location if location != "/" else "/")
        )
    return DomainPack.from_mapping(raw, source_root=root)


def _workflow_from_declaration(
    pack: DomainPack, declaration: DomainWorkflowDeclaration, label: str
) -> WorkflowSpec:
    path, _ = _source_file(pack.source_root, declaration.workflow, label)
    workflow_value = WorkflowSpec.from_mapping(_load_json(path, label))
    if (
        workflow_value.workflow_id != declaration.workflow_id
        or workflow_value.version != declaration.version
        or workflow_value.definition_digest != declaration.definition_digest
    ):
        raise WorkflowValidationError(f"{label} does not match its exact declaration")
    return workflow_value


def _walk_steps(steps: Sequence[Any]):
    for step in steps:
        yield step
        for branch in step.children:
            yield from _walk_steps(branch)


def _workflow_tools(workflow: WorkflowSpec) -> set[tuple[str, str, str]]:
    result: set[tuple[str, str, str]] = set()
    for step in _walk_steps(workflow.steps):
        if step.kind is StepKind.TOOL:
            reference = ToolReference.from_mapping(step.config["tool"])
            result.add((reference.tool_id, reference.version, reference.package_digest))
        elif step.kind is StepKind.AGENT:
            for raw in step.config.get("tools", []):
                tool = raw.get("tool", raw) if isinstance(raw, Mapping) else raw
                reference = ToolReference.from_mapping(tool)
                result.add((reference.tool_id, reference.version, reference.package_digest))
    return result


@dataclass(frozen=True)
class _StepLocation:
    step: Any
    sequence_path: tuple[tuple[str, int], ...]
    index: int


def _step_locations(steps: Sequence[Any]) -> dict[str, _StepLocation]:
    result: dict[str, _StepLocation] = {}

    def visit(
        current: Sequence[Any], sequence_path: tuple[tuple[str, int], ...]
    ) -> None:
        for index, step in enumerate(current):
            if step.step_id in result:
                raise WorkflowValidationError(
                    "domain workflow step identifiers must be globally unique"
                )
            result[step.step_id] = _StepLocation(step, sequence_path, index)
            for branch_index, branch in enumerate(step.children):
                visit(branch, (*sequence_path, (step.step_id, branch_index)))

    visit(steps, ())
    return result


def _check_gate(
    workflow: WorkflowSpec,
    binding: DomainCheckBinding,
    requirement: DomainCheckRequirement,
) -> None:
    locations = _step_locations(workflow.steps)
    check_location = locations.get(binding.step_id)
    condition_location = locations.get(binding.condition_step_id)
    check = check_location.step if check_location is not None else None
    condition = condition_location.step if condition_location is not None else None
    if check is None or check.kind is not StepKind.TOOL or check.contract is None:
        raise WorkflowValidationError(
            f"required domain check {binding.rule_ref} has no tool result boundary"
        )
    if canonical_json(check.contract.output_schema) != canonical_json(
        ValidationReportV1.schema(binding.rule_ref)
    ):
        raise WorkflowValidationError(
            f"required domain check {binding.rule_ref} must output a rule-bound ValidationReportV1"
        )
    if condition is None or condition.kind is not StepKind.CONDITION or condition.contract is None:
        raise WorkflowValidationError(
            f"required domain check {binding.rule_ref} has no condition gate"
        )
    assert check_location is not None and condition_location is not None
    if (
        check_location.sequence_path != condition_location.sequence_path
        or check_location.index >= condition_location.index
    ):
        raise WorkflowValidationError(
            f"required domain check {binding.rule_ref} must complete before its gate on the same path"
        )
    if condition.config.get("path") != "/status" or condition.config.get("equals") != "PASS":
        raise WorkflowValidationError(
            f"required domain check {binding.rule_ref} must gate PASS explicitly"
        )
    status_binding = condition.contract.bindings.get("/status")
    if (
        status_binding is None
        or status_binding.source != "step_output"
        or status_binding.step_id != binding.step_id
        or status_binding.pointer != "/status"
    ):
        raise WorkflowValidationError(
            f"required domain check {binding.rule_ref} must bind its status directly"
        )
    if not condition.children[0] or not condition.children[1]:
        raise WorkflowValidationError(
            f"required domain check {binding.rule_ref} requires explicit pass and block branches"
        )
    pass_prefix = (
        *condition_location.sequence_path,
        (binding.condition_step_id, 0),
    )
    check_bindings = check.contract.bindings
    relevant_targets = set(requirement.argument_paths)
    for input_path in requirement.input_paths:
        matches = [
            target
            for target, value in check_bindings.items()
            if value.source == "workflow_input" and value.pointer == input_path
        ]
        if not matches:
            raise WorkflowValidationError(
                f"required domain check {binding.rule_ref} does not bind declared input {input_path}"
            )
        relevant_targets.update(matches)
    missing_arguments = [
        path for path in requirement.argument_paths if path not in check_bindings
    ]
    if missing_arguments:
        raise WorkflowValidationError(
            f"required domain check {binding.rule_ref} does not bind its declared arguments"
        )
    for protected_step_id in binding.protected_steps:
        protected_location = locations.get(protected_step_id)
        if protected_location is None:
            raise WorkflowValidationError(
                f"required domain check {binding.rule_ref} names an unknown protected step"
            )
        if protected_location.sequence_path[: len(pass_prefix)] != pass_prefix:
            raise WorkflowValidationError(
                f"protected step {protected_step_id} is reachable without the PASS gate for {binding.rule_ref}"
            )
        protected = protected_location.step
        if protected.contract is None:
            raise WorkflowValidationError(
                f"protected step {protected_step_id} has no input contract"
            )
        receipt_targets = [
            target
            for target, value in protected.contract.bindings.items()
            if value.source == "step_output"
            and value.step_id == binding.step_id
            and value.pointer == ""
        ]
        if len(receipt_targets) != 1:
            raise WorkflowValidationError(
                f"protected step {protected_step_id} must bind the full validation receipt directly"
            )
        for target in relevant_targets:
            checked_value = check_bindings.get(target)
            if (
                checked_value is None
                or protected.contract.bindings.get(target) != checked_value
            ):
                raise WorkflowValidationError(
                    f"protected step {protected_step_id} must bind the same checked inputs and arguments"
                )


def _checked_domain(pack: DomainPack) -> tuple[
    tuple[tuple[DomainEntryWorkflow, WorkflowSpec], ...],
    tuple[WorkflowSpec, ...],
    tuple[Mapping[str, JsonValue], ...],
]:
    """Validate all source resources needed before any output directory exists."""

    catalog_path, _ = _source_file(pack.source_root, pack.tool_catalog, "domain tool catalog")
    raw_tools = _load_json(catalog_path, "domain tool catalog")
    if not isinstance(raw_tools, list) or any(not isinstance(item, Mapping) for item in raw_tools):
        raise WorkflowValidationError("domain tool catalog must be a JSON list")
    manifests = tuple(_normalize_tools(raw_tools))
    for manifest in manifests:
        if manifest.get("credential_ref") is not None:
            raise WorkflowValidationError(
                "domain tool catalog must not carry credential references"
            )
    declared = {
        (item.tool_id, item.version, item.package_digest) for item in pack.tools
    }
    catalogued = {
        (str(item["tool_id"]), str(item["version"]), str(item["package_digest"]))
        for item in manifests
    }
    if catalogued != declared:
        raise WorkflowValidationError(
            "domain tool catalog must exactly match its declared tool references"
        )
    permissions: set[str] = set()
    for manifest in manifests:
        raw_permissions = manifest.get("permissions", [])
        if not isinstance(raw_permissions, list) or any(not isinstance(item, str) for item in raw_permissions):
            raise WorkflowValidationError("domain tool permissions are invalid")
        permissions.update(raw_permissions)
    if permissions != set(pack.requested_permissions):
        raise WorkflowValidationError(
            "domain requested permissions must exactly match selected tool requirements"
        )
    lock_path, _ = _source_file(pack.source_root, pack.dependency_lock, "domain dependency lock")
    # Existing project parsing owns PEP 751 lock validation.  The compiler only
    # proves it is a bounded, immutable source asset at this boundary.
    if lock_path.stat().st_size > _MAX_ASSET_BYTES:
        raise WorkflowValidationError("domain dependency lock exceeds the size limit")
    entries = tuple(
        (entry, _workflow_from_declaration(pack, entry, f"domain entry {entry.name}"))
        for entry in pack.entry_workflows
    )
    children = tuple(
        _workflow_from_declaration(pack, child, "domain subworkflow")
        for child in pack.subworkflows
    )
    identities = {(item.workflow_id, item.version) for _, item in entries}
    if identities & {(item.workflow_id, item.version) for item in children}:
        raise WorkflowValidationError("domain root and subworkflow identities must not overlap")
    for _entry, current in entries:
        _require_declared_tools(current, manifests)
        if not _workflow_tools(current) <= declared:
            raise WorkflowValidationError("domain workflow uses a tool outside its declared scope")
    for current in children:
        _require_declared_tools(current, manifests)
        if not _workflow_tools(current) <= declared:
            raise WorkflowValidationError("domain subworkflow uses a tool outside its declared scope")
    known_checks = {item.rule_ref: item for item in pack.checks}
    for entry, current in entries:
        required_rules = {item.rule_ref for item in entry.required_checks}
        if not required_rules <= set(known_checks):
            raise WorkflowValidationError("domain entry references an undeclared check rule")
        for binding in entry.required_checks:
            if not known_checks[binding.rule_ref].required:
                raise WorkflowValidationError("only required domain checks may be declared as gates")
            _check_gate(current, binding, known_checks[binding.rule_ref])
    seen_assets: set[str] = set()
    total_asset_bytes = 0
    for label, assets in (("domain resource", pack.resources), ("domain test", pack.tests)):
        for asset in assets:
            path = _asset_path(pack.source_root, asset, label)
            if asset.path in seen_assets:
                raise WorkflowValidationError("domain assets are duplicated")
            seen_assets.add(asset.path)
            total_asset_bytes += path.stat().st_size
    if total_asset_bytes > _MAX_TOTAL_ASSET_BYTES:
        raise WorkflowValidationError("domain assets exceed the total size limit")
    if pack.evaluation_cases is not None:
        cases_path, _ = _source_file(pack.source_root, pack.evaluation_cases, "domain evaluation cases")
        cases = _load_json(cases_path, "domain evaluation cases")
        if not isinstance(cases, list):
            raise WorkflowValidationError("domain evaluation cases must be a JSON list")
    expected_agents = _agent_addresses((*[item for _, item in entries], *children))
    if pack.driver_capabilities and not expected_agents:
        raise WorkflowValidationError(
            "domain driver capabilities require an Agent definition"
        )
    if set(pack.agent_instructions) != set(expected_agents):
        raise WorkflowValidationError(
            "domain Agent instructions must exactly cover every Agent definition"
        )
    for path in pack.agent_instructions.values():
        instruction, _ = _source_file(pack.source_root, path, "domain Agent instruction")
        if instruction.stat().st_size > _MAX_INSTRUCTION_BYTES:
            raise WorkflowValidationError("domain Agent instruction exceeds 64 KiB")
        text = instruction.read_text(encoding="utf-8")
        _text(text, "domain Agent instruction", _MAX_INSTRUCTION_BYTES)
    _materialize_domain_context(
        pack,
        (*[item for _, item in entries], *children),
    )
    return entries, children, manifests


def check_domain_pack(source: str | Path | DomainPack) -> DomainPack:
    """Validate a pack's declarations, pinned files, workflows and gates."""

    pack = source if isinstance(source, DomainPack) else load_domain_pack(source)
    _checked_domain(pack)
    return pack


def _agent_addresses(workflows: Sequence[WorkflowSpec]) -> set[str]:
    result: set[str] = set()
    for workflow in workflows:
        for step in _walk_steps(workflow.steps):
            if step.kind is StepKind.AGENT:
                key = f"{workflow.workflow_id}@{workflow.version}:{step.step_id}"
                if key in result:
                    raise WorkflowValidationError("domain Agent definition is duplicated")
                result.add(key)
    return result


def _copy_asset(source_root: Path, raw_path: str, destination: Path) -> None:
    source, relative = _source_file(source_root, raw_path, "domain source asset")
    output = destination / relative
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, output)


def _domain_context_payload(pack: DomainPack) -> dict[str, JsonValue]:
    """Load a bounded, digest-bearing payload for explicitly scoped Agents."""

    policy = pack.context_policy
    assets = {item.path: item for item in pack.resources}
    resources: list[dict[str, JsonValue]] = []
    for relative in policy.resources:
        asset = assets[relative]
        path = _asset_path(pack.source_root, asset, "domain context resource")
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeError as error:
            raise WorkflowValidationError(
                "domain Agent context resources must be UTF-8 text"
            ) from error
        resources.append(
            {
                "path": asset.path,
                "sha256": asset.sha256,
                "role": asset.role,
                "content": content,
            }
        )
    payload: dict[str, JsonValue] = {
        "schema_version": "v6-domain-agent-context-v1",
        "resources": resources,
    }
    if policy.max_bytes is not None and len(
        canonical_json(payload).encode("utf-8")
    ) > policy.max_bytes:
        raise WorkflowValidationError(
            "domain Agent context exceeds its declared max_bytes"
        )
    return payload


def _is_domain_context_binding(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == {"source", "value"}
        and value.get("source") == "literal"
        and value.get("value") == _DOMAIN_CONTEXT_SENTINEL
    )


def _contains_domain_context_sentinel(value: object) -> bool:
    if isinstance(value, Mapping):
        if value == _DOMAIN_CONTEXT_SENTINEL:
            return True
        return any(_contains_domain_context_sentinel(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_domain_context_sentinel(item) for item in value)
    return False


def _materialize_domain_context(
    pack: DomainPack,
    workflows: Sequence[WorkflowSpec],
) -> tuple[WorkflowSpec, ...]:
    """Freeze selected source resources into only the authorized Agent inputs."""

    policy = pack.context_policy
    selected = set(policy.agent_steps)
    available = _agent_addresses(workflows)
    if selected - available:
        raise WorkflowValidationError(
            "domain context policy names an unknown Agent definition"
        )
    payload = (
        _domain_context_payload(pack)
        if policy.strategy == "declared-agent-resources-v1"
        else None
    )
    observed: set[str] = set()
    result: list[WorkflowSpec] = []
    for current in workflows:
        raw = current.to_mapping()

        def visit(value: JsonValue) -> JsonValue:
            if isinstance(value, dict):
                if value.get("kind") == StepKind.AGENT.value:
                    step_id = value.get("id")
                    if not isinstance(step_id, str):
                        raise WorkflowValidationError(
                            "domain Agent context found a malformed step"
                        )
                    address = f"{current.workflow_id}@{current.version}:{step_id}"
                    contract = value.get("contract")
                    bindings = (
                        contract.get("bindings")
                        if isinstance(contract, Mapping)
                        else None
                    )
                    binding = (
                        bindings.get("/domain_context")
                        if isinstance(bindings, Mapping)
                        else None
                    )
                    if address in selected:
                        if not _is_domain_context_binding(binding):
                            raise WorkflowValidationError(
                                "selected domain Agent must bind /domain_context from the reserved literal"
                            )
                        assert payload is not None
                        observed.add(address)
                        copied = {key: visit(item) for key, item in value.items()}
                        copied_contract = copied["contract"]
                        assert isinstance(copied_contract, dict)
                        copied_bindings = copied_contract["bindings"]
                        assert isinstance(copied_bindings, dict)
                        copied_bindings["/domain_context"] = {
                            "source": "literal",
                            "value": json_copy(payload),
                        }
                        return copied
                return {key: visit(item) for key, item in value.items()}
            if isinstance(value, list):
                return [visit(item) for item in value]
            return value

        expanded = visit(raw)
        if _contains_domain_context_sentinel(expanded):
            raise WorkflowValidationError(
                "domain context sentinel is only allowed on selected Agent steps"
            )
        if not isinstance(expanded, Mapping):  # pragma: no cover - workflow object
            raise WorkflowValidationError("domain Agent context expansion failed")
        result.append(WorkflowSpec.from_mapping(expanded))
    if observed != selected:
        raise WorkflowValidationError(
            "domain context resources were not consumed by every selected Agent"
        )
    return tuple(result)


def _materialize_parameter_profile(
    workflow: WorkflowSpec,
    profile: DomainParameterProfile | None,
) -> tuple[WorkflowSpec, int]:
    """Replace only the reserved literal binding with frozen profile JSON."""

    count = 0

    def visit(value: JsonValue) -> JsonValue:
        nonlocal count
        if isinstance(value, dict):
            if (
                value.get("source") == "literal"
                and value.get("value") == _PARAMETER_PROFILE_SENTINEL
                and set(value) == {"source", "value"}
            ):
                count += 1
                if profile is None:
                    return dict(value)
                return {"source": "literal", "value": json_copy(profile.defaults)}
            return {key: visit(item) for key, item in value.items()}
        if isinstance(value, list):
            return [visit(item) for item in value]
        return value

    raw = visit(workflow.to_mapping())
    if not isinstance(raw, Mapping):  # pragma: no cover - workflow is an object
        raise WorkflowValidationError("domain workflow profile expansion failed")
    return WorkflowSpec.from_mapping(raw), count


def _selected_parameter_profile(
    pack: DomainPack,
    workflows: Sequence[WorkflowSpec],
    requested: str | None,
) -> tuple[DomainParameterProfile | None, tuple[WorkflowSpec, ...]]:
    profiles = {item.name: item for item in pack.parameter_profiles}
    selected = profiles.get(requested) if requested is not None else None
    if requested is not None and selected is None:
        raise WorkflowValidationError(
            f"domain parameter profile {requested!r} is not declared"
        )
    probes = tuple(_materialize_parameter_profile(item, selected) for item in workflows)
    placeholders = sum(count for _workflow, count in probes)
    if placeholders and selected is None:
        raise WorkflowValidationError(
            "domain compilation requires an explicit parameter_profile"
        )
    if not placeholders and requested is not None:
        raise WorkflowValidationError(
            "selected domain parameter profile is not consumed by the workflow"
        )
    if selected is None:
        return None, tuple(item for item, _count in probes)
    materialized = tuple(
        _materialize_parameter_profile(item, selected)[0] for item in workflows
    )
    return selected, materialized


def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _array(values: Sequence[str]) -> str:
    return "[" + ", ".join(_quoted(item) for item in values) + "]"


def _project_toml(
    workflow: WorkflowSpec,
    *,
    instructions: Mapping[str, str],
    subworkflows: Mapping[str, str],
    files: Sequence[str],
    registries: Sequence[str],
) -> str:
    lines = [
        "[project]",
        f"name = {_quoted(workflow.workflow_id)}",
        f"version = {_quoted(workflow.version)}",
        'requires-python = ">=3.12"',
        "dependencies = []",
        "",
        "[tool.platform-agent-workflow]",
        f"schema_version = {_quoted(PROJECT_SCHEMA_V1)}",
        'kind = "workflow"',
        'workflow = "workflow.json"',
        'tools = "tools.json"',
        'dependency_lock = "pylock.toml"',
        'domain_lock = "domain-lock.json"',
        f"files = {_array(tuple(files))}",
        'evaluation_cases = "eval/cases.json"',
        f"tool_registries = {_array(tuple(registries))}",
        "",
        "[tool.platform-agent-workflow.agent_instructions]",
        *(f"{_quoted(key)} = {_quoted(value)}" for key, value in sorted(instructions.items())),
        "",
        "[tool.platform-agent-workflow.subworkflows]",
        *(f"{_quoted(key)} = {_quoted(value)}" for key, value in sorted(subworkflows.items())),
        "",
    ]
    return "\n".join(lines)


def _write_entry_project(
    destination: Path,
    pack: DomainPack,
    entry: DomainEntryWorkflow,
    workflow: WorkflowSpec,
    children: Sequence[WorkflowSpec],
    manifests: Sequence[Mapping[str, JsonValue]],
    registries: Sequence[str],
    parameter_profile: DomainParameterProfile | None,
) -> DomainLock:
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "workflow.json").write_text(
        canonical_json(workflow.to_mapping()) + "\n", encoding="utf-8"
    )
    (destination / "tools.json").write_text(
        canonical_json(list(manifests)) + "\n", encoding="utf-8"
    )
    lock_source, _ = _source_file(pack.source_root, pack.dependency_lock, "domain dependency lock")
    shutil.copyfile(lock_source, destination / "pylock.toml")
    child_paths: dict[str, str] = {}
    for index, child in enumerate(sorted(children, key=lambda item: (item.workflow_id, item.version))):
        relative = f"subworkflows/{index:03d}-{child.workflow_id}-{child.version}.json"
        output = destination / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(canonical_json(child.to_mapping()) + "\n", encoding="utf-8")
        child_paths[f"{child.workflow_id}@{child.version}"] = relative
    instructions: dict[str, str] = {}
    for address, source_path in sorted(pack.agent_instructions.items()):
        name = hashlib.sha256(address.encode("utf-8")).hexdigest()[:24] + ".md"
        relative = "instructions/" + name
        source, _ = _source_file(pack.source_root, source_path, "domain Agent instruction")
        output = destination / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, output)
        instructions[address] = relative
    files: list[str] = ["domain-lock.json"]
    for asset in (*pack.resources, *pack.tests):
        _copy_asset(pack.source_root, asset.path, destination)
        files.append(asset.path)
    cases_destination = destination / "eval" / "cases.json"
    cases_destination.parent.mkdir(parents=True, exist_ok=True)
    if pack.evaluation_cases is None:
        cases_destination.write_text("[]\n", encoding="utf-8")
    else:
        cases_source, _ = _source_file(pack.source_root, pack.evaluation_cases, "domain evaluation cases")
        shutil.copyfile(cases_source, cases_destination)
    lock = DomainLock.from_pack(pack, entry, parameter_profile)
    (destination / "domain-lock.json").write_text(
        canonical_json(lock.to_mapping()) + "\n", encoding="utf-8"
    )
    (destination / "pyproject.toml").write_text(
        _project_toml(
            workflow,
            instructions=instructions,
            subworkflows=child_paths,
            files=tuple(files),
            registries=tuple(registries),
        ),
        encoding="utf-8",
    )
    return lock


def compile_domain_pack(
    source: str | Path | DomainPack,
    output: str | Path,
    *,
    tool_registries: Sequence[str] = (),
    parameter_profile: str | None = None,
) -> DomainCompilation:
    """Compile a checked domain pack into ordinary, runnable V6 projects.

    ``tool_registries`` is supplied by the caller/host, never the domain
    declaration.  It merely records explicit trusted registration locations
    in the generated development project; it is not executed by compilation.
    """

    pack = check_domain_pack(source)
    entries, children, manifests = _checked_domain(pack)
    selected_profile, materialized = _selected_parameter_profile(
        pack,
        (*[item for _entry, item in entries], *children),
        parameter_profile,
    )
    materialized = _materialize_domain_context(pack, materialized)
    entry_count = len(entries)
    materialized_entries = tuple(
        (
            replace(entry, definition_digest=current.definition_digest),
            current,
        )
        for (entry, _source), current in zip(
            entries, materialized[:entry_count], strict=True
        )
    )
    materialized_children = materialized[entry_count:]
    registries = tuple(tool_registries)
    if any(
        not isinstance(item, str)
        or item.count(":") != 1
        or not item.partition(":")[0]
        or not item.partition(":")[2]
        for item in registries
    ):
        raise WorkflowValidationError(
            "domain tool_registries must use explicit MODULE:CALLABLE strings"
        )
    destination = Path(output)
    if destination.exists() or destination.is_symlink():
        raise WorkflowValidationError("domain compilation destination must not already exist")
    parent = destination.parent
    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise WorkflowValidationError("domain compilation parent is invalid")
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.domain-", dir=parent))
    published = False
    try:
        locks: dict[str, DomainLock] = {}
        for entry, workflow in materialized_entries:
            locks[entry.name] = _write_entry_project(
                staging / "entries" / entry.name,
                pack,
                entry,
                workflow,
                materialized_children,
                manifests,
                registries,
                selected_profile,
            )
        index = {
            "schema_version": DOMAIN_COMPILATION_SCHEMA_V1,
            "domain": {
                "domain_id": pack.domain_id,
                "version": pack.version,
                "definition_digest": pack.definition_digest,
            },
            "entries": {
                name: {
                    "project": f"entries/{name}",
                    "domain_lock_digest": lock.definition_digest,
                }
                for name, lock in sorted(locks.items())
            },
        }
        (staging / "domain-compilation.json").write_text(
            canonical_json(index) + "\n", encoding="utf-8"
        )
        # Validate the final V6 project representation before the one publish
        # rename.  This catches mismatches between the compiler and existing
        # portability contracts without introducing parallel project semantics.
        for entry, _workflow in materialized_entries:
            check_project(staging / "entries" / entry.name)
        os.replace(staging, destination)
        published = True
    finally:
        if not published:
            shutil.rmtree(staging, ignore_errors=True)
    projects = {
        entry.name: check_project(destination / "entries" / entry.name)
        for entry, _workflow in materialized_entries
    }
    final_locks = {
        name: load_domain_lock(project.domain_lock_path)  # type: ignore[arg-type]
        for name, project in projects.items()
    }
    return DomainCompilation(pack, destination.resolve(), projects, final_locks)


def load_domain_lock(path: str | Path) -> DomainLock:
    """Load a compiled lock without importing authoring-only dependencies."""

    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise WorkflowValidationError("domain lock file is unavailable")
    return DomainLock.from_mapping(_load_json(candidate, "domain lock"))


def _compiled_asset(root: Path, asset: DomainAsset, label: str) -> None:
    path, _ = _source_file(root, asset.path, label)
    if path.stat().st_size > _MAX_ASSET_BYTES or _sha256(path) != asset.sha256:
        raise WorkflowValidationError(f"{label} no longer matches the frozen domain lock")


def validate_domain_project(
    lock: DomainLock,
    project: PortableProject,
    workflow: WorkflowSpec,
    children: Sequence[WorkflowSpec],
    tool_manifests: Sequence[Mapping[str, JsonValue]],
) -> None:
    """Check that a generated project still matches its immutable domain lock."""

    if project.domain_lock_path is None:
        raise WorkflowValidationError("domain validation requires a domain lock path")
    if (
        workflow.workflow_id != lock.entry.workflow_id
        or workflow.version != lock.entry.version
        or workflow.definition_digest != lock.entry.definition_digest
    ):
        raise WorkflowValidationError("compiled workflow differs from its frozen domain entry")
    declared = {
        (item.tool_id, item.version, item.package_digest) for item in lock.capability_tools
    }
    manifests = {
        (str(item["tool_id"]), str(item["version"]), str(item["package_digest"]))
        for item in tool_manifests
    }
    if manifests != declared:
        raise WorkflowValidationError("compiled tool catalog differs from its frozen domain lock")
    permissions: set[str] = set()
    for manifest in tool_manifests:
        raw_permissions = manifest.get("permissions", [])
        if not isinstance(raw_permissions, list) or any(not isinstance(item, str) for item in raw_permissions):
            raise WorkflowValidationError("compiled domain tool permissions are invalid")
        permissions.update(raw_permissions)
    if permissions != set(lock.requested_permissions):
        raise WorkflowValidationError("compiled domain permissions differ from its frozen lock")
    for current in (workflow, *children):
        if not _workflow_tools(current) <= declared:
            raise WorkflowValidationError("compiled workflow exceeds frozen domain tool scope")
    all_rules = {item.rule_ref: item for item in lock.checks}
    for binding in lock.entry.required_checks:
        if binding.rule_ref not in all_rules or not all_rules[binding.rule_ref].required:
            raise WorkflowValidationError("compiled domain check is not declared as required")
        _check_gate(workflow, binding, all_rules[binding.rule_ref])
    total = 0
    for label, assets in (("compiled domain resource", lock.resources), ("compiled domain test", lock.tests)):
        for asset in assets:
            _compiled_asset(project.root, asset, label)
            total += (project.root / asset.path).stat().st_size
    if total > _MAX_TOTAL_ASSET_BYTES:
        raise WorkflowValidationError("compiled domain assets exceed the total size limit")


def validate_domain_target(
    lock: DomainLock,
    target: Any,
    workflow: WorkflowSpec,
    children: Sequence[WorkflowSpec],
    tool_manifests: Sequence[Mapping[str, JsonValue]],
) -> None:
    """Enforce the domain-request ∩ step-scope ∩ host target intersection."""

    granted = getattr(target, "allowed_permissions", frozenset())
    if not set(lock.requested_permissions) <= set(granted):
        raise WorkflowValidationError("target does not grant every domain-requested permission")
    for manifest in tool_manifests:
        permissions = manifest.get("permissions", [])
        if not isinstance(permissions, list) or not set(permissions) <= set(lock.requested_permissions):
            raise WorkflowValidationError("tool permission exceeds the frozen domain request")
    required_capabilities = set(lock.driver_capabilities)
    if not required_capabilities:
        return
    all_workflows = (workflow, *children)
    agent_addresses = [
        (current.workflow_id, current.version, step.step_id)
        for current in all_workflows
        for step in _walk_steps(current.steps)
        if step.kind is StepKind.AGENT
    ]
    for address in agent_addresses:
        exact = [
            binding
            for binding in getattr(target, "driver_bindings", ())
            if getattr(binding, "address", None) == address
        ]
        if exact:
            selected = exact
        else:
            # Match the existing project loader's legacy short-address
            # compatibility only when that step id is unambiguous.
            same_step = [item for item in agent_addresses if item[2] == address[2]]
            selected = [
                binding
                for binding in getattr(target, "driver_bindings", ())
                if getattr(binding, "address", None) is None
                and getattr(binding, "step_id", None) == address[2]
                and len(same_step) == 1
            ]
        for binding in selected:
            capabilities = getattr(binding, "capabilities", frozenset())
            if not required_capabilities <= set(capabilities):
                raise WorkflowValidationError(
                    "target driver lacks a domain-required capability"
                )
