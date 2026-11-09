from __future__ import annotations

from pathlib import Path

import pytest

from platform_lightweight_agent import (
    InMemoryRunJournal,
    LocalToolExecutor,
    RunListCursor,
    RunQueryCursorError,
    RunQueryFilter,
    RunQueryUnsupportedError,
    RunRetentionPolicy,
    RunSnapshot,
    SQLiteRunJournal,
    WorkflowEngine,
)


def _snapshot(run_id: str, *, status: str = "RUNNING", state=None) -> RunSnapshot:  # type: ignore[no-untyped-def]
    return RunSnapshot(
        run_id=run_id,
        workflow={
            "schema_version": "v6-light-workflow-v3",
            "workflow_id": "query-flow",
            "version": "1",
            "steps": [],
            "input_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "output_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "output_bindings": {},
            "execution_policy": {"max_concurrency": 1, "run_timeout_seconds": None},
        },
        inputs={},
        state=state or {"v3": {"pending_approvals": {}}},
        status=status,
        driver_bindings={},
    )


@pytest.mark.parametrize("journal_kind", ("memory", "sqlite"))
def test_run_query_is_bounded_paged_and_has_no_business_payload(
    tmp_path: Path, journal_kind: str
) -> None:
    journal = (
        InMemoryRunJournal()
        if journal_kind == "memory"
        else SQLiteRunJournal(tmp_path / "query.sqlite")
    )
    first = journal.create(
        _snapshot("same-time-a", state={"v3": {"pending_approvals": {}}}),
        "RUN_STARTED",
        {},
    )
    second = journal.create(
        _snapshot(
            "same-time-b",
            state={
                "v3": {
                    "pending_approvals": {
                        "approval-1": {"kind": "NODE", "step_key": "review"}
                    }
                }
            },
        ),
        "RUN_STARTED",
        {},
    )
    # Real creation timestamps need not tie for production, but the cursor
    # order includes the run/execution identity exactly so tied rows are safe.
    if journal_kind == "sqlite":
        import sqlite3

        with sqlite3.connect(journal.path) as connection:  # type: ignore[attr-defined]
            connection.execute(
                "UPDATE lightweight_agent_runs SET created_at=?", (10.0,)
            )

    page_one = journal.list_runs(limit=1)
    assert len(page_one.items) == 1
    assert page_one.next_cursor is not None
    assert set(page_one.items[0].to_mapping()) == {
        "run_id",
        "execution_id",
        "workflow_id",
        "workflow_version",
        "status",
        "created_at",
        "updated_at",
        "pending_approval_count",
        "failure_code",
    }
    page_two = journal.list_runs(cursor=page_one.next_cursor, limit=1)
    assert {page_one.items[0].run_id, page_two.items[0].run_id} == {
        first.run_id,
        second.run_id,
    }
    pending = journal.list_runs(RunQueryFilter(pending_only=True))
    assert [item.run_id for item in pending.items] == [second.run_id]
    assert pending.items[0].pending_approval_count == 1
    assert "state" not in pending.to_mapping()["items"][0]


def test_sqlite_run_query_updates_atomically_and_hides_archived(tmp_path: Path) -> None:
    journal = SQLiteRunJournal(tmp_path / "query-projection.sqlite")
    current = journal.create(
        _snapshot("projection", state={"v3": {"pending_approvals": {}}}),
        "RUN_STARTED",
        {},
    )
    before = journal.list_runs().items[0]
    current = journal.replace(
        current.next_revision(
            status="WAITING_APPROVAL",
            state={
                "v3": {
                    "pending_approvals": {
                        "approval-1": {"kind": "NODE", "step_key": "review"}
                    }
                }
            },
        ),
        expected_revision=current.revision,
        event_type="APPROVAL_REQUESTED",
        payload={},
    )
    after = journal.list_runs().items[0]
    assert after.execution_id == current.execution_id
    assert after.status == "WAITING_APPROVAL"
    assert after.pending_approval_count == 1
    assert after.created_at == before.created_at
    assert after.updated_at >= before.updated_at

    journal.replace(
        current.next_revision(status="SUCCEEDED"),
        expected_revision=current.revision,
        event_type="RUN_SUCCEEDED",
        payload={},
    )
    journal.apply_retention(
        RunRetentionPolicy(archive_after_seconds=0),
        now=after.updated_at + 10,
    )
    assert journal.list_runs().items == ()
    assert journal.list_runs(RunQueryFilter(include_archived=True)).items[0].run_id == (
        "projection"
    )


def test_sqlite_run_query_cursor_does_not_skip_old_page_items_after_new_insert(
    tmp_path: Path,
) -> None:
    journal = SQLiteRunJournal(tmp_path / "stable-page.sqlite")
    journal.create(_snapshot("old-a"), "RUN_STARTED", {})
    journal.create(_snapshot("old-b"), "RUN_STARTED", {})
    import sqlite3

    with sqlite3.connect(journal.path) as connection:
        connection.execute(
            "UPDATE lightweight_agent_runs SET created_at=? WHERE run_id='old-a'", (10.0,)
        )
        connection.execute(
            "UPDATE lightweight_agent_runs SET created_at=? WHERE run_id='old-b'", (9.0,)
        )
    first = journal.list_runs(limit=1)
    assert first.items[0].run_id == "old-a"
    journal.create(_snapshot("new"), "RUN_STARTED", {})
    with sqlite3.connect(journal.path) as connection:
        connection.execute(
            "UPDATE lightweight_agent_runs SET created_at=? WHERE run_id='new'", (20.0,)
        )
    following = journal.list_runs(cursor=first.next_cursor, limit=10)
    assert [item.run_id for item in following.items] == ["old-b"]


def test_run_query_cursor_and_optional_capability_have_stable_failures() -> None:
    with pytest.raises(RunQueryCursorError):
        RunListCursor.from_token("not-a-run-cursor")

    class MinimalJournal(InMemoryRunJournal):
        list_runs = None  # type: ignore[assignment]

    engine = WorkflowEngine(MinimalJournal(), LocalToolExecutor(), {})
    with pytest.raises(RunQueryUnsupportedError):
        engine.list_runs()
