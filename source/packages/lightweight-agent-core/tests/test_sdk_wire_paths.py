from __future__ import annotations

from dataclasses import replace
import json

import pytest
from platform_lightweight_agent import (
    AgentDriverFailure,
    AgentToolSpec,
    OpenAIAgentsDriver,
    PydanticAIDriver,
    ToolReference,
)
from platform_lightweight_agent.engine import AgentContext

DIALECT = "https://json-schema.org/draft/2020-12/schema"
TOOL = ToolReference("wire.lookup", "1.0.0", "sha256:" + "c" * 64)
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


def _context() -> AgentContext:
    spec = AgentToolSpec(
        "lookup", TOOL, "Look up a record.", INPUT_SCHEMA, OUTPUT_SCHEMA
    )
    return AgentContext(
        run_id="wire-run",
        workflow_id="wire-workflow",
        workflow_version="1",
        step_id="agent",
        turn=1,
        inputs={"question": "lookup"},
        step_input={"question": "lookup"},
        values={},
        prior_tool_results=(
            {
                "tool_results": [
                    {
                        "name": "lookup",
                        "arguments": {"query": "previous"},
                        "succeeded": True,
                        "output": {"answer": "known-from-history"},
                        "error": None,
                    }
                ]
            },
        ),
        allowed_tools=(TOOL,),
        workflow_schema_version="v6-light-workflow-v3",
        tool_specs=(spec,),
    )


def _chat_completion() -> dict:
    return {
        "id": "chatcmpl-wire",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-wire",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-wire",
                            "type": "function",
                            "function": {
                                "name": "lookup",
                                "arguments": '{"query":"aquifer"}',
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "total_tokens": 12,
        },
    }


def _responses_result() -> dict:
    return {
        "id": "resp-wire",
        "created_at": 1.0,
        "model": "gpt-wire",
        "object": "response",
        "output": [
            {
                "id": "fc-wire",
                "call_id": "call-wire",
                "type": "function_call",
                "name": "lookup",
                "arguments": '{"query":"aquifer"}',
                "status": "completed",
            }
        ],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
    }


def _chat_final_completion() -> dict:
    result = _chat_completion()
    result["id"] = "chatcmpl-wire-final"
    result["choices"][0]["message"]["tool_calls"] = [
        {
            "id": "call-final",
            "type": "function",
            "function": {
                "name": "final_reply",
                "arguments": '{"message":"native continuation complete"}',
            },
        }
    ]
    return result


@pytest.mark.anyio
async def test_pydantic_ai_uses_real_openai_serialization_with_mock_transport() -> None:
    httpx = pytest.importorskip("httpx")
    openai = pytest.importorskip("openai")
    pytest.importorskip("pydantic_ai")
    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    requests: list[dict] = []
    request_timeouts: list[dict] = []

    def handle(request):  # type: ignore[no-untyped-def]
        requests.append(json.loads(request.content))
        request_timeouts.append(request.extensions["timeout"])
        return httpx.Response(200, json=_chat_completion())

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    client = openai.AsyncOpenAI(api_key="test-key", http_client=http_client)
    model = OpenAIChatModel("gpt-wire", provider=OpenAIProvider(openai_client=client))
    try:
        decision = await PydanticAIDriver(
            agent=Agent(model, model_settings={"timeout": 7.25})
        ).decide_async(replace(_context(), prior_tool_results=()))
    finally:
        await client.close()

    assert decision.tool_calls[0].arguments == {"query": "aquifer"}
    assert len(requests) == 1
    assert set(request_timeouts[0].values()) == {7.25}
    tool_names = [item["function"]["name"] for item in requests[0]["tools"]]
    assert "lookup" in tool_names
    lookup = next(
        item for item in requests[0]["tools"] if item["function"]["name"] == "lookup"
    )
    assert lookup["function"]["parameters"] == {
        key: value for key, value in INPUT_SCHEMA.items() if key != "$schema"
    }
    final_reply = next(
        item
        for item in requests[0]["tools"]
        if item["function"]["name"] == "final_reply"
    )
    assert final_reply["function"]["strict"] is True
    assert set(final_reply["function"]["parameters"]["properties"]) == {"message"}
    assert "known-from-history" not in json.dumps(requests[0])
    assert "tool_id" not in json.dumps(requests[0])
    assert "package_digest" not in json.dumps(requests[0])
    assert '"version"' not in json.dumps(requests[0])
    assert "Return exactly one JSON" not in json.dumps(requests[0])


@pytest.mark.anyio
async def test_pydantic_ai_serializes_native_tool_result_after_adapter_restart() -> (
    None
):
    httpx = pytest.importorskip("httpx")
    openai = pytest.importorskip("openai")
    pytest.importorskip("pydantic_ai")
    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    requests: list[dict] = []

    def handle(request):  # type: ignore[no-untyped-def]
        requests.append(json.loads(request.content))
        response = _chat_completion() if len(requests) == 1 else _chat_final_completion()
        return httpx.Response(200, json=response)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    client = openai.AsyncOpenAI(api_key="test-key", http_client=http_client)
    model = OpenAIChatModel("gpt-wire", provider=OpenAIProvider(openai_client=client))
    first_driver = PydanticAIDriver(agent=Agent(model))
    base = replace(_context(), prior_tool_results=(), tool_calls_remaining=1)
    try:
        proposed = await first_driver.decide_async(base)
        resumed = await PydanticAIDriver(agent=Agent(model)).decide_async(
            replace(
                base,
                turn=2,
                prior_tool_results=(
                    {
                        "tool_results": [
                            {
                                "name": "lookup",
                                "arguments": {"query": "aquifer"},
                                "succeeded": True,
                                "output": {"answer": "native result"},
                                "error": None,
                            }
                        ]
                    },
                ),
                driver_continuation=proposed.continuation,
                deferred_tool_results=(
                    {
                        "action_id": "action-wire",
                        "tool_call_id": "call-wire",
                        "name": "lookup",
                        "arguments": {"query": "aquifer"},
                        "succeeded": True,
                        "output": {"answer": "native result"},
                        "error": None,
                    },
                ),
                tool_calls_remaining=0,
            )
        )
    finally:
        await client.close()

    assert resumed.message == "native continuation complete"
    assert len(requests) == 2
    messages = requests[1]["messages"]
    assert [item["role"] for item in messages[-2:]] == ["assistant", "tool"]
    assert messages[-2]["tool_calls"][0]["id"] == "call-wire"
    assert messages[-1]["tool_call_id"] == "call-wire"
    assert "native result" in messages[-1]["content"]
    second_tool_names = [
        item["function"]["name"] for item in requests[1]["tools"]
    ]
    assert second_tool_names == ["final_reply"]


@pytest.mark.anyio
async def test_agents_sdk_uses_real_openai_serialization_with_mock_transport() -> None:
    httpx = pytest.importorskip("httpx")
    openai = pytest.importorskip("openai")
    agents = pytest.importorskip("agents")
    from agents import set_trace_provider
    from agents.models.openai_responses import OpenAIResponsesModel
    from agents.tracing.provider import DefaultTraceProvider

    set_trace_provider(DefaultTraceProvider())

    requests: list[dict] = []

    def handle(request):  # type: ignore[no-untyped-def]
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=_responses_result())

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    client = openai.AsyncOpenAI(api_key="test-key", http_client=http_client)
    model = OpenAIResponsesModel("gpt-wire", client)
    try:
        driver = OpenAIAgentsDriver(
            agent=agents.Agent(
                name="wire",
                model=model,
                model_settings=agents.ModelSettings(timeout=7.25),
            )
        )
        decision = await driver.decide_async(_context())
    finally:
        await client.close()

    assert decision.tool_calls[0].arguments == {"query": "aquifer"}
    assert len(requests) == 1
    lookup = next(item for item in requests[0]["tools"] if item["name"] == "lookup")
    assert lookup["parameters"] == INPUT_SCHEMA
    assert lookup["output_schema"] == OUTPUT_SCHEMA
    assert requests[0]["text"]["format"]["strict"] is True
    assert set(requests[0]["text"]["format"]["schema"]["properties"]) == {"message"}
    assert "known-from-history" in json.dumps(requests[0])
    assert "tool_id" not in json.dumps(requests[0])
    assert "package_digest" not in json.dumps(requests[0])
    assert '"version"' not in json.dumps(requests[0])
    assert "Return exactly one JSON" not in json.dumps(requests[0])


@pytest.mark.anyio
@pytest.mark.parametrize("adapter", ["pydantic-ai", "openai-agents"])
async def test_real_provider_transport_timeout_is_a_stable_failure(
    adapter: str,
) -> None:
    import asyncio

    httpx = pytest.importorskip("httpx")
    openai = pytest.importorskip("openai")

    requests = 0

    async def handle(request):  # type: ignore[no-untyped-def]
        nonlocal requests
        requests += 1
        if adapter == "pydantic-ai":
            raise httpx.ReadTimeout("provider timed out", request=request)
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=_responses_result())

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    client = openai.AsyncOpenAI(
        api_key="test-key", http_client=http_client, max_retries=0
    )
    try:
        if adapter == "pydantic-ai":
            pytest.importorskip("pydantic_ai")
            from pydantic_ai import Agent
            from pydantic_ai.models.openai import OpenAIChatModel
            from pydantic_ai.providers.openai import OpenAIProvider

            model = OpenAIChatModel(
                "gpt-wire", provider=OpenAIProvider(openai_client=client)
            )
            driver = PydanticAIDriver(
                agent=Agent(model, model_settings={"timeout": 0.01})
            )
        else:
            agents = pytest.importorskip("agents")
            from agents import set_trace_provider
            from agents.models.openai_responses import OpenAIResponsesModel
            from agents.tracing.provider import DefaultTraceProvider

            set_trace_provider(DefaultTraceProvider())
            driver = OpenAIAgentsDriver(
                agent=agents.Agent(
                    name="wire-timeout",
                    model=OpenAIResponsesModel("gpt-wire", client),
                    model_settings=agents.ModelSettings(timeout=0.01),
                )
            )
        with pytest.raises(AgentDriverFailure) as failure:
            context = (
                replace(_context(), prior_tool_results=())
                if adapter == "pydantic-ai"
                else _context()
            )
            await driver.decide_async(context)
    finally:
        await client.close()

    assert failure.value.code == "AGENT_PROVIDER_FAILURE"
    assert requests == 1
