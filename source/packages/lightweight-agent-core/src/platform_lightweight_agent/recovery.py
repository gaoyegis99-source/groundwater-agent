"""Verified local recovery packages for a journal and its file artifacts.

Portable bundles move definitions.  This module instead moves one local
runtime's durable state: a SQLite online backup plus immutable inputs and
published artifacts.  Staging outputs are intentionally excluded.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .files import FileReference, FileWorkspace
from .environment import (
    RuntimeEnvironmentIdentity,
    RuntimeEnvironmentReport,
    capture_runtime_environment,
    compare_runtime_environment,
)
from .journal import RunSnapshot, SQLiteRunJournal
from .spec import JsonValue, WorkflowValidationError

LOCAL_RECOVERY_SCHEMA_V1 = "v6-local-recovery-v1"
_MANIFEST = "manifest.json"
_JOURNAL = "journal.sqlite"
_MAX_ENTRY_BYTES = 256 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
_MAX_ENTRIES = 100_000


class LocalRecoveryError(WorkflowValidationError):
    """A local recovery package or referenced artifact is invalid."""


@dataclass(frozen=True)
class LocalRecoveryPlan:
    """Capacity and scope preview produced without publishing an archive."""

    file_count: int
    total_size_bytes: int
    estimated_space_bytes: int
    referenced_files: tuple[FileReference, ...]

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "schema_version": "v6-local-recovery-plan-v1",
            "scope": "full_workspace",
            "file_count": self.file_count,
            "total_size_bytes": self.total_size_bytes,
            "estimated_space_bytes": self.estimated_space_bytes,
            "referenced_files": [item.to_mapping() for item in self.referenced_files],
            "limits": {
                "max_entries": _MAX_ENTRIES,
                "max_entry_bytes": _MAX_ENTRY_BYTES,
                "max_total_bytes": _MAX_ARCHIVE_BYTES,
            },
            "effects": "none",
        }


@dataclass(frozen=True)
class LocalRecoveryRetentionPreview:
    """Read-only artifact cleanup evidence; this object never deletes files."""

    protected: tuple[FileReference, ...]
    unreferenced_paths: tuple[str, ...]

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "schema_version": "v6-local-retention-preview-v1",
            "protected": [item.to_mapping() for item in self.protected],
            "unreferenced_paths": list(self.unreferenced_paths),
            "effects": "none",
        }


def create_local_recovery_package(
    journal: SQLiteRunJournal,
    workspace: FileWorkspace,
    destination: str | Path,
    *,
    overwrite: bool = False,
    dependency_lock: str | Path | None = None,
) -> Path:
    """Create a checked archive containing the journal and durable files."""

    if not isinstance(journal, SQLiteRunJournal) or not isinstance(
        workspace, FileWorkspace
    ):
        raise LocalRecoveryError("local recovery requires SQLiteRunJournal and FileWorkspace")
    target = Path(destination).resolve()
    if target.exists() and not overwrite:
        raise LocalRecoveryError("local recovery destination already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".v6-staging")
    if temporary.exists() or temporary.is_symlink():
        raise LocalRecoveryError("local recovery staging path is occupied")
    with tempfile.TemporaryDirectory(prefix="v6-recovery-") as raw:
        root = Path(raw)
        backup = journal.backup_to(root / _JOURNAL)
        backup_journal = SQLiteRunJournal(backup)
        references = _journal_references(backup_journal)
        _verify_references(workspace, references)
        files = _recovery_files(backup, workspace)
        entries = [
            {
                "path": name,
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for name, path in files
        ]
        manifest = {
            "schema_version": LOCAL_RECOVERY_SCHEMA_V1,
            "scope": {"kind": "full_workspace"},
            "environment": capture_runtime_environment(
                backup_journal, dependency_lock=dependency_lock
            ).to_mapping(),
            "entries": entries,
            "referenced_files": [item.to_mapping() for item in references],
            "excludes": ["workspace/outputs"],
        }
        manifest_bytes = json.dumps(
            manifest, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        _validate_capacity(
            [len(manifest_bytes), *(path.stat().st_size for _name, path in files)]
        )
        try:
            with zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
            ) as archive:
                archive.writestr(
                    _MANIFEST,
                    manifest_bytes,
                )
                for name, path in files:
                    archive.write(path, name)
            validate_local_recovery_package(temporary)
            os.replace(temporary, target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    return target


def plan_local_recovery_package(
    journal: SQLiteRunJournal,
    workspace: FileWorkspace,
    *,
    dependency_lock: str | Path | None = None,
) -> LocalRecoveryPlan:
    """Preview the exact full-workspace package scope and capacity."""

    if not isinstance(journal, SQLiteRunJournal) or not isinstance(
        workspace, FileWorkspace
    ):
        raise LocalRecoveryError("local recovery requires SQLiteRunJournal and FileWorkspace")
    with tempfile.TemporaryDirectory(prefix="v6-recovery-plan-") as raw:
        backup = journal.backup_to(Path(raw) / _JOURNAL)
        references = _journal_references(SQLiteRunJournal(backup))
        _verify_references(workspace, references)
        files = _recovery_files(backup, workspace)
        sizes = [path.stat().st_size for _name, path in files]
        # Include a conservative manifest/ZIP overhead estimate in the space
        # preview while enforcing the same uncompressed payload rules as read.
        estimated = sum(sizes) + max(64 * 1024, len(files) * 512)
        _validate_capacity([*sizes, estimated - sum(sizes)])
        # Validate the optional lock now so plan and create fail identically.
        capture_runtime_environment(
            SQLiteRunJournal(backup), dependency_lock=dependency_lock
        )
        return LocalRecoveryPlan(len(files), sum(sizes), estimated, references)


def validate_local_recovery_package(source: str | Path) -> LocalRecoveryPlan:
    """Apply the restore reader's structural, capacity and digest checks."""

    path = Path(source)
    manifest, entries = _validated_archive(path)
    references = _manifest_references(manifest)
    total = 0
    try:
        with zipfile.ZipFile(path) as archive:
            for name, (size, expected_digest) in entries.items():
                value = hashlib.sha256()
                observed = 0
                with archive.open(name) as stream:
                    while block := stream.read(1024 * 1024):
                        observed += len(block)
                        value.update(block)
                if observed != size or "sha256:" + value.hexdigest() != expected_digest:
                    raise LocalRecoveryError("local recovery entry digest mismatch")
                total += observed
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        raise LocalRecoveryError("local recovery archive is invalid") from error
    return LocalRecoveryPlan(
        len(entries),
        total,
        total + max(64 * 1024, len(entries) * 512),
        references,
    )


def check_local_recovery_environment(
    source: str | Path, *, dependency_lock: str | Path | None = None
) -> RuntimeEnvironmentReport:
    """Compare a package's narrow runtime identity without restoring it."""

    manifest, _entries = _validated_archive(Path(source))
    expected = RuntimeEnvironmentIdentity.from_mapping(manifest.get("environment"))
    return compare_runtime_environment(expected, dependency_lock=dependency_lock)


def restore_local_recovery_package(
    source: str | Path,
    destination: str | Path,
    *,
    overwrite: bool = False,
    dependency_lock: str | Path | None = None,
) -> tuple[SQLiteRunJournal, FileWorkspace]:
    """Restore only after archive, journal, and every referenced file verify."""

    archive_path = Path(source)
    if archive_path.is_symlink() or not archive_path.is_file():
        raise LocalRecoveryError("local recovery source must be a regular file")
    requested_target = Path(destination)
    _reject_symlink_target(requested_target)
    target = requested_target.resolve()
    if target.exists():
        reason = (
            "LOCAL_RECOVERY_OVERWRITE_UNSAFE"
            if overwrite
            else "local recovery destination already exists"
        )
        raise LocalRecoveryError(reason)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    try:
        manifest, entries = _validated_archive(archive_path)
        environment_report = compare_runtime_environment(
            RuntimeEnvironmentIdentity.from_mapping(manifest.get("environment")),
            dependency_lock=dependency_lock,
        )
        if environment_report.status == "incompatible":
            raise LocalRecoveryError("LOCAL_RECOVERY_ENVIRONMENT_INCOMPATIBLE")
        with zipfile.ZipFile(archive_path) as archive:
            for name, expected in entries.items():
                output = staging / PurePosixPath(name)
                output.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as source_file, output.open("xb") as target_file:
                    shutil.copyfileobj(source_file, target_file)
                if output.stat().st_size != expected[0] or _sha256(output) != expected[1]:
                    raise LocalRecoveryError("local recovery entry digest mismatch")
        source_journal = staging / _JOURNAL
        restored_path = staging / "runtime" / "runs.sqlite"
        restored_path.parent.mkdir(parents=True)
        restored = SQLiteRunJournal.restore_from_backup(source_journal, restored_path)
        workspace_root = staging / "runtime" / "files"
        workspace = FileWorkspace.under(workspace_root)
        _move_tree(staging / "workspace" / "inputs", workspace.input_root)
        _move_tree(staging / "workspace" / "artifacts", workspace.artifact_root)
        expected_refs = _manifest_references(manifest)
        actual_refs = _journal_references(restored)
        if actual_refs != expected_refs:
            raise LocalRecoveryError("local recovery journal references changed")
        _verify_references(workspace, actual_refs)
        os.replace(staging / "runtime", target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    shutil.rmtree(staging, ignore_errors=True)
    return SQLiteRunJournal(target / "runs.sqlite"), FileWorkspace.under(
        target / "files", create=False
    )


def preview_local_artifact_retention(
    journal: SQLiteRunJournal,
    workspace: FileWorkspace,
    *,
    backups: Sequence[str | Path] = (),
) -> LocalRecoveryRetentionPreview:
    """List protected identities and unreferenced artifact payloads.

    Recovery packages supplied through ``backups`` contribute protected file
    identities without being extracted or changing the live journal.
    """

    protected_by_identity = {
        (item.logical_id, item.sha256, item.size_bytes): item
        for item in _journal_references(journal)
    }
    for backup in backups:
        manifest, _entries = _validated_archive(Path(backup))
        for reference in _manifest_references(manifest):
            protected_by_identity[
                (reference.logical_id, reference.sha256, reference.size_bytes)
            ] = reference
    protected = tuple(
        protected_by_identity[key] for key in sorted(protected_by_identity)
    )
    protected_paths = {
        workspace.resolve_published(item).relative_to(workspace.artifact_root).as_posix()
        for item in protected
        if _is_published(workspace, item)
    }
    candidates = {
        path.relative_to(workspace.artifact_root).as_posix()
        for path in _regular_files(workspace.artifact_root)
        if not path.relative_to(workspace.artifact_root).as_posix().startswith(".")
    }
    return LocalRecoveryRetentionPreview(
        protected, tuple(sorted(candidates - protected_paths))
    )


def _journal_references(journal: SQLiteRunJournal) -> tuple[FileReference, ...]:
    found: dict[tuple[str, str, int], FileReference] = {}
    for run_id in journal.retained_run_ids():
        snapshot = journal.get(run_id)
        for reference in snapshot_file_references(snapshot):
            key = (reference.logical_id, reference.sha256, reference.size_bytes)
            found[key] = reference
    return tuple(found[key] for key in sorted(found))


def snapshot_file_references(snapshot: RunSnapshot) -> tuple[FileReference, ...]:
    """Enumerate every durable location that can carry a runtime file identity."""

    if not isinstance(snapshot, RunSnapshot):
        raise LocalRecoveryError("file reference enumeration requires RunSnapshot")
    found: dict[tuple[str, str, int], FileReference] = {}
    for value in (
        snapshot.inputs,
        snapshot.workflow,
        snapshot.execution_manifest,
        snapshot.state,
    ):
        for reference in _walk_references(value):
            key = (reference.logical_id, reference.sha256, reference.size_bytes)
            found[key] = reference
    return tuple(found[key] for key in sorted(found))


def _walk_references(value: object) -> Iterator[FileReference]:
    if isinstance(value, Mapping):
        if set(value) == {"logical_id", "sha256", "size_bytes"}:
            try:
                yield FileReference.from_mapping(value)
            except WorkflowValidationError:
                pass
        for child in value.values():
            yield from _walk_references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_references(child)


def _verify_references(
    workspace: FileWorkspace, references: tuple[FileReference, ...]
) -> None:
    for reference in references:
        try:
            workspace.resolve_input(reference)
        except WorkflowValidationError as error:
            raise LocalRecoveryError(
                f"LOCAL_RECOVERY_ARTIFACT_MISSING:{reference.logical_id}"
            ) from error


def _workspace_files(root: Path, prefix: str) -> list[tuple[str, Path]]:
    return [
        (f"{prefix}/{path.relative_to(root).as_posix()}", path)
        for path in _regular_files(root)
    ]


def _recovery_files(
    backup: Path, workspace: FileWorkspace
) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = [(_JOURNAL, backup)]
    files.extend(_workspace_files(workspace.input_root, "workspace/inputs"))
    files.extend(_workspace_files(workspace.artifact_root, "workspace/artifacts"))
    return files


def _regular_files(root: Path) -> list[Path]:
    result: list[Path] = []
    if not root.exists():
        return result
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise LocalRecoveryError("local recovery workspace contains a symlink")
        if path.is_file():
            result.append(path)
    return result


def _validated_archive(
    source: Path,
) -> tuple[Mapping[str, object], dict[str, tuple[int, str]]]:
    try:
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
            _validate_capacity([item.file_size for item in infos])
            names = [item.filename for item in infos]
            if len(names) != len(set(names)) or _MANIFEST not in names:
                raise LocalRecoveryError("local recovery archive entries are invalid")
            for info in infos:
                _safe_name(info.filename)
                if info.is_dir():
                    raise LocalRecoveryError("local recovery archive entry is invalid")
            manifest = json.loads(archive.read(_MANIFEST))
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, KeyError) as error:
        raise LocalRecoveryError("local recovery archive is invalid") from error
    if (
        not isinstance(manifest, Mapping)
        or set(manifest) != {
            "schema_version",
            "scope",
            "environment",
            "entries",
            "referenced_files",
            "excludes",
        }
        or manifest.get("schema_version") != LOCAL_RECOVERY_SCHEMA_V1
        or manifest.get("scope") != {"kind": "full_workspace"}
        or not isinstance(manifest.get("environment"), Mapping)
        or manifest.get("excludes") != ["workspace/outputs"]
        or not isinstance(manifest.get("referenced_files"), list)
    ):
        raise LocalRecoveryError("local recovery manifest is invalid")
    raw_entries = manifest.get("entries")
    if not isinstance(raw_entries, list):
        raise LocalRecoveryError("local recovery manifest entries are invalid")
    entries: dict[str, tuple[int, str]] = {}
    for item in raw_entries:
        if not isinstance(item, Mapping) or set(item) != {"path", "size_bytes", "sha256"}:
            raise LocalRecoveryError("local recovery manifest entry is invalid")
        name, size, digest = item["path"], item["size_bytes"], item["sha256"]
        if not isinstance(name, str) or not isinstance(size, int) or not isinstance(digest, str):
            raise LocalRecoveryError("local recovery manifest entry is invalid")
        _safe_name(name)
        entries[name] = (size, digest)
    if len(entries) != len(raw_entries):
        raise LocalRecoveryError("local recovery manifest entries are duplicated")
    if set(names) != {*entries, _MANIFEST} or _JOURNAL not in entries:
        raise LocalRecoveryError("local recovery manifest does not cover the archive")
    RuntimeEnvironmentIdentity.from_mapping(manifest["environment"])
    return manifest, entries


def _manifest_references(manifest: Mapping[str, object]) -> tuple[FileReference, ...]:
    raw = manifest.get("referenced_files")
    if not isinstance(raw, list):
        raise LocalRecoveryError("local recovery references are invalid")
    return tuple(FileReference.from_mapping(item) for item in raw)


def _validate_capacity(sizes: Sequence[int]) -> None:
    if len(sizes) > _MAX_ENTRIES:
        raise LocalRecoveryError("local recovery archive has too many entries")
    if any(
        not isinstance(size, int)
        or isinstance(size, bool)
        or size < 0
        or size > _MAX_ENTRY_BYTES
        for size in sizes
    ):
        raise LocalRecoveryError("local recovery archive entry is too large")
    if sum(sizes) > _MAX_ARCHIVE_BYTES:
        raise LocalRecoveryError("local recovery archive is too large")


def _reject_symlink_target(path: Path) -> None:
    current = path.absolute()
    while True:
        if current.is_symlink():
            raise LocalRecoveryError("local recovery destination must not traverse symlinks")
        parent = current.parent
        if parent == current:
            return
        current = parent


def _safe_name(name: str) -> None:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\\" in name:
        raise LocalRecoveryError("local recovery archive path is unsafe")


def _move_tree(source: Path, target: Path) -> None:
    if not source.exists():
        return
    for path in _regular_files(source):
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(path, destination)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _is_published(workspace: FileWorkspace, reference: FileReference) -> bool:
    try:
        workspace.resolve_published(reference)
    except WorkflowValidationError:
        return False
    return True
