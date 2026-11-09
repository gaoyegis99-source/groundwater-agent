from __future__ import annotations


import asyncio
from dataclasses import replace
import json

from agents.testing import ScriptedModel, ModelStep, function_call
import httpx

from platform_kernel.composition import PlatformApplication
from platform_kernel.database import connect_database


def test_single_reply_loop_receives_current_assembly(kernel_settings):
    settings = kernel_settings("summary")
    second = replace(settings.models[0], model_id="custom.analysis", model_version="7.2.0",
                     workflow_id="custom.workflow", workflow_version="4.0.0",
                     tool_name="run_custom", tool_description="用户装配的分析能力",
                     final_outputs=("z-result", "a-result"))
    settings = replace(settings, models=(*settings.models, second))
    model = ScriptedModel([
        ModelStep(output=[function_call("submit_reply", {
            "conclusion": {"text": "当前有两项已装配分析能力。", "citations": []},
            "details": [],
            "unresolved_items": [], "referenced_workflow_runs": [],
            "include_execution_status": False,
            "interaction_options": [], "workflow_draft": None,
        }, call_id="reply")]),
    ])

    async def scenario():
        app = PlatformApplication(settings=settings, model=model,
                                  client=httpx.AsyncClient(transport=httpx.MockTransport(
                                      lambda _: httpx.Response(500))))
        try:
            with connect_database(app.database) as connection:
                connection.execute("INSERT INTO sessions (session_id, project_id, goal_revision, agent_run_id, pending_agent_run_id, agent_start_state, owner_subject) VALUES ('s','p',1,NULL,NULL,NULL,'local-researcher')")
            result = await app.agent_runtime.start("s", "p", 1, "有哪些可用工作流？", (),
                                                   agent_run_id="a")
            assert result.output.unresolved_items == []
            assert result.output.workflow_runs == []
            with connect_database(app.database) as connection:
                assert connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] == 0
        finally:
            await app.close()

    asyncio.run(scenario())
    assert len(model.calls) == 1
    for call in model.calls:
        instructions = call.system_instructions
        catalog = next(json.loads(line) for line in instructions.splitlines()
                       if line.startswith('{"observation_tools":'))
        assert [(item["model_id"], item["workflow_id"], item["workflow_version"])
                for item in catalog["executable_models"]] == [
                    ("groundwater.summary", "groundwater.summary", "1.0.0"),
                    ("custom.analysis", "custom.workflow", "4.0.0"),
                ]
        assert catalog["observation_tools"] == ["inspect_data_version"]
        assert [item["final_outputs"] for item in catalog["executable_models"]] == [
            ["summary"], ["z-result", "a-result"],
        ]
        assert all(set(item) == {
            "model_id", "model_version", "workflow_id", "workflow_version", "tool",
            "description", "final_outputs",
        } for item in catalog["executable_models"])
        assert "final_outputs lists the named RESULT Data versions" in instructions
        assert settings.agent_api_key not in instructions
        assert "http://executor-service:8000" not in instructions
    assert any(tool.name == "submit_reply" for tool in model.calls[-1].tools)
    assert model.calls[-1].output_schema is None
    initial = model.calls[0]
    assert "Never put a lookup\nand a call that depends on its result in the same response" in initial.system_instructions
    assert "record one plan with a step for each task in the requested order" in initial.system_instructions
    assert "Wait for the\nsaved output, inspect its exact reference" in initial.system_instructions
    observation, = [tool for tool in initial.tools if tool.name == "inspect_data_version"]
    assert observation.description == settings.observation_tools[0].description
    assert observation.params_json_schema == settings.observation_tools[0].input_schema
