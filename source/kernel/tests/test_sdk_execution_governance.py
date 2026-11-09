from __future__ import annotations

from kernel.tests.task_testing import preserve_context_headroom


import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace

from agents import ModelBehaviorError, ModelSettings, Usage
from agents.testing import ScriptedModel, ModelStep, assistant_message, function_call
from kernel.tests.reply_testing import submit_reply
import httpx
import httpx2
from openai import APIConnectionError
import pytest

from platform_kernel.agent_runtime.sdk_execution import execution as sdk_execution
from platform_kernel.agent_runtime.sdk_execution import usage_ports
from platform_kernel.agent_runtime import model_tool
from platform_kernel.agent_runtime.context_usage import context_tokens
from platform_kernel.agent_runtime.memory import ExecutionContextV1, token_count
from platform_kernel.agent_runtime.provider import build_model_settings, protocol_retry_policy
from platform_kernel.agent_runtime.governance import (
    new_governance, run_limits, validate_governance,
)
from platform_kernel.agent_runtime.sdk_execution.activity import _RunUsageCapture
from platform_kernel.agent_runtime.sdk_execution.usage_ports import (
    CallbackActivityLog,
    CallbackActivityRecorder,
)
from platform_kernel.agent_runtime.mode import AgentModeCapabilities
from platform_kernel.composition import PlatformApplication
from platform_kernel.database import connect_database, read_session_events, utc_now
from platform_kernel.agent_runtime.persistence import create_run, ensure_dialogue_request
from platform_kernel.agent_runtime.persistence.types import ReplayedAction
from platform_kernel.errors import AgentRunError, UpstreamServiceError


def _final():
    return submit_reply('done')


def _wire_knowledge_reference(reference):
    return {
        "versionId": reference["version_id"],
        "unitId": reference["unit_id"],
        "checksum": reference["checksum"],
    }


def _execution_final():
    return function_call("submit_reply", {
        "conclusion": {"text": "done", "citations": []},
        "details": [], "unresolved_items": [],
        "referenced_workflow_runs": [],
        "include_execution_status": True,
        "interaction_options": [], "workflow_draft": None,
    }, call_id="execution-reply")


def _summary():
    return assistant_message(
        '{"goals":["analyze"],"constraints":[],"decisions":[],"unresolved_items":[]}'
    )


def _history():
    return tuple(
        item
        for revision in range(1, 10)
        for item in (
            (revision, "user", f"turn {revision} " + "x " * 100),
            (revision, "assistant", f"answer {revision} " + "y " * 100),
        )
    )


def _application(
    kernel_settings, model, *, capabilities="knowledge", observation="small",
    observation_handler=None, observation_tools=None, window=3800,
    policy="READ_ONLY", settings_changes=None,
):
    settings = replace(
        kernel_settings(capabilities),
        agent_context_window_tokens=window,
        agent_max_output_tokens=500,
    )
    if settings_changes is not None:
        settings = replace(settings, **settings_changes)
    if observation_tools is not None:
        settings = replace(settings, observation_tools=observation_tools)
    application = PlatformApplication(
        settings=settings,
        model=model,
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                observation_handler or (
                    lambda request: httpx.Response(200, json={"data": [{"text": observation}]})
                )
            )
        ),
    )
    with connect_database(application.database) as connection:
        connection.execute(
            "INSERT INTO sessions (session_id, project_id, goal_revision, agent_run_id, pending_agent_run_id, agent_start_state, owner_subject) VALUES ('session','project',10,NULL,NULL,NULL,'local-researcher')"
        )
    ensure_dialogue_request(
        application.database, "run", policy
    )
    preserve_context_headroom(application)
    return application


@pytest.mark.parametrize(
    ("mode", "clarification", "expected"),
    [
        ("DIALOGUE", True, (2, 0, 2, 0, None, 0)),
        ("DIALOGUE", False, (8, 7, 1, 12, 120_000, 20_000)),
        ("WORKFLOW_DRAFT", False, (14, 11, 3, 24, 300_000, 40_000)),
    ],
)
def test_mode_run_limits_are_fixed(mode, clarification, expected):
    capabilities = AgentModeCapabilities(
        mode, (), False, False, False, not clarification, clarification
    )
    limits = run_limits(capabilities)
    assert (
        limits.request_limit, limits.discovery_request_limit,
        limits.proposal_reserve, limits.read_tool_limit,
        limits.token_limit, limits.finalization_reserve,
    ) == expected


def test_execution_read_budget_covers_version_and_table_inspection_per_input():
    model = SimpleNamespace(data_input_parameters=tuple(
        f"input_{index}" for index in range(19)
    ))
    capabilities = AgentModeCapabilities(
        "DIALOGUE", (model,), True, True, False, True, False
    )

    limits = run_limits(capabilities)

    assert limits.request_limit == 11
    assert limits.discovery_request_limit == 5
    assert limits.proposal_reserve == 6
    assert limits.read_tool_limit == 38


async def _start(application, *, history=()):
    return await application.agent_runtime.start(
        "session", "project", 10, "query", history, agent_run_id="run"
    )


async def _initial(dependencies):
    def on_admitted():
        create_run(dependencies.database, agent_run_id="run", session_id="session",
                   project_id="project", goal_revision=10, provider="deepseek", model="test",
                   trace_id="trace_" + "a" * 32, started_at=utc_now())

    return await sdk_execution.execute_initial(
        dependencies,
        session_id="session",
        project_id="project",
        agent_run_id="run",
        goal_revision=10,
        trace_id="trace_" + "a" * 32,
        history=(),
        message="query",
        memory=None,
        execution_context=ExecutionContextV1(()),
        on_admitted=on_admitted,
    )


async def _blocked(_call):
    await asyncio.Event().wait()


def _set_timeouts(application, *, request=120, segment=300):
    dependencies = application.agent_runtime.sdk_execution
    settings = replace(
        dependencies.settings,
        agent_request_timeout_seconds=request,
        agent_segment_timeout_seconds=segment,
    )
    application.agent_runtime.sdk_execution = replace(
        dependencies, settings=settings, model_settings=build_model_settings(settings)
    )


def test_observation_growth_blocks_next_model_request_and_keeps_usage(kernel_settings):
    model = ScriptedModel(
        [
            ModelStep(
                output=[function_call("retrieve_evidence", {"query": "x"}, call_id="read")],
                usage=Usage(input_tokens=2, output_tokens=3, total_tokens=5),
            ),
        ]
    )
    application = _application(kernel_settings, model, observation="x " * 5000)

    with pytest.raises(AgentRunError, match="AGENT_CONTEXT_TOO_LARGE"):
        asyncio.run(_start(application))

    run = application.agent_runtime.get("run")
    assert (run.status, run.total_tokens) == ("FAILED", 5)
    assert len(model.calls) == 1


@pytest.mark.parametrize("final_call", ["submit_reply", "retrieve_evidence"])
def test_dialogue_enters_submit_only_finalization_on_eighth_request(
    kernel_settings, final_call,
):
    steps = [
        *[
            ModelStep(output=[function_call(
                "retrieve_evidence", {"query": f"page {index}"}, call_id=f"read-{index}",
            )])
            for index in range(7)
        ],
        ModelStep(output=[
            _final() if final_call == "submit_reply" else function_call(
                "retrieve_evidence", {"query": "page 7"}, call_id="read-7",
            )
        ]),
    ]
    if final_call == "retrieve_evidence":
        steps.append(ModelStep(output=[_final()]))
    model = ScriptedModel(steps)
    application = _application(kernel_settings, model)
    dependencies = application.agent_runtime.sdk_execution
    application.agent_runtime.sdk_execution = replace(
        dependencies,
        settings=replace(dependencies.settings, agent_context_window_tokens=131072),
    )

    assert asyncio.run(_start(application)).status == "COMPLETE"
    assert len(model.calls) == (8 if final_call == "submit_reply" else 9)
    assert [tool.name for tool in model.calls[-1].tools] == ["submit_reply"]
    if final_call == "retrieve_evidence":
        assert model.calls[-1].model_settings.tool_choice == "submit_reply"
        assert "DISCOVERY_CLOSED" in json.dumps(model.calls[-1].input)

    with connect_database(application.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM agent_actions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] == 0


@pytest.mark.parametrize("last_call", ["submit_reply", "retrieve_evidence"])
def test_exact_successful_duplicate_is_not_sent_upstream_and_forces_finalization(
    kernel_settings, last_call,
):
    upstream_calls = 0

    def observe(_request):
        nonlocal upstream_calls
        upstream_calls += 1
        return httpx.Response(200, json={"data": [{"text": "small"}]})

    steps = [
        ModelStep(output=[function_call(
            "retrieve_evidence", {"query": "same"}, call_id="read-1",
        )]),
        ModelStep(output=[function_call(
            "retrieve_evidence", {"query": "same"}, call_id="read-2",
        )]),
        ModelStep(output=[
            _final() if last_call == "submit_reply" else function_call(
                "retrieve_evidence", {"query": "different"}, call_id="read-3",
            )
        ]),
    ]
    if last_call == "retrieve_evidence":
        steps.append(ModelStep(output=[_final()]))
    model = ScriptedModel(steps)
    application = _application(
        kernel_settings, model, observation_handler=observe, window=131072,
    )
    assert asyncio.run(_start(application)).status == "COMPLETE"
    assert [tool.name for tool in model.calls[-1].tools] == ["submit_reply"]
    if last_call == "retrieve_evidence":
        assert model.calls[-1].model_settings.tool_choice == "submit_reply"
        assert "DISCOVERY_CLOSED" in json.dumps(model.calls[-1].input)
    assert upstream_calls == 1


@pytest.mark.parametrize("tool_name", ["discover_datasets"])
def test_equivalent_dataset_searches_advance_execution_to_proposal(
    kernel_settings, tool_name
):
    def observe(request):
        assert request.url.path.endswith(tool_name)
        return httpx.Response(200, json={"data": {
            "items": [{"datasetId": "dataset-a", "currentVersion": "version-a"}],
            "nextCursor": None,
        }})

    base_tool = kernel_settings("summary").observation_tools[0]
    search_tool = replace(
        base_tool,
        name=tool_name,
        path=f"/internal/tools/v3/{tool_name}",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )
    model = ScriptedModel([
        ModelStep(output=[function_call(
            tool_name, {"query": "腰站堡"}, call_id="search-1",
        )]),
        ModelStep(output=[function_call(
            tool_name, {"query": "标准化资料"}, call_id="search-2",
        )]),
        ModelStep(output=[function_call("submit_reply", {
            "conclusion": {"text": "尚不能形成执行提案。", "citations": []},
            "details": [],
            "unresolved_items": [{"text": "缺少完整输入版本。", "citations": []}],
            "referenced_workflow_runs": [],
            "include_execution_status": False,
            "interaction_options": [],
            "workflow_draft": None,
        }, call_id="reply")]),
    ])
    application = _application(
        kernel_settings, model, capabilities="summary",
        observation_handler=observe, observation_tools=(search_tool,),
        window=131072,
        policy="EXECUTION_PROPOSAL",
        settings_changes={
            "agent_provider": "deepseek",
            "agent_protocol": "responses",
        },
    )

    assert asyncio.run(_start(application)).status == "COMPLETE"
    names = {tool.name for tool in model.calls[-1].tools}
    assert tool_name not in names
    assert "run_summary" in names
    assert "record_plan" in names
    assert model.calls[-1].model_settings.tool_choice == "prepare_task_inputs"
    assert model.calls[-1].model_settings.parallel_tool_calls is False


def test_minimax_repairs_disabled_discovery_tool_after_proposal_transition(
    kernel_settings,
):
    upstream_calls = []

    def observe(request):
        upstream_calls.append(request.url.path)
        return httpx.Response(200, json={"data": {
            "items": [{"datasetId": "dataset-a", "currentVersion": "version-a"}],
            "nextCursor": None,
        }})

    base_tool = kernel_settings("summary").observation_tools[0]
    search_tool = replace(
        base_tool,
        name="discover_datasets",
        path="/internal/tools/v3/discover_datasets",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )
    model = ScriptedModel([
        ModelStep(output=[function_call(
            "discover_datasets", {"query": "腰站堡"}, call_id="search-1",
        )]),
        ModelStep(output=[function_call(
            "discover_datasets", {"query": "标准化资料"}, call_id="search-2",
        )]),
        ModelStep(output=[function_call(
            "discover_datasets", {"query": "should-not-run"}, call_id="stale-tool",
        )]),
        ModelStep(output=[function_call("submit_reply", {
            "conclusion": {"text": "尚不能形成执行提案。", "citations": []},
            "details": [],
            "unresolved_items": [{"text": "缺少完整输入版本。", "citations": []}],
            "referenced_workflow_runs": [],
            "include_execution_status": False,
            "interaction_options": [],
            "workflow_draft": None,
        }, call_id="reply")]),
    ])
    application = _application(
        kernel_settings,
        model,
        capabilities="summary",
        observation_handler=observe,
        observation_tools=(search_tool,),
        window=131072,
        policy="EXECUTION_PROPOSAL",
        settings_changes={
            "agent_provider": "minimax",
            "agent_protocol": "chat_completions",
        },
    )

    assert asyncio.run(_start(application)).status == "COMPLETE"
    assert len(upstream_calls) == 2
    assert len(model.calls) == 4
    assert "Tool 'discover_datasets' not found" in json.dumps(model.calls[-1].input)
    assert "discover_datasets" not in {
        tool.name for tool in model.calls[-1].tools
    }
    assert model.calls[-1].model_settings.tool_choice == "submit_reply"
    assert model.calls[-1].model_settings.parallel_tool_calls is False


def test_stale_read_does_not_forget_an_earlier_successful_read(kernel_settings):
    checksum_a = "sha256:" + "a" * 64
    checksum_b = "sha256:" + "b" * 64
    upstream = []

    def observe(request):
        arguments = json.loads(request.content)["arguments"]
        reference = arguments["refs"][0]
        upstream.append((reference["version_id"], reference["checksum"]))
        if reference["version_id"] == "b":
            return httpx.Response(200, json={"data": {"items": [{
                **_wire_knowledge_reference(reference),
                "status": "STALE", "content": None,
            }]}})
        return httpx.Response(200, json={"data": {"items": [{
            **_wire_knowledge_reference(reference), "status": "OK", "bindingMatch": "EXACT",
            "knowledgeId": "a",
            "locator": {"title": "A"}, "content": "Evidence A.",
        }]}})

    def read(knowledge_id, checksum, call_id):
        return function_call("read_evidence", {
            "refs": [{"version_id": knowledge_id, "unit_id": "unit-0",
                "checksum": checksum}],
        }, call_id=call_id)

    cited_reply = function_call("submit_reply", {
        "conclusion": {"text": "结论。", "citations": [{
            "version_id": "a", "unit_id": "unit-0", "checksum": checksum_a,
        }]},
        "details": [],
        "unresolved_items": [], "referenced_workflow_runs": [],
        "include_execution_status": False, "interaction_options": [],
        "workflow_draft": None,
    }, call_id="reply")
    model = ScriptedModel([
        ModelStep(output=[read("a", checksum_a, "a-1")]),
        ModelStep(output=[read("b", checksum_b, "b")]),
        ModelStep(output=[read("a", checksum_a, "a-2")]),
        ModelStep(output=[cited_reply]),
    ])
    application = _application(
        kernel_settings, model, capabilities="knowledge",
        observation_handler=observe, window=131072,
    )
    assert asyncio.run(_start(application)).status == "COMPLETE"
    assert upstream == [("a", checksum_a), ("b", checksum_b)]


def test_new_checksum_after_stale_read_is_sent_upstream(kernel_settings):
    old_checksum = "sha256:" + "a" * 64
    stale_checksum = "sha256:" + "b" * 64
    new_checksum = "sha256:" + "c" * 64
    upstream = []

    def observe(request):
        arguments = json.loads(request.content)["arguments"]
        reference = arguments["refs"][0]
        upstream.append(reference["checksum"])
        status = "STALE" if reference["version_id"] == "b" else "OK"
        return httpx.Response(200, json={"data": {"items": [{
            **_wire_knowledge_reference(reference), "status": status,
            "bindingMatch": "EXACT",
            "knowledgeId": reference["version_id"],
            "locator": {"title": "A"},
            "content": "Evidence A." if status == "OK" else None,
        }]}})

    def read(knowledge_id, checksum, call_id):
        return ModelStep(output=[function_call("read_evidence", {
            "refs": [{"version_id": knowledge_id, "unit_id": "unit-0",
                "checksum": checksum}],
        }, call_id=call_id)])

    reply = function_call("submit_reply", {
        "conclusion": {"text": "结论。", "citations": [{
            "version_id": "a", "unit_id": "unit-0", "checksum": new_checksum,
        }]},
        "details": [],
        "unresolved_items": [], "referenced_workflow_runs": [],
        "include_execution_status": False, "interaction_options": [],
        "workflow_draft": None,
    }, call_id="reply")
    model = ScriptedModel([
        read("a", old_checksum, "old"), read("b", stale_checksum, "stale"),
        read("a", new_checksum, "new"), ModelStep(output=[reply]),
    ])
    application = _application(
        kernel_settings, model, capabilities="knowledge",
        observation_handler=observe, window=131072,
    )
    assert asyncio.run(_start(application)).status == "COMPLETE"
    assert upstream == [old_checksum, stale_checksum, new_checksum]


def test_read_tool_limit_short_circuits_thirteenth_call(kernel_settings):
    upstream_calls = 0

    def observe(_request):
        nonlocal upstream_calls
        upstream_calls += 1
        return httpx.Response(200, json={"data": [{"text": "small"}]})

    model = ScriptedModel([
        ModelStep(output=[
            function_call(
                "retrieve_evidence", {"query": f"page-{index}"}, call_id=f"read-{index}",
            )
            for index in range(13)
        ]),
        ModelStep(output=[_final()]),
    ])
    application = _application(
        kernel_settings, model, observation_handler=observe, window=131072,
    )
    assert asyncio.run(_start(application)).status == "COMPLETE"
    assert upstream_calls == 12
    assert [tool.name for tool in model.calls[-1].tools] == ["submit_reply"]


def test_reported_token_threshold_is_diagnostic_and_does_not_hide_tools(kernel_settings):
    model = ScriptedModel([
        ModelStep(
            output=[function_call(
                "retrieve_evidence", {"query": "large usage"}, call_id="read",
            )],
            usage=Usage(input_tokens=120_000, output_tokens=1_000, total_tokens=121_000),
        ),
        ModelStep(output=[_final()]),
    ])
    application = _application(kernel_settings, model, window=131072)
    assert asyncio.run(_start(application)).status == "COMPLETE"
    assert "retrieve_evidence" in [tool.name for tool in model.calls[-1].tools]
    with connect_database(application.database) as connection:
        activities = read_session_events(
            connection, "session", "agent.activity", agent_run_ids=("run",),
        )
    warning = next(
        item["data"] for item in activities
        if item["data"]["kind"] == "DIAGNOSTIC"
    )
    assert warning["purpose"] == "TOKEN_SOFT_TARGET"
    assert warning["severity"] == "WARNING"


def test_context_estimate_includes_submit_reply_schema(kernel_settings):
    application = _application(kernel_settings, ScriptedModel([]))
    agent = sdk_execution._build_agent(
        application.agent_runtime.sdk_execution, ExecutionContextV1(())
    )
    input_items = [{"role": "user", "content": "query"}]
    expected = {
        "instructions": agent.instructions,
        "tools": [
            {
                "name": item.name,
                "description": item.description,
                "input_schema": item.params_json_schema,
                "output_schema": item.output_json_schema,
            }
            for item in agent.tools
        ],
        "output_schema": None,
        "input": input_items,
    }
    assert context_tokens(agent, agent.instructions, input_items) == (
        token_count(expected)
    )
    without_schema = {key: value for key, value in expected.items() if key != "output_schema"}
    assert token_count(expected) > token_count(without_schema)


@pytest.mark.parametrize(
    ("request_context", "maximum"),
    [
        (None, 1_520),
        ({"mode": "WORKFLOW_DRAFT"}, 5_900),
    ],
)
def test_submit_reply_schema_removes_metadata_titles_and_stays_bounded(
    kernel_settings, request_context, maximum,
):
    application = _application(kernel_settings, ScriptedModel([]))
    agent = sdk_execution._build_agent(
        application.agent_runtime.sdk_execution,
        ExecutionContextV1(()),
        request_context,
    )
    schema = next(
        tool.params_json_schema for tool in agent.tools if tool.name == "submit_reply"
    )

    def assert_no_schema_title(value, *, property_map=False):
        if isinstance(value, list):
            for item in value:
                assert_no_schema_title(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                assert key != "title" or property_map
                assert_no_schema_title(
                    item,
                    property_map=key in {"properties", "patternProperties", "$defs", "definitions"},
                )

    assert_no_schema_title(schema)
    assert schema["properties"]["details"]["maxItems"] == 3
    assert len(json.dumps(schema, ensure_ascii=False, separators=(",", ":"))) <= maximum


@pytest.mark.parametrize("oversized_input", [False, True])
def test_resume_budgets_real_items_not_run_state_metadata(kernel_settings, oversized_input):
    model = ScriptedModel(
        [
            ModelStep(
                output=[function_call("run_summary", {"resource_id": "a"}, call_id="run")],
                usage=Usage(input_tokens=2, output_tokens=3, total_tokens=5),
            ),
            ModelStep(output=[_execution_final()], usage=Usage(input_tokens=3, output_tokens=4, total_tokens=7)),
        ]
    )
    application = _application(
        kernel_settings, model, capabilities="summary", policy="EXECUTION_PROPOSAL",
    )
    dependencies = application.agent_runtime.sdk_execution

    async def exercise():
        initial = await _initial(dependencies)
        payload = initial.result.to_state().to_json(strict_context=True)
        assert payload["max_turns"] == 22
        assert payload["context"]["context"]["governance"]["request_count"] == 1
        payload["context"]["context"]["recovery_metadata"] = "x " * 5000
        assert token_count(payload) > 2500
        return await sdk_execution.execute_resumed(
            dependencies,
            session_id="session",
            project_id="project",
            agent_run_id="run",
            goal_revision=10,
            trace_id="trace_" + "a" * 32,
            state_json=json.dumps(payload),
            action_statuses={},
            approve=False,
            reason="no " * 5000 if oversized_input else "declined",
            execution_context=ExecutionContextV1(()),
        )

    if oversized_input:
        with pytest.raises(sdk_execution.SDKExecutionError) as failure:
            asyncio.run(exercise())
        assert failure.value.code == "AGENT_CONTEXT_TOO_LARGE"
        assert failure.value.usage.total_tokens == 0
        assert len(model.calls) == 1
    else:
        execution = asyncio.run(exercise())
        output = json.loads(execution.result.final_output)
        assert output["conclusion"] is None
        assert output["include_execution_status"] is True
        assert execution.usage.total_tokens == 7
        assert application.agent_runtime.get("run").total_tokens == 0
        assert len(model.calls) == 2
        assert "recovery_metadata" not in json.dumps(model.calls[-1].input)


def test_minimax_resume_discards_model_authored_execution_claims(kernel_settings):
    malformed_execution_reply = function_call("submit_reply", {
        "conclusion": {"text": "模型声称完成", "citations": []},
        "details": [{
            "text": "直接执行另一个分析",
            "citations": [],
        }],
        "unresolved_items": [],
        "referenced_workflow_runs": ["model-authored-run"],
        "include_execution_status": False,
        "interaction_options": [],
        "workflow_draft": None,
    }, call_id="execution-reply")
    model = ScriptedModel([
        ModelStep(output=[function_call(
            "run_summary", {"resource_id": "a"}, call_id="run",
        )]),
        ModelStep(output=[malformed_execution_reply]),
    ])
    application = _application(
        kernel_settings,
        model,
        capabilities="summary",
        policy="EXECUTION_PROPOSAL",
        settings_changes={
            "agent_provider": "minimax",
            "agent_protocol": "chat_completions",
        },
    )
    dependencies = application.agent_runtime.sdk_execution

    async def exercise():
        initial = await _initial(dependencies)
        return await sdk_execution.execute_resumed(
            dependencies,
            session_id="session",
            project_id="project",
            agent_run_id="run",
            goal_revision=10,
            trace_id="trace_" + "a" * 32,
            state_json=json.dumps(
                initial.result.to_state().to_json(strict_context=True)
            ),
            action_statuses={},
            approve=False,
            reason="declined",
            execution_context=ExecutionContextV1(()),
        )

    execution = asyncio.run(exercise())
    output = json.loads(execution.result.final_output)
    assert output == {
        "conclusion": None,
        "details": [],
        "unresolved_items": [],
        "referenced_workflow_runs": [],
        "include_execution_status": True,
        "interaction_options": [],
        "workflow_draft": None,
    }


def test_resume_rejects_modified_governance_policy(kernel_settings):
    model = ScriptedModel([
        ModelStep(output=[function_call(
            "run_summary", {"resource_id": "a"}, call_id="run",
        )]),
    ])
    application = _application(
        kernel_settings, model, capabilities="summary", policy="EXECUTION_PROPOSAL",
    )
    dependencies = application.agent_runtime.sdk_execution

    async def exercise():
        initial = await _initial(dependencies)
        payload = initial.result.to_state().to_json(strict_context=True)
        payload["context"]["context"]["governance"]["request_limit"] = 99
        return await sdk_execution.execute_resumed(
            dependencies,
            session_id="session", project_id="project", agent_run_id="run",
            goal_revision=10, trace_id="trace_" + "a" * 32,
            state_json=json.dumps(payload), action_statuses={}, approve=False,
            reason="declined", execution_context=ExecutionContextV1(()),
        )

    with pytest.raises(sdk_execution.SDKStateError, match="AGENT_RUN_STATE_INVALID"):
        asyncio.run(exercise())


@pytest.mark.parametrize("repair_missing", [False, True])
@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("initial_turns", [1, 6])
@pytest.mark.parametrize(("request_count", "succeeds"), [(10, True), (11, False)])
def test_resume_enforces_remaining_request_boundary(
    kernel_settings, monkeypatch, request_count, succeeds, initial_turns, streamed, repair_missing,
):
    model = ScriptedModel([
        *[ModelStep(output=[function_call("record_plan", {
            "goal": "Prepare the requested summary",
            "steps": [{"step_id": "summary", "tool": "run_summary", "purpose": "Summarize"}],
            "reason": "Refine the plan before confirmation",
        }, call_id=f"plan-{index}")]) for index in range(initial_turns - 1)],
        ModelStep(output=[function_call(
            "run_summary", {"resource_id": "a"}, call_id="run",
        )]),
        *([ModelStep(output=[function_call("missing_tool", {}, call_id="missing")])] if repair_missing else []),
        ModelStep(output=[_execution_final()]),
    ])
    application = _application(
        kernel_settings, model, capabilities="summary", policy="EXECUTION_PROPOSAL", window=20_000,
    )
    dependencies = application.agent_runtime.sdk_execution
    if streamed:
        dependencies = replace(dependencies, publish_preview=lambda *_args: None)
    monkeypatch.setattr(
        model_tool,
        "load_replayed_action",
        lambda *_args: ReplayedAction(
            arguments={"resource_id": "a", "axis": "columns"},
            executed_arguments={"resource_id": "a", "axis": "columns"},
            status="COMPLETE",
            workflow_run_id="workflow",
            outputs=[],
            error=None,
        ),
    )

    async def exercise():
        initial = await _initial(dependencies)
        payload = initial.result.to_state().to_json(strict_context=True)
        payload["context"]["context"]["governance"]["request_count"] = request_count
        return await sdk_execution.execute_resumed(
            dependencies,
            session_id="session", project_id="project", agent_run_id="run",
            goal_revision=10, trace_id="trace_" + "a" * 32,
            state_json=json.dumps(payload), action_statuses={"run": "COMPLETE"},
            approve=True, reason=None, execution_context=ExecutionContextV1(()),
        )

    if succeeds:
        assert asyncio.run(exercise()).result.final_output is not None
        assert len(model.calls) == initial_turns + 1 + int(repair_missing)
        assert [tool.name for tool in model.calls[-1].tools] == ["submit_reply"]
        assert model.calls[-1].model_settings.tool_choice == "submit_reply"
        assert model.calls[-1].model_settings.parallel_tool_calls is False
    else:
        with pytest.raises(sdk_execution.SDKStateError, match="AGENT_RUN_STATE_INVALID"):
            asyncio.run(exercise())
        assert len(model.calls) == initial_turns


def test_provider_guard_never_starts_request_past_hard_limit():
    capabilities = AgentModeCapabilities("DIALOGUE", (), False, False, False, True, False)
    control = new_governance(capabilities)
    control["request_count"] = control["request_limit"]
    recorded = []
    hooks = _RunUsageCapture(
        1000,
        segment={"agent_run_id": "run", "goal_revision": 1, "segment_id": "segment"},
        activity_recorder=CallbackActivityRecorder(recorded.append),
        observation_names=frozenset(), purpose="PROCESSING",
    )
    context = SimpleNamespace(
        context={"governance": control},
        usage=SimpleNamespace(input_tokens=0, output_tokens=0, total_tokens=0),
    )
    with pytest.raises(AgentRunError, match="AGENT_BUDGET_EXCEEDED"):
        asyncio.run(hooks.on_llm_start(context, None, None, []))
    assert hooks.provider_request_count == 0
    assert recorded == []


def test_governance_migrates_legacy_state_and_validates_knowledge_digests():
    capabilities = AgentModeCapabilities(
        "DIALOGUE", (), False, False, False, True, False,
    )
    identity = ["version", "unit", "sha256:" + "a" * 64]
    current = new_governance(capabilities)
    current["knowledge_evidence"] = [identity]
    current["knowledge_exact_candidates"] = [identity]

    assert validate_governance(current, capabilities) is current

    legacy = dict(current)
    legacy.pop("knowledge_exact_candidates")
    migrated = validate_governance(legacy, capabilities)
    assert migrated["knowledge_evidence"] == [identity]
    assert migrated["knowledge_exact_candidates"] == []


@pytest.mark.parametrize(
    ("kind", "expected_error", "code"),
    [
        ("request", UpstreamServiceError, "AGENT_PROVIDER_UNAVAILABLE"),
        ("segment", AgentRunError, "AGENT_DEADLINE_EXCEEDED"),
    ],
)
def test_timeout_keeps_completed_usage_and_releases_slot(
    kernel_settings, monkeypatch, kind, expected_error, code
):
    started = asyncio.Event()

    async def blocked_after_first_response(_call):
        started.set()
        await asyncio.Event().wait()

    model = ScriptedModel(
        [
            ModelStep(
                output=[function_call("retrieve_evidence", {"query": "x"}, call_id="read")],
                usage=Usage(input_tokens=2, output_tokens=3, total_tokens=5),
            ),
            ModelStep.respond(blocked_after_first_response),
        ]
    )
    application = _application(kernel_settings, model)
    # Expire the real timeout path only once the second request is running.
    # SQLite/SDK setup time must not decide which response contributes usage.
    deadlines = []
    if kind == "segment":
        timeout_at = asyncio.timeout_at

        def capture_deadline(when):
            deadline = timeout_at(when)
            deadlines.append(deadline)
            return deadline

        monkeypatch.setattr(sdk_execution.asyncio, "timeout_at", capture_deadline)
    else:
        from agents.run_internal import model_retry

        await_attempt = model_retry._await_model_attempt
        attempts = 0

        async def expire_second_attempt(awaitable, timeout, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                pending = asyncio.ensure_future(awaitable)
                await started.wait()
                return await await_attempt(pending, 0, timeout_error_seconds=timeout)
            return await await_attempt(awaitable, timeout, **kwargs)

        monkeypatch.setattr(model_retry, "_await_model_attempt", expire_second_attempt)

    async def exercise():
        pending = asyncio.create_task(_start(application))
        await asyncio.wait_for(started.wait(), timeout=5)
        if kind == "segment":
            assert len(deadlines) == 1
            deadlines[0].reschedule(asyncio.get_running_loop().time())
        with pytest.raises(expected_error, match=code):
            await pending
        slots = application.agent_runtime.sdk_execution.agent_slots
        async with asyncio.timeout(1):
            for _ in range(8):
                await slots.acquire()
        for _ in range(8):
            slots.release()

    asyncio.run(exercise())
    run = application.agent_runtime.get("run")
    assert (run.status, run.error, run.total_tokens) == ("FAILED", code, 5)
    assert len(model.calls) == 2


def test_compaction_and_main_share_deadline_and_do_not_nest_admission(
    kernel_settings, monkeypatch
):
    model = ScriptedModel(
        [
            ModelStep(output=[_summary()], usage=Usage(input_tokens=1, output_tokens=2, total_tokens=3)),
            ModelStep(output=[_final()], usage=Usage(input_tokens=3, output_tokens=4, total_tokens=7)),
        ]
    )
    application = _application(kernel_settings, model)
    application.agent_runtime.sdk_execution = replace(
        application.agent_runtime.sdk_execution, agent_slots=asyncio.Semaphore(1)
    )
    deadlines = []
    timeout_at = asyncio.timeout_at

    def record_deadline(when):
        deadlines.append(when)
        return timeout_at(when)

    monkeypatch.setattr(sdk_execution.asyncio, "timeout_at", record_deadline)
    run = asyncio.run(asyncio.wait_for(_start(application, history=_history()), timeout=2))
    assert run.total_tokens == 10
    assert len(deadlines) == 2
    assert deadlines[0] == deadlines[1]
    assert len(model.calls) == 2
    assert any(tool.name == "submit_reply" for tool in model.calls[-1].tools)


@pytest.mark.parametrize("kind", ["request", "segment", "provider"])
def test_failure_after_compaction_commits_summary_and_usage(
    kernel_settings, monkeypatch, kind,
):
    blocked_request_started = asyncio.Event()

    async def blocked_after_recorded_usage(_call):
        blocked_request_started.set()
        await asyncio.Event().wait()

    model = ScriptedModel(
        [
            ModelStep(output=[_summary()], usage=Usage(input_tokens=1, output_tokens=2, total_tokens=3)),
            ModelStep(
                output=[function_call("retrieve_evidence", {"query": "x"}, call_id="read")],
                usage=Usage(input_tokens=4, output_tokens=5, total_tokens=9),
            ),
            APIConnectionError(request=httpx2.Request("POST", "http://agent.test/v1"))
            if kind == "provider"
            else ModelStep.respond(blocked_after_recorded_usage),
        ]
    )
    application = _application(kernel_settings, model, window=4200)
    deadlines = []
    if kind == "segment":
        timeout_at = asyncio.timeout_at

        def capture_deadline(when):
            deadline = timeout_at(when)
            deadlines.append(deadline)
            return deadline

        monkeypatch.setattr(sdk_execution.asyncio, "timeout_at", capture_deadline)
    _set_timeouts(
        application,
        request=0.2 if kind == "request" else 120,
        segment=120 if kind == "segment" else 300,
    )
    expected = AgentRunError if kind == "segment" else UpstreamServiceError
    code = "AGENT_DEADLINE_EXCEEDED" if kind == "segment" else "AGENT_PROVIDER_UNAVAILABLE"

    async def exercise():
        pending = asyncio.create_task(_start(application, history=_history()))
        if kind == "segment":
            await asyncio.wait_for(blocked_request_started.wait(), timeout=5)
            assert deadlines
            now = asyncio.get_running_loop().time()
            rescheduled = 0
            for deadline in deadlines:
                try:
                    deadline.reschedule(now)
                except RuntimeError:
                    # The compaction segment has already left its timeout
                    # context; only the active main segment is rescheduled.
                    continue
                rescheduled += 1
            assert rescheduled
        with pytest.raises(expected, match=code):
            await pending

    asyncio.run(exercise())
    run = application.agent_runtime.get("run")
    assert (run.status, run.total_tokens) == ("FAILED", 12)
    with connect_database(application.database) as connection:
        memory = connection.execute(
            "SELECT through_goal_revision FROM agent_memory WHERE session_id = 'session'"
        ).fetchone()
    assert memory[0] == 7


@pytest.mark.parametrize("kind", ["request", "segment"])
def test_compactor_timeout_has_no_memory_update(kernel_settings, monkeypatch, kind):
    started = asyncio.Event()

    async def blocked(_call):
        started.set()
        await asyncio.Event().wait()

    model = ScriptedModel([ModelStep.respond(blocked)])
    application = _application(kernel_settings, model)
    _set_timeouts(
        application,
        request=0.1 if kind == "request" else 120,
        segment=300,
    )
    deadlines = []
    if kind == "segment":
        timeout_at = asyncio.timeout_at

        def capture_deadline(when):
            deadline = timeout_at(when)
            deadlines.append(deadline)
            return deadline

        monkeypatch.setattr(sdk_execution.asyncio, "timeout_at", capture_deadline)
    expected = UpstreamServiceError if kind == "request" else AgentRunError

    async def exercise():
        pending = asyncio.create_task(_start(application, history=_history()))
        await asyncio.wait_for(started.wait(), timeout=5)
        if kind == "segment":
            assert len(deadlines) == 1
            deadlines[0].reschedule(asyncio.get_running_loop().time())
        with pytest.raises(expected):
            await pending

    asyncio.run(exercise())
    assert application.agent_runtime.get("run").total_tokens == 0
    with connect_database(application.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_memory").fetchone()[0] == 0
    assert len(model.calls) == 1


def test_waiting_for_admission_does_not_consume_segment_deadline(kernel_settings, monkeypatch):
    model = ScriptedModel([ModelStep(output=[_final()])])
    application = _application(kernel_settings, model)
    dependencies = replace(
        application.agent_runtime.sdk_execution, agent_slots=asyncio.Semaphore(1)
    )

    async def exercise():
        queued = asyncio.Event()
        elapsed = 0.0
        loop = asyncio.get_running_loop()
        real_time = loop.time
        deadlines = []
        timeout_at = asyncio.timeout_at

        def capture_deadline(when):
            deadlines.append(when)
            return timeout_at(when)

        def record_activity(session_id, payload):
            dependencies.activity_log.record(session_id, payload)
            if payload["kind"] == "SEGMENT" and payload["status"] == "QUEUED":
                queued.set()

        monkeypatch.setattr(loop, "time", lambda: real_time() + elapsed)
        monkeypatch.setattr(sdk_execution.asyncio, "timeout_at", capture_deadline)
        current = replace(
            dependencies,
            activity_log=CallbackActivityLog(record_activity),
        )
        await dependencies.agent_slots.acquire()
        pending = asyncio.create_task(_initial(current))
        await asyncio.wait_for(queued.wait(), timeout=5)
        assert not pending.done()
        assert model.calls == ()
        assert deadlines == []
        # Advance the loop clock past an entire segment budget while admission
        # is blocked, then let the real deadline be created after admission.
        elapsed = dependencies.settings.agent_segment_timeout_seconds + 1
        admitted_at = loop.time()
        dependencies.agent_slots.release()
        result = await pending
        assert len(deadlines) == 1
        assert deadlines[0] >= admitted_at + dependencies.settings.agent_segment_timeout_seconds
        return result

    assert json.loads(asyncio.run(exercise()).result.final_output)["conclusion"]["text"] == "done"


def test_external_cancellation_is_not_translated_and_releases_admission(kernel_settings):
    started = asyncio.Event()

    async def block(_call):
        started.set()
        await asyncio.Event().wait()

    application = _application(kernel_settings, ScriptedModel([ModelStep.respond(block)]))
    dependencies = replace(
        application.agent_runtime.sdk_execution, agent_slots=asyncio.Semaphore(1)
    )

    async def exercise():
        pending = asyncio.create_task(_initial(dependencies))
        await asyncio.wait_for(started.wait(), timeout=1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        async with asyncio.timeout(1):
            await dependencies.agent_slots.acquire()
        dependencies.agent_slots.release()

    asyncio.run(exercise())


@pytest.mark.parametrize("provider", ["deepseek", "minimax"])
def test_provider_model_settings_leave_retries_to_persisted_runtime(kernel_settings, provider):
    settings = replace(
        kernel_settings(), agent_provider=provider,
        agent_protocol="chat_completions" if provider == "minimax" else "responses",
    )
    model_settings = build_model_settings(settings)
    assert isinstance(model_settings, ModelSettings)
    assert model_settings.timeout == 120
    assert model_settings.retry.max_retries == 0
    assert model_settings.retry.policy is protocol_retry_policy
    assert model_settings.max_tokens == settings.agent_max_output_tokens


def test_deepseek_flash_forces_one_tool_call_per_response(kernel_settings):
    settings = replace(
        kernel_settings(),
        agent_provider="deepseek",
        agent_protocol="responses",
        agent_model="deepseek-flash",
    )

    model_settings = build_model_settings(settings)

    assert model_settings.tool_choice == "required"
    assert model_settings.parallel_tool_calls is False


def test_large_model_schema_keeps_user_overrides_and_constraints(
    kernel_settings,
):
    base = kernel_settings("summary").models[0]
    required = {f"input_{index}": {"type": "string"} for index in range(19)}
    defaulted = {f"parameter_{index}": {"type": "integer"} for index in range(132)}
    definition = replace(
        base,
        input_schema={
            "type": "object",
            "properties": {**required, **defaulted},
            "required": list(required),
            "additionalProperties": False,
        },
        default_parameters={name: index for index, name in enumerate(defaulted)},
        data_input_parameters=tuple(required),
    )

    projected = model_tool.provider_model_input_schema(definition)

    assert projected == definition.input_schema
    supplied = {**{name: "version" for name in required}, "parameter_0": 999}
    # Explicit values remain legal at the Provider boundary, rather than being
    # silently replaced by Kernel defaults in the confirmation.
    from jsonschema import validate
    validate(supplied, projected)
    assert {**definition.default_parameters, **supplied}["parameter_0"] == 999


def test_small_model_schema_keeps_optional_overrides(kernel_settings):
    definition = kernel_settings("summary").models[0]

    projected = model_tool.provider_model_input_schema(definition)

    assert projected["properties"]["axis"]["description"] == (
        'Kernel default: "columns". Omit this property unless the user or '
        "current evidence explicitly overrides that default."
    )
    assert "axis" in projected["properties"]


def test_mixed_sdk_segments_share_eight_slots_without_nested_compactor_admission(
    kernel_settings,
):
    def inspect_data(_request):
        return httpx.Response(200, json={"data": {
            "status": "OK", "versionId": "a",
            "datasetId": "dataset-a", "semanticId": "semantics-a",
            "objects": [{"objectId": "object-a", "role": "raw",
                "mediaType": "text/csv", "format": "csv", "size": 8,
                    "checksum": "sha256:" + "a" * 64}],
        }})

    application = _application(
        kernel_settings, ScriptedModel([]), capabilities="summary",
        observation_handler=inspect_data, policy="EXECUTION_PROPOSAL",
        window=5000,
    )
    shared = application.agent_runtime.sdk_execution
    release = asyncio.Event()
    eight_active = asyncio.Event()
    all_entered = asyncio.Event()
    active = 0
    peak = 0
    entered = 0
    phases = []
    kinds = ("initial", "compaction", "reject", "execution") * 3
    models = []

    def blocked_response(phase):
        async def respond(_call):
            nonlocal active, peak
            if phase == "compaction":
                assert _call.tools == []
            active += 1
            peak = max(peak, active)
            phases.append(phase)
            if active == 8:
                eight_active.set()
            try:
                await release.wait()
                return ModelStep(output=[
                    _summary() if phase == "compaction"
                    else _execution_final() if phase in {"reject", "execution"}
                    else _final()
                ])
            finally:
                active -= 1

        return ModelStep.respond(respond)

    async def exercise():
        nonlocal entered
        jobs = []
        for index, kind in enumerate(kinds):
            run_id = f"mixed-{index}"
            session_id = application.interaction.create_session().session.session_id
            call_id = f"execution-{index}"
            state_json = None
            if kind in {"reject", "execution"}:
                preparation = ScriptedModel(
                    [
                        ModelStep(output=[function_call(
                            "inspect_data_version", {"version_id": "a"},
                            call_id=f"inspect-{index}",
                        )]),
                        ModelStep(
                            output=[
                                function_call(
                                    "record_plan",
                                    {
                                        "goal": "execute",
                                        "steps": [{"step_id": "step", "tool": "run_summary", "purpose": "execute"}],
                                        "reason": "requested",
                                    },
                                    call_id=f"plan-{index}",
                                )
                            ]
                        ),
                        ModelStep(output=[function_call("run_summary", {"resource_id": "a"}, call_id=call_id)]),
                    ]
                )
                application.agent_runtime.sdk_execution = replace(shared, model=preparation)
                waiting = await application.agent_runtime.start(
                    session_id, "project", 10, "query", (), agent_run_id=run_id,
                    dialogue_policy="EXECUTION_PROPOSAL",
                )
                assert waiting.status == "WAITING_CONFIRMATION"
                with connect_database(application.database) as connection:
                    state_json = connection.execute(
                        "SELECT state_json FROM agent_run_state WHERE agent_run_id = ?",
                        (run_id,),
                    ).fetchone()[0]
                if kind == "execution":
                    application.agent_runtime.confirm(run_id, "project")
                    with connect_database(application.database) as connection:
                        connection.execute(
                            "UPDATE workflow_runs SET status = 'SUCCEEDED' WHERE agent_run_id = ?",
                            (run_id,),
                        )
            model = ScriptedModel(
                [blocked_response(kind), blocked_response("compaction-main")]
                if kind == "compaction"
                else [blocked_response(kind)]
            )
            models.append(model)
            jobs.append((kind, run_id, session_id, call_id, state_json, replace(shared, model=model)))

        async def run_segment(job):
            nonlocal entered
            kind, run_id, session_id, call_id, state_json, dependencies = job
            entered += 1
            if entered == len(jobs):
                all_entered.set()
            common = {
                "session_id": session_id,
                "project_id": "project",
                "agent_run_id": run_id,
                "goal_revision": 10,
                "trace_id": "trace_" + f"{int(run_id.removeprefix('mixed-')):032x}",
                "execution_context": ExecutionContextV1(()),
            }
            if kind in {"reject", "execution"}:
                with connect_database(application.database) as connection:
                    connection.execute("UPDATE agent_runs SET status='RUNNING' WHERE agent_run_id=?", (run_id,))
                return await sdk_execution.execute_resumed(
                    dependencies,
                    **common,
                    state_json=state_json,
                    action_statuses={call_id: "COMPLETE"} if kind == "execution" else {},
                    approve=kind == "execution",
                    reason=None if kind == "execution" else "declined",
                )
            def on_admitted():
                create_run(
                    application.database, agent_run_id=run_id,
                    session_id=session_id, project_id="project",
                    goal_revision=10, provider="deepseek", model="test",
                    trace_id=common["trace_id"], started_at=utc_now(),
                )

                ensure_dialogue_request(
                    dependencies.database, run_id, "READ_ONLY"
                )
            return await sdk_execution.execute_initial(
                dependencies,
                **common,
                history=_history() if kind == "compaction" else (),
                message="query",
                memory=None,
                on_admitted=on_admitted,
            )

        tasks = [asyncio.create_task(run_segment(job)) for job in jobs]
        try:
            async with asyncio.timeout(2):
                await all_entered.wait()
                await eight_active.wait()
            await asyncio.sleep(0)
            assert active == peak == 8
            assert set(phases) == {"initial", "compaction", "reject", "execution"}
            assert sum(len(model.calls) for model in models) == 8
        finally:
            release.set()
            results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)
        assert all(
            (
                json.loads(result.result.final_output)["conclusion"] is None
                and json.loads(result.result.final_output)["include_execution_status"]
            )
            if kind in {"reject", "execution"}
            else json.loads(result.result.final_output)["conclusion"]["text"] == "done"
            for result, (kind, *_rest) in zip(results, jobs, strict=True)
        )
        assert peak == 8
        assert len(phases) == 15
        assert phases.count("compaction-main") == 3

    asyncio.run(exercise())


@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("repair_succeeds", [False, True])
@pytest.mark.parametrize("mixed_batch", [False, True])
def test_unknown_tool_repairs_once_without_replaying_completed_observations(
    kernel_settings, streamed, repair_succeeds, mixed_batch,
):
    observed = []

    def observe(request):
        observed.append(request.url.path)
        return httpx.Response(200, json={"data": [{"text": "observed"}]})

    missing = function_call("missing_tool", {}, call_id="unknown-first")
    model = ScriptedModel([
        ModelStep(output=[function_call("retrieve_evidence", {"query": "x"}, call_id="read")]),
        ModelStep(output=[missing, *(
            [function_call("retrieve_evidence", {"query": "another"}, call_id="mixed")]
            if mixed_batch else []
        )]),
        ModelStep(output=[_final() if repair_succeeds else function_call(
            "missing_again", {}, call_id="unknown-second",
        )]),
    ])
    app = _application(kernel_settings, model, observation_handler=observe, window=20_000)
    if streamed:
        app.agent_runtime.sdk_execution = replace(
            app.agent_runtime.sdk_execution, publish_preview=lambda *_args: None,
        )
    if repair_succeeds and not mixed_batch:
        assert asyncio.run(_start(app)).status == "COMPLETE"
    else:
        with pytest.raises(AgentRunError, match="AGENT_MODEL_OUTPUT_INVALID"):
            asyncio.run(_start(app))
        assert app.agent_runtime.get("run").status == "FAILED"
    assert len(observed) == 1
    assert len(model.calls) == (2 if mixed_batch else 3)
    with connect_database(app.database) as connection:
        attempts = [tuple(row) for row in connection.execute(
            "SELECT stage,attempt,outcome,error_class FROM provider_requests "
            "WHERE agent_run_id='run' ORDER BY created_at,rowid"
        )]
    assert attempts[:2] == [
        ("AGENT_INITIAL:1", 1, "SUCCEEDED", None),
        ("AGENT_INITIAL:2", 1, "FAILED", "PROTOCOL_OR_SCHEMA_INVALID"),
    ]
    if not mixed_batch:
        assert attempts[2] == (
            "AGENT_INITIAL:2", 2,
            "SUCCEEDED" if repair_succeeds else "FAILED",
            None if repair_succeeds else "PROTOCOL_OR_SCHEMA_INVALID",
        )
        assert "missing_tool" in json.dumps(model.calls[-1].input)


@pytest.mark.parametrize("provider,protocol", [
    ("deepseek", "responses"),
    ("minimax", "chat_completions"),
])
@pytest.mark.parametrize("second_succeeds", [False, True])
def test_missing_tool_call_gets_one_persisted_protocol_repair(
    kernel_settings, second_succeeds, provider, protocol,
):
    model = ScriptedModel([
        ModelStep(output=[assistant_message("I should have called a tool.")]),
        ModelStep(output=[
            _final() if second_succeeds else assistant_message("Still no tool.")
        ]),
    ])
    application = _application(
        kernel_settings,
        model,
        settings_changes={
            "agent_provider": provider,
            "agent_protocol": protocol,
        },
    )

    assert application.agent_runtime.sdk_execution.settings.agent_provider == provider
    if second_succeeds:
        assert asyncio.run(_start(application)).status == "COMPLETE"
    else:
        with pytest.raises(AgentRunError, match="AGENT_MODEL_OUTPUT_INVALID"):
            asyncio.run(_start(application))
    assert len(model.calls) == 2
    assert "protocol_repair_required" in json.dumps(model.calls[1].input)
    with connect_database(application.database) as connection:
        attempts = [tuple(row) for row in connection.execute(
            "SELECT stage,attempt,outcome,error_class FROM provider_requests "
            "WHERE agent_run_id='run' ORDER BY created_at,rowid"
        )]
    assert attempts == [
        (
            "AGENT_INITIAL:1", 1, "FAILED",
            "PROTOCOL_OR_SCHEMA_INVALID",
        ),
        (
            "AGENT_INITIAL:1", 2,
            "SUCCEEDED" if second_succeeds else "FAILED",
            None if second_succeeds else "PROTOCOL_OR_SCHEMA_INVALID",
        ),
    ]


def test_empty_provider_response_gets_one_protocol_repair(kernel_settings):
    model = ScriptedModel([
        ModelStep(output=[]),
        ModelStep(output=[_final()]),
    ])
    application = _application(kernel_settings, model)

    assert asyncio.run(_start(application)).status == "COMPLETE"
    assert len(model.calls) == 2
    assert "protocol_repair_required" in json.dumps(model.calls[-1].input)
    with connect_database(application.database) as connection:
        attempts = [tuple(row) for row in connection.execute(
            "SELECT attempt,outcome,error_class FROM provider_requests "
            "WHERE agent_run_id='run' ORDER BY created_at,rowid"
        )]
    assert attempts == [
        (1, "FAILED", "PROTOCOL_OR_SCHEMA_INVALID"),
        (2, "SUCCEEDED", None),
    ]


def test_early_sdk_model_behavior_error_gets_one_protocol_repair(
    kernel_settings,
):
    model = ScriptedModel([
        ModelStep(error=ModelBehaviorError("malformed provider tool output")),
        ModelStep(output=[_final()]),
    ])
    application = _application(kernel_settings, model)

    assert asyncio.run(_start(application)).status == "COMPLETE"
    assert len(model.calls) == 2
    assert "previous response could not be decoded" in json.dumps(
        model.calls[-1].input
    )
    with connect_database(application.database) as connection:
        attempts = [tuple(row) for row in connection.execute(
            "SELECT attempt,outcome,error_class FROM provider_requests "
            "WHERE agent_run_id='run' ORDER BY created_at,rowid"
        )]
    assert attempts == [
        (1, "FAILED", "PROTOCOL_OR_SCHEMA_INVALID"),
        (2, "SUCCEEDED", None),
    ]


def test_minimax_multiple_tool_batch_is_rejected_then_repaired_once(
    kernel_settings,
):
    observed = []

    def observe(request):
        observed.append(request.url.path)
        return httpx.Response(200, json={"data": []})

    model = ScriptedModel([
        ModelStep(output=[
            function_call("retrieve_evidence", {"query": "x"}, call_id="read"),
            _final(),
        ]),
        ModelStep(output=[_final()]),
    ])
    application = _application(
        kernel_settings,
        model,
        observation_handler=observe,
        settings_changes={
            "agent_provider": "minimax",
            "agent_protocol": "chat_completions",
        },
    )

    assert asyncio.run(_start(application)).status == "COMPLETE"
    assert observed == []
    assert len(model.calls) == 2
    assert "multiple tool calls while parallel calls are disabled" in json.dumps(
        model.calls[1].input
    )
    with connect_database(application.database) as connection:
        attempts = [tuple(row) for row in connection.execute(
            "SELECT stage,attempt,outcome,error_class FROM provider_requests "
            "WHERE agent_run_id='run' ORDER BY created_at,rowid"
        )]
    assert attempts == [
        ("AGENT_INITIAL:1", 1, "FAILED", "PROTOCOL_OR_SCHEMA_INVALID"),
        ("AGENT_INITIAL:1", 2, "SUCCEEDED", None),
    ]


def test_minimax_wrong_finalization_tool_is_rejected_then_repaired_once(
    kernel_settings,
):
    observed = []

    def observe(request):
        observed.append(request.url.path)
        return httpx.Response(200, json={"data": []})

    model = ScriptedModel([
        *[
            ModelStep(output=[function_call(
                "retrieve_evidence", {"query": f"page {index}"},
                call_id=f"read-{index}",
            )])
            for index in range(7)
        ],
        ModelStep(output=[function_call(
            "retrieve_evidence", {"query": "must-not-run"},
            call_id="wrong-final-tool",
        )]),
        ModelStep(output=[_final()]),
    ])
    application = _application(
        kernel_settings,
        model,
        observation_handler=observe,
        window=131072,
        settings_changes={
            "agent_provider": "minimax",
            "agent_protocol": "chat_completions",
        },
    )

    assert asyncio.run(_start(application)).status == "COMPLETE"
    assert len(observed) == 7
    assert len(model.calls) == 9
    assert "finalization response did not call submit_reply exactly once" in json.dumps(
        model.calls[-1].input
    )
    assert model.calls[-1].model_settings.tool_choice == "submit_reply"
    assert model.calls[-1].model_settings.parallel_tool_calls is False
    with connect_database(application.database) as connection:
        attempts = [tuple(row) for row in connection.execute(
            "SELECT attempt,outcome,error_class FROM provider_requests "
            "WHERE agent_run_id='run' ORDER BY created_at,rowid"
        )]
    assert attempts[-2:] == [
        (1, "FAILED", "PROTOCOL_OR_SCHEMA_INVALID"),
        (2, "SUCCEEDED", None),
    ]


def test_minimax_missing_tool_retry_names_single_planned_approval_tool(
    kernel_settings, monkeypatch,
):
    model = ScriptedModel([
        ModelStep(output=[assistant_message("I should have called the model tool.")]),
        ModelStep(output=[assistant_message("Still no tool.")]),
    ])
    application = _application(
        kernel_settings,
        model,
        capabilities="summary",
        policy="EXECUTION_PROPOSAL",
        settings_changes={
            "agent_provider": "minimax",
            "agent_protocol": "chat_completions",
        },
    )
    monkeypatch.setattr(
        usage_ports,
        "single_planned_approval_tool",
        lambda *_args: "run_summary",
    )

    with pytest.raises(AgentRunError, match="AGENT_MODEL_OUTPUT_INVALID"):
        asyncio.run(_start(application))

    assert model.calls[0].model_settings.tool_choice == "required"
    assert model.calls[1].model_settings.tool_choice == "run_summary"
    assert "Call run_summary" in json.dumps(model.calls[1].input)


@pytest.mark.parametrize(
    ("provider", "protocol"),
    (("minimax", "chat_completions"), ("deepseek", "responses")),
)
def test_execution_missing_tool_retry_names_input_preparation(
    kernel_settings, provider, protocol,
):
    model = ScriptedModel([
        ModelStep(output=[assistant_message("I should prepare exact inputs.")]),
        ModelStep(output=[assistant_message("Still no tool.")]),
    ])
    application = _application(
        kernel_settings,
        model,
        capabilities="summary",
        policy="EXECUTION_PROPOSAL",
        settings_changes={
            "agent_provider": provider,
            "agent_protocol": protocol,
        },
    )

    with pytest.raises(AgentRunError, match="AGENT_MODEL_OUTPUT_INVALID"):
        asyncio.run(_start(application))

    assert model.calls[0].model_settings.tool_choice == (
        "required" if provider == "minimax" else None
    )
    assert model.calls[1].model_settings.tool_choice == "prepare_task_inputs"
    assert model.calls[1].model_settings.parallel_tool_calls is False
    assert "Call prepare_task_inputs" in json.dumps(model.calls[1].input)


def test_minimax_recorded_single_step_plan_names_approval_tool(
    kernel_settings, monkeypatch,
):
    model = ScriptedModel([
        ModelStep(output=[function_call("record_plan", {
            "goal": "Summarize the selected version",
            "steps": [{
                "step_id": "summary",
                "tool": "run_summary",
                "purpose": "Produce the requested summary",
            }],
            "reason": "The exact input is already selected",
        }, call_id="plan")]),
        ModelStep(output=[function_call(
            "run_summary", {"versionId": "a"}, call_id="proposal",
        )]),
    ])
    application = _application(
        kernel_settings,
        model,
        capabilities="summary",
        policy="EXECUTION_PROPOSAL",
        settings_changes={
            "agent_provider": "minimax",
            "agent_protocol": "chat_completions",
        },
    )
    monkeypatch.setattr(
        usage_ports,
        "single_planned_approval_tool",
        lambda *_args: "run_summary",
    )

    with pytest.raises(AgentRunError, match="AGENT_MODEL_OUTPUT_INVALID"):
        asyncio.run(_start(application))

    assert model.calls[0].model_settings.tool_choice == "required"
    assert model.calls[1].model_settings.tool_choice == "run_summary"
    assert model.calls[1].model_settings.parallel_tool_calls is False


@pytest.mark.parametrize(
    ("provider", "protocol"),
    (("minimax", "chat_completions"), ("deepseek", "responses")),
)
def test_provider_ready_preparation_names_record_plan_for_next_request(
    kernel_settings, provider, protocol,
):
    model = ScriptedModel([
        ModelStep(output=[function_call("prepare_task_inputs", {
            "target_tool": "run_summary",
            "arguments": {"resource_id": "resource-a"},
        }, call_id="prepare")]),
        ModelStep(output=[assistant_message("I should record the plan.")]),
        ModelStep(output=[assistant_message("Still no tool.")]),
    ])

    def inspect_version(request):
        return httpx.Response(200, request=request, json={"data": {
            "status": "OK",
            "versionId": "resource-a",
            "datasetId": "dataset-a",
            "semanticId": "semantic-a",
            "objects": [{
                "objectId": "object-a",
                "role": "raw",
                "mediaType": "text/csv",
                "format": "csv",
                "size": 8,
                "checksum": "sha256:" + "a" * 64,
            }],
        }})

    application = _application(
        kernel_settings,
        model,
        capabilities="summary",
        policy="EXECUTION_PROPOSAL",
        observation_handler=inspect_version,
        window=131072,
        settings_changes={
            "agent_provider": provider,
            "agent_protocol": protocol,
        },
    )

    with pytest.raises(AgentRunError, match="AGENT_MODEL_OUTPUT_INVALID"):
        asyncio.run(_start(application))

    assert model.calls[1].model_settings.tool_choice == "record_plan"
    assert model.calls[1].model_settings.parallel_tool_calls is False
