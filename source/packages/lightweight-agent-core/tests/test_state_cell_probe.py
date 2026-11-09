"""Development-only proof that real v4 transitions have recoverable cells.

The probe is not a production journal or a change-set generator. It replays
already-captured transitions offline with the pinned RFC 6902 diagnostic
dependency and SQLite's own transaction/CAS machinery.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import runpy

import pytest

from platform_lightweight_agent import (
    AgentBudget,
    AgentDecision,
    AgentToolSpec,
    ApprovalDecision,
    InMemoryRunJournal,
    LocalToolExecutor,
    NodeContract,
    ParallelBranch,
    ScriptedAgentDriver,
    ToolCall,
    ToolRecoveryCapabilities,
    ToolReference,
    ValueBinding,
    WorkflowEngine,
    WorkflowExecutionPolicy,
    agent_step,
    approval_step,
    parallel_step,
    subworkflow_step,
    tool_step,
    workflow_v4,
)
from platform_lightweight_agent.journal import _state_cells
from platform_lightweight_agent.persistent_state import PersistentCellState
from platform_lightweight_agent.state_edits import StateEdit

_probe = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts" / "inventory_state_mutations.py")
)
_OfflineCellReplay = _probe["_OfflineCellReplay"]
_cell_changes = _probe["_cell_changes"]
_cell_changes_from_writes = _probe["_cell_changes_from_writes"]
_apply_write_set = _probe["_apply_write_set"]
inventory = _probe["inventory"]


class _CheckingJournal(InMemoryRunJournal):
    def __init__(self, tracked: set[str]) -> None:
        super().__init__()
        self.tracked = tracked
        self.checked: set[str] = set()

    def replace(  # type: ignore[no-untyped-def]
        self, snapshot, *, expected_revision, event_type, payload, **kwargs
    ):
        if event_type in self.tracked:
            previous = self.get(snapshot.run_id)
            write_set = snapshot._state_write_set
            assert write_set is not None
            assert _apply_write_set(previous.state, write_set) == snapshot.state
            projected = PersistentCellState.from_state(
                previous._state_copy()
            ).apply_write_set(write_set)
            assert projected is not None
            assert dict(projected._cells) == _state_cells(snapshot._state_copy())
            assert _cell_changes_from_writes(
                previous.state, snapshot.state, write_set
            ) == _cell_changes(previous.state, snapshot.state)
            self.checked.add(event_type)
        return super().replace(
            snapshot,
            expected_revision=expected_revision,
            event_type=event_type,
            payload=payload,
            **kwargs,
        )


def test_real_v4_cell_replay_keeps_dispatch_event_and_revision_atomic() -> None:
    report = inventory(8, verify_cells=True)
    replay = report["offline_cell_replay"]
    assert report["status"] == "SUCCEEDED"
    assert replay["transition_count"] == report["totals"]["transitions"]
    assert (
        report["explicit_write_sets"]["transitions_checked"]
        == replay["transition_count"]
    )
    assert report["explicit_write_sets"]["all_changed_paths_covered"]
    assert report["explicit_write_sets"]["all_revisions_reconstructed"]
    assert replay["max_operations_per_transition"] <= 5
    assert replay["cell_payload_bytes"] < report["totals"]["full_state_bytes"]
    assert replay["all_revisions_reconstructed"]
    assert replay["change_source"] == "explicit_write_set"
    assert replay["normalized_member_lists"] == ["/completed", "/approved_steps"]
    assert replay["remaining_whole_list_cells"] == []
    assert replay["rollback_with_event_atomic"]
    assert replay["dispatch_survived_reopen"]
    assert replay["stale_revision_rejected"]


def test_cell_partition_preserves_escaped_keys_and_deletions(tmp_path) -> None:  # type: ignore[no-untyped-def]
    initial = {
        "actions": {"a/b~c": {"status": "DISPATCHED"}},
        "completed": [],
        "v3": {
            "execution": {"generation": 1},
            "output": None,
            "pending_approvals": {"approval/x~": {"step_id": "s"}},
        },
    }
    updated = {
        "actions": {"a/b~c": {"status": "SUCCEEDED"}},
        "completed": ["s"],
        "v3": {
            "execution": {"generation": 1},
            "output": None,
            "pending_approvals": {},
        },
    }
    working = deepcopy(initial)
    edit = StateEdit(working)
    edit.set(("actions", "a/b~c", "status"), "SUCCEEDED")
    edit.add_sorted_member(("completed",), "s")
    edit.remove(("v3", "pending_approvals", "approval/x~"))
    assert working == updated
    changes = _cell_changes_from_writes(initial, working, edit.write_set())
    assert changes == _cell_changes(initial, updated)
    replay = _OfflineCellReplay(tmp_path / "cells.sqlite", initial)
    try:
        assert replay.read() == initial
        replay.commit(2, "SUCCEEDED", "STEP_COMPLETED", changes)
        replay.reopen()
        assert replay.read() == updated
    finally:
        replay.close()


@pytest.mark.anyio
async def test_approval_and_cancel_writes_reconstruct_durable_state() -> None:
    tracked = {
        "APPROVAL_REQUESTED",
        "APPROVALS_RECORDED",
        "CANCELLATION_REQUESTED",
        "STEP_TIMEOUT_PAUSED",
    }

    empty = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    approved = {
        **empty,
        "properties": {"approved": {"type": "boolean"}},
        "required": ["approved"],
        "additionalProperties": False,
    }
    definition = workflow_v4(
        "write-set-approval",
        "1",
        (
            approval_step(
                "review",
                "Approve?",
                contract=NodeContract(empty, approved, timeout_seconds=5.0),
            ),
        ),
        input_schema=empty,
        output_schema=approved,
        output_bindings={
            "/approved": ValueBinding("step_output", "/approved", "review")
        },
    )
    journal = _CheckingJournal(tracked)
    engine = WorkflowEngine(journal, LocalToolExecutor(), {})
    waiting = await engine.run_async(definition, {}, run_id="write-set-approved")
    approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))
    completed = await engine.resume_async(
        waiting.run_id, approvals=(ApprovalDecision(approval_id, True),)
    )
    assert completed.status == "SUCCEEDED"

    waiting = await engine.run_async(definition, {}, run_id="write-set-cancelled")
    requested = await engine.request_cancel_async(waiting.run_id)
    assert requested.status == "CANCEL_REQUESTED"
    assert journal.checked == tracked


@pytest.mark.anyio
async def test_parallel_branch_writes_reconstruct_durable_state() -> None:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    joined = {
        **schema,
        "properties": {"left": schema, "right": schema},
        "required": ["left", "right"],
    }
    reference = ToolReference("write-set.echo", "1", "sha256:" + "e" * 64)

    def leaf(step_id: str):  # type: ignore[no-untyped-def]
        return tool_step(
            step_id,
            reference,
            approval_required=False,
            contract=NodeContract(
                schema,
                schema,
                {"/value": ValueBinding("workflow_input", "/value")},
            ),
            capabilities=ToolRecoveryCapabilities(external_side_effects=False),
        )

    definition = workflow_v4(
        "write-set-parallel",
        "1",
        (
            parallel_step(
                "fanout",
                (
                    ParallelBranch("left", (leaf("left-leaf"),)),
                    ParallelBranch("right", (leaf("right-leaf"),)),
                ),
                contract=NodeContract(
                    schema,
                    joined,
                    {"/value": ValueBinding("workflow_input", "/value")},
                ),
            ),
        ),
        input_schema=schema,
        output_schema=schema,
        output_bindings={
            "/value": ValueBinding("step_output", "/left/value", "fanout")
        },
    )
    executor = LocalToolExecutor()
    executor.register(reference, lambda arguments: {"value": arguments["value"]})
    tracked = {"PARALLEL_FAN_OUT", "PARALLEL_BRANCH_SETTLED"}
    journal = _CheckingJournal(tracked)
    completed = await WorkflowEngine(journal, executor, {}).run_async(
        definition, {"value": 7}
    )
    assert completed.status == "SUCCEEDED"
    assert completed.state["v3"]["output"] == {"value": 7}
    assert journal.checked == tracked


@pytest.mark.anyio
async def test_subworkflow_deadline_writes_reconstruct_durable_state() -> None:
    empty = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    output = {
        **empty,
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
    }
    reference = ToolReference("write-set.child", "1", "sha256:" + "c" * 64)
    child = workflow_v4(
        "write-set-child",
        "1",
        (
            tool_step(
                "emit",
                reference,
                approval_required=False,
                contract=NodeContract(empty, output),
                capabilities=ToolRecoveryCapabilities(external_side_effects=False),
            ),
        ),
        input_schema=empty,
        output_schema=output,
        output_bindings={"/value": ValueBinding("step_output", "/value", "emit")},
        execution_policy=WorkflowExecutionPolicy(run_timeout_seconds=5.0),
    )
    parent = workflow_v4(
        "write-set-parent",
        "1",
        (
            subworkflow_step(
                "child",
                child.workflow_id,
                child.version,
                contract=NodeContract(empty, output),
            ),
        ),
        input_schema=empty,
        output_schema=output,
        output_bindings={"/value": ValueBinding("step_output", "/value", "child")},
    )
    executor = LocalToolExecutor()
    executor.register(reference, lambda arguments: {"value": 11})
    tracked = {"SUBWORKFLOW_STARTED", "SUBWORKFLOW_FINISHED"}
    journal = _CheckingJournal(tracked)

    completed = await WorkflowEngine(
        journal, executor, {}, workflows=(child,)
    ).run_async(parent, {})

    assert completed.status == "SUCCEEDED"
    assert completed.state["v3"]["output"] == {"value": 11}
    assert journal.checked == tracked


@pytest.mark.anyio
async def test_agent_dispatch_proposal_history_and_reply_writes_reconstruct_state() -> (
    None
):
    empty = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    tool_input = {
        **empty,
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }
    tool_output = {
        **empty,
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    }
    final_output = {
        **empty,
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    }
    reference = ToolReference("write-set.lookup", "1", "sha256:" + "b" * 64)
    tool = AgentToolSpec(
        "lookup",
        reference,
        "Look up a deterministic value.",
        tool_input,
        tool_output,
        capabilities=ToolRecoveryCapabilities(external_side_effects=False),
    )
    definition = workflow_v4(
        "write-set-agent",
        "1",
        (
            agent_step(
                "answer",
                "scripted",
                tools=(tool,),
                budget=AgentBudget(),
                contract=NodeContract(empty, final_output),
            ),
        ),
        input_schema=empty,
        output_schema=final_output,
        output_bindings={"/message": ValueBinding("step_output", "/message", "answer")},
    )
    decisions = {
        "write-set-agent@1:answer": (
            AgentDecision("lookup", (ToolCall(reference, {"query": "aquifer"}),)),
            AgentDecision("done", output={"message": "example:aquifer"}),
        )
    }
    tracked = {
        "AGENT_STARTED",
        "AGENT_REQUEST_RESERVED",
        "AGENT_REQUEST_DISPATCHED",
        "AGENT_ACTIONS_PROPOSED",
        "AGENT_TOOL_RESULTS_OBSERVED",
        "AGENT_REPLY_ACCEPTED",
    }
    journal = _CheckingJournal(tracked)
    executor = LocalToolExecutor()
    executor.register(
        reference, lambda arguments: {"answer": f"example:{arguments['query']}"}
    )
    engine = WorkflowEngine(
        journal, executor, {"scripted": ScriptedAgentDriver(decisions)}
    )

    waiting = await engine.run_async(definition, {}, run_id="write-set-agent-run")
    assert waiting.status == "WAITING_APPROVAL"
    approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))
    completed = await engine.resume_async(
        waiting.run_id, approvals=(ApprovalDecision(approval_id, True),)
    )

    assert completed.status == "SUCCEEDED"
    assert completed.state["v3"]["output"] == {"message": "example:aquifer"}
    assert journal.checked == tracked

    class CrashBeforeReplyJournal(_CheckingJournal):
        def __init__(self) -> None:
            super().__init__({"AGENT_REQUEST_USAGE_UNKNOWN"})
            self.crashed = False

        def replace(  # type: ignore[no-untyped-def]
            self, snapshot, *, expected_revision, event_type, payload, **kwargs
        ):
            if event_type == "AGENT_REPLY_ACCEPTED" and not self.crashed:
                self.crashed = True
                raise RuntimeError("injected crash before agent reply commit")
            return super().replace(
                snapshot,
                expected_revision=expected_revision,
                event_type=event_type,
                payload=payload,
                **kwargs,
            )

    crashed_journal = CrashBeforeReplyJournal()
    crashed_engine = WorkflowEngine(
        crashed_journal, executor, {"scripted": ScriptedAgentDriver(decisions)}
    )
    waiting = await crashed_engine.run_async(
        definition, {}, run_id="write-set-agent-crash"
    )
    approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))
    with pytest.raises(BaseExceptionGroup) as raised:
        await crashed_engine.resume_async(
            waiting.run_id, approvals=(ApprovalDecision(approval_id, True),)
        )
    assert "injected crash before agent reply commit" in repr(raised.value)
    after_crash = crashed_journal.get(waiting.run_id)
    blocked = await crashed_engine.resume_async(after_crash.run_id)
    assert blocked.status == "BLOCKED"
    assert blocked.state["failure"]["code"] == "AGENT_USAGE_UNKNOWN"
    assert crashed_journal.checked == {"AGENT_REQUEST_USAGE_UNKNOWN"}
