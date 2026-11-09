from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from platform_lightweight_agent.journal import RunSnapshot
from platform_lightweight_agent.workflow_v3 import (
    _Coordinator,
    _compile_schema,
    _compile_schema_json,
)
from platform_lightweight_agent import (
    AgentBudget,
    AgentDecision,
    AgentToolSpec,
    ApprovalDecision,
    InMemoryRunJournal,
    LocalToolExecutor,
    NodeContract,
    SQLiteRunJournal,
    ScriptedAgentDriver,
    ToolCall,
    ToolExecutionUncertainError,
    ToolRecoveryCapabilities,
    ToolReference,
    ValueBinding,
    WorkflowEngine,
    WorkflowSpec,
    WorkflowValidationError,
    agent_step,
    approval_step,
    compile_execution_plan,
    tool_step,
    workflow,
    workflow_v4,
)


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


EMPTY = _schema({})
RESULT = _schema(
    {"score": {"type": "integer"}, "label": {"type": "string"}},
    ("score", "label"),
)


def test_identical_node_schemas_share_one_bounded_compiled_validator() -> None:
    schema = _schema({f"value_{uuid4().hex}": {"type": "integer"}})
    before = _compile_schema_json.cache_info()
    first = _compile_schema(schema)
    after_first = _compile_schema_json.cache_info()
    second = _compile_schema(dict(reversed(tuple(schema.items()))))
    after_second = _compile_schema_json.cache_info()

    assert first is second
    assert after_first.misses == before.misses + 1
    assert after_second.hits == after_first.hits + 1
    assert after_second.maxsize == 128


@pytest.mark.anyio
@pytest.mark.parametrize("sqlite", (False, True))
@pytest.mark.parametrize("version", ("v3", "v4"))
async def test_coordinator_read_cache_preserves_compatibility_and_commit_authority(
    tmp_path: Path, sqlite: bool, version: str
) -> None:
    journal = (
        SQLiteRunJournal(tmp_path / "read-cache.sqlite")
        if sqlite
        else InMemoryRunJournal()
    )
    state = {
        "values": {},
        "completed": [],
        "branches": {},
        "actions": {},
        "agents": {},
        "approved_steps": [],
        "pending": None,
        "failure": None,
        "v3": {
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
    snapshot = journal.create(
        RunSnapshot(
            run_id="read-cache-run",
            workflow={
                "schema_version": f"v6-light-workflow-{version}",
                "workflow_id": "read-cache",
                "version": "1",
            },
            inputs={},
            state=state,
            status="RUNNING",
            driver_bindings={},
        ),
        "RUN_STARTED",
        {},
    )
    engine = WorkflowEngine(journal, LocalToolExecutor(), {})
    coordinator = _Coordinator(engine, snapshot)
    first = await coordinator.state()
    repeated = await coordinator.state()
    if version == "v4":
        assert repeated is first
        with pytest.raises(TypeError):
            first["failure"] = {"code": "NOT_DURABLE"}  # type: ignore[index]
        with pytest.raises(TypeError):
            first["v3"]["step_states"]["ephemeral"] = "COMPLETED"  # type: ignore[index]
    else:
        assert repeated is not first
        first["v3"]["step_states"]["ephemeral"] = "COMPLETED"
        assert repeated["v3"]["step_states"] == {}
    assert "ephemeral" not in journal.get(snapshot.run_id).state["v3"]["step_states"]

    committed = await coordinator.commit("OBSERVED", {})
    second = await coordinator.state()
    assert second is not first
    assert second["v3"]["step_states"] == {}
    assert committed.revision == snapshot.revision + 1


def test_workflow_defaults_to_v4_and_legacy_requires_explicit_version() -> None:
    explicit_legacy = workflow(
        "legacy-builder-explicit",
        "1",
        (approval_step("review", "Review?"),),
        schema_version="v6-light-workflow-v1",
    )
    step = approval_step("review", "Review?", contract=NodeContract(EMPTY, EMPTY))
    current = workflow(
        "modern-builder",
        "1",
        (step,),
        input_schema=EMPTY,
        output_schema=EMPTY,
    )
    named = workflow_v4(
        "modern-builder",
        "1",
        (step,),
        input_schema=EMPTY,
        output_schema=EMPTY,
    )
    direct = WorkflowSpec(
        "direct-builder", "1", (step,), input_schema=EMPTY, output_schema=EMPTY
    )

    assert current.schema_version == "v6-light-workflow-v4"
    assert current.to_mapping() == named.to_mapping()
    assert direct.schema_version == current.schema_version
    assert explicit_legacy.schema_version == "v6-light-workflow-v1"
    assert WorkflowSpec.from_mapping(explicit_legacy.to_mapping()) == explicit_legacy
    with pytest.raises(WorkflowValidationError, match="require input_schema"):
        workflow("incomplete", "1", (step,))


def _usage(tokens: int | None = 12) -> dict[str, Any]:
    return {
        "request_count": 1,
        "input_tokens": 7 if tokens is not None else None,
        "output_tokens": 5 if tokens is not None else None,
        "total_tokens": tokens,
        "cached_tokens": None,
        "reasoning_tokens": None,
        "duration_seconds": 0.01,
        "provider": "test",
        "model": "structured",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("request_count", -1),
        ("total_tokens", -500),
        ("transport_attempts", -1),
        ("duration_seconds", -0.1),
    ),
)
def test_agent_decision_rejects_non_monotonic_usage_values(
    field: str, value: int | float
) -> None:
    usage = _usage()
    usage[field] = value

    with pytest.raises(WorkflowValidationError, match="non-negative"):
        AgentDecision("invalid usage", output={"score": 1, "label": "x"}, usage=usage)


class _StructuredDriver:
    driver_version = "structured-test-v1"

    def __init__(self, decision: AgentDecision) -> None:
        self.decision = decision
        self.contexts: list[Any] = []

    async def decide_async(self, context):  # type: ignore[no-untyped-def]
        self.contexts.append(context)
        return self.decision


def _spec(*, budget: AgentBudget | None = None):  # type: ignore[no-untyped-def]
    return workflow(
        "structured-v4",
        "1",
        (
            agent_step(
                "classify",
                "primary",
                tools=(),
                budget=budget or AgentBudget(),
                contract=NodeContract(EMPTY, RESULT, {}),
            ),
        ),
        schema_version="v6-light-workflow-v4",
        input_schema=EMPTY,
        output_schema=RESULT,
        output_bindings={
            "/score": ValueBinding("step_output", "/score", "classify"),
            "/label": ValueBinding("step_output", "/label", "classify"),
        },
    )


@pytest.mark.anyio
@pytest.mark.parametrize("sqlite", (False, True))
async def test_v4_business_storage_marker_has_identical_memory_and_sqlite_semantics(
    tmp_path: Path, sqlite: bool
) -> None:
    """`$v6_storage` is a legal business/schema property, never a journal cue."""

    payload = _schema({"$v6_storage": {"type": "string"}}, ("$v6_storage",))
    reference = ToolReference("marker.echo", "1", "sha256:" + "e" * 64)
    spec = workflow(
        "storage-marker-v4",
        "1",
        (
            tool_step(
                "echo",
                reference,
                approval_required=False,
                capabilities=ToolRecoveryCapabilities(external_side_effects=False),
                contract=NodeContract(
                    payload,
                    payload,
                    {"/$v6_storage": ValueBinding("workflow_input", "/$v6_storage")},
                ),
            ),
        ),
        schema_version="v6-light-workflow-v4",
        input_schema=payload,
        output_schema=payload,
        output_bindings={
            "/$v6_storage": ValueBinding("step_output", "/$v6_storage", "echo")
        },
    )
    journal = (
        SQLiteRunJournal(tmp_path / "markers.sqlite")
        if sqlite
        else InMemoryRunJournal()
    )
    executor = LocalToolExecutor()
    executor.register(reference, lambda arguments: dict(arguments))

    snapshot = await WorkflowEngine(journal, executor, {}).run_async(
        spec, {"$v6_storage": "ordinary-business-value"}
    )

    assert snapshot.status == "SUCCEEDED"
    assert snapshot.state["v3"]["output"] == {"$v6_storage": "ordinary-business-value"}
    assert journal.get(snapshot.run_id).state["v3"]["output"] == {
        "$v6_storage": "ordinary-business-value"
    }


@pytest.mark.anyio
async def test_v4_structured_agent_output_and_usage_are_durable() -> None:
    driver = _StructuredDriver(
        AgentDecision(
            "human explanation",
            output={"score": 9, "label": "safe"},
            usage=_usage(),
        )
    )
    engine = WorkflowEngine(
        InMemoryRunJournal(), LocalToolExecutor(), {"primary": driver}
    )

    completed = await engine.run_async(_spec(), {})

    assert completed.status == "SUCCEEDED"
    assert completed.state["v3"]["output"] == {"score": 9, "label": "safe"}
    agent = completed.state["agents"]["classify"]
    assert agent["message"] == "human explanation"
    assert agent["usage"] == {
        "model_requests": 1,
        "driver_invocations": 1,
        "model_requests_unknown": False,
        "transport_attempts": 0,
        "transport_attempts_unknown": True,
        "tool_calls": 0,
        "context_bytes": agent["usage"]["context_bytes"],
        "total_tokens": 12,
        "tokens_unknown": False,
    }
    assert driver.contexts[0].values == {}
    assert driver.contexts[0].output_schema == RESULT
    assert driver.contexts[0].budget == AgentBudget()


@pytest.mark.anyio
async def test_scripted_v4_tool_approval_resumes_with_rebuilt_driver() -> None:
    input_schema = _schema({"query": {"type": "string"}}, ("query",))
    tool_output = _schema({"answer": {"type": "string"}}, ("answer",))
    final_output = _schema({"message": {"type": "string"}}, ("message",))
    reference = ToolReference("script.lookup", "1", "sha256:" + "b" * 64)
    tool = AgentToolSpec(
        "lookup",
        reference,
        "Look up one deterministic value.",
        input_schema,
        tool_output,
        capabilities=ToolRecoveryCapabilities(
            external_side_effects=False,
            supports_result_query=True,
        ),
    )
    spec = workflow(
        "scripted-v4",
        "1",
        (
            agent_step(
                "research",
                "scripted",
                tools=(tool,),
                budget=AgentBudget(),
                contract=NodeContract(EMPTY, final_output, {}),
            ),
        ),
        schema_version="v6-light-workflow-v4",
        input_schema=EMPTY,
        output_schema=final_output,
        output_bindings={
            "/message": ValueBinding("step_output", "/message", "research")
        },
    )
    decisions = {
        "scripted-v4@1:research": (
            AgentDecision("lookup", (ToolCall(reference, {"query": "aquifer"}),)),
            AgentDecision("done", output={"message": "example:aquifer"}),
        )
    }
    journal = InMemoryRunJournal()
    executor = LocalToolExecutor()
    executor.register(
        reference, lambda arguments: {"answer": f"example:{arguments['query']}"}
    )
    first_engine = WorkflowEngine(
        journal, executor, {"scripted": ScriptedAgentDriver(decisions)}
    )

    waiting = await first_engine.run_async(spec, {}, run_id="scripted-restart")

    assert waiting.status == "WAITING_APPROVAL"
    approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))
    rebuilt_engine = WorkflowEngine(
        journal, executor, {"scripted": ScriptedAgentDriver(decisions)}
    )
    completed = await rebuilt_engine.resume_async(
        waiting.run_id, approvals=(ApprovalDecision(approval_id, True),)
    )

    assert completed.status == "SUCCEEDED"
    assert completed.state["v3"]["output"] == {"message": "example:aquifer"}
    usage = completed.state["agents"]["research"]["usage"]
    assert usage["model_requests"] == 2
    assert usage["model_requests_unknown"] is False
    assert usage["tokens_unknown"] is False


@pytest.mark.anyio
async def test_v4_token_budget_is_not_reset_by_resume() -> None:
    driver = _StructuredDriver(
        AgentDecision(
            "too expensive",
            output={"score": 9, "label": "safe"},
            usage=_usage(12),
        )
    )
    engine = WorkflowEngine(
        InMemoryRunJournal(), LocalToolExecutor(), {"primary": driver}
    )
    blocked = await engine.run_async(_spec(budget=AgentBudget(max_total_tokens=10)), {})

    assert blocked.status == "BLOCKED"
    assert blocked.state["failure"]["code"] == "AGENT_BUDGET_EXCEEDED"
    assert blocked.state["agents"]["classify"]["usage"]["total_tokens"] == 12
    resumed = await engine.resume_async(blocked.run_id)
    assert resumed.revision == blocked.revision
    assert len(driver.contexts) == 1


@pytest.mark.anyio
async def test_v4_token_ceiling_rejects_unknown_usage_on_final_reply() -> None:
    driver = _StructuredDriver(
        AgentDecision(
            "unknown cost",
            output={"score": 9, "label": "safe"},
            usage=_usage(None),
        )
    )
    engine = WorkflowEngine(
        InMemoryRunJournal(), LocalToolExecutor(), {"primary": driver}
    )

    blocked = await engine.run_async(_spec(budget=AgentBudget(max_total_tokens=10)), {})

    assert blocked.status == "BLOCKED"
    assert blocked.state["failure"]["code"] == "AGENT_USAGE_UNKNOWN"
    assert blocked.state["agents"]["classify"]["usage"]["tokens_unknown"] is True


def test_v4_requires_explicit_recovery_and_budget_contracts() -> None:
    with pytest.raises(WorkflowValidationError, match="durable budget"):
        workflow(
            "missing-budget",
            "1",
            (agent_step("agent", "primary", contract=NodeContract(EMPTY, RESULT, {})),),
            schema_version="v6-light-workflow-v4",
            input_schema=EMPTY,
            output_schema=RESULT,
            output_bindings={
                "/score": ValueBinding("step_output", "/score", "agent"),
                "/label": ValueBinding("step_output", "/label", "agent"),
            },
        )


def test_v4_plan_exposes_budget_and_recovery_warning() -> None:
    plan = compile_execution_plan(_spec()).to_mapping()
    agent = plan["steps"][0]
    assert agent["budget"] == AgentBudget().to_mapping()
    assert agent["tool_recovery"] == []
    capabilities = ToolRecoveryCapabilities(
        external_side_effects=True,
        supports_result_query=False,
    )
    assert capabilities.to_mapping()["external_side_effects"] is True
    reference = ToolReference("mutating.tool", "1", "sha256:" + "a" * 64)
    mutating = workflow(
        "mutating-v4",
        "1",
        (
            tool_step(
                "write",
                reference,
                approval_required=True,
                contract=NodeContract(EMPTY, EMPTY, {}),
                capabilities=capabilities,
            ),
        ),
        schema_version="v6-light-workflow-v4",
        input_schema=EMPTY,
        output_schema=EMPTY,
        output_bindings={},
    )
    tool_plan = compile_execution_plan(mutating).to_mapping()["steps"][0]
    assert (
        tool_plan["recovery_warning"] == "UNKNOWN_RESULT_REQUIRES_MANUAL_RECONCILIATION"
    )


@pytest.mark.anyio
async def test_v4_does_not_query_an_executor_without_declared_query_support() -> None:
    class Executor:
        def __init__(self) -> None:
            self.reconciles = 0

        async def execute_async(self, request, cancellation):  # type: ignore[no-untyped-def]
            del request, cancellation
            raise ToolExecutionUncertainError("receipt lost")

        async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
            del request
            self.reconciles += 1

    reference = ToolReference("unknown.tool", "1", "sha256:" + "b" * 64)
    capabilities = ToolRecoveryCapabilities(
        external_side_effects=True,
        supports_result_query=False,
    )
    spec = workflow(
        "unknown-v4",
        "1",
        (
            tool_step(
                "write",
                reference,
                approval_required=False,
                contract=NodeContract(EMPTY, EMPTY, {}),
                capabilities=capabilities,
            ),
        ),
        schema_version="v6-light-workflow-v4",
        input_schema=EMPTY,
        output_schema=EMPTY,
        output_bindings={},
    )
    executor = Executor()
    engine = WorkflowEngine(InMemoryRunJournal(), executor, {})

    unknown = await engine.run_async(spec, {})
    resumed = await engine.resume_async(unknown.run_id)

    assert unknown.status == "RECONCILING"
    assert resumed.status == "RECONCILING"
    assert executor.reconciles == 0


@pytest.mark.anyio
async def test_v4_rejects_oversized_agent_tool_output_before_persisting_it() -> None:
    reference = ToolReference("large.tool", "1", "sha256:" + "c" * 64)
    capabilities = ToolRecoveryCapabilities(
        external_side_effects=False,
        supports_result_query=True,
    )
    tool_output = _schema({"answer": {"type": "string"}}, ("answer",))
    tool = AgentToolSpec(
        "large",
        reference,
        "Return a record.",
        EMPTY,
        tool_output,
        capabilities=capabilities,
    )
    driver = _StructuredDriver(AgentDecision("", (ToolCall(reference, {}),)))
    executor = LocalToolExecutor()
    executor.register(reference, lambda arguments: {"answer": "x" * 100})
    spec = workflow(
        "large-output-v4",
        "1",
        (
            agent_step(
                "agent",
                "primary",
                tools=(tool,),
                budget=AgentBudget(max_tool_output_bytes=10),
                contract=NodeContract(EMPTY, RESULT, {}),
            ),
        ),
        schema_version="v6-light-workflow-v4",
        input_schema=EMPTY,
        output_schema=RESULT,
        output_bindings={
            "/score": ValueBinding("step_output", "/score", "agent"),
            "/label": ValueBinding("step_output", "/label", "agent"),
        },
    )
    engine = WorkflowEngine(InMemoryRunJournal(), executor, {"primary": driver})
    waiting = await engine.run_async(spec, {})
    approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))

    blocked = await engine.resume_async(
        waiting.run_id, approvals=(ApprovalDecision(approval_id, True),)
    )

    action = next(iter(blocked.state["actions"].values()))
    assert blocked.status == "BLOCKED"
    assert blocked.state["failure"]["code"] == "AGENT_TOOL_OUTPUT_TOO_LARGE"
    assert action["status"] == "OUTPUT_INVALID"
    assert "result" not in action


class _CrashBeforeUsageCommitJournal(InMemoryRunJournal):
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
        if event_type == "AGENT_REPLY_ACCEPTED" and not self.crashed:
            self.crashed = True
            raise RuntimeError("injected crash before usage commit")
        return super().replace(
            snapshot,
            expected_revision=expected_revision,
            event_type=event_type,
            payload=payload,
        )


@pytest.mark.anyio
async def test_v4_dispatched_model_usage_is_not_treated_as_zero_after_crash() -> None:
    driver = _StructuredDriver(
        AgentDecision(
            "would have completed",
            output={"score": 9, "label": "safe"},
            usage=_usage(9),
        )
    )
    journal = _CrashBeforeUsageCommitJournal()
    engine = WorkflowEngine(journal, LocalToolExecutor(), {"primary": driver})

    with pytest.raises(BaseExceptionGroup) as raised:
        await engine.run_async(
            _spec(budget=AgentBudget(max_total_tokens=10)),
            {},
            run_id="model-usage-crash",
        )
    assert "injected crash before usage commit" in repr(raised.value)
    after_crash = journal.get("model-usage-crash")
    invocation = next(
        iter(after_crash.state["agents"]["classify"]["model_invocations"].values())
    )
    assert invocation["status"] == "DISPATCHED"

    blocked = await engine.resume_async(after_crash.run_id)

    assert blocked.status == "BLOCKED"
    assert blocked.state["failure"]["code"] == "AGENT_USAGE_UNKNOWN"
    assert blocked.state["agents"]["classify"]["usage"]["tokens_unknown"] is True
    assert len(driver.contexts) == 1
