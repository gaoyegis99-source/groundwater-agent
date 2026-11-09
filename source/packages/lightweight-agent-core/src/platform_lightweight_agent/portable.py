"""Portable V6 workflow bundles.

Bundles contain declarative workflow data, exact tool-package references and
explicit input files.  They intentionally do *not* carry Python callables,
credentials, SDK state, or an in-flight run checkpoint.  A target environment
must install and trust the referenced tool packages independently.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import tomllib
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from .spec import (
    JsonValue,
    StepKind,
    ToolRecoveryCapabilities,
    ToolReference,
    WorkflowSpec,
    WorkflowValidationError,
    canonical_json,
    contains_secret_name,
    digest,
    json_copy,
)

BUNDLE_V1 = "v6-light-bundle-v1"
BUNDLE_V2 = "v6-light-bundle-v2"
PORTABILITY_CONTRACT_V1 = "v6-portability-contract-v1"
_SCHEMA = BUNDLE_V1
_ENTRY_MANIFEST = "manifest.json"
_ENTRY_WORKFLOW = "workflow.json"
_ENTRY_TOOLS = "tools.json"
_ENTRY_DEPENDENCIES = "dependencies.json"
_ENTRY_PORTABILITY = "portability.json"
_ENTRY_LOCK = "pylock.toml"
_FILES_PREFIX = "files/"
# The archive reader works on the uncompressed payload, so use the same bound
# when producing either bundle format.  This prevents a producer from making a
# bundle which its matching reader must reject, and bounds zip-bomb expansion
# before any payload is read into memory.
_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_MAX_V2_ARCHIVE_BYTES = _MAX_ARCHIVE_BYTES
_MAX_ARCHIVE_MEMBERS = 4_096
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)(?:api[-_ ]?key|authorization|password|private[-_ ]?key|secret|token)"
    r"\s*[:=]\s*\S+"
)


@dataclass(frozen=True)
class PortableBundle:
    """Validated contents of an exported or imported portable bundle."""

    workflow: WorkflowSpec
    tools: tuple[Mapping[str, JsonValue], ...]
    dependencies: Mapping[str, JsonValue]
    files: Mapping[str, str]
    manifest: Mapping[str, JsonValue]
    portability: Mapping[str, JsonValue] | None = None
    dependency_lock: str | None = None

    @property
    def schema_version(self) -> str:
        """Return the bundle wire format without inspecting raw metadata."""

        return str(self.manifest["schema_version"])


@dataclass(frozen=True)
class BundleTarget:
    """Explicit target-environment bindings required before an imported run."""

    trusted_tools: tuple[ToolReference, ...]
    allowed_permissions: frozenset[str] = frozenset()
    credential_bindings: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if any(not isinstance(item, ToolReference) for item in self.trusted_tools):
            raise WorkflowValidationError(
                "target trusted tools must be exact tool references"
            )
        identities = {
            (item.tool_id, item.version, item.package_digest)
            for item in self.trusted_tools
        }
        if len(identities) != len(self.trusted_tools):
            raise WorkflowValidationError(
                "target trusted tools must have unique exact references"
            )
        for field_name in ("allowed_permissions", "credential_bindings"):
            values = frozenset(getattr(self, field_name))
            if any(
                not isinstance(value, str) or _PORTABLE_NAME.fullmatch(value) is None
                for value in values
            ):
                raise WorkflowValidationError(
                    f"target {field_name} must be portable identifiers"
                )
            object.__setattr__(self, field_name, values)


def export_bundle(
    output: str | Path,
    workflow: WorkflowSpec,
    *,
    tools: Sequence[Mapping[str, Any]] = (),
    dependencies: Mapping[str, Any] | None = None,
    files_root: str | Path | None = None,
    files: Iterable[str | Path] = (),
    bundle_format: str = BUNDLE_V1,
    agent_instructions: Mapping[str, str] | None = None,
    subworkflows: Sequence[WorkflowSpec] = (),
    dependency_lock: str | Path | None = None,
) -> PortableBundle:
    """Create a deterministic, credential-free ``.zip`` portable bundle.

    ``files`` are relative paths under ``files_root``.  They represent inputs
    needed by a workflow, never executable tool code.  Existing output files
    are deliberately replaced only by this explicit export operation.
    """

    if bundle_format == BUNDLE_V2:
        return _export_v2(
            Path(output),
            workflow,
            tools=tools,
            files_root=files_root,
            files=files,
            agent_instructions=agent_instructions or {},
            subworkflows=subworkflows,
            dependency_lock=dependency_lock,
        )
    if bundle_format != BUNDLE_V1:
        raise WorkflowValidationError("portable bundle format is unsupported")
    if agent_instructions or subworkflows or dependency_lock is not None:
        raise WorkflowValidationError(
            "Agent instructions, subworkflows, and pylock require bundle v2"
        )
    normalized_tools = _normalize_tools(tools)
    _require_declared_tools(workflow, normalized_tools)
    normalized_dependencies = _json_object(dependencies or {}, "dependencies")
    if contains_secret_name(normalized_dependencies):
        raise WorkflowValidationError(
            "portable dependencies must not contain credentials"
        )
    selected_files = _collect_files(files_root, files)
    file_manifest = {
        relative: {
            "sha256": _file_digest(path),
            "bytes": path.stat().st_size,
        }
        for relative, path in selected_files.items()
    }
    manifest: dict[str, JsonValue] = {
        "schema_version": _SCHEMA,
        "workflow_digest": workflow.definition_digest,
        "tools_digest": digest(normalized_tools),
        "dependencies_digest": digest(normalized_dependencies),
        "files": file_manifest,
    }
    entries: dict[str, bytes] = {
        _ENTRY_MANIFEST: canonical_json(manifest).encode("utf-8"),
        _ENTRY_WORKFLOW: canonical_json(workflow.to_mapping()).encode("utf-8"),
        _ENTRY_TOOLS: canonical_json(normalized_tools).encode("utf-8"),
        _ENTRY_DEPENDENCIES: canonical_json(normalized_dependencies).encode("utf-8"),
    }
    for relative, path in selected_files.items():
        entries[_FILES_PREFIX + relative] = path.read_bytes()
    _write_archive(Path(output), entries)
    return PortableBundle(
        workflow=workflow,
        tools=tuple(normalized_tools),
        dependencies=normalized_dependencies,
        files={name: str(details["sha256"]) for name, details in file_manifest.items()},
        manifest=manifest,
    )


def _export_v2(
    output: Path,
    workflow: WorkflowSpec,
    *,
    tools: Sequence[Mapping[str, Any]],
    files_root: str | Path | None,
    files: Iterable[str | Path],
    agent_instructions: Mapping[str, str],
    subworkflows: Sequence[WorkflowSpec],
    dependency_lock: str | Path | None,
) -> PortableBundle:
    """Create the opt-in definition-only portability bundle."""

    normalized_tools = _normalize_tools(tools)
    _validate_v2_tool_metadata(normalized_tools)
    _require_declared_tools(workflow, normalized_tools)
    for child in subworkflows:
        _require_declared_tools(child, normalized_tools)
    lock_text = _read_dependency_lock(dependency_lock)
    portability = _build_portability_contract(
        workflow, agent_instructions, subworkflows
    )
    selected_files = _collect_files(files_root, files)
    payloads: dict[str, bytes] = {
        _ENTRY_WORKFLOW: canonical_json(workflow.to_mapping()).encode("utf-8"),
        _ENTRY_TOOLS: canonical_json(normalized_tools).encode("utf-8"),
        _ENTRY_PORTABILITY: canonical_json(portability).encode("utf-8"),
        _ENTRY_LOCK: lock_text.encode("utf-8"),
    }
    for relative, path in selected_files.items():
        payloads[_FILES_PREFIX + relative] = path.read_bytes()
    total_bytes = sum(len(content) for content in payloads.values())
    if total_bytes > _MAX_ARCHIVE_BYTES:
        raise WorkflowValidationError("portable bundle v2 exceeds 64 MiB")
    manifest: dict[str, JsonValue] = {
        "schema_version": BUNDLE_V2,
        "source": {
            "workflow_id": workflow.workflow_id,
            "version": workflow.version,
            "workflow_schema_version": workflow.schema_version,
            "definition_digest": workflow.definition_digest,
        },
        "entries": {
            name: {
                "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }
            for name, content in sorted(payloads.items())
        },
    }
    manifest_bytes = canonical_json(manifest).encode("utf-8")
    if total_bytes + len(manifest_bytes) > _MAX_ARCHIVE_BYTES:
        raise WorkflowValidationError("portable bundle v2 exceeds 64 MiB")
    entries = {_ENTRY_MANIFEST: manifest_bytes, **payloads}
    _write_archive(output, entries)
    return PortableBundle(
        workflow=workflow,
        tools=tuple(normalized_tools),
        dependencies={},
        files={
            relative: str(manifest["entries"][_FILES_PREFIX + relative]["sha256"])
            for relative in selected_files
        },
        manifest=manifest,
        portability=portability,
        dependency_lock=lock_text,
    )


def _read_dependency_lock(source: str | Path | None) -> str:
    if source is None:
        raise WorkflowValidationError("bundle v2 requires a PEP 751 pylock.toml")
    if isinstance(source, Path):
        try:
            text = source.read_text(encoding="utf-8")
        except OSError as error:
            raise WorkflowValidationError("dependency lock cannot be read") from error
    elif "\n" not in source and Path(source).is_file():
        try:
            text = Path(source).read_text(encoding="utf-8")
        except OSError as error:
            raise WorkflowValidationError("dependency lock cannot be read") from error
    else:
        text = source
    try:
        parsed = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, UnicodeError) as error:
        raise WorkflowValidationError("dependency lock is not valid TOML") from error
    if parsed.get("lock-version") != "1.0" or not isinstance(
        parsed.get("requires-python"), str
    ):
        raise WorkflowValidationError(
            "dependency lock must use PEP 751 lock-version 1.0"
        )
    if contains_secret_name(parsed) or _contains_authenticated_url(parsed):
        raise WorkflowValidationError("dependency lock must not contain credentials")
    _reject_mutable_lock_sources(parsed)
    return text.replace("\r\n", "\n")


def _contains_authenticated_url(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_authenticated_url(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_authenticated_url(child) for child in value)
    if isinstance(value, str) and "://" in value:
        try:
            return urlsplit(value).username is not None
        except ValueError:
            return True
    return False


def _contains_local_url(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_local_url(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_local_url(child) for child in value)
    return isinstance(value, str) and value.lower().startswith("file:")


def _reject_mutable_lock_sources(lock: Mapping[str, object]) -> None:
    packages = lock.get("packages", [])
    if not isinstance(packages, list):
        raise WorkflowValidationError("dependency lock packages must be a list")
    for package in packages:
        if not isinstance(package, Mapping):
            raise WorkflowValidationError("dependency lock package must be an object")
        if _contains_local_url(package):
            raise WorkflowValidationError(
                "bundle v2 rejects local dependency archive URLs"
            )
        if "directory" in package or package.get("editable") is True:
            raise WorkflowValidationError(
                "bundle v2 rejects local directory dependency sources"
            )
        vcs_entry = package.get("vcs")
        if isinstance(vcs_entry, Mapping):
            commit = vcs_entry.get("commit-id") or vcs_entry.get("commit_id")
            if (
                not isinstance(commit, str)
                or re.fullmatch(r"[0-9a-fA-F]{40,64}", commit) is None
            ):
                raise WorkflowValidationError(
                    "bundle v2 rejects mutable VCS dependency sources"
                )
        source = package.get("source")
        if not isinstance(source, Mapping):
            continue
        if "directory" in source or "editable" in source:
            raise WorkflowValidationError(
                "bundle v2 rejects local directory dependency sources"
            )
        vcs = source.get("git") or source.get("url")
        if isinstance(vcs, str) and vcs.startswith("git+"):
            revision = source.get("rev") or source.get("commit")
            if (
                not isinstance(revision, str)
                or re.fullmatch(r"[0-9a-fA-F]{40,64}", revision) is None
            ):
                raise WorkflowValidationError(
                    "bundle v2 rejects mutable VCS dependency sources"
                )


def _build_portability_contract(
    workflow: WorkflowSpec,
    instructions: Mapping[str, str],
    subworkflows: Sequence[WorkflowSpec],
) -> dict[str, JsonValue]:
    if not isinstance(instructions, Mapping):
        raise WorkflowValidationError("agent instructions must be an object")
    catalog = {(item.workflow_id, item.version): item for item in subworkflows}
    if len(catalog) != len(subworkflows):
        raise WorkflowValidationError("subworkflow catalog has duplicate identities")
    agent_entries: list[dict[str, JsonValue]] = []
    subworkflow_entries: dict[tuple[str, str], dict[str, JsonValue]] = {}
    agent_nodes: list[tuple[WorkflowSpec, Any]] = []
    visited_workflows: set[tuple[str, str]] = set()

    def visit_workflow(current: WorkflowSpec) -> None:
        identity = (current.workflow_id, current.version)
        if identity in visited_workflows:
            return
        visited_workflows.add(identity)
        for step in _walk_steps(current.steps):
            if step.kind is StepKind.AGENT:
                if current.schema_version == "v6-light-workflow-v1":
                    raise WorkflowValidationError(
                        "bundle v2 cannot infer a complete contract for a v1 Agent step"
                    )
                agent_nodes.append((current, step))
            elif step.kind is StepKind.SUBWORKFLOW:
                key = (str(step.config["workflow_id"]), str(step.config["version"]))
                child = catalog.get(key)
                if child is None:
                    raise WorkflowValidationError(
                        "bundle v2 requires every referenced subworkflow definition"
                    )
                if key not in subworkflow_entries:
                    entry: dict[str, JsonValue] = {
                        "workflow_id": child.workflow_id,
                        "version": child.version,
                        "definition_digest": child.definition_digest,
                        "workflow_schema_version": child.schema_version,
                    }
                    if child.input_schema is not None:
                        entry["input_schema"] = dict(child.input_schema)
                    if child.output_schema is not None:
                        entry["output_schema"] = dict(child.output_schema)
                    subworkflow_entries[key] = entry
                    visit_workflow(child)

    visit_workflow(workflow)
    step_counts: dict[str, int] = {}
    for _current, step in agent_nodes:
        step_counts[step.step_id] = step_counts.get(step.step_id, 0) + 1
    consumed_instruction_keys: set[str] = set()
    for current, step in agent_nodes:
        address = _agent_address_key(current.workflow_id, current.version, step.step_id)
        if address in instructions:
            instruction_key = address
        elif step_counts[step.step_id] == 1 and step.step_id in instructions:
            # Existing v2 callers can keep a short key when it identifies one
            # definition node.  Exports with repeated child names must be
            # explicit instead of silently routing the wrong instructions.
            instruction_key = step.step_id
        else:
            instruction_key = address
        text = instructions.get(instruction_key)
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text.encode("utf-8")) > 64 * 1024
            or _CREDENTIAL_ASSIGNMENT.search(text) is not None
        ):
            raise WorkflowValidationError(
                f"Agent definition {address} requires bounded instructions"
            )
        consumed_instruction_keys.add(instruction_key)
        output_schema: Mapping[str, JsonValue]
        if current.schema_version == "v6-light-workflow-v2":
            output_schema = {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"message": {"type": "string", "maxLength": 64000}},
                "required": ["message"],
                "additionalProperties": False,
            }
        else:
            assert step.contract is not None
            output_schema = step.contract.output_schema
        agent_entries.append(
            {
                "workflow_id": current.workflow_id,
                "workflow_version": current.version,
                "step_id": step.step_id,
                "source_driver": str(step.config["driver"]),
                "instructions": text,
                "output_schema": dict(output_schema),
                "tools": json_copy(step.config.get("tools", [])),
                "budget": _portable_agent_budget(step.config),
                "required_capabilities": [
                    "host_managed_execution",
                    "native_tools",
                    "structured_output",
                ],
            }
        )
    unknown = set(instructions) - consumed_instruction_keys
    if unknown:
        raise WorkflowValidationError("agent instructions contain an unknown step id")
    return {
        "schema_version": PORTABILITY_CONTRACT_V1,
        "source_workflow": {
            "workflow_id": workflow.workflow_id,
            "version": workflow.version,
            "workflow_schema_version": workflow.schema_version,
            "definition_digest": workflow.definition_digest,
        },
        "agents": agent_entries,
        "subworkflows": [
            subworkflow_entries[key] for key in sorted(subworkflow_entries)
        ],
        "runtime_state": "excluded",
    }


def _walk_steps(steps):  # type: ignore[no-untyped-def]
    for step in steps:
        yield step
        for branch in step.children:
            yield from _walk_steps(branch)


def _agent_address_key(workflow_id: str, workflow_version: str, step_id: str) -> str:
    """Local wire helper; components are validated by WorkflowSpec."""

    return f"{workflow_id}@{workflow_version}:{step_id}"


def import_bundle(
    source: str | Path,
    target_directory: str | Path,
    *,
    target_capabilities: object | None = None,
    check_dependencies: bool = True,
) -> PortableBundle:
    """Validate a bundle and unpack it into a new or empty directory.

    The function rejects traversal paths, duplicate archive members, checksums,
    credentials in metadata, and a non-empty target.  Tool packages are not
    installed as an import side effect.
    """

    entries, bundle = _read_bundle_entries(source)
    if bundle.schema_version == BUNDLE_V2:
        if target_capabilities is None:
            raise WorkflowValidationError(
                "bundle v2 import requires an explicit PortableTarget"
            )
        from .portability import PortableTarget, diagnose_bundle

        if not isinstance(target_capabilities, PortableTarget):
            raise WorkflowValidationError(
                "bundle v2 import requires an explicit PortableTarget"
            )
        report = diagnose_bundle(
            bundle,
            target_capabilities,
            check_dependencies=check_dependencies,
        )
        if report.status != "compatible":
            raise WorkflowValidationError(
                f"bundle v2 target is {report.status}; run diagnose for details"
            )
    elif target_capabilities is not None:
        if not isinstance(target_capabilities, BundleTarget):
            raise WorkflowValidationError("bundle v1 requires a BundleTarget")
        validate_bundle_target(bundle, target_capabilities)
    destination = Path(target_directory)
    _prepare_import_destination(destination)
    staging = _new_import_staging_directory(destination, "bundle")
    published = False
    try:
        _extract_entries(entries, staging)
        _publish_import_directory(staging, destination)
        published = True
    finally:
        if not published:
            _discard_import_staging_directory(staging)
    return bundle


def read_bundle(source: str | Path) -> PortableBundle:
    """Validate a bundle without extracting it or checking a target binding."""

    return _read_bundle_entries(source)[1]


def _read_bundle_entries(source: str | Path) -> tuple[dict[str, bytes], PortableBundle]:
    """Read once through the canonical archive reader and parser."""

    try:
        with zipfile.ZipFile(Path(source)) as archive:
            entries = _read_archive(archive)
    except (OSError, zipfile.BadZipFile) as error:
        raise WorkflowValidationError("portable bundle cannot be opened") from error
    return entries, _parse_entries(entries)


def validate_bundle_target(bundle: PortableBundle, target: BundleTarget) -> None:
    """Check a portable bundle against an explicit target capability binding.

    Extraction never installs packages or creates credentials.  A V6 host or a
    V7 importer calls this function before it allows the imported definition to
    start, which makes missing tools, permissions, and credential bindings a
    safe import-time failure rather than a runtime surprise.
    """

    trusted = {
        (item.tool_id, item.version, item.package_digest)
        for item in target.trusted_tools
    }
    for manifest in bundle.tools:
        reference = _manifest_reference(manifest)
        identity = (reference.tool_id, reference.version, reference.package_digest)
        if identity not in trusted:
            raise WorkflowValidationError(
                "target does not trust a required exact tool package"
            )
        permissions = manifest.get("permissions", [])
        if not isinstance(permissions, list) or any(
            not isinstance(item, str) or _PORTABLE_NAME.fullmatch(item) is None
            for item in permissions
        ):
            raise WorkflowValidationError(
                "tool permissions must be a list of portable identifiers"
            )
        if not set(permissions) <= target.allowed_permissions:
            raise WorkflowValidationError(
                "target does not grant a required tool permission"
            )
        credential_ref = manifest.get("credential_ref")
        if credential_ref is not None:
            if (
                not isinstance(credential_ref, str)
                or _PORTABLE_NAME.fullmatch(credential_ref) is None
            ):
                raise WorkflowValidationError(
                    "tool credential_ref must be a portable identifier"
                )
            if credential_ref not in target.credential_bindings:
                raise WorkflowValidationError(
                    "target has not bound a required credential reference"
                )


def _normalize_tools(tools: Sequence[Mapping[str, Any]]) -> list[dict[str, JsonValue]]:
    normalized: list[dict[str, JsonValue]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in tools:
        raw = _json_object(item, "tool manifest")
        if contains_secret_name(raw):
            raise WorkflowValidationError(
                "portable tool manifests must not contain credentials"
            )
        reference = _manifest_reference(raw)
        if "capabilities" in raw:
            ToolRecoveryCapabilities.from_mapping(raw["capabilities"])
        key = (reference.tool_id, reference.version, reference.package_digest)
        if key in seen:
            raise WorkflowValidationError(
                "portable tool manifest has a duplicate exact reference"
            )
        seen.add(key)
        normalized.append(raw)
    _validate_v2_tool_metadata(normalized)
    return normalized


_PORTABLE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _manifest_reference(manifest: Mapping[str, JsonValue]) -> ToolReference:
    required = {"tool_id", "version", "package_digest"}
    if not required <= set(manifest):
        raise WorkflowValidationError(
            "tool manifest requires tool_id, version, and package_digest"
        )
    return ToolReference.from_mapping({key: manifest[key] for key in required})


def _require_declared_tools(
    workflow: WorkflowSpec, manifests: Sequence[Mapping[str, JsonValue]]
) -> None:
    declared = {
        (
            reference.tool_id,
            reference.version,
            reference.package_digest,
        )
        for reference in (_manifest_reference(item) for item in manifests)
    }
    manifest_by_identity = {
        (
            reference.tool_id,
            reference.version,
            reference.package_digest,
        ): item
        for item in manifests
        for reference in (_manifest_reference(item),)
    }
    required: set[tuple[str, str, str]] = set()

    def visit(steps) -> None:  # type: ignore[no-untyped-def]
        for step in steps:
            if step.kind is StepKind.TOOL:
                reference = ToolReference.from_mapping(step.config["tool"])
                required.add(
                    (reference.tool_id, reference.version, reference.package_digest)
                )
                capabilities = step.config.get("capabilities")
                if (
                    capabilities is not None
                    and manifest_by_identity.get(
                        (
                            reference.tool_id,
                            reference.version,
                            reference.package_digest,
                        ),
                        {},
                    ).get("capabilities")
                    != capabilities
                ):
                    raise WorkflowValidationError(
                        "workflow tool capabilities differ from the trusted manifest"
                    )
            elif step.kind is StepKind.AGENT:
                for raw in step.config.get("tools", []):
                    if isinstance(raw, Mapping) and "tool" in raw:
                        reference = ToolReference.from_mapping(raw["tool"])
                    else:
                        reference = ToolReference.from_mapping(raw)
                    required.add(
                        (reference.tool_id, reference.version, reference.package_digest)
                    )
                    capabilities = (
                        raw.get("capabilities") if isinstance(raw, Mapping) else None
                    )
                    if (
                        capabilities is not None
                        and manifest_by_identity.get(
                            (
                                reference.tool_id,
                                reference.version,
                                reference.package_digest,
                            ),
                            {},
                        ).get("capabilities")
                        != capabilities
                    ):
                        raise WorkflowValidationError(
                            "Agent tool capabilities differ from the trusted manifest"
                        )
            for branch in step.children:
                visit(branch)

    visit(workflow.steps)
    if required - declared:
        raise WorkflowValidationError(
            "portable bundle must declare every workflow and Agent tool reference"
        )


def _json_object(value: Mapping[str, Any], label: str) -> dict[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise WorkflowValidationError(f"{label} must be an object")
    copied = json_copy(dict(value))
    if not isinstance(copied, dict):
        raise WorkflowValidationError(f"{label} must be an object")
    return copied


def _collect_files(
    files_root: str | Path | None, files: Iterable[str | Path]
) -> dict[str, Path]:
    requested = tuple(files)
    if not requested:
        return {}
    if files_root is None:
        raise WorkflowValidationError("files_root is required when exporting files")
    unresolved_root = Path(files_root)
    if unresolved_root.is_symlink():
        raise WorkflowValidationError("files_root must not be a symlink")
    root = unresolved_root.resolve()
    if not root.is_dir():
        raise WorkflowValidationError("files_root must be a directory")
    collected: dict[str, Path] = {}
    for item in requested:
        candidate = Path(item)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise WorkflowValidationError(
                "portable file paths must be relative to files_root"
            )
        current = root
        for part in candidate.parts:
            current /= part
            if current.is_symlink():
                raise WorkflowValidationError(
                    "portable files must be regular non-symlink files"
                )
        source = (root / candidate).resolve()
        try:
            relative = source.relative_to(root).as_posix()
        except ValueError as error:
            raise WorkflowValidationError("portable file escapes files_root") from error
        if not source.is_file():
            raise WorkflowValidationError(
                "portable files must be regular non-symlink files"
            )
        if relative in collected:
            raise WorkflowValidationError("portable file was listed more than once")
        collected[relative] = source
    return dict(sorted(collected.items()))


def _file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return "sha256:" + hasher.hexdigest()


def _write_archive(path: Path, entries: Mapping[str, bytes]) -> None:
    """Write, formally re-read, then atomically publish one archive.

    The destination is never opened for writing.  A failed compression, formal
    validation, or final rename therefore leaves a pre-existing bundle intact
    and makes a retry safe.  The staging file is deliberately a sibling of the
    destination so ``os.replace`` does not cross a filesystem boundary.
    """

    _validate_export_entries(entries)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    temporary: Path | None = None
    try:
        descriptor, raw_temporary = tempfile.mkstemp(
            prefix=f".{path.name}.v6-export-", suffix=".tmp", dir=path.parent
        )
        temporary = Path(raw_temporary)
        os.close(descriptor)
        descriptor = None
        _write_archive_contents(temporary, entries)
        # Use the public reader rather than a separate export-only verifier so
        # a successfully published archive is necessarily readable by this
        # package version.
        read_bundle(temporary)
        os.replace(temporary, path)
        temporary = None
    except OSError as error:
        raise WorkflowValidationError("portable bundle could not be written") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                # A cleanup problem must not turn a safely preserved old
                # bundle into a misleading successful export.
                pass


def _write_archive_contents(path: Path, entries: Mapping[str, bytes]) -> None:
    """Write deterministic bytes to an owned temporary path only."""

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, entries[name])


def _validate_export_entries(entries: Mapping[str, bytes]) -> None:
    """Apply reader-equivalent envelope and layout checks before writing."""

    _validate_member_names(tuple(entries))
    if any(not isinstance(content, bytes) for content in entries.values()):
        raise WorkflowValidationError("portable bundle entries must be bytes")
    if sum(len(content) for content in entries.values()) > _MAX_ARCHIVE_BYTES:
        raise WorkflowValidationError("portable bundle exceeds 64 MiB")
    # The canonical parser owns manifest/layout validation.  Calling it here
    # prevents a producer from emitting a structurally invalid in-memory set
    # even before the independent ZIP reread below.
    _parse_entries(entries)


def _read_archive(archive: zipfile.ZipFile) -> dict[str, bytes]:
    infos = archive.infolist()
    _validate_archive_infos(infos)
    manifest_infos = [
        info for info in infos if info.filename == _ENTRY_MANIFEST
    ]
    if len(manifest_infos) == 1:
        manifest_info = manifest_infos[0]
        if manifest_info.file_size > 1024 * 1024:
            raise WorkflowValidationError("portable manifest exceeds 1 MiB")
        try:
            json.loads(archive.read(manifest_info).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowValidationError("portable manifest is not JSON") from error
    entries: dict[str, bytes] = {}
    for info in infos:
        entries[info.filename] = archive.read(info)
    return entries


def _validate_archive_infos(infos: Sequence[zipfile.ZipInfo]) -> None:
    """Reject unsafe archive metadata before reading any payload bytes."""

    _validate_member_names(tuple(info.filename for info in infos))
    if sum(info.file_size for info in infos) > _MAX_ARCHIVE_BYTES:
        raise WorkflowValidationError("portable bundle exceeds 64 MiB")
    for info in infos:
        if info.is_dir() or info.filename.endswith("/"):
            raise WorkflowValidationError(
                "portable bundle must not contain directory entries"
            )
        # Unix symlink bit.  Archive extraction must never follow it.
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise WorkflowValidationError("portable bundle must not contain symlinks")


def _validate_member_names(names: Sequence[str]) -> None:
    """Validate the bounded flat-file ZIP namespace shared by writer/reader."""

    if len(names) > _MAX_ARCHIVE_MEMBERS:
        raise WorkflowValidationError("portable bundle has too many archive members")
    if len(names) != len(set(names)):
        raise WorkflowValidationError("portable bundle has duplicate archive members")
    canonical: set[str] = set()
    for name in names:
        value = _safe_member(name)
        if name.endswith("/"):
            raise WorkflowValidationError("portable bundle must not contain directory entries")
        normalized = value.as_posix()
        if normalized in canonical:
            raise WorkflowValidationError("portable bundle has duplicate archive members")
        canonical.add(normalized)
    # Check every proper path prefix rather than relying on adjacent lexical
    # names: an unrelated ``a-...`` member sorts between ``a`` and ``a/...``.
    for name in canonical:
        parts = name.split("/")
        for index in range(1, len(parts)):
            if "/".join(parts[:index]) in canonical:
                raise WorkflowValidationError("portable bundle archive members overlap")


def _prepare_import_destination(destination: Path) -> None:
    if destination.is_symlink():
        raise WorkflowValidationError("portable import target must not be a symlink")
    if destination.exists() and (
        not destination.is_dir() or any(destination.iterdir())
    ):
        raise WorkflowValidationError(
            "portable import target must be a new or empty directory"
        )
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise WorkflowValidationError("portable import target parent cannot be created") from error


def _new_import_staging_directory(destination: Path, purpose: str) -> Path:
    """Create one owned sibling directory suitable for atomic publication."""

    try:
        return Path(
            tempfile.mkdtemp(
                prefix=f".{destination.name}.v6-{purpose}-", dir=destination.parent
            )
        )
    except OSError as error:
        raise WorkflowValidationError("portable import staging directory cannot be created") from error


def _discard_import_staging_directory(staging: Path) -> None:
    """Best-effort cleanup limited to a directory created by this operation."""

    try:
        shutil.rmtree(staging)
    except FileNotFoundError:
        pass
    except OSError:
        # The user can safely remove only a hidden, operation-owned sibling;
        # a cleanup failure never authorizes touching the requested target.
        pass


def _extract_entries(entries: Mapping[str, bytes], destination: Path) -> None:
    """Materialize already-validated entries beneath an owned directory."""

    for name, content in entries.items():
        relative = _safe_member(name)
        target_path = destination.joinpath(*relative.parts)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(content)


def _publish_import_directory(staging: Path, destination: Path) -> None:
    """Publish a complete sibling directory without modifying an old target."""

    try:
        os.replace(staging, destination)
    except OSError as error:
        raise WorkflowValidationError("portable import could not be published") from error


def _safe_member(name: str) -> PurePosixPath:
    if not isinstance(name, str):
        raise WorkflowValidationError("portable bundle has an unsafe path")
    value = PurePosixPath(name)
    if (
        not name
        or value == PurePosixPath(".")
        or name.startswith("/")
        or "\\" in name
        or value.is_absolute()
        or ".." in value.parts
    ):
        raise WorkflowValidationError("portable bundle has an unsafe path")
    return value


def _parse_entries(entries: Mapping[str, bytes]) -> PortableBundle:
    if _ENTRY_MANIFEST not in entries:
        raise WorkflowValidationError("portable bundle is missing required metadata")
    try:
        manifest = _decode_object(entries[_ENTRY_MANIFEST], "manifest")
    except UnicodeDecodeError as error:
        raise WorkflowValidationError("portable metadata must be UTF-8 JSON") from error
    schema = manifest.get("schema_version")
    if schema == BUNDLE_V2:
        return _parse_v2_entries(entries, manifest)
    return _parse_v1_entries(entries, manifest)


def _parse_v1_entries(
    entries: Mapping[str, bytes], manifest: Mapping[str, JsonValue]
) -> PortableBundle:
    required = {_ENTRY_MANIFEST, _ENTRY_WORKFLOW, _ENTRY_TOOLS, _ENTRY_DEPENDENCIES}
    if not required <= set(entries):
        raise WorkflowValidationError("portable bundle is missing required metadata")
    unexpected = [
        name
        for name in entries
        if name not in required and not name.startswith(_FILES_PREFIX)
    ]
    if unexpected:
        raise WorkflowValidationError("portable bundle has an unknown archive member")
    try:
        workflow = WorkflowSpec.from_mapping(
            _decode_object(entries[_ENTRY_WORKFLOW], "workflow")
        )
        raw_tools = _decode_json(entries[_ENTRY_TOOLS], "tools")
        raw_dependencies = _decode_object(entries[_ENTRY_DEPENDENCIES], "dependencies")
    except UnicodeDecodeError as error:
        raise WorkflowValidationError("portable metadata must be UTF-8 JSON") from error
    if not isinstance(raw_tools, list) or any(
        not isinstance(item, Mapping) for item in raw_tools
    ):
        raise WorkflowValidationError("portable tools must be a list of objects")
    normalized_tools = _normalize_tools(tuple(raw_tools))
    if contains_secret_name(raw_dependencies):
        raise WorkflowValidationError(
            "portable dependencies must not contain credentials"
        )
    expected_keys = {
        "schema_version",
        "workflow_digest",
        "tools_digest",
        "dependencies_digest",
        "files",
    }
    if set(manifest) != expected_keys or manifest.get("schema_version") != _SCHEMA:
        raise WorkflowValidationError("portable manifest schema is unsupported")
    if manifest["workflow_digest"] != workflow.definition_digest:
        raise WorkflowValidationError("portable workflow digest does not match")
    if manifest["tools_digest"] != digest(normalized_tools):
        raise WorkflowValidationError("portable tool digest does not match")
    if manifest["dependencies_digest"] != digest(raw_dependencies):
        raise WorkflowValidationError("portable dependency digest does not match")
    file_manifest = manifest["files"]
    if not isinstance(file_manifest, Mapping):
        raise WorkflowValidationError("portable files manifest must be an object")
    expected_file_entries = {_FILES_PREFIX + str(name) for name in file_manifest}
    actual_file_entries = {name for name in entries if name.startswith(_FILES_PREFIX)}
    if actual_file_entries != expected_file_entries:
        raise WorkflowValidationError(
            "portable file manifest does not match archive files"
        )
    files: dict[str, str] = {}
    for relative, details in file_manifest.items():
        if not isinstance(relative, str) or not isinstance(details, Mapping):
            raise WorkflowValidationError("portable file manifest is invalid")
        _safe_member(_FILES_PREFIX + relative)
        sha256 = details.get("sha256")
        size = details.get("bytes")
        content = entries[_FILES_PREFIX + relative]
        actual = "sha256:" + hashlib.sha256(content).hexdigest()
        if (
            sha256 != actual
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size != len(content)
        ):
            raise WorkflowValidationError("portable file digest does not match")
        files[relative] = actual
    return PortableBundle(
        workflow=workflow,
        tools=tuple(normalized_tools),
        dependencies=raw_dependencies,
        files=files,
        manifest=manifest,
    )


def _parse_v2_entries(
    entries: Mapping[str, bytes], manifest: Mapping[str, JsonValue]
) -> PortableBundle:
    required = {
        _ENTRY_MANIFEST,
        _ENTRY_WORKFLOW,
        _ENTRY_TOOLS,
        _ENTRY_PORTABILITY,
        _ENTRY_LOCK,
    }
    if not required <= set(entries):
        raise WorkflowValidationError("portable bundle v2 is missing required metadata")
    unexpected = [
        name
        for name in entries
        if name not in required and not name.startswith(_FILES_PREFIX)
    ]
    if unexpected:
        raise WorkflowValidationError("portable bundle has an unknown archive member")
    if sum(len(content) for content in entries.values()) > _MAX_V2_ARCHIVE_BYTES:
        raise WorkflowValidationError("portable bundle v2 exceeds 64 MiB")
    if set(manifest) != {"schema_version", "source", "entries"}:
        raise WorkflowValidationError("portable manifest schema is unsupported")
    entry_manifest = manifest.get("entries")
    if not isinstance(entry_manifest, Mapping):
        raise WorkflowValidationError("portable entry manifest must be an object")
    payload_names = set(entries) - {_ENTRY_MANIFEST}
    if set(entry_manifest) != payload_names:
        raise WorkflowValidationError("portable entry manifest does not match archive")
    for name, details in entry_manifest.items():
        if not isinstance(name, str) or not isinstance(details, Mapping):
            raise WorkflowValidationError("portable entry manifest is invalid")
        if set(details) != {"sha256", "bytes"}:
            raise WorkflowValidationError("portable entry manifest is invalid")
        content = entries[name]
        expected_hash = "sha256:" + hashlib.sha256(content).hexdigest()
        size = details.get("bytes")
        if (
            details.get("sha256") != expected_hash
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size != len(content)
        ):
            raise WorkflowValidationError("portable entry digest does not match")
    try:
        workflow = WorkflowSpec.from_mapping(
            _decode_object(entries[_ENTRY_WORKFLOW], "workflow")
        )
        raw_tools = _decode_json(entries[_ENTRY_TOOLS], "tools")
        portability = _decode_object(entries[_ENTRY_PORTABILITY], "portability")
        lock_text = entries[_ENTRY_LOCK].decode("utf-8")
    except UnicodeDecodeError as error:
        raise WorkflowValidationError("portable metadata must be UTF-8") from error
    if not isinstance(raw_tools, list) or any(
        not isinstance(item, Mapping) for item in raw_tools
    ):
        raise WorkflowValidationError("portable tools must be a list of objects")
    normalized_tools = _normalize_tools(tuple(raw_tools))
    _validate_v2_tool_metadata(normalized_tools)
    _require_declared_tools(workflow, normalized_tools)
    # Reuse lock validation without accepting a filesystem interpretation.
    validated_lock = _read_dependency_lock(
        lock_text + ("\n" if "\n" not in lock_text else "")
    )
    _validate_portability_contract(workflow, portability)
    source = manifest.get("source")
    expected_source = {
        "workflow_id": workflow.workflow_id,
        "version": workflow.version,
        "workflow_schema_version": workflow.schema_version,
        "definition_digest": workflow.definition_digest,
    }
    if source != expected_source:
        raise WorkflowValidationError("portable source workflow does not match")
    files = {
        name[len(_FILES_PREFIX) :]: str(entry_manifest[name]["sha256"])
        for name in sorted(payload_names)
        if name.startswith(_FILES_PREFIX)
    }
    return PortableBundle(
        workflow=workflow,
        tools=tuple(normalized_tools),
        dependencies={},
        files=files,
        manifest=manifest,
        portability=portability,
        dependency_lock=validated_lock,
    )


def _validate_portability_contract(
    workflow: WorkflowSpec, contract: Mapping[str, JsonValue]
) -> None:
    expected = {
        "schema_version",
        "source_workflow",
        "agents",
        "subworkflows",
        "runtime_state",
    }
    if (
        set(contract) != expected
        or contract.get("schema_version") != PORTABILITY_CONTRACT_V1
    ):
        raise WorkflowValidationError("portable portability contract is unsupported")
    if contract.get("runtime_state") != "excluded":
        raise WorkflowValidationError("portable bundles must exclude runtime state")
    expected_source = {
        "workflow_id": workflow.workflow_id,
        "version": workflow.version,
        "workflow_schema_version": workflow.schema_version,
        "definition_digest": workflow.definition_digest,
    }
    if contract.get("source_workflow") != expected_source:
        raise WorkflowValidationError("portability source workflow does not match")
    agents = contract.get("agents")
    subworkflows = contract.get("subworkflows")
    if not isinstance(agents, list) or not isinstance(subworkflows, list):
        raise WorkflowValidationError("portability requirements must be lists")
    workflow_subflows = {
        (str(step.config["workflow_id"]), str(step.config["version"]))
        for step in _walk_steps(workflow.steps)
        if step.kind is StepKind.SUBWORKFLOW
    }
    declared_subflows: set[tuple[str, str]] = set()
    for item in subworkflows:
        if not isinstance(item, Mapping) or set(item) - {
            "workflow_id",
            "version",
            "definition_digest",
            "workflow_schema_version",
            "input_schema",
            "output_schema",
        }:
            raise WorkflowValidationError(
                "portability subworkflow requirement is invalid"
            )
        required = {
            "workflow_id",
            "version",
            "definition_digest",
            "workflow_schema_version",
        }
        if not required <= set(item):
            raise WorkflowValidationError(
                "portability subworkflow requirement is incomplete"
            )
        identity = (str(item["workflow_id"]), str(item["version"]))
        if identity in declared_subflows:
            raise WorkflowValidationError("portability subworkflow is duplicated")
        declared_subflows.add(identity)
    # The contract also carries transitive requirements discovered from the
    # project-resolved child definitions, which are intentionally not embedded
    # in the bundle.  Only direct requirements can be reconstructed here.
    if not workflow_subflows <= declared_subflows:
        raise WorkflowValidationError(
            "portability subworkflow requirements are incomplete"
        )
    root_agents = {
        _agent_address_key(workflow.workflow_id, workflow.version, step.step_id): step
        for step in _walk_steps(workflow.steps)
        if step.kind is StepKind.AGENT
    }
    base_agent_fields = {
        "step_id",
        "source_driver",
        "instructions",
        "output_schema",
        "tools",
        "budget",
        "required_capabilities",
    }
    addressed_agent_fields = base_agent_fields | {"workflow_id", "workflow_version"}
    seen_agents: set[str] = set()
    for item in agents:
        if not isinstance(item, Mapping):
            raise WorkflowValidationError("portability Agent contract is invalid")
        if set(item) == base_agent_fields:
            # Backward-compatible v2 root-only entry.
            workflow_id, workflow_version = workflow.workflow_id, workflow.version
        elif set(item) == addressed_agent_fields:
            workflow_id = item.get("workflow_id")
            workflow_version = item.get("workflow_version")
            if not isinstance(workflow_id, str) or not isinstance(
                workflow_version, str
            ):
                raise WorkflowValidationError("portability Agent address is invalid")
        else:
            raise WorkflowValidationError("portability Agent contract is invalid")
        step_id = item.get("step_id")
        if not isinstance(step_id, str):
            raise WorkflowValidationError("portability Agent address is invalid")
        address = _agent_address_key(workflow_id, workflow_version, step_id)
        if address in seen_agents:
            raise WorkflowValidationError("portability Agent definition is duplicated")
        seen_agents.add(address)
        instructions = item.get("instructions")
        if (
            not isinstance(instructions, str)
            or not instructions.strip()
            or len(instructions.encode("utf-8")) > 64 * 1024
            or _CREDENTIAL_ASSIGNMENT.search(instructions) is not None
        ):
            raise WorkflowValidationError("portability Agent instructions are invalid")
        if item.get("required_capabilities") != [
            "host_managed_execution",
            "native_tools",
            "structured_output",
        ]:
            raise WorkflowValidationError("portability Agent contract is invalid")
        step = root_agents.get(address)
        if step is None:
            if (workflow_id, workflow_version) not in declared_subflows:
                raise WorkflowValidationError("portability Agent definition is unknown")
            # The child definition is intentionally target-bound rather than
            # embedded.  Its exact contract is checked during project assembly.
            continue
        if workflow.schema_version == "v6-light-workflow-v2":
            expected_output: Mapping[str, JsonValue] = {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"message": {"type": "string", "maxLength": 64000}},
                "required": ["message"],
                "additionalProperties": False,
            }
        else:
            if workflow.schema_version == "v6-light-workflow-v1":
                raise WorkflowValidationError(
                    "bundle v2 does not support v1 Agent steps"
                )
            assert step.contract is not None
            expected_output = step.contract.output_schema
        if (
            item.get("source_driver") != step.config.get("driver")
            or item.get("tools") != step.config.get("tools", [])
            or item.get("output_schema") != expected_output
            or item.get("budget") != _portable_agent_budget(step.config)
        ):
            raise WorkflowValidationError("portability Agent contract was altered")
    if set(root_agents) - seen_agents:
        raise WorkflowValidationError("portability Agent steps do not match workflow")


def _portable_agent_budget(config: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {"max_turns": int(config.get("max_turns", 1))}
    if "budget" in config:
        result["limits"] = json_copy(config["budget"])
    return result


def _validate_v2_tool_metadata(
    manifests: Sequence[Mapping[str, JsonValue]],
) -> None:
    for manifest in manifests:
        permissions = manifest.get("permissions", [])
        if not isinstance(permissions, list) or any(
            not isinstance(item, str) or _PORTABLE_NAME.fullmatch(item) is None
            for item in permissions
        ):
            raise WorkflowValidationError(
                "tool permissions must be a list of portable identifiers"
            )
        credential_ref = manifest.get("credential_ref")
        if credential_ref is not None and (
            not isinstance(credential_ref, str)
            or _PORTABLE_NAME.fullmatch(credential_ref) is None
        ):
            raise WorkflowValidationError(
                "tool credential_ref must be a portable identifier"
            )
        capabilities = manifest.get("capabilities")
        if capabilities is not None:
            ToolRecoveryCapabilities.from_mapping(capabilities)


def _decode_json(content: bytes, label: str) -> object:
    try:
        return json.loads(content.decode("utf-8"))
    except json.JSONDecodeError as error:
        raise WorkflowValidationError(f"portable {label} is not JSON") from error


def _decode_object(content: bytes, label: str) -> dict[str, JsonValue]:
    value = _decode_json(content, label)
    if not isinstance(value, Mapping):
        raise WorkflowValidationError(f"portable {label} must be an object")
    return _json_object(value, f"portable {label}")
