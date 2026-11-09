"""Regression for the actual synthetic DeepSeek record_plan title response."""
from __future__ import annotations


import asyncio
from dataclasses import replace
import json
from pathlib import Path

from agents.testing import ScriptedModel, ModelStep, function_call
import httpx
import pytest

from platform_kernel.composition import PlatformApplication
from platform_kernel.database import connect_database
from platform_kernel.errors import AgentRunError


FIXTURE = Path(__file__).parent / "fixtures/record-plan-title-probe.json"


def _reply_step():
    return ModelStep(output=[function_call("submit_reply", {
        "conclusion": {"text": "计划已记录", "citations": []},
        "details": [],
        "unresolved_items": [{
            "text": "缺少执行所需的数据版本。", "citations": [],
        }], "referenced_workflow_runs": [],
        "include_execution_status": False,
        "interaction_options": [], "workflow_draft": None,
    }, call_id="reply")])


def _application(kernel_settings, arguments, *, model_steps=None, **settings_changes):
    model = ScriptedModel(model_steps or [
        ModelStep(output=[function_call("record_plan", arguments, call_id="plan")]),
        _reply_step(),
    ])
    app = PlatformApplication(settings=replace(kernel_settings(), **settings_changes), model=model,
                              client=httpx.AsyncClient(transport=httpx.MockTransport(
                                  lambda _: httpx.Response(500))))
    session = app.interaction.create_session()
    return app, model, session.session.session_id


@pytest.mark.parametrize("title", ["recorded-probe", None, "absent"])
@pytest.mark.parametrize("closing_step", [False, True])
def test_saved_real_probe_title_is_internal_and_public_plan_stays_three_fields(kernel_settings, title, closing_step):
    arguments = json.loads(FIXTURE.read_text())
    if title is None:
        arguments["steps"][0]["title"] = None
    elif title == "absent":
        del arguments["steps"][0]["title"]
    if closing_step:
        arguments["steps"].append({
            "step_id": "close", "tool": "submit_reply", "purpose": "结束工具阶段并提交正文",
        })
    app, model, session_id = _application(kernel_settings, arguments)
    session = asyncio.run(app.interaction.turn(
        session_id, "对样本做统计", dialogue_policy="EXECUTION_PROPOSAL",
    ))
    assert session.session.agent_output.message == (
        "计划已记录\n\n需要补充：\n缺少执行所需的数据版本。"
    )
    run = app.agent_runtime.get(session.session.agent_run_id)
    expected = [{key: step[key] for key in ("step_id", "tool", "purpose")}
                for step in arguments["steps"]]
    assert [step.model_dump() for step in run.plans[0].steps] == expected
    with connect_database(app.database) as connection:
        stored = connection.execute("SELECT steps_json FROM agent_plans").fetchone()[0]
        assert json.loads(stored) == expected
        assert connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] == 0
    assert len(model.calls) == 2


def test_minimax_record_plan_normalizes_nested_schema_aliases(kernel_settings):
    arguments = json.loads(FIXTURE.read_text())
    arguments["steps"][0]["stepId"] = arguments["steps"][0].pop("step_id")
    app, _model, session_id = _application(
        kernel_settings,
        arguments,
        agent_provider="minimax",
        agent_protocol="chat_completions",
    )

    session = asyncio.run(app.interaction.turn(
        session_id, "对样本做统计", dialogue_policy="EXECUTION_PROPOSAL",
    ))

    assert app.agent_runtime.get(session.session.agent_run_id).plans[0].steps[0].step_id


@pytest.mark.parametrize("invalid", ["unknown_step_field", "title_number"])
def test_deepseek_retries_invalid_plan_schema_once_without_persisting_it(
    kernel_settings, invalid,
):
    invalid_arguments = json.loads(FIXTURE.read_text())
    step = invalid_arguments["steps"][0]
    if invalid == "unknown_step_field":
        step["description"] = "not an accepted field"
    elif invalid == "title_number":
        step["title"] = 42
    valid_arguments = json.loads(FIXTURE.read_text())
    app, model, session_id = _application(
        kernel_settings,
        invalid_arguments,
        model_steps=[
            ModelStep(output=[function_call(
                "record_plan", invalid_arguments, call_id="invalid-plan",
            )]),
            ModelStep(output=[function_call(
                "record_plan", valid_arguments, call_id="repaired-plan",
            )]),
            _reply_step(),
        ],
    )

    session = asyncio.run(app.interaction.turn(
        session_id, "对样本做统计", dialogue_policy="EXECUTION_PROPOSAL",
    ))

    run = app.agent_runtime.get(session.session.agent_run_id)
    assert len(run.plans) == 1
    assert run.plans[0].steps[0].tool == "run_summary"
    assert model.calls[1].model_settings.tool_choice == "record_plan"
    assert model.calls[1].model_settings.parallel_tool_calls is False
    with connect_database(app.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_plans").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] == 0


def test_deepseek_non_utf8_plan_value_fails_before_tool_invocation(kernel_settings):
    arguments = json.loads(FIXTURE.read_text())
    arguments["steps"][0]["title"] = "\ud800"
    app, model, session_id = _application(kernel_settings, arguments)

    with pytest.raises(AgentRunError, match="AGENT_MODEL_OUTPUT_INVALID"):
        asyncio.run(app.interaction.turn(
            session_id, "对样本做统计", dialogue_policy="EXECUTION_PROPOSAL",
        ))

    assert len(model.calls) == 1
    with connect_database(app.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_plans").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] == 0


@pytest.mark.parametrize("invalid", ["unknown_tool", "duplicate_step"])
def test_deepseek_retries_invalid_plan_value_once_without_persisting_it(
    kernel_settings, invalid,
):
    invalid_arguments = json.loads(FIXTURE.read_text())
    if invalid == "unknown_tool":
        invalid_arguments["steps"][0]["tool"] = "groundwater.summary"
    else:
        invalid_arguments["steps"].append(dict(invalid_arguments["steps"][0]))
    valid_arguments = json.loads(FIXTURE.read_text())
    app, model, session_id = _application(
        kernel_settings,
        invalid_arguments,
        model_steps=[
            ModelStep(output=[function_call(
                "record_plan", invalid_arguments, call_id="invalid-plan",
            )]),
            ModelStep(output=[function_call(
                "record_plan", valid_arguments, call_id="repaired-plan",
            )]),
            _reply_step(),
        ],
    )

    session = asyncio.run(app.interaction.turn(
        session_id, "对样本做统计", dialogue_policy="EXECUTION_PROPOSAL",
    ))

    run = app.agent_runtime.get(session.session.agent_run_id)
    assert len(run.plans) == 1
    assert run.plans[0].steps[0].tool == "run_summary"
    assert model.calls[1].model_settings.tool_choice == "record_plan"
    assert model.calls[1].model_settings.parallel_tool_calls is False
    with connect_database(app.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_plans").fetchone()[0] == 1
        attempts = [tuple(row) for row in connection.execute(
            "SELECT attempt,outcome,error_class FROM provider_requests "
            "WHERE agent_run_id=? ORDER BY created_at,rowid",
            (session.session.agent_run_id,),
        )]
    assert attempts[:2] == [
        (1, "FAILED", "PROTOCOL_OR_SCHEMA_INVALID"),
        (2, "SUCCEEDED", None),
    ]


def test_deepseek_invalid_plan_value_retry_is_bounded(kernel_settings):
    arguments = json.loads(FIXTURE.read_text())
    arguments["steps"][0]["tool"] = "groundwater.summary"
    app, model, session_id = _application(
        kernel_settings,
        arguments,
        model_steps=[
            ModelStep(output=[function_call(
                "record_plan", arguments, call_id="invalid-plan-1",
            )]),
            ModelStep(output=[function_call(
                "record_plan", arguments, call_id="invalid-plan-2",
            )]),
        ],
    )

    with pytest.raises(AgentRunError, match="AGENT_PLAN_INVALID"):
        asyncio.run(app.interaction.turn(
            session_id, "对样本做统计", dialogue_policy="EXECUTION_PROPOSAL",
        ))

    assert len(model.calls) == 2
    with connect_database(app.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_plans").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] == 0


def test_deepseek_invalid_plan_schema_retry_is_bounded(kernel_settings):
    arguments = json.loads(FIXTURE.read_text())
    arguments["steps"][0]["description"] = "not an accepted field"
    app, model, session_id = _application(
        kernel_settings,
        arguments,
        model_steps=[
            ModelStep(output=[function_call(
                "record_plan", arguments, call_id="invalid-plan-1",
            )]),
            ModelStep(output=[function_call(
                "record_plan", arguments, call_id="invalid-plan-2",
            )]),
        ],
    )

    with pytest.raises(AgentRunError, match="AGENT_PLAN_INVALID"):
        asyncio.run(app.interaction.turn(
            session_id, "对样本做统计", dialogue_policy="EXECUTION_PROPOSAL",
        ))

    assert len(model.calls) == 2
    with connect_database(app.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_plans").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] == 0
