from __future__ import annotations

import asyncio
from dataclasses import replace
import json

from agents import Agent, AgentOutputSchema
from agents.testing import ScriptedModel, ModelStep, assistant_message, function_call
import httpx
import pytest

from kernel.tests.test_sdk_execution_governance import _application, _final, _start
from platform_kernel.agent_runtime.sdk_execution import execution as sdk_execution
from platform_kernel.agent_runtime.context_usage import context_tokens, context_usage
from platform_kernel.agent_runtime.memory import ExecutionContextV1, build_input_items, completed_turns, token_count
from platform_kernel.agent_runtime.models import MemorySummaryV3
from platform_kernel.agent_runtime.persistence import session_run_summaries
from platform_kernel.api import create_app
from platform_kernel.composition import PlatformApplication
from platform_kernel.database import connect_database, read_session_events
from platform_kernel.errors import AgentRunError


def test_budget_breakdown_preserves_original_total_and_separates_evidence():
    agent = Agent(name="test", output_type=MemorySummaryV3)
    instructions = 'PRIVATE_INSTRUCTIONS\nExecutionContextV1:\n{"actions":[{"id":"PRIVATE_REF"}]}\nnext action'
    inputs = [
        {"role": "user", "content": "PRIVATE_DIALOGUE 中文\n"},
        {"type": "function_call", "call_id": "read", "name": "retrieve_evidence", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "read", "output": "PRIVATE_OBSERVATION " * 300},
        {"type": "function_call", "call_id": "run", "name": "run_model", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "run", "output": "PRIVATE_RESULT_REFERENCE"},
    ]
    original_total = token_count({
        "instructions": instructions, "input": inputs, "tools": [],
        "output_schema": AgentOutputSchema(MemorySummaryV3).json_schema(),
    })
    report = context_usage(agent, instructions, inputs, available=100,
                           observation_names=frozenset({"retrieve_evidence"}))
    assert report["total_tokens"] == context_tokens(agent, instructions, inputs) == original_total
    assert sum(report["breakdown"].values()) == original_total
    assert report["excess_tokens"] == original_total - 100
    assert all(report["breakdown"][key] > 0 for key in
               ("tools", "instructions", "dialogue", "execution_references", "observations"))
    assert report["breakdown"]["observations"] > report["breakdown"]["dialogue"]
    assert "PRIVATE" not in json.dumps(report)


def test_observation_overflow_diagnostic_survives_terminalization(kernel_settings):
    model = ScriptedModel([ModelStep(output=[function_call("retrieve_evidence", {"query": "x"}, call_id="read")])])
    app = _application(kernel_settings, model, observation="PRIVATE_OBSERVATION " * 5000)
    with pytest.raises(AgentRunError) as failure:
        asyncio.run(_start(app))
    report = failure.value.diagnostics
    assert report["kind"] == "CONTEXT_LIMIT" and report["excess_tokens"] > 0
    assert report["breakdown"]["observations"] > report["available_input_tokens"]
    assert len(model.calls) == 1
    with connect_database(app.database) as connection:
        failures = read_session_events(connection, "session", "agent.failed")
        turn = session_run_summaries(connection, "session", "project", (10,))[10]
    assert failures[0]["data"]["diagnostics"] == report
    assert all(activity["status"] not in {"RUNNING", "QUEUED"} for activity in turn["activities"])
    segment = next(activity for activity in turn["activities"] if activity["kind"] == "SEGMENT")
    assert segment["context_usage"] == {key: value for key, value in report.items() if key != "kind"}
    assert "PRIVATE_OBSERVATION" not in json.dumps(turn)


def test_normal_context_diagnostics_are_readable_after_reopening_http_history(kernel_settings):
    async def exercise():
        settings = kernel_settings("knowledge")
        app = PlatformApplication(settings=settings, model=ScriptedModel([ModelStep(output=[_final()])]))
        session = app.interaction.create_session().session
        await app.interaction.turn(session.session_id, "PRIVATE_USER_MESSAGE")
        await app.close()
        reopened = PlatformApplication(settings=settings, model=ScriptedModel([]))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(application=reopened)),
                                     base_url="http://kernel") as client:
            response = await client.get(f"/api/v12/sessions/{session.session_id}/messages")
            assert response.status_code == 200
            activity = next(item for item in response.json()["turns"][0]["activities"] if item["kind"] == "MODEL_REQUEST")
            usage = activity["contextUsage"]
            assert usage["excessTokens"] == 0
            assert sum(usage["breakdown"].values()) == usage["totalTokens"]
            assert "PRIVATE_USER_MESSAGE" not in json.dumps(activity)
        await reopened.close()
    asyncio.run(exercise())


def test_post_compaction_overflow_keeps_summary_and_reports_remaining_input(kernel_settings):
    summary = {"goals": ["compact conversation facts " * 100], "constraints": [], "decisions": [], "unresolved_items": []}
    model = ScriptedModel([ModelStep(output=[assistant_message(json.dumps(summary))])])
    app = _application(kernel_settings, model)
    history = tuple((revision, role, "history " * 20) for revision in range(1, 4) for role in ("user", "assistant"))
    dependencies = app.agent_runtime.sdk_execution
    agent = sdk_execution._build_agent(dependencies, ExecutionContextV1(()))
    required = build_input_items(None, completed_turns(history, after_revision=0)[-2:], "query")
    available = context_tokens(agent, agent.instructions, required) + 180
    app.agent_runtime.sdk_execution = replace(dependencies, settings=replace(
        dependencies.settings, agent_context_window_tokens=available + 500,
    ))
    with pytest.raises(AgentRunError, match="AGENT_CONTEXT_TOO_LARGE") as failure:
        asyncio.run(_start(app, history=history))
    report = failure.value.diagnostics
    assert report["available_input_tokens"] == available and report["excess_tokens"] > 0
    assert report["breakdown"]["dialogue"] >= token_count(required)
    assert len(model.calls) == 1
    with connect_database(app.database) as connection:
        saved = connection.execute("SELECT through_goal_revision,summary_json FROM agent_memory WHERE session_id='session'").fetchone()
    assert saved["through_goal_revision"] == 1
    assert json.loads(saved["summary_json"]) == summary
