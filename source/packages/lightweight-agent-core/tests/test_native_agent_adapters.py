from __future__ import annotations

import json
import os
from dataclasses import replace
from typing import Any

import pytest
from platform_lightweight_agent import (
    AgentBudget,
    AgentDriverFailure,
    AgentInvocationUsage,
    AgentToolError,
    AgentToolExample,
    AgentToolSpec,
    ApprovalDecision,
    InMemoryRunJournal,
    LocalToolExecutor,
    NodeContract,
    OpenAIAgentsDriver,
    PydanticAIDriver,
    SQLiteRunJournal,
    ToolExecutionStoppedError,
    ToolRecoveryCapabilities,
    ToolReference,
    ValueBinding,
    WorkflowEngine,
    agent_step,
    workflow,
)
from platform_lightweight_agent.engine import AgentContext

os.environ.setdefault("OPENAI_AGENTS_DISABLE_TRACING", "1")

DIALECT = "https://json-schema.org/draft/2020-12/schema"
LOOKUP_TOOL = ToolReference("demo.lookup", "1.2.3", "sha256:" + "a" * 64)
DETAIL_TOOL = ToolReference("demo.detail", "4.5.6", "sha256:" + "b" * 64)
INPUT_SCHEMA = {
    "$schema": DIALECT,
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
    "additionalProperties": False,
}
OUTPUT_SCHEMA = {
    "$schema": DIALECT,
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def _tool_spec(
    name: str = "lookup", tool: ToolReference = LOOKUP_TOOL
) -> AgentToolSpec:
    return AgentToolSpec(
        name=name,
        tool=tool,
        description="Look up a record.",
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        examples=(
            AgentToolExample({"query": "alpha"}, output={"answer": "A"}),
            AgentToolExample({"query": "missing"}, error="NOT_FOUND"),
        ),
        errors=(
            AgentToolError(
                "NOT_FOUND",
                "No matching record exists.",
                model_retryable=True,
            ),
        ),
    )


def _context(*specs: AgentToolSpec) -> AgentContext:
    selected = specs or (_tool_spec(),)
    return AgentContext(
        run_id="run-native",
        workflow_id="native-workflow",
        workflow_version="1",
        step_id="plan",
        turn=1,
        inputs={"request": "research"},
        step_input={"question": "What is known?"},
        values={"topic": "aquifer"},
        prior_tool_results=(),
        allowed_tools=tuple(spec.tool for spec in selected),
        workflow_schema_version="v6-light-workflow-v2",
        tool_specs=tuple(selected),
    )


def _agents_function_call(name: str, arguments: dict, call_id: str):
    from agents.testing import function_call

    return function_call(name, arguments, call_id=call_id)


def _agents_message(text: str):
    from agents.testing import assistant_message

    return assistant_message(text)


def _scripted_agents_model(responses: list[list[Any]]):
    from agents import set_trace_provider
    from agents.testing import ModelStep, ScriptedModel
    from agents.tracing.provider import DefaultTraceProvider

    set_trace_provider(DefaultTraceProvider())
    return ScriptedModel(ModelStep(output=tuple(items)) for items in responses)


@pytest.mark.parametrize("driver_type", [PydanticAIDriver, OpenAIAgentsDriver])
def test_custom_runner_hook_is_limited_to_v1(driver_type) -> None:  # type: ignore[no-untyped-def]
    driver = driver_type(
        agent=object(),
        runner=lambda agent, prompt: None,
        driver_version="test-driver",
    )

    with pytest.raises(RuntimeError, match="only for v1"):
        driver.decide(_context())


def test_pydantic_ai_native_tools_map_parallel_aliases_and_expose_schemas() -> None:
    pydantic_ai = pytest.importorskip("pydantic_ai")
    from pydantic_ai.capabilities import NativeTool
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.native_tools import WebSearchTool
    from pydantic_ai.profiles import ModelProfile

    lookup = _tool_spec()
    detail = _tool_spec("detail", DETAIL_TOOL)
    model = TestModel(
        call_tools=["lookup", "detail"],
        profile=ModelProfile(supports_tool_return_schema=True),
    )
    agent = pydantic_ai.Agent(
        model,
        capabilities=[NativeTool(WebSearchTool())],
    )

    @agent.tool_plain
    def caller_configured_tool() -> str:
        return "must not be exposed"

    driver = PydanticAIDriver(agent=agent)
    decision = driver.decide(_context(lookup, detail))

    assert driver.driver_version == "pydantic-ai-native-v3@1.107.9"
    assert decision.message == ""
    assert [call.tool for call in decision.tool_calls] == [LOOKUP_TOOL, DETAIL_TOOL]
    assert all(call.call_id for call in decision.tool_calls)
    assert decision.continuation is not None
    assert all(set(call.arguments) == {"query"} for call in decision.tool_calls)
    parameters = model.last_model_request_parameters
    assert parameters is not None
    assert [tool.name for tool in parameters.function_tools] == ["lookup", "detail"]
    assert parameters.native_tools == []
    assert parameters.function_tools[0].parameters_json_schema == INPUT_SCHEMA
    assert parameters.function_tools[0].return_schema == OUTPUT_SCHEMA
    assert parameters.function_tools[0].include_return_schema is True
    assert "Examples:" in (parameters.function_tools[0].description or "")
    assert "NOT_FOUND" in (parameters.function_tools[0].description or "")
    model_tool_context = repr(parameters.function_tools)
    assert LOOKUP_TOOL.tool_id not in model_tool_context
    assert LOOKUP_TOOL.version not in model_tool_context
    assert LOOKUP_TOOL.package_digest not in model_tool_context
    assert "tool_id" not in model_tool_context
    assert "package_digest" not in model_tool_context


@pytest.mark.anyio
async def test_pydantic_ai_v3_uses_native_async_run() -> None:
    pydantic_ai = pytest.importorskip("pydantic_ai")
    from pydantic_ai.models.test import TestModel

    model = TestModel(call_tools=["lookup"])
    driver = PydanticAIDriver(agent=pydantic_ai.Agent(model))

    decision = await driver.decide_async(
        replace(_context(), workflow_schema_version="v6-light-workflow-v3")
    )

    assert decision.message == ""
    assert decision.tool_calls[0].tool == LOOKUP_TOOL


@pytest.mark.anyio
@pytest.mark.parametrize("duplicate_on_final", [False, True])
async def test_pydantic_ai_v4_resumes_native_tool_protocol_after_sqlite_restart(
    tmp_path,
    duplicate_on_final: bool,
) -> None:  # type: ignore[no-untyped-def]
    """A restarted adapter receives a native tool result, not a JSON retelling."""

    pydantic_ai = pytest.importorskip("pydantic_ai")
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        ToolCallPart,
        ToolReturnPart,
    )
    from pydantic_ai.models.function import FunctionModel

    message_schema = {
        "$schema": DIALECT,
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
        "additionalProperties": False,
    }
    model_requests: list[list[Any]] = []
    exposed_tools: list[list[str]] = []

    def respond(messages, info):  # type: ignore[no-untyped-def]
        model_requests.append(list(messages))
        exposed_tools.append([tool.name for tool in info.function_tools])
        returned = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returned:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup",
                        {"query": "restart"},
                        tool_call_id="restart-tool-call",
                    )
                ]
            )
        assert len(returned) == 1
        assert returned[0].tool_call_id == "restart-tool-call"
        assert returned[0].content == {"answer": "known"}
        parts = []
        if duplicate_on_final:
            # Some providers include a stale tool call alongside a valid
            # final output even though the spent tool is no longer offered.
            parts.append(
                ToolCallPart(
                    "lookup", {"query": "restart"}, tool_call_id="repeat-call"
                )
            )
        parts.append(
            ToolCallPart(
                info.output_tools[0].name,
                {"message": "finished after native resume"},
                tool_call_id="restart-final-call",
            )
        )
        return ModelResponse(parts=parts)

    spec = workflow(
        "native-v4-restart",
        "1",
        (
            agent_step(
                "plan",
                "native",
                max_turns=2,
                tools=(
                    replace(
                        _tool_spec(),
                        capabilities=ToolRecoveryCapabilities(
                            external_side_effects=False
                        ),
                    ),
                ),
                budget=AgentBudget(max_model_requests=3, max_tool_calls=1),
                contract=NodeContract(
                    INPUT_SCHEMA,
                    message_schema,
                    {"/query": ValueBinding("workflow_input", "/query")},
                ),
            ),
        ),
        schema_version="v6-light-workflow-v4",
        input_schema=INPUT_SCHEMA,
        output_schema=message_schema,
        output_bindings={
            "/message": ValueBinding("step_output", "/message", "plan")
        },
    )
    journal_path = tmp_path / "native-continuation.sqlite"
    calls: list[dict[str, Any]] = []
    executor = LocalToolExecutor()
    executor.register(
        LOOKUP_TOOL,
        lambda arguments: calls.append(dict(arguments)) or {"answer": "known"},
    )
    first = WorkflowEngine(
        SQLiteRunJournal(journal_path),
        executor,
        {
            "native": PydanticAIDriver(
                agent=pydantic_ai.Agent(FunctionModel(respond))
            )
        },
    )

    waiting = await first.run_async(
        spec, {"query": "restart"}, run_id="native-v4-restart-run"
    )

    assert waiting.status == "WAITING_APPROVAL"
    approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))
    pending_action = next(iter(waiting.state["actions"].values()))
    assert pending_action["model_call_id"] == "restart-tool-call"
    assert calls == []

    resumed_executor = LocalToolExecutor()
    resumed_executor.register(
        LOOKUP_TOOL,
        lambda arguments: calls.append(dict(arguments)) or {"answer": "known"},
    )
    restarted = WorkflowEngine(
        SQLiteRunJournal(journal_path),
        resumed_executor,
        {
            "native": PydanticAIDriver(
                agent=pydantic_ai.Agent(FunctionModel(respond))
            )
        },
    )
    completed = await restarted.resume_async(
        waiting.run_id,
        approvals=(ApprovalDecision(approval_id, True),),
    )

    assert completed.status == "SUCCEEDED"
    assert completed.state["v3"]["output"] == {
        "message": "finished after native resume"
    }
    assert calls == [{"query": "restart"}]
    assert exposed_tools == [["lookup"], []]
    assert len(model_requests) == 2
    second_parts = [part for message in model_requests[1] for part in message.parts]
    assert any(isinstance(part, ToolCallPart) for part in second_parts)
    assert any(isinstance(part, ToolReturnPart) for part in second_parts)
    serialized = json.dumps(completed.to_mapping())
    assert "ModelResponse" not in serialized
    assert "AgentRunResult" not in serialized
    assert "restart-tool-call" in serialized


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mode", "expected_tool_calls", "expected_model_requests"),
    (("simultaneous", 1, 2), ("partial_invalid", 2, 3)),
)
async def test_pydantic_ai_v4_normalizes_complex_call_batches_before_restart(
    tmp_path,
    mode: str,
    expected_tool_calls: int,
    expected_model_requests: int,
) -> None:  # type: ignore[no-untyped-def]
    """Host results replace SDK placeholders/retries from the same response."""

    pydantic_ai = pytest.importorskip("pydantic_ai")
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        ToolCallPart,
        ToolReturnPart,
    )
    from pydantic_ai.models.function import FunctionModel

    requests: list[list[Any]] = []

    def respond(messages, info):  # type: ignore[no-untyped-def]
        requests.append(list(messages))
        returned_ids = {
            part.tool_call_id
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        }
        if not returned_ids:
            if mode == "simultaneous":
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            info.output_tools[0].name,
                            {"answer": "premature"},
                            tool_call_id="premature-final",
                        ),
                        ToolCallPart(
                            "lookup",
                            {"query": "valid"},
                            tool_call_id="valid-lookup",
                        ),
                    ]
                )
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup",
                        {"query": "valid"},
                        tool_call_id="valid-lookup",
                    ),
                    ToolCallPart(
                        "detail",
                        {"unexpected": True},
                        tool_call_id="invalid-detail",
                    ),
                ]
            )
        if mode == "partial_invalid" and "repaired-detail" not in returned_ids:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "detail",
                        {"query": "repaired"},
                        tool_call_id="repaired-detail",
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {"answer": "complete"},
                    tool_call_id="final-answer",
                )
            ]
        )

    tools = tuple(
        replace(
            spec,
            capabilities=ToolRecoveryCapabilities(external_side_effects=False),
        )
        for spec in (_tool_spec(), _tool_spec("detail", DETAIL_TOOL))
    )
    spec = workflow(
        f"complex-native-{mode}",
        "1",
        (
            agent_step(
                "plan",
                "native",
                max_turns=3,
                tools=tools,
                budget=AgentBudget(max_model_requests=4, max_tool_calls=2),
                contract=NodeContract(
                    INPUT_SCHEMA,
                    OUTPUT_SCHEMA,
                    {"/query": ValueBinding("workflow_input", "/query")},
                ),
            ),
        ),
        schema_version="v6-light-workflow-v4",
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        output_bindings={
            "/answer": ValueBinding("step_output", "/answer", "plan")
        },
    )
    journal_path = tmp_path / f"complex-native-{mode}.sqlite"
    calls: list[tuple[str, dict[str, Any]]] = []

    def build_engine() -> WorkflowEngine:
        executor = LocalToolExecutor()
        executor.register(
            LOOKUP_TOOL,
            lambda arguments: calls.append(("lookup", dict(arguments)))
            or {"answer": "known"},
        )
        executor.register(
            DETAIL_TOOL,
            lambda arguments: calls.append(("detail", dict(arguments)))
            or {"answer": "known"},
        )
        return WorkflowEngine(
            SQLiteRunJournal(journal_path),
            executor,
            {
                "native": PydanticAIDriver(
                    agent=pydantic_ai.Agent(FunctionModel(respond))
                )
            },
        )

    snapshot = await build_engine().run_async(
        spec, {"query": "test"}, run_id=f"complex-native-{mode}"
    )
    while snapshot.status == "WAITING_APPROVAL":
        approval_id = next(iter(snapshot.state["v3"]["pending_approvals"]))
        snapshot = await build_engine().resume_async(
            snapshot.run_id,
            approvals=(ApprovalDecision(approval_id, True),),
        )

    assert snapshot.status == "SUCCEEDED"
    assert snapshot.state["v3"]["output"] == {"answer": "complete"}
    assert len(calls) == expected_tool_calls
    assert len(requests) == expected_model_requests
    assert [name for name, _ in calls] == (
        ["lookup", "detail"] if mode == "partial_invalid" else ["lookup"]
    )


@pytest.mark.anyio
async def test_pydantic_ai_rejects_malformed_continuation_before_model_request() -> (
    None
):
    pydantic_ai = pytest.importorskip("pydantic_ai")
    from pydantic_ai.messages import ModelResponse
    from pydantic_ai.models.function import FunctionModel

    requests = 0

    def respond(messages, info):  # type: ignore[no-untyped-def]
        nonlocal requests
        del messages, info
        requests += 1
        return ModelResponse(parts=[])

    driver = PydanticAIDriver(agent=pydantic_ai.Agent(FunctionModel(respond)))
    context = replace(
        _context(),
        workflow_schema_version="v6-light-workflow-v4",
        output_schema=OUTPUT_SCHEMA,
        budget=AgentBudget(),
        driver_continuation={
            "schema_version": "unknown-continuation",
            "driver_version": driver.driver_version,
            "messages": [],
        },
    )

    with pytest.raises(AgentDriverFailure) as failure:
        await driver.decide_async(context)

    assert failure.value.code == "AGENT_CONTINUATION_INVALID"
    assert requests == 0


@pytest.mark.anyio
async def test_pydantic_ai_v4_uses_exact_structured_output_schema() -> None:
    pydantic_ai = pytest.importorskip("pydantic_ai")
    from pydantic_ai.models.test import TestModel

    structured_schema = {
        "$schema": DIALECT,
        "type": "object",
        "properties": {
            "score": {"type": "integer"},
            "label": {"type": "string"},
        },
        "required": ["score", "label"],
        "additionalProperties": False,
    }
    model = TestModel(call_tools=[], custom_output_args={"score": 9, "label": "safe"})
    driver = PydanticAIDriver(agent=pydantic_ai.Agent(model))
    context = replace(
        _context(),
        workflow_schema_version="v6-light-workflow-v4",
        allowed_tools=(),
        tool_specs=(),
        output_schema=structured_schema,
        budget=AgentBudget(),
    )

    decision = await driver.decide_async(context)

    assert decision.output == {"score": 9, "label": "safe"}
    assert decision.usage is not None
    parameters = model.last_model_request_parameters
    assert parameters is not None
    assert parameters.output_tools[0].parameters_json_schema == structured_schema


@pytest.mark.anyio
async def test_pydantic_ai_v4_ignores_unavailable_tool_beside_final_reply() -> None:
    """A spent tool budget must not override a valid final output."""

    pydantic_ai = pytest.importorskip("pydantic_ai")
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    exposed: list[list[str]] = []

    def respond(messages, info):  # type: ignore[no-untyped-def]
        del messages
        exposed.append([tool.name for tool in info.function_tools])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "lookup", {"query": "repeat"}, tool_call_id="unavailable-call"
                ),
                ToolCallPart(
                    info.output_tools[0].name,
                    {"answer": "final answer"},
                    tool_call_id="final-call",
                ),
            ]
        )

    driver = PydanticAIDriver(agent=pydantic_ai.Agent(FunctionModel(respond)))
    decision = await driver.decide_async(
        replace(
            _context(),
            workflow_schema_version="v6-light-workflow-v4",
            output_schema=OUTPUT_SCHEMA,
            budget=AgentBudget(max_model_requests=1, max_tool_calls=1),
            tool_calls_remaining=0,
        )
    )

    assert exposed == [[]]
    assert decision.output == {"answer": "final answer"}
    assert decision.tool_calls == ()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("request_budget", "expected_calls", "expected_failure"),
    ((2, 2, None), (1, 1, "AGENT_BUDGET_EXCEEDED")),
)
async def test_pydantic_ai_v4_bounds_sdk_repair_requests_with_usage_limits(
    request_budget: int,
    expected_calls: int,
    expected_failure: str | None,
) -> None:
    """The workflow budget fences PydanticAI's internal repair loop too."""

    pydantic_ai = pytest.importorskip("pydantic_ai")
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    output_schema = {
        "$schema": DIALECT,
        "type": "object",
        "properties": {"score": {"type": "integer"}},
        "required": ["score"],
        "additionalProperties": False,
    }
    calls = 0

    def respond(messages, info):  # type: ignore[no-untyped-def]
        nonlocal calls
        del messages
        calls += 1
        output = {} if calls == 1 else {"score": 7}
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    output,
                    tool_call_id=f"reply-{calls}",
                )
            ]
        )

    driver = PydanticAIDriver(agent=pydantic_ai.Agent(FunctionModel(respond)))
    context = replace(
        _context(),
        workflow_schema_version="v6-light-workflow-v4",
        allowed_tools=(),
        tool_specs=(),
        output_schema=output_schema,
        budget=AgentBudget(max_model_requests=request_budget),
    )

    if expected_failure is None:
        decision = await driver.decide_async(context)
        assert decision.output == {"score": 7}
        assert decision.usage is not None
        assert decision.usage["request_count"] == 2
    else:
        with pytest.raises(AgentDriverFailure) as failure:
            await driver.decide_async(context)
        assert failure.value.code == expected_failure
        assert failure.value.model_request_count == request_budget
    assert calls == expected_calls


@pytest.mark.anyio
async def test_pydantic_ai_v4_stops_repair_after_token_limit_is_exceeded() -> None:
    """The SDK's response-time token check prevents a second paid request."""

    pydantic_ai = pytest.importorskip("pydantic_ai")
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.usage import RequestUsage

    calls = 0

    def respond(messages, info):  # type: ignore[no-untyped-def]
        nonlocal calls
        del messages
        calls += 1
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {},
                    tool_call_id=f"invalid-{calls}",
                )
            ],
            usage=RequestUsage(input_tokens=7, output_tokens=5),
        )

    driver = PydanticAIDriver(agent=pydantic_ai.Agent(FunctionModel(respond)))
    context = replace(
        _context(),
        workflow_schema_version="v6-light-workflow-v4",
        allowed_tools=(),
        tool_specs=(),
        output_schema=OUTPUT_SCHEMA,
        budget=AgentBudget(max_model_requests=2, max_total_tokens=10),
    )

    with pytest.raises(AgentDriverFailure) as failure:
        await driver.decide_async(context)

    assert failure.value.code == "AGENT_BUDGET_EXCEEDED"
    assert failure.value.model_request_count is None
    assert calls == 1


@pytest.mark.anyio
async def test_v4_journal_records_sdk_requests_separately_from_one_driver_turn() -> (
    None
):
    """A PydanticAI repair consumes two SDK requests but one agent decision."""

    pydantic_ai = pytest.importorskip("pydantic_ai")
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    empty = {
        "$schema": DIALECT,
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    output_schema = {
        "$schema": DIALECT,
        "type": "object",
        "properties": {"score": {"type": "integer"}},
        "required": ["score"],
        "additionalProperties": False,
    }
    calls = 0

    def respond(messages, info):  # type: ignore[no-untyped-def]
        nonlocal calls
        del messages
        calls += 1
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {} if calls == 1 else {"score": 7},
                    tool_call_id=f"reply-{calls}",
                )
            ]
        )

    spec = workflow(
        "sdk-request-accounting",
        "1",
        (
            agent_step(
                "answer",
                "native",
                tools=(),
                budget=AgentBudget(max_model_requests=2),
                contract=NodeContract(empty, output_schema, {}),
            ),
        ),
        schema_version="v6-light-workflow-v4",
        input_schema=empty,
        output_schema=output_schema,
        output_bindings={"/score": ValueBinding("step_output", "/score", "answer")},
    )
    engine = WorkflowEngine(
        InMemoryRunJournal(),
        LocalToolExecutor(),
        {"native": PydanticAIDriver(agent=pydantic_ai.Agent(FunctionModel(respond)))},
    )

    completed = await engine.run_async(spec, {})

    usage = completed.state["agents"]["answer"]["usage"]
    assert completed.status == "SUCCEEDED"
    assert calls == 2
    assert usage["driver_invocations"] == 1
    assert usage["model_requests"] == 2
    assert usage["model_requests_unknown"] is False
    assert usage["transport_attempts_unknown"] is True


@pytest.mark.anyio
async def test_agents_sdk_v4_uses_typed_structured_output() -> None:
    agents = pytest.importorskip("agents")
    structured_schema = {
        "$schema": DIALECT,
        "type": "object",
        "properties": {"score": {"type": "integer"}},
        "required": ["score"],
        "additionalProperties": False,
    }
    model = _scripted_agents_model([[_agents_message('{"score":7}')]])
    driver = OpenAIAgentsDriver(agent=agents.Agent(name="v4", model=model))
    context = replace(
        _context(),
        workflow_schema_version="v6-light-workflow-v4",
        allowed_tools=(),
        tool_specs=(),
        output_schema=structured_schema,
        budget=AgentBudget(),
    )

    decision = await driver.decide_async(context)

    assert decision.output == {"score": 7}
    assert decision.usage is not None


@pytest.mark.anyio
async def test_agents_sdk_v4_hides_business_tools_after_budget_is_spent() -> None:
    agents = pytest.importorskip("agents")
    model = _scripted_agents_model(
        [[_agents_message('{"answer":"final answer"}')]]
    )
    driver = OpenAIAgentsDriver(agent=agents.Agent(name="spent-tools", model=model))
    context = replace(
        _context(),
        workflow_schema_version="v6-light-workflow-v4",
        output_schema=OUTPUT_SCHEMA,
        budget=AgentBudget(max_model_requests=1, max_tool_calls=1),
        tool_calls_remaining=0,
    )

    prepared = driver._prepare_native(context)[1]
    assert prepared.tools == []
    decision = await driver.decide_async(context)
    assert decision.output == {"answer": "final answer"}
    assert decision.tool_calls == ()


@pytest.mark.anyio
async def test_agents_sdk_v4_honors_remaining_request_budget_inside_runner() -> None:
    agents = pytest.importorskip("agents")
    model = _scripted_agents_model(
        [
            [_agents_function_call("lookup", {"unexpected": True}, "invalid-1")],
            [_agents_message('{"answer":"would exceed budget"}')],
        ]
    )
    driver = OpenAIAgentsDriver(agent=agents.Agent(name="budgeted", model=model))
    context = replace(
        _context(),
        workflow_schema_version="v6-light-workflow-v4",
        output_schema=OUTPUT_SCHEMA,
        budget=AgentBudget(max_model_requests=1),
    )

    with pytest.raises(AgentDriverFailure) as failure:
        await driver.decide_async(context)

    assert failure.value.code == "AGENT_BUDGET_EXCEEDED"
    assert failure.value.model_request_count == 1
    assert len(model.calls) == 1


def test_pydantic_ai_prioritizes_tool_requests_over_a_simultaneous_reply() -> None:
    pydantic_ai = pytest.importorskip("pydantic_ai")
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    def respond(messages, info):  # type: ignore[no-untyped-def]
        del messages
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {"message": "premature"},
                    tool_call_id="reply-call",
                ),
                ToolCallPart(
                    "lookup",
                    {"query": "needed"},
                    tool_call_id="tool-call",
                ),
            ]
        )

    decision = PydanticAIDriver(agent=pydantic_ai.Agent(FunctionModel(respond))).decide(
        _context()
    )

    assert decision.message == ""
    assert decision.tool_calls[0].tool == LOOKUP_TOOL
    assert decision.tool_calls[0].arguments == {"query": "needed"}


def test_pydantic_ai_uses_typed_final_output() -> None:
    pydantic_ai = pytest.importorskip("pydantic_ai")
    from pydantic_ai.models.test import TestModel

    model = TestModel(call_tools=[], custom_output_args={"message": "finished"})
    decision = PydanticAIDriver(agent=pydantic_ai.Agent(model)).decide(
        _context(_tool_spec("final_reply"))
    )

    assert decision.to_mapping() == {"message": "finished", "tool_calls": []}
    parameters = model.last_model_request_parameters
    assert parameters is not None
    assert [tool.name for tool in parameters.function_tools] == ["final_reply"]
    assert [tool.name for tool in parameters.output_tools] == ["final_reply_1"]


def test_pydantic_ai_reports_framework_neutral_usage() -> None:
    pydantic_ai = pytest.importorskip("pydantic_ai")
    from pydantic_ai.models.test import TestModel

    observed: list[AgentInvocationUsage] = []
    driver = PydanticAIDriver(
        agent=pydantic_ai.Agent(
            TestModel(call_tools=[], custom_output_args={"message": "finished"})
        ),
        usage_observer=observed.append,
        model_name="test:model",
    )

    driver.decide(_context())

    assert len(observed) == 1
    assert observed[0].request_count == 1
    assert observed[0].provider == "pydantic-ai"
    assert observed[0].model == "test:model"
    assert observed[0].duration_seconds is not None


def test_pydantic_ai_schema_validator_uses_one_framework_retry() -> None:
    pydantic_ai = pytest.importorskip("pydantic_ai")
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    requests = []

    def respond(messages, info):  # type: ignore[no-untyped-def]
        requests.append((messages, info))
        if len(requests) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup",
                        {"unexpected": True},
                        tool_call_id="invalid-call",
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "lookup",
                    {"query": "corrected"},
                    tool_call_id="valid-call",
                )
            ]
        )

    driver = PydanticAIDriver(agent=pydantic_ai.Agent(FunctionModel(respond)))
    decision = driver.decide(_context())

    assert len(requests) == 2
    assert decision.tool_calls[0].arguments == {"query": "corrected"}
    assert "RetryPromptPart" in repr(requests[1][0][-1])


@pytest.mark.parametrize("mode", ["unknown", "retry_exhausted"])
def test_pydantic_ai_maps_invalid_model_tool_output_to_stable_failure(
    mode: str,
) -> None:
    pydantic_ai = pytest.importorskip("pydantic_ai")
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    calls = 0

    def respond(messages, info):  # type: ignore[no-untyped-def]
        nonlocal calls
        del messages, info
        calls += 1
        if mode == "unknown":
            return ModelResponse(
                parts=[ToolCallPart("unknown", {}, tool_call_id=f"call-{calls}")]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "lookup",
                    {"unexpected": True},
                    tool_call_id=f"call-{calls}",
                )
            ]
        )

    with pytest.raises(AgentDriverFailure) as failure:
        PydanticAIDriver(agent=pydantic_ai.Agent(FunctionModel(respond))).decide(
            _context()
        )
    assert failure.value.code == "AGENT_MODEL_OUTPUT_INVALID"
    assert calls == 2


def test_agents_sdk_native_tools_interrupt_before_handler_and_expose_schemas() -> None:
    agents = pytest.importorskip("agents")
    lookup = _tool_spec()
    detail = _tool_spec("detail", DETAIL_TOOL)
    model = _scripted_agents_model(
        [
            [
                _agents_function_call("lookup", {"query": "one"}, "call-1"),
                _agents_function_call("detail", {"query": "two"}, "call-2"),
            ]
        ]
    )

    async def caller_handler(context, arguments):  # type: ignore[no-untyped-def]
        del context, arguments
        raise AssertionError("caller-configured tool must not execute")

    caller_tool = agents.FunctionTool(
        name="caller_configured_tool",
        description="Must be removed by the adapter.",
        params_json_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        on_invoke_tool=caller_handler,
    )
    driver = OpenAIAgentsDriver(
        agent=agents.Agent(name="native", model=model, tools=[caller_tool])
    )

    decision = driver.decide(_context(lookup, detail))

    assert driver.driver_version == "openai-agents-native-v2@0.22.3"
    assert decision.message == ""
    assert [call.tool for call in decision.tool_calls] == [LOOKUP_TOOL, DETAIL_TOOL]
    assert [call.arguments for call in decision.tool_calls] == [
        {"query": "one"},
        {"query": "two"},
    ]
    assert [call.call_id for call in decision.tool_calls] == ["call-1", "call-2"]
    assert len(model.calls) == 1
    tools = model.calls[0].tools
    assert [tool.name for tool in tools] == ["lookup", "detail"]
    assert tools[0].params_json_schema == INPUT_SCHEMA
    assert tools[0].output_json_schema == OUTPUT_SCHEMA
    assert tools[0].strict_json_schema is True
    assert tools[0].needs_approval is True
    assert tools[0].tool_input_guardrails
    assert "Examples:" in tools[0].description
    assert "NOT_FOUND" in tools[0].description
    assert model.calls[0].handoffs == []
    model_tool_context = repr(tools)
    assert LOOKUP_TOOL.tool_id not in model_tool_context
    assert LOOKUP_TOOL.version not in model_tool_context
    assert LOOKUP_TOOL.package_digest not in model_tool_context
    assert "tool_id" not in model_tool_context
    assert "package_digest" not in model_tool_context

    sent_context = repr(model.calls[0].input)
    assert "tool_id" not in sent_context
    assert "package_digest" not in sent_context
    assert '"version"' not in sent_context
    assert "Return exactly one JSON" not in sent_context


@pytest.mark.anyio
async def test_agents_sdk_v3_uses_native_async_run() -> None:
    agents = pytest.importorskip("agents")
    model = _scripted_agents_model(
        [[_agents_function_call("lookup", {"query": "async"}, "call-1")]]
    )
    driver = OpenAIAgentsDriver(agent=agents.Agent(name="native", model=model))

    decision = await driver.decide_async(
        replace(_context(), workflow_schema_version="v6-light-workflow-v3")
    )

    assert decision.message == ""
    assert decision.tool_calls[0].arguments == {"query": "async"}


def test_agents_sdk_prioritizes_tool_requests_over_a_simultaneous_reply() -> None:
    agents = pytest.importorskip("agents")
    model = _scripted_agents_model(
        [
            [
                _agents_message('{"message":"premature"}'),
                _agents_function_call("lookup", {"query": "needed"}, "call-1"),
            ]
        ]
    )

    decision = OpenAIAgentsDriver(
        agent=agents.Agent(name="native", model=model)
    ).decide(_context())

    assert decision.message == ""
    assert decision.tool_calls[0].tool == LOOKUP_TOOL
    assert decision.tool_calls[0].arguments == {"query": "needed"}


def test_agents_sdk_guardrail_uses_one_framework_retry() -> None:
    agents = pytest.importorskip("agents")
    model = _scripted_agents_model(
        [
            [_agents_function_call("lookup", {"unexpected": True}, "call-1")],
            [_agents_function_call("lookup", {"query": "corrected"}, "call-2")],
        ]
    )

    decision = OpenAIAgentsDriver(
        agent=agents.Agent(name="native", model=model)
    ).decide(_context())

    assert len(model.calls) == 2
    assert decision.tool_calls[0].arguments == {"query": "corrected"}
    assert "did not match the declared schema" in repr(model.calls[1].input)


def test_agents_sdk_uses_typed_final_output() -> None:
    agents = pytest.importorskip("agents")
    model = _scripted_agents_model([[_agents_message('{"message":"finished"}')]])

    decision = OpenAIAgentsDriver(
        agent=agents.Agent(name="native", model=model)
    ).decide(_context())

    assert decision.to_mapping() == {"message": "finished", "tool_calls": []}
    output_schema = model.calls[0].output_schema
    assert output_schema is not None
    assert output_schema.json_schema()["additionalProperties"] is False
    assert set(output_schema.json_schema()["properties"]) == {"message"}


def test_agents_sdk_reports_usage_and_maps_timeout() -> None:
    agents = pytest.importorskip("agents")
    from agents.exceptions import ModelTimeoutError
    from agents.testing import ModelStep, ScriptedModel, assistant_message
    from agents.usage import Usage

    observed: list[AgentInvocationUsage] = []
    model = ScriptedModel(
        [
            ModelStep(
                output=(assistant_message('{"message":"finished"}'),),
                usage=Usage(
                    requests=1,
                    input_tokens=10,
                    output_tokens=2,
                    total_tokens=12,
                ),
            )
        ]
    )
    driver = OpenAIAgentsDriver(
        agent=agents.Agent(name="native", model=model),
        usage_observer=observed.append,
        model_name="test:model",
    )

    driver.decide(_context())

    assert observed[0].request_count == 1
    assert observed[0].total_tokens == 12
    assert observed[0].provider == "openai-agents"

    timeout_model = ScriptedModel(
        [ModelStep.raise_error(ModelTimeoutError(timeout_seconds=0.01))]
    )
    with pytest.raises(AgentDriverFailure) as failure:
        OpenAIAgentsDriver(
            agent=agents.Agent(name="native", model=timeout_model)
        ).decide(_context())
    assert failure.value.code == "AGENT_PROVIDER_FAILURE"


def test_pydantic_ai_maps_provider_failure() -> None:
    pydantic_ai = pytest.importorskip("pydantic_ai")
    from pydantic_ai.exceptions import ModelHTTPError
    from pydantic_ai.models.function import FunctionModel

    def fail(messages, info):  # type: ignore[no-untyped-def]
        del messages, info
        raise ModelHTTPError(503, "test:model", {"error": "unavailable"})

    with pytest.raises(AgentDriverFailure) as failure:
        PydanticAIDriver(agent=pydantic_ai.Agent(FunctionModel(fail))).decide(
            _context()
        )
    assert failure.value.code == "AGENT_PROVIDER_FAILURE"


@pytest.mark.parametrize("mode", ["unknown", "retry_exhausted"])
def test_agents_sdk_maps_invalid_model_tool_output_to_stable_failure(
    mode: str,
) -> None:
    agents = pytest.importorskip("agents")
    if mode == "unknown":
        responses = [[_agents_function_call("unknown", {}, "call-1")]]
    else:
        responses = [
            [_agents_function_call("lookup", {"unexpected": True}, "call-1")],
            [_agents_function_call("lookup", {"unexpected": True}, "call-2")],
        ]
    model = _scripted_agents_model(responses)

    with pytest.raises(AgentDriverFailure) as failure:
        OpenAIAgentsDriver(agent=agents.Agent(name="native", model=model)).decide(
            _context()
        )
    assert failure.value.code == "AGENT_MODEL_OUTPUT_INVALID"
    assert len(model.calls) == 1 if mode == "unknown" else len(model.calls) == 2


@pytest.mark.parametrize("adapter", ["pydantic", "agents"])
def test_native_adapters_reject_invalid_examples_and_prior_outputs_before_model(
    adapter: str,
) -> None:
    bad_example = AgentToolSpec(
        "lookup",
        LOOKUP_TOOL,
        "Look up a record.",
        INPUT_SCHEMA,
        OUTPUT_SCHEMA,
        examples=(AgentToolExample({"wrong": True}, output={"answer": "x"}),),
    )
    invalid_history = (
        {
            "tool_results": [
                {
                    "name": "lookup",
                    "arguments": {"query": "alpha"},
                    "succeeded": True,
                    "output": {"wrong": "shape"},
                    "error": None,
                }
            ]
        },
    )

    if adapter == "pydantic":
        pydantic_ai = pytest.importorskip("pydantic_ai")
        from pydantic_ai.models.test import TestModel

        bad_example_driver = PydanticAIDriver(
            agent=pydantic_ai.Agent(TestModel(call_tools=[]))
        )
        output_driver = PydanticAIDriver(
            agent=pydantic_ai.Agent(TestModel(call_tools=[]))
        )
    else:
        agents = pytest.importorskip("agents")
        bad_example_driver = OpenAIAgentsDriver(
            agent=agents.Agent(
                name="native",
                model=_scripted_agents_model(
                    [[_agents_message('{"message":"unused"}')]]
                ),
            )
        )
        output_driver = OpenAIAgentsDriver(
            agent=agents.Agent(
                name="native",
                model=_scripted_agents_model(
                    [[_agents_message('{"message":"unused"}')]]
                ),
            )
        )

    with pytest.raises(AgentDriverFailure) as contract_failure:
        bad_example_driver.decide(_context(bad_example))
    assert contract_failure.value.code == "AGENT_TOOL_CONTRACT_INVALID"

    with pytest.raises(AgentDriverFailure) as output_failure:
        output_driver.decide(replace(_context(), prior_tool_results=invalid_history))
    assert output_failure.value.code == "AGENT_TOOL_OUTPUT_INVALID"


def test_native_schema_compiler_rejects_an_unresolved_local_reference() -> None:
    pydantic_ai = pytest.importorskip("pydantic_ai")
    from pydantic_ai.models.test import TestModel

    unresolved_schema = {
        "$schema": DIALECT,
        "type": "object",
        "properties": {"query": {"$ref": "#/$defs/missing"}},
        "required": ["query"],
        "additionalProperties": False,
    }
    contract = AgentToolSpec(
        "lookup",
        LOOKUP_TOOL,
        "Look up a record.",
        unresolved_schema,
        OUTPUT_SCHEMA,
    )

    with pytest.raises(AgentDriverFailure) as failure:
        PydanticAIDriver(agent=pydantic_ai.Agent(TestModel(call_tools=[]))).decide(
            _context(contract)
        )
    assert failure.value.code == "AGENT_TOOL_CONTRACT_INVALID"


def test_kernel_blocks_invalid_tool_output_before_a_second_model_request() -> None:
    pydantic_ai = pytest.importorskip("pydantic_ai")
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    requests = 0

    def respond(messages, info):  # type: ignore[no-untyped-def]
        nonlocal requests
        del messages
        requests += 1
        if requests == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup",
                        {"query": "alpha"},
                        tool_call_id="lookup-call",
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {"message": "must not be reached"},
                    tool_call_id="reply-call",
                )
            ]
        )

    executor = LocalToolExecutor()
    executor.register(LOOKUP_TOOL, lambda arguments: {"wrong": arguments})
    engine = WorkflowEngine(
        InMemoryRunJournal(),
        executor,
        {"native": PydanticAIDriver(agent=pydantic_ai.Agent(FunctionModel(respond)))},
    )
    spec = workflow(
        "invalid-output",
        "1",
        [agent_step("plan", "native", max_turns=2, tools=(_tool_spec(),))],
        schema_version="v6-light-workflow-v2",
    )

    waiting = engine.run(spec, run_id="invalid-output-run")
    assert waiting.status == "WAITING_APPROVAL"
    blocked = engine.resume(waiting.run_id, approved=True)

    assert blocked.status == "BLOCKED"
    assert blocked.state["failure"]["code"] == "AGENT_TOOL_OUTPUT_INVALID"
    assert requests == 1
    serialized = json.dumps(blocked.to_mapping())
    assert "pydantic_ai" not in serialized
    assert "ModelResponse" not in serialized


def _restart_driver(adapter: str, phase: str):
    if adapter == "pydantic":
        pydantic_ai = pytest.importorskip("pydantic_ai")
        from pydantic_ai.messages import ModelResponse, ToolCallPart
        from pydantic_ai.models.function import FunctionModel

        def respond(messages, info):  # type: ignore[no-untyped-def]
            del messages
            if phase == "tool":
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "lookup",
                            {"query": "restart"},
                            tool_call_id="restart-tool",
                        )
                    ]
                )
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        info.output_tools[0].name,
                        {"message": "finished after restart"},
                        tool_call_id="restart-final",
                    )
                ]
            )

        return PydanticAIDriver(agent=pydantic_ai.Agent(FunctionModel(respond)))
    agents = pytest.importorskip("agents")
    output = (
        [_agents_function_call("lookup", {"query": "restart"}, "restart-tool")]
        if phase == "tool"
        else [_agents_message('{"message":"finished after restart"}')]
    )
    return OpenAIAgentsDriver(
        agent=agents.Agent(name="native", model=_scripted_agents_model([output]))
    )


@pytest.mark.parametrize("adapter", ["pydantic", "agents"])
def test_native_driver_approval_resumes_from_neutral_sqlite_state(
    adapter: str, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    journal_path = tmp_path / f"{adapter}.sqlite"
    calls: list[dict] = []
    executor = LocalToolExecutor()
    executor.register(
        LOOKUP_TOOL,
        lambda arguments: calls.append(dict(arguments)) or {"answer": "known"},
    )
    spec = workflow(
        "native-restart",
        "1",
        [agent_step("plan", "native", max_turns=2, tools=(_tool_spec(),))],
        schema_version="v6-light-workflow-v2",
    )
    first = WorkflowEngine(
        SQLiteRunJournal(journal_path),
        executor,
        {"native": _restart_driver(adapter, "tool")},
    )

    waiting = first.run(spec, run_id=f"{adapter}-restart")

    assert waiting.status == "WAITING_APPROVAL"
    assert calls == []
    resumed_executor = LocalToolExecutor()
    resumed_executor.register(
        LOOKUP_TOOL,
        lambda arguments: calls.append(dict(arguments)) or {"answer": "known"},
    )
    resumed = WorkflowEngine(
        SQLiteRunJournal(journal_path),
        resumed_executor,
        {"native": _restart_driver(adapter, "final")},
    ).resume(waiting.run_id, approved=True)

    assert resumed.status == "SUCCEEDED"
    assert resumed.state["values"]["plan"] == {"message": "finished after restart"}
    assert calls == [{"query": "restart"}]
    serialized = json.dumps(resumed.to_mapping())
    assert "ModelResponse" not in serialized
    assert "RunResult" not in serialized


@pytest.mark.anyio
@pytest.mark.parametrize("adapter", ["pydantic", "agents"])
async def test_native_driver_agent_tool_cancellation_is_cooperative(
    adapter: str,
) -> None:
    anyio = pytest.importorskip("anyio")

    class CooperativeExecutor:
        async def execute_async(self, request, cancellation):  # type: ignore[no-untyped-def]
            del request
            while not cancellation.cancelled:
                await anyio.sleep(0.005)
            raise ToolExecutionStoppedError("operation stopped")

        async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
            del request

    message_schema = {
        "$schema": DIALECT,
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
        "additionalProperties": False,
    }
    step = agent_step(
        "plan",
        "native",
        max_turns=2,
        tools=(_tool_spec(),),
        contract=NodeContract(
            INPUT_SCHEMA,
            message_schema,
            {"/query": ValueBinding("workflow_input", "/query")},
        ),
    )
    spec = workflow(
        "native-cancel",
        "1",
        (step,),
        schema_version="v6-light-workflow-v3",
        input_schema=INPUT_SCHEMA,
        output_schema=message_schema,
        output_bindings={"/message": ValueBinding("step_output", "/message", "plan")},
    )
    engine = WorkflowEngine(
        InMemoryRunJournal(),
        CooperativeExecutor(),
        {"native": _restart_driver(adapter, "tool")},
    )
    waiting = await engine.run_async(spec, {"query": "cancel"})
    approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))
    result: dict[str, Any] = {}

    async with anyio.create_task_group() as group:

        async def resume() -> None:
            result["snapshot"] = await engine.resume_async(
                waiting.run_id,
                approvals=(ApprovalDecision(approval_id, True),),
            )

        group.start_soon(resume)
        while True:
            await anyio.sleep(0.005)
            current = engine.get(waiting.run_id)
            if any(
                action.get("status") == "DISPATCHED"
                for action in current.state["actions"].values()
            ):
                break
        requested = await engine.request_cancel_async(waiting.run_id)
        assert requested.status == "CANCEL_REQUESTED"

    assert result["snapshot"].status == "CANCELLED"
