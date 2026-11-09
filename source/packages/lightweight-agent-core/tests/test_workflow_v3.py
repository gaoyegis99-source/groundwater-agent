from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from threading import Event, Lock
from typing import Any

import anyio
import anyio.lowlevel
import pytest
from platform_lightweight_agent import (
    ActionAuthorization,
    AgentDecision,
    AgentToolSpec,
    ApprovalDecision,
    AsyncFunctionToolExecutor,
    FileWorkspace,
    FunctionToolExecutor,
    InMemoryRunJournal,
    LocalToolExecutor,
    NodeContract,
    ParallelBranch,
    SQLiteRunJournal,
    ToolCall,
    ToolContext,
    ToolExecutionFailedError,
    ToolExecutionRejectedError,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutionStoppedError,
    ToolExecutionUncertainError,
    ToolRecoveryCapabilities,
    ToolReference,
    ValueBinding,
    WorkflowEngine,
    WorkflowExecutionPolicy,
    WorkflowRunError,
    WorkflowSpec,
    WorkflowValidationError,
    agent_step,
    approval_step,
    export_bundle,
    import_bundle,
    parallel_step,
    subworkflow_step,
    tool_step,
    workflow,
)
from platform_lightweight_agent.cli import main as cli_main
from platform_lightweight_agent.journal import RunSnapshot
from platform_lightweight_agent.workflow_v3 import preflight_cache_metrics, preflight_v3

TOOL = ToolReference("v3.echo", "1", "sha256:" + "9" * 64)


def _schema(
    properties: Mapping[str, Any], required: tuple[str, ...] = ()
) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


INT = {"type": "integer"}
INPUT = _schema({"value": INT}, ("value",))
OUTPUT = _schema({"value": INT}, ("value",))


def _leaf(
    step_id: str,
    *,
    approval_required: bool = False,
    source: str = "parent_input",
) -> Any:
    return tool_step(
        step_id,
        TOOL,
        approval_required=approval_required,
        contract=NodeContract(
            INPUT,
            OUTPUT,
            {"/value": ValueBinding(source, "/value")},
        ),
    )


def _parallel_workflow(*, max_concurrency: int = 2) -> WorkflowSpec:
    join = _schema({"left": OUTPUT, "right": OUTPUT}, ("left", "right"))
    parallel = parallel_step(
        "fanout",
        (
            ParallelBranch("left", (_leaf("left-leaf"),)),
            ParallelBranch("right", (_leaf("right-leaf"),)),
        ),
        contract=NodeContract(
            INPUT,
            join,
            {"/value": ValueBinding("workflow_input", "/value")},
        ),
    )
    return workflow(
        "parallel-v3",
        "1",
        (parallel,),
        schema_version="v6-light-workflow-v3",
        input_schema=INPUT,
        output_schema=OUTPUT,
        output_bindings={
            "/value": ValueBinding("step_output", "/left/value", "fanout")
        },
        execution_policy=WorkflowExecutionPolicy(max_concurrency=max_concurrency),
    )


def test_pre_budget_v3_definition_round_trips_without_semantic_drift() -> None:
    mapping = _parallel_workflow().to_mapping()
    policy = mapping["execution_policy"]
    assert isinstance(policy, dict)
    policy.pop("max_retained_state_bytes")
    for step in mapping["steps"]:
        assert isinstance(step, dict)
        contract = step["contract"]
        assert isinstance(contract, dict)
        contract.pop("max_input_bytes")
        contract.pop("max_output_bytes")
        for branch in step.get("branches", []):
            for child in branch["steps"]:
                child_contract = child["contract"]
                child_contract.pop("max_input_bytes")
                child_contract.pop("max_output_bytes")

    restored = WorkflowSpec.from_mapping(mapping)

    assert restored.to_mapping() == mapping
    assert restored.execution_policy is not None
    assert restored.execution_policy.max_retained_state_bytes is None


class _AsyncExecutor:
    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.calls: list[str] = []
        self._lock = Lock()

    async def execute_async(self, request, cancellation):  # type: ignore[no-untyped-def]
        del cancellation
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.calls.append(request.action_id)
        await anyio.sleep(0.03)
        with self._lock:
            self.active -= 1
        return ToolExecutionResult(
            request.action_id, True, {"value": request.arguments["value"]}
        )

    async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
        del request


def test_v3_contract_round_trip_and_named_parallel_wire_format() -> None:
    spec = _parallel_workflow()
    mapping = spec.to_mapping()

    assert mapping["schema_version"] == "v6-light-workflow-v3"
    assert mapping["steps"][0]["branches"][0]["name"] == "left"
    assert WorkflowSpec.from_mapping(mapping) == spec


def test_v3_named_branches_round_trip_in_v1_bundle(tmp_path: Path) -> None:
    archive = tmp_path / "v3.v6bundle"
    exported = export_bundle(
        archive,
        _parallel_workflow(),
        tools=(TOOL.to_mapping(),),
    )
    imported = import_bundle(archive, tmp_path / "imported")

    assert exported.manifest["schema_version"] == "v6-light-bundle-v1"
    assert imported.workflow == _parallel_workflow()


def test_v3_contract_rejects_overlapping_bindings_and_legacy_parallel() -> None:
    with pytest.raises(WorkflowValidationError, match="overlap"):
        NodeContract(
            INPUT,
            OUTPUT,
            {
                "/item": ValueBinding("literal", value={}),
                "/item/value": ValueBinding("literal", value=1),
            },
        )

    with pytest.raises(WorkflowValidationError, match="named branches"):
        workflow(
            "bad-v3",
            "1",
            (
                parallel_step(
                    "parallel",
                    ((_leaf("one"),), (_leaf("two"),)),
                    contract=NodeContract(INPUT, OUTPUT, {}),
                ),
            ),
            schema_version="v6-light-workflow-v3",
            input_schema=INPUT,
            output_schema=OUTPUT,
        )


def test_v3_preflight_rejects_missing_forward_and_incompatible_bindings() -> None:
    missing = tool_step(
        "missing",
        TOOL,
        approval_required=False,
        contract=NodeContract(INPUT, OUTPUT, {}),
    )
    with pytest.raises(WorkflowValidationError, match="does not bind required"):
        preflight_v3(
            workflow(
                "missing",
                "1",
                (missing,),
                schema_version="v6-light-workflow-v3",
                input_schema=INPUT,
                output_schema=OUTPUT,
                output_bindings={
                    "/value": ValueBinding("step_output", "/value", "missing")
                },
            )
        )

    forward = tool_step(
        "first",
        TOOL,
        approval_required=False,
        contract=NodeContract(
            INPUT,
            OUTPUT,
            {"/value": ValueBinding("step_output", "/value", "second")},
        ),
    )
    second = _leaf("second", source="workflow_input")
    with pytest.raises(WorkflowValidationError, match="not definitely complete"):
        preflight_v3(
            workflow(
                "forward",
                "1",
                (forward, second),
                schema_version="v6-light-workflow-v3",
                input_schema=INPUT,
                output_schema=OUTPUT,
                output_bindings={
                    "/value": ValueBinding("step_output", "/value", "second")
                },
            )
        )

    string_input = _schema({"value": {"type": "string"}}, ("value",))
    incompatible = tool_step(
        "bad-type",
        TOOL,
        approval_required=False,
        contract=NodeContract(
            string_input,
            OUTPUT,
            {"/value": ValueBinding("workflow_input", "/value")},
        ),
    )
    with pytest.raises(WorkflowValidationError, match="incompatible"):
        preflight_v3(
            workflow(
                "bad-type",
                "1",
                (incompatible,),
                schema_version="v6-light-workflow-v3",
                input_schema=INPUT,
                output_schema=OUTPUT,
                output_bindings={
                    "/value": ValueBinding("step_output", "/value", "bad-type")
                },
            )
        )


def test_v3_preflight_cache_is_root_specific_and_order_independent() -> None:
    first = workflow(
        "preflight-first",
        "1",
        (_leaf("first", source="workflow_input"),),
        schema_version="v6-light-workflow-v3",
        input_schema=INPUT,
        output_schema=OUTPUT,
        output_bindings={"/value": ValueBinding("step_output", "/value", "first")},
    )
    second = workflow(
        "preflight-second",
        "1",
        (_leaf("second", source="workflow_input"),),
        schema_version="v6-light-workflow-v3",
        input_schema=INPUT,
        output_schema=OUTPUT,
        output_bindings={"/value": ValueBinding("step_output", "/value", "second")},
    )

    cold_first = preflight_v3(first, workflows=(first, second))
    cold_second = preflight_v3(second, workflows=(second, first))
    before_hot = preflight_cache_metrics()
    hot_second = preflight_v3(second, workflows=(first, second))
    after_hot = preflight_cache_metrics()

    first_key = f"workflow:{(first.workflow_id, first.version)}:input"
    second_key = f"workflow:{(second.workflow_id, second.version)}:input"
    assert first_key in cold_first
    assert second_key in cold_second
    assert cold_second == hot_second
    assert after_hot["hits"] == before_hot["hits"] + 1
    assert 0 <= after_hot["entries"] <= after_hot["capacity"]


@pytest.mark.anyio
async def test_v3_async_function_tool_adapter_awaits_and_receives_cancellation() -> (
    None
):
    executor = AsyncFunctionToolExecutor()
    observed: list[tuple[dict[str, Any], bool]] = []

    async def echo(arguments, cancellation):  # type: ignore[no-untyped-def]
        observed.append((dict(arguments), cancellation.cancelled))
        await anyio.lowlevel.checkpoint()
        return {"value": arguments["value"]}

    executor.register(TOOL, echo)
    engine = WorkflowEngine(InMemoryRunJournal(), executor, {})
    spec = workflow(
        "native-async-tool",
        "1",
        (_leaf("write", source="workflow_input"),),
        schema_version="v6-light-workflow-v3",
        input_schema=INPUT,
        output_schema=OUTPUT,
        output_bindings={"/value": ValueBinding("step_output", "/value", "write")},
    )

    completed = await engine.run_async(spec, {"value": 17})

    assert completed.status == "SUCCEEDED"
    assert observed == [({"value": 17}, False)]


@pytest.mark.anyio
async def test_function_tool_executor_unifies_sync_async_registration_and_context(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    """The standard registry keeps both handler kinds on their native paths."""

    sync_tool = ToolReference("v3.context-sync", "1", "sha256:" + "a" * 64)
    async_tool = ToolReference("v3.context-async", "1", "sha256:" + "b" * 64)
    workspace = FileWorkspace.under(tmp_path / "tool-context-files")
    observed: list[tuple[str, int, bool, object | None]] = []

    def context_for(request, cancellation):  # type: ignore[no-untyped-def]
        return ToolContext(request.action_id, cancellation, workspace)

    def sync_handler(arguments, context):  # type: ignore[no-untyped-def]
        observed.append(
            (
                context.action_id,
                arguments["value"],
                context.cancellation.cancelled,
                context.files,
            )
        )
        return {"value": arguments["value"]}

    async def async_handler(arguments, context):  # type: ignore[no-untyped-def]
        await anyio.lowlevel.checkpoint()
        observed.append(
            (
                context.action_id,
                arguments["value"],
                context.cancellation.cancelled,
                context.files,
            )
        )
        return {"value": arguments["value"]}

    executor = FunctionToolExecutor(context_factory=context_for)
    executor.register(sync_tool, sync_handler)
    executor.register(async_tool, async_handler)

    def spec(name: str, reference: ToolReference) -> WorkflowSpec:
        return workflow(
            name,
            "1",
            (
                tool_step(
                    "write",
                    reference,
                    approval_required=False,
                    contract=NodeContract(
                        INPUT,
                        OUTPUT,
                        {"/value": ValueBinding("workflow_input", "/value")},
                    ),
                ),
            ),
            schema_version="v6-light-workflow-v3",
            input_schema=INPUT,
            output_schema=OUTPUT,
            output_bindings={"/value": ValueBinding("step_output", "/value", "write")},
        )

    engine = WorkflowEngine(InMemoryRunJournal(), executor, {})
    sync_result = await engine.run_async(spec("context-sync", sync_tool), {"value": 3})
    async_result = await engine.run_async(
        spec("context-async", async_tool), {"value": 5}
    )

    assert sync_result.status == async_result.status == "SUCCEEDED"
    assert [(value, cancelled, files) for _, value, cancelled, files in observed] == [
        (3, False, workspace),
        (5, False, workspace),
    ]
    assert all(action_id.startswith("action-") for action_id, *_ in observed)


def test_sync_tool_context_is_explicitly_opt_in() -> None:
    """A legacy optional second parameter keeps its previous meaning."""

    reference = ToolReference("v3.context-legacy", "1", "sha256:" + "c" * 64)
    received: list[object | None] = []

    def context_for(request, cancellation):  # type: ignore[no-untyped-def]
        return ToolContext(request.action_id, cancellation)

    def legacy_handler(arguments, optional=None):  # type: ignore[no-untyped-def]
        received.append(optional)
        return {"value": arguments["value"]}

    executor = FunctionToolExecutor(context_factory=context_for)
    executor.register(reference, legacy_handler)

    result = executor.execute(
        ToolExecutionRequest("context-legacy-action", reference, {"value": 8})
    )

    assert result.output == {"value": 8}
    assert received == [None]


@pytest.mark.anyio
async def test_v3_custom_executor_preserves_proven_business_failure() -> None:
    class KnownFailureExecutor:
        def execute(self, request):  # type: ignore[no-untyped-def]
            raise ToolExecutionFailedError("UPSTREAM_DECLINED")

        def reconcile(self, request):  # type: ignore[no-untyped-def]
            return None

    engine = WorkflowEngine(InMemoryRunJournal(), KnownFailureExecutor(), {})
    result = await engine.run_async(
        workflow(
            "v3-known-custom-tool-failure",
            "1",
            (_leaf("write", source="workflow_input"),),
            schema_version="v6-light-workflow-v3",
            input_schema=INPUT,
            output_schema=OUTPUT,
            output_bindings={"/value": ValueBinding("step_output", "/value", "write")},
        ),
        {"value": 7},
    )

    action = next(iter(result.state["actions"].values()))
    assert result.status == "BLOCKED"
    assert action["status"] == "RESOLVED"
    assert action["result"]["error"] == "UPSTREAM_DECLINED"


@pytest.mark.anyio
async def test_v3_parallel_is_bounded_and_join_is_declaration_ordered() -> None:
    executor = _AsyncExecutor()
    engine = WorkflowEngine(InMemoryRunJournal(), executor, {})

    snapshot = await engine.run_async(_parallel_workflow(), {"value": 7})

    assert snapshot.status == "SUCCEEDED"
    assert snapshot.state["v3"]["output"] == {"value": 7}
    assert executor.peak == 2
    join = snapshot.state["v3"]["step_outputs"]["fanout"]
    assert list(join) == ["left", "right"]


class _BranchFailureExecutor:
    def __init__(self) -> None:
        self.finished: list[int] = []

    async def execute_async(self, request, cancellation):  # type: ignore[no-untyped-def]
        del cancellation
        value = request.arguments["value"]
        if value == 0:
            await anyio.sleep(0.01)
            self.finished.append(value)
            return ToolExecutionResult(request.action_id, False, error="BRANCH_FAILED")
        try:
            await anyio.sleep(0.08)
        except anyio.get_cancelled_exc_class() as error:
            raise ToolExecutionStoppedError("branch stopped") from error
        self.finished.append(value)
        return ToolExecutionResult(request.action_id, True, {"value": value})

    async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
        del request


def _failure_workflow(policy: str) -> WorkflowSpec:
    empty = _schema({})
    branches = []
    for name, value in (("failure", 0), ("slow", 1)):
        branch_step = tool_step(
            f"{name}-step",
            TOOL,
            approval_required=False,
            contract=NodeContract(
                INPUT,
                OUTPUT,
                {"/value": ValueBinding("literal", value=value)},
            ),
        )
        branches.append(ParallelBranch(name, (branch_step,)))
    join = _schema({"failure": OUTPUT, "slow": OUTPUT}, ("failure", "slow"))
    fanout = parallel_step(
        "branches",
        branches,
        contract=NodeContract(empty, join, {}),
        failure_policy=policy,
    )
    return workflow(
        f"{policy}-v3",
        "1",
        (fanout,),
        schema_version="v6-light-workflow-v3",
        input_schema=empty,
        output_schema=empty,
        output_bindings={},
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("policy", "finished"),
    (("fail_fast", [0]), ("wait_all", [0, 1])),
)
async def test_v3_parallel_failure_policy(policy: str, finished: list[int]) -> None:
    executor = _BranchFailureExecutor()
    engine = WorkflowEngine(InMemoryRunJournal(), executor, {})

    snapshot = await engine.run_async(_failure_workflow(policy), {})

    assert snapshot.status == "BLOCKED"
    assert sorted(executor.finished) == finished
    assert "branches" not in snapshot.state["v3"]["step_outputs"]


class _RecoveringExecutor:
    def __init__(self) -> None:
        self.executed: list[int] = []
        self.reconciled: list[int] = []

    async def execute_async(self, request, cancellation):  # type: ignore[no-untyped-def]
        del cancellation
        value = request.arguments["value"]
        self.executed.append(value)
        if value == 1:
            raise ToolExecutionUncertainError("receipt unavailable")
        return ToolExecutionResult(request.action_id, True, {"value": value})

    async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
        value = request.arguments["value"]
        self.reconciled.append(value)
        return ToolExecutionResult(request.action_id, True, {"value": value})


class _LostReceiptLedger:
    def __init__(self) -> None:
        self.execute_calls = 0
        self.results: dict[str, ToolExecutionResult] = {}


class _LostReceiptExecutor:
    def __init__(self, ledger: _LostReceiptLedger) -> None:
        self.ledger = ledger

    async def execute_async(self, request, cancellation):  # type: ignore[no-untyped-def]
        del cancellation
        self.ledger.execute_calls += 1
        self.ledger.results[request.action_id] = ToolExecutionResult(
            request.action_id, True, {"value": request.arguments["value"]}
        )
        raise ToolExecutionUncertainError("receipt lost after external commit")

    async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
        return self.ledger.results.get(request.action_id)


@pytest.mark.anyio
async def test_v3_restart_cancel_and_late_receipt_never_redispatch(
    tmp_path: Path,
) -> None:
    path = tmp_path / "combined-recovery.sqlite"
    ledger = _LostReceiptLedger()
    first = WorkflowEngine(SQLiteRunJournal(path), _LostReceiptExecutor(ledger), {})
    spec = workflow(
        "combined-recovery-v3",
        "1",
        (_leaf("write", source="workflow_input"),),
        schema_version="v6-light-workflow-v3",
        input_schema=INPUT,
        output_schema=OUTPUT,
        output_bindings={"/value": ValueBinding("step_output", "/value", "write")},
    )

    unknown = await first.run_async(spec, {"value": 7}, run_id="combined-recovery-run")
    action_id = next(iter(unknown.state["actions"]))
    assert unknown.status == "RECONCILING"
    assert ledger.execute_calls == 1

    restarted = WorkflowEngine(SQLiteRunJournal(path), _LostReceiptExecutor(ledger), {})
    requested = await restarted.request_cancel_async(unknown.run_id)
    assert requested.state["v3"]["cancel_requested"] is True
    settled = await restarted.reconcile_async(
        unknown.run_id, action_id, ledger.results[action_id]
    )

    assert settled.status == "CANCELLED"
    assert settled.state["actions"][action_id]["status"] == "RESOLVED"
    assert ledger.execute_calls == 1
    assert (await restarted.resume_async(unknown.run_id)).revision == settled.revision


@pytest.mark.anyio
async def test_v3_parallel_resume_skips_completed_branch_and_reconciles_action() -> (
    None
):
    executor = _RecoveringExecutor()
    engine = WorkflowEngine(InMemoryRunJournal(), executor, {})

    unknown = await engine.run_async(_failure_workflow("wait_all"), {})
    assert unknown.status == "RECONCILING"
    assert any(key.endswith("failure-step") for key in unknown.state["completed"])

    complete = await engine.resume_async(unknown.run_id)

    assert complete.status == "SUCCEEDED"
    assert sorted(executor.executed) == [0, 1]
    assert executor.reconciled == [1]


def _two_unknown_actions_workflow(
    *, policy: WorkflowExecutionPolicy | None = None
) -> WorkflowSpec:
    empty = _schema({})
    joined = _schema({"first": OUTPUT, "second": OUTPUT}, ("first", "second"))
    fanout = parallel_step(
        "fanout",
        (
            ParallelBranch(
                "first",
                (
                    tool_step(
                        "first-write",
                        TOOL,
                        approval_required=False,
                        contract=NodeContract(
                            INPUT,
                            OUTPUT,
                            {"/value": ValueBinding("literal", value=1)},
                        ),
                    ),
                ),
            ),
            ParallelBranch(
                "second",
                (
                    tool_step(
                        "second-write",
                        TOOL,
                        approval_required=False,
                        contract=NodeContract(
                            INPUT,
                            OUTPUT,
                            {"/value": ValueBinding("literal", value=2)},
                        ),
                    ),
                ),
            ),
        ),
        contract=NodeContract(empty, joined, {}),
        failure_policy="wait_all",
    )
    return workflow(
        "two-unknown-actions-v3",
        "1",
        (fanout,),
        schema_version="v6-light-workflow-v3",
        input_schema=empty,
        output_schema=empty,
        output_bindings={},
        execution_policy=policy,
    )


class _MultipleUnknownExecutor:
    def __init__(self) -> None:
        self.queries: list[int] = []

    async def execute_async(self, request, cancellation):  # type: ignore[no-untyped-def]
        del request, cancellation
        raise ToolExecutionUncertainError("receipt lost")

    async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
        value = int(request.arguments["value"])
        self.queries.append(value)
        if value == 1:
            return None
        return ToolExecutionResult(request.action_id, True, {"value": value})


@pytest.mark.anyio
async def test_v3_recovery_round_queries_all_unknown_actions_and_persists_progress() -> (
    None
):
    executor = _MultipleUnknownExecutor()
    engine = WorkflowEngine(InMemoryRunJournal(), executor, {})

    unknown = await engine.run_async(_two_unknown_actions_workflow(), {})
    recovered = await engine.resume_async(unknown.run_id)

    assert unknown.status == "RECONCILING"
    assert recovered.status == "RECONCILING"
    assert sorted(executor.queries) == [1, 2]
    actions = {
        int(action["request"]["arguments"]["value"]): action
        for action in recovered.state["actions"].values()
    }
    assert actions[1]["status"] == "RECONCILING"
    assert actions[1]["recovery"]["query_count"] == 1
    assert actions[1]["recovery"]["latest_result"] == "unknown"
    assert (
        actions[1]["recovery"]["next_query_at"]
        > actions[1]["recovery"]["last_query_at"]
    )
    assert actions[2]["status"] == "RESOLVED"
    assert actions[2]["recovery"]["latest_result"] == "resolved"
    assert actions[2]["recovery"]["next_query_at"] is None


class _SlowAndKnownRecoveryExecutor(_MultipleUnknownExecutor):
    async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
        value = int(request.arguments["value"])
        self.queries.append(value)
        if value == 1:
            await anyio.sleep(1)
        return ToolExecutionResult(request.action_id, True, {"value": value})


@pytest.mark.anyio
async def test_v3_recovery_query_timeout_does_not_block_other_actions() -> None:
    executor = _SlowAndKnownRecoveryExecutor()
    engine = WorkflowEngine(InMemoryRunJournal(), executor, {})
    spec = _two_unknown_actions_workflow(
        policy=WorkflowExecutionPolicy(
            recovery_query_timeout_seconds=0.02,
            recovery_round_timeout_seconds=0.2,
        )
    )
    unknown = await engine.run_async(spec, {})
    # The stalled query must not hold the known peer. The result assertions
    # below distinguish per-query timeout from the 200 ms round deadline.
    with anyio.fail_after(0.5):
        recovered = await engine.resume_async(unknown.run_id)

    assert recovered.status == "RECONCILING"
    assert sorted(executor.queries) == [1, 2]
    actions = {
        int(action["request"]["arguments"]["value"]): action
        for action in recovered.state["actions"].values()
    }
    assert actions[1]["recovery"]["latest_result"] == "timeout"
    assert actions[1]["recovery"]["latest_error_code"] == "RESULT_QUERY_TIMEOUT"
    assert actions[2]["status"] == "RESOLVED"


class _NeverReturningRecoveryExecutor(_MultipleUnknownExecutor):
    async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
        self.queries.append(int(request.arguments["value"]))
        await anyio.sleep_forever()


@pytest.mark.anyio
async def test_v3_recovery_round_timeout_bounds_all_pending_queries() -> None:
    executor = _NeverReturningRecoveryExecutor()
    engine = WorkflowEngine(InMemoryRunJournal(), executor, {})
    spec = _two_unknown_actions_workflow(
        policy=WorkflowExecutionPolicy(
            recovery_query_timeout_seconds=1,
            recovery_round_timeout_seconds=0.02,
        )
    )
    unknown = await engine.run_async(spec, {})
    # The round must cancel a never-returning query before its 1 s per-query
    # timeout. Allow scheduler and snapshot overhead without a brittle 150 ms
    # end-to-end wall-clock assertion.
    with anyio.fail_after(0.5):
        recovered = await engine.resume_async(unknown.run_id)

    assert recovered.status == "RECONCILING"
    assert sorted(executor.queries) == [1, 2]
    assert {
        action["recovery"]["latest_result"]
        for action in recovered.state["actions"].values()
    } == {"round_timeout"}


@pytest.mark.anyio
async def test_v3_approval_uses_stable_id_and_does_not_replay_action() -> None:
    executor = _AsyncExecutor()
    step = _leaf("approved", approval_required=True, source="workflow_input")
    spec = workflow(
        "approval-v3",
        "1",
        (step,),
        schema_version="v6-light-workflow-v3",
        input_schema=INPUT,
        output_schema=OUTPUT,
        output_bindings={"/value": ValueBinding("step_output", "/value", "approved")},
    )
    engine = WorkflowEngine(InMemoryRunJournal(), executor, {})

    waiting = await engine.run_async(spec, {"value": 11})
    assert waiting.status == "WAITING_APPROVAL"
    approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))
    complete = await engine.resume_async(
        waiting.run_id, approvals=(ApprovalDecision(approval_id, True),)
    )

    assert complete.status == "SUCCEEDED"
    assert len(executor.calls) == 1
    assert (await engine.resume_async(complete.run_id)).revision == complete.revision


class _CooperativeExecutor:
    async def execute_async(self, request, cancellation):  # type: ignore[no-untyped-def]
        while not cancellation.cancelled:
            await anyio.sleep(0.005)
        raise ToolExecutionStoppedError("operation stopped")

    async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
        del request


@pytest.mark.anyio
async def test_v3_cancel_distinguishes_requested_from_stopped() -> None:
    spec = workflow(
        "cancel-v3",
        "1",
        (_leaf("slow", source="workflow_input"),),
        schema_version="v6-light-workflow-v3",
        input_schema=INPUT,
        output_schema=OUTPUT,
        output_bindings={"/value": ValueBinding("step_output", "/value", "slow")},
    )
    engine = WorkflowEngine(InMemoryRunJournal(), _CooperativeExecutor(), {})
    result: dict[str, Any] = {}

    async with anyio.create_task_group() as group:

        async def run() -> None:
            result["snapshot"] = await engine.run_async(spec, {"value": 1})

        group.start_soon(run)
        while True:
            await anyio.sleep(0.005)
            try:
                current = next(iter(engine._journal._snapshots.values()))
            except StopIteration:
                continue
            if any(
                action.get("status") == "DISPATCHED"
                for action in current.state["actions"].values()
            ):
                break
        requested = await engine.request_cancel_async(current.run_id)
        assert requested.status == "CANCEL_REQUESTED"

    assert result["snapshot"].status == "CANCELLED"


@pytest.mark.anyio
async def test_v3_sync_executor_cancel_after_dispatch_is_unknown_then_reconciled() -> (
    None
):
    calls = 0
    executor = LocalToolExecutor()

    def blocking(arguments):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        time.sleep(0.08)
        return {"value": arguments["value"]}

    executor.register(TOOL, blocking)
    spec = workflow(
        "sync-cancel-v3",
        "1",
        (_leaf("slow-sync", source="workflow_input"),),
        schema_version="v6-light-workflow-v3",
        input_schema=INPUT,
        output_schema=OUTPUT,
        output_bindings={"/value": ValueBinding("step_output", "/value", "slow-sync")},
    )
    engine = WorkflowEngine(InMemoryRunJournal(), executor, {})
    result: dict[str, Any] = {}

    async with anyio.create_task_group() as group:

        async def run() -> None:
            result["snapshot"] = await engine.run_async(spec, {"value": 5})

        group.start_soon(run)
        while True:
            await anyio.sleep(0.005)
            try:
                current = next(iter(engine._journal._snapshots.values()))
            except StopIteration:
                continue
            if any(
                action.get("status") == "DISPATCHED"
                for action in current.state["actions"].values()
            ):
                break
        await engine.request_cancel_async(current.run_id)

    unknown = result["snapshot"]
    assert unknown.status == "RECONCILING"
    await anyio.sleep(0.1)
    reconciled = await engine.resume_async(unknown.run_id)
    assert reconciled.status == "CANCELLED"
    assert calls == 1


@pytest.mark.anyio
async def test_v3_sync_tool_context_observes_the_live_run_cancel_token() -> None:
    started = Event()
    stopped = Event()
    executor = LocalToolExecutor()

    def cooperative(arguments, context):  # type: ignore[no-untyped-def]
        started.set()
        while not context.cancellation.cancelled:
            time.sleep(0.002)
        stopped.set()
        raise ToolExecutionStoppedError("sync operation stopped")

    executor.register(TOOL, cooperative)
    spec = workflow(
        "sync-context-cancel-v3",
        "1",
        (_leaf("slow-sync", source="workflow_input"),),
        schema_version="v6-light-workflow-v3",
        input_schema=INPUT,
        output_schema=OUTPUT,
        output_bindings={"/value": ValueBinding("step_output", "/value", "slow-sync")},
    )
    engine = WorkflowEngine(InMemoryRunJournal(), executor, {})
    result: dict[str, RunSnapshot] = {}

    async with anyio.create_task_group() as group:

        async def run() -> None:
            result["snapshot"] = await engine.run_async(
                spec, {"value": 5}, run_id="sync-context-cancel-run"
            )

        group.start_soon(run)
        assert await anyio.to_thread.run_sync(started.wait, 1)
        await engine.request_cancel_async("sync-context-cancel-run")

    assert result["snapshot"].status in {"CANCELLED", "RECONCILING"}
    assert await anyio.to_thread.run_sync(stopped.wait, 1)


@pytest.mark.anyio
async def test_v3_step_timeout_requests_the_sync_tool_context_token() -> None:
    observed_timeout = Event()
    executor = LocalToolExecutor()

    def cooperative(arguments, context):  # type: ignore[no-untyped-def]
        while not context.cancellation.cancelled:
            time.sleep(0.002)
        observed_timeout.set()
        raise ToolExecutionStoppedError("step timeout observed")

    executor.register(TOOL, cooperative)
    step = tool_step(
        "timed-sync",
        TOOL,
        approval_required=False,
        contract=NodeContract(
            INPUT,
            OUTPUT,
            {"/value": ValueBinding("workflow_input", "/value")},
            timeout_seconds=0.02,
        ),
    )
    spec = workflow(
        "sync-context-timeout-v3",
        "1",
        (step,),
        schema_version="v6-light-workflow-v3",
        input_schema=INPUT,
        output_schema=OUTPUT,
        output_bindings={"/value": ValueBinding("step_output", "/value", "timed-sync")},
    )
    snapshot = await WorkflowEngine(InMemoryRunJournal(), executor, {}).run_async(
        spec, {"value": 1}
    )

    assert snapshot.status == "RECONCILING"
    assert await anyio.to_thread.run_sync(observed_timeout.wait, 1)


class _SlowExecutor:
    async def execute_async(self, request, cancellation):  # type: ignore[no-untyped-def]
        del cancellation
        await anyio.sleep(1)
        return ToolExecutionResult(request.action_id, True, {"value": 1})

    async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
        del request


class _InvalidOutputExecutor:
    async def execute_async(self, request, cancellation):  # type: ignore[no-untyped-def]
        del cancellation
        return ToolExecutionResult(request.action_id, True, {"rejected": 99})

    async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
        del request


class _BlobExecutor:
    def __init__(self, size: int) -> None:
        self.size = size
        self.calls = 0

    async def execute_async(self, request, cancellation):  # type: ignore[no-untyped-def]
        del cancellation
        self.calls += 1
        return ToolExecutionResult(request.action_id, True, {"blob": "x" * self.size})

    async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
        del request


@pytest.mark.anyio
async def test_v3_rejects_tool_output_before_journaling_the_value() -> None:
    spec = workflow(
        "invalid-output-v3",
        "1",
        (_leaf("invalid", source="workflow_input"),),
        schema_version="v6-light-workflow-v3",
        input_schema=INPUT,
        output_schema=OUTPUT,
        output_bindings={"/value": ValueBinding("step_output", "/value", "invalid")},
    )
    engine = WorkflowEngine(InMemoryRunJournal(), _InvalidOutputExecutor(), {})

    snapshot = await engine.run_async(spec, {"value": 1})

    action = next(iter(snapshot.state["actions"].values()))
    assert snapshot.status == "BLOCKED"
    assert snapshot.state["failure"]["code"] == "STEP_OUTPUT_INVALID"
    assert action["status"] == "OUTPUT_INVALID"
    assert "result" not in action
    assert "rejected" not in json.dumps(snapshot.to_mapping())
    assert "rejected" not in json.dumps(
        [event.to_mapping() for event in engine.events(snapshot.run_id)]
    )


@pytest.mark.anyio
async def test_v3_node_input_limit_fails_before_dispatch() -> None:
    blob = _schema({"blob": {"type": "string"}}, ("blob",))
    executor = _BlobExecutor(1)
    step = tool_step(
        "bounded-input",
        TOOL,
        approval_required=False,
        contract=NodeContract(
            blob,
            blob,
            {"/blob": ValueBinding("workflow_input", "/blob")},
            max_input_bytes=64,
        ),
    )
    spec = workflow(
        "bounded-input-v3",
        "1",
        (step,),
        schema_version="v6-light-workflow-v3",
        input_schema=blob,
        output_schema=blob,
        output_bindings={
            "/blob": ValueBinding("step_output", "/blob", "bounded-input")
        },
    )

    snapshot = await WorkflowEngine(InMemoryRunJournal(), executor, {}).run_async(
        spec, {"blob": "x" * 100}
    )

    assert snapshot.status == "BLOCKED"
    assert snapshot.state["failure"]["code"] == "STEP_INPUT_TOO_LARGE"
    assert snapshot.state["failure"]["keyword"] == "maxBytes"
    assert executor.calls == 0


@pytest.mark.anyio
async def test_v3_tool_output_limit_closes_dispatched_action_without_retry() -> None:
    empty = _schema({})
    blob = _schema({"blob": {"type": "string"}}, ("blob",))
    executor = _BlobExecutor(256)
    spec = workflow(
        "bounded-output-v3",
        "1",
        (
            tool_step(
                "bounded-output",
                TOOL,
                approval_required=False,
                contract=NodeContract(empty, blob, {}, max_output_bytes=64),
            ),
        ),
        schema_version="v6-light-workflow-v3",
        input_schema=empty,
        output_schema=blob,
        output_bindings={
            "/blob": ValueBinding("step_output", "/blob", "bounded-output")
        },
    )
    engine = WorkflowEngine(InMemoryRunJournal(), executor, {})

    snapshot = await engine.run_async(spec, {})

    action = next(iter(snapshot.state["actions"].values()))
    assert snapshot.status == "BLOCKED"
    assert snapshot.state["failure"]["code"] == "STEP_OUTPUT_TOO_LARGE"
    assert action["status"] == "OUTPUT_INVALID"
    assert "result" not in action
    assert executor.calls == 1


@pytest.mark.anyio
async def test_v3_cumulative_retained_state_limit_blocks_without_retaining_payload() -> (
    None
):
    empty = _schema({})
    blob = _schema({"blob": {"type": "string"}}, ("blob",))
    executor = _BlobExecutor(900)
    steps = tuple(
        tool_step(
            f"blob-{index}",
            TOOL,
            approval_required=False,
            contract=NodeContract(empty, blob, {}, max_output_bytes=2_000),
        )
        for index in range(2)
    )
    spec = workflow(
        "retained-state-v3",
        "1",
        steps,
        schema_version="v6-light-workflow-v3",
        input_schema=empty,
        output_schema=blob,
        output_bindings={"/blob": ValueBinding("step_output", "/blob", "blob-1")},
        execution_policy=WorkflowExecutionPolicy(max_retained_state_bytes=4_500),
    )

    snapshot = await WorkflowEngine(InMemoryRunJournal(), executor, {}).run_async(
        spec, {}
    )

    assert executor.calls >= 1
    assert snapshot.status == "BLOCKED"
    assert snapshot.state["failure"]["code"] == "RUN_RETAINED_STATE_TOO_LARGE"
    assert snapshot.state["failure"]["keyword"] == "maxRunBytes"
    assert "x" * 900 not in json.dumps(snapshot.state["failure"])


@pytest.mark.anyio
async def test_v3_retained_state_limit_rejects_large_action_arguments_before_dispatch() -> (
    None
):
    empty = _schema({})
    arguments = _schema({"blob": {"type": "string"}}, ("blob",))
    executor = _BlobExecutor(1)
    spec = workflow(
        "retained-action-arguments-v3",
        "1",
        (
            tool_step(
                "write",
                TOOL,
                approval_required=False,
                contract=NodeContract(
                    arguments,
                    empty,
                    {"/blob": ValueBinding("literal", value="x" * 2_048)},
                    max_input_bytes=4_096,
                ),
            ),
        ),
        schema_version="v6-light-workflow-v3",
        input_schema=empty,
        output_schema=empty,
        output_bindings={},
        execution_policy=WorkflowExecutionPolicy(max_retained_state_bytes=2_000),
    )

    snapshot = await WorkflowEngine(InMemoryRunJournal(), executor, {}).run_async(
        spec, {}
    )

    assert snapshot.status == "BLOCKED"
    assert snapshot.state["failure"]["code"] == "RUN_RETAINED_STATE_TOO_LARGE"
    assert snapshot.state["actions"] == {}
    assert executor.calls == 0


@pytest.mark.anyio
async def test_v3_timeout_after_dispatch_requires_reconciliation() -> None:
    step = tool_step(
        "timeout",
        TOOL,
        approval_required=False,
        contract=NodeContract(
            INPUT,
            OUTPUT,
            {"/value": ValueBinding("workflow_input", "/value")},
            timeout_seconds=0.02,
        ),
    )
    spec = workflow(
        "timeout-v3",
        "1",
        (step,),
        schema_version="v6-light-workflow-v3",
        input_schema=INPUT,
        output_schema=OUTPUT,
        output_bindings={"/value": ValueBinding("step_output", "/value", "timeout")},
    )
    engine = WorkflowEngine(InMemoryRunJournal(), _SlowExecutor(), {})

    snapshot = await engine.run_async(spec, {"value": 1})

    assert snapshot.status == "RECONCILING"
    assert next(iter(snapshot.state["actions"].values()))["status"] == "RECONCILING"


@pytest.mark.anyio
async def test_v3_run_timeout_counts_approval_wait() -> None:
    approval_output = _schema({"approved": {"type": "boolean"}}, ("approved",))
    step = approval_step(
        "review",
        "Approve?",
        contract=NodeContract(_schema({}), approval_output, {}),
    )
    spec = workflow(
        "run-timeout-v3",
        "1",
        (step,),
        schema_version="v6-light-workflow-v3",
        input_schema=_schema({}),
        output_schema=approval_output,
        output_bindings={
            "/approved": ValueBinding("step_output", "/approved", "review")
        },
        execution_policy=WorkflowExecutionPolicy(run_timeout_seconds=0.02),
    )
    engine = WorkflowEngine(InMemoryRunJournal(), _AsyncExecutor(), {})
    waiting = await engine.run_async(spec, {})
    await anyio.sleep(0.03)

    timed_out = await engine.resume_async(waiting.run_id)

    assert timed_out.status == "BLOCKED"
    assert timed_out.state["failure"]["code"] == "RUN_TIMEOUT"


@pytest.mark.anyio
async def test_v3_event_stream_replays_from_persisted_cursor() -> None:
    engine = WorkflowEngine(InMemoryRunJournal(), _AsyncExecutor(), {})
    snapshot = await engine.run_async(_parallel_workflow(), {"value": 2})
    events = engine.events(snapshot.run_id)
    replayed = []

    async for event in engine.stream_events(snapshot.run_id, after=events[-2].sequence):
        replayed.append(event)

    assert [item.sequence for item in replayed] == [events[-1].sequence]


@pytest.mark.anyio
async def test_v3_event_stream_observes_another_sqlite_writer(
    tmp_path: Path,
) -> None:
    empty = _schema({})
    approved = _schema({"approved": {"type": "boolean"}}, ("approved",))
    spec = workflow(
        "sqlite-events-v3",
        "1",
        (
            approval_step(
                "review",
                "Approve?",
                contract=NodeContract(empty, approved, {}),
            ),
        ),
        schema_version="v6-light-workflow-v3",
        input_schema=empty,
        output_schema=approved,
        output_bindings={
            "/approved": ValueBinding("step_output", "/approved", "review")
        },
    )
    journal_path = tmp_path / "events.sqlite"
    reader = WorkflowEngine(SQLiteRunJournal(journal_path), _AsyncExecutor(), {})
    writer = WorkflowEngine(SQLiteRunJournal(journal_path), _AsyncExecutor(), {})
    waiting = await reader.run_async(spec, {})
    cursor = reader.events(waiting.run_id)[-1].sequence
    observed: list[str] = []

    async with anyio.create_task_group() as group:

        async def collect() -> None:
            async for event in reader.stream_events(waiting.run_id, after=cursor):
                observed.append(event.type)
                if event.type == "RUN_CANCELLED":
                    break

        group.start_soon(collect)
        await anyio.sleep(0.02)
        await writer.request_cancel_async(waiting.run_id)
        await writer.resume_async(waiting.run_id)

    assert observed == [
        "CANCELLATION_REQUESTED",
        "EXECUTOR_CLAIMED",
        "RUN_CANCELLED",
    ]


class _AsyncAgent:
    driver_version = "async-test-v1"

    def __init__(self) -> None:
        self.contexts = []

    async def decide_async(self, context):  # type: ignore[no-untyped-def]
        self.contexts.append(context)
        if not context.prior_tool_results:
            return AgentDecision(
                "",
                (
                    ToolCall(TOOL, {"value": 3}),
                    ToolCall(TOOL, {"value": 4}),
                ),
            )
        return AgentDecision("complete")


@pytest.mark.anyio
async def test_v3_agent_parallel_calls_use_async_driver_and_neutral_history() -> None:
    tool = AgentToolSpec(
        "echo",
        TOOL,
        "Echo an integer.",
        INPUT,
        OUTPUT,
    )
    agent_output = _schema({"message": {"type": "string"}}, ("message",))
    step = agent_step(
        "agent",
        "async",
        tools=(tool,),
        max_turns=3,
        contract=NodeContract(
            INPUT,
            agent_output,
            {"/value": ValueBinding("workflow_input", "/value")},
        ),
    )
    spec = workflow(
        "agent-v3",
        "1",
        (step,),
        schema_version="v6-light-workflow-v3",
        input_schema=INPUT,
        output_schema=agent_output,
        output_bindings={"/message": ValueBinding("step_output", "/message", "agent")},
    )
    executor = _AsyncExecutor()
    driver = _AsyncAgent()
    engine = WorkflowEngine(InMemoryRunJournal(), executor, {"async": driver})

    waiting = await engine.run_async(spec, {"value": 1})
    approval_ids = tuple(waiting.state["v3"]["pending_approvals"])
    assert len(approval_ids) == 2
    complete = await engine.resume_async(
        waiting.run_id,
        approvals=tuple(ApprovalDecision(item, True) for item in approval_ids),
    )

    assert complete.status == "SUCCEEDED"
    assert executor.peak == 2
    assert driver.contexts[1].prior_tool_results == (
        {
            "tool_results": [
                {
                    "name": "echo",
                    "arguments": {"value": 3},
                    "succeeded": True,
                    "output": {"value": 3},
                    "error": None,
                },
                {
                    "name": "echo",
                    "arguments": {"value": 4},
                    "succeeded": True,
                    "output": {"value": 4},
                    "error": None,
                },
            ]
        },
    )
    durable_history = complete.state["agents"]["agent"]["history"]
    assert list(durable_history[0]) == ["action_ids"]
    assert len(durable_history[0]["action_ids"]) == 2


class _DelayedResultExecutor:
    def __init__(self) -> None:
        self.started = anyio.Event()
        self.release = anyio.Event()

    async def execute_async(self, request, cancellation):  # type: ignore[no-untyped-def]
        del cancellation
        self.started.set()
        await self.release.wait()
        return ToolExecutionResult(request.action_id, True, {"value": 1})

    async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
        del request


@pytest.mark.anyio
async def test_v3_late_executor_result_cannot_overwrite_reconciliation(
    tmp_path: Path,
) -> None:
    journal_path = tmp_path / "late-result.sqlite"
    executor = _DelayedResultExecutor()
    runner = WorkflowEngine(SQLiteRunJournal(journal_path), executor, {})
    controller = WorkflowEngine(SQLiteRunJournal(journal_path), executor, {})
    spec = workflow(
        "late-result-v3",
        "1",
        (_leaf("write", source="workflow_input"),),
        schema_version="v6-light-workflow-v3",
        input_schema=INPUT,
        output_schema=OUTPUT,
        output_bindings={"/value": ValueBinding("step_output", "/value", "write")},
    )
    result: dict[str, RunSnapshot] = {}

    async with anyio.create_task_group() as group:

        async def run() -> None:
            result["snapshot"] = await runner.run_async(
                spec, {"value": 1}, run_id="late-result-run"
            )

        group.start_soon(run)
        await executor.started.wait()
        with pytest.raises(WorkflowRunError, match="active persisted executor"):
            await controller.resume_async("late-result-run")
        current = controller.get("late-result-run")
        action_id = next(iter(current.state["actions"]))
        controlled = await controller.reconcile_async(
            current.run_id,
            action_id,
            ToolExecutionResult(action_id, True, {"value": 99}),
        )
        assert controlled.state["actions"][action_id]["result"]["output"] == {
            "value": 99
        }
        executor.release.set()

    completed = result["snapshot"]
    persisted = controller.get(completed.run_id)
    assert completed.status == "SUCCEEDED"
    assert completed.state["v3"]["output"] == {"value": 99}
    assert persisted.state["v3"]["output"] == {"value": 99}
    assert "ACTION_RESULT_CONFLICT" in {
        event.type for event in controller.events(completed.run_id)
    }


class _CrashAfterAgentReplyJournal(InMemoryRunJournal):
    def __init__(self) -> None:
        super().__init__()
        self.crashed = False

    def replace(
        self,
        snapshot,
        *,
        expected_revision,
        event_type,
        payload,
    ):  # type: ignore[no-untyped-def]
        saved = super().replace(
            snapshot,
            expected_revision=expected_revision,
            event_type=event_type,
            payload=payload,
        )
        if event_type == "AGENT_REPLY_ACCEPTED" and not self.crashed:
            self.crashed = True
            raise RuntimeError("injected crash after durable Agent reply")
        return saved


class _FinalReplyAgent:
    driver_version = "final-reply-v1"

    def __init__(self) -> None:
        self.calls = 0

    async def decide_async(self, context):  # type: ignore[no-untyped-def]
        del context
        self.calls += 1
        return AgentDecision("durable answer")


class _CrashBeforeAgentReplyJournal(InMemoryRunJournal):
    def __init__(self) -> None:
        super().__init__()
        self.crashed = False

    def replace(self, snapshot, *, expected_revision, event_type, payload):  # type: ignore[no-untyped-def]
        if event_type == "AGENT_REPLY_ACCEPTED" and not self.crashed:
            self.crashed = True
            raise RuntimeError("injected crash before Agent reply commit")
        return super().replace(
            snapshot,
            expected_revision=expected_revision,
            event_type=event_type,
            payload=payload,
        )


@pytest.mark.anyio
async def test_v3_unbudgeted_agent_invocation_is_durable_and_not_replayed() -> None:
    output = _schema({"message": {"type": "string"}}, ("message",))
    empty = _schema({})
    spec = workflow(
        "agent-unbudgeted-v3",
        "1",
        (
            agent_step(
                "answer",
                "final",
                max_turns=1,
                tools=(),
                contract=NodeContract(empty, output, {}),
            ),
        ),
        schema_version="v6-light-workflow-v3",
        input_schema=empty,
        output_schema=output,
        output_bindings={"/message": ValueBinding("step_output", "/message", "answer")},
    )
    journal = _CrashBeforeAgentReplyJournal()
    driver = _FinalReplyAgent()
    engine = WorkflowEngine(journal, _AsyncExecutor(), {"final": driver})

    with pytest.raises(BaseExceptionGroup) as raised:
        await engine.run_async(spec, {}, run_id="unbudgeted-agent-crash")
    assert "injected crash before Agent reply commit" in repr(raised.value)
    after_crash = journal.get("unbudgeted-agent-crash")
    invocation = next(
        iter(after_crash.state["agents"]["answer"]["model_invocations"].values())
    )
    assert invocation["status"] == "DISPATCHED"

    blocked = await engine.resume_async("unbudgeted-agent-crash")

    assert blocked.status == "BLOCKED"
    assert blocked.state["failure"]["code"] == "AGENT_USAGE_UNKNOWN"
    assert blocked.state["agents"]["answer"]["usage"]["model_requests_unknown"] is True
    assert driver.calls == 1


@pytest.mark.anyio
async def test_v3_agent_reply_and_step_completion_are_one_durable_transition() -> None:
    output = _schema({"message": {"type": "string"}}, ("message",))
    empty = _schema({})
    spec = workflow(
        "agent-crash-v3",
        "1",
        (
            agent_step(
                "answer",
                "final",
                max_turns=1,
                tools=(),
                contract=NodeContract(empty, output, {}),
            ),
        ),
        schema_version="v6-light-workflow-v3",
        input_schema=empty,
        output_schema=output,
        output_bindings={"/message": ValueBinding("step_output", "/message", "answer")},
    )
    journal = _CrashAfterAgentReplyJournal()
    driver = _FinalReplyAgent()
    engine = WorkflowEngine(journal, _AsyncExecutor(), {"final": driver})

    with pytest.raises(BaseExceptionGroup) as raised:
        await engine.run_async(spec, {}, run_id="agent-crash-run")
    assert "injected crash after durable Agent reply" in repr(raised.value)

    after_crash = journal.get("agent-crash-run")
    assert after_crash.state["v3"]["step_outputs"]["answer"] == {
        "message": "durable answer"
    }
    assert "answer" in after_crash.state["completed"]
    resumed = await engine.resume_async("agent-crash-run")
    assert resumed.status == "SUCCEEDED"
    assert resumed.state["v3"]["output"] == {"message": "durable answer"}
    assert driver.calls == 1


class _RejectingExecutor:
    def __init__(self, code: str | None = None) -> None:
        self.reconcile_calls = 0
        self.code = code

    async def execute_async(self, request, cancellation):  # type: ignore[no-untyped-def]
        del request, cancellation
        raise ToolExecutionRejectedError(
            "rejected before execution",
            code=self.code,
        )

    async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
        del request
        self.reconcile_calls += 1


@pytest.mark.anyio
async def test_v3_pre_execution_rejection_is_not_left_reconciling() -> None:
    executor = _RejectingExecutor()
    engine = WorkflowEngine(InMemoryRunJournal(), executor, {})
    spec = workflow(
        "reject-v3",
        "1",
        (_leaf("reject", source="workflow_input"),),
        schema_version="v6-light-workflow-v3",
        input_schema=INPUT,
        output_schema=OUTPUT,
        output_bindings={"/value": ValueBinding("step_output", "/value", "reject")},
    )

    blocked = await engine.run_async(spec, {"value": 1})
    action = next(iter(blocked.state["actions"].values()))
    assert blocked.status == "BLOCKED"
    assert blocked.state["failure"]["code"] == "TOOL_REJECTED"
    assert action["status"] == "REJECTED"
    assert (await engine.resume_async(blocked.run_id)).revision == blocked.revision
    assert executor.reconcile_calls == 0


@pytest.mark.anyio
async def test_v3_pre_execution_rejection_preserves_validated_stable_code() -> None:
    code = "RESOURCE_LIMIT_ENFORCEMENT_UNAVAILABLE"
    executor = _RejectingExecutor(code)
    journal = InMemoryRunJournal()
    engine = WorkflowEngine(journal, executor, {})
    spec = workflow(
        "stable-rejection-v3",
        "1",
        (_leaf("reject", source="workflow_input"),),
        schema_version="v6-light-workflow-v3",
        input_schema=INPUT,
        output_schema=OUTPUT,
        output_bindings={"/value": ValueBinding("step_output", "/value", "reject")},
    )

    blocked = await engine.run_async(spec, {"value": 1})

    assert blocked.status == "BLOCKED"
    assert blocked.state["failure"]["code"] == code
    event = next(
        event
        for event in journal.events(blocked.run_id)
        if event.type == "TOOL_REJECTED"
    )
    assert event.payload["error_code"] == code


def _literal_child(value: int) -> WorkflowSpec:
    empty = _schema({})
    call = tool_step(
        "child-call",
        TOOL,
        approval_required=False,
        contract=NodeContract(
            INPUT,
            OUTPUT,
            {"/value": ValueBinding("literal", value=value)},
        ),
    )
    return workflow(
        "frozen-child",
        "1",
        (call,),
        schema_version="v6-light-workflow-v3",
        input_schema=empty,
        output_schema=OUTPUT,
        output_bindings={"/value": ValueBinding("step_output", "/value", "child-call")},
    )


@pytest.mark.anyio
async def test_v3_resume_executes_frozen_subworkflow_definition() -> None:
    empty = _schema({})
    approved = _schema({"approved": {"type": "boolean"}}, ("approved",))
    original = _literal_child(1)
    replacement = _literal_child(2)
    parent = workflow(
        "frozen-parent",
        "1",
        (
            approval_step(
                "review",
                "Continue?",
                contract=NodeContract(empty, approved, {}),
            ),
            subworkflow_step(
                "child",
                original.workflow_id,
                original.version,
                contract=NodeContract(empty, OUTPUT, {}),
            ),
        ),
        schema_version="v6-light-workflow-v3",
        input_schema=empty,
        output_schema=OUTPUT,
        output_bindings={"/value": ValueBinding("step_output", "/value", "child")},
    )
    journal = InMemoryRunJournal()
    first = WorkflowEngine(journal, _AsyncExecutor(), {}, workflows=(original,))
    waiting = await first.run_async(parent, {}, run_id="frozen-subworkflow-run")
    approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))
    resumed_engine = WorkflowEngine(
        journal, _AsyncExecutor(), {}, workflows=(replacement,)
    )

    completed = await resumed_engine.resume_async(
        waiting.run_id, approvals=(ApprovalDecision(approval_id, True),)
    )

    assert completed.status == "SUCCEEDED"
    assert completed.state["v3"]["output"] == {"value": 1}
    frozen = completed.execution_manifest["subworkflows"][0]
    assert frozen["definition_digest"] == original.definition_digest
    assert frozen["definition_digest"] != replacement.definition_digest


@pytest.mark.anyio
async def test_v3_subworkflow_concurrency_policy_limits_its_subtree() -> None:
    child = _parallel_workflow(max_concurrency=1)
    parent = workflow(
        "concurrency-parent",
        "1",
        (
            subworkflow_step(
                "child",
                child.workflow_id,
                child.version,
                contract=NodeContract(
                    INPUT,
                    OUTPUT,
                    {"/value": ValueBinding("workflow_input", "/value")},
                ),
            ),
        ),
        schema_version="v6-light-workflow-v3",
        input_schema=INPUT,
        output_schema=OUTPUT,
        output_bindings={"/value": ValueBinding("step_output", "/value", "child")},
        execution_policy=WorkflowExecutionPolicy(max_concurrency=4),
    )
    executor = _AsyncExecutor()
    engine = WorkflowEngine(InMemoryRunJournal(), executor, {}, workflows=(child,))

    completed = await engine.run_async(parent, {"value": 7})

    assert completed.status == "SUCCEEDED"
    assert executor.peak == 1


class _PersistedCancelExecutor:
    def __init__(self) -> None:
        self.started = anyio.Event()
        self.cancel_seen = False

    async def execute_async(self, request, cancellation):  # type: ignore[no-untyped-def]
        del request
        self.started.set()
        while not cancellation.cancelled:
            await anyio.sleep(0.005)
        self.cancel_seen = True
        raise ToolExecutionStoppedError("cancel observed")

    async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
        del request


@pytest.mark.anyio
async def test_v3_executor_observes_cancel_from_another_engine(tmp_path: Path) -> None:
    journal_path = tmp_path / "cross-engine-cancel.sqlite"
    executor = _PersistedCancelExecutor()
    runner = WorkflowEngine(SQLiteRunJournal(journal_path), executor, {})
    controller = WorkflowEngine(SQLiteRunJournal(journal_path), executor, {})
    spec = workflow(
        "cross-cancel-v3",
        "1",
        (_leaf("slow", source="workflow_input"),),
        schema_version="v6-light-workflow-v3",
        input_schema=INPUT,
        output_schema=OUTPUT,
        output_bindings={"/value": ValueBinding("step_output", "/value", "slow")},
    )
    result: dict[str, RunSnapshot] = {}

    async with anyio.create_task_group() as group:

        async def run() -> None:
            result["snapshot"] = await runner.run_async(
                spec, {"value": 1}, run_id="cross-cancel-run"
            )

        group.start_soon(run)
        await executor.started.wait()
        requested = await controller.request_cancel_async("cross-cancel-run")
        assert requested.status == "CANCEL_REQUESTED"

    assert executor.cancel_seen is True
    assert result["snapshot"].status == "CANCELLED"


class _RevocablePolicy:
    policy_version = "revocable-v1"

    def __init__(self) -> None:
        self.allowed = True
        self.calls = 0

    def authorize(self, request, *, run_id, workflow, step_id):  # type: ignore[no-untyped-def]
        del request, run_id, workflow, step_id
        self.calls += 1
        return ActionAuthorization(
            self.allowed, None if self.allowed else "PERMISSION_REVOKED"
        )


class _QueuedAuthorizationExecutor:
    def __init__(self) -> None:
        self.started = anyio.Event()
        self.release = anyio.Event()
        self.calls = 0

    async def execute_async(self, request, cancellation):  # type: ignore[no-untyped-def]
        del cancellation
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return ToolExecutionResult(
            request.action_id, True, {"value": request.arguments["value"]}
        )

    async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
        del request


@pytest.mark.anyio
async def test_v3_authorization_is_rechecked_after_capacity_queue() -> None:
    executor = _QueuedAuthorizationExecutor()
    policy = _RevocablePolicy()
    engine = WorkflowEngine(InMemoryRunJournal(), executor, {}, policy=policy)
    result: dict[str, RunSnapshot] = {}

    async with anyio.create_task_group() as group:

        async def run() -> None:
            result["snapshot"] = await engine.run_async(
                _parallel_workflow(max_concurrency=1), {"value": 1}
            )

        group.start_soon(run)
        await executor.started.wait()
        policy.allowed = False
        executor.release.set()

    blocked = result["snapshot"]
    assert blocked.status == "BLOCKED"
    assert blocked.state["failure"]["code"] == "ACTION_DENIED"
    assert executor.calls == 1
    assert policy.calls == 2


def test_v3_sync_api_rejects_an_active_event_loop() -> None:
    engine = WorkflowEngine(InMemoryRunJournal(), _AsyncExecutor(), {})

    async def invoke() -> None:
        with pytest.raises(WorkflowRunError, match="async API"):
            engine.run(_parallel_workflow(), {"value": 1})

    anyio.run(invoke)


def test_base_import_does_not_load_optional_workflow_dependencies() -> None:
    package_root = Path(__file__).parents[1] / "src"
    script = (
        "import json,sys; before=set(sys.modules); "
        "import platform_lightweight_agent; "
        "print(json.dumps(sorted(set(sys.modules)-before)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=package_root,
        check=True,
        capture_output=True,
        text=True,
    )
    imported = set(json.loads(result.stdout))
    assert "anyio" not in imported
    assert "jsonschema" not in imported
    assert "immutables" not in imported
    assert "pydantic" not in imported


def test_v3_cli_check_and_approval_id_resume(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = _schema({})
    approved = _schema({"approved": {"type": "boolean"}}, ("approved",))
    spec = workflow(
        "cli-v3",
        "1",
        (
            approval_step(
                "review",
                "Approve?",
                contract=NodeContract(empty, approved, {}),
            ),
        ),
        schema_version="v6-light-workflow-v3",
        input_schema=empty,
        output_schema=approved,
        output_bindings={
            "/approved": ValueBinding("step_output", "/approved", "review")
        },
    )
    definition = tmp_path / "workflow.json"
    definition.write_text(json.dumps(spec.to_mapping()), encoding="utf-8")
    journal = tmp_path / "runs.sqlite"

    assert cli_main(["check", str(definition)]) == 0
    capsys.readouterr()
    assert (
        cli_main(
            [
                "run",
                str(definition),
                "--journal",
                str(journal),
                "--run-id",
                "cli-v3-run",
            ]
        )
        == 0
    )
    waiting = json.loads(capsys.readouterr().out)
    approval_id = next(iter(waiting["state"]["v3"]["pending_approvals"]))

    assert (
        cli_main(
            [
                "resume",
                "cli-v3-run",
                "--journal",
                str(journal),
                "--approval-id",
                approval_id,
                "--approve",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "SUCCEEDED"


class _HeldExecutor:
    def __init__(self, *, uncertain: bool = False) -> None:
        self.started = anyio.Event()
        self.release = anyio.Event()
        self.uncertain = uncertain

    async def execute_async(self, request, cancellation):  # type: ignore[no-untyped-def]
        del cancellation
        self.started.set()
        await self.release.wait()
        if self.uncertain:
            raise ToolExecutionUncertainError("late receipt loss")
        return ToolExecutionResult(
            request.action_id, True, {"value": request.arguments["value"]}
        )

    async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
        return ToolExecutionResult(
            request.action_id, True, {"value": request.arguments["value"]}
        )


def _approval_while_running_workflow() -> WorkflowSpec:
    empty = _schema({})
    approved = _schema({"approved": {"type": "boolean"}}, ("approved",))
    joined = _schema({"review": approved, "work": OUTPUT}, ("review", "work"))
    fanout = parallel_step(
        "fanout",
        (
            ParallelBranch(
                "review",
                (
                    approval_step(
                        "review-step",
                        "Continue?",
                        contract=NodeContract(empty, approved, {}),
                    ),
                ),
            ),
            ParallelBranch(
                "work",
                (
                    tool_step(
                        "work-step",
                        TOOL,
                        approval_required=False,
                        contract=NodeContract(
                            INPUT,
                            OUTPUT,
                            {"/value": ValueBinding("literal", value=8)},
                        ),
                    ),
                ),
            ),
        ),
        contract=NodeContract(empty, joined, {}),
        failure_policy="wait_all",
    )
    return workflow(
        "approval-race-v3",
        "1",
        (fanout,),
        schema_version="v6-light-workflow-v3",
        input_schema=empty,
        output_schema=empty,
        output_bindings={},
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("approved", "expected"), ((True, "SUCCEEDED"), (False, "BLOCKED"))
)
async def test_v3_midflight_approval_is_idempotent_and_consumed_by_owner(
    tmp_path: Path, approved: bool, expected: str
) -> None:
    journal_path = tmp_path / f"approval-race-{approved}.sqlite"
    executor = _HeldExecutor()
    runner = WorkflowEngine(SQLiteRunJournal(journal_path), executor, {})
    controller = WorkflowEngine(SQLiteRunJournal(journal_path), executor, {})
    result: dict[str, RunSnapshot] = {}

    async with anyio.create_task_group() as group:

        async def run() -> None:
            result["snapshot"] = await runner.run_async(
                _approval_while_running_workflow(),
                {},
                run_id=f"approval-race-{approved}",
            )

        group.start_soon(run)
        await executor.started.wait()
        while True:
            current = controller.get(f"approval-race-{approved}")
            pending = current.state["v3"]["pending_approvals"]
            if pending:
                break
            await anyio.lowlevel.checkpoint()
        approval_id = next(iter(pending))
        accepted = await controller.resume_async(
            current.run_id,
            approvals=(ApprovalDecision(approval_id, approved),),
        )
        repeated = await controller.resume_async(
            current.run_id,
            approvals=(ApprovalDecision(approval_id, approved),),
        )
        assert accepted.status == "RUNNING"
        assert repeated.state["v3"]["pending_approvals"] == {}
        executor.release.set()

    assert result["snapshot"].status == expected
    assert result["snapshot"].state["v3"]["pending_approvals"] == {}


@pytest.mark.anyio
async def test_v3_late_executor_error_cannot_reopen_a_reconciled_action(
    tmp_path: Path,
) -> None:
    journal_path = tmp_path / "late-error.sqlite"
    executor = _HeldExecutor(uncertain=True)
    runner = WorkflowEngine(SQLiteRunJournal(journal_path), executor, {})
    controller = WorkflowEngine(SQLiteRunJournal(journal_path), executor, {})
    spec = workflow(
        "late-error-v3",
        "1",
        (_leaf("write", source="workflow_input"),),
        schema_version="v6-light-workflow-v3",
        input_schema=INPUT,
        output_schema=OUTPUT,
        output_bindings={"/value": ValueBinding("step_output", "/value", "write")},
    )
    result: dict[str, RunSnapshot] = {}

    async with anyio.create_task_group() as group:

        async def run() -> None:
            result["snapshot"] = await runner.run_async(
                spec, {"value": 1}, run_id="late-error-run"
            )

        group.start_soon(run)
        await executor.started.wait()
        current = controller.get("late-error-run")
        action_id = next(iter(current.state["actions"]))
        controlled = await controller.reconcile_async(
            current.run_id,
            action_id,
            ToolExecutionResult(action_id, True, {"value": 99}),
        )
        assert controlled.state["actions"][action_id]["status"] == "RESOLVED"
        executor.release.set()

    completed = result["snapshot"]
    action = completed.state["actions"][action_id]
    assert completed.status == "SUCCEEDED"
    assert action["status"] == "RESOLVED"
    assert action["result"]["output"] == {"value": 99}
    assert "LATE_ACTION_ERROR_IGNORED" in {
        event.type for event in controller.events(completed.run_id)
    }


class _FailureAndUnknownExecutor:
    def __init__(self) -> None:
        self.reconciles = 0

    async def execute_async(self, request, cancellation):  # type: ignore[no-untyped-def]
        del cancellation
        value = request.arguments["value"]
        if value == 0:
            return ToolExecutionResult(request.action_id, False, error="FAILED")
        raise ToolExecutionUncertainError("receipt lost")

    async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
        self.reconciles += 1
        return ToolExecutionResult(
            request.action_id, True, {"value": request.arguments["value"]}
        )


@pytest.mark.anyio
async def test_v3_failure_does_not_skip_reconciliation_of_dispatched_actions() -> None:
    executor = _FailureAndUnknownExecutor()
    engine = WorkflowEngine(InMemoryRunJournal(), executor, {})

    unknown = await engine.run_async(_failure_workflow("wait_all"), {})
    completed = await engine.resume_async(unknown.run_id)

    assert unknown.status == "RECONCILING"
    assert completed.status == "BLOCKED"
    assert executor.reconciles == 1
    assert all(
        action["status"] != "RECONCILING"
        for action in completed.state["actions"].values()
    )


class _CancellableAgent:
    driver_version = "cancellable-agent-v1"

    def __init__(self) -> None:
        self.started = anyio.Event()
        self.calls = 0
        self.cancelled = 0

    async def decide_async(self, context):  # type: ignore[no-untyped-def]
        del context
        self.calls += 1
        self.started.set()
        try:
            await anyio.sleep_forever()
        finally:
            self.cancelled += 1


def _parallel_agent_workflow() -> WorkflowSpec:
    empty = _schema({})
    reply = _schema({"message": {"type": "string"}}, ("message",))
    joined = _schema({"left": reply, "right": reply}, ("left", "right"))
    fanout = parallel_step(
        "agents",
        (
            ParallelBranch(
                "left",
                (
                    agent_step(
                        "left-agent",
                        "slow-agent",
                        tools=(),
                        contract=NodeContract(empty, reply, {}),
                    ),
                ),
            ),
            ParallelBranch(
                "right",
                (
                    agent_step(
                        "right-agent",
                        "slow-agent",
                        tools=(),
                        contract=NodeContract(empty, reply, {}),
                    ),
                ),
            ),
        ),
        contract=NodeContract(empty, joined, {}),
        failure_policy="wait_all",
    )
    return workflow(
        "agent-cancel-v3",
        "1",
        (fanout,),
        schema_version="v6-light-workflow-v3",
        input_schema=empty,
        output_schema=empty,
        output_bindings={},
        execution_policy=WorkflowExecutionPolicy(max_concurrency=1),
    )


@pytest.mark.anyio
async def test_v3_cancel_stops_active_model_and_does_not_dispatch_queued_model() -> (
    None
):
    driver = _CancellableAgent()
    engine = WorkflowEngine(
        InMemoryRunJournal(), _AsyncExecutor(), {"slow-agent": driver}
    )
    result: dict[str, RunSnapshot] = {}

    async with anyio.create_task_group() as group:

        async def run() -> None:
            result["snapshot"] = await engine.run_async(
                _parallel_agent_workflow(), {}, run_id="agent-cancel-run"
            )

        group.start_soon(run)
        await driver.started.wait()
        await engine.request_cancel_async("agent-cancel-run")

    assert result["snapshot"].status == "CANCELLED"
    assert driver.calls == 1
    assert driver.cancelled == 1


class _MutexExecutor:
    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.calls = 0
        self._lock = Lock()

    async def execute_async(self, request, cancellation):  # type: ignore[no-untyped-def]
        del cancellation
        with self._lock:
            self.calls += 1
            self.active += 1
            self.peak = max(self.peak, self.active)
        await anyio.sleep(0.03)
        with self._lock:
            self.active -= 1
        return ToolExecutionResult(
            request.action_id, True, {"value": request.arguments["value"]}
        )

    async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
        del request


def _mutex_workflow(name: str, resource: str) -> WorkflowSpec:
    step = tool_step(
        "write",
        TOOL,
        approval_required=False,
        contract=NodeContract(
            INPUT,
            OUTPUT,
            {"/value": ValueBinding("workflow_input", "/value")},
        ),
        capabilities=ToolRecoveryCapabilities(
            external_side_effects=True,
            supports_result_query=True,
            mutex_resource=resource,
        ),
    )
    return workflow(
        name,
        "1",
        (step,),
        schema_version="v6-light-workflow-v4",
        input_schema=INPUT,
        output_schema=OUTPUT,
        output_bindings={"/value": ValueBinding("step_output", "/value", "write")},
    )


@pytest.mark.anyio
async def test_v3_mutex_resource_is_shared_across_engine_instances() -> None:
    executor = _MutexExecutor()
    first = WorkflowEngine(InMemoryRunJournal(), executor, {})
    second = WorkflowEngine(InMemoryRunJournal(), executor, {})
    resource = "test-shared-project-directory"

    async with anyio.create_task_group() as group:
        group.start_soon(
            first.run_async,
            _mutex_workflow("mutex-first", resource),
            {"value": 1},
        )
        group.start_soon(
            second.run_async,
            _mutex_workflow("mutex-second", resource),
            {"value": 2},
        )

    assert executor.calls == 2
    assert executor.peak == 1


@pytest.mark.anyio
async def test_v3_unknown_sync_action_retains_mutex_until_reconciled() -> None:
    executor = LocalToolExecutor()
    started = Event()
    release = Event()
    calls = 0
    calls_lock = Lock()

    def blocking(arguments):  # type: ignore[no-untyped-def]
        nonlocal calls
        with calls_lock:
            calls += 1
        started.set()
        assert release.wait(timeout=2)
        return {"value": arguments["value"]}

    executor.register(TOOL, blocking)
    first = WorkflowEngine(InMemoryRunJournal(), executor, {})
    second = WorkflowEngine(InMemoryRunJournal(), executor, {})
    resource = "test-unknown-project-directory"
    results: dict[str, RunSnapshot] = {}

    async with anyio.create_task_group() as group:

        async def run_first() -> None:
            results["first"] = await first.run_async(
                _mutex_workflow("unknown-first", resource),
                {"value": 1},
                run_id="unknown-first-run",
            )

        async def run_second() -> None:
            results["second"] = await second.run_async(
                _mutex_workflow("unknown-second", resource),
                {"value": 2},
                run_id="unknown-second-run",
            )

        group.start_soon(run_first)
        assert await anyio.to_thread.run_sync(started.wait, 1)
        await first.request_cancel_async("unknown-first-run")
        while "first" not in results:
            await anyio.lowlevel.checkpoint()
        assert results["first"].status == "RECONCILING"

        group.start_soon(run_second)
        await anyio.sleep(0.05)
        assert calls == 1
        release.set()
        await anyio.sleep(0.05)
        assert calls == 1

        action_id = next(iter(results["first"].state["actions"]))
        reconciled = await first.reconcile_async(
            results["first"].run_id,
            action_id,
            ToolExecutionResult(action_id, True, {"value": 1}),
        )
        assert reconciled.status == "CANCELLED"

    assert calls == 2
    assert results["second"].status == "SUCCEEDED"
