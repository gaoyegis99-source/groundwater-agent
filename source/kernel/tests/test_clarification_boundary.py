from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from agents.testing import ScriptedModel
import httpx
import pytest

from platform_kernel.agent_runtime.memory import ExecutionContextV1
from platform_kernel.agent_runtime.mode import resolve_mode_capabilities
from platform_kernel.agent_runtime.observation_client import ObservationClient
from platform_kernel.agent_runtime.persistence import create_run
from platform_kernel.agent_runtime.provider import build_model_settings
from platform_kernel.agent_runtime.reply import render_submitted_reply
from platform_kernel.agent_runtime.sdk_execution.execution import _build_agent
from platform_kernel.agent_runtime.sdk_execution.types import SDKExecutionDependencies
from platform_kernel.agent_runtime.sdk_execution.usage_ports import CallbackActivityLog
from platform_kernel.agent_runtime.tooling import (
    build_agent_tools,
    validate_clarification_reply,
)
from platform_kernel.database import connect_database, utc_now
from platform_kernel.errors import AgentRunError


def _reply(question="你希望得到解释、分析建议，还是可复用的处理流程？"):
    return {
        "conclusion": None,
        "details": [],
        "unresolved_items": [{"text": question, "citations": []}],
        "referenced_workflow_runs": [],
        "include_execution_status": False,
        "interaction_options": [
            {"label": "解释问题", "description": "直接解释当前问题", "message": "请直接解释当前问题。"},
            {"label": "比较方法", "description": "比较适用方法", "message": "请比较当前可行的方法。"},
        ],
        "workflow_draft": None,
    }


def _clarification_tools(settings, client):
    capabilities = resolve_mode_capabilities(
        {"mode": "DIALOGUE", "clarification_only": True}, settings.models
    )
    tools = build_agent_tools(
        settings.data_root / "kernel.sqlite3",
        settings.services,
        settings.observation_tools,
        settings.models,
        ObservationClient(client),
        request_context={"mode": "DIALOGUE", "clarification_only": True},
        operator_catalog=settings.operator_catalog,
        capabilities=capabilities,
    )
    return capabilities, tools


def test_clarification_mode_exposes_only_strict_submit_reply(kernel_settings):
    settings = kernel_settings()

    async def exercise():
        async with httpx.AsyncClient(trust_env=False) as client:
            capabilities, tools = _clarification_tools(settings, client)
            assert capabilities.clarification_only
            assert capabilities.read_only
            assert not capabilities.allow_observations
            assert tools[0].name == "submit_reply" and len(tools) == 1
            assert tools[0].strict_json_schema
            schema = tools[0].params_json_schema
            assert schema["properties"]["conclusion"] == {"type": "null"}
            assert schema["properties"]["details"]["maxItems"] == 0
            assert schema["properties"]["unresolved_items"]["minItems"] == 1
            assert schema["properties"]["unresolved_items"]["maxItems"] == 1
            validated = await tools[0].on_invoke_tool(
                None, json.dumps(_reply(), ensure_ascii=False)
            )
            assert validate_clarification_reply(validated).endswith("？")

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(
            conclusion={"text": "这里是直接回答。", "citations": []}
        ),
        lambda value: value["unresolved_items"].append(
            {"text": "你还需要什么？", "citations": []}
        ),
        lambda value: value["unresolved_items"][0].update(
            text="请补充你的具体目标"
        ),
            lambda value: value["unresolved_items"][0]["citations"].append({
                "version_id": "version-doc", "unit_id": "unit-0",
                "checksum": "sha256:" + "a" * 64,
            }),
        lambda value: value.update(include_execution_status=True),
    ],
)
def test_clarification_submit_rejects_answers_multiple_questions_and_references(
    kernel_settings, mutate,
):
    settings = kernel_settings()
    body = _reply()
    mutate(body)

    async def exercise():
        async with httpx.AsyncClient(trust_env=False) as client:
            _, tools = _clarification_tools(settings, client)
            with pytest.raises(AgentRunError, match="AGENT_MODEL_OUTPUT_INVALID"):
                await tools[0].on_invoke_tool(
                    None, json.dumps(body, ensure_ascii=False)
                )

    asyncio.run(exercise())


def test_clarification_agent_prompt_and_capabilities_do_not_expose_business_tools(
    kernel_settings,
):
    settings = kernel_settings()
    model = ScriptedModel([])

    async def exercise():
        async with httpx.AsyncClient(trust_env=False) as client:
            dependencies = SDKExecutionDependencies(
                database=settings.data_root / "kernel.sqlite3",
                model=model,
                model_settings=build_model_settings(settings),
                settings=settings,
                observation_client=ObservationClient(client),
                agent_slots=asyncio.Semaphore(1),
                activity_log=CallbackActivityLog(
                    lambda _session_id, _activity: None
                ),
            )
            agent = _build_agent(
                dependencies,
                ExecutionContextV1(()),
                {"mode": "DIALOGUE", "clarification_only": True},
            )
            assert [tool.name for tool in agent.tools] == ["submit_reply"]
            assert "This request is clarification-only" in agent.instructions
            assert "inspect_data_version" not in agent.instructions
            assert "run_summary" not in agent.instructions

    asyncio.run(exercise())


def test_clarification_render_is_exactly_one_question_without_heading(kernel_settings):
    settings = kernel_settings()
    database = settings.data_root / "kernel.sqlite3"
    now = utc_now()
    with connect_database(database) as connection:
        connection.execute(
            "INSERT INTO sessions (session_id, project_id, goal_revision, agent_run_id, pending_agent_run_id, agent_start_state, owner_subject) VALUES ('session','p',1,'run',NULL,NULL,'local-researcher')"
        )
        connection.execute(
            "INSERT INTO agent_requests VALUES ('run','DIALOGUE',NULL,'READ_ONLY')"
        )
        connection.execute(
            """INSERT INTO message_requests(
                   agent_run_id,session_id,request_id,goal_revision,message,input_references_json,
                   requested_mode,routing_source,routing_status,intent_decision,resolved_mode,
                   semantic_intent,task_action,clarification_reason,
                   clarification_required,route_input_tokens,route_output_tokens,
                   route_total_tokens,route_attempts,route_provider_requests,
                   unknown_cost_attempts,created_at
               ) VALUES (
                   'run','session','request',1,'帮我处理一下','[]','AUTO','AUTO','DECIDED',
                   'NEEDS_CLARIFICATION','DIALOGUE','UNKNOWN','NONE',
                   'REQUEST_KIND_AMBIGUOUS',1,0,0,0,1,0,0,?
               )""",
            (now,),
        )
    create_run(
        database,
        agent_run_id="run",
        session_id="session",
        project_id="p",
        goal_revision=1,
        provider="deepseek",
        model="test-model",
        trace_id="trace_" + "a" * 32,
        started_at=now,
    )
    question = _reply()["unresolved_items"][0]["text"]
    result = SimpleNamespace(
        final_output=json.dumps(_reply(), ensure_ascii=False),
        to_input_list=lambda: [],
    )

    rendered = render_submitted_reply(database, "run", result)
    assert rendered.message == question
    assert rendered.unresolved_items == [question]
    assert rendered.workflow_runs == []
    assert len(rendered.interaction_options) == 2
    assert rendered.presentation.model_dump() == {
        "conclusion": None,
        "details": [],
        "execution_summary": None,
        "unresolved_items": [{"markdown": question, "source_indexes": []}],
        "sources": [],
    }

    invalid = _reply()
    invalid["conclusion"] = {"text": "直接回答。", "citations": []}
    result.final_output = json.dumps(invalid, ensure_ascii=False)
    with pytest.raises(AgentRunError, match="AGENT_MODEL_OUTPUT_INVALID"):
        render_submitted_reply(database, "run", result)
