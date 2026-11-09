"""Explicit, content-addressed file hand-off for host-owned tools.

Workflow values remain JSON.  A tool that needs a local file receives a
``FileReference`` in those JSON values and resolves it through a host-owned
``FileWorkspace``; it never receives an ambient relative path or relies on the
process working directory.  The module deliberately uses only the standard
library so file staging does not add a base-package dependency.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .spec import JsonValue, WorkflowValidationError

_DIGEST_PREFIX = "sha256:"
_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class FileReference:
    """A JSON-safe identity for one staged file, not a filesystem path."""

    logical_id: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "logical_id", _logical_id(self.logical_id))
        if (
            not isinstance(self.sha256, str)
            or not self.sha256.startswith(_DIGEST_PREFIX)
            or len(self.sha256) != len(_DIGEST_PREFIX) + 64
            or any(character not in _HEX for character in self.sha256[7:])
        ):
            raise WorkflowValidationError("file reference sha256 is invalid")
        if (
            not isinstance(self.size_bytes, int)
            or isinstance(self.size_bytes, bool)
            or self.size_bytes < 0
        ):
            raise WorkflowValidationError("file reference size_bytes is invalid")

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "logical_id": self.logical_id,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_mapping(cls, value: object) -> FileReference:
        if not isinstance(value, Mapping) or set(value) != {
            "logical_id",
            "sha256",
            "size_bytes",
        }:
            raise WorkflowValidationError("file reference must have exact fields")
        return cls(
            logical_id=value["logical_id"],  # type: ignore[arg-type]
            sha256=value["sha256"],  # type: ignore[arg-type]
            size_bytes=value["size_bytes"],  # type: ignore[arg-type]
        )


class FileWorkspace:
    """Explicit roots for immutable inputs, staging outputs, and publications.

    The host creates this object and may give it directly to a trusted tool
    handler.  Tools resolve a supplied :class:`FileReference` only through
    :meth:`resolve_input`; they can reserve an output location and publish a
    completed file into an immutable content-addressed store.  A published
    reference is directly resolvable as the next tool's input.  No method
    changes the process working directory or follows symlinks.
    """

    def __init__(
        self,
        input_root: str | Path,
        output_root: str | Path,
        *,
        artifact_root: str | Path | None = None,
        create: bool = True,
    ) -> None:
        self._input_root = _workspace_root(Path(input_root), create=create)
        self._output_root = _workspace_root(Path(output_root), create=create)
        published = (
            Path(artifact_root)
            if artifact_root is not None
            else self._output_root.parent / f"{self._output_root.name}.artifacts"
        )
        self._artifact_root = _workspace_root(published, create=create)
        if len({self._input_root, self._output_root, self._artifact_root}) != 3:
            raise WorkflowValidationError(
                "file workspace input, output, and artifact roots must be distinct"
            )

    @property
    def input_root(self) -> Path:
        return self._input_root

    @property
    def output_root(self) -> Path:
        return self._output_root

    @property
    def artifact_root(self) -> Path:
        """Host-owned immutable publication root for this workspace."""

        return self._artifact_root

    @classmethod
    def under(cls, root: str | Path, *, create: bool = True) -> FileWorkspace:
        """Create the conventional isolated input/output/artifact layout."""

        base = _workspace_root(Path(root), create=create)
        return cls(
            base / "inputs",
            base / "outputs",
            artifact_root=base / "artifacts",
            create=create,
        )

    def stage_input(self, source: str | Path, logical_id: str) -> FileReference:
        """Copy one caller-selected regular file into the immutable input root."""

        source_path = Path(source)
        if source_path.is_symlink() or not source_path.is_file():
            raise WorkflowValidationError("staged input must be a regular non-symlink file")
        destination = _workspace_path(self._input_root, logical_id, create_parent=True)
        if destination.exists() or destination.is_symlink():
            raise WorkflowValidationError("staged input logical_id already exists")
        temporary = destination.with_name(destination.name + ".v6-staging")
        if temporary.exists() or temporary.is_symlink():
            raise WorkflowValidationError("file workspace staging path is occupied")
        try:
            shutil.copyfile(source_path, temporary, follow_symlinks=False)
            os.replace(temporary, destination)
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise WorkflowValidationError("could not stage input file") from error
        return _reference_for(destination, logical_id)

    def stage_declared_inputs(
        self,
        source_root: str | Path,
        references: Mapping[str, FileReference | Mapping[str, object]],
    ) -> dict[str, FileReference]:
        """Stage an imported bundle's declared inputs under this workspace.

        ``references`` is normally the result of
        :func:`bundle_file_references` applied to a validated bundle manifest.
        Each source file is checked against that immutable identity *before*
        it is copied, so changing directories or mutating an imported file
        cannot silently redirect a tool to another local path.
        """

        root = _workspace_root(Path(source_root), create=False)
        staged: dict[str, FileReference] = {}
        for logical_id, raw_reference in sorted(references.items()):
            normalized = (
                raw_reference
                if isinstance(raw_reference, FileReference)
                else FileReference.from_mapping(raw_reference)
            )
            if _logical_id(logical_id) != normalized.logical_id:
                raise WorkflowValidationError(
                    "declared input key does not match its file reference"
                )
            source = _workspace_path(root, normalized.logical_id, create_parent=False)
            if source.is_symlink() or not source.is_file():
                raise WorkflowValidationError("declared input file is unavailable")
            if _reference_for(source, normalized.logical_id) != normalized:
                raise WorkflowValidationError(
                    "declared input file does not match its bundle identity"
                )
            staged_reference = self.stage_input(source, normalized.logical_id)
            if staged_reference != normalized:  # pragma: no cover - copy integrity guard
                raise WorkflowValidationError("staged input does not match its identity")
            staged[normalized.logical_id] = staged_reference
        return staged

    def resolve_input(self, reference: FileReference | Mapping[str, object]) -> Path:
        """Return a verified staged input or immutable published artifact.

        This makes a returned publication reference directly usable as a
        later node's input; callers do not need a second, ad-hoc copy step.
        """

        normalized = (
            reference
            if isinstance(reference, FileReference)
            else FileReference.from_mapping(reference)
        )
        input_path = _workspace_path(
            self._input_root, normalized.logical_id, create_parent=False
        )
        artifact_path = self._published_path(normalized, create_parent=False)
        mismatch = False
        for path in (input_path, artifact_path):
            if path.is_symlink() or not path.is_file():
                continue
            if _reference_for(path, normalized.logical_id) == normalized:
                return path
            mismatch = True
        if mismatch:
            raise WorkflowValidationError(
                "referenced input file no longer matches its digest"
            )
        raise WorkflowValidationError("referenced input file is unavailable")

    def resolve_published(
        self, reference: FileReference | Mapping[str, object]
    ) -> Path:
        """Return only a verified immutable publication, never a staging file."""

        normalized = (
            reference
            if isinstance(reference, FileReference)
            else FileReference.from_mapping(reference)
        )
        path = self._published_path(normalized, create_parent=False)
        if path.is_symlink() or not path.is_file():
            raise WorkflowValidationError("published output is unavailable")
        if _reference_for(path, normalized.logical_id) != normalized:
            raise WorkflowValidationError("published output no longer matches its digest")
        return path

    def output_path(self, logical_id: str) -> Path:
        """Reserve a legacy staging path which cannot silently overwrite."""

        path = _workspace_path(self._output_root, logical_id, create_parent=True)
        if path.exists() or path.is_symlink():
            raise WorkflowValidationError("file workspace output path is already reserved")
        return path

    def action_output_path(self, action_id: str, logical_id: str) -> Path:
        """Reserve an action-isolated staging path for one tool invocation."""

        action = _action_id(action_id)
        path = _workspace_path(
            self._output_root, f"{action}/{_logical_id(logical_id)}", create_parent=True
        )
        if path.exists() or path.is_symlink():
            raise WorkflowValidationError("file workspace output path is already reserved")
        return path

    def record_output(
        self, logical_id: str, *, action_id: str | None = None
    ) -> FileReference:
        """Publish a completed output before it enters workflow JSON.

        ``record_output`` retains the original convenience API, but a returned
        reference now names an immutable publication rather than only a
        mutable staging file.  Supplying ``action_id`` additionally records a
        durable local publication index for safe action-level recovery.
        """

        return self.publish_output(logical_id, action_id=action_id)

    def publish_output(
        self, logical_id: str, *, action_id: str | None = None
    ) -> FileReference:
        """Atomically publish an output copy under its content identity.

        Publication occurs before the caller records the reference in a
        workflow result.  If a process exits in that gap, the artifact is
        either absent or immutable; :meth:`published_output` lets a recovery
        handler reuse an indexed action publication rather than overwrite it.
        This is deliberately not presented as a filesystem/journal transaction.
        """

        normalized = _logical_id(logical_id)
        action = _action_id(action_id) if action_id is not None else None
        if action is not None:
            try:
                return self.published_output(action, normalized)
            except WorkflowValidationError:
                # An unavailable index means no successful action publication
                # has been observed yet; validate the staging file below.
                pass
            source = _workspace_path(
                self._output_root,
                f"{action}/{normalized}",
                create_parent=False,
            )
        else:
            source = _workspace_path(
                self._output_root, normalized, create_parent=False
            )
        if source.is_symlink() or not source.is_file():
            raise WorkflowValidationError("recorded output must be a regular non-symlink file")
        reference = _reference_for(source, normalized)
        destination = self._published_path(reference, create_parent=True)
        _publish_copy(source, destination, reference)
        if action is not None:
            self._write_publication_index(action, reference)
        else:
            self._write_legacy_publication_index(reference)
        return reference

    def published_output(self, action_id: str, logical_id: str) -> FileReference:
        """Find and verify a prior successful publication for one action."""

        action = _action_id(action_id)
        path = self._publication_index_path(action, _logical_id(logical_id), create_parent=False)
        if path.is_symlink() or not path.is_file():
            raise WorkflowValidationError("published action output is unavailable")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise WorkflowValidationError("published action output is invalid") from error
        reference = FileReference.from_mapping(raw)
        if reference.logical_id != logical_id:
            raise WorkflowValidationError("published action output is invalid")
        self.resolve_published(reference)
        return reference

    def _published_path(self, reference: FileReference, *, create_parent: bool) -> Path:
        return _workspace_path(
            self._artifact_root,
            f"{reference.sha256[len(_DIGEST_PREFIX):]}/{reference.logical_id}",
            create_parent=create_parent,
        )

    def _publication_index_path(
        self, action_id: str, logical_id: str, *, create_parent: bool
    ) -> Path:
        return _workspace_path(
            self._artifact_root,
            f".actions/{action_id}/{logical_id}.json",
            create_parent=create_parent,
        )

    def _write_publication_index(self, action_id: str, reference: FileReference) -> None:
        destination = self._publication_index_path(
            action_id, reference.logical_id, create_parent=True
        )
        payload = json.dumps(
            reference.to_mapping(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        _publish_bytes(destination, payload)

    def _legacy_publication_index_path(
        self, logical_id: str, *, create_parent: bool
    ) -> Path:
        return _workspace_path(
            self._artifact_root,
            f".outputs/{logical_id}.json",
            create_parent=create_parent,
        )

    def _write_legacy_publication_index(self, reference: FileReference) -> None:
        destination = self._legacy_publication_index_path(
            reference.logical_id, create_parent=True
        )
        payload = json.dumps(
            reference.to_mapping(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        _publish_bytes(destination, payload)

    def staging_manifest(self) -> tuple[FileReference, ...]:
        """Return transient files for diagnostics and abandoned-work cleanup.

        These references are intentionally *not* workflow artifacts.  A file
        appears here as soon as a tool writes it, before publication validation
        and before any durable publication index exists.
        """

        result: list[FileReference] = []
        for directory, subdirectories, filenames in os.walk(
            self._output_root, topdown=True, followlinks=False
        ):
            current = Path(directory)
            retained: list[str] = []
            for name in sorted(subdirectories):
                child = current / name
                if child.is_symlink():
                    raise WorkflowValidationError(
                        "file workspace output directory must not be a symlink"
                    )
                retained.append(name)
            subdirectories[:] = retained
            for name in sorted(filenames):
                child = current / name
                if child.is_symlink() or not child.is_file():
                    raise WorkflowValidationError(
                        "file workspace output must contain regular files only"
                    )
                relative = child.relative_to(self._output_root).as_posix()
                result.append(_reference_for(child, relative))
        return tuple(result)

    def published_manifest(self) -> tuple[FileReference, ...]:
        """Return only verified outputs with a completed publication index.

        The index is the local publication fact.  Content-addressed blobs left
        behind by a crash before index commit are safe orphans and are not
        exposed as formal outputs.
        """

        references: list[FileReference] = []
        roots = (self._artifact_root / ".actions", self._artifact_root / ".outputs")
        for root in roots:
            if root.is_symlink():
                raise WorkflowValidationError(
                    "file publication index directory must not be a symlink"
                )
            if not root.exists():
                continue
            if not root.is_dir():
                raise WorkflowValidationError("file publication index is invalid")
            for directory, subdirectories, filenames in os.walk(
                root, topdown=True, followlinks=False
            ):
                current = Path(directory)
                retained: list[str] = []
                for name in sorted(subdirectories):
                    child = current / name
                    if child.is_symlink():
                        raise WorkflowValidationError(
                            "file publication index directory must not be a symlink"
                        )
                    retained.append(name)
                subdirectories[:] = retained
                for name in sorted(filenames):
                    child = current / name
                    if child.is_symlink() or not child.is_file() or not name.endswith(
                        ".json"
                    ):
                        raise WorkflowValidationError("file publication index is invalid")
                    try:
                        raw = json.loads(child.read_text(encoding="utf-8"))
                        reference = FileReference.from_mapping(raw)
                    except (OSError, json.JSONDecodeError, WorkflowValidationError) as error:
                        raise WorkflowValidationError(
                            "file publication index is invalid"
                        ) from error
                    indexed_logical_id = child.relative_to(root).as_posix()[:-5]
                    if root.name == ".actions":
                        parts = PurePosixPath(indexed_logical_id).parts
                        if len(parts) < 2:
                            raise WorkflowValidationError(
                                "file publication index is invalid"
                            )
                        _action_id(parts[0])
                        indexed_logical_id = PurePosixPath(*parts[1:]).as_posix()
                    if reference.logical_id != indexed_logical_id:
                        raise WorkflowValidationError("file publication index is invalid")
                    self.resolve_published(reference)
                    references.append(reference)
        return tuple(references)

    def output_manifest(self) -> tuple[FileReference, ...]:
        """Compatibility alias for the formal, verified artifact manifest."""

        return self.published_manifest()


def _logical_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 1_024
        or "\\" in value
        or "\x00" in value
    ):
        raise WorkflowValidationError("file logical_id is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise WorkflowValidationError("file logical_id must be a confined relative path")
    normalized = path.as_posix()
    if normalized != value:
        raise WorkflowValidationError("file logical_id must be normalized")
    return normalized


def _action_id(value: object) -> str:
    """Validate one action directory component without treating it as a path."""

    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 256
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or value in {".", ".."}
    ):
        raise WorkflowValidationError("file action_id is invalid")
    return value


def _publish_copy(source: Path, destination: Path, reference: FileReference) -> None:
    """Install one complete copied artifact without overwriting an identity.

    A completed temporary file is hard-linked into place, which gives the
    local filesystem an exclusive, no-replace publication point.  The artifact
    is deliberately a copy rather than a hard link to mutable staging output.
    """

    if destination.is_symlink():
        raise WorkflowValidationError("published output path must not be a symlink")
    if destination.exists():
        if not destination.is_file():
            raise WorkflowValidationError("published output path is occupied")
        if _reference_for(destination, reference.logical_id) != reference:
            raise WorkflowValidationError("published output identity is occupied")
        return
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".v6-publish-", dir=destination.parent
        )
    except OSError as error:
        raise WorkflowValidationError("could not prepare published output") from error
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as target, source.open("rb") as handle:
            shutil.copyfileobj(handle, target, length=1024 * 1024)
        if _reference_for(temporary, reference.logical_id) != reference:
            raise WorkflowValidationError("output changed before publication")
        try:
            os.chmod(temporary, 0o444)
            os.link(temporary, destination)
        except FileExistsError:
            if (
                destination.is_symlink()
                or not destination.is_file()
                or _reference_for(destination, reference.logical_id) != reference
            ):
                raise WorkflowValidationError("published output identity is occupied")
        except OSError as error:
            raise WorkflowValidationError("could not atomically publish output") from error
    except OSError as error:
        raise WorkflowValidationError("could not publish output") from error
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _publish_bytes(destination: Path, payload: bytes) -> None:
    """Atomically install a small immutable action-publication index."""

    if destination.is_symlink():
        raise WorkflowValidationError("published action output is invalid")
    if destination.exists():
        try:
            if destination.is_file() and destination.read_bytes() == payload:
                return
        except OSError as error:
            raise WorkflowValidationError("published action output is invalid") from error
        raise WorkflowValidationError("published action output is occupied")
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".v6-publication-", dir=destination.parent
        )
    except OSError as error:
        raise WorkflowValidationError("could not prepare published action output") from error
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
        try:
            os.chmod(temporary, 0o444)
            os.link(temporary, destination)
        except FileExistsError:
            try:
                if destination.is_file() and destination.read_bytes() == payload:
                    return
            except OSError as error:
                raise WorkflowValidationError("published action output is invalid") from error
            raise WorkflowValidationError("published action output is occupied")
        except OSError as error:
            raise WorkflowValidationError(
                "could not atomically publish action output"
            ) from error
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def bundle_file_references(manifest: Mapping[str, object]) -> dict[str, FileReference]:
    """Decode a validated bundle ``manifest.json`` into strict references.

    Bundle v1 stores file metadata in ``files``.  Bundle v2 signs every
    archive member in ``entries``, so only its ``files/`` entries are
    projected here.  This accepts the JSON object already integrity-checked
    by bundle import; it does not open an archive, trust a path, or perform
    any filesystem work.  The caller supplies the import/project root
    explicitly to :meth:`FileWorkspace.stage_declared_inputs`.
    """

    if "files" in manifest:
        raw_files = manifest["files"]
        if not isinstance(raw_files, Mapping):
            raise WorkflowValidationError("bundle file manifest is invalid")
        file_entries = raw_files.items()
    else:
        raw_entries = manifest.get("entries")
        if not isinstance(raw_entries, Mapping):
            raise WorkflowValidationError("bundle file manifest is invalid")
        v2_files: list[tuple[str, object]] = []
        for name, metadata in raw_entries.items():
            if not isinstance(name, str) or not isinstance(metadata, Mapping):
                raise WorkflowValidationError("bundle file manifest is invalid")
            if name.startswith("files/"):
                v2_files.append((name[len("files/") :], metadata))
        file_entries = v2_files
    result: dict[str, FileReference] = {}
    for logical_id, raw_metadata in file_entries:
        if not isinstance(logical_id, str) or not isinstance(raw_metadata, Mapping):
            raise WorkflowValidationError("bundle file manifest is invalid")
        if set(raw_metadata) != {"sha256", "bytes"}:
            raise WorkflowValidationError("bundle file manifest is invalid")
        result[logical_id] = FileReference(
            logical_id,
            raw_metadata["sha256"],  # type: ignore[arg-type]
            raw_metadata["bytes"],  # type: ignore[arg-type]
        )
    return dict(sorted(result.items()))


def _workspace_root(path: Path, *, create: bool) -> Path:
    if path.is_symlink():
        raise WorkflowValidationError("file workspace root must not be a symlink")
    try:
        if create:
            path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise WorkflowValidationError("could not create file workspace root") from error
    if path.is_symlink() or not path.is_dir():
        raise WorkflowValidationError("file workspace root must be a real directory")
    return path.resolve()


def _workspace_path(root: Path, logical_id: str, *, create_parent: bool) -> Path:
    normalized = _logical_id(logical_id)
    path = root
    components = PurePosixPath(normalized).parts
    for component in components[:-1]:
        path = path / component
        if path.is_symlink():
            raise WorkflowValidationError("file workspace path must not traverse symlinks")
        if create_parent:
            try:
                path.mkdir(exist_ok=True)
            except OSError as error:
                raise WorkflowValidationError("could not create file workspace path") from error
        if not path.is_dir():
            if not create_parent:
                # Lookup callers still need a confined candidate path so they
                # can report an unavailable reference rather than treating a
                # missing publication directory as a path escape.
                return root.joinpath(*components)
            raise WorkflowValidationError("file workspace parent is not a directory")
    result = path / components[-1]
    # ``logical_id`` already disallows absolute/parent components.  This
    # check makes the containment claim explicit even on unusual platforms.
    try:
        result.relative_to(root)
    except ValueError as error:  # pragma: no cover - PurePosixPath guard above
        raise WorkflowValidationError("file workspace path escaped its root") from error
    return result


def _reference_for(path: Path, logical_id: str) -> FileReference:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except OSError as error:
        raise WorkflowValidationError("could not read workspace file") from error
    return FileReference(logical_id, _DIGEST_PREFIX + digest.hexdigest(), size)
