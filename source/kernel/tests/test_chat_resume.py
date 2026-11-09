"""Actual provider adapters retain current-turn reasoning across serialized approval."""
from __future__ import annotations


import asyncio
from dataclasses import replace
import json

import httpx2
from openai import AsyncOpenAI
import pytest

from platform_kernel.agent_runtime import model_tool, provider as provider_module, sdk_execution
from platform_kernel.agent_runtime.availability import HttpReadinessProbe, ProbeResult
from platform_kernel.agent_runtime.memory import ExecutionContextV1, MemoryRecord
from platform_kernel.agent_runtime.models import MemorySummaryV3
from platform_kernel.agent_runtime.persistence import create_run, ensure_dialogue_request
from platform_kernel.agent_runtime.persistence.types import ReplayedAction
from platform_kernel.composition import PlatformApplication
from platform_kernel.database import connect_database, utc_now


@pytest.mark.parametrize("approve", [False, True])
@pytest.mark.parametrize("with_memory", [False, True])
@pytest.mark.parametrize("provider_name,protocol", [
    ("minimax", "chat_completions"), ("deepseek", "responses"),
    ("deepseek", "chat_completions"),
])
def test_reasoning_survives_serialized_approval_and_submit_reply(
    kernel_settings, monkeypatch, approve, with_memory, provider_name, protocol,
):
    requests = []
    arguments = {"resource_id": "input", "axis": "columns"}
    reason = "Synthetic private reasoning for this current turn."
    def provider(request):
        if request.method == "GET" and request.url.path == "/v1/models":
            return httpx2.Response(200, json={"data": []})
        body = json.loads(request.content)
        requests.append(body)
        count = len(requests)
        if count > 1:
            messages = body["input" if protocol == "responses" else "messages"]
            assert "上一轮的完整中文回复" in json.dumps(messages, ensure_ascii=False)
            if protocol == "responses":
                reasoning = next(m for m in messages if m.get("type") == "reasoning")
                assert reasoning["content"] == [{"type": "reasoning_text", "text": reason}]
                outputs = [m for m in messages if m.get("type") == "function_call_output"]
                tool_result = outputs[0]["output"]
            else:
                assistant = next(m for m in messages if m.get("tool_calls"))
                assert assistant["reasoning_content"] == reason
                outputs = [m for m in messages if m["role"] == "tool"]
                tool_result = outputs[0]["content"]
            if with_memory:
                assert any("MemorySummaryV3" in str(m.get("content")) for m in messages)
            assert len(outputs) == count - 1
            assert json.loads(tool_result)["workflow_run_id"] == "workflow" if approve else tool_result == "用户拒绝"
        if count == 1:
            name, supplied, call_id = "run_summary", arguments, "call-summary"
        else:
            name, supplied, call_id = "submit_reply", {
                "conclusion": {"text": "已完成" if approve else "已拒绝", "citations": []},
                "details": [],
                "unresolved_items": [], "referenced_workflow_runs": [],
                "include_execution_status": False,
                "interaction_options": [], "workflow_draft": None,
            }, "reply"
        message = {"role": "assistant", "content": None, "reasoning_content": reason,
                   "tool_calls": [{"id": call_id, "type": "function", "function": {
                       "name": name, "arguments": json.dumps(supplied),
                   }}]}
        output = [
            {"id": f"reasoning-{count}", "type": "reasoning", "summary": [],
             "content": [{"type": "reasoning_text", "text": reason}]},
            {"id": f"function-{count}", "type": "function_call", "call_id": call_id,
             "name": name, "arguments": json.dumps(supplied)},
        ]
        if protocol == "responses":
            assert body["reasoning"] == {"effort": "high"}
            assert not body.get("text")
            return httpx2.Response(200, json={
                "id": f"response-{count}", "object": "response", "created_at": 1,
                "status": "completed", "model": "test-model", "output": output,
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            })
        if provider_name == "deepseek":
            assert body["reasoning_effort"] == "high"
            assert body["thinking"] == {"type": "enabled"}
        else:
            assert body["reasoning_split"] is True
        return httpx2.Response(200, json={
            "id": f"chat-{count}", "object": "chat.completion", "created": 1,
            "model": "test-model", "choices": [{"index": 0, "message": message,
                "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    async def exercise():
        async def ready(probe, *, force=False):
            return ProbeResult(True, None, probe.endpoint)

        monkeypatch.setattr(HttpReadinessProbe, "check", ready)
        settings = replace(kernel_settings(), agent_provider=provider_name, agent_protocol=protocol)
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(provider)) as client:
            monkeypatch.setattr(provider_module, "AsyncOpenAI", lambda **kwargs: AsyncOpenAI(
                api_key="test", base_url="http://agent.test/v1", max_retries=0, http_client=client))
            app = PlatformApplication(settings=settings)
            with connect_database(app.database) as connection:
                connection.execute("INSERT INTO sessions (session_id, project_id, goal_revision, agent_run_id, pending_agent_run_id, agent_start_state, owner_subject) VALUES ('session','project',3,NULL,NULL,NULL,'local-researcher')")
            def on_admitted():
                create_run(app.database, agent_run_id="run", session_id="session", project_id="project",
                           goal_revision=3, provider=provider_name, model="test", trace_id="trace_"+"a"*32,
                           started_at=utc_now())
            monkeypatch.setattr(model_tool, "load_replayed_action", lambda *a: ReplayedAction(
                arguments=arguments, executed_arguments=arguments, status="COMPLETE",
                workflow_run_id="workflow", outputs=[], error=None))
            memory = MemoryRecord(1, MemorySummaryV3(goals=["合成任务"], constraints=[], decisions=[],
                                                     unresolved_items=[])) if with_memory else None
            common = dict(session_id="session", project_id="project", agent_run_id="run",
                          goal_revision=3, trace_id="trace_"+"a"*32, execution_context=ExecutionContextV1(()))
            ensure_dialogue_request(app.database, "run", "EXECUTION_PROPOSAL")
            initial = await sdk_execution.execute_initial(
                app.agent_runtime.sdk_execution, **common,
                history=((2, "user", "先前问题"), (2, "assistant", "上一轮的完整中文回复")),
                message="现在请计算", memory=memory, on_admitted=on_admitted,
            )
            state = json.dumps(initial.result.to_state().to_json(strict_context=True))
            result = await sdk_execution.execute_resumed(
                app.agent_runtime.sdk_execution, **common, state_json=state, approve=approve,
                reason=None if approve else "用户拒绝",
                action_statuses={"call-summary": "COMPLETE"} if approve else {},
            )
            final_output = json.loads(result.result.final_output)
            assert final_output["conclusion"] is None
            assert final_output["include_execution_status"] is True
            assert final_output["unresolved_items"] == []
            assert final_output["interaction_options"] == []
            assert len(requests) == 2
            await app.close()
    asyncio.run(exercise())
