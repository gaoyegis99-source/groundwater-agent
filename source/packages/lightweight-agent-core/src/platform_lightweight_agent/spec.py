"""Portable, declarative workflow specifications and Agent decisions.

This module is intentionally standard-library only.  A workflow carries
references to versioned tool packages; it never serializes Python callables,
credentials, or a framework checkpoint.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from typing import Any

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_AGENT_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SECRET_KEY_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
_MAX_AGENT_TOOL_CONTRACT_BYTES = 64 * 1024
_MAX_AGENT_STEP_CONTRACT_BYTES = 256 * 1024
DEFAULT_NODE_INPUT_BYTES = 1 * 1024 * 1024
DEFAULT_NODE_OUTPUT_BYTES = 1 * 1024 * 1024
DEFAULT_RUN_RETAINED_STATE_BYTES = 8 * 1024 * 1024
_MAX_PAYLOAD_BUDGET_BYTES = 256 * 1024 * 1024
V1_SCHEMA_VERSION = "v6-light-workflow-v1"
V2_SCHEMA_VERSION = "v6-light-workflow-v2"
V3_SCHEMA_VERSION = "v6-light-workflow-v3"
V4_SCHEMA_VERSION = "v6-light-workflow-v4"
ASYNC_SCHEMA_VERSIONS = frozenset({V3_SCHEMA_VERSION, V4_SCHEMA_VERSION})
# New Python definitions use v4. Older wire definitions retain their explicit
# schema version and are never silently reinterpreted on resume.
RECOMMENDED_NEW_WORKFLOW_SCHEMA_VERSION = V4_SCHEMA_VERSION
_BINDING_SOURCES = frozenset(
    {"workflow_input", "parent_input", "step_output", "literal"}
)
_SECRET_NAMES = frozenset(
    {
        "api_key",
        "authorization",
        "credential",
        "password",
        "private_key",
        "secret",
        "token",
    }
)
_SECRET_SUFFIXES = (
    "_api_key",
    "_authorization",
    "_credential",
    "_password",
    "_private_key",
    "_secret",
    "_token",
)


class WorkflowValidationError(ValueError):
    """A portable workflow or tool manifest is malformed or unsafe."""


class StepKind(StrEnum):
    TOOL = "tool"
    AGENT = "agent"
    APPROVAL = "approval"
    CONDITION = "condition"
    PARALLEL = "parallel"
    SUBWORKFLOW = "subworkflow"


@dataclass(frozen=True)
class WorkflowSemantics:
    """Stable, machine-readable behavior summary for one schema version.

    The schema version is a contract, not a cosmetic label: a user deciding
    whether to create or resume a workflow needs to see its concurrency,
    cancellation, recovery, and budget behavior without reverse-engineering a
    snapshot.  This summary deliberately contains no mutable provider or host
    configuration.
    """

    schema_version: str
    recommended_for_new_projects: bool
    node_contracts: bool
    native_agent_tools: bool
    bounded_async_execution: bool
    cancellation_mode: str
    recovery_mode: str
    durable_agent_budget: bool

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "recommended_for_new_projects": self.recommended_for_new_projects,
            "node_contracts": self.node_contracts,
            "native_agent_tools": self.native_agent_tools,
            "concurrency": (
                "bounded_async"
                if self.bounded_async_execution
                else "legacy_deterministic"
            ),
            "cancellation": self.cancellation_mode,
            "recovery": self.recovery_mode,
            "durable_agent_budget": self.durable_agent_budget,
            "automatic_migration": False,
        }


def _json_pointer(value: object, label: str, *, allow_root: bool = True) -> str:
    if not isinstance(value, str) or (value and not value.startswith("/")):
        raise WorkflowValidationError(f"{label} must be a JSON Pointer")
    if not allow_root and value == "":
        raise WorkflowValidationError(f"{label} must not replace the document root")
    for token in value.split("/")[1:]:
        index = 0
        while index < len(token):
            if token[index] == "~":
                if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
                    raise WorkflowValidationError(f"{label} has an invalid escape")
                index += 2
            else:
                index += 1
    return value


def canonical_json(value: object) -> str:
    """Canonical JSON used for portable digests and conflict checks."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise WorkflowValidationError(
            "Workflow values must be JSON serializable"
        ) from error


def json_copy(value: JsonValue | Mapping[str, Any] | Sequence[Any]) -> Any:
    """Validate JSON data and detach it from a caller-owned object."""

    return json.loads(canonical_json(value))


def digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise WorkflowValidationError(f"{label} must be a portable identifier")
    return value


def _version(value: object, label: str) -> str:
    version = _identifier(value, label)
    if version.lower() == "latest":
        raise WorkflowValidationError(f"{label} must be fixed; latest is not portable")
    return version


def _mapping(value: object, label: str) -> dict[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise WorkflowValidationError(f"{label} must be an object")
    copied = json_copy(dict(value))
    if not isinstance(copied, dict):  # defensive; json_copy preserves mappings
        raise WorkflowValidationError(f"{label} must be an object")
    return copied


def contains_secret_name(value: object) -> bool:
    """Detect values that must not enter a portable definition or export."""

    # JSON check-point state is overwhelmingly made of plain ``dict`` and
    # ``list`` values.  Take those concrete fast paths before the ABC checks:
    # the function is deliberately called at every persistence boundary, and
    # repeatedly asking ``collections.abc.Sequence`` about a large JSON tree
    # can dominate a long local run.  The fallback keeps the public behavior
    # for arbitrary Mapping/Sequence implementations unchanged.
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            for key, child in current.items():
                if _is_secret_key(key):
                    return True
                pending.append(child)
            continue
        if isinstance(current, (list, tuple)):
            pending.extend(current)
            continue
        if isinstance(current, Mapping):
            for key, child in current.items():
                if _is_secret_key(key):
                    return True
                pending.append(child)
            continue
        # A JSON tree reaches this point mostly for scalars.  Do not pay an
        # ABC ``Sequence`` lookup for each one; retain that fallback solely
        # for the public helper's compatibility with custom sequences.
        if isinstance(current, (str, bytes, bytearray, int, float, bool, type(None))):
            continue
        if isinstance(current, Sequence):
            pending.extend(current)
    return False


def _is_secret_key(key: object) -> bool:
    """Preserve the wire-level secret-key rules without regex per normal key."""

    return _is_secret_key_text(str(key))


@lru_cache(maxsize=512)
def _is_secret_key_text(text: str) -> bool:
    """Bounded memoization for repeated JSON field names in run state."""

    if text in _SECRET_NAMES or text.endswith(_SECRET_SUFFIXES):
        return True
    # Most persisted state keys are already lower snake case.  Regex work is
    # required only when a hyphen or a camel-case boundary could change the
    # normalization used by the compatibility rule below.
    if "-" not in text and text.lower() == text:
        return False
    normalized = _SECRET_KEY_BOUNDARY.sub("_", text).lower().replace("-", "_")
    return normalized in _SECRET_NAMES or normalized.endswith(_SECRET_SUFFIXES)


@dataclass(frozen=True)
class ToolReference:
    """An exact reference to a trusted tool package, not executable code."""

    tool_id: str
    version: str
    package_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_id", _identifier(self.tool_id, "tool_id"))
        object.__setattr__(self, "version", _version(self.version, "tool version"))
        if not isinstance(self.package_digest, str) or not _DIGEST.fullmatch(
            self.package_digest
        ):
            raise WorkflowValidationError("tool package_digest must be a sha256 digest")

    @classmethod
    def from_mapping(cls, value: object) -> ToolReference:
        raw = _mapping(value, "tool")
        if set(raw) != {"tool_id", "version", "package_digest"}:
            raise WorkflowValidationError(
                "tool must contain tool_id, version, and package_digest"
            )
        return cls(**raw)  # type: ignore[arg-type]

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "tool_id": self.tool_id,
            "version": self.version,
            "package_digest": self.package_digest,
        }


def _strict_object_schema(value: object, label: str) -> dict[str, JsonValue]:
    schema = _mapping(value, label)
    if schema.get("$schema") != _JSON_SCHEMA_DIALECT:
        raise WorkflowValidationError(f"{label} must declare JSON Schema Draft 2020-12")
    if schema.get("type") != "object":
        raise WorkflowValidationError(f"{label} root type must be object")
    if contains_secret_name(schema):
        raise WorkflowValidationError(f"{label} must not describe credential values")

    schema_maps = {
        "$defs",
        "definitions",
        "dependentSchemas",
        "patternProperties",
        "properties",
    }
    schema_sequences = {"allOf", "anyOf", "oneOf", "prefixItems"}
    schema_values = {
        "additionalProperties",
        "contains",
        "contentSchema",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    }
    object_keywords = {
        "additionalProperties",
        "dependentRequired",
        "dependentSchemas",
        "maxProperties",
        "minProperties",
        "patternProperties",
        "properties",
        "propertyNames",
        "required",
        "unevaluatedProperties",
    }

    def visit(node: object) -> None:
        if isinstance(node, Mapping):
            for reference_keyword in ("$ref", "$dynamicRef"):
                reference = node.get(reference_keyword)
                if reference is not None and (
                    not isinstance(reference, str) or not reference.startswith("#")
                ):
                    raise WorkflowValidationError(
                        f"{label} may contain only local JSON Schema references"
                    )
            declared_type = node.get("type")
            includes_object = declared_type == "object" or (
                isinstance(declared_type, list) and "object" in declared_type
            )
            if (includes_object or object_keywords.intersection(node)) and node.get(
                "additionalProperties"
            ) is not False:
                raise WorkflowValidationError(
                    f"{label} object schemas must set additionalProperties to false"
                )
            for key, child in node.items():
                if key in schema_maps and isinstance(child, Mapping):
                    for nested_schema in child.values():
                        visit(nested_schema)
                elif key in schema_sequences and isinstance(child, Sequence):
                    for nested_schema in child:
                        visit(nested_schema)
                elif key in schema_values:
                    visit(child)

    visit(schema)
    return schema


@dataclass(frozen=True)
class AgentToolExample:
    """A bounded example shown with an Agent tool definition."""

    arguments: Mapping[str, JsonValue] = field(default_factory=dict)
    output: Mapping[str, JsonValue] | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        arguments = _mapping(self.arguments, "agent tool example arguments")
        if contains_secret_name(arguments):
            raise WorkflowValidationError(
                "agent tool examples must not contain credentials"
            )
        object.__setattr__(self, "arguments", arguments)
        if (self.output is None) == (self.error is None):
            raise WorkflowValidationError(
                "agent tool example requires exactly one of output or error"
            )
        if self.output is not None:
            output = _mapping(self.output, "agent tool example output")
            if contains_secret_name(output):
                raise WorkflowValidationError(
                    "agent tool examples must not contain credentials"
                )
            object.__setattr__(self, "output", output)
        if self.error is not None and (
            not isinstance(self.error, str) or _ERROR_CODE.fullmatch(self.error) is None
        ):
            raise WorkflowValidationError(
                "agent tool example error must be a stable error code"
            )

    @classmethod
    def from_mapping(cls, value: object) -> AgentToolExample:
        raw = _mapping(value, "agent tool example")
        if set(raw) not in (
            {"arguments", "output"},
            {"arguments", "error"},
        ):
            raise WorkflowValidationError(
                "agent tool example must contain arguments and one result"
            )
        output = raw.get("output")
        return cls(
            arguments=_mapping(raw["arguments"], "agent tool example arguments"),
            output=(
                _mapping(output, "agent tool example output")
                if output is not None
                else None
            ),
            error=raw.get("error") if isinstance(raw.get("error"), str) else None,
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {"arguments": dict(self.arguments)}
        if self.output is not None:
            result["output"] = dict(self.output)
        else:
            result["error"] = self.error
        return result


@dataclass(frozen=True)
class AgentToolError:
    """A stable business error the model may receive from an Agent tool."""

    code: str
    description: str
    model_retryable: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or _ERROR_CODE.fullmatch(self.code) is None:
            raise WorkflowValidationError("agent tool error code must be stable")
        if (
            not isinstance(self.description, str)
            or not self.description.strip()
            or len(self.description.encode("utf-8")) > 4_096
        ):
            raise WorkflowValidationError(
                "agent tool error description must be a bounded non-empty string"
            )
        if not isinstance(self.model_retryable, bool):
            raise WorkflowValidationError(
                "agent tool error model_retryable must be boolean"
            )

    @classmethod
    def from_mapping(cls, value: object) -> AgentToolError:
        raw = _mapping(value, "agent tool error")
        if set(raw) != {"code", "description", "model_retryable"}:
            raise WorkflowValidationError(
                "agent tool error has unknown or missing fields"
            )
        return cls(
            code=raw["code"],  # type: ignore[arg-type]
            description=raw["description"],  # type: ignore[arg-type]
            model_retryable=raw["model_retryable"],  # type: ignore[arg-type]
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "code": self.code,
            "description": self.description,
            "model_retryable": self.model_retryable,
        }


def _render_agent_tool_description(
    description: str,
    examples: Sequence[AgentToolExample],
    errors: Sequence[AgentToolError],
) -> str:
    sections = [description.strip()]
    if examples:
        sections.append(
            "Examples:\n"
            + "\n".join(
                "- " + canonical_json(example.to_mapping()) for example in examples
            )
        )
    if errors:
        sections.append(
            "Possible errors:\n"
            + "\n".join(
                f"- {error.code}: {error.description} "
                f"(model_retryable={str(error.model_retryable).lower()})"
                for error in errors
            )
        )
    return "\n\n".join(sections)


@dataclass(frozen=True)
class ToolRecoveryCapabilities:
    """Trusted recovery facts supplied by a tool developer, never a model."""

    external_side_effects: bool
    accepts_idempotency_key: bool = False
    supports_result_query: bool = False
    supports_cooperative_cancel: bool = False
    mutex_resource: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "external_side_effects",
            "accepts_idempotency_key",
            "supports_result_query",
            "supports_cooperative_cancel",
        ):
            if not isinstance(getattr(self, name), bool):
                raise WorkflowValidationError(f"tool capability {name} must be boolean")
        if self.mutex_resource is not None:
            object.__setattr__(
                self,
                "mutex_resource",
                _identifier(self.mutex_resource, "tool mutex_resource"),
            )

    @classmethod
    def from_mapping(cls, value: object) -> ToolRecoveryCapabilities:
        raw = _mapping(value, "tool recovery capabilities")
        if set(raw) != {
            "external_side_effects",
            "accepts_idempotency_key",
            "supports_result_query",
            "supports_cooperative_cancel",
            "mutex_resource",
        }:
            raise WorkflowValidationError(
                "tool recovery capabilities have unknown or missing fields"
            )
        return cls(**raw)  # type: ignore[arg-type]

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "external_side_effects": self.external_side_effects,
            "accepts_idempotency_key": self.accepts_idempotency_key,
            "supports_result_query": self.supports_result_query,
            "supports_cooperative_cancel": self.supports_cooperative_cancel,
            "mutex_resource": self.mutex_resource,
        }


@dataclass(frozen=True)
class AgentBudget:
    """Durable v4 limits enforced around provider and tool boundaries."""

    max_model_requests: int = 8
    max_tool_calls: int = 32
    max_context_bytes: int = 256 * 1024
    max_total_tokens: int | None = None
    max_tool_output_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        limits = {
            "max_model_requests": (self.max_model_requests, 1, 128),
            "max_tool_calls": (self.max_tool_calls, 0, 1_024),
            "max_context_bytes": (self.max_context_bytes, 1, 8 * 1024 * 1024),
            "max_tool_output_bytes": (
                self.max_tool_output_bytes,
                1,
                8 * 1024 * 1024,
            ),
        }
        for name, (value, minimum, maximum) in limits.items():
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not minimum <= value <= maximum
            ):
                raise WorkflowValidationError(
                    f"Agent budget {name} must be between {minimum} and {maximum}"
                )
        if self.max_total_tokens is not None and (
            not isinstance(self.max_total_tokens, int)
            or isinstance(self.max_total_tokens, bool)
            or not 1 <= self.max_total_tokens <= 100_000_000
        ):
            raise WorkflowValidationError(
                "Agent budget max_total_tokens must be positive"
            )

    @classmethod
    def from_mapping(cls, value: object) -> AgentBudget:
        raw = _mapping(value, "Agent budget")
        if set(raw) != {
            "max_model_requests",
            "max_tool_calls",
            "max_context_bytes",
            "max_total_tokens",
            "max_tool_output_bytes",
        }:
            raise WorkflowValidationError("Agent budget has unknown or missing fields")
        return cls(**raw)  # type: ignore[arg-type]

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "max_model_requests": self.max_model_requests,
            "max_tool_calls": self.max_tool_calls,
            "max_context_bytes": self.max_context_bytes,
            "max_total_tokens": self.max_total_tokens,
            "max_tool_output_bytes": self.max_tool_output_bytes,
        }


@dataclass(frozen=True)
class AgentToolSpec:
    """A frozen model-facing contract bound to an exact trusted tool package."""

    name: str
    tool: ToolReference
    description: str
    input_schema: Mapping[str, JsonValue]
    output_schema: Mapping[str, JsonValue]
    examples: tuple[AgentToolExample, ...] = ()
    errors: tuple[AgentToolError, ...] = ()
    capabilities: ToolRecoveryCapabilities | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or _AGENT_TOOL_NAME.fullmatch(self.name) is None
        ):
            raise WorkflowValidationError(
                "agent tool name must be a provider-compatible short name"
            )
        if not isinstance(self.tool, ToolReference):
            raise WorkflowValidationError("agent tool must use an exact ToolReference")
        if (
            not isinstance(self.description, str)
            or not self.description.strip()
            or len(self.description.encode("utf-8")) > 4_096
        ):
            raise WorkflowValidationError(
                "agent tool description must be a bounded non-empty string"
            )
        object.__setattr__(
            self,
            "input_schema",
            _strict_object_schema(self.input_schema, "agent tool input_schema"),
        )
        object.__setattr__(
            self,
            "output_schema",
            _strict_object_schema(self.output_schema, "agent tool output_schema"),
        )
        examples = tuple(self.examples)
        errors = tuple(self.errors)
        if len(examples) > 4 or any(
            not isinstance(item, AgentToolExample) for item in examples
        ):
            raise WorkflowValidationError(
                "agent tool may declare at most four typed examples"
            )
        if len(errors) > 32 or any(
            not isinstance(item, AgentToolError) for item in errors
        ):
            raise WorkflowValidationError(
                "agent tool may declare at most 32 typed errors"
            )
        if len({item.code for item in errors}) != len(errors):
            raise WorkflowValidationError("agent tool error codes must be unique")
        declared_errors = {item.code for item in errors}
        if any(
            example.error is not None and example.error not in declared_errors
            for example in examples
        ):
            raise WorkflowValidationError(
                "agent tool example errors must be declared by the tool"
            )
        object.__setattr__(self, "examples", examples)
        object.__setattr__(self, "errors", errors)
        if self.capabilities is not None and not isinstance(
            self.capabilities, ToolRecoveryCapabilities
        ):
            raise WorkflowValidationError(
                "agent tool capabilities must be ToolRecoveryCapabilities"
            )
        if self.model_contract_size > _MAX_AGENT_TOOL_CONTRACT_BYTES:
            raise WorkflowValidationError("agent tool contract exceeds 64 KiB")

    @property
    def model_description(self) -> str:
        return _render_agent_tool_description(
            self.description, self.examples, self.errors
        )

    @property
    def model_contract_size(self) -> int:
        return len(
            canonical_json(
                {
                    "name": self.name,
                    "description": self.model_description,
                    "input_schema": self.input_schema,
                    "output_schema": self.output_schema,
                }
            ).encode("utf-8")
        )

    @classmethod
    def from_mapping(cls, value: object) -> AgentToolSpec:
        raw = _mapping(value, "agent tool")
        expected = {
            "name",
            "tool",
            "description",
            "input_schema",
            "output_schema",
            "examples",
            "errors",
        }
        if set(raw) not in (expected, expected | {"capabilities"}):
            raise WorkflowValidationError("agent tool has unknown or missing fields")
        examples = raw["examples"]
        errors = raw["errors"]
        if not isinstance(examples, list) or not isinstance(errors, list):
            raise WorkflowValidationError(
                "agent tool examples and errors must be lists"
            )
        return cls(
            name=raw["name"],  # type: ignore[arg-type]
            tool=ToolReference.from_mapping(raw["tool"]),
            description=raw["description"],  # type: ignore[arg-type]
            input_schema=_mapping(raw["input_schema"], "agent tool input_schema"),
            output_schema=_mapping(raw["output_schema"], "agent tool output_schema"),
            examples=tuple(AgentToolExample.from_mapping(item) for item in examples),
            errors=tuple(AgentToolError.from_mapping(item) for item in errors),
            capabilities=(
                ToolRecoveryCapabilities.from_mapping(raw["capabilities"])
                if "capabilities" in raw
                else None
            ),
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "name": self.name,
            "tool": self.tool.to_mapping(),
            "description": self.description,
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
            "examples": [item.to_mapping() for item in self.examples],
            "errors": [item.to_mapping() for item in self.errors],
        }
        if self.capabilities is not None:
            result["capabilities"] = self.capabilities.to_mapping()
        return result


@dataclass(frozen=True)
class ToolCall:
    """A model-proposed call.  The engine performs approval and execution."""

    tool: ToolReference
    arguments: Mapping[str, JsonValue] = field(default_factory=dict)
    # Native model protocols use this opaque identity to correlate a later
    # tool result with the assistant message that proposed it.  It is not an
    # action identity and never grants execution authority; the kernel binds
    # it to its own durable ``action_id`` before dispatch.
    call_id: str | None = None

    def __post_init__(self) -> None:
        arguments = _mapping(self.arguments, "tool arguments")
        if contains_secret_name(arguments):
            raise WorkflowValidationError(
                "tool arguments must reference credentials, not carry them"
            )
        object.__setattr__(self, "arguments", arguments)
        if self.call_id is not None and (
            not isinstance(self.call_id, str)
            or not self.call_id
            or len(self.call_id.encode("utf-8")) > 1_024
            or any(ord(character) < 32 for character in self.call_id)
        ):
            raise WorkflowValidationError(
                "tool call_id must be a bounded opaque string"
            )

    @classmethod
    def from_mapping(cls, value: object) -> ToolCall:
        raw = _mapping(value, "tool call")
        if set(raw) not in (
            {"tool", "arguments"},
            {"tool", "arguments", "call_id"},
        ):
            raise WorkflowValidationError(
                "tool call must contain tool, arguments, and optional call_id"
            )
        return cls(
            tool=ToolReference.from_mapping(raw["tool"]),
            arguments=_mapping(raw["arguments"], "tool arguments"),
            call_id=raw.get("call_id"),  # type: ignore[arg-type]
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "tool": self.tool.to_mapping(),
            "arguments": dict(self.arguments),
        }
        if self.call_id is not None:
            result["call_id"] = self.call_id
        return result


@dataclass(frozen=True)
class AgentDecision:
    """Framework-neutral output of an :class:`AgentDriver`."""

    message: str
    tool_calls: tuple[ToolCall, ...] = ()
    output: Mapping[str, JsonValue] | None = None
    usage: Mapping[str, JsonValue] | None = None
    # Adapter-owned, JSON-only continuation data.  The execution binding pins
    # the adapter/version that may consume it; SDK objects are never stored.
    continuation: Mapping[str, JsonValue] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.message, str) or len(self.message) > 64_000:
            raise WorkflowValidationError("agent message must be a bounded string")
        if len(self.tool_calls) > 32:
            raise WorkflowValidationError(
                "an agent decision may propose at most 32 tools"
            )
        call_ids = [
            item.call_id for item in self.tool_calls if item.call_id is not None
        ]
        if len(call_ids) != len(set(call_ids)):
            raise WorkflowValidationError("agent tool call_id values must be unique")
        if self.output is not None:
            output = _mapping(self.output, "agent structured output")
            if contains_secret_name(output):
                raise WorkflowValidationError(
                    "agent structured output must not contain credentials"
                )
            object.__setattr__(self, "output", output)
        if self.usage is not None:
            usage = dict(_mapping(self.usage, "agent invocation usage"))
            allowed = {
                "request_count",
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "cached_tokens",
                "reasoning_tokens",
                "transport_attempts",
                "duration_seconds",
                "provider",
                "model",
            }
            required = allowed - {"transport_attempts"}
            if not required <= set(usage) or set(usage) - allowed:
                raise WorkflowValidationError("agent invocation usage is malformed")
            # Older custom drivers can continue returning the original neutral
            # usage shape.  Missing transport telemetry is an explicit unknown,
            # never an inferred single network attempt.
            usage.setdefault("transport_attempts", None)
            count_fields = {
                "request_count",
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "cached_tokens",
                "reasoning_tokens",
                "transport_attempts",
            }
            if any(
                value is not None
                and (not isinstance(value, int) or isinstance(value, bool) or value < 0)
                for name, value in usage.items()
                if name in count_fields
            ):
                raise WorkflowValidationError(
                    "agent invocation usage counts must be non-negative integers"
                )
            duration = usage["duration_seconds"]
            if duration is not None and (
                not isinstance(duration, (int, float))
                or isinstance(duration, bool)
                or not math.isfinite(duration)
                or duration < 0
            ):
                raise WorkflowValidationError(
                    "agent invocation usage duration must be non-negative and finite"
                )
            for name in ("provider", "model"):
                value = usage[name]
                if value is not None and (
                    not isinstance(value, str)
                    or not value
                    or len(value.encode("utf-8")) > 1_024
                    or any(ord(character) < 32 for character in value)
                ):
                    raise WorkflowValidationError(
                        f"agent invocation usage {name} is malformed"
                    )
            object.__setattr__(self, "usage", usage)
        if self.continuation is not None:
            continuation = json_copy(_mapping(self.continuation, "agent continuation"))
            if not isinstance(continuation, dict):  # defensive for type checkers
                raise WorkflowValidationError("agent continuation must be an object")
            object.__setattr__(self, "continuation", continuation)

    @classmethod
    def from_mapping(cls, value: object) -> AgentDecision:
        raw = _mapping(value, "agent decision")
        if (
            set(raw) - {"message", "tool_calls", "output", "usage", "continuation"}
            or not {
                "message",
                "tool_calls",
            }
            <= set(raw)
            or not isinstance(raw["tool_calls"], list)
        ):
            raise WorkflowValidationError(
                "agent decision must contain message and tool_calls"
            )
        if not isinstance(raw["message"], str):
            raise WorkflowValidationError("agent decision message must be a string")
        return cls(
            raw["message"],
            tuple(ToolCall.from_mapping(item) for item in raw["tool_calls"]),
            _mapping(raw["output"], "agent structured output")
            if raw.get("output") is not None
            else None,
            _mapping(raw["usage"], "agent invocation usage")
            if raw.get("usage") is not None
            else None,
            _mapping(raw["continuation"], "agent continuation")
            if raw.get("continuation") is not None
            else None,
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "message": self.message,
            "tool_calls": [item.to_mapping() for item in self.tool_calls],
        }
        if self.output is not None:
            result["output"] = dict(self.output)
        if self.usage is not None:
            result["usage"] = dict(self.usage)
        if self.continuation is not None:
            result["continuation"] = json_copy(self.continuation)
        return result


@dataclass(frozen=True)
class ValueBinding:
    """Bind one source value to a target JSON Pointer in a v3 node input."""

    source: str
    pointer: str = ""
    step_id: str | None = None
    value: JsonValue = None

    def __post_init__(self) -> None:
        if self.source not in _BINDING_SOURCES:
            raise WorkflowValidationError("binding source is unsupported")
        object.__setattr__(
            self, "pointer", _json_pointer(self.pointer, "binding source pointer")
        )
        if self.source == "step_output":
            if self.step_id is None:
                raise WorkflowValidationError(
                    "step_output binding requires a source step_id"
                )
            object.__setattr__(
                self, "step_id", _identifier(self.step_id, "binding source step_id")
            )
        elif self.step_id is not None:
            raise WorkflowValidationError(
                "only step_output bindings may declare step_id"
            )
        if self.source == "literal":
            object.__setattr__(self, "value", json_copy(self.value))
            if contains_secret_name(self.value):
                raise WorkflowValidationError(
                    "literal bindings must not contain credentials"
                )
            if self.pointer:
                raise WorkflowValidationError(
                    "literal bindings do not use a source pointer"
                )
        elif self.value is not None:
            raise WorkflowValidationError("only literal bindings may declare a value")

    @classmethod
    def from_mapping(cls, value: object) -> ValueBinding:
        raw = _mapping(value, "value binding")
        if "source" not in raw or set(raw) - {
            "source",
            "pointer",
            "step_id",
            "value",
        }:
            raise WorkflowValidationError("value binding has unknown or missing fields")
        source = raw["source"]
        if not isinstance(source, str):
            raise WorkflowValidationError("binding source must be a string")
        if source == "literal" and "value" not in raw:
            raise WorkflowValidationError("literal binding requires value")
        if source != "literal" and "value" in raw:
            raise WorkflowValidationError("only literal bindings may contain value")
        pointer = raw.get("pointer", "")
        step_id = raw.get("step_id")
        if not isinstance(pointer, str) or (
            step_id is not None and not isinstance(step_id, str)
        ):
            raise WorkflowValidationError("binding pointer and step_id must be strings")
        return cls(
            source=source,
            pointer=pointer,
            step_id=step_id,
            value=raw.get("value"),
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {"source": self.source}
        if self.pointer:
            result["pointer"] = self.pointer
        if self.step_id is not None:
            result["step_id"] = self.step_id
        if self.source == "literal":
            result["value"] = self.value
        return result


def _bindings(
    value: Mapping[str, ValueBinding | Mapping[str, JsonValue]], label: str
) -> dict[str, ValueBinding]:
    if not isinstance(value, Mapping):
        raise WorkflowValidationError(f"{label} must be an object")
    result: dict[str, ValueBinding] = {}
    for raw_target, raw_binding in value.items():
        target = _json_pointer(raw_target, f"{label} target", allow_root=False)
        binding = (
            raw_binding
            if isinstance(raw_binding, ValueBinding)
            else ValueBinding.from_mapping(raw_binding)
        )
        result[target] = binding
    ordered = sorted(result, key=lambda item: (item.count("/"), item))
    for index, target in enumerate(ordered):
        for parent in ordered[:index]:
            if target.startswith(parent + "/"):
                raise WorkflowValidationError(
                    f"{label} targets must not overlap at {parent} and {target}"
                )
    return result


@dataclass(frozen=True)
class NodeContract:
    """Complete, portable input/output boundary for one v3 node."""

    input_schema: Mapping[str, JsonValue]
    output_schema: Mapping[str, JsonValue]
    bindings: Mapping[str, ValueBinding | Mapping[str, JsonValue]] = field(
        default_factory=dict
    )
    timeout_seconds: float | None = None
    # ``None`` is reserved for deserializing pre-budget v3/v4 definitions so
    # their canonical mapping, digest, and resume semantics remain unchanged.
    max_input_bytes: int | None = DEFAULT_NODE_INPUT_BYTES
    max_output_bytes: int | None = DEFAULT_NODE_OUTPUT_BYTES

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "input_schema",
            _strict_object_schema(self.input_schema, "node input_schema"),
        )
        object.__setattr__(
            self,
            "output_schema",
            _strict_object_schema(self.output_schema, "node output_schema"),
        )
        object.__setattr__(self, "bindings", _bindings(self.bindings, "node bindings"))
        timeout = self.timeout_seconds
        if timeout is not None and (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not math.isfinite(float(timeout))
            or timeout <= 0
        ):
            raise WorkflowValidationError("node timeout_seconds must be positive")
        if timeout is not None:
            object.__setattr__(self, "timeout_seconds", float(timeout))
        for field_name in ("max_input_bytes", "max_output_bytes"):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 1 <= value <= _MAX_PAYLOAD_BUDGET_BYTES
            ):
                raise WorkflowValidationError(
                    f"{field_name} must be between 1 byte and 256 MiB"
                )

    @classmethod
    def from_mapping(cls, value: object) -> NodeContract:
        raw = _mapping(value, "node contract")
        required = {"input_schema", "output_schema", "bindings"}
        if not required <= set(raw) or set(raw) - (
            required | {"timeout_seconds", "max_input_bytes", "max_output_bytes"}
        ):
            raise WorkflowValidationError("node contract has unknown or missing fields")
        raw_bindings = raw["bindings"]
        if not isinstance(raw_bindings, Mapping):
            raise WorkflowValidationError("node bindings must be an object")
        timeout = raw.get("timeout_seconds")
        return cls(
            input_schema=_mapping(raw["input_schema"], "node input_schema"),
            output_schema=_mapping(raw["output_schema"], "node output_schema"),
            bindings={
                str(target): ValueBinding.from_mapping(binding)
                for target, binding in raw_bindings.items()
            },
            timeout_seconds=timeout,  # type: ignore[arg-type]
            max_input_bytes=raw.get("max_input_bytes"),  # type: ignore[arg-type]
            max_output_bytes=raw.get("max_output_bytes"),  # type: ignore[arg-type]
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
            "bindings": {
                target: binding.to_mapping()
                for target, binding in self.bindings.items()
            },
            "timeout_seconds": self.timeout_seconds,
        }
        if self.max_input_bytes is not None:
            result["max_input_bytes"] = self.max_input_bytes
        if self.max_output_bytes is not None:
            result["max_output_bytes"] = self.max_output_bytes
        return result


@dataclass(frozen=True)
class ParallelBranch:
    """A named, isolated v3 parallel branch."""

    name: str
    steps: tuple[StepSpec, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _identifier(self.name, "parallel branch name"))
        object.__setattr__(self, "steps", tuple(self.steps))
        if not self.steps or any(not isinstance(step, StepSpec) for step in self.steps):
            raise WorkflowValidationError("parallel branch must contain workflow steps")

    @classmethod
    def from_mapping(cls, value: object) -> ParallelBranch:
        raw = _mapping(value, "parallel branch")
        if set(raw) != {"name", "steps"}:
            raise WorkflowValidationError("parallel branch requires name and steps")
        return cls(
            _identifier(raw["name"], "parallel branch name"),
            tuple(
                StepSpec.from_mapping(item)
                for item in _sequence(raw["steps"], "parallel branch steps")
            ),
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "name": self.name,
            "steps": [step.to_mapping() for step in self.steps],
        }


@dataclass(frozen=True)
class WorkflowExecutionPolicy:
    max_concurrency: int = 4
    run_timeout_seconds: float | None = None
    recovery_query_timeout_seconds: float | None = None
    recovery_round_timeout_seconds: float | None = None
    # ``None`` preserves definitions created before retained-state budgets.
    max_retained_state_bytes: int | None = DEFAULT_RUN_RETAINED_STATE_BYTES

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_concurrency, int)
            or isinstance(self.max_concurrency, bool)
            or not 1 <= self.max_concurrency <= 1_024
        ):
            raise WorkflowValidationError("max_concurrency must be between 1 and 1024")
        timeout = self.run_timeout_seconds
        if timeout is not None and (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not math.isfinite(float(timeout))
            or timeout <= 0
        ):
            raise WorkflowValidationError("run_timeout_seconds must be positive")
        if timeout is not None:
            object.__setattr__(self, "run_timeout_seconds", float(timeout))
        for field_name in (
            "recovery_query_timeout_seconds",
            "recovery_round_timeout_seconds",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or value <= 0
            ):
                raise WorkflowValidationError(f"{field_name} must be positive")
            if value is not None:
                object.__setattr__(self, field_name, float(value))
        if self.max_retained_state_bytes is not None and (
            not isinstance(self.max_retained_state_bytes, int)
            or isinstance(self.max_retained_state_bytes, bool)
            or not 1 <= self.max_retained_state_bytes <= _MAX_PAYLOAD_BUDGET_BYTES
        ):
            raise WorkflowValidationError(
                "max_retained_state_bytes must be between 1 byte and 256 MiB"
            )

    @classmethod
    def from_mapping(cls, value: object) -> WorkflowExecutionPolicy:
        raw = _mapping(value, "workflow execution policy")
        if set(raw) - {
            "max_concurrency",
            "run_timeout_seconds",
            "recovery_query_timeout_seconds",
            "recovery_round_timeout_seconds",
            "max_retained_state_bytes",
        }:
            raise WorkflowValidationError(
                "workflow execution policy has unknown or missing fields"
            )
        timeout = raw.get("run_timeout_seconds")
        return cls(
            max_concurrency=raw.get("max_concurrency", 4),  # type: ignore[arg-type]
            run_timeout_seconds=timeout,  # type: ignore[arg-type]
            recovery_query_timeout_seconds=raw.get("recovery_query_timeout_seconds"),  # type: ignore[arg-type]
            recovery_round_timeout_seconds=raw.get("recovery_round_timeout_seconds"),  # type: ignore[arg-type]
            max_retained_state_bytes=raw.get("max_retained_state_bytes"),  # type: ignore[arg-type]
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "max_concurrency": self.max_concurrency,
            "run_timeout_seconds": self.run_timeout_seconds,
        }
        if self.max_retained_state_bytes is not None:
            result["max_retained_state_bytes"] = self.max_retained_state_bytes
        # Keep the v3/v4 wire representation stable for existing definitions.
        # Recovery limits are opt-in overrides; the runtime has conservative
        # defaults when they are absent.
        if self.recovery_query_timeout_seconds is not None:
            result["recovery_query_timeout_seconds"] = (
                self.recovery_query_timeout_seconds
            )
        if self.recovery_round_timeout_seconds is not None:
            result["recovery_round_timeout_seconds"] = (
                self.recovery_round_timeout_seconds
            )
        return result


@dataclass(frozen=True)
class ApprovalDecision:
    approval_id: str
    approved: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "approval_id", _identifier(self.approval_id, "approval_id")
        )
        if not isinstance(self.approved, bool):
            raise WorkflowValidationError("approval decision must be boolean")

    @classmethod
    def from_mapping(cls, value: object) -> ApprovalDecision:
        raw = _mapping(value, "approval decision")
        if set(raw) != {"approval_id", "approved"}:
            raise WorkflowValidationError(
                "approval decision requires approval_id and approved"
            )
        return cls(
            approval_id=raw["approval_id"],  # type: ignore[arg-type]
            approved=raw["approved"],  # type: ignore[arg-type]
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        return {"approval_id": self.approval_id, "approved": self.approved}


@dataclass(frozen=True)
class StepSpec:
    """One declarative step; nested branches are part of the portable AST."""

    step_id: str
    kind: StepKind
    config: Mapping[str, JsonValue] = field(default_factory=dict)
    children: tuple[tuple[StepSpec, ...], ...] = ()
    contract: NodeContract | None = None
    branch_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _identifier(self.step_id, "step_id"))
        try:
            kind = self.kind if isinstance(self.kind, StepKind) else StepKind(self.kind)
        except ValueError as error:
            raise WorkflowValidationError("Unsupported step kind") from error
        object.__setattr__(self, "kind", kind)
        config = _mapping(self.config, f"configuration for {self.step_id}")
        if contains_secret_name(config):
            raise WorkflowValidationError(
                "workflow configuration must use credential references, not credentials"
            )
        object.__setattr__(self, "config", config)
        children = tuple(tuple(branch) for branch in self.children)
        if any(
            not isinstance(step, StepSpec) for branch in children for step in branch
        ):
            raise WorkflowValidationError("step children must be workflow steps")
        object.__setattr__(self, "children", children)
        if self.contract is not None and not isinstance(self.contract, NodeContract):
            raise WorkflowValidationError("step contract must be a NodeContract")
        branch_names = tuple(self.branch_names)
        object.__setattr__(self, "branch_names", branch_names)
        if branch_names:
            if self.kind is not StepKind.PARALLEL or len(branch_names) != len(children):
                raise WorkflowValidationError(
                    "parallel branch names must match the declared branches"
                )
            normalized = tuple(
                _identifier(name, "parallel branch name") for name in branch_names
            )
            if len(set(normalized)) != len(normalized):
                raise WorkflowValidationError("parallel branch names must be unique")
            object.__setattr__(self, "branch_names", normalized)
        self._validate_shape()

    def _validate_shape(self) -> None:
        config = self.config
        if self.kind is StepKind.TOOL:
            if self.children or set(config) - {
                "tool",
                "arguments",
                "approval_required",
                "capabilities",
            }:
                raise WorkflowValidationError("tool step has unsupported fields")
            ToolReference.from_mapping(config.get("tool"))
            _mapping(config.get("arguments", {}), "tool arguments")
            if "approval_required" in config and not isinstance(
                config["approval_required"], bool
            ):
                raise WorkflowValidationError("approval_required must be boolean")
            if "capabilities" in config:
                ToolRecoveryCapabilities.from_mapping(config["capabilities"])
        elif self.kind is StepKind.AGENT:
            if self.children or set(config) - {
                "driver",
                "input",
                "max_turns",
                "tools",
                "budget",
            }:
                raise WorkflowValidationError("agent step has unsupported fields")
            _identifier(config.get("driver"), "agent driver")
            _mapping(config.get("input", {}), "agent input")
            turns = config.get("max_turns", 1)
            if (
                not isinstance(turns, int)
                or isinstance(turns, bool)
                or not 1 <= turns <= 32
            ):
                raise WorkflowValidationError(
                    "agent max_turns must be between 1 and 32"
                )
            tools = config.get("tools", [])
            if not isinstance(tools, list) or len(tools) > 32:
                raise WorkflowValidationError(
                    "agent tools must be a list of at most 32 contracts"
                )
            identities: list[tuple[str, str, str]] = []
            names: list[str] = []
            kinds: set[str] = set()
            model_contract_bytes = 0
            for item in tools:
                if isinstance(item, Mapping) and "name" in item:
                    contract = AgentToolSpec.from_mapping(item)
                    reference = contract.tool
                    names.append(contract.name)
                    kinds.add("contract")
                    model_contract_bytes += contract.model_contract_size
                else:
                    reference = ToolReference.from_mapping(item)
                    kinds.add("reference")
                identities.append(
                    (reference.tool_id, reference.version, reference.package_digest)
                )
            if len(kinds) > 1:
                raise WorkflowValidationError(
                    "agent tools must not mix references and contracts"
                )
            if len(identities) != len(set(identities)):
                raise WorkflowValidationError("agent tool references must be unique")
            if len(names) != len(set(names)):
                raise WorkflowValidationError("agent tool names must be unique")
            if (
                kinds == {"contract"}
                and model_contract_bytes + max(len(names) - 1, 0) + 2
                > _MAX_AGENT_STEP_CONTRACT_BYTES
            ):
                raise WorkflowValidationError(
                    "agent step tool contracts exceed 256 KiB"
                )
            if "budget" in config:
                AgentBudget.from_mapping(config["budget"])
        elif self.kind is StepKind.APPROVAL:
            if (
                self.children
                or set(config) != {"prompt"}
                or not isinstance(config["prompt"], str)
            ):
                raise WorkflowValidationError("approval step requires a prompt")
        elif self.kind is StepKind.CONDITION:
            if set(config) != {"path", "equals"} or len(self.children) != 2:
                raise WorkflowValidationError(
                    "condition requires path, equals, then, and otherwise branches"
                )
            if not isinstance(config["path"], str) or not config["path"].startswith(
                "/"
            ):
                raise WorkflowValidationError("condition path must be a JSON pointer")
        elif self.kind is StepKind.PARALLEL:
            if (
                set(config) - {"failure_policy", "max_concurrency"}
                or len(self.children) < 2
            ):
                raise WorkflowValidationError("parallel requires at least two branches")
            failure_policy = config.get("failure_policy", "fail_fast")
            if failure_policy not in {"fail_fast", "wait_all"}:
                raise WorkflowValidationError(
                    "parallel failure_policy must be fail_fast or wait_all"
                )
            local_limit = config.get("max_concurrency")
            if local_limit is not None and (
                not isinstance(local_limit, int)
                or isinstance(local_limit, bool)
                or not 1 <= local_limit <= 1_024
            ):
                raise WorkflowValidationError(
                    "parallel max_concurrency must be between 1 and 1024"
                )
        elif self.kind is StepKind.SUBWORKFLOW:
            if self.children or set(config) != {"workflow_id", "version"}:
                raise WorkflowValidationError(
                    "subworkflow requires workflow_id and version"
                )
            _identifier(config["workflow_id"], "subworkflow workflow_id")
            _version(config["version"], "subworkflow version")

    @classmethod
    def from_mapping(cls, value: object) -> StepSpec:
        raw = _mapping(value, "step")
        if not {"id", "kind"} <= set(raw):
            raise WorkflowValidationError("step requires id and kind")
        kind = StepKind(raw["kind"])
        allowed = {"id", "kind", "config", "contract"}
        children: tuple[tuple[StepSpec, ...], ...] = ()
        branch_names: tuple[str, ...] = ()
        if kind is StepKind.CONDITION:
            allowed |= {"then", "otherwise"}
            children = (
                tuple(
                    cls.from_mapping(item)
                    for item in _sequence(raw.get("then"), "then")
                ),
                tuple(
                    cls.from_mapping(item)
                    for item in _sequence(raw.get("otherwise"), "otherwise")
                ),
            )
        elif kind is StepKind.PARALLEL:
            allowed |= {"branches"}
            branches = _sequence(raw.get("branches"), "branches")
            if branches and all(isinstance(branch, Mapping) for branch in branches):
                parsed: list[tuple[StepSpec, ...]] = []
                names: list[str] = []
                for branch in branches:
                    branch_raw = _mapping(branch, "parallel branch")
                    if set(branch_raw) != {"name", "steps"}:
                        raise WorkflowValidationError(
                            "named parallel branch requires name and steps"
                        )
                    names.append(
                        _identifier(branch_raw["name"], "parallel branch name")
                    )
                    parsed.append(
                        tuple(
                            cls.from_mapping(item)
                            for item in _sequence(
                                branch_raw["steps"], "parallel branch steps"
                            )
                        )
                    )
                children = tuple(parsed)
                branch_names = tuple(names)
            elif any(isinstance(branch, Mapping) for branch in branches):
                raise WorkflowValidationError(
                    "parallel branches must be either all named or all legacy lists"
                )
            else:
                children = tuple(
                    tuple(
                        cls.from_mapping(item)
                        for item in _sequence(branch, "parallel branch")
                    )
                    for branch in branches
                )
        if set(raw) - allowed:
            raise WorkflowValidationError("step has unknown fields")
        return cls(
            step_id=_identifier(raw["id"], "step id"),
            kind=kind,
            config=_mapping(raw.get("config", {}), "step config"),
            children=children,
            contract=(
                NodeContract.from_mapping(raw["contract"])
                if raw.get("contract") is not None
                else None
            ),
            branch_names=branch_names,
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "id": self.step_id,
            "kind": self.kind.value,
            "config": dict(self.config),
        }
        if self.contract is not None:
            result["contract"] = self.contract.to_mapping()
        if self.kind is StepKind.CONDITION:
            result["then"] = [step.to_mapping() for step in self.children[0]]
            result["otherwise"] = [step.to_mapping() for step in self.children[1]]
        elif self.kind is StepKind.PARALLEL:
            if self.branch_names:
                result["branches"] = [
                    {
                        "name": name,
                        "steps": [step.to_mapping() for step in branch],
                    }
                    for name, branch in zip(
                        self.branch_names, self.children, strict=True
                    )
                ]
            else:
                result["branches"] = [
                    [step.to_mapping() for step in branch] for branch in self.children
                ]
        return result


def _sequence(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise WorkflowValidationError(f"{label} must be a list")
    return value


@dataclass(frozen=True)
class WorkflowSpec:
    """The versioned portable format used by API, Python, YAML, and JSON."""

    workflow_id: str
    version: str
    steps: tuple[StepSpec, ...]
    schema_version: str = V4_SCHEMA_VERSION
    input_schema: Mapping[str, JsonValue] | None = None
    output_schema: Mapping[str, JsonValue] | None = None
    output_bindings: Mapping[str, ValueBinding | Mapping[str, JsonValue]] = field(
        default_factory=dict
    )
    execution_policy: WorkflowExecutionPolicy | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "workflow_id", _identifier(self.workflow_id, "workflow_id")
        )
        object.__setattr__(self, "version", _version(self.version, "workflow version"))
        steps = tuple(self.steps)
        if any(not isinstance(step, StepSpec) for step in steps):
            raise WorkflowValidationError("workflow steps must be StepSpec values")
        object.__setattr__(self, "steps", steps)
        if self.schema_version not in {
            "v6-light-workflow-v1",
            "v6-light-workflow-v2",
            V3_SCHEMA_VERSION,
            V4_SCHEMA_VERSION,
        }:
            raise WorkflowValidationError("Unsupported workflow schema_version")
        if not steps:
            raise WorkflowValidationError("workflow must contain at least one step")
        all_steps = list(_walk(self.steps))
        if len(all_steps) > 1_000:
            raise WorkflowValidationError("workflow may contain at most 1000 steps")
        if len({step.step_id for step in all_steps}) != len(all_steps):
            raise WorkflowValidationError(
                "step ids must be unique across nested branches"
            )
        if _depth(self.steps) > 16:
            raise WorkflowValidationError("workflow nesting is limited to 16 levels")
        if self.schema_version in ASYNC_SCHEMA_VERSIONS:
            if self.input_schema is None or self.output_schema is None:
                raise WorkflowValidationError(
                    "v3/v4 workflows require input_schema and output_schema"
                )
            object.__setattr__(
                self,
                "input_schema",
                _strict_object_schema(self.input_schema, "workflow input_schema"),
            )
            object.__setattr__(
                self,
                "output_schema",
                _strict_object_schema(self.output_schema, "workflow output_schema"),
            )
            object.__setattr__(
                self,
                "output_bindings",
                _bindings(self.output_bindings, "workflow output_bindings"),
            )
            policy = self.execution_policy or WorkflowExecutionPolicy()
            if not isinstance(policy, WorkflowExecutionPolicy):
                raise WorkflowValidationError(
                    "v3/v4 execution_policy must be a WorkflowExecutionPolicy"
                )
            object.__setattr__(self, "execution_policy", policy)
            for step in all_steps:
                if step.contract is None:
                    raise WorkflowValidationError(
                        f"v3/v4 step {step.step_id} requires a complete NodeContract"
                    )
                if step.kind is StepKind.PARALLEL and not step.branch_names:
                    raise WorkflowValidationError(
                        "v3 parallel steps require named branches"
                    )
                if step.kind is StepKind.TOOL and step.config.get("arguments"):
                    raise WorkflowValidationError(
                        "v3 tool steps use contract bindings, not argument templates"
                    )
                if step.kind is StepKind.AGENT and step.config.get("input"):
                    raise WorkflowValidationError(
                        "v3 agent steps use contract bindings, not input templates"
                    )
                if self.schema_version == V4_SCHEMA_VERSION:
                    if step.kind is StepKind.TOOL and "capabilities" not in step.config:
                        raise WorkflowValidationError(
                            f"v4 tool step {step.step_id} requires recovery capabilities"
                        )
                    if step.kind is StepKind.AGENT:
                        if "budget" not in step.config:
                            raise WorkflowValidationError(
                                f"v4 Agent step {step.step_id} requires a durable budget"
                            )
                        for raw_tool in step.config.get("tools", []):
                            tool_contract = AgentToolSpec.from_mapping(raw_tool)
                            if tool_contract.capabilities is None:
                                raise WorkflowValidationError(
                                    f"v4 Agent tool {tool_contract.name} requires recovery capabilities"
                                )
                elif any(
                    (step.kind is StepKind.TOOL and "capabilities" in step.config)
                    or (step.kind is StepKind.AGENT and "budget" in step.config)
                    for step in all_steps
                ):
                    raise WorkflowValidationError(
                        "tool recovery capabilities and Agent budgets require schema version v4"
                    )
        elif (
            any(
                value is not None
                for value in (
                    self.input_schema,
                    self.output_schema,
                    self.execution_policy,
                )
            )
            or self.output_bindings
        ):
            raise WorkflowValidationError(
                "v3 workflow fields require schema version v3"
            )
        elif any(step.contract is not None or step.branch_names for step in all_steps):
            raise WorkflowValidationError("v3 step contracts require schema version v3")
        for step in all_steps:
            if step.kind is not StepKind.AGENT:
                continue
            for item in step.config.get("tools", []):  # type: ignore[union-attr]
                is_contract = isinstance(item, Mapping) and "name" in item
                if self.schema_version == "v6-light-workflow-v1" and is_contract:
                    raise WorkflowValidationError(
                        "v1 agent steps require exact tool references"
                    )
                if self.schema_version == "v6-light-workflow-v2" and not is_contract:
                    raise WorkflowValidationError(
                        "v2 agent steps require complete AgentToolSpec contracts"
                    )
                if self.schema_version in ASYNC_SCHEMA_VERSIONS and not is_contract:
                    raise WorkflowValidationError(
                        "v3 agent steps require complete AgentToolSpec contracts"
                    )

    @classmethod
    def from_mapping(cls, value: object) -> WorkflowSpec:
        raw = _mapping(value, "workflow")
        schema_version = str(raw.get("schema_version", ""))
        legacy_fields = {"schema_version", "workflow_id", "version", "steps"}
        v3_fields = legacy_fields | {
            "input_schema",
            "output_schema",
            "output_bindings",
            "execution_policy",
        }
        expected = (
            v3_fields if schema_version in ASYNC_SCHEMA_VERSIONS else legacy_fields
        )
        if set(raw) != expected:
            raise WorkflowValidationError("workflow has unknown or missing fields")
        raw_output_bindings = raw.get("output_bindings", {})
        if not isinstance(raw_output_bindings, Mapping):
            raise WorkflowValidationError("workflow output_bindings must be an object")
        return cls(
            workflow_id=_identifier(raw["workflow_id"], "workflow_id"),
            version=_version(raw["version"], "workflow version"),
            steps=tuple(
                StepSpec.from_mapping(item) for item in _sequence(raw["steps"], "steps")
            ),
            schema_version=schema_version,
            input_schema=(
                _mapping(raw["input_schema"], "workflow input_schema")
                if schema_version in ASYNC_SCHEMA_VERSIONS
                else None
            ),
            output_schema=(
                _mapping(raw["output_schema"], "workflow output_schema")
                if schema_version in ASYNC_SCHEMA_VERSIONS
                else None
            ),
            output_bindings={
                str(target): ValueBinding.from_mapping(binding)
                for target, binding in raw_output_bindings.items()
            },
            execution_policy=(
                WorkflowExecutionPolicy.from_mapping(raw["execution_policy"])
                if schema_version in ASYNC_SCHEMA_VERSIONS
                else None
            ),
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "schema_version": self.schema_version,
            "workflow_id": self.workflow_id,
            "version": self.version,
            "steps": [step.to_mapping() for step in self.steps],
        }
        if self.schema_version in ASYNC_SCHEMA_VERSIONS:
            assert self.input_schema is not None
            assert self.output_schema is not None
            assert self.execution_policy is not None
            result.update(
                {
                    "input_schema": dict(self.input_schema),
                    "output_schema": dict(self.output_schema),
                    "output_bindings": {
                        target: binding.to_mapping()
                        for target, binding in self.output_bindings.items()
                    },
                    "execution_policy": self.execution_policy.to_mapping(),
                }
            )
        return result

    @property
    def definition_digest(self) -> str:
        return digest(self.to_mapping())


def workflow_semantics(
    workflow_or_schema: WorkflowSpec | str,
) -> WorkflowSemantics:
    """Return the durable execution semantics for a workflow schema version.

    Existing definitions retain their explicit schema version. This function
    is the discovery path used by ``check`` and ``plan`` to report that frozen
    behavior independently of the current Python constructor default.
    """

    schema_version = (
        workflow_or_schema.schema_version
        if isinstance(workflow_or_schema, WorkflowSpec)
        else workflow_or_schema
    )
    if schema_version == V1_SCHEMA_VERSION:
        return WorkflowSemantics(
            schema_version,
            False,
            False,
            False,
            False,
            "legacy_terminal_cancel",
            "legacy_single_action_reconcile",
            False,
        )
    if schema_version == V2_SCHEMA_VERSION:
        return WorkflowSemantics(
            schema_version,
            False,
            False,
            True,
            False,
            "legacy_terminal_cancel",
            "legacy_single_action_reconcile",
            False,
        )
    if schema_version == V3_SCHEMA_VERSION:
        return WorkflowSemantics(
            schema_version,
            False,
            True,
            True,
            True,
            "request_cancelled_reconciling",
            "bounded_observational_reconcile",
            False,
        )
    if schema_version == V4_SCHEMA_VERSION:
        return WorkflowSemantics(
            schema_version,
            True,
            True,
            True,
            True,
            "request_cancelled_reconciling",
            "bounded_observational_reconcile",
            True,
        )
    raise WorkflowValidationError("Unsupported workflow schema_version")


def _walk(steps: Sequence[StepSpec]):
    for step in steps:
        yield step
        for branch in step.children:
            yield from _walk(branch)


def _depth(steps: Sequence[StepSpec], current: int = 1) -> int:
    if not steps:
        return current
    return max(
        [
            current,
            *(
                _depth(branch, current + 1)
                for step in steps
                for branch in step.children
            ),
        ]
    )


def tool_step(
    step_id: str,
    tool: ToolReference,
    arguments: Mapping[str, JsonValue] | None = None,
    *,
    approval_required: bool = True,
    contract: NodeContract | None = None,
    capabilities: ToolRecoveryCapabilities | None = None,
) -> StepSpec:
    config: dict[str, JsonValue] = {
        "tool": tool.to_mapping(),
        "arguments": dict(arguments or {}),
        "approval_required": approval_required,
    }
    if capabilities is not None:
        config["capabilities"] = capabilities.to_mapping()
    return StepSpec(
        step_id,
        StepKind.TOOL,
        config,
        contract=contract,
    )


def agent_step(
    step_id: str,
    driver: str,
    input: Mapping[str, JsonValue] | None = None,
    *,
    max_turns: int = 4,
    tools: Sequence[ToolReference | AgentToolSpec] = (),
    contract: NodeContract | None = None,
    budget: AgentBudget | None = None,
) -> StepSpec:
    config: dict[str, JsonValue] = {
        "driver": driver,
        "input": dict(input or {}),
        "max_turns": max_turns,
        "tools": [item.to_mapping() for item in tools],
    }
    if budget is not None:
        config["budget"] = budget.to_mapping()
    return StepSpec(
        step_id,
        StepKind.AGENT,
        config,
        contract=contract,
    )


def approval_step(
    step_id: str, prompt: str, *, contract: NodeContract | None = None
) -> StepSpec:
    return StepSpec(step_id, StepKind.APPROVAL, {"prompt": prompt}, contract=contract)


def condition_step(
    step_id: str,
    path: str,
    equals: JsonValue,
    then: Sequence[StepSpec],
    otherwise: Sequence[StepSpec],
    *,
    contract: NodeContract | None = None,
) -> StepSpec:
    return StepSpec(
        step_id,
        StepKind.CONDITION,
        {"path": path, "equals": equals},
        (tuple(then), tuple(otherwise)),
        contract=contract,
    )


def parallel_step(
    step_id: str,
    branches: Sequence[Sequence[StepSpec] | ParallelBranch],
    *,
    contract: NodeContract | None = None,
    failure_policy: str = "fail_fast",
    max_concurrency: int | None = None,
) -> StepSpec:
    raw = tuple(branches)
    if raw and all(isinstance(branch, ParallelBranch) for branch in raw):
        named = tuple(branch for branch in raw if isinstance(branch, ParallelBranch))
        children = tuple(branch.steps for branch in named)
        names = tuple(branch.name for branch in named)
        config: dict[str, JsonValue] = {"failure_policy": failure_policy}
        if max_concurrency is not None:
            config["max_concurrency"] = max_concurrency
        return StepSpec(
            step_id,
            StepKind.PARALLEL,
            config,
            children,
            contract=contract,
            branch_names=names,
        )
    if any(isinstance(branch, ParallelBranch) for branch in raw):
        raise WorkflowValidationError(
            "parallel branches must not mix named and legacy forms"
        )
    if (
        contract is not None
        or failure_policy != "fail_fast"
        or max_concurrency is not None
    ):
        config = {"failure_policy": failure_policy}
        if max_concurrency is not None:
            config["max_concurrency"] = max_concurrency
    else:
        config = {}
    return StepSpec(
        step_id,
        StepKind.PARALLEL,
        config,
        tuple(tuple(branch) for branch in raw),  # type: ignore[arg-type]
        contract=contract,
    )


def subworkflow_step(
    step_id: str,
    workflow_id: str,
    version: str,
    *,
    contract: NodeContract | None = None,
) -> StepSpec:
    return StepSpec(
        step_id,
        StepKind.SUBWORKFLOW,
        {"workflow_id": workflow_id, "version": version},
        contract=contract,
    )


def workflow(
    workflow_id: str,
    version: str,
    steps: Sequence[StepSpec],
    *,
    schema_version: str = V4_SCHEMA_VERSION,
    input_schema: Mapping[str, JsonValue] | None = None,
    output_schema: Mapping[str, JsonValue] | None = None,
    output_bindings: Mapping[str, ValueBinding | Mapping[str, JsonValue]] | None = None,
    execution_policy: WorkflowExecutionPolicy | None = None,
) -> WorkflowSpec:
    return WorkflowSpec(
        workflow_id,
        version,
        tuple(steps),
        schema_version,
        input_schema,
        output_schema,
        output_bindings or {},
        execution_policy,
    )


def workflow_v4(
    workflow_id: str,
    version: str,
    steps: Sequence[StepSpec],
    *,
    input_schema: Mapping[str, JsonValue],
    output_schema: Mapping[str, JsonValue],
    output_bindings: Mapping[str, ValueBinding | Mapping[str, JsonValue]] | None = None,
    execution_policy: WorkflowExecutionPolicy | None = None,
) -> WorkflowSpec:
    """Create a workflow with the recommended recoverable v4 semantics.

    This named constructor remains available for code written during the
    0.1.x transition. It is equivalent to the 0.2.x :func:`workflow` default.
    """

    return workflow(
        workflow_id,
        version,
        steps,
        schema_version=V4_SCHEMA_VERSION,
        input_schema=input_schema,
        output_schema=output_schema,
        output_bindings=output_bindings,
        execution_policy=execution_policy,
    )
