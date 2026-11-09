"""Exercise the real SDK wire format and strict output boundary without an API key."""

import asyncio
import json

from agents import Agent, RunConfig, Runner
import httpx
from openai import AsyncOpenAI
import pytest

from platform_kernel.agent_runtime import provider, sdk_execution
from platform_kernel.agent_runtime.memory import ExecutionContextV1
from platform_kernel.agent_runtime.models import AgentFinalV5, MemorySummaryV3
from platform_kernel.agent_runtime.persistence import create_run
from platform_kernel.composition import PlatformApplication
from platform_kernel.database import utc_now


FINAL = {
    "message": "完整中文回复",
    "presentation": {
        "conclusion": {"markdown": "完整中文回复", "source_indexes": []},
        "details": [],
        "execution_summary": None,
        "unresolved_items": [],
        "sources": [],
    },
    "workflow_runs": [],
    "unresolved_items": [],
    "interaction_options": [],
}
SUMMARY = {
    "goals": ["合成项目"],
    "constraints": [],
    "decisions": [],
    "unresolved_items": [],
}


def response(text):
    return httpx.Response(
        200,
        json={
            "id": "response-1",
            "object": "response",
            "created_at": 1,
            "status": "completed",
            "model": "deepseek-flash",
            "output": [
                {
                    "id": "message-1",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": text, "annotations": []}
                    ],
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        },
    )


@pytest.mark.parametrize(
    "schema,output", [(AgentFinalV5, FINAL), (MemorySummaryV3, SUMMARY)]
)
def test_deepseek_schema_wire_keeps_high_thinking_and_strict_types(
    kernel_settings, monkeypatch, schema, output
):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return response(json.dumps(output, ensure_ascii=False))

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            monkeypatch.setattr(
                provider,
                "AsyncOpenAI",
                lambda **kwargs: AsyncOpenAI(
                    http_client=http,
                    **{
                        key: value
                        for key, value in kwargs.items()
                        if key != "http_client"
                    },
                ),
            )
            settings = kernel_settings()
            configured = provider.build_agent_provider(settings)
            agent = Agent(
                name="DeepSeek output boundary",
                instructions="Return JSON.",
                model=configured.model,
                model_settings=provider.build_model_settings(settings),
                output_type=schema,
            )
            result = await Runner.run(
                agent, "合成测试", run_config=RunConfig(tracing_disabled=True)
            )
            assert (
                result.final_output_as(
                    schema, raise_if_incorrect_type=True
                ).model_dump()
                == output
            )
            assert len(requests) == 1
            assert requests[0]["text"]["format"]["type"] == "json_schema"
            assert requests[0]["text"]["format"]["strict"] is True
            assert requests[0]["reasoning"] == {"effort": "high"}
            assert requests[0]["max_output_tokens"] == settings.agent_max_output_tokens
            await configured.close()

    asyncio.run(exercise())


def test_empty_final_stops_after_one_request_and_keeps_usage(
    kernel_settings, monkeypatch
):
    requests = []

    def handle(request):
        requests.append(request)
        return response("")

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            monkeypatch.setattr(
                provider,
                "AsyncOpenAI",
                lambda **kwargs: AsyncOpenAI(
                    http_client=http,
                    **{
                        key: value
                        for key, value in kwargs.items()
                        if key != "http_client"
                    },
                ),
            )
            settings = kernel_settings()
            configured = provider.build_agent_provider(settings)
            application = PlatformApplication(settings=settings, model=configured.model)
            session_id = application.interaction.create_session().session.session_id

            def on_admitted():
                create_run(
                    application.database,
                    agent_run_id="run",
                    session_id=session_id,
                    project_id="project",
                    goal_revision=1,
                    provider=settings.agent_provider,
                    model=settings.agent_model,
                    trace_id="trace_" + "a" * 32,
                    started_at=utc_now(),
                )

            try:
                with pytest.raises(sdk_execution.SDKExecutionError) as failure:
                    await sdk_execution.execute_initial(
                        application.agent_runtime.sdk_execution,
                        session_id=session_id,
                        project_id="project",
                        agent_run_id="run",
                        goal_revision=1,
                        trace_id="trace_" + "a" * 32,
                        history=(),
                        message="合成测试",
                        memory=None,
                        execution_context=ExecutionContextV1(()),
                        on_admitted=on_admitted,
                    )
                assert failure.value.code == "AGENT_MODEL_OUTPUT_INVALID"
                assert failure.value.usage.total_tokens == 15
                assert len(requests) == 1
            finally:
                await application.close()
                await configured.close()

    asyncio.run(exercise())
