from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

import pytest

from platform_lightweight_agent.persistent_state import (
    CellStateEdit,
    PersistentCellState,
)
from platform_lightweight_agent.state_edits import StateEdit


def _state() -> dict[str, object]:
    return {
        "actions": {
            "action-z": {"status": "READY"},
            "action-a": {"status": "READY"},
        },
        "agents": {},
        "branches": {},
        "completed": ["first"],
        "approved_steps": [],
        "values": {},
        "pending": None,
        "failure": None,
        "v3": {
            "step_outputs": {"first": {"value": 1}},
            "step_states": {"first": "COMPLETED"},
            "pending_approvals": {},
            "cancel_requested": False,
            "output": None,
        },
    }


def _materialize(value: object, *, section: str = "") -> object:
    if section in {"completed", "approved_steps"} and isinstance(value, Mapping):
        return sorted(value)
    if isinstance(value, Mapping):
        return {key: _materialize(item, section=key) for key, item in value.items()}
    if isinstance(value, list):
        return [_materialize(item) for item in value]
    return value


def test_persistent_cells_keep_old_revision_and_decode_only_requested_entry() -> None:
    state = _state()
    before = PersistentCellState.from_state(state)
    old_view = before.view()
    edited = deepcopy(state)
    edit = StateEdit(edited)
    edit.set(("actions", "action-a", "status"), "RESOLVED")
    edit.set(("v3", "step_outputs", "second"), {"value": 2})
    edit.add_sorted_member(("completed",), "second")
    after = before.after_commit(edited, edit.write_set()).view()

    assert old_view["actions"]["action-a"]["status"] == "READY"
    assert old_view["v3"]["step_outputs"].get("second") is None
    assert "second" not in old_view["completed"]
    assert after["actions"]["action-a"]["status"] == "RESOLVED"
    assert list(after["actions"]) == ["action-a", "action-z"]
    assert after["v3"]["step_outputs"]["second"] == {"value": 2}
    assert "second" in after["completed"]
    assert set(after) == set(state)
    assert _materialize(old_view) == state
    assert _materialize(after) == edited
    with pytest.raises(TypeError):
        after["v3"]["step_outputs"]["third"] = {"value": 3}
    detached = after["actions"]["action-a"]
    detached["status"] = "SPOOFED"
    assert after["actions"]["action-a"]["status"] == "RESOLVED"


def test_persistent_cells_rebuild_when_transition_has_no_local_write_set() -> None:
    before = PersistentCellState.from_state(_state())
    changed = _state()
    changed["failure"] = {"code": "FAILED"}
    after = before.after_commit(changed, None)
    assert before.view()["failure"] is None
    assert after.view()["failure"] == {"code": "FAILED"}


def test_editor_writes_project_directly_without_a_full_state_copy() -> None:
    state = _state()
    before = PersistentCellState.from_state(state)
    edited = deepcopy(state)
    edit = StateEdit(edited)
    edit.set(("actions", "action-a", "status"), "DISPATCHED")
    edit.set(("actions", "action-a", "result"), {"value": 7})
    edit.remove(("v3", "step_outputs", "first"))
    edit.set(("v3", "step_outputs", "second"), {"value": 2})
    edit.add_sorted_member(("completed",), "second")
    projected = before.apply_write_set(edit.write_set())

    assert projected is not None
    assert _materialize(projected.view()) == edited
    assert projected.to_state() == edited
    assert _materialize(before.view()) == state


def test_direct_projection_declines_whole_section_or_untrusted_writes() -> None:
    state = _state()
    before = PersistentCellState.from_state(state)
    edited = deepcopy(state)
    edit = StateEdit(edited)
    edit.set(("actions",), {})
    assert before.apply_write_set(edit.write_set()) is None

    from platform_lightweight_agent.state_edits import StateWrite, StateWriteSet

    untrusted = StateWriteSet((StateWrite(("failure",), False, {"code": "SPOOFED"}),))
    assert before.apply_write_set(untrusted) is None


def test_direct_projection_preserves_escaped_member_names() -> None:
    state = _state()
    edited = deepcopy(state)
    key = "step/~区"
    edit = StateEdit(edited)
    edit.set(("v3", "step_outputs", key), {"a/b~c": [1, 2]})
    edit.add_sorted_member(("completed",), key)

    projected = PersistentCellState.from_state(state).apply_write_set(edit.write_set())
    assert projected is not None
    assert _materialize(projected.view()) == edited


def test_direct_projection_declines_noncanonical_legacy_membership() -> None:
    state = _state()
    state["completed"] = ["z", "a"]
    edited = deepcopy(state)
    edit = StateEdit(edited)
    edit.add_sorted_member(("completed",), "b")

    assert (
        PersistentCellState.from_state(state).apply_write_set(edit.write_set()) is None
    )


def test_sparse_cell_editor_reuses_state_edit_contract_without_full_state() -> None:
    state = _state()
    cells = PersistentCellState.from_state(state)
    edit = CellStateEdit(cells)
    action = edit.read(("actions", "action-a"))
    action["status"] = "DISPATCHED"
    edit.set(("actions", "action-a"), action)
    edit.set(("v3", "step_outputs", "second"), {"value": 2})
    edit.remove(("v3", "step_outputs", "first"))
    edit.add_sorted_member(("completed",), "second")
    assert edit.read(("v3", "step_outputs", "second")) == {"value": 2}
    assert edit.changed_paths == (
        ("actions", "action-a"),
        ("completed",),
        ("v3", "step_outputs", "first"),
        ("v3", "step_outputs", "second"),
    )
    projected = cells.apply_write_set(edit.write_set())
    assert projected is not None
    expected = deepcopy(state)
    expected["actions"]["action-a"]["status"] = "DISPATCHED"
    expected["v3"]["step_outputs"] = {"second": {"value": 2}}
    expected["completed"] = ["first", "second"]
    assert _materialize(projected.view()) == expected
    assert _materialize(cells.view()) == state
