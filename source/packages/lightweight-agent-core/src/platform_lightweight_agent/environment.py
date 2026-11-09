"""Small, inspectable runtime identity for local recovery compatibility."""

from __future__ import annotations

import hashlib
import platform
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from .journal import SQLiteRunJournal
from .portability import DriverBinding
from .spec import JsonValue, WorkflowValidationError

RUNTIME_ENVIRONMENT_SCHEMA_V1 = "v6-runtime-environment-v1"
RUNTIME_ENVIRONMENT_REPORT_V1 = "v6-runtime-environment-report-v1"
_CORE_DISTRIBUTION = "platform-lightweight-agent-core"
_KNOWN_DRIVER_DISTRIBUTIONS = {
    "pydantic-ai": "pydantic-ai-slim",
    "openai-agents": "openai-agents",
}


@dataclass(frozen=True)
class RuntimePackageIdentity:
    distribution: str
    role: str
    installed_version: str | None
    declared_version: str | None
    resume_requirement: str

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "distribution": self.distribution,
            "role": self.role,
            "installed_version": self.installed_version,
            "declared_version": self.declared_version,
            "resume_requirement": self.resume_requirement,
        }

    @classmethod
    def from_mapping(cls, value: object) -> RuntimePackageIdentity:
        required = {
            "distribution",
            "role",
            "installed_version",
            "declared_version",
            "resume_requirement",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise WorkflowValidationError("runtime package identity is invalid")
        for key in ("distribution", "role", "resume_requirement"):
            if not isinstance(value[key], str) or not value[key]:
                raise WorkflowValidationError("runtime package identity is invalid")
        if value["role"] not in {"core", "driver", "provider"}:
            raise WorkflowValidationError("runtime package role is invalid")
        for key in ("installed_version", "declared_version"):
            if value[key] is not None and not isinstance(value[key], str):
                raise WorkflowValidationError("runtime package identity is invalid")
        if value["resume_requirement"] not in {"required", "warning"}:
            raise WorkflowValidationError("runtime package requirement is invalid")
        return cls(
            value["distribution"],  # type: ignore[arg-type]
            value["role"],  # type: ignore[arg-type]
            value["installed_version"],  # type: ignore[arg-type]
            value["declared_version"],  # type: ignore[arg-type]
            value["resume_requirement"],  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class RuntimeEnvironmentIdentity:
    core_version: str | None
    python_implementation: str
    python_version: str
    python_cache_tag: str | None
    platform_system: str
    platform_machine: str
    dependency_lock_sha256: str | None
    packages: tuple[RuntimePackageIdentity, ...]

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "schema_version": RUNTIME_ENVIRONMENT_SCHEMA_V1,
            "core_version": self.core_version,
            "python": {
                "implementation": self.python_implementation,
                "version": self.python_version,
                "cache_tag": self.python_cache_tag,
            },
            "platform": {
                "system": self.platform_system,
                "machine": self.platform_machine,
            },
            "dependency_lock_sha256": self.dependency_lock_sha256,
            "packages": [item.to_mapping() for item in self.packages],
        }

    @classmethod
    def from_mapping(cls, value: object) -> RuntimeEnvironmentIdentity:
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version",
            "core_version",
            "python",
            "platform",
            "dependency_lock_sha256",
            "packages",
        } or value.get("schema_version") != RUNTIME_ENVIRONMENT_SCHEMA_V1:
            raise WorkflowValidationError("runtime environment identity is invalid")
        python = value["python"]
        target_platform = value["platform"]
        packages = value["packages"]
        if (
            not isinstance(python, Mapping)
            or set(python) != {"implementation", "version", "cache_tag"}
            or not isinstance(target_platform, Mapping)
            or set(target_platform) != {"system", "machine"}
            or not isinstance(packages, list)
        ):
            raise WorkflowValidationError("runtime environment identity is invalid")
        strings = (
            python["implementation"],
            python["version"],
            target_platform["system"],
            target_platform["machine"],
        )
        if any(not isinstance(item, str) for item in strings):
            raise WorkflowValidationError("runtime environment identity is invalid")
        optional = (
            value["core_version"],
            python["cache_tag"],
            value["dependency_lock_sha256"],
        )
        if any(item is not None and not isinstance(item, str) for item in optional):
            raise WorkflowValidationError("runtime environment identity is invalid")
        parsed_packages = tuple(
            RuntimePackageIdentity.from_mapping(item) for item in packages
        )
        if len({(item.role, item.distribution) for item in parsed_packages}) != len(
            parsed_packages
        ):
            raise WorkflowValidationError("runtime package identities must be unique")
        core = [
            item
            for item in parsed_packages
            if item.role == "core" and item.distribution == _CORE_DISTRIBUTION
        ]
        if (
            len(core) != 1
            or core[0].resume_requirement != "required"
            or core[0].installed_version != value["core_version"]
        ):
            raise WorkflowValidationError("runtime core identity is invalid")
        return cls(
            value["core_version"],  # type: ignore[arg-type]
            python["implementation"],  # type: ignore[arg-type]
            python["version"],  # type: ignore[arg-type]
            python["cache_tag"],  # type: ignore[arg-type]
            target_platform["system"],  # type: ignore[arg-type]
            target_platform["machine"],  # type: ignore[arg-type]
            value["dependency_lock_sha256"],  # type: ignore[arg-type]
            parsed_packages,
        )


@dataclass(frozen=True)
class RuntimeEnvironmentDiagnostic:
    code: str
    severity: str
    subject: str

    def to_mapping(self) -> dict[str, JsonValue]:
        return {"code": self.code, "severity": self.severity, "subject": self.subject}


@dataclass(frozen=True)
class RuntimeEnvironmentReport:
    status: str
    expected: RuntimeEnvironmentIdentity
    current: RuntimeEnvironmentIdentity
    diagnostics: tuple[RuntimeEnvironmentDiagnostic, ...]

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "schema_version": RUNTIME_ENVIRONMENT_REPORT_V1,
            "status": self.status,
            "diagnostics": [item.to_mapping() for item in self.diagnostics],
            "expected": self.expected.to_mapping(),
            "current": self.current.to_mapping(),
            "effects": "none",
        }


def capture_runtime_environment(
    journal: SQLiteRunJournal, *, dependency_lock: str | Path | None = None
) -> RuntimeEnvironmentIdentity:
    """Capture only packages needed by retained runs, never the whole host."""

    bindings: dict[str, DriverBinding] = {}
    frozen_lock_digests: set[str] = set()
    for run_id in journal.retained_run_ids():
        snapshot = journal.get(run_id)
        runtime_binding = snapshot.execution_manifest.get("runtime_binding")
        if isinstance(runtime_binding, Mapping):
            frozen_lock = runtime_binding.get("dependency_lock_sha256")
            if isinstance(frozen_lock, str):
                frozen_lock_digests.add(frozen_lock)
        for binding in _walk_driver_bindings(snapshot.execution_manifest):
            bindings[binding.binding_key] = binding
    packages: dict[tuple[str, str], RuntimePackageIdentity] = {}
    core_version = _installed_version(_CORE_DISTRIBUTION)
    packages[("core", _CORE_DISTRIBUTION)] = RuntimePackageIdentity(
        _CORE_DISTRIBUTION, "core", core_version, core_version, "required"
    )
    for binding in bindings.values():
        distribution = binding.distribution or _KNOWN_DRIVER_DISTRIBUTIONS.get(
            binding.driver
        )
        if distribution is not None:
            declared = binding.driver_version.rpartition("@")[2] or None
            packages[("driver", distribution)] = RuntimePackageIdentity(
                distribution,
                "driver",
                _installed_version(distribution),
                declared,
                "required",
            )
        for dependency in binding.runtime_dependencies:
            packages[("provider", dependency.distribution)] = RuntimePackageIdentity(
                dependency.distribution,
                "provider",
                _installed_version(dependency.distribution),
                dependency.version,
                "warning",
            )
    frozen_lock = (
        next(iter(frozen_lock_digests))
        if len(frozen_lock_digests) == 1
        else "sha256:"
        + hashlib.sha256("\n".join(sorted(frozen_lock_digests)).encode()).hexdigest()
        if frozen_lock_digests
        else None
    )
    return _identity(
        tuple(packages[key] for key in sorted(packages)),
        dependency_lock,
        fallback_lock_digest=frozen_lock,
    )


def compare_runtime_environment(
    expected: RuntimeEnvironmentIdentity,
    *,
    dependency_lock: str | Path | None = None,
) -> RuntimeEnvironmentReport:
    """Compare current required package identities; never install or upgrade."""

    current_packages = tuple(
        RuntimePackageIdentity(
            item.distribution,
            item.role,
            _installed_version(item.distribution),
            item.declared_version,
            item.resume_requirement,
        )
        for item in expected.packages
    )
    current = _identity(current_packages, dependency_lock)
    diagnostics: list[RuntimeEnvironmentDiagnostic] = []
    current_by_key = {
        (item.role, item.distribution): item for item in current.packages
    }
    for item in expected.packages:
        observed = current_by_key[(item.role, item.distribution)]
        if observed.installed_version == item.installed_version:
            continue
        severity = "error" if item.resume_requirement == "required" else "warning"
        code = (
            "RUNTIME_REQUIRED_PACKAGE_MISMATCH"
            if severity == "error"
            else "RUNTIME_PROVIDER_VERSION_CHANGED"
        )
        diagnostics.append(
            RuntimeEnvironmentDiagnostic(code, severity, item.distribution)
        )
    if (
        expected.dependency_lock_sha256 is not None
        and current.dependency_lock_sha256 != expected.dependency_lock_sha256
    ):
        diagnostics.append(
            RuntimeEnvironmentDiagnostic(
                "RUNTIME_DEPENDENCY_LOCK_CHANGED", "warning", "dependency_lock"
            )
        )
    if (
        expected.python_implementation != current.python_implementation
        or _major_minor(expected.python_version) != _major_minor(current.python_version)
    ):
        diagnostics.append(
            RuntimeEnvironmentDiagnostic(
                "RUNTIME_PYTHON_CHANGED", "warning", "python"
            )
        )
    if (
        expected.platform_system != current.platform_system
        or expected.platform_machine != current.platform_machine
    ):
        diagnostics.append(
            RuntimeEnvironmentDiagnostic(
                "RUNTIME_PLATFORM_CHANGED", "warning", "platform"
            )
        )
    status = (
        "incompatible"
        if any(item.severity == "error" for item in diagnostics)
        else "warning"
        if diagnostics
        else "compatible"
    )
    return RuntimeEnvironmentReport(status, expected, current, tuple(diagnostics))


def _identity(
    packages: tuple[RuntimePackageIdentity, ...],
    dependency_lock: str | Path | None,
    *,
    fallback_lock_digest: str | None = None,
) -> RuntimeEnvironmentIdentity:
    return RuntimeEnvironmentIdentity(
        _installed_version(_CORE_DISTRIBUTION),
        platform.python_implementation(),
        platform.python_version(),
        sys.implementation.cache_tag,
        platform.system(),
        platform.machine(),
        _lock_digest(dependency_lock) if dependency_lock is not None else fallback_lock_digest,
        packages,
    )


def _walk_driver_bindings(value: object) -> Iterator[DriverBinding]:
    if isinstance(value, Mapping):
        if {"step_id", "driver", "driver_version", "model", "capabilities"} <= set(
            value
        ):
            try:
                yield DriverBinding.from_mapping(value)
            except WorkflowValidationError:
                pass
        for child in value.values():
            yield from _walk_driver_bindings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_driver_bindings(child)


def _installed_version(distribution: str) -> str | None:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


def _lock_digest(path: str | Path | None) -> str | None:
    if path is None:
        return None
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise WorkflowValidationError("dependency lock must be a regular file")
    value = hashlib.sha256(candidate.read_bytes()).hexdigest()
    return "sha256:" + value


def _major_minor(version: str) -> tuple[str, str]:
    parts = version.split(".")
    return (parts[0] if parts else "", parts[1] if len(parts) > 1 else "")
