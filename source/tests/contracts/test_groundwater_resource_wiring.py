from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from uuid import uuid4

from agents import UserError, Usage
from agents.models.chatcmpl_converter import Converter
from agents.testing import ModelStep, ScriptedModel, function_call
import httpx
import pytest
from fastapi.testclient import TestClient
from platform_kernel.agent_runtime.sdk_execution import execution as sdk_execution
from platform_kernel.agent_runtime.observation_client import ObservationClient
from platform_kernel.agent_runtime.governance import new_governance
from platform_kernel.agent_runtime.mode import resolve_mode_capabilities
from platform_kernel.agent_runtime.tooling import build_agent_tools
from platform_kernel.api.app import create_app
from platform_kernel.composition import PlatformApplication
from platform_kernel.database import connect_database
from platform_kernel.errors import UpstreamServiceError


DATA_TOOLS = {
    "discover_datasets", "get_data_inventory", "discover_datasets",
    "inspect_data_version", "inspect_data_table",
}


def _submit_dialogue(client: TestClient, session_id: str, message: str) -> dict:
    response = client.post(
        f"/api/v12/sessions/{session_id}/messages",
        json={
            "requestId": str(uuid4()),
            "message": message,
            "requestedMode": "DIALOGUE",
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def _wait_failed(client: TestClient, session_id: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        session = client.get(f"/api/v12/sessions/{session_id}").json()
        if session["agentSteps"] and session["agentSteps"][-1] == "FAILED":
            return session
        time.sleep(0.01)
    raise AssertionError("AGENT_FAILURE_TIMEOUT")


def test_compiled_groundwater_tools_support_chat_completions(
    tmp_path, kernel_settings, monkeypatch
):
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1")
    settings = kernel_settings("groundwater")
    client = httpx.AsyncClient(trust_env=False)
    try:
        tools = build_agent_tools(
            tmp_path / "unused",
            settings.services,
            settings.observation_tools,
            settings.models,
            ObservationClient(client),
        )
        selected = {tool.name: tool for tool in tools if tool.name in DATA_TOOLS}
        assert set(selected) == DATA_TOOLS
        for tool in tools:
            assert tool.output_json_schema is None
            assert Converter.tool_to_openai(tool)["type"] == "function"
        for definition in settings.observation_tools:
            if definition.name in DATA_TOOLS:
                assert definition.timeout_seconds == 30
                assert definition.path == f"/internal/tools/v4/{definition.name}"
    finally:
        asyncio.run(client.aclose())


def test_groundwater_still_validates_service_output(tmp_path, kernel_settings):
    settings = kernel_settings("groundwater")

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"data": [{"resource_id": "incomplete"}]}
                )
            )
        ) as client:
            tool = next(
                t
                for t in build_agent_tools(
                    tmp_path / "unused",
                    settings.services,
                    settings.observation_tools,
                    settings.models,
                    ObservationClient(client),
                )
                if t.name == "discover_datasets"
            )
            with pytest.raises(
                UpstreamServiceError, match="OBSERVATION_RESPONSE_INVALID"
            ):
                await tool.on_invoke_tool(
                    SimpleNamespace(
                        context={
                            "project_id": settings.project_id,
                            "trace_id": "trace",
                            "governance": new_governance(
                                resolve_mode_capabilities(None, settings.models)
                            ),
                        },
                        tool_call_id="call",
                    ),
                    json.dumps({
                        "progress_intent": {
                            "capability": "data.discovery",
                            "objective": "查找 2025 年 RGB 数据版本",
                            "template_key": "data.discovery",
                        },
                        "query": "2025 RGB",
                    }),
                )

    asyncio.run(check())


def test_sdk_configuration_error_terminalizes_run_session_and_trace(
    kernel_settings, monkeypatch
):
    settings = kernel_settings("groundwater")
    application = PlatformApplication(settings=settings, model=ScriptedModel([]))

    async def reproduce_incompatible_tool(agent, *args, **kwargs):
        tool = next(t for t in agent.tools if t.name == "discover_datasets")
        tool.output_json_schema = {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
        # Use the real SDK converter that failed in the deployed Chat Completions path.
        Converter.tool_to_openai(tool)
        pytest.fail("SDK must reject a Responses-only tool feature")

    monkeypatch.setattr(sdk_execution.Runner, "run", reproduce_incompatible_tool)
    with TestClient(create_app(application=application)) as client:
        session = client.post("/api/v12/sessions", json={"projectId": settings.project_id}).json()
        sid = session["sessionId"]
        receipt = _submit_dialogue(client, sid, "查找遥感资料")
        current = _wait_failed(client, sid)
        rid = receipt["agentRunId"]
        run = client.get(f"/api/v12/agent-runs/{rid}", params={"projectId": settings.project_id}).json()
        assert run["status"] == "FAILED" and run["timing"]["finishedAt"] is not None
        assert run["errorCode"] == "AGENT_SDK_CONFIGURATION_INVALID"
        assert run["tokenUsage"]["totalTokens"] == 0 and run["actions"] == []
        assert current["agentRunId"] == rid
        assert current["agentSteps"][-1] == "FAILED"
        assert current["confirmation"] is None and current["workflowRunIds"] == []
        trace = client.get(
            f"/api/v12/agent-runs/{rid}/trace", params={"projectId": settings.project_id}
        ).json()
        assert "AGENT_SDK_CONFIGURATION_INVALID" in json.dumps(trace)
        assert "output_json_schema" not in json.dumps(run)
        with connect_database(application.database) as connection:
            assert (
                connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0]
                == 0
            )
            events = connection.execute(
                "SELECT event_type FROM session_events WHERE session_id=?", (sid,)
            ).fetchall()
            assert sum(row[0] == "agent.failed" for row in events) == 1


def test_sdk_user_error_is_not_replaced_by_success(kernel_settings, monkeypatch):
    application = PlatformApplication(
        settings=kernel_settings("groundwater"), model=ScriptedModel([])
    )

    async def fail(*args, **kwargs):
        raise UserError("private configuration detail")

    monkeypatch.setattr(sdk_execution.Runner, "run", fail)
    with TestClient(create_app(application=application)) as client:
        session = client.post("/api/v12/sessions", json={"projectId": application.settings.project_id}).json()
        receipt = _submit_dialogue(client, session["sessionId"], "search")
        _wait_failed(client, session["sessionId"])
        run = client.get(
            f"/api/v12/agent-runs/{receipt['agentRunId']}",
            params={"projectId": application.settings.project_id},
        )
        assert run.status_code == 200
        assert "private configuration detail" not in run.text
        assert run.json()["errorCode"] == "AGENT_SDK_CONFIGURATION_INVALID"


def test_sdk_configuration_failure_retains_completed_model_usage(kernel_settings):
    model = ScriptedModel(
        [
            ModelStep(
                output=[
                    function_call(
                        "discover_datasets", {"query": "2025 RGB"}, call_id="search"
                    )
                ],
                usage=Usage(input_tokens=11, output_tokens=3, total_tokens=14),
            ),
            ModelStep(error=UserError("private SDK detail")),
        ]
    )
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": []})
        )
    )
    application = PlatformApplication(
        settings=kernel_settings("groundwater"), model=model, client=http
    )
    try:
        with TestClient(create_app(application=application)) as client:
            sid = client.post("/api/v12/sessions", json={"projectId": application.settings.project_id}).json()[
                "sessionId"
            ]
            receipt = _submit_dialogue(client, sid, "search")
            _wait_failed(client, sid)
            rid = receipt["agentRunId"]
            run = client.get(
                f"/api/v12/agent-runs/{rid}", params={"projectId": application.settings.project_id}
            ).json()
            assert run["status"] == "FAILED"
            assert run["tokenUsage"] == {
                "inputTokens": 11,
                "outputTokens": 3,
                "totalTokens": 14,
            }
            assert run["workflowRunIds"] == []
    finally:
        asyncio.run(http.aclose())
