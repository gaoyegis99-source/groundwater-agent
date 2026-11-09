from __future__ import annotations

import time

import pytest

from platform_lightweight_agent import (
    EventCursor,
    LocalToolExecutor,
    RunHandle,
    RunRetentionPolicy,
    SQLiteRunJournal,
    WorkflowEngine,
    approval_step,
    workflow,
)
from platform_lightweight_agent.journal import RunExecutionMismatchError


def _completed(engine: WorkflowEngine, run_id: str):  # type: ignore[no-untyped-def]
    waiting = engine.run(
        workflow(
            "identity",
            "1",
            [approval_step("review", "approve?")],
            schema_version="v6-light-workflow-v1",
        ),
        {},
        run_id=run_id,
    )
    assert waiting.status == "WAITING_APPROVAL"
    completed = engine.resume(run_id, approved=True)
    assert completed.status == "SUCCEEDED"
    return completed


def test_reused_run_id_rejects_old_handle_cursor_and_atomic_transition(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    journal = SQLiteRunJournal(tmp_path / "identity.sqlite")
    engine = WorkflowEngine(journal, LocalToolExecutor(), {})
    original = _completed(engine, "reused-run")
    original_events = engine.events(original.run_id)
    original_cursor = original_events[-1].cursor

    assert original.handle == RunHandle("reused-run", original.execution_id)
    assert EventCursor.from_token(original_cursor.to_token()) == original_cursor
    assert journal.status(original.run_id).execution_id == original.execution_id
    assert all(event.execution_id == original.execution_id for event in original_events)

    retention = journal.apply_retention(
        RunRetentionPolicy(archive_after_seconds=0, purge_after_seconds=0),
        now=time.time() + 60,
    )
    assert retention.purged_run_ids == ("reused-run",)

    replacement = engine.run(
        workflow(
            "identity",
            "1",
            [approval_step("review", "approve?")],
            schema_version="v6-light-workflow-v1",
        ),
        {},
        run_id="reused-run",
    )
    assert replacement.execution_id != original.execution_id

    with pytest.raises(RunExecutionMismatchError):
        engine.cancel(
            replacement.run_id,
            expected_execution_id=original.execution_id,
        )
    assert engine.get(replacement.handle).status == "WAITING_APPROVAL"

    with pytest.raises(RunExecutionMismatchError):
        engine.events(replacement.run_id, cursor=original_cursor)
    with pytest.raises(RunExecutionMismatchError):
        journal.events(
            replacement.run_id,
            after=original_cursor.sequence,
            expected_execution_id=original.execution_id,
        )

    # This mimics a stale worker that read the old snapshot before retention
    # purged it and a caller reused its business name.  The durable SQL update
    # checks the immutable execution identity in the same transaction.
    with pytest.raises(RunExecutionMismatchError):
        journal.replace(
            original.next_revision(state=original.state),
            expected_revision=original.revision,
            event_type="STALE_WORKER",
            payload={},
        )
