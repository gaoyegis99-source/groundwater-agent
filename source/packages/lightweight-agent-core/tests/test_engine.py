from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from platform_lightweight_agent import (
    ActionAuthorization,
    AgentDecision,
    InMemoryRunJournal,
    LocalToolExecutor,
    RunJournal,
    SQLiteRunJournal,
    ToolCall,
    ToolReference,
    WorkflowEngine,
    WorkflowValidationError,
    agent_step,
    approval_step,
    condition_step,
    parallel_step,
    subworkflow_step,
    tool_step,
    workflow,
)
from platform_lightweight_agent.engine import (
    ToolExecutionFailedError,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutionUncertainError,
    WorkflowRunError,
)
from platform_lightweight_agent.journal import RunConflictError

TOOL = ToolReference("demo.echo", "1.0.0", "sha256:" + "1" * 64)


def _executor(*, calls: list[dict] | None = None) -> LocalToolExecutor:
    executor = LocalToolExecutor()

    def echo(arguments: dict) -> dict:
        if calls is not None:
            calls.append(arguments)
        return {"echo": arguments}

    executor.register(TOOL, echo)
    return executor


def test_local_tool_requires_approval_then_runs_once() -> None:
    calls: list[dict] = []
    engine = WorkflowEngine(InMemoryRunJournal(), _executor(calls=calls), {})
    spec = workflow(
        "demo",
        "1.0.0",
        [tool_step("echo", TOOL, {"value": 7})],
        schema_version="v6-light-workflow-v1",
    )

    waiting = engine.run(spec, run_id="run-approval")

    assert waiting.status == "WAITING_APPROVAL"
    complete = engine.resume("run-approval", approved=True)
    assert complete.status == "SUCCEEDED"
    assert calls == [{"value": 7}]
    assert complete.state["values"]["echo"] == {"echo": {"value": 7}}
    assert engine.resume("run-approval").revision == complete.revision


def test_explicit_approval_rejection_and_cancellation_are_terminal() -> None:
    engine = WorkflowEngine(InMemoryRunJournal(), _executor(), {})
    rejected = engine.run(
        workflow(
            "reject",
            "1",
            [approval_step("review", "allow?")],
            schema_version="v6-light-workflow-v1",
        ),
        run_id="run-reject",
    )
    assert rejected.status == "WAITING_APPROVAL"
    assert engine.resume("run-reject", approved=False).status == "BLOCKED"

    cancelled = engine.run(
        workflow(
            "cancel",
            "1",
            [approval_step("review2", "allow?")],
            schema_version="v6-light-workflow-v1",
        ),
        run_id="run-cancel",
    )
    assert cancelled.status == "WAITING_APPROVAL"
    assert (
        engine.cancel("run-cancel", reason="operator stopped it").status == "CANCELLED"
    )


class _RecordingDriver:
    driver_version = "recording-v1"

    def __init__(self, decisions: list[AgentDecision]) -> None:
        self.decisions = list(decisions)
        self.contexts = []

    def decide(self, context):  # type: ignore[no-untyped-def]
        self.contexts.append(context)
        return self.decisions.pop(0)


class _CrashBeforeLegacyReplyJournal(InMemoryRunJournal):
    def __init__(self) -> None:
        super().__init__()
        self.crashed = False

    def replace(self, snapshot, *, expected_revision, event_type, payload):  # type: ignore[no-untyped-def]
        if event_type == "AGENT_REPLY_ACCEPTED" and not self.crashed:
            self.crashed = True
            raise RuntimeError("injected crash before legacy Agent reply")
        return super().replace(
            snapshot,
            expected_revision=expected_revision,
            event_type=event_type,
            payload=payload,
        )


@pytest.mark.parametrize(
    "schema_version", ["v6-light-workflow-v1", "v6-light-workflow-v2"]
)
def test_legacy_agent_dispatched_request_is_not_replayed_after_crash(
    schema_version: str,
) -> None:
    journal = _CrashBeforeLegacyReplyJournal()
    driver = _RecordingDriver([AgentDecision("answer")])
    engine = WorkflowEngine(journal, _executor(), {"planner": driver})
    spec = workflow(
        "legacy-agent-crash",
        "1",
        [agent_step("answer", "planner", tools=())],
        schema_version=schema_version,
    )

    with pytest.raises(RuntimeError, match="injected crash"):
        engine.run(spec, run_id=f"legacy-agent-{schema_version}")
    crashed = journal.get(f"legacy-agent-{schema_version}")
    invocation = next(
        iter(crashed.state["agents"]["answer"]["model_invocations"].values())
    )
    assert invocation["status"] == "DISPATCHED"

    blocked = engine.resume(crashed.run_id)

    assert blocked.status == "BLOCKED"
    assert blocked.state["failure"]["code"] == "AGENT_USAGE_UNKNOWN"
    assert len(driver.contexts) == 1


def test_agent_proposal_uses_same_tool_and_approval_contract() -> None:
    driver = _RecordingDriver(
        [
            AgentDecision("need data", (ToolCall(TOOL, {"value": "from-agent"}),)),
            AgentDecision("finished"),
        ]
    )
    calls: list[dict] = []
    engine = WorkflowEngine(
        InMemoryRunJournal(), _executor(calls=calls), {"planner": driver}
    )
    spec = workflow(
        "agent-demo",
        "1",
        [
            agent_step(
                "plan",
                "planner",
                {"request": "hello"},
                max_turns=2,
                tools=(TOOL,),
            )
        ],
        schema_version="v6-light-workflow-v1",
    )

    waiting = engine.run(spec, run_id="run-agent")
    assert waiting.status == "WAITING_APPROVAL"
    complete = engine.resume("run-agent", approved=True)

    assert complete.status == "SUCCEEDED"
    assert calls == [{"value": "from-agent"}]
    assert len(driver.contexts) == 2
    assert driver.contexts[0].step_input == {"request": "hello"}
    assert complete.state["values"]["plan"] == {"message": "finished"}


def test_agent_cannot_propose_a_tool_outside_its_frozen_scope() -> None:
    other = ToolReference("demo.other", "1", "sha256:" + "3" * 64)
    driver = _RecordingDriver([AgentDecision("try other", (ToolCall(other, {}),))])
    engine = WorkflowEngine(InMemoryRunJournal(), _executor(), {"planner": driver})
    result = engine.run(
        workflow(
            "agent-scope",
            "1",
            [agent_step("plan", "planner", tools=(TOOL,))],
            schema_version="v6-light-workflow-v1",
        ),
        run_id="run-agent-scope",
    )

    assert result.status == "BLOCKED"
    assert result.state["failure"]["code"] == "AGENT_TOOL_SCOPE_DENIED"


def test_condition_parallel_and_subworkflow_are_portable_control_flow() -> None:
    calls: list[dict] = []
    executor = _executor(calls=calls)
    child = workflow(
        "child",
        "1",
        [tool_step("child-call", TOOL, {"name": "child"}, approval_required=False)],
        schema_version="v6-light-workflow-v1",
    )
    parent = workflow(
        "parent",
        "1",
        [
            condition_step(
                "route",
                "/choose",
                True,
                [
                    tool_step(
                        "then-call", TOOL, {"name": "then"}, approval_required=False
                    )
                ],
                [
                    tool_step(
                        "else-call", TOOL, {"name": "else"}, approval_required=False
                    )
                ],
            ),
            parallel_step(
                "fanout",
                [
                    [tool_step("one", TOOL, {"name": "one"}, approval_required=False)],
                    [tool_step("two", TOOL, {"name": "two"}, approval_required=False)],
                ],
            ),
            subworkflow_step("nested", "child", "1"),
        ],
        schema_version="v6-light-workflow-v1",
    )
    result = WorkflowEngine(InMemoryRunJournal(), executor, {}, workflows=[child]).run(
        parent, {"choose": True}, run_id="run-control-flow"
    )

    assert result.status == "SUCCEEDED"
    assert [item["name"] for item in calls] == ["then", "one", "two", "child"]
    assert "route/then-call" in result.state["values"]
    assert "fanout/one" in result.state["values"]
    assert "nested/child-call" in result.state["values"]


class _UncertainExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, request):  # type: ignore[no-untyped-def]
        self.calls += 1
        raise ConnectionError("receipt was lost")

    def reconcile(self, request):  # type: ignore[no-untyped-def]
        return None


def test_unknown_external_outcome_never_blindly_retries() -> None:
    executor = _UncertainExecutor()
    engine = WorkflowEngine(InMemoryRunJournal(), executor, {})
    spec = workflow(
        "uncertain",
        "1",
        [tool_step("write", TOOL, {"x": 1}, approval_required=False)],
        schema_version="v6-light-workflow-v1",
    )

    uncertain = engine.run(spec, run_id="run-uncertain")
    assert uncertain.status == "RECONCILING"
    assert executor.calls == 1
    assert engine.resume("run-uncertain").status == "RECONCILING"
    assert executor.calls == 1
    action_id = next(iter(uncertain.state["actions"]))
    complete = engine.reconcile(
        "run-uncertain",
        action_id,
        ToolExecutionResult(action_id, True, output={"confirmed": True}),
    )
    assert complete.status == "SUCCEEDED"
    with pytest.raises(WorkflowRunError, match="reconciling"):
        engine.reconcile(
            "run-uncertain",
            action_id,
            ToolExecutionResult(action_id, True, output={"confirmed": True}),
        )


def test_local_executor_preserves_an_uncertain_outcome_without_a_second_call() -> None:
    calls = 0
    executor = LocalToolExecutor()

    def uncertain(arguments: dict) -> dict:
        nonlocal calls
        del arguments
        calls += 1
        raise ToolExecutionUncertainError("remote receipt was lost")

    executor.register(TOOL, uncertain)
    engine = WorkflowEngine(InMemoryRunJournal(), executor, {})
    result = engine.run(
        workflow(
            "uncertain-local",
            "1",
            [tool_step("write", TOOL, {}, approval_required=False)],
            schema_version="v6-light-workflow-v1",
        ),
        run_id="run-uncertain-local",
    )

    assert result.status == "RECONCILING"
    assert engine.resume("run-uncertain-local").status == "RECONCILING"
    assert calls == 1


def test_local_executor_serializes_concurrent_identical_action_calls() -> None:
    started = Event()
    release = Event()
    calls = 0
    executor = LocalToolExecutor()

    def slow(arguments: dict) -> dict:
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=2)
        return {"echo": arguments}

    executor.register(TOOL, slow)
    action = ToolExecutionRequest("same-action", TOOL, {"value": "one"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(executor.execute, action)
        assert started.wait(timeout=2)
        second = pool.submit(executor.execute, action)
        release.set()
        assert (
            first.result(timeout=2).to_mapping()
            == second.result(timeout=2).to_mapping()
        )
    assert calls == 1


def test_executor_bounds_only_results_that_the_engine_has_journaled() -> None:
    calls: list[dict] = []
    executor = LocalToolExecutor(max_durable_results=1)

    def echo(arguments: dict) -> dict:
        calls.append(arguments)
        return {"echo": arguments}

    executor.register(TOOL, echo)
    result = WorkflowEngine(InMemoryRunJournal(), executor, {}).run(
        workflow(
            "bounded-executor-cache",
            "1",
            [
                tool_step("first", TOOL, {"value": 1}, approval_required=False),
                tool_step("second", TOOL, {"value": 2}, approval_required=False),
            ],
            schema_version="v6-light-workflow-v1",
        ),
        run_id="bounded-executor-cache",
    )

    assert result.status == "SUCCEEDED"
    assert calls == [{"value": 1}, {"value": 2}]
    assert executor.cached_result_count == 1
    actions = list(result.state["actions"].values())
    assert all(action["status"] == "RESOLVED" for action in actions)
    first_action = next(
        action for action in actions if action["request"]["arguments"] == {"value": 1}
    )
    first_request = ToolExecutionRequest.from_mapping(first_action["request"])
    # The cache can forget this completed result only because the immutable
    # action fact above is already in the journal; a workflow will not invoke
    # the executor for it again.
    assert executor.reconcile(first_request) is None


def test_executor_never_evicts_an_unknown_outcome_after_acknowledgement() -> None:
    executor = LocalToolExecutor(max_durable_results=0)

    def uncertain(arguments: dict) -> dict:
        del arguments
        raise ToolExecutionUncertainError("receipt was lost")

    executor.register(TOOL, uncertain)
    request = ToolExecutionRequest("unknown-cache-entry", TOOL, {})
    with pytest.raises(ToolExecutionUncertainError):
        executor.execute(request)

    executor.acknowledge_durable_result(request)
    with pytest.raises(ToolExecutionUncertainError):
        executor.execute(request)


def test_sqlite_restart_uses_journal_not_a_database_connection_in_the_api(
    tmp_path: Path,
) -> None:
    journal_path = tmp_path / "runs.sqlite"
    calls: list[dict] = []
    executor = _executor(calls=calls)
    first = WorkflowEngine(SQLiteRunJournal(journal_path), executor, {})
    spec = workflow(
        "restart",
        "1",
        [tool_step("write", TOOL, {"x": 2})],
        schema_version="v6-light-workflow-v1",
    )
    assert first.run(spec, run_id="run-restart").status == "WAITING_APPROVAL"

    restarted = WorkflowEngine(SQLiteRunJournal(journal_path), executor, {})
    complete = restarted.resume("run-restart", approved=True)
    assert complete.status == "SUCCEEDED"
    assert calls == [{"x": 2}]
    journal: RunJournal = SQLiteRunJournal(journal_path)
    assert [event.type for event in journal.events("run-restart")][
        -1
    ] == "RUN_SUCCEEDED"


def test_legacy_resume_uses_the_frozen_subworkflow_definition() -> None:
    original = workflow(
        "legacy-child",
        "1",
        [tool_step("emit", TOOL, {"value": 1}, approval_required=False)],
        schema_version="v6-light-workflow-v1",
    )
    replacement = workflow(
        "legacy-child",
        "1",
        [tool_step("emit", TOOL, {"value": 2}, approval_required=False)],
        schema_version="v6-light-workflow-v1",
    )
    parent = workflow(
        "legacy-parent",
        "1",
        [
            approval_step("review", "continue?"),
            subworkflow_step("child", "legacy-child", "1"),
        ],
        schema_version="v6-light-workflow-v1",
    )
    journal = InMemoryRunJournal()
    waiting = WorkflowEngine(journal, _executor(), {}, workflows=(original,)).run(
        parent, run_id="legacy-frozen-child"
    )

    complete = WorkflowEngine(
        journal, _executor(), {}, workflows=(replacement,)
    ).resume(waiting.run_id, approved=True)

    assert complete.status == "SUCCEEDED"
    assert complete.state["values"]["child/emit"] == {"echo": {"value": 1}}


def test_resume_refuses_to_switch_agent_driver_versions() -> None:
    original = _RecordingDriver([AgentDecision("pause")])
    journal = InMemoryRunJournal()
    first = WorkflowEngine(journal, _executor(), {"planner": original})
    spec = workflow(
        "driver",
        "1",
        [agent_step("agent", "planner")],
        schema_version="v6-light-workflow-v1",
    )
    # The run succeeds immediately, but binding is still persisted and checked.
    first.run(spec, run_id="run-driver")
    changed = _RecordingDriver([])
    changed.driver_version = "recording-v2"
    with pytest.raises(RunConflictError, match="different driver version"):
        WorkflowEngine(journal, _executor(), {"planner": changed}).resume("run-driver")


class _RevokedPolicy:
    def authorize(self, request, *, run_id, workflow, step_id):  # type: ignore[no-untyped-def]
        del request, run_id, workflow, step_id
        return ActionAuthorization(False, "PERMISSION_REVOKED")


def test_permissions_and_budgets_can_be_rechecked_after_approval() -> None:
    calls: list[dict] = []
    engine = WorkflowEngine(
        InMemoryRunJournal(), _executor(calls=calls), {}, policy=_RevokedPolicy()
    )
    spec = workflow(
        "policy",
        "1",
        [tool_step("write", TOOL, {"x": 1})],
        schema_version="v6-light-workflow-v1",
    )
    assert engine.run(spec, run_id="run-policy").status == "WAITING_APPROVAL"

    result = engine.resume("run-policy", approved=True)
    assert result.status == "BLOCKED"
    assert result.state["failure"]["code"] == "ACTION_DENIED"
    assert calls == []


def test_journal_keeps_unknown_tool_errors_and_cancellation_reasons_safe() -> None:
    executor = LocalToolExecutor()

    def failing(arguments: dict) -> dict:
        del arguments
        raise RuntimeError("api_key=not-for-a-journal")

    executor.register(TOOL, failing)
    journal = InMemoryRunJournal()
    engine = WorkflowEngine(journal, executor, {})
    uncertain = engine.run(
        workflow(
            "safe-errors",
            "1",
            [tool_step("write", TOOL, {}, approval_required=False)],
            schema_version="v6-light-workflow-v1",
        ),
        run_id="run-safe-errors",
    )
    action_id = next(iter(uncertain.state["actions"]))
    assert uncertain.status == "RECONCILING"
    assert uncertain.state["actions"][action_id]["status"] == "RECONCILING"
    serialized_unknown = json.dumps(
        {
            "snapshot": uncertain.to_mapping(),
            "events": [
                event.to_mapping() for event in journal.events("run-safe-errors")
            ],
        },
        sort_keys=True,
    )
    assert "not-for-a-journal" not in serialized_unknown

    waiting = engine.run(
        workflow(
            "safe-cancel",
            "1",
            [approval_step("review", "approve?")],
            schema_version="v6-light-workflow-v1",
        ),
        run_id="run-safe-cancel",
    )
    assert waiting.status == "WAITING_APPROVAL"
    cancelled = engine.cancel("run-safe-cancel", reason="token=not-for-a-journal")
    serialized = json.dumps(
        {
            "snapshot": cancelled.to_mapping(),
            "events": [
                event.to_mapping() for event in journal.events("run-safe-cancel")
            ],
        },
        sort_keys=True,
    )
    assert "not-for-a-journal" not in serialized
    assert cancelled.state["failure"]["reason_code"] == "CANCELLED_BY_CALLER"

    with pytest.raises(WorkflowValidationError, match="stable"):
        ToolExecutionResult("action", False, error="token leaked")
    with pytest.raises(WorkflowValidationError, match="stable"):
        ActionAuthorization(False, "permission revoked")


def test_local_tool_can_report_a_proven_business_failure() -> None:
    executor = LocalToolExecutor()

    def declined(arguments: dict) -> dict:
        del arguments
        raise ToolExecutionFailedError("ACCOUNT_CLOSED")

    executor.register(TOOL, declined)
    result = WorkflowEngine(InMemoryRunJournal(), executor, {}).run(
        workflow(
            "known-tool-failure",
            "1",
            [tool_step("write", TOOL, {}, approval_required=False)],
            schema_version="v6-light-workflow-v1",
        )
    )

    action = next(iter(result.state["actions"].values()))
    assert result.status == "BLOCKED"
    assert action["status"] == "RESOLVED"
    assert action["result"]["error"] == "ACCOUNT_CLOSED"


def test_custom_executor_can_report_a_proven_business_failure() -> None:
    class KnownFailureExecutor:
        def execute(self, request):  # type: ignore[no-untyped-def]
            raise ToolExecutionFailedError("UPSTREAM_DECLINED")

        def reconcile(self, request):  # type: ignore[no-untyped-def]
            return None

    result = WorkflowEngine(InMemoryRunJournal(), KnownFailureExecutor(), {}).run(
        workflow(
            "known-custom-tool-failure",
            "1",
            [tool_step("write", TOOL, {}, approval_required=False)],
            schema_version="v6-light-workflow-v1",
        )
    )

    action = next(iter(result.state["actions"].values()))
    assert result.status == "BLOCKED"
    assert action["status"] == "RESOLVED"
    assert action["result"]["error"] == "UPSTREAM_DECLINED"


def test_local_tool_rejects_async_function_registration() -> None:
    executor = LocalToolExecutor()

    async def handler(arguments: dict) -> dict:
        return arguments

    with pytest.raises(WorkflowValidationError, match="AsyncToolExecutor"):
        executor.register(TOOL, handler)


def test_subworkflow_cycles_and_credential_outputs_are_rejected() -> None:
    cyclic = workflow(
        "cycle",
        "1",
        [subworkflow_step("again", "cycle", "1")],
        schema_version="v6-light-workflow-v1",
    )
    with pytest.raises(WorkflowValidationError, match="cycle"):
        WorkflowEngine(InMemoryRunJournal(), _executor(), {}, workflows=[cyclic]).run(
            cyclic
        )

    executor = LocalToolExecutor()
    executor.register(TOOL, lambda arguments: {"api_key": "not allowed"})
    result = WorkflowEngine(InMemoryRunJournal(), executor, {}).run(
        workflow(
            "safe-output",
            "1",
            [tool_step("tool", TOOL, {}, approval_required=False)],
            schema_version="v6-light-workflow-v1",
        )
    )
    assert result.status == "BLOCKED"
