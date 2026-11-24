from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from threading import Event
import time

from agents import Usage
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call
from fastapi.testclient import TestClient
import httpx
from openai import APIConnectionError
from pydantic import ValidationError
import pytest

from platform_kernel.api import create_app
from platform_kernel.composition import PlatformApplication
from platform_kernel.database import connect_database
from platform_kernel.agent_runtime.models import MemorySummaryV3
from platform_kernel.agent_runtime.provider import (
    build_intent_router_model_settings,
    build_intent_router_provider,
)
from platform_kernel.intent_routing import (
    INTENT_ROUTING_FAILURE_CODES,
    IntentDecisionV4,
    IntentRouter,
    IntentRoutingError,
)
from platform_kernel.intent_routing import router as router_module
from platform_kernel.intent_routing import provider_call as provider_call_module


def test_deepseek_router_prompt_treats_confirmation_proposal_as_execution():
    instructions = router_module._DEEPSEEK_INSTRUCTIONS

    assert "先核实资料并提交确认" in instructions
    assert "EXECUTE_ON_DATA|NONE|NONE" in instructions


@pytest.mark.parametrize("instructions", [
    pytest.param(router_module._DEEPSEEK_INSTRUCTIONS, id="semantic-literal"),
    pytest.param(router_module._INSTRUCTIONS, id="structured"),
])
def test_router_prompt_classifies_the_requested_deliverable(instructions):
    text = " ".join(instructions.split())

    assert "CONSULT: explain a concept or answer a factual question" in text
    assert "DESIGN: recommend an approach or give analysis steps" in text
    assert "CREATE_WORKFLOW: produce a workflow artifact" in text
    assert 'A request for a workflow draft is enough even without "reusable" or "exportable"' in text
    assert "Do not execute" in text and "does not change the requested deliverable" in text


@pytest.mark.parametrize(
    "message,semantic,response_kind,policy",
    [
        ("解释一下哪些情况适合做水化学分析。", "CONSULT", "DIALOGUE", "READ_ONLY"),
        ("建议一下这批样品的筛选规则和分析步骤，先别计算。", "DESIGN", "DIALOGUE", "READ_ONLY"),
        ("起草一个从样品筛选到质量报告的工作流，先别计算。", "CREATE_WORKFLOW", "WORKFLOW_DRAFT", None),
    ],
    ids=["explanation", "analysis-design", "workflow-artifact"],
)
def test_kernel_maps_design_and_draft_without_execution_authorization(
    kernel_settings, message, semantic, response_kind, policy,
):
    model = ScriptedModel([ModelStep(output=[assistant_message(f"{semantic}|NONE|NONE")])])
    router = IntentRouter(kernel_settings().intent_router, agent_slots=asyncio.Semaphore(1), model=model)

    result = asyncio.run(router.route(message=message, memory=None, history=()))

    assert result.intent.semantic_intent == semantic
    assert result.intent.response_kind == response_kind
    assert result.intent.dialogue_policy == policy
    assert result.intent.control_intent is None
    assert result.provider_request_count == 1


class _UsageLedger:
    def __init__(self, *, create=None) -> None:
        self.created = []
        self.finished = []
        self._create = create

    def create_context_view(self, **value):
        if self._create is not None:
            return self._create(**value)
        self.created.append(value)
        return f"view-{len(self.created)}"

    def finish_context_view(self, view_id, **value) -> None:
        self.finished.append((view_id, value))

    def begin_provider_request(self, **_value) -> str:
        return "provider-request"

    def finish_provider_request(self, _request_id, **_value) -> None:
        return None

    def mark_protocol_failure(self, _agent_run_id):
        return None

    def stage_protocol_retry(self, _context) -> bool:
        return False

    def planned_approval_tool(self, _agent_run_id, _enabled_tools):
        return None


def _decision(value: str, *, policy: str | None = None, **extra) -> dict:
    semantic = (
        "EXECUTE_ON_DATA" if policy == "EXECUTION_PROPOSAL"
        else "CREATE_WORKFLOW" if value == "WORKFLOW_DRAFT"
        else "UNKNOWN" if value == "NEEDS_CLARIFICATION"
        else "CONSULT"
    )
    return {
        "schemaVersion": "intent-decision.v4",
        "responseKind": value,
        "semanticIntent": semantic,
        "taskAction": "NONE",
        "dialoguePolicy": (
            policy if policy is not None
            else "READ_ONLY" if value == "DIALOGUE" else None
        ),
        "controlIntent": None,
        "clarificationReason": "REQUEST_KIND_AMBIGUOUS" if value == "NEEDS_CLARIFICATION" else None,
        **extra,
    }



def _model(value: dict, *, usage: Usage | None = None) -> ScriptedModel:
    literal = "|".join((
        value["semanticIntent"],
        value["taskAction"],
        value["clarificationReason"] or "NONE",
    ))
    return ScriptedModel([
        ModelStep(
            output=[assistant_message(literal)],
            usage=usage or Usage(input_tokens=11, output_tokens=3, total_tokens=14),
        )
    ])


def _reply_step() -> ModelStep:
    return ModelStep(output=[function_call("submit_reply", {
        "conclusion": {"text": "我可以直接解释这个问题。", "citations": []},
        "details": [],
        "unresolved_items": [],
        "referenced_workflow_runs": [],
        "include_execution_status": False,
        "interaction_options": [],
        "workflow_draft": None,
    }, call_id="reply")])


def _reply_model() -> ScriptedModel:
    return ScriptedModel([_reply_step()])


def _clarification_reply_model() -> ScriptedModel:
    return ScriptedModel([
        ModelStep(output=[function_call("submit_reply", {
            "conclusion": None,
            "details": [],
            "unresolved_items": [{
                "text": "你希望得到解释、分析建议，还是可复用的处理流程？",
                "citations": [],
            }],
            "referenced_workflow_runs": [],
            "include_execution_status": False,
            "interaction_options": [
                {"label": "解释问题", "description": "直接解释当前问题", "message": "请直接解释当前问题。"},
                {"label": "比较方法", "description": "比较适用方法", "message": "请比较当前可行的方法。"},
            ],
            "workflow_draft": None,
        }, call_id="clarification")])
    ])


def _await_terminal_turn(client: TestClient, session_id: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        turn = client.get(
            f"/api/v12/sessions/{session_id}/messages"
        ).json()["turns"][-1]
        if turn["status"] in {"COMPLETE", "FAILED"}:
            return turn
        time.sleep(0.02)
    raise AssertionError("message did not reach a terminal state")


def _await_run_status(application: PlatformApplication, agent_run_id: str) -> str:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with connect_database(application.database) as connection:
            row = connection.execute(
                "SELECT status FROM agent_runs WHERE agent_run_id=?",
                (agent_run_id,),
            ).fetchone()
        if row is not None and row["status"] in {"COMPLETE", "FAILED"}:
            return row["status"]
        time.sleep(0.02)
    raise AssertionError("agent run did not reach a terminal state")


def test_intent_decision_v4_is_strict_and_closed():
    assert INTENT_ROUTING_FAILURE_CODES == {
        "AGENT_PROVIDER_UNAVAILABLE",
        "AGENT_DEADLINE_EXCEEDED",
        "AGENT_CONTEXT_TOO_LARGE",
        "AGENT_INTENT_ROUTING_INVALID",
        "AGENT_SDK_CONFIGURATION_INVALID",
    }
    legal = (
        _decision("DIALOGUE"),
        _decision("DIALOGUE", policy="EXECUTION_PROPOSAL"),
        _decision("WORKFLOW_DRAFT"),
        _decision("NEEDS_CLARIFICATION"),
        {
            "schemaVersion": "intent-decision.v4",
            "responseKind": "CONTROL",
            "semanticIntent": "UNSPECIFIED",
            "taskAction": "NONE",
            "dialoguePolicy": None,
            "controlIntent": "STOP_ANALYSIS",
            "clarificationReason": None,
        },
    )
    decisions = [IntentDecisionV4.model_validate(value) for value in legal]
    assert [decision.response_kind for decision in decisions] == [
        "DIALOGUE", "DIALOGUE", "WORKFLOW_DRAFT", "NEEDS_CLARIFICATION", "CONTROL",
    ]
    assert decisions[0].model_dump() == legal[0]
    for value in (
        _decision("OTHER"),
        _decision("DIALOGUE", reason="because"),
        {"responseKind": "DIALOGUE"},
        _decision("WORKFLOW_DRAFT", policy="READ_ONLY"),
        _decision("DIALOGUE", semanticIntent="CREATE_WORKFLOW"),
        {
            **legal[-1],
            "semanticIntent": "CONSULT",
        },
        {
            **legal[-1],
            "taskAction": "CONTINUE",
        },
        {
            "schema_version": "intent-decision.v4",
            "response_kind": "DIALOGUE",
            "semantic_intent": "CONSULT",
            "task_action": "NONE",
            "dialogue_policy": "READ_ONLY",
            "control_intent": None,
            "clarification_reason": None,
        },
        {"schemaVersion": "intent-decision.v4", "responseKind": "DIALOGUE", "semanticIntent": "CONSULT", "taskAction": "NONE", "dialoguePolicy": None, "controlIntent": None, "clarificationReason": None},
    ):
        with pytest.raises(ValidationError):
            IntentDecisionV4.model_validate(value)


def test_kernel_rejects_review_action_without_pending_user_review(kernel_settings):
    model = _model(_decision("DIALOGUE", taskAction="REVIEW"))
    router = IntentRouter(
        kernel_settings().intent_router,
        agent_slots=asyncio.Semaphore(1),
        model=model,
    )

    result = asyncio.run(router.route(
        message="请确认任务是否完成",
        memory=None,
        history=(),
        task_context={"objective": "形成报告"},
        candidate_context={
            "has_task": True,
            "allowed_task_actions": ["NONE", "REVISE", "CONTINUE"],
        },
    ))

    assert result.intent.response_kind == "NEEDS_CLARIFICATION"
    assert result.intent.clarification_reason == "TASK_CONFLICT"
    assert result.intent.dialogue_policy is None


def test_intent_router_provider_leaves_retry_to_persisted_router_loop(kernel_settings):
    settings = kernel_settings().intent_router
    provider = build_intent_router_provider(settings)
    model_settings = build_intent_router_model_settings(settings)
    try:
        assert provider.client.max_retries == 0
        assert model_settings.retry.max_retries == 0
        assert model_settings.max_tokens == 64
        assert model_settings.reasoning.effort == "none"
        assert model_settings.timeout == 120
    finally:
        asyncio.run(provider.close())


def test_minimax_m3_router_disables_thinking(kernel_settings):
    settings = replace(
        kernel_settings().intent_router,
        provider="minimax",
        protocol="chat_completions",
        model="MiniMax-M3",
    )
    assert build_intent_router_model_settings(settings).extra_body == {
        "reasoning_split": True,
        "thinking": {"type": "disabled"},
    }
    assert build_intent_router_model_settings(settings).include_usage is True


def test_minimax_m31_router_uses_low_adaptive_reasoning(kernel_settings):
    settings = replace(
        kernel_settings().intent_router,
        provider="minimax",
        protocol="chat_completions",
        model="MiniMax-M3.1-Flash-Preview",
    )
    model_settings = build_intent_router_model_settings(settings)

    assert model_settings.reasoning is not None
    assert model_settings.reasoning.effort == "low"
    assert model_settings.extra_body == {"reasoning_split": True}
    assert model_settings.include_usage is True


def test_router_uses_no_tools_and_loads_complete_turns_within_budget(kernel_settings):
    model = _model(_decision("DIALOGUE"))
    router = IntentRouter(
        kernel_settings().intent_router,
        agent_slots=asyncio.Semaphore(1),
        model=model,
    )
    history = tuple(
        item
        for revision in range(1, 4)
        for item in (
            (revision, "user", f"user-{revision}"),
            (revision, "assistant", f"assistant-{revision}"),
        )
    )
    memory = MemorySummaryV3(
        goals=["understand the data"],
        constraints=[],
        decisions=[],
        unresolved_items=[],
    )

    result = asyncio.run(router.route(
        message="我有几份监测数据，应该怎么分析",
        memory=memory,
        history=history,
    ))

    assert result.decision == "DIALOGUE"
    assert result.dialogue_policy == "READ_ONLY"
    assert (result.provider, result.model) == ("deepseek", "test-router-model")
    assert result.usage.total_tokens == 14
    assert result.provider_request_count == 1
    assert result.trace_id.startswith("trace_")
    assert result.started_at.endswith("Z") and result.finished_at.endswith("Z")
    assert result.duration_ms >= 0
    assert len(model.calls) == 1
    call = model.calls[0]
    assert call.tools == []
    assert call.output_schema is None
    assert "SEMANTIC|TASK_ACTION|CLARIFICATION" in call.system_instructions
    encoded = json.dumps(call.input, ensure_ascii=False)
    assert all(value in encoded for value in (
        "understand the data", "user-1", "assistant-1",
        "user-2", "assistant-2", "user-3",
        "assistant-3", "我有几份监测数据，应该怎么分析",
    ))
    assert "Test product prompt" not in call.system_instructions


def test_router_invalid_output_retries_once_then_fails(kernel_settings):
    invalid = ModelStep(
        output=[assistant_message(json.dumps(
            _decision("DIALOGUE", reason="not allowed")
        ))],
        usage=Usage(input_tokens=11, output_tokens=3, total_tokens=14),
    )
    model = ScriptedModel([invalid, invalid])
    router = IntentRouter(
        kernel_settings().intent_router,
        agent_slots=asyncio.Semaphore(1),
        model=model,
    )

    with pytest.raises(IntentRoutingError) as failure:
        asyncio.run(router.route(message="你好", memory=None, history=()))

    assert failure.value.code == "AGENT_INTENT_ROUTING_INVALID"
    assert failure.value.upstream is False
    assert failure.value.usage.total_tokens == 28
    assert failure.value.provider_request_count == 2
    assert failure.value.trace_id.startswith("trace_")
    assert len(model.calls) == 2


def test_router_does_not_replay_a_transient_provider_failure(
    kernel_settings, monkeypatch,
) -> None:
    model = _model(_decision("DIALOGUE"))
    router = IntentRouter(
        kernel_settings().intent_router,
        agent_slots=asyncio.Semaphore(1),
        model=model,
    )
    original = provider_call_module.Runner.run
    calls = 0

    async def flaky(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise APIConnectionError(
                request=httpx.Request("POST", "http://intent-router.test/v1")
            )
        return await original(*args, **kwargs)

    monkeypatch.setattr(provider_call_module.Runner, "run", flaky)

    with pytest.raises(IntentRoutingError) as failure:
        asyncio.run(router.route(
            message="解释一下", memory=None, history=(),
        ))

    assert failure.value.code == "AGENT_PROVIDER_UNAVAILABLE"
    assert failure.value.upstream is True
    assert calls == 1
    assert len(model.calls) == 0


def test_minimax_router_accepts_exact_literal_without_sdk_response_format(
    kernel_settings,
):
    settings = replace(
        kernel_settings().intent_router,
        provider="minimax",
        protocol="chat_completions",
    )
    model = ScriptedModel([
        ModelStep(output=[assistant_message("CONSULT|NONE|NONE")])
    ])
    router = IntentRouter(
        settings,
        agent_slots=asyncio.Semaphore(1),
        model=model,
    )

    result = asyncio.run(router.route(message="解释一下", memory=None, history=()))

    assert result.decision == "DIALOGUE"
    assert result.dialogue_policy == "READ_ONLY"
    assert model.calls[0].output_schema is None


@pytest.mark.parametrize(
    "output",
    [
        '{"schemaVersion":"intent-decision.v3","decision":"DIALOGUE","dialoguePolicy":"READ_ONLY"}',
        "```\nCONSULT|NONE|NONE\n```", "CONSULT|NONE|NONE\nUNKNOWN|NONE|NONE",
        "consult|none|none", "Decision: CONSULT|NONE|NONE", "DIALOGUE",
    ],
)
def test_minimax_router_rejects_every_non_literal_output(kernel_settings, output):
    settings = replace(
        kernel_settings().intent_router,
        provider="minimax", protocol="chat_completions",
    )
    model = ScriptedModel([ModelStep(output=[assistant_message(output)]), ModelStep(output=[assistant_message(output)])])
    router = IntentRouter(settings, agent_slots=asyncio.Semaphore(1), model=model)
    with pytest.raises(IntentRoutingError) as failure:
        asyncio.run(router.route(message="解释一下", memory=None, history=()))
    assert failure.value.code == "AGENT_INTENT_ROUTING_INVALID"
    assert len(model.calls) == 2


def test_minimax_router_trims_only_ascii_edge_whitespace(kernel_settings):
    settings = replace(
        kernel_settings().intent_router,
        provider="minimax", protocol="chat_completions",
    )
    model = ScriptedModel([
        ModelStep(output=[assistant_message(" \tCONSULT|NONE|NONE\r\n")])
    ])
    router = IntentRouter(settings, agent_slots=asyncio.Semaphore(1), model=model)
    assert asyncio.run(router.route(
        message="解释一下", memory=None, history=()
    )).decision == "DIALOGUE"


def test_router_segment_deadline_starts_after_shared_admission(
    kernel_settings, monkeypatch,
):
    model = _model(_decision("DIALOGUE"))
    slots = asyncio.Semaphore(1)
    router = IntentRouter(
        kernel_settings().intent_router,
        agent_slots=slots,
        model=model,
    )
    deadline_started = asyncio.Event()
    original_timeout = router_module.asyncio.timeout

    def observe_timeout(delay):
        deadline_started.set()
        return original_timeout(delay)

    monkeypatch.setattr(router_module.asyncio, "timeout", observe_timeout)

    async def exercise():
        await slots.acquire()
        pending = asyncio.create_task(
            router.route(message="解释一下", memory=None, history=())
        )
        await asyncio.sleep(0)
        assert not deadline_started.is_set()
        slots.release()
        result = await pending
        assert result.decision == "DIALOGUE"
        assert deadline_started.is_set()

    asyncio.run(exercise())


def test_router_context_guard_prevents_provider_request(kernel_settings):
    model = _model(_decision("DIALOGUE"))
    settings = replace(
        kernel_settings().intent_router,
        context_window_tokens=32,
        max_output_tokens=16,
    )
    router = IntentRouter(settings, agent_slots=asyncio.Semaphore(1), model=model)

    with pytest.raises(IntentRoutingError) as failure:
        asyncio.run(router.route(message="很长的消息 " * 100, memory=None, history=()))

    assert failure.value.code == "AGENT_CONTEXT_TOO_LARGE"
    assert failure.value.provider_request_count == 0
    assert failure.value.usage.total_tokens == 0
    assert model.calls == ()


def test_router_v1_compiles_without_an_extra_provider_request(kernel_settings):
    model = _model(_decision("DIALOGUE"))
    ledger = _UsageLedger()

    router = IntentRouter(
        kernel_settings().intent_router,
        agent_slots=asyncio.Semaphore(1),
        model=model,
        usage_ledger=ledger,
    )
    history = tuple(
        item
        for revision in range(1, 11)
        for item in (
            (revision, "user", f"user-{revision} " + "x " * 500),
            (revision, "assistant", f"assistant-{revision} " + "y " * 500),
        )
    )

    result = asyncio.run(router.route(
        message="继续", memory=None, history=history,
    ))

    assert result.provider_request_count == 1
    assert len(model.calls) == 1
    compiled = next(item for item in ledger.created if item["status"] == "DISPATCHED")
    assert any(
        block["disposition"] in {"COMPACTED", "OMITTED"}
        for block in compiled["manifest"]["blocks"]
    )
    assert len(ledger.finished) == 1


def test_context_manifest_write_failure_prevents_router_provider_request(
    kernel_settings,
):
    model = _model(_decision("DIALOGUE"))

    def fail_write(**_value):
        raise RuntimeError("manifest unavailable")

    router = IntentRouter(
        kernel_settings().intent_router,
        agent_slots=asyncio.Semaphore(1),
        model=model,
        usage_ledger=_UsageLedger(create=fail_write),
    )

    with pytest.raises(RuntimeError, match="manifest unavailable"):
        asyncio.run(router.route(message="解释一下", memory=None, history=()))
    assert model.calls == ()


def test_platform_auto_message_persists_route_then_runs_decided_agent(
    kernel_settings,
):
    router_model = _model(_decision("DIALOGUE"))
    agent_model = _reply_model()
    application = PlatformApplication(
        settings=kernel_settings(),
        model=agent_model,
        intent_router_model=router_model,
    )

    with TestClient(create_app(application=application)) as client:
        session_id = client.post(
            "/api/v12/sessions", json={"projectId": "p"}
        ).json()["sessionId"]
        accepted = client.post(
            f"/api/v12/sessions/{session_id}/messages",
            json={
                "requestId": "auto-dialogue",
                "message": "请解释地下水位趋势的含义",
                "requestedMode": "AUTO",
            },
        )
        assert accepted.status_code == 202
        assert accepted.json()["routingStatus"] == "ROUTING_REQUESTED"

        turn = _await_terminal_turn(client, session_id)

    assert turn["status"] == "COMPLETE"
    assert turn["intentDecision"] == "DIALOGUE"
    assert turn["dialoguePolicy"] == "READ_ONLY"
    assert turn["resolvedMode"] == "DIALOGUE"
    assert turn["routingStatus"] == "START_REQUESTED"
    metrics = turn["routingMetrics"]
    assert metrics == {
        "provider": "deepseek",
        "model": "test-router-model",
        "requestCount": 1,
        "inputTokens": 11,
        "outputTokens": 3,
        "totalTokens": 14,
        "durationMs": metrics["durationMs"],
        "unknownAttempts": 0,
    }
    assert metrics["durationMs"] >= 0
    assert len(router_model.calls) == 1
    assert len(agent_model.calls) == 1
    tool_names = {tool.name for tool in agent_model.calls[0].tools}
    assert "record_plan" not in tool_names
    assert not any(name.startswith("run_") for name in tool_names)
    with connect_database(application.database) as connection:
        request = connection.execute(
            "SELECT mode,dialogue_policy FROM agent_requests WHERE agent_run_id=?",
            (accepted.json()["agentRunId"],),
        ).fetchone()
        assert request["mode"] == "DIALOGUE"
        assert request["dialogue_policy"] == "READ_ONLY"
        counts = {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in (
                "agent_plans",
                "confirmations",
                "workflow_runs",
                "workflow_dispatches",
            )
        }
        context_rows = connection.execute(
            """SELECT purpose,status,actual_input_tokens,manifest_json
               FROM context_views WHERE request_agent_run_id=? ORDER BY created_at""",
            (accepted.json()["agentRunId"],),
        ).fetchall()
    assert counts == {
        "agent_plans": 0,
        "confirmations": 0,
        "workflow_runs": 0,
        "workflow_dispatches": 0,
    }
    assert {row["purpose"] for row in context_rows} == {
        "INTENT_ROUTING", "AGENT_INITIAL",
    }
    assert {row["status"] for row in context_rows} == {"COMPLETE"}
    assert all(row["actual_input_tokens"] is not None for row in context_rows)
    manifests = "".join(row["manifest_json"] for row in context_rows)
    assert "请解释地下水位趋势的含义" not in manifests
    assert "TOOL_SCHEMAS" in manifests and "OUTPUT_SCHEMA" in manifests


@pytest.mark.parametrize("followup", [None, "rule", "status", "invalid", "change", "approved", "replaced", "control"])
def test_auto_dialogue_execution_request_waits_for_confirmation_before_job(
    kernel_settings, monkeypatch, followup,
):
    router_model = _model(_decision("DIALOGUE", policy="EXECUTION_PROPOSAL"))
    agent_model = ScriptedModel([
        ModelStep(output=[function_call("inspect_data_version", {"version_id": "resource-a"}, call_id="inspect")]),
        ModelStep(output=[function_call("record_plan", {
            "goal": "分析监测数据",
            "steps": [{
                "step_id": "summary",
                "tool": "run_summary",
                "purpose": "计算数据概览",
            }],
            "reason": "用户要求实际分析计算",
        }, call_id="plan")]),
        ModelStep(output=[function_call(
            "run_summary", {"resource_id": "resource-a"}, call_id="execute"
        )]),
    ])
    service_requests = []

    def observe_service(request):
        service_requests.append(request)
        if request.url.path.endswith("inspect_data_version"):
            return httpx.Response(200, json={"data": {"status": "OK", "versionId": "resource-a", "datasetId": "dataset-a", "semanticId": "semantics-a", "objects": [{"objectId": "object-a", "role": "raw", "mediaType": "text/csv", "format": "csv", "size": 8, "checksum": "sha256:" + "a" * 64}]}})
        return httpx.Response(500)

    service_client = httpx.AsyncClient(
        transport=httpx.MockTransport(observe_service)
    )
    application = PlatformApplication(
        settings=kernel_settings(),
        model=agent_model,
        intent_router_model=router_model,
        client=service_client,
    )

    try:
        with TestClient(create_app(application=application)) as client:
            session_id = client.post(
                "/api/v12/sessions", json={"projectId": "p"}
            ).json()["sessionId"]
            accepted = client.post(
                f"/api/v12/sessions/{session_id}/messages",
                json={
                    "requestId": "auto-execution",
                    "message": "请实际计算这份监测数据的统计摘要",
                    "requestedMode": "AUTO",
                },
            )
            assert accepted.status_code == 202

            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                turn = client.get(
                    f"/api/v12/sessions/{session_id}/messages"
                ).json()["turns"][-1]
                if turn["confirmations"]:
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("confirmation was not proposed")

            if followup is not None:
                path = f"/api/v12/sessions/{session_id}"
                original = client.get(path).raise_for_status().json()
                assert original["confirmation"]["status"] == "PENDING"
                messages_path = path + "/messages"
                body = {
                    "requestId": "followup",
                    "message": {
                        "rule": "查看状态",
                        "status": "刚才提交的监测统计处理到哪一步了，还有什么等我处理？",
                        "invalid": "之前那个分析现在还缺哪一步？",
                        "change": "换一个目标，请直接解释地下水位的含义。",
                        "approved": "监测摘要现在到了哪个环节？",
                        "replaced": "改为讨论含水层的定义。",
                        "control": "改为讨论含水层的定义。",
                    }[followup],
                    "requestedMode": "AUTO",
                }
                started, release = Event(), Event()
                if followup != "rule":
                    literal = {
                        "status": "STATUS_QUERY|NONE|NONE",
                        "invalid": "an invalid routing response",
                        "change": "CONSULT|NONE|NONE",
                        "approved": "STATUS_QUERY|CONTINUE|NONE",
                        "replaced": "CONSULT|NONE|NONE",
                        "control": "CONSULT|NONE|NONE",
                    }[followup]
                    router_model.enqueue(ModelStep(output=[assistant_message(literal)]))
                    if followup == "invalid":
                        router_model.enqueue(ModelStep(output=[assistant_message(literal)]))
                    if followup in {"change", "replaced"}:
                        agent_model.enqueue(_reply_step())
                    respond = router_model.get_response

                    async def pause_route(*args, **kwargs):
                        started.set()
                        assert await asyncio.to_thread(release.wait, 5)
                        return await respond(*args, **kwargs)

                    monkeypatch.setattr(router_model, "get_response", pause_route)
                try:
                    receipt = client.post(messages_path, json=body).raise_for_status().json()
                    if followup != "rule":
                        assert started.wait(5)
                        during = client.get(path).raise_for_status().json()
                        assert during["goalRevision"] == original["goalRevision"]
                        assert during["confirmation"] == original["confirmation"]
                        pending = client.get(messages_path).raise_for_status().json()
                        assert len(pending["turns"]) == 1
                        assert pending["controlMessages"][-1]["requestId"] == "followup"
                        assert pending["controlMessages"][-1]["status"] in {"ROUTING_REQUESTED", "ROUTING_RUNNING"}
                        assert client.post(messages_path, json=body).raise_for_status().json() == receipt
                        if followup == "approved":
                            approve = {"confirmationId": original["confirmation"]["confirmationId"]}
                            approved = client.post(path + "/confirm", json=approve).raise_for_status().json()
                            (workflow_run_id,) = approved["workflowRunIds"]
                            assert client.post(path + "/confirm", json=approve).raise_for_status().json()[
                                "workflowRunIds"
                            ] == [workflow_run_id]
                        elif followup == "replaced":
                            client.post(messages_path, json={
                                "requestId": "other-goal", "message": "请解释地下水位。", "requestedMode": "DIALOGUE",
                            }).raise_for_status()
                            assert _await_terminal_turn(client, session_id)["requestId"] == "other-goal"
                        elif followup == "control":
                            client.post(messages_path, json={
                                "requestId": "approval-proposal", "message": "批准", "requestedMode": "AUTO",
                            }).raise_for_status()
                finally:
                    release.set()

                if followup == "change":
                    terminal = _await_terminal_turn(client, session_id)
                    assert terminal["status"] == "COMPLETE"
                    changed = client.get(messages_path).raise_for_status().json()
                    assert changed["goalRevision"] == original["goalRevision"] + 1
                    assert changed["turns"][0]["confirmations"][0]["status"] == "SUPERSEDED"
                    assert changed["turns"][-1]["requestId"] == "followup"
                    assert len(agent_model.calls) == 4
                else:
                    for _ in range(120):
                        snapshot = client.get(messages_path).raise_for_status().json()
                        result = next((item for item in snapshot["controlMessages"]
                                       if item["requestId"] == "followup" and item["status"] in
                                       {"CONTROL_COMPLETE", "FAILED"}), None)
                        if result is not None:
                            break
                        time.sleep(0.02)
                    else:
                        raise AssertionError("status request did not finish")
                    assert snapshot["goalRevision"] == original["goalRevision"] + int(followup == "replaced")
                    assert len(snapshot["turns"]) == 1 + int(followup == "replaced")
                    current = client.get(path).raise_for_status().json()
                    if followup == "approved":
                        assert current["confirmation"]["confirmationId"] == original["confirmation"]["confirmationId"]
                        assert current["confirmation"]["status"] == "CONFIRMED"
                        assert current["confirmation"]["actions"] == original["confirmation"]["actions"]
                        assert current["workflowRunIds"] == [workflow_run_id]
                    elif followup == "replaced":
                        assert snapshot["turns"][-1]["requestId"] == "other-goal"
                        assert snapshot["turns"][0]["confirmations"][0]["status"] == "SUPERSEDED"
                    else:
                        assert current["confirmation"] == original["confirmation"]
                    assert len(agent_model.calls) == 3 + int(followup == "replaced")
                    replay = client.post(messages_path, json=body).raise_for_status().json()
                    assert replay == receipt
                    assert client.post(messages_path, json={**body, "message": "不同的请求"}).status_code == 409
                    if followup == "replaced":
                        assert result["status"] == "FAILED"
                        assert result["errorCode"] == "MESSAGE_GOAL_CHANGED"
                    elif followup == "control":
                        assert result["status"] == "FAILED"
                        assert result["errorCode"] == "CONTROL_PROPOSAL_PENDING"
                        assert snapshot["controlProposals"][0]["status"] == "PENDING"
                    elif followup == "invalid":
                        assert result["status"] == "FAILED"
                        assert result["errorCode"] == "AGENT_INTENT_ROUTING_INVALID"
                    else:
                        assert result["semanticIntent"] == "STATUS_QUERY"
                        if followup == "approved":
                            assert result["statusSnapshot"] != "WAITING_CONFIRMATION"
                        else:
                            assert result["statusSnapshot"] == "WAITING_CONFIRMATION"
                        assert receipt["routingStatus"] == ("CONTROL_COMPLETE" if followup == "rule" else "ROUTING_REQUESTED")
    finally:
        asyncio.run(service_client.aclose())

    assert turn["intentDecision"] == "DIALOGUE"
    assert turn["dialoguePolicy"] == "EXECUTION_PROPOSAL"
    assert turn["resolvedMode"] == "DIALOGUE"
    assert turn["confirmations"][0]["status"] == "PENDING"
    assert turn["executionTasks"] == []
    assert service_requests[0].url.path.endswith("inspect_data_version")
    if followup != "approved":
        assert len(service_requests) == 1
    with connect_database(application.database) as connection:
        counts = {
            table: connection.execute(
                f'SELECT COUNT(*) FROM "{table}"'
            ).fetchone()[0]
            for table in ("confirmations", "workflow_runs", "workflow_dispatches")
        }
    assert counts == {
        "confirmations": 1,
        "workflow_runs": int(followup == "approved"),
        "workflow_dispatches": int(followup == "approved"),
    }


@pytest.mark.parametrize(
    "literal,message,reason",
    [
        ("UNKNOWN|NONE|REQUEST_KIND_AMBIGUOUS", "请帮我处理一下", "REQUEST_KIND_AMBIGUOUS"),
        ("EXECUTE_ON_DATA|CONTINUE|NONE", "请运行监测趋势分析，先提交确认。", "TASK_CONFLICT"),
        ("CREATE_WORKFLOW|CONTINUE|NONE", "请继续设计可复用的监测分析流程。", "TASK_CONFLICT"),
    ],
    ids=["ambiguous-request", "missing-task-for-execution", "missing-task-for-workflow"],
)
def test_platform_clarification_asks_one_question_without_confirmation_workflow_or_job(
    kernel_settings, literal, message, reason,
):
    router_model = ScriptedModel([
        ModelStep(output=[assistant_message(literal)]) for _ in range(2)
    ])
    agent_model = _clarification_reply_model()
    service_requests = []

    def observe_service(request):
        service_requests.append(request)
        return httpx.Response(500)

    service_client = httpx.AsyncClient(
        transport=httpx.MockTransport(observe_service)
    )
    application = PlatformApplication(
        settings=kernel_settings(),
        model=agent_model,
        intent_router_model=router_model,
        client=service_client,
    )

    try:
        with TestClient(create_app(application=application)) as client:
            session_id = client.post(
                "/api/v12/sessions", json={"projectId": "p"}
            ).json()["sessionId"]
            accepted = client.post(
                f"/api/v12/sessions/{session_id}/messages",
                json={
                    "requestId": "clarification",
                    "message": message,
                    "requestedMode": "AUTO",
                },
            )
            assert accepted.status_code == 202
            turn = _await_terminal_turn(client, session_id)
    finally:
        asyncio.run(service_client.aclose())

    assert turn["status"] == "COMPLETE"
    assert turn["intentDecision"] == "NEEDS_CLARIFICATION"
    assert turn["resolvedMode"] == "DIALOGUE"
    assert turn["clarificationRequired"] is True
    assert turn["confirmations"] == []
    assert turn["executionTasks"] == []
    assert len(router_model.calls) == 1
    assert [tool.name for tool in agent_model.calls[0].tools] == ["submit_reply"]
    assert service_requests == []
    with connect_database(application.database) as connection:
        counts = {
            table: connection.execute(
                f'SELECT COUNT(*) FROM "{table}"'
            ).fetchone()[0]
            for table in ("confirmations", "workflow_runs", "workflow_dispatches")
        }
        decision = connection.execute(
            """SELECT semantic_intent, task_action, clarification_reason
               FROM message_requests WHERE session_id=?""",
            (session_id,),
        ).fetchone()
        assistant = connection.execute(
            """SELECT content FROM messages
               WHERE session_id=? AND role='assistant'""",
            (session_id,),
        ).fetchone()[0]
    assert counts == {
        "confirmations": 0,
        "workflow_runs": 0,
        "workflow_dispatches": 0,
    }
    assert tuple(decision) == ("UNKNOWN", "NONE", reason)
    assert assistant == "你希望得到解释、分析建议，还是可复用的处理流程？"


def test_platform_router_keeps_latest_turns_when_memory_covers_them(
    kernel_settings,
):
    router_model = _model(_decision("DIALOGUE"))
    application = PlatformApplication(
        settings=kernel_settings(),
        model=_reply_model(),
        intent_router_model=router_model,
    )

    with TestClient(create_app(application=application)) as client:
        session_id = client.post(
            "/api/v12/sessions", json={"projectId": "p"}
        ).json()["sessionId"]
        summary = MemorySummaryV3(
            goals=["persisted-summary-marker"],
            constraints=[],
            decisions=[],
            unresolved_items=[],
        )
        with connect_database(application.database) as connection:
            connection.execute(
                "UPDATE sessions SET goal_revision=3 WHERE session_id=?",
                (session_id,),
            )
            connection.executemany(
                """INSERT INTO messages(session_id,goal_revision,role,content)
                   VALUES (?,?,?,?)""",
                [
                    (session_id, revision, role, f"{label}-{role}")
                    for revision, label in (
                        (1, "oldest-turn"),
                        (2, "recent-turn-two"),
                        (3, "recent-turn-three"),
                    )
                    for role in ("user", "assistant")
                ],
            )
            connection.execute(
                """INSERT INTO agent_memory(
                       session_id,through_goal_revision,summary_json
                   ) VALUES (?,?,?)""",
                (session_id, 3, summary.model_dump_json()),
            )

        assert application.interaction.route_history(session_id) == (
            (2, "user", "recent-turn-two-user"),
            (2, "assistant", "recent-turn-two-assistant"),
            (3, "user", "recent-turn-three-user"),
            (3, "assistant", "recent-turn-three-assistant"),
        )

        accepted = client.post(
            f"/api/v12/sessions/{session_id}/messages",
            json={
                "requestId": "auto-with-memory",
                "message": "current-router-message",
                "requestedMode": "AUTO",
            },
        )
        assert accepted.status_code == 202
        assert _await_run_status(
            application, accepted.json()["agentRunId"]
        ) == "COMPLETE"

    assert len(router_model.calls) == 1
    encoded = json.dumps(router_model.calls[0].input, ensure_ascii=False)
    assert "persisted-summary-marker" in encoded
    assert "oldest-turn" not in encoded
    assert "recent-turn-two" not in encoded
    assert "recent-turn-three" not in encoded
    assert "current-router-message" in encoded


def test_platform_invalid_auto_route_terminalizes_without_agent_fallback(
    kernel_settings,
):
    invalid = ModelStep(output=[assistant_message(json.dumps(
        _decision("DIALOGUE", reason="not allowed")
    ))])
    router_model = ScriptedModel([invalid, invalid])
    agent_model = ScriptedModel([])
    application = PlatformApplication(
        settings=kernel_settings(),
        model=agent_model,
        intent_router_model=router_model,
    )

    with TestClient(create_app(application=application)) as client:
        session_id = client.post(
            "/api/v12/sessions", json={"projectId": "p"}
        ).json()["sessionId"]
        accepted = client.post(
            f"/api/v12/sessions/{session_id}/messages",
            json={
                "requestId": "invalid-auto-route",
                "message": "帮我看看",
                "requestedMode": "AUTO",
            },
        )
        assert accepted.status_code == 202
        assert accepted.json()["routingStatus"] == "ROUTING_REQUESTED"

        turn = _await_terminal_turn(client, session_id)
        session = client.get(f"/api/v12/sessions/{session_id}").json()

    assert turn["status"] == "FAILED"
    assert turn["routingStatus"] == "FAILED"
    assert turn["intentDecision"] is None
    assert turn["resolvedMode"] is None
    assert turn["errorCode"] == "AGENT_INTENT_ROUTING_INVALID"
    assert turn["diagnostics"] == {"reason": "AGENT_INTENT_ROUTING_INVALID"}
    assert turn["agentMetrics"] is None
    assert session["agentSteps"] == ["FAILED"]
    assert len(router_model.calls) == 2
    assert agent_model.calls == ()
    with connect_database(application.database) as connection:
        run_id = accepted.json()["agentRunId"]
        assert connection.execute(
            "SELECT COUNT(*) FROM agent_requests WHERE agent_run_id=?", (run_id,)
        ).fetchone()[0] == 0
        failed = connection.execute(
            "SELECT status,error FROM agent_runs WHERE agent_run_id=?", (run_id,)
        ).fetchone()
        assert (failed["status"], failed["error"]) == (
            "FAILED",
            "AGENT_INTENT_ROUTING_INVALID",
        )
