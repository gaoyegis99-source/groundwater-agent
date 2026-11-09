"""Developer-facing portability, authoring, and compatibility services.

The module is standard-library only at import time.  Copier, uv, and workflow
validation dependencies are loaded or invoked only by the explicit operation
that needs them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from importlib import metadata, resources
from pathlib import Path
from typing import Any

from .portable import (
    BUNDLE_V2,
    PortableBundle,
    _discard_import_staging_directory,
    _extract_entries,
    _manifest_reference,
    _new_import_staging_directory,
    _normalize_tools,
    _portable_agent_budget,
    _prepare_import_destination,
    _publish_import_directory,
    _read_dependency_lock,
    _read_bundle_entries,
    _require_declared_tools,
    export_bundle,
    read_bundle,
)
from .spec import (
    ASYNC_SCHEMA_VERSIONS,
    V4_SCHEMA_VERSION,
    JsonValue,
    StepKind,
    ToolReference,
    WorkflowSpec,
    WorkflowValidationError,
    canonical_json,
    contains_secret_name,
    json_copy,
    workflow_semantics,
)

PROJECT_SCHEMA_V1 = "v6-agent-project-v1"
TARGET_SCHEMA_V1 = "v6-portability-target-v1"
REPORT_SCHEMA_V1 = "v6-portability-report-v1"
ENVIRONMENT_REPORT_SCHEMA_V1 = "v6-environment-readiness-v1"
PLAN_SCHEMA_V1 = "v6-execution-plan-v1"

_PORTABLE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_REPORT_STATUSES = frozenset(
    {"compatible", "binding_required", "incompatible", "invalid"}
)
_CHECK_STATUSES = _REPORT_STATUSES | {"not_checked"}
_ENVIRONMENT_STATUSES = frozenset(
    {"ready", "binding_required", "incompatible", "invalid"}
)
_ENTRY_POINT = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}:[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
)
_CALLABLE_PATH = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_.]{0,255}:[A-Za-z_][A-Za-z0-9_]{0,127}$"
)
_IMPORT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,255}$")

# These are package names, not import paths.  The mapping deliberately stays
# small: custom integrations must declare their own distribution rather than
# being guessed from a logical provider name.
_KNOWN_DRIVER_DISTRIBUTIONS = {
    "pydantic-ai": "pydantic-ai-slim",
    "openai-agents": "openai-agents",
}

# The base distribution intentionally stays dependency-free.  These are the
# concrete runtime packages required only after a portable workflow has chosen
# an asynchronous V3/V4 execution contract.
_ASYNC_WORKFLOW_RUNTIME_DISTRIBUTIONS = ("anyio", "jsonschema")
_V4_STATE_RUNTIME_DISTRIBUTION = "immutables"


def _name(value: object, label: str) -> str:
    if not isinstance(value, str) or _PORTABLE_NAME.fullmatch(value) is None:
        raise WorkflowValidationError(f"{label} must be a portable identifier")
    return value


def _bounded_text(value: object, label: str, *, maximum: int = 4_096) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.encode("utf-8")) > maximum
    ):
        raise WorkflowValidationError(f"{label} must be a bounded non-empty string")
    return value


def agent_address_key(workflow_id: str, workflow_version: str, step_id: str) -> str:
    """Stable definition address for an Agent node, never an instance path."""

    return (
        f"{_name(workflow_id, 'Agent workflow_id')}"
        f"@{_name(workflow_version, 'Agent workflow version')}"
        f":{_name(step_id, 'Agent step_id')}"
    )


def parse_agent_address_key(value: object) -> tuple[str, str, str]:
    """Parse the compact project-file representation of an Agent address."""

    if not isinstance(value, str):
        raise WorkflowValidationError("Agent address must be a string")
    workflow, separator, remainder = value.partition("@")
    version, second_separator, step = remainder.partition(":")
    if not separator or not second_separator or not workflow or not version or not step:
        raise WorkflowValidationError("Agent address must use workflow@version:step")
    normalized = agent_address_key(workflow, version, step)
    if normalized != value:
        raise WorkflowValidationError("Agent address is not canonical")
    return workflow, version, step


@dataclass(frozen=True)
class DriverRuntimeDependency:
    """One provider dependency required to construct a bound model driver."""

    distribution: str
    import_name: str
    install_hint: str
    version: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "distribution", _name(self.distribution, "driver dependency")
        )
        if self.version is not None:
            object.__setattr__(
                self,
                "version",
                _bounded_text(self.version, "driver dependency version", maximum=128),
            )
        if (
            not isinstance(self.import_name, str)
            or _IMPORT_NAME.fullmatch(self.import_name) is None
        ):
            raise WorkflowValidationError("driver dependency import_name is invalid")
        object.__setattr__(
            self,
            "install_hint",
            _bounded_text(self.install_hint, "driver dependency install hint", maximum=512),
        )

    @classmethod
    def from_mapping(cls, value: object) -> DriverRuntimeDependency:
        if not isinstance(value, Mapping) or not {
            "distribution",
            "import_name",
            "install_hint",
        } <= set(value) or set(value) - {
            "distribution",
            "import_name",
            "install_hint",
            "version",
        }:
            raise WorkflowValidationError("driver runtime dependency is invalid")
        return cls(
            value["distribution"],  # type: ignore[arg-type]
            value["import_name"],  # type: ignore[arg-type]
            value["install_hint"],  # type: ignore[arg-type]
            value.get("version"),  # type: ignore[arg-type]
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "distribution": self.distribution,
            "import_name": self.import_name,
            "install_hint": self.install_hint,
        }
        if self.version is not None:
            result["version"] = self.version
        return result


def _pydantic_provider_dependencies(
    driver: str, model: str
) -> tuple[DriverRuntimeDependency, ...]:
    if driver != "pydantic-ai":
        return ()
    provider = model.partition(":")[0]
    aliases = {
        "google-gla": "google",
        "google-vertex": "google",
    }
    provider = aliases.get(provider, provider)
    packages = {
        "anthropic": ("anthropic", "anthropic"),
        "google": ("google-genai", "google.genai"),
        "openai": ("openai", "openai"),
    }
    package = packages.get(provider)
    if package is None:
        return ()
    distribution, import_name = package
    try:
        installed_version = metadata.version(distribution)
    except metadata.PackageNotFoundError:
        installed_version = None
    return (
        DriverRuntimeDependency(
            distribution,
            import_name,
            f"platform-lightweight-agent-core[pydantic-ai-{provider}]",
            installed_version,
        ),
    )


@dataclass(frozen=True)
class DriverBinding:
    """Target-owned model binding for one portable Agent step."""

    step_id: str
    driver: str
    driver_version: str
    model: str
    capabilities: frozenset[str] = frozenset()
    workflow_id: str | None = None
    workflow_version: str | None = None
    distribution: str | None = None
    entry_point: str | None = None
    runtime_dependencies: tuple[DriverRuntimeDependency, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _name(self.step_id, "driver step_id"))
        object.__setattr__(self, "driver", _name(self.driver, "driver name"))
        version = _bounded_text(self.driver_version, "driver version", maximum=256)
        if version.lower() == "latest":
            raise WorkflowValidationError("driver version must be fixed")
        object.__setattr__(self, "driver_version", version)
        object.__setattr__(
            self, "model", _bounded_text(self.model, "model", maximum=512)
        )
        capabilities = frozenset(
            _name(item, "driver capability") for item in self.capabilities
        )
        object.__setattr__(self, "capabilities", capabilities)
        if (self.workflow_id is None) != (self.workflow_version is None):
            raise WorkflowValidationError(
                "driver workflow_id and workflow_version must be supplied together"
            )
        if self.workflow_id is not None:
            object.__setattr__(
                self,
                "workflow_id",
                _name(self.workflow_id, "driver workflow_id"),
            )
            object.__setattr__(
                self,
                "workflow_version",
                _name(self.workflow_version, "driver workflow_version"),
            )

        if self.distribution is not None:
            object.__setattr__(
                self,
                "distribution",
                _name(self.distribution, "driver distribution"),
            )
        if self.entry_point is not None:
            if (
                not isinstance(self.entry_point, str)
                or _ENTRY_POINT.fullmatch(self.entry_point) is None
            ):
                raise WorkflowValidationError("driver entry_point must use group:name")
            object.__setattr__(self, "entry_point", self.entry_point)
        dependencies = tuple(self.runtime_dependencies) or _pydantic_provider_dependencies(
            self.driver, self.model
        )
        if any(not isinstance(item, DriverRuntimeDependency) for item in dependencies):
            raise WorkflowValidationError("driver runtime dependencies are invalid")
        if len({item.distribution for item in dependencies}) != len(dependencies):
            raise WorkflowValidationError("driver runtime dependencies must be unique")
        object.__setattr__(self, "runtime_dependencies", dependencies)

    @property
    def address(self) -> tuple[str, str, str] | None:
        if self.workflow_id is None or self.workflow_version is None:
            return None
        return self.workflow_id, self.workflow_version, self.step_id

    @property
    def binding_key(self) -> str:
        if self.address is None:
            return f"legacy:{self.step_id}"
        return agent_address_key(*self.address)

    @classmethod
    def from_mapping(cls, value: object) -> DriverBinding:
        required = {
            "step_id",
            "driver",
            "driver_version",
            "model",
            "capabilities",
        }
        optional = {
            "workflow_id",
            "workflow_version",
            "distribution",
            "entry_point",
            "runtime_dependencies",
        }
        if (
            not isinstance(value, Mapping)
            or not required <= set(value)
            or set(value) - required - optional
        ):
            raise WorkflowValidationError(
                "driver binding has unknown or missing fields"
            )
        capabilities = value["capabilities"]
        if not isinstance(capabilities, list) or any(
            not isinstance(item, str) for item in capabilities
        ):
            raise WorkflowValidationError("driver capabilities must be a list")
        runtime_dependencies = value.get("runtime_dependencies", [])
        if not isinstance(runtime_dependencies, list):
            raise WorkflowValidationError(
                "driver runtime_dependencies must be a list"
            )
        return cls(
            step_id=value["step_id"],  # type: ignore[arg-type]
            driver=value["driver"],  # type: ignore[arg-type]
            driver_version=value["driver_version"],  # type: ignore[arg-type]
            model=value["model"],  # type: ignore[arg-type]
            capabilities=frozenset(capabilities),
            workflow_id=value.get("workflow_id"),  # type: ignore[arg-type]
            workflow_version=value.get("workflow_version"),  # type: ignore[arg-type]
            distribution=value.get("distribution"),  # type: ignore[arg-type]
            entry_point=value.get("entry_point"),  # type: ignore[arg-type]
            runtime_dependencies=tuple(
                DriverRuntimeDependency.from_mapping(item)
                for item in runtime_dependencies
            ),
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "step_id": self.step_id,
            "driver": self.driver,
            "driver_version": self.driver_version,
            "model": self.model,
            "capabilities": sorted(self.capabilities),
        }
        if self.workflow_id is not None:
            result["workflow_id"] = self.workflow_id
            assert self.workflow_version is not None
            result["workflow_version"] = self.workflow_version
        if self.distribution is not None:
            result["distribution"] = self.distribution
        if self.entry_point is not None:
            result["entry_point"] = self.entry_point
        if self.runtime_dependencies:
            result["runtime_dependencies"] = [
                item.to_mapping() for item in self.runtime_dependencies
            ]
        return result


@dataclass(frozen=True)
class SubworkflowRequirement:
    """Exact published definition required by a portable parent workflow."""

    workflow_id: str
    version: str
    definition_digest: str
    workflow_schema_version: str
    input_schema: Mapping[str, JsonValue] | None = None
    output_schema: Mapping[str, JsonValue] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workflow_id", _name(self.workflow_id, "workflow_id"))
        object.__setattr__(self, "version", _name(self.version, "workflow version"))
        if (
            not isinstance(self.definition_digest, str)
            or _DIGEST.fullmatch(self.definition_digest) is None
        ):
            raise WorkflowValidationError("subworkflow definition_digest is invalid")
        _bounded_text(
            self.workflow_schema_version, "subworkflow schema version", maximum=128
        )
        for field_name in ("input_schema", "output_schema"):
            value = getattr(self, field_name)
            if value is not None:
                copied = json_copy(value)
                if not isinstance(copied, dict):
                    raise WorkflowValidationError(
                        f"subworkflow {field_name} must be an object"
                    )
                object.__setattr__(self, field_name, copied)

    @property
    def binding_key(self) -> str:
        return f"{self.workflow_id}@{self.version}"

    @classmethod
    def from_mapping(cls, value: object) -> SubworkflowRequirement:
        if not isinstance(value, Mapping):
            raise WorkflowValidationError("subworkflow requirement must be an object")
        required = {
            "workflow_id",
            "version",
            "definition_digest",
            "workflow_schema_version",
        }
        if not required <= set(value) or set(value) - required - {
            "input_schema",
            "output_schema",
        }:
            raise WorkflowValidationError("subworkflow requirement is incomplete")
        return cls(
            workflow_id=value["workflow_id"],  # type: ignore[arg-type]
            version=value["version"],  # type: ignore[arg-type]
            definition_digest=value["definition_digest"],  # type: ignore[arg-type]
            workflow_schema_version=value["workflow_schema_version"],  # type: ignore[arg-type]
            input_schema=value.get("input_schema"),  # type: ignore[arg-type]
            output_schema=value.get("output_schema"),  # type: ignore[arg-type]
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "workflow_id": self.workflow_id,
            "version": self.version,
            "definition_digest": self.definition_digest,
            "workflow_schema_version": self.workflow_schema_version,
        }
        if self.input_schema is not None:
            result["input_schema"] = dict(self.input_schema)
        if self.output_schema is not None:
            result["output_schema"] = dict(self.output_schema)
        return result


@dataclass(frozen=True)
class ToolDelivery:
    """Target-side evidence connecting a trusted tool to its implementation.

    The default development check verifies only the declared top-level module;
    it never imports application code and therefore cannot claim a reproducible
    implementation. An explicit host-trusted readiness probe may additionally
    load the full module and check its callable attribute, but it does not
    register or invoke the tool. A release delivery names a distribution, its
    PyPA entry point, and the wheel whose SHA-256 must equal the immutable
    ``ToolReference.package_digest``. Discovery is still not authorization:
    only ``RuntimeAssembly.tool_routes`` may execute a tool.
    """

    tool: ToolReference
    mode: str
    distribution: str | None = None
    entry_point: str | None = None
    wheel_path: str | None = None
    development_callable: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tool, ToolReference):
            raise WorkflowValidationError("tool delivery requires a ToolReference")
        if self.mode not in {"development", "release"}:
            raise WorkflowValidationError(
                "tool delivery mode must be development or release"
            )
        if self.mode == "development":
            if (
                not isinstance(self.development_callable, str)
                or _CALLABLE_PATH.fullmatch(self.development_callable) is None
            ):
                raise WorkflowValidationError(
                    "development tool delivery requires MODULE:CALLABLE"
                )
            if any(
                value is not None
                for value in (self.distribution, self.entry_point, self.wheel_path)
            ):
                raise WorkflowValidationError(
                    "development tool delivery cannot claim release artifacts"
                )
            return
        if not isinstance(self.distribution, str):
            raise WorkflowValidationError(
                "release tool delivery requires a distribution"
            )
        if (
            not isinstance(self.entry_point, str)
            or _ENTRY_POINT.fullmatch(self.entry_point) is None
        ):
            raise WorkflowValidationError(
                "release tool delivery requires an entry point group:name"
            )
        if (
            not isinstance(self.wheel_path, str)
            or not self.wheel_path
            or "\x00" in self.wheel_path
        ):
            raise WorkflowValidationError("release tool delivery requires a wheel path")
        if len(self.wheel_path.encode("utf-8")) > 4_096:
            raise WorkflowValidationError("release tool wheel path is too long")
        if self.development_callable is not None:
            raise WorkflowValidationError(
                "release tool delivery cannot declare a development callable"
            )
        object.__setattr__(
            self, "distribution", _name(self.distribution, "tool distribution")
        )
        object.__setattr__(self, "entry_point", self.entry_point)
        object.__setattr__(self, "wheel_path", self.wheel_path)

    @property
    def identity(self) -> tuple[str, str, str]:
        return _tool_identity(self.tool)

    @classmethod
    def from_mapping(cls, value: object) -> ToolDelivery:
        required = {
            "tool",
            "mode",
            "distribution",
            "entry_point",
            "wheel_path",
            "development_callable",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise WorkflowValidationError("tool delivery has unknown or missing fields")
        return cls(
            ToolReference.from_mapping(value["tool"]),
            value["mode"],  # type: ignore[arg-type]
            value["distribution"],  # type: ignore[arg-type]
            value["entry_point"],  # type: ignore[arg-type]
            value["wheel_path"],  # type: ignore[arg-type]
            value["development_callable"],  # type: ignore[arg-type]
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "tool": self.tool.to_mapping(),
            "mode": self.mode,
            "distribution": self.distribution,
            "entry_point": self.entry_point,
            "wheel_path": self.wheel_path,
            "development_callable": self.development_callable,
        }


def _tool_identity(reference: ToolReference) -> tuple[str, str, str]:
    return reference.tool_id, reference.version, reference.package_digest


@dataclass(frozen=True)
class PortableTarget:
    """Credential-free bindings and capabilities of an import destination."""

    trusted_tools: tuple[ToolReference, ...] = ()
    allowed_permissions: frozenset[str] = frozenset()
    credential_bindings: frozenset[str] = frozenset()
    driver_bindings: tuple[DriverBinding, ...] = ()
    subworkflow_bindings: Mapping[str, Mapping[str, JsonValue]] = field(
        default_factory=dict
    )
    python_executable: str | None = None
    target_kind: str = "v6"
    importer_features: frozenset[str] = frozenset()
    tool_deliveries: tuple[ToolDelivery, ...] = ()

    def __post_init__(self) -> None:
        if self.target_kind not in {"v6", "v7"}:
            raise WorkflowValidationError("target kind must be v6 or v7")
        tools = tuple(self.trusted_tools)
        if any(not isinstance(item, ToolReference) for item in tools):
            raise WorkflowValidationError(
                "target tools must be exact ToolReference values"
            )
        identities = {
            (item.tool_id, item.version, item.package_digest) for item in tools
        }
        if len(identities) != len(tools):
            raise WorkflowValidationError("target tools must be unique")
        object.__setattr__(self, "trusted_tools", tools)
        deliveries = tuple(self.tool_deliveries)
        if any(not isinstance(item, ToolDelivery) for item in deliveries):
            raise WorkflowValidationError("target tool deliveries are invalid")
        delivery_identities = {item.identity for item in deliveries}
        if len(delivery_identities) != len(deliveries):
            raise WorkflowValidationError("target tool deliveries must be unique")
        if not delivery_identities <= identities:
            raise WorkflowValidationError(
                "target tool deliveries must reference trusted tools"
            )
        object.__setattr__(self, "tool_deliveries", deliveries)
        for field_name in (
            "allowed_permissions",
            "credential_bindings",
            "importer_features",
        ):
            values = frozenset(
                _name(item, f"target {field_name}")
                for item in getattr(self, field_name)
            )
            object.__setattr__(self, field_name, values)
        bindings = tuple(self.driver_bindings)
        if any(not isinstance(item, DriverBinding) for item in bindings):
            raise WorkflowValidationError("target driver bindings are invalid")
        if len({item.binding_key for item in bindings}) != len(bindings):
            raise WorkflowValidationError("target driver bindings must be unique")
        object.__setattr__(self, "driver_bindings", bindings)
        copied = json_copy(dict(self.subworkflow_bindings))
        if not isinstance(copied, dict) or contains_secret_name(copied):
            raise WorkflowValidationError("target subworkflow bindings are invalid")
        for key, item in copied.items():
            _bounded_text(key, "subworkflow binding key", maximum=256)
            if not isinstance(item, Mapping) or set(item) != {
                "workflow_id",
                "version",
                "definition_digest",
            }:
                raise WorkflowValidationError("subworkflow binding is invalid")
            _name(item["workflow_id"], "bound workflow_id")
            _name(item["version"], "bound workflow version")
            if (
                not isinstance(item["definition_digest"], str)
                or _DIGEST.fullmatch(item["definition_digest"]) is None
            ):
                raise WorkflowValidationError("bound workflow digest is invalid")
        object.__setattr__(self, "subworkflow_bindings", copied)
        if self.python_executable is not None:
            object.__setattr__(
                self,
                "python_executable",
                _bounded_text(
                    self.python_executable, "target python executable", maximum=4_096
                ),
            )

    @classmethod
    def from_mapping(cls, value: object) -> PortableTarget:
        required = {
            "schema_version",
            "target_kind",
            "trusted_tools",
            "allowed_permissions",
            "credential_bindings",
            "driver_bindings",
            "subworkflow_bindings",
            "python_executable",
            "importer_features",
        }
        optional = {"tool_deliveries"}
        if (
            not isinstance(value, Mapping)
            or not required <= set(value)
            or set(value) - required - optional
        ):
            raise WorkflowValidationError(
                "portable target has unknown or missing fields"
            )
        if value.get("schema_version") != TARGET_SCHEMA_V1:
            raise WorkflowValidationError("portable target schema is unsupported")
        list_fields = (
            "trusted_tools",
            "allowed_permissions",
            "credential_bindings",
            "driver_bindings",
            "importer_features",
        )
        if any(not isinstance(value[field_name], list) for field_name in list_fields):
            raise WorkflowValidationError("portable target list field is invalid")
        if "tool_deliveries" in value and not isinstance(
            value["tool_deliveries"], list
        ):
            raise WorkflowValidationError("tool_deliveries must be a list")
        subflows = value["subworkflow_bindings"]
        if not isinstance(subflows, Mapping):
            raise WorkflowValidationError("subworkflow_bindings must be an object")
        python = value["python_executable"]
        if python is not None and not isinstance(python, str):
            raise WorkflowValidationError("python_executable must be a string or null")
        return cls(
            trusted_tools=tuple(
                ToolReference.from_mapping(item) for item in value["trusted_tools"]
            ),
            tool_deliveries=tuple(
                ToolDelivery.from_mapping(item)
                for item in value.get("tool_deliveries", [])
            ),
            allowed_permissions=frozenset(value["allowed_permissions"]),  # type: ignore[arg-type]
            credential_bindings=frozenset(value["credential_bindings"]),  # type: ignore[arg-type]
            driver_bindings=tuple(
                DriverBinding.from_mapping(item) for item in value["driver_bindings"]
            ),
            subworkflow_bindings=subflows,  # type: ignore[arg-type]
            python_executable=python,
            target_kind=value["target_kind"],  # type: ignore[arg-type]
            importer_features=frozenset(value["importer_features"]),  # type: ignore[arg-type]
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "schema_version": TARGET_SCHEMA_V1,
            "target_kind": self.target_kind,
            "trusted_tools": [item.to_mapping() for item in self.trusted_tools],
            "tool_deliveries": [item.to_mapping() for item in self.tool_deliveries],
            "allowed_permissions": sorted(self.allowed_permissions),
            "credential_bindings": sorted(self.credential_bindings),
            "driver_bindings": [item.to_mapping() for item in self.driver_bindings],
            "subworkflow_bindings": json_copy(self.subworkflow_bindings),
            "python_executable": self.python_executable,
            "importer_features": sorted(self.importer_features),
        }


def _workflow_agent_addresses(
    workflows: Sequence[WorkflowSpec],
) -> dict[str, tuple[str, str, str]]:
    """Enumerate independently composable definition addresses."""

    addresses: dict[str, tuple[str, str, str]] = {}
    for current in workflows:
        for step in _walk_steps(current.steps):
            if step.kind is not StepKind.AGENT:
                continue
            address = (current.workflow_id, current.version, step.step_id)
            key = agent_address_key(*address)
            if key in addresses:
                raise WorkflowValidationError("project Agent definition is duplicated")
            addresses[key] = address
    return addresses


def _driver_binding_for(
    target: PortableTarget,
    address: tuple[str, str, str],
    all_addresses: Mapping[str, tuple[str, str, str]],
) -> DriverBinding | None:
    """Resolve an exact address, with a safe legacy single-node fallback."""

    exact = [item for item in target.driver_bindings if item.address == address]
    if exact:
        return exact[0]
    workflow_id, version, step_id = address
    del workflow_id, version
    legacy = [
        item
        for item in target.driver_bindings
        if item.address is None and item.step_id == step_id
    ]
    if not legacy:
        return None
    same_step = [item for item in all_addresses.values() if item[2] == step_id]
    if len(same_step) != 1:
        raise WorkflowValidationError(
            "duplicate Agent step ids require full workflow@version:step driver bindings"
        )
    return legacy[0]


@dataclass(frozen=True)
class CompatibilityDiagnostic:
    code: str
    classification: str
    path: str
    message: str

    def __post_init__(self) -> None:
        _name(self.code, "diagnostic code")
        if self.classification not in {
            "binding_required",
            "incompatible",
            "invalid",
            "warning",
        }:
            raise WorkflowValidationError("diagnostic classification is invalid")
        if not isinstance(self.path, str) or not self.path.startswith("/"):
            raise WorkflowValidationError("diagnostic path must be a JSON Pointer")
        _bounded_text(self.message, "diagnostic message", maximum=4_096)

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "code": self.code,
            "classification": self.classification,
            "path": self.path,
            "message": self.message,
        }


@dataclass(frozen=True)
class CompatibilityReport:
    status: str
    source: Mapping[str, JsonValue] | None
    target_kind: str
    diagnostics: tuple[CompatibilityDiagnostic, ...] = ()
    contract_status: str = "not_checked"
    installability_status: str = "not_checked"
    environment_status: str = "not_checked"

    def __post_init__(self) -> None:
        if self.status not in _REPORT_STATUSES:
            raise WorkflowValidationError("compatibility report status is invalid")
        for value in (
            self.contract_status,
            self.installability_status,
            self.environment_status,
        ):
            if value not in _CHECK_STATUSES:
                raise WorkflowValidationError("compatibility check status is invalid")

    @property
    def exit_code(self) -> int:
        return {
            "compatible": 0,
            "binding_required": 3,
            "incompatible": 4,
            "invalid": 2,
        }[self.status]

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "schema_version": REPORT_SCHEMA_V1,
            "status": self.status,
            "source": dict(self.source) if self.source is not None else None,
            "target_kind": self.target_kind,
            "diagnostics": [item.to_mapping() for item in self.diagnostics],
            "checks": {
                "contract": self.contract_status,
                "dependency_installability": self.installability_status,
                "environment_readiness": self.environment_status,
            },
            "runtime_state_transfer": False,
        }


@dataclass(frozen=True)
class EnvironmentReadinessReport:
    """Read-only evidence that a target interpreter can host a bundle.

    This is intentionally separate from dependency installability: it checks
    the interpreter that is there now, never calls a model, invokes a tool, or
    serializes a credential value.
    """

    status: str
    target_kind: str
    python_executable: str | None
    diagnostics: tuple[CompatibilityDiagnostic, ...] = ()
    source: Mapping[str, JsonValue] | None = None
    python_version: str | None = None
    runtime_construction_checked: bool = False
    development_modules_checked: bool = False
    development_full_modules_checked: bool = False
    development_entrypoints_checked: bool = False

    def __post_init__(self) -> None:
        if self.status not in _ENVIRONMENT_STATUSES:
            raise WorkflowValidationError("environment readiness status is invalid")

    @property
    def exit_code(self) -> int:
        return {
            "ready": 0,
            "binding_required": 3,
            "incompatible": 4,
            "invalid": 2,
        }[self.status]

    def to_mapping(self) -> dict[str, JsonValue]:
        codes = {item.code for item in self.diagnostics}
        metadata_failed = bool(
            codes
            & {
                "TARGET_PYTHON_UNAVAILABLE",
                "TARGET_PYTHON_PROBE_INVALID",
                "DRIVER_DISTRIBUTION_MISSING",
                "DRIVER_VERSION_MISMATCH",
                "DRIVER_ENTRY_POINT_MISSING",
                "DRIVER_RUNTIME_DEPENDENCY_MISSING",
                "DRIVER_RUNTIME_DEPENDENCY_VERSION_MISMATCH",
                "WORKFLOW_RUNTIME_DEPENDENCY_MISSING",
            }
        )
        metadata_not_checked = bool(
            codes
            & {
                "TARGET_PYTHON_BINDING_REQUIRED",
                "DRIVER_DISTRIBUTION_BINDING_REQUIRED",
            }
        )
        import_failed = bool(
            codes
            & {
                "DRIVER_RUNTIME_DEPENDENCY_MISSING",
                "DRIVER_RUNTIME_IMPORT_FAILED",
            }
        )
        import_not_checked = bool(
            codes
            & {
                "TARGET_PYTHON_BINDING_REQUIRED",
                "TARGET_PYTHON_UNAVAILABLE",
                "TARGET_PYTHON_PROBE_INVALID",
            }
        )
        construction_failed = "DRIVER_RUNTIME_CONSTRUCTION_FAILED" in codes
        development_module_failed = "DEVELOPMENT_TOOL_TOP_LEVEL_MODULE_MISSING" in codes
        development_full_module_failed = (
            "DEVELOPMENT_TOOL_FULL_MODULE_UNAVAILABLE" in codes
        )
        development_entrypoint_failed = "DEVELOPMENT_TOOL_ENTRYPOINT_UNAVAILABLE" in codes
        return {
            "schema_version": ENVIRONMENT_REPORT_SCHEMA_V1,
            "status": self.status,
            "source": dict(self.source) if self.source is not None else None,
            "target_kind": self.target_kind,
            "python_executable": self.python_executable,
            "python_version": self.python_version,
            "diagnostics": [item.to_mapping() for item in self.diagnostics],
            "checks": {
                "package_metadata": (
                    "not_checked"
                    if metadata_not_checked
                    else "failed"
                    if metadata_failed
                    else "passed"
                ),
                "provider_imports": (
                    "not_checked"
                    if import_not_checked
                    else "failed"
                    if import_failed
                    else "passed"
                ),
                "runtime_construction": (
                    "failed"
                    if construction_failed
                    else "passed"
                    if self.runtime_construction_checked
                    else "not_checked"
                ),
                # Development code is intentionally more conservative than a
                # release wheel.  The default metadata probe checks only a
                # top-level name; a dotted module can load arbitrary parent
                # package code, so it is checked only after explicit opt-in.
                "development_top_level_modules": (
                    "failed"
                    if development_module_failed
                    else "passed"
                    if self.development_modules_checked
                    else "not_checked"
                ),
                "development_full_module_locator": (
                    "failed"
                    if development_full_module_failed
                    else "passed"
                    if self.development_full_modules_checked
                    else "not_checked"
                ),
                "development_entrypoints": (
                    "failed"
                    if development_entrypoint_failed
                    else "passed"
                    if self.development_entrypoints_checked
                    else "not_checked"
                ),
                # A callable being importable is neither a registration grant
                # nor authorization to make a business call. Those operations
                # remain owned by RuntimeAssembly and its host.
                "tool_registration": "not_checked",
                "tool_business_invocation": "not_checked",
                "model_network": "not_checked",
            },
            "effects": "local_environment_only",
        }


@dataclass(frozen=True)
class PortableProject:
    """Resolved, checked authoring project rooted at a local directory."""

    root: Path
    kind: str
    workflow_path: Path | None
    tools_path: Path | None
    agent_instruction_paths: Mapping[str, Path]
    subworkflow_paths: Mapping[str, Path]
    files: tuple[str, ...]
    dependency_lock_path: Path
    tool_registries: tuple[str, ...] = ()
    evaluation_cases_path: Path | None = None
    tool_manifest_path: Path | None = None
    registry: str | None = None
    # Domain compilation is deliberately an authoring concern, but the
    # resulting project must carry its immutable lock into normal runtime
    # assembly and resume.  ``None`` preserves every existing v1 project.
    domain_lock_path: Path | None = None


@dataclass(frozen=True)
class WorkflowCompilationContext:
    """One checked project definition graph shared by check, plan, and run."""

    project: PortableProject
    workflow: WorkflowSpec
    subworkflows: tuple[WorkflowSpec, ...]

    @property
    def catalogue(self) -> Mapping[tuple[str, str], WorkflowSpec]:
        return {
            (item.workflow_id, item.version): item for item in self.subworkflows
        }


@dataclass(frozen=True)
class WorkflowPlan:
    """A deterministic, non-executing view of a workflow definition."""

    workflow_id: str
    version: str
    definition_digest: str
    workflow_schema_version: str
    plan: Mapping[str, JsonValue]

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "schema_version": PLAN_SCHEMA_V1,
            "workflow_id": self.workflow_id,
            "version": self.version,
            "definition_digest": self.definition_digest,
            "workflow_schema_version": self.workflow_schema_version,
            **json_copy(self.plan),
        }


def load_project(source: str | Path) -> PortableProject:
    root = Path(source)
    if root.is_file():
        if root.name != "pyproject.toml":
            raise WorkflowValidationError(
                "project source must be a directory or pyproject.toml"
            )
        root = root.parent
    pyproject_path = root / "pyproject.toml"
    try:
        raw = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise WorkflowValidationError(
            "portable project pyproject.toml cannot be read"
        ) from error
    except tomllib.TOMLDecodeError as error:
        raise WorkflowValidationError(
            "portable project pyproject.toml is invalid"
        ) from error
    config = raw.get("tool", {}).get("platform-agent-workflow")
    if (
        not isinstance(config, Mapping)
        or config.get("schema_version") != PROJECT_SCHEMA_V1
    ):
        raise WorkflowValidationError(
            "portable project configuration is missing or unsupported"
        )
    if contains_secret_name(config):
        raise WorkflowValidationError(
            "portable project configuration must not contain credentials"
        )
    kind = config.get("kind")
    if kind not in {"workflow", "tool"}:
        raise WorkflowValidationError("portable project kind must be workflow or tool")
    common = {"schema_version", "kind", "dependency_lock"}
    workflow_fields = common | {
        "workflow",
        "tools",
        "agent_instructions",
        "subworkflows",
        "files",
        "evaluation_cases",
    }
    tool_fields = common | {"tool_manifest", "registry"}
    expected = workflow_fields if kind == "workflow" else tool_fields
    optional = {"tool_registries", "domain_lock"} if kind == "workflow" else set()
    if not expected <= set(config) or set(config) - expected - optional:
        raise WorkflowValidationError("portable project has unknown or missing fields")
    lock = _project_path(root, config["dependency_lock"], "dependency_lock")
    if kind == "tool":
        registry = config["registry"]
        if not isinstance(registry, str) or registry.count(":") != 1:
            raise WorkflowValidationError(
                "tool project registry must use MODULE:CALLABLE"
            )
        return PortableProject(
            root=root.resolve(),
            kind="tool",
            workflow_path=None,
            tools_path=None,
            agent_instruction_paths={},
            subworkflow_paths={},
            files=(),
            dependency_lock_path=lock,
            tool_manifest_path=_project_path(
                root, config["tool_manifest"], "tool_manifest"
            ),
            registry=registry,
        )
    instructions = config["agent_instructions"]
    subworkflows = config["subworkflows"]
    files = config["files"]
    registries = config.get("tool_registries", [])
    if not isinstance(instructions, Mapping) or not isinstance(subworkflows, Mapping):
        raise WorkflowValidationError("project Agent and subworkflow maps are invalid")
    if not isinstance(files, list) or any(not isinstance(item, str) for item in files):
        raise WorkflowValidationError("project files must be a list of paths")
    if not isinstance(registries, list) or any(
        not isinstance(item, str) or item.count(":") != 1 for item in registries
    ):
        raise WorkflowValidationError(
            "project tool_registries must contain MODULE:CALLABLE strings"
        )
    instruction_paths = {
        _bounded_text(step_id, "Agent instruction address", maximum=512): _project_path(
            root, path, "Agent instruction"
        )
        for step_id, path in instructions.items()
    }
    subworkflow_paths = {
        _bounded_text(key, "subworkflow project key", maximum=256): _project_path(
            root, path, "subworkflow"
        )
        for key, path in subworkflows.items()
    }
    for relative in files:
        _project_path(root, relative, "project file")
    evaluation = config["evaluation_cases"]
    return PortableProject(
        root=root.resolve(),
        kind="workflow",
        workflow_path=_project_path(root, config["workflow"], "workflow"),
        tools_path=_project_path(root, config["tools"], "tools"),
        agent_instruction_paths=instruction_paths,
        subworkflow_paths=subworkflow_paths,
        files=tuple(files),
        dependency_lock_path=lock,
        tool_registries=tuple(registries),
        evaluation_cases_path=(
            _project_path(root, evaluation, "evaluation_cases")
            if evaluation is not None
            else None
        ),
        domain_lock_path=(
            _project_path(root, config["domain_lock"], "domain_lock")
            if config.get("domain_lock") is not None
            else None
        ),
    )


def _project_path(root: Path, raw: object, label: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise WorkflowValidationError(f"{label} must be a relative project path")
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        raise WorkflowValidationError(f"{label} must be a relative project path")
    resolved_root = root.resolve()
    current = resolved_root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise WorkflowValidationError(f"{label} must not be a symlink")
    resolved = (resolved_root / relative).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as error:
        raise WorkflowValidationError(f"{label} escapes the project root") from error
    return resolved


def _load_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise WorkflowValidationError(f"{label} cannot be read") from error
    except json.JSONDecodeError as error:
        raise WorkflowValidationError(f"{label} is not valid JSON") from error


def _check_project_context(
    source: str | Path | PortableProject,
) -> tuple[PortableProject, WorkflowSpec | None, tuple[WorkflowSpec, ...]]:
    # A checked PortableProject may contain convenience aliases for unique
    # Agent step ids. Re-read its declarative project file so every consumer
    # compiles the same canonical addresses instead of validating those
    # derived aliases as if the author had declared them twice.
    project = load_project(source.root if isinstance(source, PortableProject) else source)
    _read_dependency_lock(project.dependency_lock_path)
    if project.kind == "tool":
        assert project.tool_manifest_path is not None
        _normalize_tool_descriptor(
            _load_json(project.tool_manifest_path, "tool descriptor")
        )
        return project, None, ()
    assert project.workflow_path is not None and project.tools_path is not None
    workflow = WorkflowSpec.from_mapping(_load_json(project.workflow_path, "workflow"))
    tools = _load_json(project.tools_path, "tools")
    if not isinstance(tools, list) or any(
        not isinstance(item, Mapping) for item in tools
    ):
        raise WorkflowValidationError("project tools must be a list of objects")
    normalized_tools = _normalize_tools(tools)
    children: list[WorkflowSpec] = []
    child_identities: set[tuple[str, str]] = set()
    for key, path in project.subworkflow_paths.items():
        child = WorkflowSpec.from_mapping(_load_json(path, "subworkflow"))
        if key != f"{child.workflow_id}@{child.version}":
            raise WorkflowValidationError(
                "subworkflow project key does not match definition"
            )
        identity = (child.workflow_id, child.version)
        if identity in child_identities:
            raise WorkflowValidationError("subworkflow definitions are duplicated")
        child_identities.add(identity)
        children.append(child)
    all_workflows = (workflow, *children)
    required_subflows = {
        (str(step.config["workflow_id"]), str(step.config["version"]))
        for current in all_workflows
        for step in _walk_steps(current.steps)
        if step.kind is StepKind.SUBWORKFLOW
    }
    if not required_subflows <= child_identities:
        raise WorkflowValidationError("project is missing a referenced subworkflow")
    for current in all_workflows:
        _require_declared_tools(current, normalized_tools)
    if any(
        current.schema_version in ASYNC_SCHEMA_VERSIONS for current in all_workflows
    ):
        from .workflow_v3 import preflight_v3

        for current in all_workflows:
            if current.schema_version in ASYNC_SCHEMA_VERSIONS:
                preflight_v3(current, workflows=tuple(children))
    addresses = _workflow_agent_addresses(all_workflows)
    by_step: dict[str, list[str]] = {}
    for key, address in addresses.items():
        by_step.setdefault(address[2], []).append(key)
    resolved_instructions: dict[str, Path] = {}
    for raw_key, path in project.agent_instruction_paths.items():
        if raw_key in addresses:
            key = raw_key
        else:
            candidates = by_step.get(raw_key, [])
            if len(candidates) != 1:
                raise WorkflowValidationError(
                    "project Agent instructions require full workflow@version:step addresses"
                )
            key = candidates[0]
        if key in resolved_instructions:
            raise WorkflowValidationError("project Agent instructions are duplicated")
        resolved_instructions[key] = path
    if set(resolved_instructions) != set(addresses):
        raise WorkflowValidationError(
            "project Agent instruction files must exactly match all Agent definitions"
        )
    for path in resolved_instructions.values():
        text = path.read_text(encoding="utf-8")
        _bounded_text(text, "Agent instructions", maximum=64 * 1024)
    for relative in project.files:
        path = _project_path(project.root, relative, "project file")
        if not path.is_file():
            raise WorkflowValidationError("project file does not exist")
    if project.evaluation_cases_path is not None:
        raw_cases = _load_json(project.evaluation_cases_path, "evaluation cases")
        if not isinstance(raw_cases, list):
            raise WorkflowValidationError("evaluation cases must be a JSON list")
    if project.domain_lock_path is not None:
        # This lazy import keeps ordinary projects and base-package imports
        # independent of domain authoring.  The domain-lock decoder itself is
        # standard-library-only so compiled projects remain runnable without
        # Copier/jsonschema.
        from .domain import load_domain_lock, validate_domain_project

        validate_domain_project(
            load_domain_lock(project.domain_lock_path),
            project,
            workflow,
            tuple(children),
            normalized_tools,
        )
    # Preserve the historic short lookup for a unique node as a convenience,
    # while all internal routing uses the canonical definition address.  An
    # ambiguous short name deliberately has no alias.
    public_instructions = dict(resolved_instructions)
    for step_id, keys in by_step.items():
        if len(keys) == 1:
            public_instructions.setdefault(step_id, resolved_instructions[keys[0]])
    checked = replace(project, agent_instruction_paths=public_instructions)
    return checked, workflow, tuple(children)


def check_project(source: str | Path | PortableProject) -> PortableProject:
    """Validate a project and return its resolved, credential-free paths."""

    return _check_project_context(source)[0]


def compile_project_context(
    source: str | Path | PortableProject,
) -> WorkflowCompilationContext:
    """Return the exact checked definition graph used by plan and execution."""

    project, workflow, children = _check_project_context(source)
    if project.kind != "workflow" or workflow is None:
        raise WorkflowValidationError("workflow compilation requires a workflow project")
    return WorkflowCompilationContext(project, workflow, children)


def export_project_bundle(source: str | Path, output: str | Path) -> PortableBundle:
    project = check_project(source)
    if project.kind != "workflow":
        raise WorkflowValidationError("only workflow projects export workflow bundles")
    assert project.workflow_path is not None and project.tools_path is not None
    workflow = WorkflowSpec.from_mapping(_load_json(project.workflow_path, "workflow"))
    raw_tools = _load_json(project.tools_path, "tools")
    assert isinstance(raw_tools, list)
    instructions = {
        address: path.read_text(encoding="utf-8")
        for address, path in project.agent_instruction_paths.items()
        if "@" in address and ":" in address
    }
    subworkflows = [
        WorkflowSpec.from_mapping(_load_json(path, "subworkflow"))
        for path in project.subworkflow_paths.values()
    ]
    return export_bundle(
        output,
        workflow,
        tools=raw_tools,  # type: ignore[arg-type]
        files_root=project.root,
        files=project.files,
        bundle_format=BUNDLE_V2,
        agent_instructions=instructions,
        subworkflows=subworkflows,
        dependency_lock=project.dependency_lock_path,
    )


def import_bundle_project(
    source: str | Path,
    target_directory: str | Path,
    target: PortableTarget,
    *,
    tool_registries: Sequence[str] = (),
    subworkflow_sources: Mapping[str, str | Path] | None = None,
    check_dependencies: bool = True,
) -> PortableProject:
    """Assemble a validated v2 definition bundle into a checked V6 project.

    The operation writes only declarative project metadata.  Target-owned tool
    registry modules must already be installed or be added explicitly by the
    developer; executable code and credential values are never copied from the
    bundle.
    """

    if not isinstance(target, PortableTarget):
        raise WorkflowValidationError(
            "project assembly requires an explicit PortableTarget"
        )
    entries, bundle = _read_bundle_entries(source)
    if bundle.schema_version != BUNDLE_V2 or bundle.portability is None:
        raise WorkflowValidationError("project assembly requires a bundle v2")
    report = diagnose_bundle(bundle, target, check_dependencies=check_dependencies)
    if report.status != "compatible":
        raise WorkflowValidationError(
            f"bundle v2 target is {report.status}; run diagnose for details"
        )
    registries = tuple(tool_registries)
    if any(not isinstance(item, str) or item.count(":") != 1 for item in registries):
        raise WorkflowValidationError(
            "project tool registries must use MODULE:CALLABLE"
        )
    requirements = tuple(
        SubworkflowRequirement.from_mapping(item)
        for item in bundle.portability.get("subworkflows", [])
    )
    supplied = dict(subworkflow_sources or {})
    if set(supplied) != {item.binding_key for item in requirements}:
        raise WorkflowValidationError(
            "project assembly requires one exact local source for every subworkflow"
        )
    children: dict[str, WorkflowSpec] = {}
    for requirement in requirements:
        raw = _load_json(Path(supplied[requirement.binding_key]), "subworkflow")
        child = WorkflowSpec.from_mapping(raw)
        if (
            child.workflow_id != requirement.workflow_id
            or child.version != requirement.version
            or child.definition_digest != requirement.definition_digest
        ):
            raise WorkflowValidationError(
                f"subworkflow source {requirement.binding_key} does not match its bundle requirement"
            )
        children[requirement.binding_key] = child

    destination = Path(target_directory)
    reserved_roots = {
        "eval",
        "instructions",
        "manifest.json",
        "portability.json",
        "pylock.toml",
        "pyproject.toml",
        "subworkflows",
        "target.json",
        "tools.json",
        "workflow.json",
    }
    for relative in bundle.files:
        if not Path(relative).parts or Path(relative).parts[0] in reserved_roots:
            raise WorkflowValidationError(
                "bundle file conflicts with assembled project metadata"
            )
    # User input paths (including the perfectly valid path ``files``) are
    # assembled from an operation-owned sibling staging directory.  The
    # archive's internal ``files/`` transport namespace therefore never
    # consumes a path in the final project namespace.
    _prepare_import_destination(destination)
    assembly = _new_import_staging_directory(destination, "project")
    try:
        unpacked = _new_import_staging_directory(destination, "payload")
    except BaseException:
        _discard_import_staging_directory(assembly)
        raise
    published = False
    try:
        _extract_entries(entries, unpacked)
        _extract_entries(
            {
                name: content
                for name, content in entries.items()
                if not name.startswith("files/")
            },
            assembly,
        )
        for relative in sorted(bundle.files):
            source_path = unpacked / "files" / relative
            project_path = assembly / relative
            project_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.replace(source_path, project_path)
            except OSError as error:
                raise WorkflowValidationError(
                    "bundle file could not be assembled into the project"
                ) from error
        # A compiled domain project already declares its lock as an ordinary
        # project file, so the existing v2 archive transports it without a
        # parallel bundle format.  Treat this conventional filename as a
        # domain lock only when it passes the strict, stdlib-only decoder;
        # an unrelated user file with the same name remains an ordinary file.
        domain_lock = None
        candidate_domain_lock = assembly / "domain-lock.json"
        if candidate_domain_lock.is_file():
            from .domain import load_domain_lock

            try:
                domain_lock = load_domain_lock(candidate_domain_lock)
            except WorkflowValidationError:
                pass
        agents = bundle.portability.get("agents", [])
        if not isinstance(agents, list):  # validated by reader; defensive guard
            raise WorkflowValidationError("bundle Agent requirements are malformed")
        all_workflows = (bundle.workflow, *children.values())
        expected_addresses = _workflow_agent_addresses(all_workflows)
        steps_by_address: dict[str, tuple[WorkflowSpec, Any]] = {}
        for current in all_workflows:
            for step in _walk_steps(current.steps):
                if step.kind is StepKind.AGENT:
                    steps_by_address[
                        agent_address_key(
                            current.workflow_id, current.version, step.step_id
                        )
                    ] = (current, step)
        instruction_paths: dict[str, str] = {}
        seen_agents: set[str] = set()
        for item in agents:
            if not isinstance(item, Mapping):
                raise WorkflowValidationError("bundle Agent requirement is malformed")
            step_id = _name(item.get("step_id"), "Agent instruction step_id")
            workflow_id = item.get("workflow_id", bundle.workflow.workflow_id)
            workflow_version = item.get("workflow_version", bundle.workflow.version)
            address = agent_address_key(
                _name(workflow_id, "Agent instruction workflow_id"),
                _name(workflow_version, "Agent instruction workflow_version"),
                step_id,
            )
            if address in seen_agents or address not in expected_addresses:
                raise WorkflowValidationError("bundle Agent definition is invalid")
            seen_agents.add(address)
            instructions = _bounded_text(
                item.get("instructions"), "Agent instructions", maximum=64 * 1024
            )
            current, step = steps_by_address[address]
            expected_output: Mapping[str, JsonValue]
            if current.schema_version == "v6-light-workflow-v2":
                expected_output = {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "properties": {"message": {"type": "string", "maxLength": 64000}},
                    "required": ["message"],
                    "additionalProperties": False,
                }
            else:
                assert step.contract is not None
                expected_output = step.contract.output_schema
            if (
                item.get("source_driver") != step.config.get("driver")
                or item.get("tools") != step.config.get("tools", [])
                or item.get("output_schema") != expected_output
                or item.get("budget") != _portable_agent_budget(step.config)
            ):
                raise WorkflowValidationError(
                    "bundle Agent contract does not match definition"
                )
            relative = (
                "instructions/"
                + hashlib.sha256(address.encode("utf-8")).hexdigest()[:24]
                + ".md"
            )
            path = assembly / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(instructions, encoding="utf-8")
            instruction_paths[address] = relative
        if seen_agents != set(expected_addresses):
            raise WorkflowValidationError(
                "bundle Agent instructions do not cover every imported definition"
            )
        child_paths: dict[str, str] = {}
        for index, (key, child) in enumerate(sorted(children.items())):
            relative = f"subworkflows/{index:03d}-{child.workflow_id}-{child.version}.json"
            path = assembly / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(canonical_json(child.to_mapping()) + "\n", encoding="utf-8")
            child_paths[key] = relative
        evaluation = assembly / "eval" / "cases.json"
        evaluation.parent.mkdir(parents=True, exist_ok=True)
        evaluation.write_text("[]\n", encoding="utf-8")
        (assembly / "target.json").write_text(
            canonical_json(target.to_mapping()) + "\n", encoding="utf-8"
        )
        project_toml = _assembled_project_toml(
            bundle.workflow,
            instruction_paths,
            child_paths,
            tuple(sorted(bundle.files)),
            registries,
            domain_lock="domain-lock.json" if domain_lock is not None else None,
        )
        (assembly / "pyproject.toml").write_text(project_toml, encoding="utf-8")
        # The only validation that matters is the final assembled declaration,
        # not the transient archive layout.  Do it before the one publish step.
        check_project(assembly)
        if domain_lock is not None:
            # ``diagnose_bundle`` validates normal workflow capabilities. The
            # lock adds the domain-requested permission and any extra driver
            # capability boundary before this project becomes visible.
            from .domain import validate_domain_target

            validate_domain_target(
                domain_lock,
                target,
                bundle.workflow,
                tuple(children.values()),
                bundle.tools,
            )
        _publish_import_directory(assembly, destination)
        published = True
    finally:
        _discard_import_staging_directory(unpacked)
        if not published:
            _discard_import_staging_directory(assembly)
    return check_project(destination)


def _assembled_project_toml(
    workflow: WorkflowSpec,
    instructions: Mapping[str, str],
    subworkflows: Mapping[str, str],
    files: Sequence[str],
    registries: Sequence[str],
    *,
    domain_lock: str | None = None,
) -> str:
    def quoted(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)

    def array(values: Sequence[str]) -> str:
        return "[" + ", ".join(quoted(item) for item in values) + "]"

    lines = [
        "[project]",
        f"name = {quoted(workflow.workflow_id)}",
        f"version = {quoted(workflow.version)}",
        'requires-python = ">=3.12"',
        "dependencies = []",
        "",
        "[tool.platform-agent-workflow]",
        f"schema_version = {quoted(PROJECT_SCHEMA_V1)}",
        'kind = "workflow"',
        'workflow = "workflow.json"',
        'tools = "tools.json"',
        'dependency_lock = "pylock.toml"',
        *(
            [f"domain_lock = {quoted(domain_lock)}"]
            if domain_lock is not None
            else []
        ),
        f"files = {array(files)}",
        'evaluation_cases = "eval/cases.json"',
        f"tool_registries = {array(registries)}",
        "",
        "[tool.platform-agent-workflow.agent_instructions]",
        *(
            f"{quoted(key)} = {quoted(value)}"
            for key, value in sorted(instructions.items())
        ),
        "",
        "[tool.platform-agent-workflow.subworkflows]",
        *(
            f"{quoted(key)} = {quoted(value)}"
            for key, value in sorted(subworkflows.items())
        ),
        "",
    ]
    return "\n".join(lines)


def compile_execution_plan(
    source: WorkflowSpec | PortableProject | str | Path,
    *,
    subworkflows: Sequence[WorkflowSpec] = (),
) -> WorkflowPlan:
    if isinstance(source, WorkflowSpec):
        workflow = source
        children = tuple(subworkflows)
    else:
        context = compile_project_context(source)
        workflow = context.workflow
        children = context.subworkflows
    catalogue = {(item.workflow_id, item.version): item for item in children}
    if workflow.schema_version in ASYNC_SCHEMA_VERSIONS:
        from .workflow_v3 import preflight_v3

        preflight_v3(workflow, workflows=children)
        for child in children:
            if child.schema_version in ASYNC_SCHEMA_VERSIONS:
                preflight_v3(child, workflows=children)
    plan: dict[str, JsonValue] = {
        "semantics": workflow_semantics(workflow).to_mapping(),
        "inputs": dict(workflow.input_schema) if workflow.input_schema else None,
        "outputs": dict(workflow.output_schema) if workflow.output_schema else None,
        "execution_policy": (
            workflow.execution_policy.to_mapping()
            if workflow.execution_policy is not None
            else None
        ),
        "steps": [
            _plan_step(step, f"/{index}", catalogue)
            for index, step in enumerate(workflow.steps)
        ],
        "subworkflows": [
            {
                "workflow_id": child.workflow_id,
                "version": child.version,
                "definition_digest": child.definition_digest,
                "workflow_schema_version": child.schema_version,
                "inputs": (
                    dict(child.input_schema) if child.input_schema is not None else None
                ),
                "outputs": (
                    dict(child.output_schema)
                    if child.output_schema is not None
                    else None
                ),
                "steps": [
                    _plan_step(step, f"/{index}", catalogue)
                    for index, step in enumerate(child.steps)
                ],
            }
            for child in children
        ],
        "effects": "not_executed",
    }
    return WorkflowPlan(
        workflow.workflow_id,
        workflow.version,
        workflow.definition_digest,
        workflow.schema_version,
        plan,
    )


def _plan_step(
    step: Any,
    path: str,
    catalogue: Mapping[tuple[str, str], WorkflowSpec],
) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {
        "path": path,
        "step_id": step.step_id,
        "kind": step.kind.value,
        "input_bindings": (
            {
                target: binding.to_mapping()
                for target, binding in step.contract.bindings.items()
            }
            if step.contract is not None
            else None
        ),
        "timeout_seconds": (
            step.contract.timeout_seconds if step.contract is not None else None
        ),
        "max_input_bytes": (
            step.contract.max_input_bytes if step.contract is not None else None
        ),
        "max_output_bytes": (
            step.contract.max_output_bytes if step.contract is not None else None
        ),
    }
    if step.kind is StepKind.TOOL:
        capabilities = step.config.get("capabilities")
        warning = _recovery_warning(capabilities)
        result.update(
            {
                "tool": json_copy(step.config["tool"]),
                "approval_required": bool(step.config.get("approval_required", True)),
                "action_boundary": True,
                "recovery_capabilities": json_copy(capabilities),
                "recovery_warning": warning,
            }
        )
    elif step.kind is StepKind.AGENT:
        tool_recovery = [
            {
                "name": item.get("name"),
                "capabilities": json_copy(item.get("capabilities")),
                "warning": _recovery_warning(item.get("capabilities")),
            }
            for item in step.config.get("tools", [])
            if isinstance(item, Mapping)
        ]
        result.update(
            {
                "driver_requirement": step.config["driver"],
                "max_turns": int(step.config.get("max_turns", 1)),
                "tool_scope": [
                    item.get("name", item.get("tool_id"))
                    for item in step.config.get("tools", [])
                    if isinstance(item, Mapping)
                ],
                "action_boundary": True,
                "budget": json_copy(step.config.get("budget")),
                "tool_recovery": tool_recovery,
            }
        )
    elif step.kind is StepKind.APPROVAL:
        result["approval_boundary"] = True
    elif step.kind is StepKind.SUBWORKFLOW:
        identity = (str(step.config["workflow_id"]), str(step.config["version"]))
        child = catalogue.get(identity)
        result["requirement"] = {
            "workflow_id": step.config["workflow_id"],
            "version": step.config["version"],
            "definition_digest": (
                child.definition_digest if child is not None else None
            ),
            "workflow_schema_version": (
                child.schema_version if child is not None else None
            ),
        }
    elif step.kind is StepKind.CONDITION:
        result["condition"] = {
            "path": step.config["path"],
            "equals": step.config["equals"],
        }
        result["branches"] = [
            {
                "name": name,
                "steps": [
                    _plan_step(child, f"{path}/{name}/{index}", catalogue)
                    for index, child in enumerate(branch)
                ],
            }
            for name, branch in zip(("then", "otherwise"), step.children, strict=True)
        ]
    elif step.kind is StepKind.PARALLEL:
        names = step.branch_names or tuple(
            f"branch-{index}" for index in range(len(step.children))
        )
        result["parallel"] = {
            "failure_policy": step.config.get("failure_policy", "fail_fast"),
            "max_concurrency": step.config.get("max_concurrency"),
            "join_order": list(names),
        }
        result["branches"] = [
            {
                "name": name,
                "steps": [
                    _plan_step(child, f"{path}/{name}/{index}", catalogue)
                    for index, child in enumerate(branch)
                ],
            }
            for name, branch in zip(names, step.children, strict=True)
        ]
    return result


def _recovery_warning(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    if value.get("external_side_effects") is not True:
        return None
    if value.get("supports_result_query") is True:
        return None
    if value.get("accepts_idempotency_key") is True:
        return "EXTERNAL_IDEMPOTENCY_REQUIRED"
    return "UNKNOWN_RESULT_REQUIRES_MANUAL_RECONCILIATION"


def diagnose_bundle(
    source: PortableBundle | str | Path,
    target: PortableTarget,
    *,
    check_dependencies: bool = True,
    check_environment: bool = False,
    verify_development_entrypoints: bool = False,
    credential_resolver: Callable[[str], object] | None = None,
) -> CompatibilityReport:
    if verify_development_entrypoints and not check_environment:
        raise WorkflowValidationError(
            "verify_development_entrypoints requires check_environment"
        )
    try:
        bundle = source if isinstance(source, PortableBundle) else read_bundle(source)
    except (OSError, WorkflowValidationError) as error:
        del error
        return CompatibilityReport(
            "invalid",
            None,
            target.target_kind,
            (
                CompatibilityDiagnostic(
                    "BUNDLE_INVALID",
                    "invalid",
                    "/",
                    "The bundle failed structural or integrity validation.",
                ),
            ),
            contract_status="invalid",
        )
    source_identity = {
        "workflow_id": bundle.workflow.workflow_id,
        "version": bundle.workflow.version,
        "definition_digest": bundle.workflow.definition_digest,
        "bundle_schema_version": bundle.schema_version,
    }
    if bundle.schema_version != BUNDLE_V2 or bundle.portability is None:
        return CompatibilityReport(
            "incompatible",
            source_identity,
            target.target_kind,
            (
                CompatibilityDiagnostic(
                    "BUNDLE_V2_REQUIRED",
                    "incompatible",
                    "/manifest/schema_version",
                    "Compatibility diagnosis requires an opt-in bundle v2 contract.",
                ),
            ),
            contract_status="incompatible",
        )
    diagnostics: list[CompatibilityDiagnostic] = []
    trusted = {
        (item.tool_id, item.version, item.package_digest)
        for item in target.trusted_tools
    }
    trusted_releases = {
        (item.tool_id, item.version): item.package_digest
        for item in target.trusted_tools
    }
    for index, manifest in enumerate(bundle.tools):
        reference = _manifest_reference(manifest)
        identity = (reference.tool_id, reference.version, reference.package_digest)
        path = f"/tools/{index}"
        if identity not in trusted:
            release_digest = trusted_releases.get(
                (reference.tool_id, reference.version)
            )
            if release_digest is not None:
                diagnostics.append(
                    CompatibilityDiagnostic(
                        "TOOL_DIGEST_MISMATCH",
                        "incompatible",
                        path + "/package_digest",
                        "The target has a conflicting digest for this tool release.",
                    )
                )
            else:
                diagnostics.append(
                    CompatibilityDiagnostic(
                        "TOOL_BINDING_REQUIRED",
                        "binding_required",
                        path,
                        "The target must install and trust the required exact tool package.",
                    )
                )
        permissions = manifest.get("permissions", [])
        if (
            isinstance(permissions, list)
            and not set(permissions) <= target.allowed_permissions
        ):
            diagnostics.append(
                CompatibilityDiagnostic(
                    "TOOL_PERMISSION_BINDING_REQUIRED",
                    "binding_required",
                    path + "/permissions",
                    "The target is missing one or more required permission grants.",
                )
            )
        credential = manifest.get("credential_ref")
        if isinstance(credential, str) and credential not in target.credential_bindings:
            diagnostics.append(
                CompatibilityDiagnostic(
                    "TOOL_CREDENTIAL_BINDING_REQUIRED",
                    "binding_required",
                    path + "/credential_ref",
                    "The target has not bound the required credential reference.",
                )
            )
    agents = bundle.portability.get("agents", [])
    subflows = bundle.portability.get("subworkflows", [])
    if not isinstance(agents, list) or not isinstance(subflows, list):
        return CompatibilityReport(
            "invalid",
            source_identity,
            target.target_kind,
            (
                CompatibilityDiagnostic(
                    "PORTABILITY_CONTRACT_INVALID",
                    "invalid",
                    "/portability",
                    "The portability contract is malformed.",
                ),
            ),
            contract_status="invalid",
        )
    if target.target_kind == "v7" and (agents or subflows):
        diagnostics.append(
            CompatibilityDiagnostic(
                "TARGET_IMPORTER_SUPPORT_REQUIRED",
                "binding_required",
                "/target/target_kind",
                "The current V7 importer does not implement V6 Agent or subworkflow semantics.",
            )
        )
    agent_addresses: dict[str, tuple[str, str, str]] = {}
    for item in agents:
        if not isinstance(item, Mapping) or not isinstance(item.get("step_id"), str):
            continue
        workflow_id = item.get("workflow_id", bundle.workflow.workflow_id)
        workflow_version = item.get("workflow_version", bundle.workflow.version)
        if not isinstance(workflow_id, str) or not isinstance(workflow_version, str):
            continue
        address = (workflow_id, workflow_version, item["step_id"])
        agent_addresses[agent_address_key(*address)] = address
    for index, item in enumerate(agents):
        if not isinstance(item, Mapping) or not isinstance(item.get("step_id"), str):
            continue
        workflow_id = item.get("workflow_id", bundle.workflow.workflow_id)
        workflow_version = item.get("workflow_version", bundle.workflow.version)
        if not isinstance(workflow_id, str) or not isinstance(workflow_version, str):
            continue
        address = (workflow_id, workflow_version, item["step_id"])
        path = f"/portability/agents/{index}"
        try:
            binding = _driver_binding_for(target, address, agent_addresses)
        except WorkflowValidationError:
            diagnostics.append(
                CompatibilityDiagnostic(
                    "AGENT_DRIVER_ADDRESS_BINDING_REQUIRED",
                    "binding_required",
                    path,
                    "Repeated Agent step ids require a full workflow@version:step driver binding.",
                )
            )
            continue
        if binding is None:
            diagnostics.append(
                CompatibilityDiagnostic(
                    "AGENT_DRIVER_BINDING_REQUIRED",
                    "binding_required",
                    path,
                    "The target must bind an exact driver version and model.",
                )
            )
            continue
        required = item.get("required_capabilities", [])
        if isinstance(required, list) and not set(required) <= binding.capabilities:
            diagnostics.append(
                CompatibilityDiagnostic(
                    "AGENT_DRIVER_CAPABILITY_MISSING",
                    "incompatible",
                    path + "/required_capabilities",
                    "The bound driver does not provide all required Agent capabilities.",
                )
            )
    for index, raw in enumerate(subflows):
        try:
            requirement = SubworkflowRequirement.from_mapping(raw)
        except WorkflowValidationError:
            continue
        binding = target.subworkflow_bindings.get(requirement.binding_key)
        path = f"/portability/subworkflows/{index}"
        if binding is None:
            diagnostics.append(
                CompatibilityDiagnostic(
                    "SUBWORKFLOW_BINDING_REQUIRED",
                    "binding_required",
                    path,
                    "The target must bind the required published subworkflow.",
                )
            )
        elif binding.get("definition_digest") != requirement.definition_digest:
            diagnostics.append(
                CompatibilityDiagnostic(
                    "SUBWORKFLOW_DIGEST_MISMATCH",
                    "incompatible",
                    path + "/definition_digest",
                    "The bound subworkflow does not match the required definition digest.",
                )
            )
        if requirement.input_schema is None or requirement.output_schema is None:
            diagnostics.append(
                CompatibilityDiagnostic(
                    "SUBWORKFLOW_SCHEMA_UNAVAILABLE",
                    "warning",
                    path,
                    "This legacy subworkflow can be checked only by identity and digest.",
                )
            )
    contract_diagnostics = tuple(diagnostics)
    dependency_diagnostics: list[CompatibilityDiagnostic] = []
    if check_dependencies:
        dependency_diagnostics = _diagnose_dependencies(bundle, target)
    environment_diagnostics: tuple[CompatibilityDiagnostic, ...] = ()
    environment_status = "not_checked"
    if check_environment:
        readiness = inspect_target_environment(
            target,
            bundle=bundle,
            verify_development_entrypoints=verify_development_entrypoints,
            credential_resolver=credential_resolver,
        )
        environment_diagnostics = readiness.diagnostics
        environment_status = (
            "compatible" if readiness.status == "ready" else readiness.status
        )
    all_diagnostics = (
        *contract_diagnostics,
        *dependency_diagnostics,
        *environment_diagnostics,
    )
    status = _report_status(all_diagnostics)
    return CompatibilityReport(
        status,
        source_identity,
        target.target_kind,
        tuple(all_diagnostics),
        contract_status=_report_status(contract_diagnostics),
        installability_status=(
            _report_status(dependency_diagnostics)
            if check_dependencies
            else "not_checked"
        ),
        environment_status=environment_status,
    )


def _diagnose_dependencies(
    bundle: PortableBundle, target: PortableTarget
) -> list[CompatibilityDiagnostic]:
    if target.python_executable is None:
        return [
            CompatibilityDiagnostic(
                "TARGET_PYTHON_BINDING_REQUIRED",
                "binding_required",
                "/target/python_executable",
                "A target Python interpreter is required for dependency validation.",
            )
        ]
    uv = shutil.which("uv")
    if uv is None:
        return [
            CompatibilityDiagnostic(
                "UV_NOT_AVAILABLE",
                "binding_required",
                "/target/python_executable",
                "uv is required to validate the PEP 751 dependency lock.",
            )
        ]
    assert bundle.dependency_lock is not None
    try:
        with tempfile.TemporaryDirectory(prefix="v6-portability-") as temporary:
            root = Path(temporary)
            lock = root / "pylock.toml"
            lock.write_text(bundle.dependency_lock, encoding="utf-8")
            completed = subprocess.run(
                [
                    uv,
                    "pip",
                    "install",
                    "--dry-run",
                    "--target",
                    str(root / "site"),
                    "--python",
                    target.python_executable,
                    "--preview-features",
                    "pylock",
                    "-r",
                    str(lock),
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
    except subprocess.TimeoutExpired:
        return [
            CompatibilityDiagnostic(
                "DEPENDENCY_CHECK_TIMEOUT",
                "binding_required",
                "/pylock.toml",
                "The dependency dry-run timed out; repair target access and retry.",
            )
        ]
    except OSError:
        return [
            CompatibilityDiagnostic(
                "DEPENDENCY_CHECK_UNAVAILABLE",
                "binding_required",
                "/pylock.toml",
                "The dependency dry-run could not be started; verify uv and the target interpreter.",
            )
        ]
    if completed.returncode == 0:
        return []
    return [_dependency_check_failure(completed)]


def _dependency_check_failure(
    completed: subprocess.CompletedProcess[str],
) -> CompatibilityDiagnostic:
    """Classify a failed uv dry-run without exposing command output.

    A solver conflict is a confirmed incompatibility.  Transport, launch, and
    otherwise ambiguous failures are deliberately reported as incomplete so a
    caller cannot mistake an unavailable check for a valid target.
    """

    output = "\n".join(
        value for value in (completed.stdout, completed.stderr) if isinstance(value, str)
    ).lower()
    if any(
        marker in output
        for marker in (
            "network",
            "connection",
            "timed out",
            "timeout",
            "dns",
            "proxy",
            "certificate",
            "ssl",
            "offline",
            "failed to download",
            "failed to fetch",
        )
    ):
        return CompatibilityDiagnostic(
            "DEPENDENCY_CHECK_NETWORK_UNAVAILABLE",
            "binding_required",
            "/pylock.toml",
            "The dependency dry-run could not reach its package source; retry with target network access.",
        )
    if any(
        marker in output
        for marker in (
            "no python",
            "python executable",
            "failed to find python",
            "could not find python",
            "no interpreter",
            "no python interpreter",
            "interpreter not found",
            "python interpreter not found",
            "failed to inspect python",
            "failed to inspect interpreter",
        )
    ):
        return CompatibilityDiagnostic(
            "DEPENDENCY_TARGET_PYTHON_INCOMPATIBLE",
            "incompatible",
            "/target/python_executable",
            "The selected target interpreter cannot satisfy the dependency check.",
        )
    if any(
        marker in output
        for marker in (
            "no solution found",
            "dependency conflict",
            "conflicting dependencies",
            "requires-python",
            "requires python",
            "cannot satisfy",
            "incompatible with",
        )
    ):
        return CompatibilityDiagnostic(
            "DEPENDENCY_LOCK_INCOMPATIBLE",
            "incompatible",
            "/pylock.toml",
            "The dependency lock is incompatible with the selected target interpreter.",
        )
    return CompatibilityDiagnostic(
        "DEPENDENCY_CHECK_INCOMPLETE",
        "binding_required",
        "/pylock.toml",
        "The dependency dry-run did not produce a confirmed compatibility result; inspect target access and retry.",
    )


def inspect_target_environment(
    target: PortableTarget,
    *,
    bundle: PortableBundle | str | Path | None = None,
    verify_development_entrypoints: bool = False,
    credential_resolver: Callable[[str], object] | None = None,
) -> EnvironmentReadinessReport:
    """Inspect the target that exists now with an explicit code-loading boundary.

    ``uv`` dry-run answers whether a lock *could* be installed.  This function
    answers a separate question: whether the selected interpreter currently
    exposes the declared distributions and entry points, and whether a release
    wheel still hashes to the exact immutable tool identity.  It only executes
    a small stdlib metadata probe in the explicitly selected interpreter by
    default.  Set ``verify_development_entrypoints`` only for host-trusted
    development code: that opt-in imports the full module and checks that the
    declared attribute is callable, but never registers or invokes a tool.
    """

    if not isinstance(target, PortableTarget):
        raise WorkflowValidationError(
            "environment inspection requires an explicit PortableTarget"
        )
    if not isinstance(verify_development_entrypoints, bool):
        raise WorkflowValidationError(
            "verify_development_entrypoints must be boolean"
        )
    resolved_bundle: PortableBundle | None = None
    source_identity: Mapping[str, JsonValue] | None = None
    if bundle is not None:
        try:
            resolved_bundle = (
                bundle if isinstance(bundle, PortableBundle) else read_bundle(bundle)
            )
        except (OSError, WorkflowValidationError):
            return EnvironmentReadinessReport(
                "invalid",
                target.target_kind,
                target.python_executable,
                (
                    CompatibilityDiagnostic(
                        "BUNDLE_INVALID",
                        "invalid",
                        "/",
                        "The bundle failed structural or integrity validation.",
                    ),
                ),
            )
        source_identity = {
            "workflow_id": resolved_bundle.workflow.workflow_id,
            "version": resolved_bundle.workflow.version,
            "definition_digest": resolved_bundle.workflow.definition_digest,
            "bundle_schema_version": resolved_bundle.schema_version,
        }

    required_tools = (
        tuple(_manifest_reference(item) for item in resolved_bundle.tools)
        if resolved_bundle is not None
        else target.trusted_tools
    )
    required_drivers = _environment_driver_bindings(resolved_bundle, target)
    runtime_distributions = _workflow_runtime_distributions(resolved_bundle)
    distribution_names = {
        delivery.distribution
        for delivery in target.tool_deliveries
        if delivery.mode == "release" and delivery.distribution is not None
    }
    for binding in required_drivers:
        distribution = _driver_distribution(binding)
        if distribution is not None:
            distribution_names.add(distribution)
        distribution_names.update(
            item.distribution for item in binding.runtime_dependencies
        )
    distribution_names.update(runtime_distributions)
    required_tool_identities = {_tool_identity(reference) for reference in required_tools}
    development_callables = tuple(
        sorted(
            {
                delivery.development_callable
                for delivery in target.tool_deliveries
                if delivery.identity in required_tool_identities
                and delivery.mode == "development"
                and delivery.development_callable is not None
            }
        )
    )
    # Do not pass dotted names to importlib.util.find_spec() or an equivalent
    # helper. Python imports a parent package while locating a submodule, which
    # would violate this method's default metadata-only promise.
    development_modules = {
        callable_path.partition(":")[0].split(".", 1)[0]
        for callable_path in development_callables
    }
    provider_imports = {
        item.import_name
        for binding in required_drivers
        for item in binding.runtime_dependencies
    }
    diagnostics: list[CompatibilityDiagnostic] = []
    probe: Mapping[str, object] | None = None
    python_version: str | None = None
    runtime_construction_checked = False
    development_modules_checked = False
    development_full_modules_checked = False
    development_entrypoints_checked = False
    development_evidence: Mapping[str, Mapping[str, bool]] | None = None
    if target.python_executable is None:
        diagnostics.append(
            CompatibilityDiagnostic(
                "TARGET_PYTHON_BINDING_REQUIRED",
                "binding_required",
                "/target/python_executable",
                "A target Python interpreter is required for environment inspection.",
            )
        )
    else:
        probe_error, probe = _inspect_python_environment(
            target.python_executable,
            sorted(distribution_names),
            sorted(development_modules),
            sorted(provider_imports),
        )
        if probe_error is not None:
            diagnostics.append(
                CompatibilityDiagnostic(
                    "TARGET_PYTHON_UNAVAILABLE",
                    "incompatible",
                    "/target/python_executable",
                    "The target interpreter could not complete the metadata-only readiness probe.",
                )
            )
    if probe is not None:
        raw_version = probe.get("python_version")
        if isinstance(raw_version, str):
            python_version = raw_version
        distributions = probe.get("distributions")
        modules = probe.get("modules")
        imports = probe.get("imports")
        if (
            not isinstance(distributions, Mapping)
            or not isinstance(modules, Mapping)
            or not isinstance(imports, Mapping)
        ):
            diagnostics.append(
                CompatibilityDiagnostic(
                    "TARGET_PYTHON_PROBE_INVALID",
                    "incompatible",
                    "/target/python_executable",
                    "The target interpreter returned malformed package metadata.",
                )
            )
        else:
            development_modules_checked = bool(development_callables)
            _diagnose_driver_environment(
                required_drivers, distributions, imports, diagnostics
            )
            constructable = tuple(
                binding
                for binding in required_drivers
                if binding.driver in {"pydantic-ai", "openai-agents"}
                and not binding.model.startswith("test")
            )
            if constructable:
                runtime_construction_checked = True
                construction = _inspect_driver_runtime(
                    target.python_executable, constructable
                )
                for binding in constructable:
                    if construction.get(binding.binding_key) is not True:
                        diagnostics.append(
                            CompatibilityDiagnostic(
                                "DRIVER_RUNTIME_CONSTRUCTION_FAILED",
                                "incompatible",
                                "/target/driver_bindings",
                                "The bound driver and model could not be constructed locally; verify its provider extra, credential reference, and proxy runtime.",
                            )
                        )
            _diagnose_workflow_runtime_environment(
                runtime_distributions, distributions, diagnostics
            )
            if verify_development_entrypoints and development_callables:
                entrypoint_error, development_evidence = (
                    _inspect_development_entrypoints(
                        target.python_executable, development_callables
                    )
                )
                if entrypoint_error is not None:
                    diagnostics.append(
                        CompatibilityDiagnostic(
                            "DEVELOPMENT_TOOL_ENTRYPOINT_CHECK_UNAVAILABLE",
                            "binding_required",
                            "/target/tool_deliveries",
                            "The opted-in development entry-point check could not complete; repair the target interpreter and retry.",
                        )
                    )
                else:
                    assert development_evidence is not None
                    development_full_modules_checked = True
                    development_entrypoints_checked = all(
                        details["module_locator"] is True
                        for details in development_evidence.values()
                    )
            _diagnose_tool_environment(
                required_tools,
                target,
                distributions,
                modules,
                diagnostics,
                development_evidence,
                verify_development_entrypoints,
            )
    _diagnose_runtime_credentials(
        resolved_bundle,
        target,
        credential_resolver,
        diagnostics,
    )
    status = _report_status(diagnostics)
    return EnvironmentReadinessReport(
        "ready" if status == "compatible" else status,
        target.target_kind,
        target.python_executable,
        tuple(diagnostics),
        source_identity,
        python_version,
        runtime_construction_checked,
        development_modules_checked,
        development_full_modules_checked,
        development_entrypoints_checked,
    )


def _inspect_driver_runtime(
    executable: str, bindings: Sequence[DriverBinding]
) -> Mapping[str, bool]:
    """Construct built-in model adapters locally without making a request."""

    probe = (
        "import json, sys\n"
        "requested = json.loads(sys.argv[1])\n"
        "result = {}\n"
        "for item in requested:\n"
        "    try:\n"
        "        if item['driver'] == 'pydantic-ai':\n"
        "            from pydantic_ai import Agent\n"
        "            Agent(item['model'])\n"
        "        else:\n"
        "            from agents import Agent\n"
        "            Agent(name='readiness-probe', model=item['model'])\n"
        "        result[item['key']] = True\n"
        "    except Exception:\n"
        "        result[item['key']] = False\n"
        "print(json.dumps(result, sort_keys=True))\n"
    )
    payload = [
        {
            "key": binding.binding_key,
            "driver": binding.driver,
            "model": binding.model,
        }
        for binding in bindings
    ]
    try:
        completed = subprocess.run(
            [executable, "-c", probe, json.dumps(payload)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        raw = json.loads(completed.stdout) if completed.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {}
    if not isinstance(raw, Mapping):
        return {}
    return {str(key): value is True for key, value in raw.items()}


def _environment_driver_bindings(
    bundle: PortableBundle | None, target: PortableTarget
) -> tuple[DriverBinding, ...]:
    if bundle is None or bundle.portability is None:
        return target.driver_bindings
    agents = bundle.portability.get("agents", [])
    if not isinstance(agents, list):
        return ()
    addresses: dict[str, tuple[str, str, str]] = {}
    for item in agents:
        if not isinstance(item, Mapping) or not isinstance(item.get("step_id"), str):
            continue
        workflow_id = item.get("workflow_id", bundle.workflow.workflow_id)
        workflow_version = item.get("workflow_version", bundle.workflow.version)
        if isinstance(workflow_id, str) and isinstance(workflow_version, str):
            address = (workflow_id, workflow_version, item["step_id"])
            addresses[agent_address_key(*address)] = address
    resolved: list[DriverBinding] = []
    for address in addresses.values():
        try:
            binding = _driver_binding_for(target, address, addresses)
        except WorkflowValidationError:
            continue
        if binding is not None and binding not in resolved:
            resolved.append(binding)
    return tuple(resolved)


def _workflow_runtime_distributions(
    bundle: PortableBundle | None,
) -> tuple[str, ...]:
    """Infer required execution packages from the frozen workflow contract."""

    if bundle is not None:
        if bundle.workflow.schema_version == V4_SCHEMA_VERSION:
            return (
                *_ASYNC_WORKFLOW_RUNTIME_DISTRIBUTIONS,
                _V4_STATE_RUNTIME_DISTRIBUTION,
            )
        if bundle.workflow.schema_version in ASYNC_SCHEMA_VERSIONS:
            return _ASYNC_WORKFLOW_RUNTIME_DISTRIBUTIONS
    return ()


def _diagnose_workflow_runtime_environment(
    required: Sequence[str],
    distributions: Mapping[object, object],
    diagnostics: list[CompatibilityDiagnostic],
) -> None:
    for distribution in required:
        if not isinstance(distributions.get(distribution), Mapping):
            diagnostics.append(
                CompatibilityDiagnostic(
                    "WORKFLOW_RUNTIME_DEPENDENCY_MISSING",
                    "incompatible",
                    "/workflow/schema_version",
                    "The selected workflow schema requires the target runtime distribution "
                    f"{distribution}.",
                )
            )


def _driver_distribution(binding: DriverBinding) -> str | None:
    return binding.distribution or _KNOWN_DRIVER_DISTRIBUTIONS.get(binding.driver)


def _driver_expected_version(binding: DriverBinding) -> str | None:
    _, separator, version = binding.driver_version.rpartition("@")
    return version if separator and version else None


def _inspect_python_environment(
    executable: str,
    distributions: Sequence[str],
    modules: Sequence[str],
    imports: Sequence[str] = (),
) -> tuple[str | None, Mapping[str, object] | None]:
    """Run a fixed stdlib-only metadata query in a selected interpreter."""

    probe = (
        "from importlib.machinery import PathFinder\n"
        "import importlib, importlib.metadata as metadata, json, sys\n"
        "requested = json.loads(sys.argv[1])\n"
        "result = {'python_version': '.'.join(map(str, sys.version_info[:3])), "
        "'distributions': {}, 'modules': {}, 'imports': {}}\n"
        "for name in requested['distributions']:\n"
        "    try:\n"
        "        dist = metadata.distribution(name)\n"
        "        files = dist.files or ()\n"
        "        result['distributions'][name] = {'version': dist.version, "
        "'entry_points': sorted(f'{item.group}:{item.name}' for item in dist.entry_points), "
        "'record_present': any(str(item).replace('\\\\', '/').endswith('.dist-info/RECORD') for item in files)}\n"
        "    except metadata.PackageNotFoundError:\n"
        "        result['distributions'][name] = None\n"
        "for name in requested['modules']:\n"
        "    result['modules'][name] = PathFinder.find_spec(name, sys.path) is not None\n"
        "for name in requested['imports']:\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        "        result['imports'][name] = True\n"
        "    except Exception:\n"
        "        result['imports'][name] = False\n"
        "print(json.dumps(result, sort_keys=True))\n"
    )
    try:
        completed = subprocess.run(
            [
                executable,
                "-c",
                probe,
                json.dumps(
                    {
                        "distributions": list(distributions),
                        "modules": list(modules),
                        "imports": list(imports),
                    }
                ),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable", None
    if completed.returncode != 0:
        return "unavailable", None
    try:
        raw = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return "invalid", None
    return (None, raw) if isinstance(raw, Mapping) else ("invalid", None)


def _inspect_development_entrypoints(
    executable: str, callable_paths: Sequence[str]
) -> tuple[str | None, Mapping[str, Mapping[str, bool]] | None]:
    """Load explicitly trusted development callables without invoking them.

    The default metadata probe intentionally avoids dotted imports.  This
    separate opt-in probe runs only in the target interpreter. It separately
    checks full-module discovery and then imports the declared module to test
    its named attribute with :func:`callable`. It neither registers the result
    with V6 nor calls business code. Module import itself can run import-time
    code, which is why callers must explicitly designate it host-trusted.
    """

    probe = (
        "import importlib, importlib.util, json, sys\n"
        "requested = json.loads(sys.argv[1])\n"
        "result = {}\n"
        "for item in requested:\n"
        "    details = {'module_locator': False, 'callable': False}\n"
        "    try:\n"
        "        module, attribute = item.rsplit(':', 1)\n"
        "        details['module_locator'] = importlib.util.find_spec(module) is not None\n"
        "    except Exception:\n"
        "        result[item] = details\n"
        "        continue\n"
        "    if details['module_locator']:\n"
        "        try:\n"
        "            details['callable'] = callable(getattr(importlib.import_module(module), attribute))\n"
        "        except Exception:\n"
        "            pass\n"
        "    result[item] = details\n"
        "print(json.dumps(result, sort_keys=True))\n"
    )
    requested = tuple(sorted(set(callable_paths)))
    try:
        completed = subprocess.run(
            [executable, "-c", probe, json.dumps(requested)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable", None
    if completed.returncode != 0:
        return "unavailable", None
    try:
        raw = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return "invalid", None
    if not isinstance(raw, Mapping) or set(raw) != set(requested):
        return "invalid", None
    if any(
        not isinstance(raw[item], Mapping)
        or set(raw[item]) != {"module_locator", "callable"}
        or any(not isinstance(raw[item][key], bool) for key in raw[item])
        for item in requested
    ):
        return "invalid", None
    return (
        None,
        {
            item: {
                "module_locator": raw[item]["module_locator"],
                "callable": raw[item]["callable"],
            }
            for item in requested
        },
    )


def _diagnose_driver_environment(
    bindings: Sequence[DriverBinding],
    distributions: Mapping[object, object],
    imports: Mapping[object, object],
    diagnostics: list[CompatibilityDiagnostic],
) -> None:
    for index, binding in enumerate(bindings):
        path = f"/target/driver_bindings/{index}"
        distribution = _driver_distribution(binding)
        if distribution is None:
            diagnostics.append(
                CompatibilityDiagnostic(
                    "DRIVER_DISTRIBUTION_BINDING_REQUIRED",
                    "binding_required",
                    path + "/distribution",
                    "A custom driver must declare its installed distribution for readiness checks.",
                )
            )
            continue
        raw = distributions.get(distribution)
        if not isinstance(raw, Mapping):
            diagnostics.append(
                CompatibilityDiagnostic(
                    "DRIVER_DISTRIBUTION_MISSING",
                    "incompatible",
                    path + "/distribution",
                    "The target interpreter does not have the bound driver distribution.",
                )
            )
            continue
        expected_version = _driver_expected_version(binding)
        if expected_version is not None and raw.get("version") != expected_version:
            diagnostics.append(
                CompatibilityDiagnostic(
                    "DRIVER_VERSION_MISMATCH",
                    "incompatible",
                    path + "/driver_version",
                    "The installed driver distribution does not match the fixed binding version.",
                )
            )
        if binding.entry_point is not None:
            entries = raw.get("entry_points")
            if not isinstance(entries, list) or binding.entry_point not in entries:
                diagnostics.append(
                    CompatibilityDiagnostic(
                        "DRIVER_ENTRY_POINT_MISSING",
                        "incompatible",
                        path + "/entry_point",
                        "The installed driver distribution lacks the declared entry point.",
                    )
                )
        for dependency in binding.runtime_dependencies:
            dependency_path = path + "/runtime_dependencies"
            if not isinstance(distributions.get(dependency.distribution), Mapping):
                diagnostics.append(
                    CompatibilityDiagnostic(
                        "DRIVER_RUNTIME_DEPENDENCY_MISSING",
                        "incompatible",
                        dependency_path,
                        "The selected model provider dependency is missing; install "
                        + dependency.install_hint
                        + ".",
                    )
                )
            if imports.get(dependency.import_name) is not True:
                diagnostics.append(
                    CompatibilityDiagnostic(
                        "DRIVER_RUNTIME_IMPORT_FAILED",
                        "incompatible",
                        dependency_path,
                        "The selected model provider package is installed but cannot be imported; reinstall "
                        + dependency.install_hint
                        + ".",
                    )
                )
            installed = distributions.get(dependency.distribution)
            if (
                dependency.version is not None
                and isinstance(installed, Mapping)
                and installed.get("version") != dependency.version
            ):
                diagnostics.append(
                    CompatibilityDiagnostic(
                        "DRIVER_RUNTIME_DEPENDENCY_VERSION_MISMATCH",
                        "incompatible",
                        dependency_path,
                        "The selected model provider package version differs from the frozen binding.",
                    )
                )


def _diagnose_tool_environment(
    required_tools: Sequence[ToolReference],
    target: PortableTarget,
    distributions: Mapping[object, object],
    modules: Mapping[object, object],
    diagnostics: list[CompatibilityDiagnostic],
    development_evidence: Mapping[str, Mapping[str, bool]] | None,
    verify_development_entrypoints: bool,
) -> None:
    deliveries = {delivery.identity: delivery for delivery in target.tool_deliveries}
    for index, reference in enumerate(required_tools):
        path = f"/target/trusted_tools/{index}"
        delivery = deliveries.get(_tool_identity(reference))
        if delivery is None:
            diagnostics.append(
                CompatibilityDiagnostic(
                    "TOOL_DELIVERY_BINDING_REQUIRED",
                    "binding_required",
                    path,
                    "The target needs a development or release delivery binding for this exact tool.",
                )
            )
            continue
        if delivery.mode == "development":
            assert delivery.development_callable is not None
            module = delivery.development_callable.partition(":")[0].split(".", 1)[0]
            if modules.get(module) is not True:
                diagnostics.append(
                    CompatibilityDiagnostic(
                        "DEVELOPMENT_TOOL_TOP_LEVEL_MODULE_MISSING",
                        "incompatible",
                        path + "/development_callable",
                        "The target interpreter cannot locate the development tool's top-level module.",
                    )
                )
                continue
            if not verify_development_entrypoints:
                diagnostics.append(
                    CompatibilityDiagnostic(
                        "DEVELOPMENT_TOOL_ENTRYPOINT_NOT_CHECKED",
                        "binding_required",
                        path + "/development_callable",
                        "The default environment check verifies only the top-level module; opt in to trusted callable loading before declaring this delivery ready.",
                    )
                )
            elif development_evidence is not None:
                evidence = development_evidence.get(delivery.development_callable)
                if evidence is None or evidence.get("module_locator") is not True:
                    diagnostics.append(
                        CompatibilityDiagnostic(
                            "DEVELOPMENT_TOOL_FULL_MODULE_UNAVAILABLE",
                            "incompatible",
                            path + "/development_callable",
                            "The target interpreter cannot locate the full declared development module.",
                        )
                    )
                elif evidence.get("callable") is not True:
                    diagnostics.append(
                        CompatibilityDiagnostic(
                            "DEVELOPMENT_TOOL_ENTRYPOINT_UNAVAILABLE",
                            "incompatible",
                            path + "/development_callable",
                            "The target interpreter could not load the declared development callable.",
                        )
                    )
            diagnostics.append(
                CompatibilityDiagnostic(
                    "DEVELOPMENT_TOOL_IMPLEMENTATION_UNVERIFIABLE",
                    "warning",
                    path,
                    "Callable loading does not register, authorize, or invoke development tool code; those host-owned layers remain unverified.",
                )
            )
            continue
        assert delivery.distribution is not None
        assert delivery.entry_point is not None
        assert delivery.wheel_path is not None
        raw = distributions.get(delivery.distribution)
        if not isinstance(raw, Mapping):
            diagnostics.append(
                CompatibilityDiagnostic(
                    "TOOL_DISTRIBUTION_MISSING",
                    "incompatible",
                    path + "/distribution",
                    "The target interpreter does not have the release tool distribution.",
                )
            )
        else:
            if raw.get("version") != reference.version:
                diagnostics.append(
                    CompatibilityDiagnostic(
                        "TOOL_DISTRIBUTION_VERSION_MISMATCH",
                        "incompatible",
                        path + "/distribution",
                        "The installed release tool distribution version does not match the ToolReference.",
                    )
                )
            entries = raw.get("entry_points")
            if not isinstance(entries, list) or delivery.entry_point not in entries:
                diagnostics.append(
                    CompatibilityDiagnostic(
                        "TOOL_ENTRY_POINT_MISSING",
                        "incompatible",
                        path + "/entry_point",
                        "The installed release tool distribution lacks the declared entry point.",
                    )
                )
            if raw.get("record_present") is not True:
                diagnostics.append(
                    CompatibilityDiagnostic(
                        "TOOL_INSTALL_RECORD_UNAVAILABLE",
                        "warning",
                        path + "/distribution",
                        "The installed release distribution has no RECORD metadata; "
                        "wheel identity and entry-point checks cannot independently "
                        "attest its installed file inventory.",
                    )
                )
        digest = _sha256_file(Path(delivery.wheel_path))
        if digest is None:
            diagnostics.append(
                CompatibilityDiagnostic(
                    "TOOL_WHEEL_UNAVAILABLE",
                    "incompatible",
                    path + "/wheel_path",
                    "The release wheel needed to verify this tool identity is unavailable.",
                )
            )
        elif digest != reference.package_digest:
            diagnostics.append(
                CompatibilityDiagnostic(
                    "TOOL_WHEEL_DIGEST_MISMATCH",
                    "incompatible",
                    path + "/wheel_path",
                    "The release wheel digest does not match the immutable ToolReference.",
                )
            )


def _sha256_file(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        value = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                value.update(chunk)
    except OSError:
        return None
    return "sha256:" + value.hexdigest()


def _diagnose_runtime_credentials(
    bundle: PortableBundle | None,
    target: PortableTarget,
    resolver: Callable[[str], object] | None,
    diagnostics: list[CompatibilityDiagnostic],
) -> None:
    if bundle is None:
        return
    references = {
        item.get("credential_ref")
        for item in bundle.tools
        if isinstance(item.get("credential_ref"), str)
    }
    for reference in sorted(references):
        assert isinstance(reference, str)
        if reference not in target.credential_bindings:
            # Contract diagnosis emits the more useful missing-target-binding
            # result; this check only verifies a declared binding at runtime.
            continue
        if resolver is None:
            diagnostics.append(
                CompatibilityDiagnostic(
                    "CREDENTIAL_RUNTIME_CHECK_REQUIRED",
                    "binding_required",
                    "/target/credential_bindings",
                    "A credential resolver must be supplied to verify this runtime binding without exposing its value.",
                )
            )
            continue
        try:
            present = resolver(reference) is not None
        except Exception:  # noqa: BLE001 - do not expose credential backend detail
            present = False
        if not present:
            diagnostics.append(
                CompatibilityDiagnostic(
                    "CREDENTIAL_REFERENCE_UNBOUND",
                    "incompatible",
                    "/target/credential_bindings",
                    "The target credential resolver has no value for a declared reference.",
                )
            )


def _report_status(diagnostics: Sequence[CompatibilityDiagnostic]) -> str:
    classifications = {item.classification for item in diagnostics}
    if "invalid" in classifications:
        return "invalid"
    if "incompatible" in classifications:
        return "incompatible"
    if "binding_required" in classifications:
        return "binding_required"
    return "compatible"


def scaffold_project(
    kind: str,
    destination: str | Path,
    *,
    project_name: str = "agent-workflow",
) -> PortableProject:
    """Render one of the bundled safe Copier templates."""

    if kind not in {"workflow", "tool"}:
        raise WorkflowValidationError("template kind must be workflow or tool")
    _name(project_name, "project name")
    target = Path(destination)
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise WorkflowValidationError("template destination must be new or empty")
    try:
        from copier import run_copy
    except ImportError as error:
        raise RuntimeError(
            "Install platform-lightweight-agent-core[authoring] to use templates"
        ) from error
    template = resources.files("platform_lightweight_agent").joinpath("templates", kind)
    with resources.as_file(template) as template_path:
        run_copy(
            str(template_path),
            target,
            data={"project_name": project_name},
            defaults=True,
            overwrite=False,
            quiet=True,
            unsafe=False,
            skip_tasks=True,
        )
    return check_project(target)


def lock_project(
    source: str | Path,
    *,
    check: bool = False,
    uv_executable: str = "uv",
) -> Path:
    """Delegate dependency resolution to uv and export a PEP 751 lock."""

    project = load_project(source)
    uv = shutil.which(uv_executable)
    if uv is None:
        raise WorkflowValidationError("uv is required for dependency locking")
    _reject_mutable_project_sources(project.root / "pyproject.toml")
    if not check:
        _run_uv([uv, "lock"], project.root)
    else:
        _run_uv([uv, "lock", "--check"], project.root)
    completed = _run_uv(
        [
            uv,
            "export",
            "--format",
            "pylock.toml",
            "--locked",
            "--no-dev",
            "--no-emit-project",
        ],
        project.root,
    )
    generated = completed.stdout.replace("\r\n", "\n")
    _read_dependency_lock(generated)
    if check:
        try:
            existing = project.dependency_lock_path.read_text(encoding="utf-8").replace(
                "\r\n", "\n"
            )
        except OSError as error:
            raise WorkflowValidationError(
                "project dependency lock is missing"
            ) from error
        if existing != generated:
            raise WorkflowValidationError("project dependency lock is stale")
    else:
        project.dependency_lock_path.write_text(generated, encoding="utf-8")
    return project.dependency_lock_path


def _run_uv(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WorkflowValidationError("uv operation could not complete") from error
    if completed.returncode != 0:
        raise WorkflowValidationError("uv operation failed; inspect uv output locally")
    return completed


def _reject_mutable_project_sources(pyproject_path: Path) -> None:
    raw = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    dependencies = raw.get("project", {}).get("dependencies", [])
    if isinstance(dependencies, list):
        for dependency in dependencies:
            if not isinstance(dependency, str):
                continue
            lowered = dependency.lower()
            if " @ file:" in lowered or " @ ../" in lowered or " @ ./" in lowered:
                raise WorkflowValidationError(
                    "local directory dependencies are not portable"
                )
            if (
                "git+" in lowered
                and re.search(r"@[0-9a-f]{40,64}(?:#|$)", lowered) is None
            ):
                raise WorkflowValidationError(
                    "mutable VCS dependencies are not portable"
                )
    sources = raw.get("tool", {}).get("uv", {}).get("sources", {})
    if isinstance(sources, Mapping):
        for source in sources.values():
            if not isinstance(source, Mapping):
                continue
            if "path" in source or source.get("editable") is True:
                raise WorkflowValidationError(
                    "local uv dependency sources are not portable"
                )
            if (
                "git" in source
                and re.fullmatch(r"[0-9a-fA-F]{40,64}", str(source.get("rev", "")))
                is None
            ):
                raise WorkflowValidationError("mutable uv VCS sources are not portable")


def build_tool_package(
    source: str | Path, *, uv_executable: str = "uv"
) -> Mapping[str, JsonValue]:
    """Build a tool wheel with uv and emit its exact portable manifest."""

    project = check_project(source)
    if project.kind != "tool" or project.tool_manifest_path is None:
        raise WorkflowValidationError("build-tool requires a tool project")
    uv = shutil.which(uv_executable)
    if uv is None:
        raise WorkflowValidationError("uv is required to build a tool package")
    output = project.root / "dist"
    output.mkdir(parents=True, exist_ok=True)
    _run_uv([uv, "build", "--wheel", "--out-dir", str(output)], project.root)
    wheels = sorted(output.glob("*.whl"), key=lambda item: item.stat().st_mtime_ns)
    if not wheels:
        raise WorkflowValidationError("uv did not produce a tool wheel")
    wheel = wheels[-1]
    # A wheel cannot truthfully embed its own SHA-256: changing an embedded
    # digest changes the wheel bytes again.  The source file is therefore a
    # logical descriptor (tool ID/version/permissions/capabilities), while the
    # generated sidecar below is the immutable build artifact manifest.
    descriptor = _normalize_tool_descriptor(
        _load_json(project.tool_manifest_path, "tool descriptor")
    )
    manifest = json_copy(descriptor)
    manifest["package_digest"] = (
        "sha256:" + hashlib.sha256(wheel.read_bytes()).hexdigest()
    )
    normalized = _normalize_tools((manifest,))[0]
    (output / "tool-manifest.json").write_text(
        canonical_json(normalized) + "\n", encoding="utf-8"
    )
    return normalized


def _normalize_tool_descriptor(value: object) -> dict[str, JsonValue]:
    """Validate a build-time descriptor without accepting an artifact digest.

    ``_normalize_tools`` remains the sole schema authority for a tool's
    metadata.  Supplying a synthetic digest lets this thin adapter reuse it
    while ensuring the source descriptor cannot accidentally claim a release
    artifact identity.  The all-zero field used by older generated templates
    is accepted only as a migration placeholder and is removed.
    """

    if not isinstance(value, Mapping):
        raise WorkflowValidationError("tool descriptor must be an object")
    copied = json_copy(value)
    if not isinstance(copied, dict):  # pragma: no cover - json_copy guard
        raise WorkflowValidationError("tool descriptor must be an object")
    declared = copied.pop("package_digest", None)
    placeholder = "sha256:" + "0" * 64
    if declared is not None and declared != placeholder:
        raise WorkflowValidationError(
            "tool descriptor must not declare a built artifact digest"
        )
    copied["package_digest"] = "sha256:" + "f" * 64
    normalized = dict(_normalize_tools((copied,))[0])
    normalized.pop("package_digest", None)
    return normalized


def _walk_steps(steps: Sequence[Any]):
    for step in steps:
        yield step
        for branch in step.children:
            yield from _walk_steps(branch)


def current_python_target() -> str:
    """Convenience for generated local target configurations."""

    return sys.executable
