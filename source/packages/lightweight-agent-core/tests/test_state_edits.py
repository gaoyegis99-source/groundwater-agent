"""Explicit state writes are local, detached, and credential-free."""

from __future__ import annotations

import pytest

from platform_lightweight_agent.journal import (
    InMemoryRunJournal,
    RunSnapshot,
    SQLiteRunJournal,
)
from platform_lightweight_agent.spec import WorkflowValidationError
from platform_lightweight_agent.state_edits import (
    StateEdit,
    StateMemberAdd,
    StateWrite,
    StateWriteSet,
)


def test_editor_records_only_effective_named_writes_and_detaches_reads() -> None:
    state = {
        "actions": {"a/b~c": {"status": "READY"}},
        "v3": {"step_outputs": {}, "step_states": {}},
        "completed": [],
    }
    edit = StateEdit(state, initial_changed_paths=(("v3", "step_states", "older"),))
    detached = edit.read(("actions", "a/b~c"))
    assert isinstance(detached, dict)
    detached["status"] = "DISPATCHED"
    assert state["actions"]["a/b~c"]["status"] == "READY"

    edit.set(("actions", "a/b~c", "status"), "DISPATCHED")
    edit.set(("v3", "step_outputs", "step/a"), {"value": 7})
    edit.add_sorted_member(("completed",), "step/a")
    edit.add_sorted_member(("completed",), "step/a")
    edit.remove(("v3", "step_states", "absent"), missing_ok=True)
    assert state["actions"]["a/b~c"]["status"] == "DISPATCHED"
    assert state["v3"]["step_outputs"]["step/a"] == {"value": 7}
    assert state["completed"] == ["step/a"]
    assert edit.changed_paths == (
        ("actions", "a/b~c", "status"),
        ("completed",),
        ("v3", "step_outputs", "step/a"),
        ("v3", "step_states", "older"),
    )
    assert edit.write_set().member_additions == (
        StateMemberAdd(("completed",), "step/a"),
    )


def test_editor_rejects_invalid_or_sensitive_writes_before_state_changes() -> None:
    state = {"actions": {}, "v3": {"step_states": {}}}
    edit = StateEdit(state)
    with pytest.raises(WorkflowValidationError, match="credentials"):
        edit.set(("actions", "a"), {"apiKey": "must-not-be-saved"})
    with pytest.raises(WorkflowValidationError, match="parent path"):
        edit.set(("v3", "missing", "a"), "value")
    with pytest.raises(WorkflowValidationError, match="path is invalid"):
        edit.set((), "value")
    with pytest.raises(WorkflowValidationError, match="credentials"):
        edit.set(("actions", "apiKey"), "value")
    with pytest.raises(WorkflowValidationError, match="path is missing"):
        edit.remove(("actions", "a"))
    assert state == {"actions": {}, "v3": {"step_states": {}}}
    assert edit.changed_paths == ()


def test_write_set_is_process_local_and_never_enters_journal_mapping() -> None:
    snapshot = RunSnapshot(
        run_id="write-set-run",
        workflow={"schema_version": "v6-light-workflow-v4"},
        inputs={},
        state={"actions": {}},
        status="RUNNING",
        driver_bindings={},
    )
    journal = InMemoryRunJournal()
    journal.create(snapshot, "RUN_STARTED", {})
    write_set = StateWriteSet(
        (StateWrite(("actions", "a"), False, {"status": "DISPATCHED"}),)
    )
    changed = snapshot.next_revision(
        state={"actions": {"a": {"status": "DISPATCHED"}}},
        _state_write_set=write_set,
    )
    journal.replace(
        changed, expected_revision=1, event_type="ACTION_DISPATCHED", payload={}
    )
    assert changed._state_write_set is write_set
    assert "_state_write_set" not in changed.to_mapping()
    assert journal.get(snapshot.run_id)._state_write_set is None
    assert RunSnapshot.from_mapping(changed.to_mapping())._state_write_set is None

    with pytest.raises(WorkflowValidationError, match="write set is empty"):
        StateWriteSet(())
    with pytest.raises(WorkflowValidationError, match="not canonical"):
        StateWriteSet((StateWrite(("v3",), True), StateWrite(("actions",), True)))
    with pytest.raises(WorkflowValidationError, match="paths overlap"):
        StateWriteSet(
            (
                StateWrite(("v3",), False, {"step_states": {}}),
                StateWrite(("v3", "step_states"), False, {}),
            )
        )


def test_write_set_is_not_restored_from_sqlite(tmp_path) -> None:  # type: ignore[no-untyped-def]
    journal = SQLiteRunJournal(tmp_path / "runs.sqlite")
    snapshot = RunSnapshot(
        run_id="sqlite-write-set",
        workflow={
            "schema_version": "v6-light-workflow-v4",
            "workflow_id": "write-set-workflow",
            "version": "1.0.0",
        },
        inputs={},
        state={"actions": {}},
        status="RUNNING",
        driver_bindings={},
    )
    journal.create(snapshot, "RUN_STARTED", {})
    changed = snapshot.next_revision(
        state={"actions": {"a": {"status": "DISPATCHED"}}},
        _state_write_set=StateWriteSet(
            (StateWrite(("actions", "a"), False, {"status": "DISPATCHED"}),)
        ),
    )
    journal.replace(
        changed, expected_revision=1, event_type="ACTION_DISPATCHED", payload={}
    )

    reopened = SQLiteRunJournal(journal.path).get(snapshot.run_id)
    assert reopened.state == changed.state
    assert reopened._state_write_set is None
    assert reopened.revision == 2


def test_write_set_captures_final_values_and_collapses_parent_child() -> None:
    state = {"actions": {"old": {"status": "READY"}}, "v3": {}}
    edit = StateEdit(state)
    edit.set(("actions", "new"), {"status": "READY"})
    edit.set(("actions", "new", "status"), "DISPATCHED")
    edit.remove(("actions", "old"))
    edit.set(("v3", "execution"), {"generation": 1})
    edit.set(("v3", "execution", "generation"), 2)
    writes = edit.write_set()
    assert writes.paths == (
        ("actions", "new"),
        ("actions", "old"),
        ("v3", "execution"),
    )
    assert writes.writes[0].value == {"status": "DISPATCHED"}
    assert writes.writes[1].deleted
    assert writes.writes[2].value == {"generation": 2}
    state["actions"]["new"]["status"] = "CHANGED_AFTER_CAPTURE"
    assert writes.writes[0].value == {"status": "DISPATCHED"}
    detached = writes.writes[0].value
    assert isinstance(detached, dict)
    detached["status"] = "CHANGED_AFTER_READ"
    assert writes.writes[0].value == {"status": "DISPATCHED"}


def test_member_addition_has_constant_size_delta_and_is_invalidated_by_replace() -> (
    None
):
    state = {"completed": sorted(f"step-{index}" for index in range(100))}
    edit = StateEdit(state)
    edit.add_sorted_member(("completed",), "step-next")
    writes = edit.write_set()
    assert writes.writes == ()
    assert writes.member_additions == (StateMemberAdd(("completed",), "step-next"),)
    assert writes.paths == (("completed",),)

    edit.set(("completed",), ["replaced"])
    replaced = edit.write_set()
    assert replaced.member_additions == ()
    assert replaced.writes[0].value == ["replaced"]

    legacy = StateEdit({"approved_steps": ["z", "z", "a"]})
    legacy.add_sorted_member(("approved_steps",), "z")
    normalized = legacy.write_set()
    assert normalized.member_additions == ()
    assert normalized.writes[0].value == ["a", "z"]
