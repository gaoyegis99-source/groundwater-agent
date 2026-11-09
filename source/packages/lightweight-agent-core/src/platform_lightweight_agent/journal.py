"""Run journals with atomic state/event transitions.

The public protocol intentionally exposes records rather than a SQLite
connection.  SQLite is one implementation detail appropriate for local V6
use; applications can provide a different journal without changing the engine.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import os
import shutil
import sqlite3
import tempfile
import time
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any, Protocol

from .state_edits import StateWriteSet
from .spec import (
    JsonValue,
    WorkflowValidationError,
    _is_secret_key_text,
    canonical_json,
    contains_secret_name,
    json_copy,
)

if TYPE_CHECKING:
    from .persistent_state import PersistentCellState

_VALUE_INLINE_BYTES = 256
_VALUE_STORAGE_KEY = "$v6_storage"
_CELL_ENTRY_SECTIONS = frozenset({"actions", "agents", "branches"})
_CELL_MEMBER_SECTIONS = frozenset({"completed", "approved_steps"})
_CELL_V3_ENTRY_SECTIONS = frozenset(
    {
        "step_outputs",
        "step_states",
        "branch_states",
        "pending_approvals",
        "approval_decisions",
        "node_timeouts",
        "subworkflow_timeouts",
        "step_started_at",
    }
)
_CELL_ABSENT = object()
JOURNAL_STORAGE_FORMAT_V1 = "v6-sqlite-run-journal-v1"
JOURNAL_STORAGE_VERSION = 7
_STATE_LAYOUT_SNAPSHOT = "snapshot"
_STATE_LAYOUT_CELLS = "cells-v1"
_TERMINAL_RUN_STATUSES = frozenset({"SUCCEEDED", "BLOCKED", "CANCELLED"})
BACKUP_RECOVERY_STATUS = "RECOVERY_PENDING"
BACKUP_RECOVERY_PROTOCOL_V1 = "v6-backup-recovery-v1"
# These states can make safe progress without an additional human decision.
# ``WAITING_APPROVAL`` deliberately does not appear here, and backup-restored
# runs use a separate recovery hold rather than being picked up automatically.
_RECOVERABLE_RUN_STATUSES = frozenset({"RUNNING", "CANCEL_REQUESTED"})


class RunNotFoundError(KeyError):
    pass


class RunConflictError(RuntimeError):
    pass


class RunExecutionMismatchError(RunConflictError):
    """A caller addressed a replaced run name with an old execution identity."""

    code = "RUN_EXECUTION_MISMATCH"

    def __init__(self) -> None:
        super().__init__(self.code)


class RunJournalError(RuntimeError):
    pass


class RunJournalBusyError(RunJournalError):
    """A bounded SQLite control read met a transient database lock."""


class _CellDeltaUnavailableError(RunJournalError):
    """This run or transition needs the existing full-snapshot fallback."""


class EventCursorError(WorkflowValidationError):
    """An instance-bound event cursor could not be decoded or validated."""

    code = "EVENT_CURSOR_INVALID"

    def __init__(self) -> None:
        super().__init__(self.code)


class RunQueryCursorError(WorkflowValidationError):
    """A run-list cursor could not be decoded or validated."""

    code = "RUN_QUERY_CURSOR_INVALID"

    def __init__(self) -> None:
        super().__init__(self.code)


class RunQueryUnsupportedError(RunJournalError):
    """The supplied journal deliberately has no optional list projection."""

    code = "RUN_QUERY_UNSUPPORTED"

    def __init__(self) -> None:
        super().__init__(self.code)


def _validated_execution_id(value: object) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 128:
        raise WorkflowValidationError("execution_id is invalid")
    return value


def _expected_execution_id(value: object) -> str | None:
    if value is None:
        return None
    return _validated_execution_id(value)


def _require_execution_id(actual: str, expected: str | None) -> None:
    if expected is not None and actual != expected:
        raise RunExecutionMismatchError()


@dataclass(frozen=True)
class RunHandle:
    """An immutable address for exactly one execution of a reusable run name."""

    run_id: str
    execution_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id:
            raise WorkflowValidationError("run_id is required")
        object.__setattr__(
            self, "execution_id", _validated_execution_id(self.execution_id)
        )

    def to_mapping(self) -> dict[str, str]:
        return {"run_id": self.run_id, "execution_id": self.execution_id}


_EVENT_CURSOR_TOKEN_PREFIX = "v6ec1."
_RUN_LIST_CURSOR_TOKEN_PREFIX = "v6rc1."
RUN_PAGE_SCHEMA_V1 = "v6-run-page-v1"
_RUN_QUERY_DEFAULT_LIMIT = 50
_RUN_QUERY_MAX_LIMIT = 100
_RUN_QUERY_MAX_STATUSES = 32


@dataclass(frozen=True)
class EventCursor:
    """A durable event position bound to one concrete execution instance.

    ``sequence`` remains monotonic only within an execution.  The compact
    token form is suitable for SSE ``id`` / ``Last-Event-ID`` transport; the
    structured mapping is more convenient for JSON APIs and embedding code.
    """

    execution_id: str
    sequence: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "execution_id", _validated_execution_id(self.execution_id)
        )
        if (
            not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 0
        ):
            raise WorkflowValidationError("event cursor sequence is invalid")

    def to_mapping(self) -> dict[str, JsonValue]:
        return {"execution_id": self.execution_id, "sequence": self.sequence}

    def to_token(self) -> str:
        encoded = (
            base64.urlsafe_b64encode(canonical_json(self.to_mapping()).encode("utf-8"))
            .decode("ascii")
            .rstrip("=")
        )
        return _EVENT_CURSOR_TOKEN_PREFIX + encoded

    @classmethod
    def from_mapping(cls, value: object) -> EventCursor:
        if not isinstance(value, Mapping) or set(value) != {"execution_id", "sequence"}:
            raise EventCursorError()
        try:
            return cls(value["execution_id"], value["sequence"])
        except WorkflowValidationError as error:
            raise EventCursorError() from error

    @classmethod
    def from_token(cls, value: object) -> EventCursor:
        if (
            not isinstance(value, str)
            or not value.startswith(_EVENT_CURSOR_TOKEN_PREFIX)
            or len(value) > 1_024
        ):
            raise EventCursorError()
        encoded = value[len(_EVENT_CURSOR_TOKEN_PREFIX) :]
        if not encoded:
            raise EventCursorError()
        try:
            padding = "=" * (-len(encoded) % 4)
            decoded = base64.b64decode(
                (encoded + padding).encode("ascii"), altchars=b"-_", validate=True
            )
            if len(decoded) > 512:
                raise ValueError("cursor payload is too large")
            return cls.from_mapping(json.loads(decoded.decode("utf-8")))
        except (
            UnicodeError,
            ValueError,
            json.JSONDecodeError,
            EventCursorError,
        ) as error:
            if isinstance(error, EventCursorError):
                raise
            raise EventCursorError() from error


def _validated_query_timestamp(value: object) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise WorkflowValidationError("run query timestamp is invalid")
    return float(value)


def _validated_query_text(value: object, field: str, *, maximum: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or "\x00" in value
    ):
        raise WorkflowValidationError(f"{field} is invalid")
    return value


@dataclass(frozen=True)
class RunListCursor:
    """Stable opaque position in the immutable run-creation ordering."""

    created_at: float
    run_id: str
    execution_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "created_at", _validated_query_timestamp(self.created_at)
        )
        object.__setattr__(
            self, "run_id", _validated_query_text(self.run_id, "run_id", maximum=1_024)
        )
        object.__setattr__(
            self, "execution_id", _validated_execution_id(self.execution_id)
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "created_at": self.created_at,
            "run_id": self.run_id,
            "execution_id": self.execution_id,
        }

    def to_token(self) -> str:
        encoded = (
            base64.urlsafe_b64encode(canonical_json(self.to_mapping()).encode("utf-8"))
            .decode("ascii")
            .rstrip("=")
        )
        return _RUN_LIST_CURSOR_TOKEN_PREFIX + encoded

    @classmethod
    def from_mapping(cls, value: object) -> RunListCursor:
        if not isinstance(value, Mapping) or set(value) != {
            "created_at",
            "run_id",
            "execution_id",
        }:
            raise RunQueryCursorError()
        try:
            return cls(value["created_at"], value["run_id"], value["execution_id"])
        except WorkflowValidationError as error:
            raise RunQueryCursorError() from error

    @classmethod
    def from_token(cls, value: object) -> RunListCursor:
        if (
            not isinstance(value, str)
            or not value.startswith(_RUN_LIST_CURSOR_TOKEN_PREFIX)
            or len(value) > 1_024
        ):
            raise RunQueryCursorError()
        encoded = value[len(_RUN_LIST_CURSOR_TOKEN_PREFIX) :]
        if not encoded:
            raise RunQueryCursorError()
        try:
            padding = "=" * (-len(encoded) % 4)
            decoded = base64.b64decode(
                (encoded + padding).encode("ascii"), altchars=b"-_", validate=True
            )
            if len(decoded) > 512:
                raise ValueError("cursor payload is too large")
            return cls.from_mapping(json.loads(decoded.decode("utf-8")))
        except (
            UnicodeError,
            ValueError,
            json.JSONDecodeError,
            RunQueryCursorError,
        ) as error:
            if isinstance(error, RunQueryCursorError):
                raise
            raise RunQueryCursorError() from error


@dataclass(frozen=True)
class RunQueryFilter:
    """Bounded, credential-free filters for the optional run-list projection."""

    statuses: tuple[str, ...] = ()
    workflow_id: str | None = None
    pending_only: bool = False
    include_archived: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.statuses, str):
            raise WorkflowValidationError("run query statuses are invalid")
        try:
            statuses = tuple(self.statuses)
        except TypeError as error:
            raise WorkflowValidationError("run query statuses are invalid") from error
        if len(statuses) > _RUN_QUERY_MAX_STATUSES:
            raise WorkflowValidationError("run query has too many statuses")
        normalized = tuple(
            _validated_query_text(item, "run query status", maximum=128)
            for item in statuses
        )
        if len(set(normalized)) != len(normalized):
            raise WorkflowValidationError("run query statuses must be unique")
        if self.workflow_id is not None:
            object.__setattr__(
                self,
                "workflow_id",
                _validated_query_text(self.workflow_id, "workflow_id"),
            )
        for name in ("pending_only", "include_archived"):
            if not isinstance(getattr(self, name), bool):
                raise WorkflowValidationError(f"run query {name} is invalid")
        object.__setattr__(self, "statuses", normalized)


@dataclass(frozen=True)
class RunSummary:
    """One compact, payload-free run-list item."""

    run_id: str
    execution_id: str
    workflow_id: str
    workflow_version: str
    status: str
    created_at: float
    updated_at: float
    pending_approval_count: int
    failure_code: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "run_id", _validated_query_text(self.run_id, "run_id", maximum=1_024)
        )
        object.__setattr__(
            self, "execution_id", _validated_execution_id(self.execution_id)
        )
        object.__setattr__(
            self, "workflow_id", _validated_query_text(self.workflow_id, "workflow_id")
        )
        object.__setattr__(
            self,
            "workflow_version",
            _validated_query_text(self.workflow_version, "workflow_version"),
        )
        object.__setattr__(
            self, "status", _validated_query_text(self.status, "status", maximum=128)
        )
        object.__setattr__(
            self, "created_at", _validated_query_timestamp(self.created_at)
        )
        object.__setattr__(
            self, "updated_at", _validated_query_timestamp(self.updated_at)
        )
        if self.updated_at < self.created_at:
            raise WorkflowValidationError("run query timestamps are inconsistent")
        if (
            not isinstance(self.pending_approval_count, int)
            or isinstance(self.pending_approval_count, bool)
            or self.pending_approval_count < 0
        ):
            raise WorkflowValidationError("pending approval count is invalid")
        if self.failure_code is not None:
            object.__setattr__(
                self,
                "failure_code",
                _validated_query_text(self.failure_code, "failure_code", maximum=128),
            )

    @property
    def cursor(self) -> RunListCursor:
        return RunListCursor(self.created_at, self.run_id, self.execution_id)

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "run_id": self.run_id,
            "execution_id": self.execution_id,
            "workflow_id": self.workflow_id,
            "workflow_version": self.workflow_version,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "pending_approval_count": self.pending_approval_count,
            "failure_code": self.failure_code,
        }


@dataclass(frozen=True)
class RunPage:
    """One bounded page from a :class:`RunQuery` implementation."""

    items: tuple[RunSummary, ...]
    next_cursor: RunListCursor | None = None

    def __post_init__(self) -> None:
        if not all(isinstance(item, RunSummary) for item in self.items):
            raise WorkflowValidationError("run page contains an invalid item")
        if self.next_cursor is not None and not isinstance(
            self.next_cursor, RunListCursor
        ):
            raise WorkflowValidationError("run page cursor is invalid")

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "schema_version": RUN_PAGE_SCHEMA_V1,
            "items": [item.to_mapping() for item in self.items],
            "next_cursor": (
                self.next_cursor.to_token() if self.next_cursor is not None else None
            ),
        }


@dataclass(frozen=True)
class RunEvent:
    run_id: str
    sequence: int
    type: str
    payload: Mapping[str, JsonValue]
    created_at: float = field(default_factory=time.time)
    # Keep this after ``created_at`` to retain compatibility with the former
    # positional constructor.  Legacy persisted events derive their identity
    # from the old run-id identity rule.
    execution_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "execution_id",
            _validated_execution_id(
                self.run_id if self.execution_id == "" else self.execution_id
            ),
        )

    @property
    def cursor(self) -> EventCursor:
        return EventCursor(self.execution_id, self.sequence)

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "run_id": self.run_id,
            "execution_id": self.execution_id,
            "sequence": self.sequence,
            "cursor": self.cursor.to_mapping(),
            "cursor_token": self.cursor.to_token(),
            "type": self.type,
            "payload": dict(self.payload),
            "created_at": self.created_at,
            "payload_size_bytes": len(canonical_json(self.payload).encode("utf-8")),
        }


@dataclass(frozen=True)
class RunStatus:
    """Small journal projection used by polling and event subscribers."""

    run_id: str
    status: str
    revision: int
    last_sequence: int
    archived_at: float | None = None
    execution_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "execution_id",
            _validated_execution_id(
                self.run_id if self.execution_id == "" else self.execution_id
            ),
        )

    @property
    def handle(self) -> RunHandle:
        return RunHandle(self.run_id, self.execution_id)

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "run_id": self.run_id,
            "execution_id": self.execution_id,
            "status": self.status,
            "revision": self.revision,
            "last_sequence": self.last_sequence,
            "archived_at": self.archived_at,
        }


@dataclass(frozen=True)
class RunControl:
    """Small control-plane projection for active executor polling."""

    run_id: str
    status: str
    revision: int
    cancel_requested: bool
    execution_generation: int
    execution_owner_id: str | None
    execution_lease_expires: float | None
    execution_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "execution_id",
            _validated_execution_id(
                self.run_id if self.execution_id == "" else self.execution_id
            ),
        )

    @property
    def handle(self) -> RunHandle:
        return RunHandle(self.run_id, self.execution_id)

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "run_id": self.run_id,
            "execution_id": self.execution_id,
            "status": self.status,
            "revision": self.revision,
            "cancel_requested": self.cancel_requested,
            "execution_generation": self.execution_generation,
            "execution_owner_id": self.execution_owner_id,
            "execution_lease_expires": self.execution_lease_expires,
        }


@dataclass(frozen=True)
class JournalStorageInfo:
    """Versioned, read-only identity of a local SQLite journal."""

    format: str
    schema_version: int
    migrations: tuple[int, ...]

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "format": self.format,
            "schema_version": self.schema_version,
            "migrations": list(self.migrations),
        }


@dataclass(frozen=True)
class JournalSpaceInfo:
    """Physical SQLite space usage; logical deletion is reported separately."""

    database_size_bytes: int
    wal_size_bytes: int
    page_size_bytes: int
    page_count: int
    freelist_pages: int
    reusable_bytes: int
    live_bytes_estimate: int
    compact_size_estimate_bytes: int

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "schema_version": "v6-journal-space-v1",
            "database_size_bytes": self.database_size_bytes,
            "wal_size_bytes": self.wal_size_bytes,
            "page_size_bytes": self.page_size_bytes,
            "page_count": self.page_count,
            "freelist_pages": self.freelist_pages,
            "reusable_bytes": self.reusable_bytes,
            "live_bytes_estimate": self.live_bytes_estimate,
            "compact_size_estimate_bytes": self.compact_size_estimate_bytes,
        }


@dataclass(frozen=True)
class JournalVacuumReport:
    """Observed effect of one explicit, offline-safe SQLite VACUUM."""

    before: JournalSpaceInfo
    after: JournalSpaceInfo

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "schema_version": "v6-journal-vacuum-report-v1",
            "before": self.before.to_mapping(),
            "after": self.after.to_mapping(),
            "reclaimed_bytes": max(
                0,
                self.before.database_size_bytes - self.after.database_size_bytes,
            ),
        }


@dataclass(frozen=True)
class RunRetentionPolicy:
    """Explicit lifecycle policy for terminal local runs.

    Archiving only marks a terminal row as retained locally.  Purging is opt-in
    through ``purge_after_seconds`` or ``max_archived_runs`` and is the only
    destructive operation.  Active, waiting, and reconciling runs are never
    candidates.
    """

    archive_after_seconds: float = 30 * 24 * 60 * 60
    purge_after_seconds: float | None = None
    max_archived_runs: int | None = None

    def __post_init__(self) -> None:
        for name in ("archive_after_seconds", "purge_after_seconds"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value < 0
            ):
                raise WorkflowValidationError(f"{name} must be non-negative")
            if value is not None:
                object.__setattr__(self, name, float(value))
        if self.max_archived_runs is not None and (
            not isinstance(self.max_archived_runs, int)
            or isinstance(self.max_archived_runs, bool)
            or self.max_archived_runs < 0
        ):
            raise WorkflowValidationError("max_archived_runs must be non-negative")


@dataclass(frozen=True)
class RunRetentionReport:
    """Exact maintenance effects from one explicit retention pass."""

    archived_run_ids: tuple[str, ...]
    purged_run_ids: tuple[str, ...]
    removed_definitions: int
    removed_values: int

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "archived_run_ids": list(self.archived_run_ids),
            "purged_run_ids": list(self.purged_run_ids),
            "removed_definitions": self.removed_definitions,
            "removed_values": self.removed_values,
        }


@dataclass(frozen=True)
class RunInspection:
    """Payload-free journal projection used by explanations and recovery views."""

    run_id: str
    workflow: Mapping[str, JsonValue]
    state: Mapping[str, JsonValue]
    status: str
    driver_bindings: Mapping[str, str]
    execution_id: str = ""
    revision: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "execution_id",
            _validated_execution_id(
                self.run_id if self.execution_id == "" else self.execution_id
            ),
        )

    @property
    def handle(self) -> RunHandle:
        return RunHandle(self.run_id, self.execution_id)


@dataclass(frozen=True)
class RunViewSource:
    """One persisted revision's control metadata and final output only."""

    inspection: RunInspection
    output: JsonValue


_DEFINITION_CACHE_TOKEN = object()
_MUTABLE_CACHE_TOKEN = object()


@dataclass(frozen=True)
class _SnapshotDefinition:
    """Verified immutable definition shared by successive run revisions.

    The canonical JSON forms are the authority for the workflow definition and
    frozen execution manifest.  A normal ``RunSnapshot`` still exposes plain
    detached mappings for ergonomic reads, but a later state-only transition
    never needs to re-scan, copy, or serialize those unchanged definition
    trees merely to prove they are credential-free again.
    """

    token: object
    digest: str
    workflow_json: str
    execution_manifest_json: str
    schema_version: str
    workflow_id: str
    workflow_version: str


@dataclass(frozen=True)
class _SnapshotMutable:
    """Validated canonical mutable sections shared only between owned copies.

    Every changed state is still serialized, checked for credential-shaped
    fields, and detached before CAS.  The cache avoids repeating that full
    scan when the journal makes its defensive owned copy and gives the v3
    coordinator a clean copy without re-serializing the public mapping.
    """

    token: object
    inputs_json: str
    state_json: str
    driver_bindings_json: str
    inputs_owner: int
    state_owner: int
    driver_bindings_owner: int


def _reject_secret_keys(value: dict[str, Any]) -> dict[str, Any]:
    # json.loads calls this once for each object, including nested objects.
    # Checking keys while decoding avoids walking the entire detached state
    # again on every journal revision.
    if any(_is_secret_key_text(key) for key in value):
        raise WorkflowValidationError("run checkpoints must not contain credentials")
    return value


def _checkpoint_object(
    serialized: str, field_name: str, *, reject_secrets: bool = False
) -> dict[str, Any]:
    try:
        decoded = json.loads(
            serialized,
            object_hook=_reject_secret_keys if reject_secrets else None,
        )
    except (
        TypeError,
        json.JSONDecodeError,
    ) as error:  # pragma: no cover - internal cache
        raise WorkflowValidationError("run mutable cache is invalid") from error
    if not isinstance(decoded, dict):
        raise WorkflowValidationError(f"run {field_name} must be an object")
    return decoded


def _validate_checkpoint_object(
    value: Mapping[str, Any], field_name: str
) -> tuple[dict[str, Any], str]:
    serialized = canonical_json(value)
    decoded = _checkpoint_object(serialized, field_name, reject_secrets=True)
    return decoded, serialized


def _definition_object(serialized: str, field_name: str) -> dict[str, JsonValue]:
    """Decode a cached canonical object without trusting caller-owned maps."""

    try:
        decoded = json.loads(serialized)
    except (
        TypeError,
        json.JSONDecodeError,
    ) as error:  # pragma: no cover - internal cache
        raise WorkflowValidationError("run definition cache is invalid") from error
    if not isinstance(decoded, dict):  # pragma: no cover - guarded at creation
        raise WorkflowValidationError(f"run {field_name} must be an object")
    return decoded


@dataclass(frozen=True)
class RunSnapshot:
    """A self-contained resume record; SDK-specific objects never enter it."""

    run_id: str
    workflow: Mapping[str, JsonValue]
    inputs: Mapping[str, JsonValue]
    state: Mapping[str, JsonValue]
    status: str
    driver_bindings: Mapping[str, str]
    execution_manifest: Mapping[str, JsonValue] = field(default_factory=dict)
    revision: int = 1
    # ``run_id`` is a caller-facing business key and may be reused after a
    # retained record is purged.  ``execution_id`` is the immutable identity
    # used for external idempotency keys.  It intentionally survives every
    # revision and resume of one snapshot.  Keep it after the original
    # optional fields so existing positional construction of ``RunSnapshot``
    # remains source-compatible.
    execution_id: str = ""
    _definition: _SnapshotDefinition | None = field(
        default=None, repr=False, compare=False, kw_only=True
    )
    _mutable: _SnapshotMutable | None = field(
        default=None, repr=False, compare=False, kw_only=True
    )
    # A process-local write set from the current coordinator transition. It
    # is never serialized. New v4 SQLite runs may use editor-minted writes to
    # update local cells; the journal verifies changed values and commits
    # them with revision CAS and the event in one transaction. Legacy layouts
    # and untrusted writers retain a full-state storage path.
    _state_write_set: StateWriteSet | None = field(
        default=None, repr=False, compare=False, kw_only=True
    )

    def __post_init__(self) -> None:
        if not self.run_id:
            raise WorkflowValidationError("run_id is required")
        # Snapshots written before ``execution_id`` existed derived external
        # action IDs directly from ``run_id``.  Preserve that exact identity
        # on resume: hashing or prefixing it here would make an old snapshot
        # calculate a new Action/approval/model ID and could redispatch work.
        legacy_identity = self.execution_id == ""
        execution_id = self.run_id if legacy_identity else self.execution_id
        if (
            not isinstance(execution_id, str)
            or not execution_id
            or (not legacy_identity and len(execution_id.encode("utf-8")) > 128)
        ):
            raise WorkflowValidationError("execution_id is invalid")
        if self.revision < 1:
            raise WorkflowValidationError("run revision must be positive")
        if self._state_write_set is not None and not isinstance(
            self._state_write_set, StateWriteSet
        ):
            raise WorkflowValidationError("run state write set is invalid")
        definition = self._definition
        if definition is None:
            workflow = json_copy(self.workflow)
            manifest = json_copy(self.execution_manifest)
            if not isinstance(workflow, dict) or not isinstance(manifest, dict):
                raise WorkflowValidationError(
                    "run workflow and execution_manifest must be objects"
                )
            if contains_secret_name(workflow) or contains_secret_name(manifest):
                raise WorkflowValidationError(
                    "run checkpoints must not contain credentials"
                )
            workflow_json = canonical_json(workflow)
            manifest_json = canonical_json(manifest)
            definition = _SnapshotDefinition(
                _DEFINITION_CACHE_TOKEN,
                "sha256:"
                + hashlib.sha256(
                    (workflow_json + "\n" + manifest_json).encode("utf-8")
                ).hexdigest(),
                workflow_json,
                manifest_json,
                str(workflow.get("schema_version", "")),
                str(workflow.get("workflow_id", "")),
                str(workflow.get("version", "")),
            )
        elif (
            not isinstance(definition, _SnapshotDefinition)
            or definition.token is not _DEFINITION_CACHE_TOKEN
        ):
            raise WorkflowValidationError("run definition cache is invalid")
        else:
            # State transitions share canonical definition authority. Public
            # mappings are detached lazily only if a caller actually reads them.
            workflow = None
            manifest = None
        mutable = self._mutable
        if mutable is not None and (
            not isinstance(mutable, _SnapshotMutable)
            or mutable.token is not _MUTABLE_CACHE_TOKEN
        ):
            raise WorkflowValidationError("run mutable cache is invalid")

        def section(value: Mapping[str, Any], name: str) -> tuple[dict[str, Any], str]:
            # dataclasses.replace carries caches, but a newly supplied section
            # must still cross JSON and credential validation. Unchanged public
            # views are never authority, even if the caller mutated them in place.
            if mutable is not None and id(value) == getattr(mutable, name + "_owner"):
                serialized = getattr(mutable, name + "_json")
                return _checkpoint_object(serialized, name), serialized
            return _validate_checkpoint_object(value, name)

        inputs, inputs_json = section(self.inputs, "inputs")
        state, state_json = section(self.state, "state")
        bindings, bindings_json = section(self.driver_bindings, "driver_bindings")
        mutable = _SnapshotMutable(
            _MUTABLE_CACHE_TOKEN,
            inputs_json,
            state_json,
            bindings_json,
            id(inputs),
            id(state),
            id(bindings),
        )
        object.__setattr__(self, "workflow", workflow)
        object.__setattr__(self, "inputs", inputs)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "driver_bindings", bindings)
        object.__setattr__(self, "execution_id", execution_id)
        object.__setattr__(self, "execution_manifest", manifest)
        object.__setattr__(self, "_definition", definition)
        object.__setattr__(self, "_mutable", mutable)

    def __getattribute__(self, name: str) -> Any:
        value = object.__getattribute__(self, name)
        if name in {"workflow", "execution_manifest"} and value is None:
            definition = object.__getattribute__(self, "_definition")
            if definition is None:
                return value
            serialized = (
                definition.workflow_json
                if name == "workflow"
                else definition.execution_manifest_json
            )
            value = _definition_object(serialized, name)
            object.__setattr__(self, name, value)
        return value

    def next_revision(self, **changes: Any) -> RunSnapshot:
        if {"workflow", "execution_manifest", "_definition", "_mutable"} & set(changes):
            raise WorkflowValidationError(
                "run definition is immutable across revisions"
            )
        # dataclasses.replace would read both public definition properties and
        # materialize them on every event. Internal commits need only metadata.
        fields = {
            "run_id": self.run_id,
            "workflow": {},
            "execution_manifest": {},
            "inputs": self.inputs,
            "state": self.state,
            "status": self.status,
            "driver_bindings": self.driver_bindings,
            "execution_id": self.execution_id,
            "revision": self.revision + 1,
            "_definition": self._definition,
            "_mutable": self._mutable,
        }
        return RunSnapshot(**(fields | changes))

    @property
    def handle(self) -> RunHandle:
        """Return the immutable public address of this execution instance."""

        return RunHandle(self.run_id, self.execution_id)

    def to_mapping(self) -> dict[str, JsonValue]:
        definition = self._definition
        if definition is None:  # pragma: no cover - safeguarded by __post_init__
            raise WorkflowValidationError("run definition cache is unavailable")
        return {
            "run_id": self.run_id,
            "workflow": _definition_object(definition.workflow_json, "workflow"),
            "inputs": dict(self.inputs),
            "state": dict(self.state),
            "status": self.status,
            "driver_bindings": dict(self.driver_bindings),
            "execution_id": self.execution_id,
            "execution_manifest": _definition_object(
                definition.execution_manifest_json, "execution_manifest"
            ),
            "revision": self.revision,
        }

    def _definition_parts(self) -> tuple[str, str, str]:
        """Return cached immutable definition storage fields for this module."""

        definition = self._definition
        if definition is None:  # pragma: no cover - safeguarded by __post_init__
            raise WorkflowValidationError("run definition cache is unavailable")
        return (
            definition.digest,
            definition.workflow_json,
            definition.execution_manifest_json,
        )

    def _definition_schema_version(self) -> str:
        definition = self._definition
        if definition is None:  # pragma: no cover - safeguarded by __post_init__
            raise WorkflowValidationError("run definition cache is unavailable")
        return definition.schema_version

    def _definition_identity(self) -> dict[str, str]:
        definition = self._definition
        assert definition is not None
        return {
            "workflow_id": definition.workflow_id,
            "version": definition.workflow_version,
        }

    def _detached_copy(self) -> RunSnapshot:
        """Copy mutable run state while retaining verified definition identity.

        ``InMemoryRunJournal`` needs a defensive copy just as SQLite does, but
        copying a revision that it already owns must not repeatedly revalidate
        the unchanged workflow/manifest trees.  The public mappings on the new
        snapshot are rebuilt from the cache by ``__post_init__``; mutable
        inputs, state, and bindings keep their normal detached-copy checks.
        """

        definition = self._definition
        if definition is None:  # pragma: no cover - safeguarded by __post_init__
            raise WorkflowValidationError("run definition cache is unavailable")
        mutable = self._mutable
        if mutable is None:  # pragma: no cover - safeguarded by __post_init__
            raise WorkflowValidationError("run mutable cache is unavailable")
        return RunSnapshot(
            run_id=self.run_id,
            workflow={},
            inputs=self.inputs,
            state=self.state,
            status=self.status,
            driver_bindings=self.driver_bindings,
            execution_manifest={},
            revision=self.revision,
            execution_id=self.execution_id,
            _definition=definition,
            _mutable=mutable,
        )

    def _state_copy(self) -> dict[str, Any]:
        """Return the validated state authority for an internal transition."""

        mutable = self._mutable
        if mutable is None:  # pragma: no cover - safeguarded by __post_init__
            raise WorkflowValidationError("run mutable cache is unavailable")
        return _checkpoint_object(mutable.state_json, "state")

    def _retained_size_bytes(self) -> int:
        """Exact canonical byte size of retained run inputs plus mutable state."""

        mutable = self._mutable
        if mutable is None:  # pragma: no cover - safeguarded by __post_init__
            raise WorkflowValidationError("run mutable cache is unavailable")
        # canonical_json sorts these two keys as ``inputs`` then ``state``.
        envelope_bytes = len(b'{"inputs":,"state":}')
        return (
            envelope_bytes
            + len(mutable.inputs_json.encode("utf-8"))
            + len(mutable.state_json.encode("utf-8"))
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> RunSnapshot:
        return cls(
            run_id=str(value["run_id"]),
            workflow=value["workflow"],  # type: ignore[arg-type]
            inputs=value["inputs"],  # type: ignore[arg-type]
            state=value["state"],  # type: ignore[arg-type]
            status=str(value["status"]),
            driver_bindings=value["driver_bindings"],  # type: ignore[arg-type]
            execution_id=str(value.get("execution_id", "")),
            execution_manifest=value.get("execution_manifest", {}),  # type: ignore[arg-type]
            revision=int(value["revision"]),
        )


@dataclass(frozen=True)
class _CellCommitHead:
    """Process-local immutable identity for a v4 cell-layout transition.

    This is not a resumable checkpoint. The journal row and cells remain the
    only durable facts; a restarted owner rebuilds this head from ``get()``.
    """

    run_id: str
    execution_id: str
    definition_digest: str
    workflow_id: str
    workflow_version: str
    revision: int
    status: str
    cells: PersistentCellState = field(repr=False, compare=False)
    retained_upper_bound_bytes: int = field(repr=False, compare=False)

    @classmethod
    def from_snapshot(cls, snapshot: RunSnapshot) -> _CellCommitHead:
        from .persistent_state import PersistentCellState

        if snapshot._definition_schema_version() != "v6-light-workflow-v4":
            raise _CellDeltaUnavailableError("only v4 cell runs support local commits")
        identity = snapshot._definition_identity()
        return cls(
            snapshot.run_id,
            snapshot.execution_id,
            snapshot._definition_parts()[0],
            identity["workflow_id"],
            identity["version"],
            snapshot.revision,
            snapshot.status,
            PersistentCellState.from_state(snapshot._state_copy()),
            snapshot._retained_size_bytes(),
        )

    def upper_bound_after(self, write_set: StateWriteSet) -> int:
        """Conservative retained-size bound; exact near-limit checks fall back."""

        added = sum(
            len(write.value_json.encode("utf-8"))
            + len(canonical_json(write.path).encode("utf-8"))
            + 2
            for write in write_set.writes
            if write.value_json is not None
        )
        added += sum(
            len(canonical_json((*addition.path, addition.member)).encode("utf-8")) + 2
            for addition in write_set.member_additions
        )
        return self.retained_upper_bound_bytes + added

    def next_revision(
        self, status: str, cells: PersistentCellState, write_set: StateWriteSet
    ) -> _CellCommitHead:
        return _CellCommitHead(
            self.run_id,
            self.execution_id,
            self.definition_digest,
            self.workflow_id,
            self.workflow_version,
            self.revision + 1,
            status,
            cells,
            self.upper_bound_after(write_set),
        )


class RunJournal(Protocol):
    """Atomic source of truth for one local workflow run and its events."""

    def create(
        self, snapshot: RunSnapshot, event_type: str, payload: Mapping[str, JsonValue]
    ) -> RunSnapshot: ...

    def get(
        self, run_id: str, *, expected_execution_id: str | None = None
    ) -> RunSnapshot: ...

    def replace(
        self,
        snapshot: RunSnapshot,
        *,
        expected_revision: int,
        event_type: str,
        payload: Mapping[str, JsonValue],
        expected_execution_id: str | None = None,
    ) -> RunSnapshot: ...

    def status(
        self, run_id: str, *, expected_execution_id: str | None = None
    ) -> RunStatus: ...

    def control(
        self, run_id: str, *, expected_execution_id: str | None = None
    ) -> RunControl: ...

    def recoverable_runs(
        self,
        *,
        limit: int = 256,
        now: float | None = None,
        after: tuple[int, str] | None = None,
    ) -> tuple[RunControl, ...]: ...

    def events(
        self,
        run_id: str,
        *,
        after: int = 0,
        limit: int = 256,
        expected_execution_id: str | None = None,
    ) -> tuple[RunEvent, ...]: ...


class RunQuery(Protocol):
    """Optional, read-only compact run-list capability.

    This protocol deliberately does not extend :class:`RunJournal`: embedded
    applications may keep a minimal custom journal and opt out of operator
    listing without losing execution support.  Implementations return only
    derived control metadata, never workflow inputs, outputs, or credentials.
    """

    def list_runs(
        self,
        filter: RunQueryFilter | None = None,
        cursor: RunListCursor | None = None,
        limit: int = _RUN_QUERY_DEFAULT_LIMIT,
    ) -> RunPage: ...


def _event(
    run_id: str,
    sequence: int,
    type: str,
    payload: Mapping[str, JsonValue],
    execution_id: str | None = None,
) -> RunEvent:
    if not type or len(type) > 128:
        raise WorkflowValidationError("event type is invalid")
    return RunEvent(
        run_id,
        sequence,
        type,
        _redact(payload),
        time.time(),
        run_id if execution_id is None else execution_id,
    )


def _event_limit(limit: int) -> int:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1_000:
        raise WorkflowValidationError("event page limit must be between 1 and 1000")
    return limit


def _run_query_limit(limit: object) -> int:
    if (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= _RUN_QUERY_MAX_LIMIT
    ):
        raise WorkflowValidationError(
            f"run query page limit must be between 1 and {_RUN_QUERY_MAX_LIMIT}"
        )
    return limit


def _run_query_filter(value: RunQueryFilter | None) -> RunQueryFilter:
    if value is None:
        return RunQueryFilter()
    if not isinstance(value, RunQueryFilter):
        raise WorkflowValidationError("run query filter is invalid")
    return value


def _run_query_cursor(value: RunListCursor | None) -> RunListCursor | None:
    if value is not None and not isinstance(value, RunListCursor):
        raise WorkflowValidationError("run query cursor is invalid")
    return value


def _event_after(after: object) -> int:
    if not isinstance(after, int) or isinstance(after, bool) or after < 0:
        raise WorkflowValidationError("event cursor must be a non-negative integer")
    return after


def _recovery_moment(now: float | None) -> float:
    moment = time.time() if now is None else now
    if not isinstance(moment, (int, float)) or isinstance(moment, bool) or moment < 0:
        raise WorkflowValidationError("recovery scan time must be a timestamp")
    return float(moment)


def _recovery_cursor(after: object) -> tuple[int, str] | None:
    """Validate the opaque, stable ordering cursor used by host scans.

    A cursor is deliberately only a compact scheduling hint.  It is not a
    claim token and is never persisted, so a restarted host safely begins a
    new fair scan from the journal's current durable control rows.
    """

    if after is None:
        return None
    if (
        not isinstance(after, tuple)
        or len(after) != 2
        or not isinstance(after[0], int)
        or isinstance(after[0], bool)
        or after[0] < 0
        or not isinstance(after[1], str)
        or not after[1]
    ):
        raise WorkflowValidationError("recovery scan cursor is invalid")
    return after


def _redact(value: object) -> Any:
    """Journal events are diagnostics and must never contain credential values."""

    sensitive = {
        "api_key",
        "authorization",
        "password",
        "private_key",
        "secret",
        "token",
    }
    if isinstance(value, Mapping):
        result = {}
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in sensitive or normalized.endswith(("_token", "_secret")):
                result[str(key)] = "[REDACTED]"
            else:
                result[str(key)] = _redact(child)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    return value


class InMemoryRunJournal:
    """Thread-safe journal useful for embedding and deterministic unit tests."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._snapshots: dict[str, RunSnapshot] = {}
        self._cell_heads: dict[str, _CellCommitHead] = {}
        self._events: dict[str, list[RunEvent]] = {}

    def _current_snapshot(self, run_id: str) -> RunSnapshot:
        snapshot = self._snapshots.get(run_id)
        if snapshot is None:
            raise RunNotFoundError(run_id)
        head = self._cell_heads.get(run_id)
        if head is not None and head.revision != snapshot.revision:
            snapshot = snapshot.next_revision(
                revision=head.revision,
                status=head.status,
                state=head.cells.to_state(),
            )
            self._snapshots[run_id] = snapshot
        return snapshot

    def _current_control(self, run_id: str, snapshot: RunSnapshot) -> RunControl:
        head = self._cell_heads.get(run_id)
        if head is None:
            return _snapshot_control(snapshot)
        cancel, generation, owner_id, lease = _state_control(head.cells.view())
        return RunControl(
            run_id,
            head.status,
            head.revision,
            cancel,
            generation,
            owner_id,
            lease,
            head.execution_id,
        )

    def create(
        self, snapshot: RunSnapshot, event_type: str, payload: Mapping[str, JsonValue]
    ) -> RunSnapshot:
        with self._lock:
            if snapshot.run_id in self._snapshots:
                raise RunConflictError("run already exists")
            saved = _snapshot_copy(snapshot)
            event = _event(
                snapshot.run_id, 1, event_type, payload, snapshot.execution_id
            )
            self._snapshots[snapshot.run_id] = saved
            self._events[snapshot.run_id] = [event]
            # ``saved`` is the defensive copy owned by the journal.  Returning
            # the caller's already-validated snapshot keeps that ownership
            # boundary while avoiding a third full JSON clone for every local
            # state transition.
            return snapshot

    def get(
        self, run_id: str, *, expected_execution_id: str | None = None
    ) -> RunSnapshot:
        expected = _expected_execution_id(expected_execution_id)
        with self._lock:
            snapshot = self._current_snapshot(run_id)
            _require_execution_id(snapshot.execution_id, expected)
            return _snapshot_copy(snapshot)

    def inspection(
        self, run_id: str, *, expected_execution_id: str | None = None
    ) -> RunInspection:
        expected = _expected_execution_id(expected_execution_id)
        with self._lock:
            snapshot = self._current_snapshot(run_id)
            _require_execution_id(snapshot.execution_id, expected)
            return _snapshot_inspection(snapshot)

    def view_source(
        self, run_id: str, *, expected_execution_id: str | None = None
    ) -> RunViewSource:
        with self._lock:
            snapshot = self._current_snapshot(run_id)
            _require_execution_id(
                snapshot.execution_id, _expected_execution_id(expected_execution_id)
            )
            return _snapshot_view_source(snapshot)

    def replace(
        self,
        snapshot: RunSnapshot,
        *,
        expected_revision: int,
        event_type: str,
        payload: Mapping[str, JsonValue],
        expected_execution_id: str | None = None,
    ) -> RunSnapshot:
        expected = _expected_execution_id(expected_execution_id)
        with self._lock:
            current = self._current_snapshot(snapshot.run_id)
            _require_execution_id(current.execution_id, expected)
            if current.execution_id != snapshot.execution_id:
                raise RunExecutionMismatchError()
            if current.revision != expected_revision:
                raise RunConflictError("run changed before this transition")
            if snapshot.revision != expected_revision + 1:
                raise RunJournalError("journal transitions must advance one revision")
            saved = _snapshot_copy(snapshot)
            event = _event(
                snapshot.run_id,
                len(self._events[snapshot.run_id]) + 1,
                event_type,
                payload,
                snapshot.execution_id,
            )
            self._snapshots[snapshot.run_id] = saved
            self._cell_heads.pop(snapshot.run_id, None)
            self._events[snapshot.run_id].append(event)
            # See ``create``: callers never receive the journal-owned copy.
            return snapshot

    def _replace_cell_writes(
        self,
        head: _CellCommitHead,
        write_set: StateWriteSet,
        *,
        status: str,
        event_type: str,
        payload: Mapping[str, JsonValue],
    ) -> _CellCommitHead:
        """Use the same v4 cell projection without revalidating every sibling."""

        from .persistent_state import PersistentCellState

        if (
            not isinstance(head, _CellCommitHead)
            or not isinstance(head.cells, PersistentCellState)
            or not isinstance(write_set, StateWriteSet)
            or not write_set._from_editor
            or status != head.status
            or status != "RUNNING"
        ):
            raise _CellDeltaUnavailableError("local cell commit basis is invalid")
        projected = head.cells.apply_write_set(write_set)
        if projected is None:
            raise _CellDeltaUnavailableError("local cell write needs full state")
        next_head = head.next_revision(status, projected, write_set)
        with self._lock:
            current = self._snapshots.get(head.run_id)
            if current is None:
                raise RunNotFoundError(head.run_id)
            basis = self._cell_heads.get(head.run_id)
            revision = basis.revision if basis is not None else current.revision
            if current.execution_id != head.execution_id:
                raise RunExecutionMismatchError()
            if revision != head.revision:
                raise RunConflictError("run changed before this transition")
            if current._definition_parts()[0] != head.definition_digest:
                raise RunConflictError("immutable run definition changed")
            event = _event(
                head.run_id,
                len(self._events[head.run_id]) + 1,
                event_type,
                payload,
                head.execution_id,
            )
            self._cell_heads[head.run_id] = next_head
            self._events[head.run_id].append(event)
        return next_head

    def status(
        self, run_id: str, *, expected_execution_id: str | None = None
    ) -> RunStatus:
        expected = _expected_execution_id(expected_execution_id)
        with self._lock:
            snapshot = self._snapshots.get(run_id)
            if snapshot is None:
                raise RunNotFoundError(run_id)
            _require_execution_id(snapshot.execution_id, expected)
            head = self._cell_heads.get(run_id)
            return RunStatus(
                run_id,
                head.status if head is not None else snapshot.status,
                head.revision if head is not None else snapshot.revision,
                len(self._events[run_id]),
                execution_id=snapshot.execution_id,
            )

    def control(
        self, run_id: str, *, expected_execution_id: str | None = None
    ) -> RunControl:
        expected = _expected_execution_id(expected_execution_id)
        with self._lock:
            snapshot = self._snapshots.get(run_id)
            if snapshot is None:
                raise RunNotFoundError(run_id)
            _require_execution_id(snapshot.execution_id, expected)
            return self._current_control(run_id, snapshot)

    def recoverable_runs(
        self,
        *,
        limit: int = 256,
        now: float | None = None,
        after: tuple[int, str] | None = None,
    ) -> tuple[RunControl, ...]:
        """Return lease-free, nonterminal work for a bounded host drain.

        This is a scheduling projection, not a claim: the workflow engine
        still claims the persisted execution lease before it can run.
        """

        page_size = _event_limit(limit)
        moment = _recovery_moment(now)
        cursor = _recovery_cursor(after)
        with self._lock:
            controls = []
            for run_id, snapshot in self._snapshots.items():
                control = self._current_control(run_id, snapshot)
                if control.status in _RECOVERABLE_RUN_STATUSES:
                    controls.append(control)
        candidates = (
            control
            for control in sorted(
                controls, key=lambda item: (item.revision, item.run_id)
            )
            if (
                control.execution_owner_id is None
                or control.execution_lease_expires is None
                or control.execution_lease_expires <= moment
            )
            and (cursor is None or (control.revision, control.run_id) > cursor)
        )
        return tuple(candidates)[:page_size]

    def events(
        self,
        run_id: str,
        *,
        after: int = 0,
        limit: int = 256,
        expected_execution_id: str | None = None,
    ) -> tuple[RunEvent, ...]:
        page_size = _event_limit(limit)
        cursor = _event_after(after)
        expected = _expected_execution_id(expected_execution_id)
        with self._lock:
            snapshot = self._snapshots.get(run_id)
            if snapshot is None:
                raise RunNotFoundError(run_id)
            _require_execution_id(snapshot.execution_id, expected)
            return tuple(
                copy.deepcopy(event)
                for event in self._events[run_id]
                if event.sequence > cursor
            )[:page_size]

    def list_runs(
        self,
        filter: RunQueryFilter | None = None,
        cursor: RunListCursor | None = None,
        limit: int = _RUN_QUERY_DEFAULT_LIMIT,
    ) -> RunPage:
        """List the same compact projection as SQLite for test embeddings.

        The in-memory implementation derives timestamps from already durable
        in-memory events.  It intentionally does not add a second mutable
        index that could disagree with the snapshots it is meant to inspect.
        """

        query_filter = _run_query_filter(filter)
        page_size = _run_query_limit(limit)
        query_cursor = _run_query_cursor(cursor)
        with self._lock:
            summaries = [
                _run_summary_from_snapshot(
                    self._current_snapshot(run_id),
                    created_at=self._events[run_id][0].created_at,
                    updated_at=self._events[run_id][-1].created_at,
                )
                for run_id, snapshot in self._snapshots.items()
                if self._events.get(run_id)
            ]
        return _run_page(
            summaries,
            filter=query_filter,
            cursor=query_cursor,
            limit=page_size,
        )


def _snapshot_copy(snapshot: RunSnapshot) -> RunSnapshot:
    return snapshot._detached_copy()


def _snapshot_control(snapshot: RunSnapshot, *, state: object = None) -> RunControl:
    cancel, generation, owner_id, lease = _state_control(
        snapshot.state if state is None else state
    )
    return RunControl(
        snapshot.run_id,
        snapshot.status,
        snapshot.revision,
        cancel,
        generation,
        owner_id,
        lease,
        snapshot.execution_id,
    )


def _snapshot_view_source(snapshot: RunSnapshot) -> RunViewSource:
    return RunViewSource(
        _snapshot_inspection(snapshot), json_copy(_final_output(snapshot.state))
    )


def _final_output(state: object) -> JsonValue:
    v3 = state.get("v3") if isinstance(state, Mapping) else None
    return v3.get("output") if isinstance(v3, Mapping) else None


def _snapshot_inspection(snapshot: RunSnapshot) -> RunInspection:
    return RunInspection(
        snapshot.run_id,
        json_copy(snapshot.workflow),
        _inspection_state(snapshot.state),
        snapshot.status,
        {str(key): str(value) for key, value in snapshot.driver_bindings.items()},
        snapshot.execution_id,
        snapshot.revision,
    )


def _inspection_bindings(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _inspection_state(value: object) -> dict[str, JsonValue]:
    """Copy only control metadata needed by :mod:`inspection`.

    Stored-value envelopes and their inline payloads never cross this boundary.
    The presence of an action result is represented as an empty sentinel only.
    """

    if not isinstance(value, Mapping):
        return {}
    result: dict[str, JsonValue] = {}
    completed = value.get("completed")
    if isinstance(completed, list):
        result["completed"] = [item for item in completed if isinstance(item, str)]
    actions = value.get("actions")
    if isinstance(actions, Mapping):
        summaries: dict[str, JsonValue] = {}
        for action_id, raw in actions.items():
            if not isinstance(action_id, str) or not isinstance(raw, Mapping):
                continue
            item: dict[str, JsonValue] = {}
            for key in ("status", "step_key", "step_id"):
                field = raw.get(key)
                if isinstance(field, (str, int, float, bool)) or field is None:
                    item[key] = field
            request = raw.get("request")
            if isinstance(request, Mapping) and isinstance(
                request.get("tool"), Mapping
            ):
                item["request"] = {"tool": json_copy(request["tool"])}
            if raw.get("result") is not None:
                item["result"] = {}
            recovery = raw.get("recovery")
            if isinstance(recovery, Mapping):
                item["recovery"] = {
                    key: field
                    for key in (
                        "query_count",
                        "latest_result",
                        "last_query_at",
                        "next_query_at",
                        "latest_error_code",
                    )
                    if isinstance((field := recovery.get(key)), (str, int, float))
                    and not isinstance(field, bool)
                }
            summaries[action_id] = item
        result["actions"] = summaries
    failure = value.get("failure")
    if isinstance(failure, Mapping):
        result["failure"] = {
            key: field
            for key in (
                "code",
                "step_id",
                "action_id",
                "approval_id",
                "path",
                "keyword",
            )
            if isinstance((field := failure.get(key)), (str, int, float))
            and not isinstance(field, bool)
        }
    pending = value.get("pending")
    if isinstance(pending, Mapping):
        result["pending"] = {
            key: field
            for key in ("kind", "step_id", "action_id")
            if isinstance((field := pending.get(key)), str)
        }
    v3 = value.get("v3")
    if isinstance(v3, Mapping):
        control: dict[str, JsonValue] = {
            "cancel_requested": v3.get("cancel_requested") is True
        }
        step_states = v3.get("step_states")
        if isinstance(step_states, Mapping):
            control["step_states"] = {
                str(key): value
                for key, value in step_states.items()
                if isinstance(value, str)
            }
        approvals = v3.get("pending_approvals")
        if isinstance(approvals, Mapping):
            control["pending_approvals"] = {
                approval_id: {
                    key: field
                    for key in ("kind", "step_key", "action_id")
                    if isinstance((field := item.get(key)), str)
                }
                for approval_id, item in approvals.items()
                if isinstance(approval_id, str) and isinstance(item, Mapping)
            }
        result["v3"] = control
    return result


def _state_control(
    state: object,
) -> tuple[bool, int, str | None, float | None]:
    if not isinstance(state, Mapping):
        return False, 0, None, None
    v3 = state.get("v3")
    if not isinstance(v3, Mapping):
        return False, 0, None, None
    execution = v3.get("execution")
    if not isinstance(execution, Mapping):
        execution = {}
    generation = execution.get("generation", 0)
    owner_id = execution.get("owner_id")
    lease = execution.get("lease_expires")
    return (
        bool(v3.get("cancel_requested", False)),
        int(generation) if isinstance(generation, int) else 0,
        owner_id if isinstance(owner_id, str) else None,
        float(lease) if isinstance(lease, (int, float)) else None,
    )


def _projection_workflow_identity(workflow: object) -> tuple[str, str]:
    """Extract the small immutable workflow identity used by run listings."""

    if not isinstance(workflow, Mapping):
        raise WorkflowValidationError("run workflow identity is invalid")
    return (
        _validated_query_text(workflow.get("workflow_id"), "workflow_id"),
        _validated_query_text(workflow.get("version"), "workflow_version"),
    )


def _projection_pending_approval_count(state: object) -> int:
    """Count approval facts without decoding retained business values."""

    if not isinstance(state, Mapping):
        return 0
    v3 = state.get("v3")
    if isinstance(v3, Mapping):
        approvals = v3.get("pending_approvals")
        if isinstance(approvals, Mapping):
            return sum(
                isinstance(approval_id, str) and isinstance(item, Mapping)
                for approval_id, item in approvals.items()
            )
    pending = state.get("pending")
    if isinstance(pending, Mapping):
        kind = pending.get("kind")
        if isinstance(kind, str) and "APPROVAL" in kind.upper():
            return 1
    actions = state.get("actions")
    if not isinstance(actions, Mapping):
        return 0
    return sum(
        isinstance(action, Mapping) and action.get("status") == "PENDING_APPROVAL"
        for action in actions.values()
    )


def _projection_failure_code(state: object, status: object) -> str | None:
    if isinstance(state, Mapping):
        failure = state.get("failure")
        if isinstance(failure, Mapping):
            code = failure.get("code")
            if isinstance(code, str) and code and len(code.encode("utf-8")) <= 128:
                return code
    # Reconciliation is deliberately a visible unknown-outcome state rather
    # than a generic error.  This lets a compact operator list surface the
    # distinction without parsing action payloads.
    if status == "RECONCILING":
        return "EXTERNAL_RESULT_UNKNOWN"
    return None


def _run_summary_from_parts(
    *,
    run_id: object,
    execution_id: object,
    workflow: object,
    state: object,
    status: object,
    created_at: object,
    updated_at: object,
) -> RunSummary:
    workflow_id, workflow_version = _projection_workflow_identity(workflow)
    return RunSummary(
        _validated_query_text(run_id, "run_id", maximum=1_024),
        _validated_execution_id(execution_id),
        workflow_id,
        workflow_version,
        _validated_query_text(status, "status", maximum=128),
        _validated_query_timestamp(created_at),
        _validated_query_timestamp(updated_at),
        _projection_pending_approval_count(state),
        _projection_failure_code(state, status),
    )


def _run_summary_from_snapshot(
    snapshot: RunSnapshot,
    *,
    created_at: object,
    updated_at: object,
    state: object = None,
) -> RunSummary:
    return _run_summary_from_parts(
        run_id=snapshot.run_id,
        execution_id=snapshot.execution_id,
        workflow=snapshot._definition_identity(),
        state=snapshot.state if state is None else state,
        status=snapshot.status,
        created_at=created_at,
        updated_at=updated_at,
    )


def _matches_run_query(summary: RunSummary, filter: RunQueryFilter) -> bool:
    return (
        (not filter.statuses or summary.status in filter.statuses)
        and (filter.workflow_id is None or summary.workflow_id == filter.workflow_id)
        and (not filter.pending_only or summary.pending_approval_count > 0)
    )


def _after_run_cursor(summary: RunSummary, cursor: RunListCursor | None) -> bool:
    """Whether a descending-created-time item follows an opaque cursor."""

    if cursor is None:
        return True
    if summary.created_at != cursor.created_at:
        return summary.created_at < cursor.created_at
    return (summary.run_id, summary.execution_id) > (
        cursor.run_id,
        cursor.execution_id,
    )


def _run_page(
    summaries: list[RunSummary],
    *,
    filter: RunQueryFilter,
    cursor: RunListCursor | None,
    limit: int,
) -> RunPage:
    ordered = sorted(
        (
            item
            for item in summaries
            if _matches_run_query(item, filter) and _after_run_cursor(item, cursor)
        ),
        key=lambda item: (-item.created_at, item.run_id, item.execution_id),
    )
    page_items = tuple(ordered[:limit])
    next_cursor = page_items[-1].cursor if len(ordered) > limit else None
    return RunPage(page_items, next_cursor)


class SQLiteRunJournal:
    """A local SQLite journal with one transaction for state and event commits."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            _ensure_migration_ledger(connection)
            _preflight_journal_schema(connection)
            # V6's SQLite journal is single-host. WAL lets the frequent
            # execution-control reads coexist with a state/event writer;
            # SQLite remains the sole recovery authority. Preserve FULL
            # synchronous durability for dispatched action records.
            try:
                mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
            except sqlite3.Error as error:
                raise RunJournalError("local run journal requires WAL mode") from error
            if mode is None or str(mode[0]).lower() != "wal":
                raise RunJournalError("local run journal requires WAL mode")
            applied = _applied_migrations(connection)
            for version, name, migrate in _JOURNAL_MIGRATIONS:
                if version in applied:
                    continue
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    # Another opener may have upgraded the database since the
                    # first inspection. Never replay a completed migration.
                    current = _preflight_journal_schema(connection)
                    if current >= version:
                        connection.execute("COMMIT")
                        continue
                    if current != version - 1:
                        raise RunJournalError(
                            "local run journal migration order is invalid"
                        )
                    migrate(connection)
                    connection.execute(
                        "INSERT INTO lightweight_agent_journal_migrations"
                        "(version, name, applied_at) VALUES (?, ?, ?)",
                        (version, name, time.time()),
                    )
                    connection.execute(
                        "INSERT INTO lightweight_agent_journal_meta(key, value) "
                        "VALUES ('schema_version', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (str(version),),
                    )
                    _preflight_journal_schema(connection)
                    connection.execute("COMMIT")
                except sqlite3.Error as error:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    raise RunJournalError(
                        "could not upgrade local run journal"
                    ) from error
                except BaseException:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    raise
            if _preflight_journal_schema(connection) != JOURNAL_STORAGE_VERSION:
                raise RunJournalError("local run journal upgrade is incomplete")

    def create(
        self, snapshot: RunSnapshot, event_type: str, payload: Mapping[str, JsonValue]
    ) -> RunSnapshot:
        event = _event(snapshot.run_id, 1, event_type, payload, snapshot.execution_id)
        definition_digest, workflow_json, manifest_json = _snapshot_definition(snapshot)
        cell_layout = snapshot._definition_schema_version() == "v6-light-workflow-v4"
        if cell_layout:
            verified_state = snapshot._state_copy()
            compact, values = _compact_cell_snapshot(snapshot)
            state_cells = _state_cells(verified_state)
            state_for_projection = verified_state
        else:
            compact, values = _compact_snapshot(snapshot)
            state_cells = {}
            state_for_projection = compact["state"]
        compact_json = canonical_json(compact)
        control = _snapshot_control(snapshot, state=state_for_projection)
        projection = _run_summary_from_snapshot(
            snapshot,
            created_at=event.created_at,
            updated_at=event.created_at,
            state=state_for_projection,
        )
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT OR IGNORE INTO lightweight_agent_definitions"
                    "(definition_digest, workflow_json, execution_manifest_json) "
                    "VALUES (?, ?, ?)",
                    (definition_digest, workflow_json, manifest_json),
                )
                _insert_values(connection, values)
                connection.execute(
                    "INSERT INTO lightweight_agent_runs"
                    "(run_id, execution_id, revision, status, workflow_id, "
                    "workflow_version, created_at, updated_at, "
                    "pending_approval_count, failure_code, definition_digest, "
                    "cancel_requested, execution_generation, execution_owner_id, "
                    "execution_lease_expires, snapshot_json, state_layout) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        snapshot.run_id,
                        snapshot.execution_id,
                        snapshot.revision,
                        snapshot.status,
                        projection.workflow_id,
                        projection.workflow_version,
                        projection.created_at,
                        projection.updated_at,
                        projection.pending_approval_count,
                        projection.failure_code,
                        definition_digest,
                        int(control.cancel_requested),
                        control.execution_generation,
                        control.execution_owner_id,
                        control.execution_lease_expires,
                        compact_json,
                        (
                            _STATE_LAYOUT_CELLS
                            if cell_layout
                            else _STATE_LAYOUT_SNAPSHOT
                        ),
                    ),
                )
                if cell_layout:
                    _write_state_cells(
                        connection, snapshot.run_id, state_cells, replace_all=False
                    )
                connection.execute(
                    "INSERT INTO lightweight_agent_events(run_id, execution_id, sequence, type, created_at, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        event.run_id,
                        event.execution_id,
                        event.sequence,
                        event.type,
                        event.created_at,
                        canonical_json(event.payload),
                    ),
                )
                connection.execute("COMMIT")
        except sqlite3.IntegrityError as error:
            raise RunConflictError("run already exists") from error
        except sqlite3.Error as error:
            raise RunJournalError("could not commit local run journal") from error
        # SQLite has committed an independently serialized record above.  The
        # supplied snapshot is already detached by ``RunSnapshot`` and is not
        # the stored object, so a further round-trip adds cost without adding
        # an isolation guarantee.
        return snapshot

    def get(
        self, run_id: str, *, expected_execution_id: str | None = None
    ) -> RunSnapshot:
        expected = _expected_execution_id(expected_execution_id)
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN")
                row = connection.execute(
                    "SELECT r.execution_id, r.state_layout, r.snapshot_json, d.workflow_json, "
                    "d.execution_manifest_json "
                    "FROM lightweight_agent_runs AS r "
                    "LEFT JOIN lightweight_agent_definitions AS d "
                    "ON d.definition_digest=r.definition_digest "
                    "WHERE r.run_id = ?",
                    (run_id,),
                ).fetchone()
                if row is not None:
                    if row["state_layout"] not in {
                        _STATE_LAYOUT_SNAPSHOT,
                        _STATE_LAYOUT_CELLS,
                    }:
                        raise RunJournalError("run state layout is unsupported")
                    compact = json.loads(row["snapshot_json"])
                    # Pre-normalization rows carry an inline workflow.  They
                    # never used journal storage envelopes, and a user value
                    # in one of their ordinary JSON slots may legitimately
                    # look exactly like one.  Decode only the layout that
                    # this journal itself compacted.
                    compact_layout = "workflow" not in compact
                    digests = (
                        _compact_value_digests(compact) if compact_layout else set()
                    )
                    mapping = _hydrate_snapshot_row(row)
                    stored_values = _read_values(connection, digests)
                    if compact_layout:
                        mapping = _hydrate_compact_values(mapping, stored_values)
                    if row["state_layout"] == _STATE_LAYOUT_CELLS:
                        if not compact_layout:
                            raise RunJournalError(
                                "cell run definition layout is invalid"
                            )
                        mapping["state"] = _read_state_cells(connection, run_id)
                connection.execute("COMMIT")
        except sqlite3.Error as error:
            raise RunJournalError("could not read local run journal") from error
        if row is None:
            raise RunNotFoundError(run_id)
        snapshot = RunSnapshot.from_mapping(mapping)
        _require_execution_id(snapshot.execution_id, expected)
        # ``execution_id`` is projected as a column for atomic compare-and-set
        # operations.  A compact snapshot disagreeing with it indicates a
        # corrupt journal rather than a harmless old format.
        if snapshot.execution_id != _validated_execution_id(row["execution_id"]):
            raise RunJournalError("run execution identity is inconsistent")
        return snapshot

    def inspection(
        self, run_id: str, *, expected_execution_id: str | None = None
    ) -> RunInspection:
        """Read operator metadata without hydrating retained business values."""

        return self._read_view_source(
            run_id, expected_execution_id, with_output=False
        ).inspection

    def view_source(
        self, run_id: str, *, expected_execution_id: str | None = None
    ) -> RunViewSource:
        return self._read_view_source(run_id, expected_execution_id, with_output=True)

    def _read_view_source(
        self, run_id: str, expected_execution_id: str | None, *, with_output: bool
    ) -> RunViewSource:
        expected = _expected_execution_id(expected_execution_id)
        cell_state: dict[str, Any] | None = None
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN")
                row = connection.execute(
                    "SELECT r.execution_id, r.state_layout, r.snapshot_json, "
                    "d.workflow_json "
                    "FROM lightweight_agent_runs AS r "
                    "LEFT JOIN lightweight_agent_definitions AS d "
                    "ON d.definition_digest=r.definition_digest "
                    "WHERE r.run_id=?",
                    (run_id,),
                ).fetchone()
                output: JsonValue = None
                if row is not None:
                    _require_execution_id(
                        _validated_execution_id(row["execution_id"]), expected
                    )
                    if row["state_layout"] == _STATE_LAYOUT_CELLS:
                        cell_state = _read_inspection_cells(connection, run_id)
                    elif row["state_layout"] != _STATE_LAYOUT_SNAPSHOT:
                        raise RunJournalError("run state layout is unsupported")
                if row is not None and with_output:
                    compact = json.loads(row["snapshot_json"])
                    if cell_state is not None:
                        output = _read_cell_output(connection, run_id)
                    else:
                        output = _final_output(compact.get("state"))
                    if cell_state is None and "workflow" not in compact:
                        # Read only this revision's final output, never all retained
                        # tool results. The transaction also protects against GC.
                        digests = set()
                        if (
                            isinstance(output, dict)
                            and output.get(_VALUE_STORAGE_KEY) == "ref"
                        ):
                            digests.add(str(output.get("digest")))
                        output = _decode_stored_value(
                            output, _read_values(connection, digests)
                        )
                connection.execute("COMMIT")
        except sqlite3.Error as error:
            raise RunJournalError("could not inspect local run") from error
        except (TypeError, AttributeError, json.JSONDecodeError) as error:
            raise RunJournalError("run inspection record is invalid") from error
        if row is None:
            raise RunNotFoundError(run_id)
        try:
            compact = json.loads(row["snapshot_json"])
            if cell_state is not None:
                compact["state"] = cell_state
            execution_id = _validated_execution_id(row["execution_id"])
            _require_execution_id(execution_id, expected)
            workflow = compact.get("workflow")
            if not isinstance(workflow, Mapping):
                if row["workflow_json"] is None:
                    raise RunJournalError("run definition record is missing")
                workflow = json.loads(row["workflow_json"])
            inspection = RunInspection(
                run_id=str(compact["run_id"]),
                workflow=json_copy(workflow),
                state=_inspection_state(compact.get("state")),
                status=str(compact["status"]),
                driver_bindings={
                    str(key): str(value)
                    for key, value in _inspection_bindings(
                        compact.get("driver_bindings")
                    ).items()
                },
                execution_id=execution_id,
                revision=int(compact["revision"]),
            )
            if compact.get("execution_id", execution_id) != execution_id:
                raise RunJournalError("run execution identity is inconsistent")
            return RunViewSource(inspection, output)
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise RunJournalError("run inspection record is invalid") from error

    def replace(
        self,
        snapshot: RunSnapshot,
        *,
        expected_revision: int,
        event_type: str,
        payload: Mapping[str, JsonValue],
        expected_execution_id: str | None = None,
    ) -> RunSnapshot:
        expected = _expected_execution_id(expected_execution_id)
        if expected is not None and expected != snapshot.execution_id:
            raise RunExecutionMismatchError()
        if snapshot.revision != expected_revision + 1:
            raise RunJournalError("journal transitions must advance one revision")
        definition_digest, workflow_json, manifest_json = _snapshot_definition(snapshot)
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                current = connection.execute(
                    "SELECT execution_id, definition_digest, state_layout, "
                    "snapshot_json, created_at FROM "
                    "lightweight_agent_runs WHERE run_id=?",
                    (snapshot.run_id,),
                ).fetchone()
                if current is None:
                    connection.execute("ROLLBACK")
                    raise RunNotFoundError(snapshot.run_id)
                current_execution_id = _validated_execution_id(current["execution_id"])
                if expected is not None and current_execution_id != expected:
                    connection.execute("ROLLBACK")
                    raise RunExecutionMismatchError()
                if current_execution_id != snapshot.execution_id:
                    connection.execute("ROLLBACK")
                    raise RunExecutionMismatchError()
                current_digest = current["definition_digest"]
                if current_digest is None:
                    current_mapping = json.loads(current["snapshot_json"])
                    current_definition = _mapping_definition(current_mapping)
                    if current_definition[0] != definition_digest:
                        connection.execute("ROLLBACK")
                        raise RunConflictError(
                            "immutable run definition changed before transition"
                        )
                elif current_digest != definition_digest:
                    connection.execute("ROLLBACK")
                    raise RunConflictError(
                        "immutable run definition changed before transition"
                    )
                layout = current["state_layout"]
                if layout == _STATE_LAYOUT_CELLS:
                    verified_state = snapshot._state_copy()
                    compact, values = _compact_cell_snapshot(snapshot)
                    cell_updates = (
                        _cell_updates_from_writes(
                            verified_state, snapshot._state_write_set
                        )
                        if snapshot._state_write_set is not None
                        else None
                    )
                    rewrite_cells = cell_updates is None
                    if cell_updates is None:
                        cell_updates = _state_cells(verified_state)
                    state_for_projection = verified_state
                elif layout == _STATE_LAYOUT_SNAPSHOT:
                    compact, values = _compact_snapshot(snapshot)
                    cell_updates = None
                    rewrite_cells = False
                    state_for_projection = compact["state"]
                else:
                    raise RunJournalError("run state layout is unsupported")
                compact_json = canonical_json(compact)
                control = _snapshot_control(snapshot, state=state_for_projection)
                projection = _run_summary_from_snapshot(
                    snapshot,
                    created_at=current["created_at"],
                    updated_at=time.time(),
                    state=state_for_projection,
                )
                connection.execute(
                    "INSERT OR IGNORE INTO lightweight_agent_definitions"
                    "(definition_digest, workflow_json, execution_manifest_json) "
                    "VALUES (?, ?, ?)",
                    (definition_digest, workflow_json, manifest_json),
                )
                _insert_values(connection, values)
                self._commit_replacement_row(
                    connection,
                    run_id=snapshot.run_id,
                    execution_id=snapshot.execution_id,
                    expected_revision=expected_revision,
                    revision=snapshot.revision,
                    status=snapshot.status,
                    definition_digest=definition_digest,
                    compact_json=compact_json,
                    control=control,
                    projection=projection,
                    cell_updates=cell_updates,
                    rewrite_cells=rewrite_cells,
                    event_type=event_type,
                    payload=payload,
                )
        except (RunNotFoundError, RunConflictError):
            raise
        except sqlite3.Error as error:
            raise RunJournalError("could not commit local run journal") from error
        # The durable row is independent of this caller-owned instance; see
        # ``create`` for why returning it is safe.
        return snapshot

    def _replace_cell_writes(
        self,
        head: _CellCommitHead,
        write_set: StateWriteSet,
        *,
        status: str,
        event_type: str,
        payload: Mapping[str, JsonValue],
    ) -> _CellCommitHead:
        """Commit a trusted v4 cell delta without constructing a full snapshot.

        This private fast path is only for a coordinator holding the validated
        cells of ``head.revision``. The SQLite CAS still fences another owner,
        and the public ``get()`` remains the full validating recovery read.
        """

        from .persistent_state import PersistentCellState

        if not isinstance(head, _CellCommitHead) or not isinstance(
            head.cells, PersistentCellState
        ):
            raise _CellDeltaUnavailableError("local cell commit basis is invalid")
        if not isinstance(write_set, StateWriteSet) or not write_set._from_editor:
            raise _CellDeltaUnavailableError("local cell writes are not trusted")
        projected = head.cells.apply_write_set(write_set)
        if projected is None:
            raise _CellDeltaUnavailableError("local cell write needs full state")
        affected: set[str] = set()
        for write in write_set.writes:
            address = _cell_address_for_write(write.path)
            if address is None:  # pragma: no cover - checked by projection
                raise _CellDeltaUnavailableError("local cell write needs full state")
            affected.add(_cell_path(*address))
        for addition in write_set.member_additions:
            affected.add(_cell_path(*addition.path, addition.member))
        updates = {
            key: projected._cells.get(key)
            for key in affected
            if head.cells._cells.get(key) != projected._cells.get(key)
        }
        next_head = head.next_revision(status, projected, write_set)
        state_view = projected.view()
        cancel, generation, owner_id, lease = _state_control(state_view)
        control = RunControl(
            head.run_id,
            status,
            next_head.revision,
            cancel,
            generation,
            owner_id,
            lease,
            head.execution_id,
        )
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                current = connection.execute(
                    "SELECT execution_id, revision, status, workflow_id, "
                    "workflow_version, definition_digest, state_layout, "
                    "snapshot_json, created_at FROM lightweight_agent_runs "
                    "WHERE run_id=?",
                    (head.run_id,),
                ).fetchone()
                if current is None:
                    connection.execute("ROLLBACK")
                    raise RunNotFoundError(head.run_id)
                if (
                    _validated_execution_id(current["execution_id"])
                    != head.execution_id
                ):
                    connection.execute("ROLLBACK")
                    raise RunExecutionMismatchError()
                if current["definition_digest"] != head.definition_digest:
                    connection.execute("ROLLBACK")
                    raise RunConflictError(
                        "immutable run definition changed before transition"
                    )
                if (
                    current["workflow_id"] != head.workflow_id
                    or current["workflow_version"] != head.workflow_version
                ):
                    connection.execute("ROLLBACK")
                    raise RunConflictError("run workflow identity changed")
                if current["state_layout"] != _STATE_LAYOUT_CELLS:
                    connection.execute("ROLLBACK")
                    raise _CellDeltaUnavailableError("run has legacy state layout")
                compact = json.loads(current["snapshot_json"])
                if (
                    not isinstance(compact, dict)
                    or compact.get("run_id") != head.run_id
                    or compact.get("execution_id") != head.execution_id
                    or compact.get("revision") != current["revision"]
                    or compact.get("status") != current["status"]
                    or compact.get("state") != {}
                ):
                    raise RunJournalError("local cell run header is invalid")
                compact["revision"] = next_head.revision
                compact["status"] = status
                projection = _run_summary_from_parts(
                    run_id=head.run_id,
                    execution_id=head.execution_id,
                    workflow={
                        "workflow_id": head.workflow_id,
                        "version": head.workflow_version,
                    },
                    state=state_view,
                    status=status,
                    created_at=current["created_at"],
                    updated_at=time.time(),
                )
                self._commit_replacement_row(
                    connection,
                    run_id=head.run_id,
                    execution_id=head.execution_id,
                    expected_revision=head.revision,
                    revision=next_head.revision,
                    status=status,
                    definition_digest=head.definition_digest,
                    compact_json=canonical_json(compact),
                    control=control,
                    projection=projection,
                    cell_updates=updates,
                    rewrite_cells=False,
                    event_type=event_type,
                    payload=payload,
                )
        except (RunNotFoundError, RunConflictError, _CellDeltaUnavailableError):
            raise
        except sqlite3.Error as error:
            raise RunJournalError("could not commit local run journal") from error
        return next_head

    @staticmethod
    def _commit_replacement_row(
        connection: sqlite3.Connection,
        *,
        run_id: str,
        execution_id: str,
        expected_revision: int,
        revision: int,
        status: str,
        definition_digest: str,
        compact_json: str,
        control: RunControl,
        projection: RunSummary,
        cell_updates: Mapping[str, str | None] | None,
        rewrite_cells: bool,
        event_type: str,
        payload: Mapping[str, JsonValue],
    ) -> None:
        """Commit one revision, local cells, and event in the same transaction."""

        result = connection.execute(
            "UPDATE lightweight_agent_runs SET execution_id=?, revision=?, status=?, "
            "workflow_id=?, workflow_version=?, updated_at=?, "
            "pending_approval_count=?, failure_code=?, definition_digest=?, "
            "cancel_requested=?, "
            "execution_generation=?, execution_owner_id=?, "
            "execution_lease_expires=?, snapshot_json=? "
            "WHERE run_id=? AND revision=? AND execution_id=?",
            (
                execution_id,
                revision,
                status,
                projection.workflow_id,
                projection.workflow_version,
                projection.updated_at,
                projection.pending_approval_count,
                projection.failure_code,
                definition_digest,
                int(control.cancel_requested),
                control.execution_generation,
                control.execution_owner_id,
                control.execution_lease_expires,
                compact_json,
                run_id,
                expected_revision,
                execution_id,
            ),
        )
        if result.rowcount != 1:
            connection.execute("ROLLBACK")
            replacement = connection.execute(
                "SELECT execution_id FROM lightweight_agent_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if replacement is None:
                raise RunNotFoundError(run_id)
            if _validated_execution_id(replacement["execution_id"]) != execution_id:
                raise RunExecutionMismatchError()
            raise RunConflictError("run changed before this transition")
        if cell_updates is not None:
            _write_state_cells(
                connection, run_id, cell_updates, replace_all=rewrite_cells
            )
        sequence = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM lightweight_agent_events WHERE run_id=?",
            (run_id,),
        ).fetchone()[0]
        event = _event(run_id, sequence, event_type, payload, execution_id)
        connection.execute(
            "INSERT INTO lightweight_agent_events(run_id, execution_id, sequence, type, created_at, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
            (
                event.run_id,
                event.execution_id,
                event.sequence,
                event.type,
                event.created_at,
                canonical_json(event.payload),
            ),
        )
        connection.execute("COMMIT")

    def status(
        self, run_id: str, *, expected_execution_id: str | None = None
    ) -> RunStatus:
        expected = _expected_execution_id(expected_execution_id)
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT execution_id, revision, status, archived_at, "
                    "(SELECT COALESCE(MAX(sequence), 0) FROM lightweight_agent_events "
                    "WHERE run_id=?) AS last_sequence "
                    "FROM lightweight_agent_runs WHERE run_id=?",
                    (run_id, run_id),
                ).fetchone()
        except sqlite3.Error as error:
            raise RunJournalError("could not read local run status") from error
        if row is None:
            raise RunNotFoundError(run_id)
        execution_id = _validated_execution_id(row["execution_id"])
        _require_execution_id(execution_id, expected)
        return RunStatus(
            run_id,
            str(row["status"]),
            int(row["revision"]),
            int(row["last_sequence"]),
            (float(row["archived_at"]) if row["archived_at"] is not None else None),
            execution_id,
        )

    def control(
        self, run_id: str, *, expected_execution_id: str | None = None
    ) -> RunControl:
        expected = _expected_execution_id(expected_execution_id)
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT execution_id, revision, status, cancel_requested, "
                    "execution_generation, execution_owner_id, "
                    "execution_lease_expires FROM lightweight_agent_runs "
                    "WHERE run_id=?",
                    (run_id,),
                ).fetchone()
        except sqlite3.Error as error:
            code = getattr(error, "sqlite_errorcode", None)
            if isinstance(code, int) and code & 0xFF in {
                sqlite3.SQLITE_BUSY,
                sqlite3.SQLITE_LOCKED,
            }:
                raise RunJournalBusyError(
                    "local run control is temporarily busy"
                ) from error
            raise RunJournalError("could not read local run control") from error
        if row is None:
            raise RunNotFoundError(run_id)
        execution_id = _validated_execution_id(row["execution_id"])
        _require_execution_id(execution_id, expected)
        lease = row["execution_lease_expires"]
        return RunControl(
            run_id,
            str(row["status"]),
            int(row["revision"]),
            bool(row["cancel_requested"]),
            int(row["execution_generation"]),
            row["execution_owner_id"],
            float(lease) if lease is not None else None,
            execution_id,
        )

    def recoverable_runs(
        self,
        *,
        limit: int = 256,
        now: float | None = None,
        after: tuple[int, str] | None = None,
    ) -> tuple[RunControl, ...]:
        """List bounded, lease-free work that a host may attempt to claim.

        The query intentionally exposes only a compact control projection.
        Hydration and schema/driver validation remain in ``WorkflowEngine`` so
        a journal never becomes an execution authority.
        """

        page_size = _event_limit(limit)
        moment = _recovery_moment(now)
        cursor = _recovery_cursor(after)
        placeholders = ",".join("?" for _ in _RECOVERABLE_RUN_STATUSES)
        cursor_clause = ""
        cursor_values: tuple[object, ...] = ()
        if cursor is not None:
            cursor_clause = "AND (revision > ? OR (revision = ? AND run_id > ?)) "
            cursor_values = (cursor[0], cursor[0], cursor[1])
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT run_id, execution_id, status, revision, cancel_requested, "
                    "execution_generation, execution_owner_id, "
                    "execution_lease_expires FROM lightweight_agent_runs "
                    f"WHERE archived_at IS NULL AND status IN ({placeholders}) "
                    "AND (execution_owner_id IS NULL "
                    "OR execution_lease_expires IS NULL "
                    "OR execution_lease_expires <= ?) "
                    f"{cursor_clause}"
                    "ORDER BY revision ASC, run_id ASC LIMIT ?",
                    (
                        *sorted(_RECOVERABLE_RUN_STATUSES),
                        moment,
                        *cursor_values,
                        page_size,
                    ),
                ).fetchall()
        except sqlite3.Error as error:
            raise RunJournalError("could not scan recoverable local runs") from error
        return tuple(
            RunControl(
                str(row["run_id"]),
                str(row["status"]),
                int(row["revision"]),
                bool(row["cancel_requested"]),
                int(row["execution_generation"]),
                row["execution_owner_id"],
                (
                    float(row["execution_lease_expires"])
                    if row["execution_lease_expires"] is not None
                    else None
                ),
                _validated_execution_id(row["execution_id"]),
            )
            for row in rows
        )

    def list_runs(
        self,
        filter: RunQueryFilter | None = None,
        cursor: RunListCursor | None = None,
        limit: int = _RUN_QUERY_DEFAULT_LIMIT,
    ) -> RunPage:
        """Read a bounded projection without hydrating run business state."""

        query_filter = _run_query_filter(filter)
        page_size = _run_query_limit(limit)
        query_cursor = _run_query_cursor(cursor)
        clauses: list[str] = []
        values: list[object] = []
        if not query_filter.include_archived:
            clauses.append("archived_at IS NULL")
        if query_filter.statuses:
            placeholders = ",".join("?" for _ in query_filter.statuses)
            clauses.append(f"status IN ({placeholders})")
            values.extend(query_filter.statuses)
        if query_filter.workflow_id is not None:
            clauses.append("workflow_id=?")
            values.append(query_filter.workflow_id)
        if query_filter.pending_only:
            clauses.append("pending_approval_count>0")
        if query_cursor is not None:
            clauses.append(
                "(created_at < ? OR (created_at = ? AND "
                "(run_id > ? OR (run_id = ? AND execution_id > ?))))"
            )
            values.extend(
                (
                    query_cursor.created_at,
                    query_cursor.created_at,
                    query_cursor.run_id,
                    query_cursor.run_id,
                    query_cursor.execution_id,
                )
            )
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT run_id, execution_id, workflow_id, workflow_version, "
                    "status, created_at, updated_at, pending_approval_count, "
                    "failure_code FROM lightweight_agent_runs"
                    + where
                    + " ORDER BY created_at DESC, run_id ASC, execution_id ASC LIMIT ?",
                    (*values, page_size + 1),
                ).fetchall()
        except sqlite3.Error as error:
            raise RunJournalError("could not query local run journal") from error
        try:
            summaries = tuple(
                RunSummary(
                    row["run_id"],
                    row["execution_id"],
                    row["workflow_id"],
                    row["workflow_version"],
                    row["status"],
                    row["created_at"],
                    row["updated_at"],
                    row["pending_approval_count"],
                    row["failure_code"],
                )
                for row in rows[:page_size]
            )
        except WorkflowValidationError as error:
            raise RunJournalError("local run query projection is invalid") from error
        next_cursor = summaries[-1].cursor if len(rows) > page_size else None
        return RunPage(summaries, next_cursor)

    def events(
        self,
        run_id: str,
        *,
        after: int = 0,
        limit: int = 256,
        expected_execution_id: str | None = None,
    ) -> tuple[RunEvent, ...]:
        page_size = _event_limit(limit)
        cursor = _event_after(after)
        expected = _expected_execution_id(expected_execution_id)
        try:
            with closing(self._connect()) as connection:
                # The identity projection and event page must come from the
                # same SQLite read snapshot.  Otherwise a purge/reuse between
                # two autocommit SELECTs could turn an old cursor into an
                # indistinguishable empty page.
                connection.execute("BEGIN")
                exists = connection.execute(
                    "SELECT execution_id FROM lightweight_agent_runs WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if exists is None:
                    raise RunNotFoundError(run_id)
                execution_id = _validated_execution_id(exists["execution_id"])
                _require_execution_id(execution_id, expected)
                rows = connection.execute(
                    "SELECT execution_id, sequence, type, created_at, payload_json "
                    "FROM lightweight_agent_events "
                    "WHERE run_id=? AND execution_id=? AND sequence>? "
                    "ORDER BY sequence LIMIT ?",
                    (run_id, execution_id, cursor, page_size),
                ).fetchall()
                connection.execute("COMMIT")
        except sqlite3.Error as error:
            raise RunJournalError("could not read local run events") from error
        return tuple(
            RunEvent(
                run_id,
                int(row["sequence"]),
                row["type"],
                json.loads(row["payload_json"]),
                float(row["created_at"]),
                _validated_execution_id(row["execution_id"]),
            )
            for row in rows
        )

    def storage_info(self) -> JournalStorageInfo:
        """Return the durable format and completed migration ledger."""

        try:
            with closing(self._connect()) as connection:
                schema_version = _preflight_journal_schema(connection)
                rows = connection.execute(
                    "SELECT version FROM lightweight_agent_journal_migrations "
                    "ORDER BY version"
                ).fetchall()
        except sqlite3.Error as error:
            raise RunJournalError("could not inspect local run journal") from error
        versions = tuple(int(row["version"]) for row in rows)
        if schema_version != JOURNAL_STORAGE_VERSION:
            raise RunJournalError("local run journal upgrade is incomplete")
        return JournalStorageInfo(JOURNAL_STORAGE_FORMAT_V1, schema_version, versions)

    def space_info(self) -> JournalSpaceInfo:
        """Report file size and reusable pages without changing the database."""

        try:
            with closing(self._connect()) as connection:
                page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
                page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
                freelist = int(
                    connection.execute("PRAGMA freelist_count").fetchone()[0]
                )
        except sqlite3.Error as error:
            raise RunJournalError("could not inspect local journal space") from error
        try:
            database_size = self.path.stat().st_size if self.path.exists() else 0
            wal_path = Path(str(self.path) + "-wal")
            wal_size = wal_path.stat().st_size if wal_path.exists() else 0
        except OSError as error:
            raise RunJournalError("could not inspect local journal files") from error
        reusable = page_size * freelist
        live_estimate = max(0, page_size * (page_count - freelist))
        return JournalSpaceInfo(
            database_size,
            wal_size,
            page_size,
            page_count,
            freelist,
            reusable,
            live_estimate,
            live_estimate,
        )

    def vacuum(self) -> JournalVacuumReport:
        """Explicitly rebuild an idle journal; retention never calls this method."""

        before = self.space_info()
        try:
            with closing(self._connect()) as connection:
                active = connection.execute(
                    "SELECT 1 FROM lightweight_agent_runs "
                    "WHERE status IS NULL OR status NOT IN (?, ?, ?) LIMIT 1",
                    tuple(sorted(_TERMINAL_RUN_STATUSES)),
                ).fetchone()
                if active is not None:
                    raise RunConflictError(
                        "journal VACUUM requires every retained run to be terminal"
                    )
                required = before.database_size_bytes * 2
                if shutil.disk_usage(self.path.parent).free < required:
                    raise RunJournalError("insufficient free space for journal VACUUM")
                connection.execute("VACUUM")
        except (RunConflictError, RunJournalError):
            raise
        except (OSError, sqlite3.Error) as error:
            raise RunJournalError("could not VACUUM local run journal") from error
        return JournalVacuumReport(before, self.space_info())

    def backup_to(self, destination: Path | str, *, overwrite: bool = False) -> Path:
        """Create a verified SQLite online-backup snapshot at ``destination``.

        The target is written to a sibling temporary database and atomically
        replaced only after SQLite's backup API and an integrity check succeed.
        No journal rows are modified.
        """

        source = self.path.resolve()
        target = _backup_target(destination, source, overwrite=overwrite)
        try:
            _copy_sqlite_database(source, target)
            _seal_sqlite_file(target)
            _validate_sqlite_database(target)
        except sqlite3.Error as error:
            _remove_backup_temp(target)
            raise RunJournalError(
                "could not create local run journal backup"
            ) from error
        except BaseException:
            _remove_backup_temp(target)
            raise
        final = Path(destination).resolve()
        try:
            os.replace(target, final)
        except OSError as error:
            _remove_backup_temp(target)
            raise RunJournalError(
                "could not finalize local run journal backup"
            ) from error
        return final

    def retained_run_ids(self) -> tuple[str, ...]:
        """Return every live or archived run retained by this journal.

        Local recovery packaging uses this narrow projection to find file
        identities referenced by snapshots without exposing the SQLite
        connection or teaching the file layer about journal tables.
        """

        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT run_id FROM lightweight_agent_runs ORDER BY run_id"
                ).fetchall()
        except sqlite3.Error as error:
            raise RunJournalError("could not list retained local runs") from error
        return tuple(str(row["run_id"]) for row in rows)

    @classmethod
    def restore_from_backup(
        cls,
        source: Path | str,
        destination: Path | str,
        *,
        overwrite: bool = False,
    ) -> SQLiteRunJournal:
        """Restore a verified SQLite backup behind an explicit recovery hold.

        A restored older journal is upgraded by the same transactional ledger
        as a normal open.  Every nonterminal run is moved to
        :data:`BACKUP_RECOVERY_STATUS` before the restored file is made
        visible.  An operator must explicitly confirm that the old runtime
        owner is stopped, then choose result-query or idempotent redispatch
        recovery.  The original backup is never changed.
        """

        requested_backup = Path(source)
        if requested_backup.is_symlink():
            raise RunJournalError("journal backup source must be a regular file")
        backup = requested_backup.resolve()
        if not backup.is_file():
            raise RunJournalError("journal backup source must be a regular file")
        _validate_sqlite_database(backup)
        target = _backup_target(destination, backup, overwrite=overwrite)
        try:
            _copy_sqlite_database(backup, target)
            _validate_sqlite_database(target)
            restored = cls(target)
            restored._hold_restored_runs()
            _seal_sqlite_file(target)
            _validate_sqlite_database(target)
        except sqlite3.Error as error:
            _remove_backup_temp(target)
            raise RunJournalError(
                "could not restore local run journal backup"
            ) from error
        except BaseException:
            _remove_backup_temp(target)
            raise
        final = Path(destination).resolve()
        try:
            os.replace(target, final)
        except OSError as error:
            _remove_backup_temp(target)
            raise RunJournalError(
                "could not finalize local run journal restore"
            ) from error
        return cls(final)

    def _hold_restored_runs(self) -> tuple[str, ...]:
        """Put copied live runs behind a durable, non-executing recovery gate.

        This method is intentionally private: the only supported way to place
        a run on this gate is restoring a SQLite backup.  It runs while the
        restored database is still a temporary sibling file, so a failure can
        never publish a partly prepared recovery database.
        """

        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT run_id FROM lightweight_agent_runs "
                    "WHERE status NOT IN (?, ?, ?) ORDER BY run_id",
                    tuple(sorted(_TERMINAL_RUN_STATUSES)),
                ).fetchall()
        except sqlite3.Error as error:
            raise RunJournalError(
                "could not inspect restored local run journal"
            ) from error

        held: list[str] = []
        restored_at = time.time()
        for row in rows:
            run_id = str(row["run_id"])
            snapshot = self.get(run_id)
            state = json_copy(snapshot.state)
            if not isinstance(
                state, dict
            ):  # defensive: RunSnapshot already checks this
                raise RunJournalError("restored run state is malformed")
            existing = state.get("backup_recovery")
            original_status = (
                existing.get("original_status")
                if isinstance(existing, Mapping)
                and isinstance(existing.get("original_status"), str)
                and existing.get("original_status") != BACKUP_RECOVERY_STATUS
                else snapshot.status
            )
            if original_status == BACKUP_RECOVERY_STATUS:
                raise RunJournalError("restored run recovery origin is invalid")
            state["backup_recovery"] = {
                "protocol": BACKUP_RECOVERY_PROTOCOL_V1,
                "original_status": original_status,
                "restored_at": restored_at,
                "source_owner_stopped": False,
            }
            # A copied lease says something only about the old database.  It
            # cannot grant the restored copy execution authority, even after
            # the explicit gate is later released.
            v3 = state.get("v3")
            if isinstance(v3, dict):
                execution = v3.get("execution")
                if isinstance(execution, dict):
                    execution["owner_id"] = None
                    execution["lease_expires"] = None
            self.replace(
                snapshot.next_revision(state=state, status=BACKUP_RECOVERY_STATUS),
                expected_revision=snapshot.revision,
                event_type="BACKUP_RECOVERY_HOLD",
                payload={"original_status": original_status},
            )
            held.append(run_id)
        return tuple(held)

    def recovery_holds(self, *, limit: int = 256) -> tuple[RunStatus, ...]:
        """List restored runs still blocked from dispatching new work."""

        page_size = _event_limit(limit)
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT r.run_id, r.execution_id, r.status, r.revision, r.archived_at, "
                    "(SELECT COALESCE(MAX(sequence), 0) "
                    "FROM lightweight_agent_events WHERE run_id=r.run_id) "
                    "AS last_sequence FROM lightweight_agent_runs AS r "
                    "WHERE r.status=? AND r.archived_at IS NULL "
                    "ORDER BY r.run_id LIMIT ?",
                    (BACKUP_RECOVERY_STATUS, page_size),
                ).fetchall()
        except sqlite3.Error as error:
            raise RunJournalError("could not list backup recovery holds") from error
        return tuple(
            RunStatus(
                str(row["run_id"]),
                str(row["status"]),
                int(row["revision"]),
                int(row["last_sequence"]),
                float(row["archived_at"]) if row["archived_at"] is not None else None,
                _validated_execution_id(row["execution_id"]),
            )
            for row in rows
        )

    def release_backup_recovery(
        self,
        run_id: str,
        *,
        source_owner_stopped: bool,
        strategy: str = "query",
        expected_execution_id: str | None = None,
    ) -> RunSnapshot:
        """Release a restored run only after an explicit ownership handoff.

        ``query`` is the safe default: every action that was dispatchable in
        the copied snapshot becomes ``RECONCILING`` and must be observed before
        the workflow can dispatch it again.  A copied ``READY`` action is not
        proof that the original owner did not dispatch it after the backup was
        taken.  ``idempotent-redispatch`` is intentionally narrow: it is
        accepted only for a previously running run when every such action
        declares a stable downstream idempotency key.  Both routes retain the
        original action IDs.
        """

        if source_owner_stopped is not True:
            raise RunJournalError("BACKUP_SOURCE_OWNER_STOP_REQUIRED")
        if strategy not in {"query", "idempotent-redispatch"}:
            raise WorkflowValidationError(
                "backup recovery strategy must be query or idempotent-redispatch"
            )
        snapshot = self.get(run_id, expected_execution_id=expected_execution_id)
        if snapshot.status != BACKUP_RECOVERY_STATUS:
            raise RunJournalError("run is not waiting for backup recovery")
        state = json_copy(snapshot.state)
        if not isinstance(state, dict):
            raise RunJournalError("backup recovery state is malformed")
        recovery = state.get("backup_recovery")
        if (
            not isinstance(recovery, Mapping)
            or recovery.get("protocol") != BACKUP_RECOVERY_PROTOCOL_V1
            or not isinstance(recovery.get("original_status"), str)
        ):
            raise RunJournalError("backup recovery metadata is malformed")
        original_status = str(recovery["original_status"])
        if (
            original_status in _TERMINAL_RUN_STATUSES
            or original_status == BACKUP_RECOVERY_STATUS
        ):
            raise RunJournalError("backup recovery original status is invalid")
        actions = state.get("actions")
        if not isinstance(actions, dict):
            raise RunJournalError("backup recovery actions are malformed")
        dispatched = [
            (action_id, action)
            for action_id, action in actions.items()
            if isinstance(action_id, str)
            and isinstance(action, dict)
            and action.get("status") == "DISPATCHED"
        ]
        ready = [
            (action_id, action)
            for action_id, action in actions.items()
            if isinstance(action_id, str)
            and isinstance(action, dict)
            and action.get("status") == "READY"
        ]
        replayable = [*dispatched, *ready]
        reconciling = [
            action_id
            for action_id, action in actions.items()
            if isinstance(action_id, str)
            and isinstance(action, dict)
            and action.get("status") == "RECONCILING"
        ]
        if strategy == "idempotent-redispatch":
            if original_status != "RUNNING" or reconciling:
                raise RunJournalError("BACKUP_IDEMPOTENT_REDISPATCH_NOT_AVAILABLE")
            unsupported = [
                action_id
                for action_id, action in replayable
                if not isinstance(action.get("capabilities"), Mapping)
                or action["capabilities"].get("accepts_idempotency_key") is not True
            ]
            if unsupported:
                raise RunJournalError("BACKUP_IDEMPOTENCY_NOT_DECLARED")
            for _action_id, action in dispatched:
                action["status"] = "READY"
        else:
            for _action_id, action in replayable:
                action["status"] = "RECONCILING"
            reconciling.extend(action_id for action_id, _action in replayable)

        next_status = "RECONCILING" if reconciling else original_status
        mutable_recovery = dict(recovery)
        mutable_recovery.update(
            {
                "source_owner_stopped": True,
                "released_at": time.time(),
                "strategy": strategy,
            }
        )
        state["backup_recovery"] = mutable_recovery
        v3 = state.get("v3")
        if isinstance(v3, dict):
            execution = v3.get("execution")
            if isinstance(execution, dict):
                execution["owner_id"] = None
                execution["lease_expires"] = None
        return self.replace(
            snapshot.next_revision(state=state, status=next_status),
            expected_revision=snapshot.revision,
            event_type="BACKUP_RECOVERY_RELEASED",
            payload={
                "strategy": strategy,
                "reconciling_action_ids": sorted(reconciling),
                "redispatch_action_ids": (
                    sorted(action_id for action_id, _action in replayable)
                    if strategy == "idempotent-redispatch"
                    else []
                ),
            },
        )

    def archived_runs(self, *, limit: int = 256) -> tuple[RunStatus, ...]:
        """List locally archived terminal runs without hydrating snapshots."""

        page_size = _event_limit(limit)
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT r.run_id, r.execution_id, r.status, r.revision, r.archived_at, "
                    "(SELECT COALESCE(MAX(sequence), 0) "
                    "FROM lightweight_agent_events WHERE run_id=r.run_id) "
                    "AS last_sequence FROM lightweight_agent_runs AS r "
                    "WHERE r.archived_at IS NOT NULL "
                    "ORDER BY r.archived_at DESC, r.run_id ASC LIMIT ?",
                    (page_size,),
                ).fetchall()
        except sqlite3.Error as error:
            raise RunJournalError("could not list archived local runs") from error
        return tuple(
            RunStatus(
                str(row["run_id"]),
                str(row["status"]),
                int(row["revision"]),
                int(row["last_sequence"]),
                float(row["archived_at"]),
                _validated_execution_id(row["execution_id"]),
            )
            for row in rows
        )

    def apply_retention(
        self,
        policy: RunRetentionPolicy,
        *,
        now: float | None = None,
    ) -> RunRetentionReport:
        """Archive/purge only terminal runs under an explicit policy.

        The maintenance transaction never touches a live run.  After a purge,
        unreferenced immutable definitions and large JSON values are removed;
        values referenced by any retained live or archived snapshot remain.
        """

        if not isinstance(policy, RunRetentionPolicy):
            raise WorkflowValidationError("retention requires a RunRetentionPolicy")
        moment = time.time() if now is None else now
        if (
            not isinstance(moment, (int, float))
            or isinstance(moment, bool)
            or moment < 0
        ):
            raise WorkflowValidationError("retention now must be a timestamp")
        archived: list[str] = []
        purged: list[str] = []
        removed_definitions = 0
        removed_values = 0
        try:
            with closing(self._connect()) as connection:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    candidates = connection.execute(
                        "SELECT r.run_id, "
                        "COALESCE((SELECT MAX(created_at) "
                        "FROM lightweight_agent_events WHERE run_id=r.run_id), 0) "
                        "AS terminal_at FROM lightweight_agent_runs AS r "
                        "WHERE r.archived_at IS NULL AND r.status IN (?, ?, ?)",
                        tuple(sorted(_TERMINAL_RUN_STATUSES)),
                    ).fetchall()
                    archive_cutoff = float(moment) - policy.archive_after_seconds
                    for row in candidates:
                        if float(row["terminal_at"]) <= archive_cutoff:
                            run_id = str(row["run_id"])
                            updated = connection.execute(
                                "UPDATE lightweight_agent_runs SET archived_at=? "
                                "WHERE run_id=? AND archived_at IS NULL "
                                "AND status IN (?, ?, ?)",
                                (moment, run_id, *sorted(_TERMINAL_RUN_STATUSES)),
                            )
                            if updated.rowcount == 1:
                                archived.append(run_id)

                    rows = connection.execute(
                        "SELECT run_id, archived_at FROM lightweight_agent_runs "
                        "WHERE archived_at IS NOT NULL "
                        "ORDER BY archived_at ASC, run_id ASC"
                    ).fetchall()
                    purge_ids: set[str] = set()
                    if policy.purge_after_seconds is not None:
                        cutoff = float(moment) - policy.purge_after_seconds
                        purge_ids.update(
                            str(row["run_id"])
                            for row in rows
                            if float(row["archived_at"]) <= cutoff
                        )
                    if policy.max_archived_runs is not None:
                        excess = len(rows) - policy.max_archived_runs
                        if excess > 0:
                            purge_ids.update(
                                str(row["run_id"]) for row in rows[:excess]
                            )
                    purged = sorted(purge_ids)
                    for run_id in purged:
                        connection.execute(
                            "DELETE FROM lightweight_agent_events WHERE run_id=?",
                            (run_id,),
                        )
                        connection.execute(
                            "DELETE FROM lightweight_agent_runs WHERE run_id=?",
                            (run_id,),
                        )
                    if purged:
                        removed_definitions, removed_values = _remove_orphaned_storage(
                            connection
                        )
                    connection.execute("COMMIT")
                except BaseException:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    raise
        except sqlite3.Error as error:
            raise RunJournalError("could not apply local run retention") from error
        return RunRetentionReport(
            tuple(sorted(archived)),
            tuple(purged),
            removed_definitions,
            removed_values,
        )


_JOURNAL_COLUMNS_BY_MIGRATION: tuple[dict[str, set[str]], ...] = (
    {
        "lightweight_agent_runs": {"run_id", "revision", "snapshot_json"},
        "lightweight_agent_events": {"run_id", "sequence", "type", "payload_json"},
    },
    {
        "lightweight_agent_definitions": {
            "definition_digest",
            "workflow_json",
            "execution_manifest_json",
        },
        "lightweight_agent_values": {"value_digest", "value_json"},
        "lightweight_agent_runs": {"status", "definition_digest"},
    },
    {
        "lightweight_agent_runs": {
            "cancel_requested",
            "execution_generation",
            "execution_owner_id",
            "execution_lease_expires",
        },
        "lightweight_agent_events": {"created_at"},
    },
    {"lightweight_agent_runs": {"archived_at"}},
    {
        "lightweight_agent_runs": {"execution_id"},
        "lightweight_agent_events": {"execution_id"},
    },
    {
        "lightweight_agent_runs": {
            "workflow_id",
            "workflow_version",
            "created_at",
            "updated_at",
            "pending_approval_count",
            "failure_code",
        },
    },
    {
        "lightweight_agent_runs": {"state_layout"},
        "lightweight_agent_state_cells": {
            "run_id",
            "path_json",
            "value_json",
            "value_digest",
        },
    },
)
_JOURNAL_INDEXES_BY_MIGRATION: tuple[dict[str, tuple[str, ...]], ...] = (
    {},
    {},
    {},
    {"lightweight_agent_runs_retention": ("status", "archived_at")},
    {"lightweight_agent_events_execution": ("run_id", "execution_id", "sequence")},
    {
        "lightweight_agent_runs_query_order": (
            "archived_at",
            "created_at",
            "run_id",
            "execution_id",
        ),
        "lightweight_agent_runs_query_filter": (
            "workflow_id",
            "status",
            "pending_approval_count",
            "archived_at",
            "created_at",
            "run_id",
            "execution_id",
        ),
    },
    {},
)


def _preflight_journal_schema(connection: sqlite3.Connection) -> int:
    """Reject unknown or torn journal schemas before any migration writes.

    A pre-ledger database is accepted only in the original two-table layout.
    Once a ledger exists, its exact prefix, metadata, tables, columns and
    journal-owned indexes must agree. This also rejects a newer writer's file
    without modifying its metadata or attempting an older migration.
    """

    try:
        objects = {
            str(row["name"]): str(row["type"])
            for row in connection.execute(
                "SELECT name, type FROM sqlite_master "
                "WHERE name LIKE 'lightweight_agent_%'"
            ).fetchall()
        }
        tables = {name for name, kind in objects.items() if kind == "table"}
        ledger_tables = {
            "lightweight_agent_journal_meta",
            "lightweight_agent_journal_migrations",
        }
        present_ledger = tables & ledger_tables
        if not present_ledger:
            if not objects:
                return 0
            legacy = {"lightweight_agent_runs", "lightweight_agent_events"}
            if tables != legacy or set(objects) != legacy:
                raise RunJournalError("local run journal has an unknown legacy schema")
            for table, columns in _JOURNAL_COLUMNS_BY_MIGRATION[0].items():
                if _table_columns(connection, table) != columns:
                    raise RunJournalError("local run journal legacy table is invalid")
            return 0
        if present_ledger != ledger_tables:
            raise RunJournalError("local run journal migration ledger is incomplete")
        if _table_columns(connection, "lightweight_agent_journal_meta") != {
            "key",
            "value",
        }:
            raise RunJournalError("local run journal metadata table is invalid")
        if _table_columns(connection, "lightweight_agent_journal_migrations") != {
            "version",
            "name",
            "applied_at",
        }:
            raise RunJournalError("local run journal migration table is invalid")
        meta = {
            str(row["key"]): str(row["value"])
            for row in connection.execute(
                "SELECT key, value FROM lightweight_agent_journal_meta"
            ).fetchall()
        }
        if (
            set(meta) - {"format", "schema_version"}
            or meta.get("format") != JOURNAL_STORAGE_FORMAT_V1
        ):
            raise RunJournalError("local run journal format is unsupported")
        migrations = connection.execute(
            "SELECT version, name FROM lightweight_agent_journal_migrations ORDER BY version"
        ).fetchall()
        known = tuple((version, name) for version, name, _ in _JOURNAL_MIGRATIONS)
        recorded = tuple((row["version"], row["name"]) for row in migrations)
        if recorded != known[: len(recorded)]:
            raise RunJournalError("local run journal migration ledger is unsupported")
        version = len(recorded)
        if version > JOURNAL_STORAGE_VERSION or (
            ("schema_version" in meta) != (version > 0)
            or (version > 0 and meta["schema_version"] != str(version))
        ):
            raise RunJournalError("local run journal version is invalid")
        expected_columns: dict[str, set[str]] = {}
        expected_indexes: dict[str, tuple[str, ...]] = {}
        for stage in range(version):
            for table, columns in _JOURNAL_COLUMNS_BY_MIGRATION[stage].items():
                expected_columns.setdefault(table, set()).update(columns)
            expected_indexes.update(_JOURNAL_INDEXES_BY_MIGRATION[stage])
        if version == 0:
            # A crash after creating the ledger but before migration 1 leaves
            # either no run tables or the unchanged pre-ledger layout.
            run_tables = tables - ledger_tables
            if run_tables:
                expected_columns = {
                    table: set(columns)
                    for table, columns in _JOURNAL_COLUMNS_BY_MIGRATION[0].items()
                }
        if tables != ledger_tables | set(expected_columns):
            raise RunJournalError(
                "local run journal tables do not match migration ledger"
            )
        for table, columns in expected_columns.items():
            if _table_columns(connection, table) != columns:
                raise RunJournalError(
                    "local run journal columns do not match migration ledger"
                )
        indexes = {name for name, kind in objects.items() if kind == "index"}
        if indexes != set(expected_indexes):
            raise RunJournalError(
                "local run journal indexes do not match migration ledger"
            )
        for name, columns in expected_indexes.items():
            actual = tuple(
                str(row[2]) for row in connection.execute(f"PRAGMA index_info({name})")
            )
            if actual != columns:
                raise RunJournalError("local run journal index is invalid")
        if version >= 7:
            _preflight_cell_schema(connection)
        return version
    except sqlite3.Error as error:
        raise RunJournalError("could not inspect local run journal schema") from error


def _ensure_migration_ledger(connection: sqlite3.Connection) -> None:
    """Create the immutable migration record before touching run tables."""

    try:
        connection.execute("BEGIN IMMEDIATE")
        _preflight_journal_schema(connection)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS lightweight_agent_journal_meta ("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS lightweight_agent_journal_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at REAL NOT NULL)"
        )
        row = connection.execute(
            "SELECT value FROM lightweight_agent_journal_meta WHERE key='format'"
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO lightweight_agent_journal_meta(key, value) VALUES "
                "('format', ?)",
                (JOURNAL_STORAGE_FORMAT_V1,),
            )
        elif row["value"] != JOURNAL_STORAGE_FORMAT_V1:
            raise RunJournalError("local run journal format is unsupported")
        connection.execute("COMMIT")
    except sqlite3.Error as error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise RunJournalError(
            "could not initialize local run journal ledger"
        ) from error
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def _applied_migrations(connection: sqlite3.Connection) -> set[int]:
    try:
        rows = connection.execute(
            "SELECT version FROM lightweight_agent_journal_migrations"
        ).fetchall()
    except sqlite3.Error as error:
        raise RunJournalError("could not read local run journal ledger") from error
    return {int(row["version"]) for row in rows}


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _preflight_cell_schema(connection: sqlite3.Connection) -> None:
    """Reject a cell table that cannot preserve CAS-owned state on purge."""

    run_info = {
        str(row[1]): row
        for row in connection.execute(
            "PRAGMA table_info(lightweight_agent_runs)"
        ).fetchall()
    }
    layout = run_info.get("state_layout")
    if layout is None or layout[3] != 1 or layout[4] != "'snapshot'":
        raise RunJournalError("local run journal state layout column is invalid")
    cell_info = {
        str(row[1]): row
        for row in connection.execute(
            "PRAGMA table_info(lightweight_agent_state_cells)"
        ).fetchall()
    }
    if (
        cell_info["run_id"][3:6:2] != (1, 1)
        or cell_info["path_json"][3:6:2] != (1, 2)
        or cell_info["value_json"][3] != 0
        or cell_info["value_digest"][3] != 0
    ):
        raise RunJournalError("local run journal state cell key is invalid")
    foreign_keys = {
        (str(row[2]), str(row[3]), str(row[4]), str(row[6]))
        for row in connection.execute(
            "PRAGMA foreign_key_list(lightweight_agent_state_cells)"
        ).fetchall()
    }
    if foreign_keys != {
        ("lightweight_agent_runs", "run_id", "run_id", "CASCADE"),
        ("lightweight_agent_values", "value_digest", "value_digest", "NO ACTION"),
    }:
        raise RunJournalError("local run journal state cell references are invalid")


def _migrate_base_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS lightweight_agent_runs ("
        "run_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
        "snapshot_json TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS lightweight_agent_events ("
        "run_id TEXT NOT NULL REFERENCES lightweight_agent_runs(run_id), "
        "sequence INTEGER NOT NULL, type TEXT NOT NULL, payload_json TEXT NOT NULL, "
        "PRIMARY KEY (run_id, sequence))"
    )


def _migrate_compact_definition_storage(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS lightweight_agent_definitions ("
        "definition_digest TEXT PRIMARY KEY, workflow_json TEXT NOT NULL, "
        "execution_manifest_json TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS lightweight_agent_values ("
        "value_digest TEXT PRIMARY KEY, value_json TEXT NOT NULL)"
    )
    columns = _table_columns(connection, "lightweight_agent_runs")
    if "status" not in columns:
        connection.execute("ALTER TABLE lightweight_agent_runs ADD COLUMN status TEXT")
        rows = connection.execute(
            "SELECT run_id, snapshot_json FROM lightweight_agent_runs"
        ).fetchall()
        for row in rows:
            try:
                raw = json.loads(row["snapshot_json"])
                status = str(raw["status"])
            except (KeyError, TypeError, json.JSONDecodeError) as error:
                raise RunJournalError(
                    "legacy run snapshot has no valid status"
                ) from error
            connection.execute(
                "UPDATE lightweight_agent_runs SET status=? WHERE run_id=?",
                (status, row["run_id"]),
            )
    if "definition_digest" not in columns:
        connection.execute(
            "ALTER TABLE lightweight_agent_runs ADD COLUMN definition_digest TEXT"
        )


def _migrate_control_projection(connection: sqlite3.Connection) -> None:
    columns = _table_columns(connection, "lightweight_agent_runs")
    control_columns = {
        "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
        "execution_generation": "INTEGER NOT NULL DEFAULT 0",
        "execution_owner_id": "TEXT",
        "execution_lease_expires": "REAL",
    }
    added = False
    for column, declaration in control_columns.items():
        if column not in columns:
            connection.execute(
                f"ALTER TABLE lightweight_agent_runs ADD COLUMN {column} {declaration}"
            )
            added = True
    if added:
        rows = connection.execute(
            "SELECT run_id, snapshot_json FROM lightweight_agent_runs"
        ).fetchall()
        for row in rows:
            try:
                raw = json.loads(row["snapshot_json"])
            except json.JSONDecodeError as error:
                raise RunJournalError(
                    "legacy run snapshot is not valid JSON"
                ) from error
            state = raw.get("state", {}) if isinstance(raw, Mapping) else {}
            cancel, generation, owner, lease = _state_control(state)
            connection.execute(
                "UPDATE lightweight_agent_runs SET cancel_requested=?, "
                "execution_generation=?, execution_owner_id=?, "
                "execution_lease_expires=? WHERE run_id=?",
                (int(cancel), generation, owner, lease, row["run_id"]),
            )
    event_columns = _table_columns(connection, "lightweight_agent_events")
    if "created_at" not in event_columns:
        connection.execute(
            "ALTER TABLE lightweight_agent_events ADD COLUMN created_at REAL"
        )
        connection.execute(
            "UPDATE lightweight_agent_events SET created_at=0 WHERE created_at IS NULL"
        )


def _migrate_retention_lifecycle(connection: sqlite3.Connection) -> None:
    columns = _table_columns(connection, "lightweight_agent_runs")
    if "archived_at" not in columns:
        connection.execute(
            "ALTER TABLE lightweight_agent_runs ADD COLUMN archived_at REAL"
        )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS lightweight_agent_runs_retention "
        "ON lightweight_agent_runs(status, archived_at)"
    )


def _migrate_execution_identity(connection: sqlite3.Connection) -> None:
    """Project immutable execution identity into durable rows and events.

    Older journals kept the identity only inside ``snapshot_json``.  The
    projection makes compare-and-set identity checks possible in one SQLite
    statement and prevents an old cursor from silently observing a reused
    business ``run_id``.
    """

    run_columns = _table_columns(connection, "lightweight_agent_runs")
    if "execution_id" not in run_columns:
        connection.execute(
            "ALTER TABLE lightweight_agent_runs ADD COLUMN execution_id TEXT"
        )
    rows = connection.execute(
        "SELECT run_id, snapshot_json, execution_id FROM lightweight_agent_runs "
        "WHERE execution_id IS NULL OR execution_id=''"
    ).fetchall()
    for row in rows:
        try:
            raw = json.loads(row["snapshot_json"])
            # Pre-instance snapshots have no identity field.  Treat an empty
            # legacy placeholder the same way rather than making an otherwise
            # recoverable old journal impossible to open.
            execution_id = raw.get("execution_id") or row["run_id"]
            execution_id = _validated_execution_id(execution_id)
        except (
            AttributeError,
            TypeError,
            json.JSONDecodeError,
            WorkflowValidationError,
        ) as error:
            raise RunJournalError(
                "legacy run snapshot has no valid execution identity"
            ) from error
        connection.execute(
            "UPDATE lightweight_agent_runs SET execution_id=? WHERE run_id=?",
            (execution_id, row["run_id"]),
        )

    event_columns = _table_columns(connection, "lightweight_agent_events")
    if "execution_id" not in event_columns:
        connection.execute(
            "ALTER TABLE lightweight_agent_events ADD COLUMN execution_id TEXT"
        )
    connection.execute(
        "UPDATE lightweight_agent_events SET execution_id=("
        "SELECT execution_id FROM lightweight_agent_runs "
        "WHERE lightweight_agent_runs.run_id=lightweight_agent_events.run_id"
        ") WHERE execution_id IS NULL OR execution_id=''"
    )
    missing = connection.execute(
        "SELECT 1 FROM lightweight_agent_events "
        "WHERE execution_id IS NULL OR execution_id='' LIMIT 1"
    ).fetchone()
    if missing is not None:
        raise RunJournalError("legacy event has no execution identity")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS lightweight_agent_events_execution "
        "ON lightweight_agent_events(run_id, execution_id, sequence)"
    )


def _migrate_run_query_projection(connection: sqlite3.Connection) -> None:
    """Add a compact operator-list projection to existing local journals.

    The normal create/replace path keeps these fields current in the same
    transaction as the snapshot and event.  This one-time migration is the
    sole place that parses legacy compact snapshots for listing metadata.
    """

    columns = _table_columns(connection, "lightweight_agent_runs")
    projection_columns = {
        "workflow_id": "TEXT",
        "workflow_version": "TEXT",
        "created_at": "REAL",
        "updated_at": "REAL",
        "pending_approval_count": "INTEGER NOT NULL DEFAULT 0",
        "failure_code": "TEXT",
    }
    for column, declaration in projection_columns.items():
        if column not in columns:
            connection.execute(
                f"ALTER TABLE lightweight_agent_runs ADD COLUMN {column} {declaration}"
            )
    rows = connection.execute(
        "SELECT r.run_id, r.execution_id, r.status, r.snapshot_json, "
        "d.workflow_json, "
        "COALESCE((SELECT MIN(e.created_at) FROM lightweight_agent_events AS e "
        "WHERE e.run_id=r.run_id), 0) AS first_event_at, "
        "COALESCE((SELECT MAX(e.created_at) FROM lightweight_agent_events AS e "
        "WHERE e.run_id=r.run_id), 0) AS last_event_at "
        "FROM lightweight_agent_runs AS r "
        "LEFT JOIN lightweight_agent_definitions AS d "
        "ON d.definition_digest=r.definition_digest"
    ).fetchall()
    for row in rows:
        try:
            compact = json.loads(row["snapshot_json"])
            if not isinstance(compact, Mapping):
                raise TypeError("snapshot is not an object")
            workflow = compact.get("workflow")
            if not isinstance(workflow, Mapping):
                if row["workflow_json"] is None:
                    raise KeyError("workflow_json")
                workflow = json.loads(row["workflow_json"])
            summary = _run_summary_from_parts(
                run_id=row["run_id"],
                execution_id=row["execution_id"],
                workflow=workflow,
                state=compact.get("state"),
                status=row["status"],
                created_at=row["first_event_at"],
                updated_at=row["last_event_at"],
            )
        except (
            KeyError,
            TypeError,
            json.JSONDecodeError,
            WorkflowValidationError,
        ) as error:
            raise RunJournalError("legacy run query projection is invalid") from error
        connection.execute(
            "UPDATE lightweight_agent_runs SET workflow_id=?, workflow_version=?, "
            "created_at=?, updated_at=?, pending_approval_count=?, failure_code=? "
            "WHERE run_id=?",
            (
                summary.workflow_id,
                summary.workflow_version,
                summary.created_at,
                summary.updated_at,
                summary.pending_approval_count,
                summary.failure_code,
                summary.run_id,
            ),
        )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS lightweight_agent_runs_query_order "
        "ON lightweight_agent_runs(archived_at, created_at DESC, run_id, execution_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS lightweight_agent_runs_query_filter "
        "ON lightweight_agent_runs(workflow_id, status, pending_approval_count, "
        "archived_at, created_at DESC, run_id, execution_id)"
    )


def _migrate_local_state_cells(connection: sqlite3.Connection) -> None:
    """Add one alternative state layout without changing existing run owners."""

    connection.execute(
        "ALTER TABLE lightweight_agent_runs ADD COLUMN state_layout TEXT NOT NULL "
        "DEFAULT 'snapshot'"
    )
    connection.execute(
        "CREATE TABLE lightweight_agent_state_cells ("
        "run_id TEXT NOT NULL REFERENCES lightweight_agent_runs(run_id) "
        "ON DELETE CASCADE, path_json TEXT NOT NULL, value_json TEXT, "
        "value_digest TEXT REFERENCES lightweight_agent_values(value_digest), "
        "CHECK ((value_json IS NULL) != (value_digest IS NULL)), "
        "PRIMARY KEY (run_id, path_json))"
    )


_JOURNAL_MIGRATIONS = (
    (1, "base-run-events", _migrate_base_tables),
    (2, "compact-definition-storage", _migrate_compact_definition_storage),
    (3, "control-projection", _migrate_control_projection),
    (4, "retention-lifecycle", _migrate_retention_lifecycle),
    (5, "execution-identity", _migrate_execution_identity),
    (6, "run-query-projection", _migrate_run_query_projection),
    (7, "local-state-cells", _migrate_local_state_cells),
)


def _backup_target(destination: Path | str, source: Path, *, overwrite: bool) -> Path:
    requested = Path(destination)
    if requested.is_symlink():
        raise RunJournalError("journal backup target must not be a symlink")
    final = requested.resolve()
    if final == source.resolve():
        raise RunJournalError("journal backup target must differ from source")
    if final.exists() and not overwrite:
        raise RunJournalError("journal backup target already exists")
    if _wal_sidecars_exist(final):
        raise RunJournalError("journal backup target has WAL sidecars")
    final.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{final.name}.backup-", suffix=".sqlite", dir=final.parent
    )
    os.close(descriptor)
    return Path(temporary)


def _copy_sqlite_database(source: Path, destination: Path) -> None:
    with (
        closing(sqlite3.connect(source)) as source_connection,
        closing(sqlite3.connect(destination)) as destination_connection,
    ):
        source_connection.backup(destination_connection)


def _wal_sidecars_exist(path: Path) -> bool:
    return any(
        sidecar.exists() or sidecar.is_symlink()
        for sidecar in (Path(f"{path}-wal"), Path(f"{path}-shm"))
    )


def _seal_sqlite_file(path: Path) -> None:
    """Checkpoint a temporary WAL database before publishing its main file."""

    try:
        with closing(sqlite3.connect(path, isolation_level=None)) as connection:
            mode = connection.execute("PRAGMA journal_mode = DELETE").fetchone()
    except sqlite3.Error as error:
        raise RunJournalError("could not seal temporary journal database") from error
    if mode is None or str(mode[0]).lower() != "delete":
        raise RunJournalError("could not seal temporary journal database")
    if _wal_sidecars_exist(path):
        raise RunJournalError("temporary journal still has WAL sidecars")


def _remove_backup_temp(path: Path) -> None:
    """Remove only the exact temporary database created by `_backup_target`."""

    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def _validate_sqlite_database(path: Path) -> None:
    try:
        with closing(sqlite3.connect(path)) as connection:
            row = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as error:
        raise RunJournalError("journal database integrity check failed") from error
    if row is None or str(row[0]).lower() != "ok":
        raise RunJournalError("journal database integrity check failed")


def _remove_orphaned_storage(connection: sqlite3.Connection) -> tuple[int, int]:
    """Remove only immutable content no retained compact snapshot references."""

    rows = connection.execute(
        "SELECT snapshot_json FROM lightweight_agent_runs"
    ).fetchall()
    referenced_values: set[str] = set()
    for row in rows:
        try:
            compact = json.loads(row["snapshot_json"])
        except json.JSONDecodeError as error:
            raise RunJournalError("retained run snapshot is not valid JSON") from error
        referenced_values.update(_compact_value_digests(compact))
    referenced_values.update(
        str(row["value_digest"])
        for row in connection.execute(
            "SELECT DISTINCT value_digest FROM lightweight_agent_state_cells "
            "WHERE value_digest IS NOT NULL"
        ).fetchall()
    )
    if referenced_values:
        placeholders = ",".join("?" for _ in referenced_values)
        removed_values = connection.execute(
            "DELETE FROM lightweight_agent_values WHERE value_digest NOT IN "
            f"({placeholders})",
            tuple(sorted(referenced_values)),
        ).rowcount
    else:
        removed_values = connection.execute(
            "DELETE FROM lightweight_agent_values"
        ).rowcount
    removed_definitions = connection.execute(
        "DELETE FROM lightweight_agent_definitions WHERE definition_digest NOT IN "
        "(SELECT DISTINCT definition_digest FROM lightweight_agent_runs "
        "WHERE definition_digest IS NOT NULL)"
    ).rowcount
    return int(removed_definitions), int(removed_values)


def _mapping_definition(
    mapping: Mapping[str, object],
) -> tuple[str, str, str]:
    workflow_json = canonical_json(mapping.get("workflow", {}))
    manifest_json = canonical_json(mapping.get("execution_manifest", {}))
    definition_digest = (
        "sha256:"
        + hashlib.sha256(
            (workflow_json + "\n" + manifest_json).encode("utf-8")
        ).hexdigest()
    )
    return definition_digest, workflow_json, manifest_json


def _snapshot_definition(snapshot: RunSnapshot) -> tuple[str, str, str]:
    return snapshot._definition_parts()


def _cell_path(*parts: str) -> str:
    return canonical_json(list(parts))


def _state_cells(state: Mapping[str, JsonValue]) -> dict[str, str]:
    """Partition one validated v4 state into journal-owned JSON cells."""

    cells: dict[str, str] = {}

    def put(path: tuple[str, ...], value: JsonValue) -> None:
        cells[_cell_path(*path)] = canonical_json(value)

    for key, value in state.items():
        if key in _CELL_ENTRY_SECTIONS and isinstance(value, Mapping):
            put((key,), {})
            for member, item in value.items():
                put((key, member), item)
        elif (
            key in _CELL_MEMBER_SECTIONS
            and isinstance(value, list)
            and all(isinstance(member, str) and member for member in value)
            and value == sorted(set(value))
        ):
            put((key,), [])
            for member in value:
                put((key, member), True)
        elif key == "v3" and isinstance(value, Mapping):
            put((key,), {})
            for section, item in value.items():
                if section in _CELL_V3_ENTRY_SECTIONS and isinstance(item, Mapping):
                    put((key, section), {})
                    for member, entry in item.items():
                        put((key, section, member), entry)
                else:
                    put((key, section), item)
        else:
            put((key,), value)
    return cells


def _read_state_cells(connection: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT path_json, value_json, value_digest "
        "FROM lightweight_agent_state_cells "
        "WHERE run_id=?",
        (run_id,),
    ).fetchall()
    if not rows:
        raise RunJournalError("run state cell record is missing")
    stored_values = _read_values(
        connection,
        {str(row["value_digest"]) for row in rows if row["value_digest"] is not None},
    )
    decoded: list[tuple[tuple[str, ...], JsonValue]] = []
    try:
        for row in rows:
            path = _parse_cell_path(row["path_json"])
            value = (
                stored_values[str(row["value_digest"])]
                if row["value_digest"] is not None
                else json.loads(row["value_json"])
            )
            decoded.append((path, value))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise RunJournalError("run state cell record is invalid") from error
    state: dict[str, Any] = {}
    member_cell_sections: set[str] = set()
    for path, value in sorted(decoded, key=lambda item: (len(item[0]), item[0])):
        if len(path) == 1:
            state[path[0]] = value
        elif len(path) == 2 and path[0] in _CELL_MEMBER_SECTIONS:
            members = state.get(path[0])
            if (
                not isinstance(members, list)
                or value is not True
                or (path[0] not in member_cell_sections and members)
            ):
                raise RunJournalError("run state membership cell is invalid")
            member_cell_sections.add(path[0])
            members.append(path[1])
        elif len(path) == 2:
            parent = state.get(path[0])
            if not isinstance(parent, dict):
                raise RunJournalError("run state cell parent is invalid")
            parent[path[1]] = value
        elif len(path) == 3 and path[0] == "v3":
            v3 = state.get("v3")
            parent = v3.get(path[1]) if isinstance(v3, dict) else None
            if not isinstance(parent, dict):
                raise RunJournalError("run state cell parent is invalid")
            parent[path[2]] = value
        else:
            raise RunJournalError("run state cell path is invalid")
    for section in member_cell_sections:
        members = state.get(section)
        if isinstance(members, list):
            members.sort()
    return state


def _parse_cell_path(raw: str) -> tuple[str, ...]:
    try:
        parts = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise RunJournalError("run state cell path is invalid") from error
    if (
        not isinstance(parts, list)
        or not parts
        or any(not isinstance(part, str) or not part for part in parts)
        or _cell_path(*parts) != raw
    ):
        raise RunJournalError("run state cell path is invalid")
    return tuple(parts)


def _read_inspection_cells(
    connection: sqlite3.Connection, run_id: str
) -> dict[str, Any]:
    """Project only operator fields; retained tool outputs stay in SQLite.

    Action results can be larger than the rest of a run. SQLite's built-in
    JSON path functions extract their small control fields without returning
    a complete result or all other state cells to Python. This is a read-only
    projection; ``get`` still validates and hydrates the full recovery state.
    """

    roots = (
        _cell_path("completed"),
        _cell_path("failure"),
        _cell_path("pending"),
        _cell_path("v3", "cancel_requested"),
        _cell_path("v3", "step_states"),
        _cell_path("v3", "pending_approvals"),
    )
    prefixes = (
        '["completed",%',
        '["v3","step_states",%',
        '["v3","pending_approvals",%',
    )
    rows = connection.execute(
        "SELECT path_json, value_json, value_digest "
        "FROM lightweight_agent_state_cells WHERE run_id=? "
        "AND (path_json IN (?,?,?,?,?,?) OR path_json LIKE ? "
        "OR path_json LIKE ? OR path_json LIKE ?)",
        (run_id, *roots, *prefixes),
    ).fetchall()
    digests = {
        str(row["value_digest"]) for row in rows if row["value_digest"] is not None
    }
    stored_values = _read_values(connection, digests) if digests else {}
    state: dict[str, Any] = {"actions": {}, "v3": {}}
    completed_members = False
    for row in sorted(rows, key=lambda item: len(_parse_cell_path(item["path_json"]))):
        path = _parse_cell_path(row["path_json"])
        try:
            value = (
                stored_values[str(row["value_digest"])]
                if row["value_digest"] is not None
                else json.loads(row["value_json"])
            )
        except (TypeError, json.JSONDecodeError) as error:
            raise RunJournalError("run inspection cell is invalid") from error
        if path == ("completed",):
            state["completed"] = value
        elif len(path) == 2 and path[0] == "completed":
            members = state.get("completed")
            if (
                not isinstance(members, list)
                or value is not True
                or (not completed_members and members)
            ):
                raise RunJournalError("run inspection membership is invalid")
            members.append(path[1])
            completed_members = True
        elif path in {("failure",), ("pending",)}:
            state[path[0]] = value
        elif path == ("v3", "cancel_requested"):
            state["v3"]["cancel_requested"] = value
        elif path in {("v3", "step_states"), ("v3", "pending_approvals")}:
            state["v3"][path[1]] = value
        elif len(path) == 3 and path[:2] in {
            ("v3", "step_states"),
            ("v3", "pending_approvals"),
        }:
            parent = state["v3"].get(path[1])
            if not isinstance(parent, dict):
                raise RunJournalError("run inspection cell parent is invalid")
            parent[path[2]] = value
        else:
            raise RunJournalError("run inspection cell path is invalid")
    if completed_members:
        state["completed"].sort()

    value = "COALESCE(c.value_json, v.value_json)"
    actions = connection.execute(
        "SELECT c.path_json, "
        f"json_type({value}) AS value_type, "
        f"json_extract({value}, '$.status') AS status, "
        f"json_extract({value}, '$.step_key') AS step_key, "
        f"json_extract({value}, '$.step_id') AS step_id, "
        f"json_type({value}, '$.request.tool') AS tool_type, "
        f"json_extract({value}, '$.request.tool') AS tool_json, "
        f"json_type({value}, '$.result') AS result_type, "
        f"json_type({value}, '$.recovery') AS recovery_type, "
        f"json_extract({value}, '$.recovery') AS recovery_json "
        "FROM lightweight_agent_state_cells AS c "
        "LEFT JOIN lightweight_agent_values AS v ON v.value_digest=c.value_digest "
        "WHERE c.run_id=? AND c.path_json LIKE ?",
        (run_id, '["actions",%'),
    ).fetchall()
    for row in actions:
        path = _parse_cell_path(row["path_json"])
        if len(path) != 2 or path[0] != "actions" or row["value_type"] != "object":
            raise RunJournalError("run inspection action cell is invalid")
        item: dict[str, Any] = {
            key: row[key] for key in ("status", "step_key", "step_id")
        }
        if row["tool_type"] == "object":
            item["request"] = {"tool": json.loads(row["tool_json"])}
        if row["result_type"] not in {None, "null"}:
            item["result"] = {}
        if row["recovery_type"] == "object":
            item["recovery"] = json.loads(row["recovery_json"])
        state["actions"][path[1]] = item
    if not rows and not actions:
        exists = connection.execute(
            "SELECT 1 FROM lightweight_agent_state_cells WHERE run_id=? LIMIT 1",
            (run_id,),
        ).fetchone()
        if exists is None:
            raise RunJournalError("run state cell record is missing")
    return state


def _read_cell_output(connection: sqlite3.Connection, run_id: str) -> JsonValue:
    row = connection.execute(
        "SELECT value_json, value_digest FROM lightweight_agent_state_cells "
        "WHERE run_id=? AND path_json=?",
        (run_id, _cell_path("v3", "output")),
    ).fetchone()
    if row is None:
        return None
    digest = row["value_digest"]
    if digest is not None:
        return _read_values(connection, {str(digest)})[str(digest)]
    try:
        return json.loads(row["value_json"])
    except (TypeError, json.JSONDecodeError) as error:
        raise RunJournalError("run output cell is invalid") from error


def _cell_value(state: Mapping[str, Any], path: tuple[str, ...]) -> object:
    value: object = state
    for part in path:
        if not isinstance(value, Mapping) or part not in value:
            return _CELL_ABSENT
        value = value[part]
    return value


def _cell_address_for_write(path: tuple[str, ...]) -> tuple[str, ...] | None:
    section = path[0]
    if section in _CELL_ENTRY_SECTIONS:
        return path[:2] if len(path) >= 2 else None
    if section in _CELL_MEMBER_SECTIONS:
        return None
    if section == "v3":
        if len(path) < 2:
            return None
        if path[1] in _CELL_V3_ENTRY_SECTIONS:
            return path[:3] if len(path) >= 3 else None
        return path[:2]
    return path[:1]


def _cell_updates_from_writes(
    state: Mapping[str, Any], write_set: StateWriteSet
) -> dict[str, str | None] | None:
    """Validate changed values and project editor writes to local cells.

    A section-wide replacement needs a full cell rewrite. The write set is
    never accepted from a caller-created snapshot unless the in-process
    editor minted it; other journal clients keep the full-state fallback.
    """

    if not write_set._from_editor:
        return None
    affected: set[tuple[str, ...]] = set()
    for write in write_set.writes:
        actual = _cell_value(state, write.path)
        if write.deleted:
            if actual is not _CELL_ABSENT:
                raise RunJournalError("run state deletion does not match snapshot")
        elif actual is _CELL_ABSENT or canonical_json(actual) != write.value_json:
            raise RunJournalError("run state write does not match snapshot")
        address = _cell_address_for_write(write.path)
        if address is None:
            return None
        affected.add(address)
    for addition in write_set.member_additions:
        members = _cell_value(state, addition.path)
        if not isinstance(members, list) or addition.member not in members:
            raise RunJournalError("run state member does not match snapshot")
        affected.add((*addition.path, addition.member))
    updates: dict[str, str | None] = {}
    for address in sorted(affected):
        if len(address) == 2 and address[0] in _CELL_MEMBER_SECTIONS:
            updates[_cell_path(*address)] = "true"
            continue
        value = _cell_value(state, address)
        updates[_cell_path(*address)] = (
            None if value is _CELL_ABSENT else canonical_json(value)
        )
    return updates


def _write_state_cells(
    connection: sqlite3.Connection,
    run_id: str,
    cells: Mapping[str, str | None],
    *,
    replace_all: bool,
) -> None:
    if replace_all:
        connection.execute(
            "DELETE FROM lightweight_agent_state_cells WHERE run_id=?", (run_id,)
        )
    for path_json, value_json in cells.items():
        if value_json is None:
            connection.execute(
                "DELETE FROM lightweight_agent_state_cells "
                "WHERE run_id=? AND path_json=?",
                (run_id, path_json),
            )
        else:
            if len(value_json.encode("utf-8")) >= _VALUE_INLINE_BYTES:
                digest = (
                    "sha256:" + hashlib.sha256(value_json.encode("utf-8")).hexdigest()
                )
                connection.execute(
                    "INSERT OR IGNORE INTO lightweight_agent_values"
                    "(value_digest, value_json) VALUES (?, ?)",
                    (digest, value_json),
                )
                inline = None
            else:
                digest = None
                inline = value_json
            connection.execute(
                "INSERT INTO lightweight_agent_state_cells"
                "(run_id, path_json, value_json, value_digest) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(run_id, path_json) DO UPDATE "
                "SET value_json=excluded.value_json, "
                "value_digest=excluded.value_digest",
                (run_id, path_json, inline, digest),
            )


def _compact_cell_snapshot(
    snapshot: RunSnapshot,
) -> tuple[dict[str, JsonValue], dict[str, str]]:
    """Keep immutable inputs/metadata in the run row, not duplicated state."""

    mutable = snapshot._mutable
    if mutable is None:  # pragma: no cover - safeguarded by RunSnapshot
        raise WorkflowValidationError("run mutable cache is unavailable")
    values: dict[str, str] = {}
    mapping: dict[str, JsonValue] = {
        "run_id": snapshot.run_id,
        "inputs": _encode_stored_value(
            _checkpoint_object(mutable.inputs_json, "inputs"), values
        ),
        "state": {},
        "status": snapshot.status,
        "driver_bindings": _checkpoint_object(
            mutable.driver_bindings_json, "driver_bindings"
        ),
        "execution_id": snapshot.execution_id,
        "revision": snapshot.revision,
    }
    return mapping, values


def _compact_snapshot(
    snapshot: RunSnapshot,
) -> tuple[dict[str, JsonValue], dict[str, str]]:
    # The immutable workflow and frozen execution manifest are stored in the
    # definitions table and serialized by ``_snapshot_definition`` above.  Do
    # not JSON-copy those potentially large trees again for every state/event
    # transition only to remove them immediately.  Mutable values still take
    # the normal validating detached-copy path before they can be persisted.
    mapping: dict[str, JsonValue] = {
        "run_id": snapshot.run_id,
        "inputs": _checkpoint_object(snapshot._mutable.inputs_json, "inputs"),
        "state": snapshot._state_copy(),
        "status": snapshot.status,
        "driver_bindings": _checkpoint_object(
            snapshot._mutable.driver_bindings_json, "driver_bindings"
        ),
        "execution_id": snapshot.execution_id,
        "revision": snapshot.revision,
    }
    values: dict[str, str] = {}
    mapping["inputs"] = _encode_stored_value(mapping["inputs"], values)
    state = mapping.get("state")
    if isinstance(state, dict):
        schema_version = snapshot._definition_schema_version()
        state_values = state.get("values")
        if isinstance(state_values, dict):
            if schema_version in {
                "v6-light-workflow-v3",
                "v6-light-workflow-v4",
            }:
                state["values"] = _encode_stored_value(state_values, values)
            else:
                state["values"] = {
                    key: _encode_stored_value(value, values)
                    for key, value in state_values.items()
                }
        actions = state.get("actions")
        if isinstance(actions, dict):
            for action in actions.values():
                if not isinstance(action, dict):
                    continue
                result = action.get("result")
                if isinstance(result, dict) and "output" in result:
                    result["output"] = _encode_stored_value(result["output"], values)
        agents = state.get("agents")
        if isinstance(agents, dict):
            for agent in agents.values():
                if not isinstance(agent, dict):
                    continue
                if "final_output" in agent:
                    agent["final_output"] = _encode_stored_value(
                        agent["final_output"], values
                    )
                _encode_legacy_agent_history(agent, values)
        v3 = state.get("v3")
        if isinstance(v3, dict):
            step_outputs = v3.get("step_outputs")
            if isinstance(step_outputs, dict):
                v3["step_outputs"] = {
                    key: _encode_stored_value(value, values)
                    for key, value in step_outputs.items()
                }
            if "output" in v3:
                v3["output"] = _encode_stored_value(v3["output"], values)
    return mapping, values


def _encode_stored_value(value: JsonValue, values: dict[str, str]) -> JsonValue:
    serialized = canonical_json(value)
    size = len(serialized.encode("utf-8"))
    if size < _VALUE_INLINE_BYTES:
        return {_VALUE_STORAGE_KEY: "inline", "value": json_copy(value)}
    value_digest = "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    values[value_digest] = serialized
    return {
        _VALUE_STORAGE_KEY: "ref",
        "digest": value_digest,
        "bytes": size,
    }


def _encode_legacy_agent_history(agent: dict[str, Any], values: dict[str, str]) -> None:
    history = agent.get("history")
    if not isinstance(history, list):
        return
    for batch in history:
        if not isinstance(batch, dict):
            continue
        results = batch.get("tool_results")
        if not isinstance(results, list):
            continue
        for result in results:
            if isinstance(result, dict) and "output" in result:
                result["output"] = _encode_stored_value(result["output"], values)


def _insert_values(connection: sqlite3.Connection, values: Mapping[str, str]) -> None:
    connection.executemany(
        "INSERT OR IGNORE INTO lightweight_agent_values(value_digest, value_json) "
        "VALUES (?, ?)",
        values.items(),
    )


def _stored_value_digest(value: object) -> str | None:
    """Recognize only the exact private envelope written by this journal."""

    if not isinstance(value, Mapping):
        return None
    if (
        set(value) == {_VALUE_STORAGE_KEY, "digest", "bytes"}
        and value.get(_VALUE_STORAGE_KEY) == "ref"
        and isinstance(value.get("digest"), str)
        and isinstance(value.get("bytes"), int)
        and not isinstance(value.get("bytes"), bool)
    ):
        return str(value["digest"])
    return None


def _compact_value_digests(mapping: object) -> set[str]:
    """Find references only at compact-snapshot slots owned by the journal.

    A pre-normalization snapshot contains ``workflow`` and has no private
    envelopes.  Most importantly, this function never recursively searches
    business JSON, including an inline envelope's opaque ``value`` member.
    """

    if not isinstance(mapping, Mapping) or "workflow" in mapping:
        return set()
    found: set[str] = set()

    def add(value: object) -> None:
        digest = _stored_value_digest(value)
        if digest is not None:
            found.add(digest)

    add(mapping.get("inputs"))
    state = mapping.get("state")
    if not isinstance(state, Mapping):
        return found
    state_values = state.get("values")
    if _stored_value_digest(state_values) is not None:
        add(state_values)
    elif isinstance(state_values, Mapping):
        for value in state_values.values():
            add(value)
    actions = state.get("actions")
    if isinstance(actions, Mapping):
        for action in actions.values():
            if not isinstance(action, Mapping):
                continue
            action_result = action.get("result")
            if isinstance(action_result, Mapping):
                add(action_result.get("output"))
    agents = state.get("agents")
    if isinstance(agents, Mapping):
        for agent in agents.values():
            if not isinstance(agent, Mapping):
                continue
            add(agent.get("final_output"))
            history = agent.get("history")
            if not isinstance(history, list):
                continue
            for batch in history:
                results = (
                    batch.get("tool_results") if isinstance(batch, Mapping) else None
                )
                if isinstance(results, list):
                    for item in results:
                        if isinstance(item, Mapping):
                            add(item.get("output"))
    v3 = state.get("v3")
    if isinstance(v3, Mapping):
        step_outputs = v3.get("step_outputs")
        if isinstance(step_outputs, Mapping):
            for value in step_outputs.values():
                add(value)
        add(v3.get("output"))
    return found


def _read_values(
    connection: sqlite3.Connection, digests: set[str]
) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    pending = sorted(digests)
    for offset in range(0, len(pending), 500):
        chunk = pending[offset : offset + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = connection.execute(
            "SELECT value_digest, value_json FROM lightweight_agent_values "
            f"WHERE value_digest IN ({placeholders})",
            chunk,
        ).fetchall()
        for row in rows:
            value_digest = str(row["value_digest"])
            try:
                parsed = json.loads(row["value_json"])
            except json.JSONDecodeError as error:
                raise RunJournalError("run value record is not valid JSON") from error
            actual = (
                "sha256:"
                + hashlib.sha256(canonical_json(parsed).encode("utf-8")).hexdigest()
            )
            if actual != value_digest:
                raise RunJournalError("run value record digest does not match")
            result[value_digest] = parsed
    missing = digests - set(result)
    if missing:
        raise RunJournalError("run value record is missing")
    return result


def _decode_stored_value(
    value: object, stored_values: Mapping[str, JsonValue]
) -> JsonValue:
    if not isinstance(value, Mapping):
        return json_copy(value)  # type: ignore[arg-type]
    if (
        set(value) == {_VALUE_STORAGE_KEY, "value"}
        and value.get(_VALUE_STORAGE_KEY) == "inline"
    ):
        return json_copy(value["value"])  # type: ignore[arg-type]
    if (
        set(value) == {_VALUE_STORAGE_KEY, "digest", "bytes"}
        and value.get(_VALUE_STORAGE_KEY) == "ref"
    ):
        digest_value = value.get("digest")
        if not isinstance(digest_value, str) or digest_value not in stored_values:
            raise RunJournalError("run value reference is invalid")
        return json_copy(stored_values[digest_value])
    # Backward-compatible read of an unwrapped snapshot value.
    return json_copy(value)  # type: ignore[arg-type]


def _hydrate_compact_values(
    mapping: dict[str, object], stored_values: Mapping[str, JsonValue]
) -> dict[str, object]:
    if "inputs" in mapping:
        mapping["inputs"] = _decode_stored_value(mapping["inputs"], stored_values)
    state = mapping.get("state")
    if not isinstance(state, dict):
        return mapping
    schema_version = ""
    workflow = mapping.get("workflow")
    if isinstance(workflow, Mapping):
        schema_version = str(workflow.get("schema_version", ""))
    state_values = state.get("values")
    if isinstance(state_values, Mapping):
        if schema_version in {"v6-light-workflow-v3", "v6-light-workflow-v4"}:
            state["values"] = _decode_stored_value(state_values, stored_values)
        else:
            state["values"] = {
                key: _decode_stored_value(value, stored_values)
                for key, value in state_values.items()
            }
    actions = state.get("actions")
    if isinstance(actions, dict):
        for action in actions.values():
            if not isinstance(action, dict):
                continue
            result = action.get("result")
            if isinstance(result, dict) and "output" in result:
                result["output"] = _decode_stored_value(result["output"], stored_values)
    agents = state.get("agents")
    if isinstance(agents, dict):
        for agent in agents.values():
            if not isinstance(agent, dict):
                continue
            if "final_output" in agent:
                agent["final_output"] = _decode_stored_value(
                    agent["final_output"], stored_values
                )
            _hydrate_legacy_agent_history(agent, stored_values)
    v3 = state.get("v3")
    if isinstance(v3, dict):
        step_outputs = v3.get("step_outputs")
        if isinstance(step_outputs, Mapping):
            v3["step_outputs"] = {
                key: _decode_stored_value(value, stored_values)
                for key, value in step_outputs.items()
            }
        if "output" in v3:
            v3["output"] = _decode_stored_value(v3["output"], stored_values)
    return mapping


def _hydrate_legacy_agent_history(
    agent: dict[str, Any], stored_values: Mapping[str, JsonValue]
) -> None:
    history = agent.get("history")
    if not isinstance(history, list):
        return
    for batch in history:
        if not isinstance(batch, dict):
            continue
        results = batch.get("tool_results")
        if not isinstance(results, list):
            continue
        for result in results:
            if isinstance(result, dict) and "output" in result:
                result["output"] = _decode_stored_value(result["output"], stored_values)


def _hydrate_snapshot_row(row: sqlite3.Row) -> dict[str, object]:
    mapping = json.loads(row["snapshot_json"])
    if "workflow" in mapping:
        # Backward-compatible read of the pre-normalization journal layout.
        return mapping
    workflow_json = row["workflow_json"]
    manifest_json = row["execution_manifest_json"]
    if workflow_json is None or manifest_json is None:
        raise RunJournalError("run definition record is missing")
    mapping["workflow"] = json.loads(workflow_json)
    mapping["execution_manifest"] = json.loads(manifest_json)
    return mapping
