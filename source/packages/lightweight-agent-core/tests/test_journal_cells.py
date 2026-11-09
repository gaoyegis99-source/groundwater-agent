"""Production SQLite cell layout keeps one transactional run authority."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from platform_lightweight_agent import (
    InMemoryRunJournal,
    LocalToolExecutor,
    RunRetentionPolicy,
    RunSnapshot,
    SQLiteRunJournal,
    WorkflowEngine,
    WorkflowValidationError,
)
from platform_lightweight_agent.journal import (
    RunConflictError,
    RunJournalError,
    _CellCommitHead,
    _CellDeltaUnavailableError,
)
from platform_lightweight_agent.state_edits import (
    ExplicitStateMutation,
    StateEdit,
    StateWrite,
    StateWriteSet,
)
from platform_lightweight_agent.persistent_state import CellStateEdit
from platform_lightweight_agent.workflow_v3 import (
    _Coordinator,
    _RetainedStateTooLargeError,
    _failure_mutation,
    _validation_failure_mutation,
)


def _snapshot(run_id: str, *, version: str = "v6-light-workflow-v4") -> RunSnapshot:
    state = {
        "values": {},
        "completed": [],
        "branches": {},
        "actions": {"existing": {"status": "READY"}},
        "agents": {},
        "approved_steps": [],
        "pending": None,
        "failure": None,
        "v3": {
            "execution": {"generation": 0, "owner_id": None, "lease_expires": None},
            "step_outputs": {},
            "step_states": {},
            "branch_states": {},
            "pending_approvals": {},
            "approval_decisions": {},
            "node_timeouts": {},
            "subworkflow_timeouts": {},
            "step_started_at": {},
            "cancel_requested": False,
            "deadline": None,
            "output": None,
        },
    }
    return RunSnapshot(
        run_id=run_id,
        workflow={
            "schema_version": version,
            "workflow_id": "cell-contract",
            "version": "1",
        },
        inputs={},
        state=state,
        status="RUNNING",
        driver_bindings={},
    )


def _edited(snapshot: RunSnapshot, change) -> RunSnapshot:  # type: ignore[no-untyped-def]
    state = snapshot._state_copy()
    edit = StateEdit(state)
    change(edit)
    return snapshot.next_revision(state=state, _state_write_set=edit.write_set())


@pytest.mark.anyio
async def test_in_memory_cell_commits_defer_full_snapshot_until_public_read() -> None:
    journal = InMemoryRunJournal()
    initial = _snapshot("memory-cell-run").to_mapping()
    initial["state"]["v3"]["execution"] = {
        "generation": 1,
        "owner_id": "local-owner",
        "lease_expires": None,
    }
    created = journal.create(RunSnapshot.from_mapping(initial), "RUN_STARTED", {})
    coordinator = _Coordinator(
        WorkflowEngine(journal, LocalToolExecutor(), {}),
        created,
        owner_id="local-owner",
        generation=1,
    )
    for step_id in ("first", "second"):
        await coordinator.commit(
            "STEP_STARTED",
            {"step_id": step_id},
            ExplicitStateMutation(
                lambda edit, key=step_id: edit.set(
                    ("v3", "step_states", key), "RUNNING"
                )
            ),
        )
    assert journal._snapshots[created.run_id].revision == created.revision
    assert journal.status(created.run_id).revision == created.revision + 2
    assert journal.control(created.run_id).execution_owner_id == "local-owner"
    assert [event.type for event in journal.events(created.run_id)] == [
        "RUN_STARTED",
        "STEP_STARTED",
        "STEP_STARTED",
    ]
    public = journal.get(created.run_id)
    assert public.state["v3"]["step_states"] == {
        "first": "RUNNING",
        "second": "RUNNING",
    }
    public.state["v3"]["step_states"]["first"] = "CALLER_MUTATION"
    assert journal.get(created.run_id).state["v3"]["step_states"]["first"] == (
        "RUNNING"
    )
    terminal = await coordinator.commit(
        "RUN_WAITING_APPROVAL",
        {},
        ExplicitStateMutation(
            lambda edit: edit.set(("v3", "execution", "owner_id"), None)
        ),
        status="WAITING_APPROVAL",
    )
    assert terminal.revision == created.revision + 3
    assert journal.status(created.run_id).status == "WAITING_APPROVAL"
    assert journal._cell_heads == {}


def test_in_memory_full_and_cell_commits_reject_bad_event_atomically() -> None:
    journal = InMemoryRunJournal()
    created = journal.create(_snapshot("memory-atomic"), "RUN_STARTED", {})
    changed = _edited(
        created, lambda edit: edit.set(("v3", "step_states", "first"), "RUNNING")
    )
    with pytest.raises(WorkflowValidationError, match="event type"):
        journal.replace(
            changed,
            expected_revision=created.revision,
            event_type="",
            payload={},
        )
    head = _CellCommitHead.from_snapshot(created)
    edit = CellStateEdit(head.cells)
    edit.set(("v3", "step_states", "first"), "RUNNING")
    with pytest.raises(WorkflowValidationError, match="event type"):
        journal._replace_cell_writes(
            head,
            edit.write_set(),
            status="RUNNING",
            event_type="",
            payload={},
        )
    assert journal.get(created.run_id).revision == created.revision
    assert [event.type for event in journal.events(created.run_id)] == ["RUN_STARTED"]


@pytest.mark.anyio
async def test_coordinator_uses_cell_delta_for_active_owner_then_full_terminal_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = SQLiteRunJournal(tmp_path / "coordinator-cell.sqlite")
    initial = _snapshot("coordinator-cell-run").to_mapping()
    initial["state"]["v3"]["execution"] = {
        "generation": 1,
        "owner_id": "local-owner",
        "lease_expires": None,
    }
    created = journal.create(RunSnapshot.from_mapping(initial), "RUN_STARTED", {})
    fast_events: list[str] = []
    full_events: list[str] = []
    cell_commit = journal._replace_cell_writes
    full_commit = journal.replace

    def record_cell(*args, **kwargs):  # type: ignore[no-untyped-def]
        fast_events.append(kwargs["event_type"])
        return cell_commit(*args, **kwargs)

    def record_full(*args, **kwargs):  # type: ignore[no-untyped-def]
        full_events.append(kwargs["event_type"])
        return full_commit(*args, **kwargs)

    monkeypatch.setattr(journal, "_replace_cell_writes", record_cell)
    monkeypatch.setattr(journal, "replace", record_full)
    coordinator = _Coordinator(
        WorkflowEngine(journal, LocalToolExecutor(), {}),
        created,
        owner_id="local-owner",
        generation=1,
    )
    await coordinator.commit(
        "STEP_STARTED",
        {"step_id": "first"},
        ExplicitStateMutation(
            lambda edit: edit.set(("v3", "step_states", "first"), "RUNNING")
        ),
    )
    assert (await coordinator.state())["v3"]["step_states"] == {"first": "RUNNING"}
    completed = await coordinator.commit(
        "RUN_WAITING_APPROVAL",
        {},
        ExplicitStateMutation(
            lambda edit: edit.set(("v3", "execution", "owner_id"), None)
        ),
        status="WAITING_APPROVAL",
    )

    assert fast_events == ["STEP_STARTED"]
    assert full_events == ["RUN_WAITING_APPROVAL"]
    assert completed.revision == created.revision + 2
    assert SQLiteRunJournal(journal.path).get(created.run_id).state == completed.state
    assert [event.type for event in journal.events(created.run_id)] == [
        "RUN_STARTED",
        "STEP_STARTED",
        "RUN_WAITING_APPROVAL",
    ]


@pytest.mark.anyio
async def test_cell_delta_budget_falls_back_to_exact_check_without_committing(
    tmp_path: Path,
) -> None:
    journal = SQLiteRunJournal(tmp_path / "cell-limit.sqlite")
    initial = _snapshot("cell-limit-run").to_mapping()
    initial["state"]["v3"]["execution"] = {
        "generation": 1,
        "owner_id": "local-owner",
        "lease_expires": None,
    }
    initial["state"]["v3"]["max_retained_state_bytes"] = 1
    created = journal.create(RunSnapshot.from_mapping(initial), "RUN_STARTED", {})
    coordinator = _Coordinator(
        WorkflowEngine(journal, LocalToolExecutor(), {}),
        created,
        owner_id="local-owner",
        generation=1,
    )

    with pytest.raises(_RetainedStateTooLargeError):
        await coordinator.commit(
            "STEP_STARTED",
            {"step_id": "first"},
            ExplicitStateMutation(
                lambda edit: edit.set(("v3", "step_states", "first"), "RUNNING")
            ),
            enforce_retained_budget=True,
        )
    assert journal.get(created.run_id).revision == created.revision
    assert [event.type for event in journal.events(created.run_id)] == ["RUN_STARTED"]


def test_v4_cell_layout_is_authoritative_and_legacy_layout_is_unchanged(
    tmp_path: Path,
) -> None:
    journal = SQLiteRunJournal(tmp_path / "runs.sqlite")
    current = journal.create(_snapshot("cell-run"), "RUN_STARTED", {})
    legacy = journal.create(
        _snapshot("legacy-run", version="v6-light-workflow-v1"),
        "RUN_STARTED",
        {},
    )
    with sqlite3.connect(journal.path) as connection:
        rows = connection.execute(
            "SELECT run_id, state_layout, snapshot_json FROM lightweight_agent_runs "
            "ORDER BY run_id"
        ).fetchall()
        cell_count = connection.execute(
            "SELECT COUNT(*) FROM lightweight_agent_state_cells WHERE run_id='cell-run'"
        ).fetchone()[0]
    layouts = {row[0]: (row[1], json.loads(row[2])) for row in rows}
    assert layouts["cell-run"][0] == "cells-v1"
    assert layouts["cell-run"][1]["state"] == {}
    assert layouts["legacy-run"][0] == "snapshot"
    assert layouts["legacy-run"][1]["state"]
    assert cell_count > 10

    current.state["actions"]["existing"]["status"] = "CALLER_MUTATION"
    assert journal.get(current.run_id).state["actions"]["existing"]["status"] == "READY"
    assert journal.get(legacy.run_id).state == legacy.state
    assert journal.storage_info().schema_version == 7


def test_existing_v6_journal_migrates_without_rewriting_old_runs(
    tmp_path: Path,
) -> None:
    path = tmp_path / "previous-version.sqlite"
    journal = SQLiteRunJournal(path)
    old = journal.create(
        _snapshot("old-run", version="v6-light-workflow-v3"), "RUN_STARTED", {}
    )
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE lightweight_agent_state_cells")
        connection.execute(
            "ALTER TABLE lightweight_agent_runs DROP COLUMN state_layout"
        )
        connection.execute(
            "DELETE FROM lightweight_agent_journal_migrations WHERE version=7"
        )
        connection.execute(
            "UPDATE lightweight_agent_journal_meta SET value='6' "
            "WHERE key='schema_version'"
        )

    upgraded = SQLiteRunJournal(path)
    assert upgraded.get(old.run_id).state == old.state
    with sqlite3.connect(path) as connection:
        layout = connection.execute(
            "SELECT state_layout FROM lightweight_agent_runs WHERE run_id=?",
            (old.run_id,),
        ).fetchone()[0]
    assert layout == "snapshot"
    assert upgraded.storage_info().schema_version == 7


def test_cell_delta_is_atomic_with_cas_and_event_and_never_clears_unchanged_cells(
    tmp_path: Path,
) -> None:
    journal = SQLiteRunJournal(tmp_path / "atomic.sqlite")
    first = journal.create(_snapshot("atomic-cell-run"), "RUN_STARTED", {})
    with sqlite3.connect(journal.path) as connection:
        connection.execute(
            "CREATE TRIGGER forbid_cell_clear BEFORE DELETE ON "
            "lightweight_agent_state_cells BEGIN "
            "SELECT RAISE(ABORT, 'whole-cell-clear'); END"
        )
    changed = _edited(
        first,
        lambda edit: (
            edit.set(("actions", "existing", "status"), "DISPATCHED"),
            edit.add_sorted_member(("completed",), "step-one"),
            edit.set(("v3", "step_outputs", "step-one"), {"value": 7}),
        ),
    )
    saved = journal.replace(
        changed,
        expected_revision=first.revision,
        event_type="ACTION_DISPATCHED",
        payload={"action_id": "existing"},
    )
    reopened = SQLiteRunJournal(journal.path)
    assert reopened.get(saved.run_id).state == saved.state
    assert [event.type for event in reopened.events(saved.run_id)] == [
        "RUN_STARTED",
        "ACTION_DISPATCHED",
    ]
    with pytest.raises(RunConflictError):
        reopened.replace(
            changed,
            expected_revision=first.revision,
            event_type="STALE",
            payload={},
        )
    assert reopened.get(saved.run_id).revision == saved.revision

    with sqlite3.connect(journal.path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_event BEFORE INSERT ON lightweight_agent_events "
            "WHEN NEW.type='FAULT' BEGIN SELECT RAISE(ABORT, 'event-fault'); END"
        )
    faulted = _edited(
        saved, lambda edit: edit.set(("actions", "existing", "status"), "SUCCEEDED")
    )
    with pytest.raises(RunJournalError, match="could not commit"):
        reopened.replace(
            faulted,
            expected_revision=saved.revision,
            event_type="FAULT",
            payload={},
        )
    assert SQLiteRunJournal(journal.path).get(saved.run_id).state == saved.state
    assert len(reopened.events(saved.run_id)) == 2


def test_private_cell_commit_reuses_the_same_cas_and_event_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import platform_lightweight_agent.journal as journal_module

    journal = SQLiteRunJournal(tmp_path / "local-delta.sqlite")
    first = journal.create(_snapshot("local-delta-run"), "RUN_STARTED", {})
    head = _CellCommitHead.from_snapshot(first)
    edited = first._state_copy()
    edit = StateEdit(edited)
    edit.set(("actions", "existing", "status"), "DISPATCHED")
    edit.add_sorted_member(("completed",), "first")
    edit.set(("v3", "step_outputs", "first"), {"value": 1})
    writes = edit.write_set()
    original_validate = journal_module._validate_checkpoint_object

    def forbid_full_state(value, name):  # type: ignore[no-untyped-def]
        if name == "state":
            raise AssertionError("local commit constructed a full snapshot")
        return original_validate(value, name)

    monkeypatch.setattr(
        journal_module, "_validate_checkpoint_object", forbid_full_state
    )
    second_head = journal._replace_cell_writes(
        head,
        writes,
        status="RUNNING",
        event_type="ACTION_DISPATCHED",
        payload={"action_id": "existing"},
    )
    monkeypatch.setattr(
        journal_module, "_validate_checkpoint_object", original_validate
    )
    assert second_head.revision == first.revision + 1
    assert second_head.cells.view()["actions"]["existing"]["status"] == "DISPATCHED"
    assert head.cells.view()["actions"]["existing"]["status"] == "READY"
    reopened = SQLiteRunJournal(journal.path)
    assert reopened.get(first.run_id).state == edited
    assert [event.type for event in reopened.events(first.run_id)] == [
        "RUN_STARTED",
        "ACTION_DISPATCHED",
    ]
    with pytest.raises(RunConflictError):
        reopened._replace_cell_writes(
            head,
            writes,
            status="RUNNING",
            event_type="STALE",
            payload={},
        )
    assert len(reopened.events(first.run_id)) == 2

    with sqlite3.connect(journal.path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_local_event BEFORE INSERT ON lightweight_agent_events "
            "WHEN NEW.type='FAULT' BEGIN SELECT RAISE(ABORT, 'event-fault'); END"
        )
    next_state = reopened.get(first.run_id)._state_copy()
    next_edit = StateEdit(next_state)
    next_edit.set(("actions", "existing", "status"), "SUCCEEDED")
    with pytest.raises(RunJournalError, match="could not commit"):
        reopened._replace_cell_writes(
            second_head,
            next_edit.write_set(),
            status="RUNNING",
            event_type="FAULT",
            payload={},
        )
    assert reopened.get(first.run_id).state == edited
    assert len(reopened.events(first.run_id)) == 2
    final_head = reopened._replace_cell_writes(
        second_head,
        next_edit.write_set(),
        status="SUCCEEDED",
        event_type="ACTION_SUCCEEDED",
        payload={},
    )
    assert final_head.revision == 3
    assert reopened.status(first.run_id).status == "SUCCEEDED"
    assert reopened.get(first.run_id).state == next_state
    assert [event.type for event in reopened.events(first.run_id)] == [
        "RUN_STARTED",
        "ACTION_DISPATCHED",
        "ACTION_SUCCEEDED",
    ]


def test_private_cell_commit_declines_untrusted_writes_without_mutation(
    tmp_path: Path,
) -> None:
    journal = SQLiteRunJournal(tmp_path / "local-untrusted.sqlite")
    first = journal.create(_snapshot("local-untrusted-run"), "RUN_STARTED", {})
    head = _CellCommitHead.from_snapshot(first)
    writes = StateWriteSet(
        (StateWrite(("actions", "existing", "status"), False, "DISPATCHED"),)
    )
    with pytest.raises(_CellDeltaUnavailableError):
        journal._replace_cell_writes(
            head,
            writes,
            status="RUNNING",
            event_type="SPOOFED",
            payload={},
        )
    assert journal.get(first.run_id).revision == 1
    assert [event.type for event in journal.events(first.run_id)] == ["RUN_STARTED"]


@pytest.mark.anyio
async def test_common_failure_transitions_update_only_the_failure_cell(
    tmp_path: Path,
) -> None:
    journal = SQLiteRunJournal(tmp_path / "failures.sqlite")
    first = journal.create(_snapshot("failure-cell-run"), "RUN_STARTED", {})
    with sqlite3.connect(journal.path) as connection:
        connection.execute(
            "CREATE TRIGGER forbid_failure_cell_clear BEFORE DELETE ON "
            "lightweight_agent_state_cells BEGIN "
            "SELECT RAISE(ABORT, 'whole-cell-clear'); END"
        )
    coordinator = _Coordinator(WorkflowEngine(journal, LocalToolExecutor(), {}), first)
    failed = await coordinator.commit(
        "STEP_FAILED", {"step_id": "one"}, _failure_mutation("STEP_FAILED", "one")
    )
    assert failed.state["failure"] == {"code": "STEP_FAILED", "step_id": "one"}
    invalid = await coordinator.commit(
        "STEP_OUTPUT_INVALID",
        {"step_id": "two", "path": "/value", "keyword": "type"},
        _validation_failure_mutation("STEP_OUTPUT_INVALID", "two", ("/value", "type")),
    )
    assert journal.get(invalid.run_id).state["failure"] == {
        "code": "STEP_OUTPUT_INVALID",
        "step_id": "two",
        "path": "/value",
        "keyword": "type",
    }
    assert len(journal.events(invalid.run_id)) == 3

    # Composite callbacks still use the conservative full-state path, but
    # calling a common failure helper against their detached state works.
    detached = invalid._state_copy()
    _failure_mutation("RUN_TIMEOUT")(detached)
    assert detached["failure"] == {"code": "RUN_TIMEOUT"}


def test_untrusted_or_section_wide_cell_writes_take_validated_full_fallback(
    tmp_path: Path,
) -> None:
    journal = SQLiteRunJournal(tmp_path / "fallback.sqlite")
    first = journal.create(_snapshot("fallback-run"), "RUN_STARTED", {})
    state = first._state_copy()
    state["actions"]["existing"]["status"] = "DISPATCHED"
    untrusted = first.next_revision(
        state=state,
        _state_write_set=StateWriteSet(
            (StateWrite(("actions", "existing", "status"), False, "DISPATCHED"),)
        ),
    )
    second = journal.replace(
        untrusted,
        expected_revision=first.revision,
        event_type="UNTRUSTED",
        payload={},
    )
    assert journal.get(first.run_id).state == second.state

    third = _edited(
        second,
        lambda edit: edit.set(("actions",), {"replacement": {"status": "READY"}}),
    )
    journal.replace(
        third,
        expected_revision=second.revision,
        event_type="SECTION_REPLACED",
        payload={},
    )
    assert journal.get(first.run_id).state == third.state
    with sqlite3.connect(journal.path) as connection:
        actions = connection.execute(
            "SELECT path_json FROM lightweight_agent_state_cells "
            "WHERE run_id=? AND path_json LIKE '%actions%' ORDER BY path_json",
            (first.run_id,),
        ).fetchall()
    assert {tuple(json.loads(row[0])) for row in actions} == {
        ("actions",),
        ("actions", "replacement"),
    }


def test_cell_outputs_keep_content_addressing_and_purge_without_orphans(
    tmp_path: Path,
) -> None:
    journal = SQLiteRunJournal(tmp_path / "values.sqlite")
    initial = _snapshot("cell-values")
    state = initial._state_copy()
    large = {"value": "x" * 1024}
    state["v3"]["step_outputs"]["step-one"] = large
    state["v3"]["output"] = large
    state["actions"]["existing"]["result"] = {"output": large}
    completed = RunSnapshot(
        run_id=initial.run_id,
        workflow=initial.workflow,
        inputs=initial.inputs,
        state=state,
        status="SUCCEEDED",
        driver_bindings=initial.driver_bindings,
    )
    journal.create(completed, "RUN_SUCCEEDED", {})
    with sqlite3.connect(journal.path) as connection:
        compact = connection.execute(
            "SELECT snapshot_json FROM lightweight_agent_runs WHERE run_id=?",
            (completed.run_id,),
        ).fetchone()[0]
        digests = connection.execute(
            "SELECT value_digest FROM lightweight_agent_state_cells "
            "WHERE run_id=? AND path_json IN (?, ?)",
            (
                completed.run_id,
                '["v3","output"]',
                '["v3","step_outputs","step-one"]',
            ),
        ).fetchall()
    assert "x" * 1024 not in compact
    assert len(digests) == 2 and digests[0][0] == digests[1][0]
    assert journal.get(completed.run_id).state == completed.state
    assert journal.view_source(completed.run_id).output == large
    assert journal.inspection(completed.run_id).status == "SUCCEEDED"

    purged = journal.apply_retention(
        RunRetentionPolicy(archive_after_seconds=0, purge_after_seconds=0),
        now=10_000_000_000,
    )
    assert purged.purged_run_ids == (completed.run_id,)
    with sqlite3.connect(journal.path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM lightweight_agent_state_cells"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM lightweight_agent_values"
            ).fetchone()[0]
            == 0
        )


def test_cell_inspection_does_not_hydrate_retained_business_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import platform_lightweight_agent.journal as journal_module

    journal = SQLiteRunJournal(tmp_path / "inspection-cells.sqlite")
    initial = _snapshot("inspection-cells")
    state = initial._state_copy()
    large = {"value": "x" * 4096}
    state["v3"]["step_outputs"]["write"] = large
    state["v3"]["output"] = large
    state["v3"]["step_states"]["write"] = "COMPLETED"
    state["actions"]["existing"] = {
        "status": "RESOLVED",
        "step_key": "write",
        "request": {
            "tool": {
                "tool_id": "example.write",
                "version": "1",
                "package_digest": "sha256:" + "b" * 64,
            },
            "arguments": large,
        },
        "result": {"output": large},
    }
    saved = journal.create(
        RunSnapshot(
            run_id=initial.run_id,
            workflow=initial.workflow,
            inputs=initial.inputs,
            state=state,
            status="SUCCEEDED",
            driver_bindings=initial.driver_bindings,
        ),
        "RUN_SUCCEEDED",
        {},
    )
    original = journal_module._read_values

    def forbidden(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        raise AssertionError("inspection hydrated retained business values")

    monkeypatch.setattr(journal_module, "_read_values", forbidden)
    inspection = journal.inspection(saved.run_id)
    assert inspection.state["actions"]["existing"]["result"] == {}
    assert (
        inspection.state["actions"]["existing"]["request"]["tool"]["tool_id"]
        == "example.write"
    )
    assert inspection.state["v3"]["step_states"] == {"write": "COMPLETED"}
    assert "step_outputs" not in inspection.state["v3"]
    monkeypatch.setattr(journal_module, "_read_values", original)
    assert journal.view_source(saved.run_id).output == large


def test_missing_or_corrupt_cell_fails_closed(tmp_path: Path) -> None:
    journal = SQLiteRunJournal(tmp_path / "corrupt.sqlite")
    saved = journal.create(_snapshot("corrupt-cell-run"), "RUN_STARTED", {})
    with sqlite3.connect(journal.path) as connection:
        connection.execute(
            "UPDATE lightweight_agent_state_cells SET value_digest=? , value_json=NULL "
            "WHERE run_id=? AND path_json=?",
            ("sha256:" + "0" * 64, saved.run_id, '["actions","existing"]'),
        )
    with pytest.raises(RunJournalError, match="record is missing"):
        journal.get(saved.run_id)


def test_cell_schema_without_run_cascade_is_rejected_on_open(tmp_path: Path) -> None:
    path = tmp_path / "damaged-cell-schema.sqlite"
    SQLiteRunJournal(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE lightweight_agent_state_cells")
        connection.execute(
            "CREATE TABLE lightweight_agent_state_cells ("
            "run_id TEXT NOT NULL, path_json TEXT NOT NULL, value_json TEXT, "
            "value_digest TEXT, PRIMARY KEY (run_id, path_json))"
        )
    with pytest.raises(RunJournalError, match="state cell references"):
        SQLiteRunJournal(path)
