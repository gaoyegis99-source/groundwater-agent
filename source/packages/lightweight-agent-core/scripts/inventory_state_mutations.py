"""Inspect real v4 state changes without changing the runtime or its journal.

Run with the pinned, development-only RFC 6902 implementation::

    uv run --locked --extra workflow --group development \
        python scripts/inventory_state_mutations.py --steps 40

JSON Patch is used here only to *measure and cross-check* existing transitions.
The offline cell replay derives its changes from the coordinator's explicit
write set; the full-document diff is a development-only oracle, not a
candidate incremental production commit path. The report contains JSON
Pointer paths and byte counts, never state values.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import runpy
import sqlite3
import tempfile
from typing import Any

import jsonpatch
from jsonpointer import JsonPointer, resolve_pointer, set_pointer

from platform_lightweight_agent import (
    InMemoryRunJournal,
    LocalToolExecutor,
    WorkflowEngine,
)
from platform_lightweight_agent.journal import RunSnapshot
from platform_lightweight_agent.spec import canonical_json
from platform_lightweight_agent.state_edits import StateWriteSet


def _section(path: str) -> str:
    parts = path.split("/")
    if len(parts) < 2 or not parts[1]:
        return "<root>"
    if parts[1] == "v3" and len(parts) > 2:
        return "v3/" + parts[2]
    if parts[1] in {"actions", "agents", "branches", "completed", "pending", "values"}:
        return parts[1] + "/<entry>"
    return parts[1]


_ENTRY_SECTIONS = frozenset({"actions", "agents", "branches"})
_V3_ATOMIC_SECTIONS = frozenset({"execution", "output"})
_MEMBER_LIST_SECTIONS = frozenset({"completed", "approved_steps"})
_ABSENT = object()


def _cells(state: dict[str, Any]) -> dict[str, Any]:
    """Partition actual v4 state for an offline, non-authoritative replay.

    Completed/approved members get independent fact cells; other lists keep
    their ordinary JSON shape. JSON Pointer escaping comes from the pinned
    RFC 6902 library's ``jsonpointer`` dependency.
    """

    cells: dict[str, Any] = {}

    def pointer(*parts: str) -> str:
        return JsonPointer.from_parts(parts).path

    for key, value in state.items():
        if key in _ENTRY_SECTIONS and isinstance(value, dict):
            cells[pointer(key)] = {}
            for child, item in value.items():
                cells[pointer(key, child)] = item
        elif key in _MEMBER_LIST_SECTIONS and isinstance(value, list):
            cells[pointer(key)] = []
            for member in value:
                if not isinstance(member, str) or not member:
                    raise AssertionError("workflow membership fact is invalid")
                cells[pointer(key, member)] = True
        elif key == "v3" and isinstance(value, dict):
            cells[pointer(key)] = {}
            for child, item in value.items():
                if child not in _V3_ATOMIC_SECTIONS and isinstance(item, dict):
                    cells[pointer(key, child)] = {}
                    for entry, entry_value in item.items():
                        cells[pointer(key, child, entry)] = entry_value
                else:
                    cells[pointer(key, child)] = item
        else:
            cells[pointer(key)] = value
    return cells


def _cell_changes(
    before: dict[str, Any], after: dict[str, Any]
) -> tuple[tuple[str, Any, bool], ...]:
    old = _cells(before)
    new = _cells(after)
    return tuple(
        (path, new.get(path), path not in new)
        for path in sorted(old.keys() | new.keys())
        if path not in old or path not in new or old[path] != new[path]
    )


def _apply_write_set(
    before: dict[str, Any], write_set: StateWriteSet
) -> dict[str, Any]:
    """Reconstruct state from the explicit source facts using RFC 6902 only here."""

    patch = jsonpatch.JsonPatch(
        [
            (
                {"op": "remove", "path": JsonPointer.from_parts(write.path).path}
                if write.deleted
                else {
                    "op": "add",
                    "path": JsonPointer.from_parts(write.path).path,
                    "value": write.value,
                }
            )
            for write in write_set.writes
        ]
    )
    reconstructed = patch.apply(before)
    for addition in write_set.member_additions:
        pointer = JsonPointer.from_parts(addition.path).path
        members = resolve_pointer(reconstructed, pointer)
        if not isinstance(members, list) or any(
            not isinstance(member, str) for member in members
        ):
            raise AssertionError("workflow member list is invalid")
        set_pointer(
            reconstructed,
            pointer,
            sorted({*members, addition.member}),
            inplace=True,
        )
    return reconstructed


def _cell_changes_from_writes(
    before: dict[str, Any], after: dict[str, Any], write_set: StateWriteSet
) -> tuple[tuple[str, Any, bool], ...]:
    """Project explicit writes to local cells without a full-state diff.

    Section-wide replacements need separate expansion. Refuse them here
    instead of silently scanning the whole run and claiming a local delta.
    """

    affected: set[str] = set()
    for write in write_set.writes:
        path = write.path
        if path[0] in _MEMBER_LIST_SECTIONS:
            raise AssertionError("whole membership list write needs expansion")
        if path[0] in _ENTRY_SECTIONS:
            if len(path) < 2:
                raise AssertionError("section-wide state write needs expansion")
            cell = path[:2]
        elif path[0] == "v3":
            if len(path) < 2:
                raise AssertionError("section-wide v3 write needs expansion")
            section = path[1]
            v3 = after.get("v3", before.get("v3"))
            section_value = v3.get(section) if isinstance(v3, Mapping) else None
            if section not in _V3_ATOMIC_SECTIONS and isinstance(
                section_value, Mapping
            ):
                if len(path) < 3:
                    raise AssertionError("section-wide v3 member write needs expansion")
                cell = path[:3]
            else:
                cell = path[:2]
        else:
            cell = path[:1]
        affected.add(JsonPointer.from_parts(cell).path)
    for addition in write_set.member_additions:
        affected.add(JsonPointer.from_parts((*addition.path, addition.member)).path)

    def cell_value(state: dict[str, Any], pointer: str) -> Any:
        parts = tuple(JsonPointer(pointer).parts)
        if len(parts) == 2 and parts[0] in _MEMBER_LIST_SECTIONS:
            members = state.get(parts[0])
            return (
                True if isinstance(members, list) and parts[1] in members else _ABSENT
            )
        return resolve_pointer(state, pointer, default=_ABSENT)

    changes: list[tuple[str, Any, bool]] = []
    for pointer in sorted(affected):
        old = cell_value(before, pointer)
        new = cell_value(after, pointer)
        if old == new:
            continue
        if new is _ABSENT:
            changes.append((pointer, None, True))
        else:
            changes.append((pointer, json.loads(canonical_json(new)), False))
    return tuple(changes)


class _OfflineCellReplay:
    """SQLite CAS/event proof over captured transitions, never a run journal."""

    def __init__(self, path: Path, initial: dict[str, Any]) -> None:
        self.path = path
        self.connection = sqlite3.connect(path, isolation_level=None)
        self.connection.execute(
            "CREATE TABLE runs (revision INTEGER NOT NULL, status TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE events (sequence INTEGER PRIMARY KEY, type TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE cells (path TEXT PRIMARY KEY, value_json TEXT NOT NULL)"
        )
        self.connection.execute("INSERT INTO runs VALUES (1, 'RUNNING')")
        self.connection.execute("INSERT INTO events VALUES (1, 'RUN_STARTED')")
        self.connection.executemany(
            "INSERT INTO cells VALUES (?, ?)",
            ((path, canonical_json(value)) for path, value in _cells(initial).items()),
        )

    def commit(
        self,
        revision: int,
        status: str,
        event_type: str,
        changes: tuple[tuple[str, Any, bool], ...],
        *,
        fail_after_cells: bool = False,
    ) -> None:
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            updated = connection.execute(
                "UPDATE runs SET revision=?, status=? WHERE revision=?",
                (revision, status, revision - 1),
            )
            if updated.rowcount != 1:
                raise AssertionError("offline cell CAS rejected a stale transition")
            for path, value, deleted in changes:
                if deleted:
                    connection.execute("DELETE FROM cells WHERE path=?", (path,))
                else:
                    connection.execute(
                        "INSERT INTO cells(path, value_json) VALUES (?, ?) "
                        "ON CONFLICT(path) DO UPDATE SET value_json=excluded.value_json",
                        (path, canonical_json(value)),
                    )
            if fail_after_cells:
                raise RuntimeError("injected fault before event write")
            connection.execute(
                "INSERT INTO events(sequence, type) VALUES (?, ?)",
                (revision, event_type),
            )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise

    def read(self) -> dict[str, Any]:
        state: dict[str, Any] = {}
        memberships: dict[str, list[str]] = {key: [] for key in _MEMBER_LIST_SECTIONS}
        rows = self.connection.execute("SELECT path, value_json FROM cells").fetchall()
        for path, serialized in sorted(
            rows, key=lambda row: (row[0].count("/"), row[0])
        ):
            parts = tuple(JsonPointer(path).parts)
            if len(parts) == 2 and parts[0] in _MEMBER_LIST_SECTIONS:
                if json.loads(serialized) is not True:
                    raise AssertionError("workflow membership cell is invalid")
                memberships[parts[0]].append(parts[1])
                continue
            set_pointer(state, path, json.loads(serialized), inplace=True)
        for key, members in memberships.items():
            if key in state:
                state[key] = sorted(members)
        return state

    def reopen(self) -> None:
        self.connection.close()
        self.connection = sqlite3.connect(self.path, isolation_level=None)

    def close(self) -> None:
        self.connection.close()


def inventory(steps: int, *, verify_cells: bool = False) -> dict[str, Any]:
    if steps < 1 or steps > 500:
        raise ValueError("steps must be between 1 and 500")
    fixture = runpy.run_path(str(Path(__file__).with_name("measure_v3_v4_overhead.py")))
    definition = fixture["_definition"](steps, f"mutation-inventory-{steps}")
    executor = LocalToolExecutor()
    tool = fixture["_TOOL"]
    executor.register(tool, lambda arguments: {"value": arguments["value"]})
    totals: Counter[str] = Counter()
    by_section: Counter[str] = Counter()
    by_event: dict[str, Counter[str]] = defaultdict(Counter)
    pending: dict[int, tuple[int, int, tuple[str, ...]]] = {}
    cell_pending: dict[int, tuple[tuple[tuple[str, Any, bool], ...], str]] = {}
    captured: list[tuple[int, str, str, tuple[tuple[str, Any, bool], ...], str]] = []
    explicit_transitions = 0
    explicit_paths = 0
    max_explicit_paths = 0
    initial_state: dict[str, Any] | None = None
    original = RunSnapshot.next_revision

    def observed(self: RunSnapshot, **changes: Any) -> RunSnapshot:
        nonlocal explicit_transitions, explicit_paths, max_explicit_paths
        updated = original(self, **changes)
        if "state" in changes:
            before = self._state_copy()
            after = changes["state"]
            patch = jsonpatch.make_patch(before, after)
            if patch.apply(before) != after:
                raise AssertionError("JSON Patch failed to reconstruct a v4 transition")
            operations = patch.patch
            write_set = updated._state_write_set
            if write_set is not None:
                paths = write_set.paths
                if _apply_write_set(before, write_set) != after:
                    raise AssertionError(
                        "explicit write set failed to reconstruct a v4 transition"
                    )
                for operation in operations:
                    checked = [operation["path"]]
                    if operation["op"] == "move":
                        checked.append(operation["from"])
                    for pointer in checked:
                        parts = tuple(JsonPointer(pointer).parts)
                        if not any(parts[: len(path)] == path for path in paths):
                            raise AssertionError(
                                f"explicit write set missed {pointer} at "
                                f"revision {updated.revision}"
                            )
                explicit_transitions += 1
                explicit_paths += len(paths)
                max_explicit_paths = max(max_explicit_paths, len(paths))
            serialized = canonical_json(after)
            pending[updated.revision] = (
                len(operations),
                len(canonical_json(operations).encode("utf-8")),
                tuple(_section(operation["path"]) for operation in operations),
            )
            totals["full_state_bytes"] += len(serialized.encode("utf-8"))
            if verify_cells:
                if write_set is None:
                    raise AssertionError("cell replay requires an explicit write set")
                changes_from_writes = _cell_changes_from_writes(
                    before, after, write_set
                )
                if changes_from_writes != _cell_changes(before, after):
                    raise AssertionError(
                        "explicit write set missed a local-cell transition"
                    )
                cell_pending[updated.revision] = (
                    changes_from_writes,
                    hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
                )
        return updated

    class ObservedJournal(InMemoryRunJournal):
        def create(self, snapshot, event_type, payload):  # type: ignore[no-untyped-def]
            nonlocal initial_state
            result = super().create(snapshot, event_type, payload)
            if verify_cells:
                initial_state = snapshot._state_copy()
            return result

        def replace(
            self, snapshot, *, expected_revision, event_type, payload, **kwargs
        ):  # type: ignore[no-untyped-def]
            result = super().replace(
                snapshot,
                expected_revision=expected_revision,
                event_type=event_type,
                payload=payload,
                **kwargs,
            )
            operations, patch_bytes, sections = pending.pop(snapshot.revision)
            totals["transitions"] += 1
            totals["patch_operations"] += operations
            totals["patch_bytes"] += patch_bytes
            totals["max_operations_per_transition"] = max(
                totals["max_operations_per_transition"], operations
            )
            counts = by_event[event_type]
            counts["commits"] += 1
            counts["patch_operations"] += operations
            counts["patch_bytes"] += patch_bytes
            counts["max_operations"] = max(counts["max_operations"], operations)
            by_section.update(sections)
            if verify_cells:
                changes, state_digest = cell_pending.pop(snapshot.revision)
                captured.append(
                    (
                        snapshot.revision,
                        snapshot.status,
                        event_type,
                        changes,
                        state_digest,
                    )
                )
            return result

    RunSnapshot.next_revision = observed
    try:
        result = WorkflowEngine(ObservedJournal(), executor, {}).run(
            definition, {"value": steps}, run_id=f"mutation-inventory-{steps}"
        )
    finally:
        RunSnapshot.next_revision = original
    if result.status != "SUCCEEDED":
        raise AssertionError(f"inventory workflow ended in {result.status}")
    if pending:
        raise AssertionError("uncommitted mutation inventory records remained")
    report = {
        "schema_version": "v6-mutation-inventory-v1",
        "steps": steps,
        "status": result.status,
        "revision": result.revision,
        "totals": dict(totals),
        "explicit_write_sets": {
            "transitions_checked": explicit_transitions,
            "paths_recorded": explicit_paths,
            "max_paths_per_transition": max_explicit_paths,
            "all_changed_paths_covered": True,
            "all_revisions_reconstructed": True,
        },
        "changed_sections": dict(sorted(by_section.items())),
        "events": {event: dict(counts) for event, counts in sorted(by_event.items())},
        "scope": (
            "One real v4 local-tool workflow; RFC 6902 diffs are computed only "
            "for development inspection and deliberately excluded from timing"
        ),
    }
    if verify_cells:
        if initial_state is None or cell_pending:
            raise AssertionError(
                "offline cell replay lacks a complete transition stream"
            )
        with tempfile.TemporaryDirectory(prefix="v6-real-state-cells-") as raw:
            replay = _OfflineCellReplay(Path(raw) / "cells.sqlite", initial_state)
            rollback_checked = False
            dispatch_reopened = False
            cell_payload_bytes = 0
            cell_operations = 0
            max_cell_operations = 0
            try:
                for revision, status, event_type, changes, expected_digest in captured:
                    if event_type == "ACTION_DISPATCHED" and not rollback_checked:
                        before = replay.read()
                        try:
                            replay.commit(
                                revision,
                                status,
                                event_type,
                                changes,
                                fail_after_cells=True,
                            )
                        except RuntimeError as error:
                            if str(error) != "injected fault before event write":
                                raise
                        else:
                            raise AssertionError(
                                "offline cell fault injection did not fail"
                            )
                        if replay.read() != before:
                            raise AssertionError(
                                "uncommitted cell changes became visible"
                            )
                        durable_revision = replay.connection.execute(
                            "SELECT revision FROM runs"
                        ).fetchone()[0]
                        if durable_revision != revision - 1:
                            raise AssertionError(
                                "cell revision escaped a rolled-back event"
                            )
                        rollback_checked = True
                    replay.commit(revision, status, event_type, changes)
                    actual = replay.read()
                    actual_digest = hashlib.sha256(
                        canonical_json(actual).encode("utf-8")
                    ).hexdigest()
                    if actual_digest != expected_digest:
                        raise AssertionError(
                            f"offline state cells diverged at revision {revision}"
                        )
                    cell_operations += len(changes)
                    max_cell_operations = max(max_cell_operations, len(changes))
                    cell_payload_bytes += sum(
                        len(canonical_json(value).encode("utf-8"))
                        for _, value, deleted in changes
                        if not deleted
                    )
                    if event_type == "ACTION_DISPATCHED" and not dispatch_reopened:
                        replay.reopen()
                        actions = replay.read().get("actions", {})
                        if not any(
                            isinstance(item, dict)
                            and item.get("status") == "DISPATCHED"
                            for item in actions.values()
                        ):
                            raise AssertionError("dispatched action was lost on reopen")
                        dispatch_reopened = True
                if not rollback_checked or not dispatch_reopened:
                    raise AssertionError("offline replay missed the dispatch boundary")
                if replay.read() != result.state:
                    raise AssertionError(
                        "offline replay final state differs from the run"
                    )
                durable_revision = replay.connection.execute(
                    "SELECT revision FROM runs"
                ).fetchone()[0]
                event_count = replay.connection.execute(
                    "SELECT COUNT(*) FROM events"
                ).fetchone()[0]
                if (
                    durable_revision != result.revision
                    or event_count != result.revision
                ):
                    raise AssertionError(
                        "offline cell revision and event count diverged"
                    )
                try:
                    replay.commit(2, "RUNNING", "STALE", ())
                except AssertionError as error:
                    if str(error) != "offline cell CAS rejected a stale transition":
                        raise
                else:
                    raise AssertionError(
                        "offline cell CAS did not reject a stale write"
                    )
            finally:
                replay.close()
        report["offline_cell_replay"] = {
            "transition_count": len(captured),
            "change_source": "explicit_write_set",
            "cell_operations": cell_operations,
            "max_operations_per_transition": max_cell_operations,
            "cell_payload_bytes": cell_payload_bytes,
            "all_revisions_reconstructed": True,
            "rollback_with_event_atomic": True,
            "dispatch_survived_reopen": True,
            "stale_revision_rejected": True,
            "normalized_member_lists": ["/completed", "/approved_steps"],
            "remaining_whole_list_cells": [],
            "scope": (
                "Offline replay of captured real v4 transitions; full-state "
                "diffing remains diagnostic and is not a production write set"
            ),
        }
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=40)
    parser.add_argument("--verify-cells", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            inventory(args.steps, verify_cells=args.verify_cells), sort_keys=True
        )
    )
