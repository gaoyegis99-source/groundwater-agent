from __future__ import annotations

import asyncio
import importlib.util
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import httpx2
import pytest
from agents import FunctionTool, ModelSettings
from agents.models.interface import ModelTracing
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI
from openai.types.shared import Reasoning
from platform_kernel.agent_runtime.models import SubmittedReplyV6
from scripts.checks.deepseek_data_knowledge_live import _disable_sdk_trace_export


def test_live_provider_smoke_installs_no_export_trace_provider() -> None:
    provider = _disable_sdk_trace_export()

    assert provider.create_trace("disabled-live-provider-smoke").export() is None


@pytest.fixture
def fixed_provider(monkeypatch):
    directory = Path(__file__).resolve().parents[1] / "e2e/compose-stub"
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location("fixed_provider_wire", directory / "stub_server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module




def _tool(name):
    async def never_invoke(context, arguments):
        raise AssertionError("wire probe does not execute tools")

    return FunctionTool(
        name=name, description=name,
        params_json_schema={"type": "object", "properties": {}, "additionalProperties": False},
        on_invoke_tool=never_invoke,
    )


def _append_result(items, name, value):
    call_id = f"call-{len(items)}"
    items.extend([
        {"type": "function_call", "call_id": call_id, "name": name, "arguments": "{}"},
        {"type": "function_call_output", "call_id": call_id,
         "output": value if isinstance(value, str) else json.dumps(value)},
    ])


@pytest.mark.parametrize(
    "message,expected",
    [
        ("请执行描述统计", "EXECUTE_ON_DATA|NONE|NONE"),
        ("请生成工作流草稿", "CREATE_WORKFLOW|NONE|NONE"),
        ("请比较分析方法", "CONSULT|NONE|NONE"),
        ("帮我处理一下", "UNKNOWN|NONE|REQUEST_KIND_AMBIGUOUS"),
    ],
)
def test_fixed_deepseek_router_uses_v11_semantic_literal(
    fixed_provider, message, expected
):
    action = fixed_provider.provider_action(
        {
            "model": "test-router-model",
            "input": [{"role": "user", "content": message}],
            "tools": [],
        },
        chat=False,
    )
    assert action == (None, expected)


def test_v11_multi_input_execution_inspects_versions_in_one_logical_stage(
    fixed_provider,
):
    request = {
        "tool": "run_transfer_learning_analysis",
        "arguments": {
            "sourceVersionId": "version-source",
            "targetVersionId": "version-target",
        },
    }
    name, calls = fixed_provider.provider_action(
        {
            "input": [{
                "role": "user",
                "content": "[Evaluation case: multi-input]\\n[Model execution] "
                + json.dumps(request),
            }],
            "tools": [
                {"name": item}
                for item in (
                    "inspect_data_version", "inspect_data_table", "record_plan",
                    "run_transfer_learning_analysis", "submit_reply",
                )
            ],
        },
        chat=False,
    )
    assert name == "__batch__"
    assert calls == [
        ("inspect_data_version", {"version_id": "version-source"}),
        ("inspect_data_version", {"version_id": "version-target"}),
    ]


@pytest.mark.parametrize("chat", [False, True])
def test_fixed_provider_authors_progress_intent_from_tool_contract(
    fixed_provider,
    chat,
):
    request = {
        "tool": "run_transfer_learning_analysis",
        "arguments": {
            "sourceVersionId": "version-source",
            "targetVersionId": "version-target",
        },
    }
    schema = {
        "type": "object",
        "properties": {
            "version_id": {"type": "string"},
            "progress_intent": {
                "type": "object",
                "properties": {
                    "capability": {
                        "type": "string",
                        "const": "data.version.inspect",
                    },
                    "objective": {"type": "string"},
                    "template_key": {
                        "type": "string",
                        "const": "data.version.inspect",
                    },
                },
                "required": ["capability", "objective", "template_key"],
            },
        },
        "required": ["version_id", "progress_intent"],
    }
    names = (
        "inspect_data_version",
        "record_plan",
        "run_transfer_learning_analysis",
        "submit_reply",
    )
    tools = [
        (
            {
                "type": "function",
                "function": {
                    "name": name,
                    "parameters": schema if name == "inspect_data_version" else {},
                },
            }
            if chat
            else {
                "type": "function",
                "name": name,
                "parameters": schema if name == "inspect_data_version" else {},
            }
        )
        for name in names
    ]
    input_items = [{
        "role": "user",
        "content": "[Model execution] " + json.dumps(request),
    }]
    body = {
        ("messages" if chat else "input"): input_items,
        "tools": tools,
    }

    name, calls = fixed_provider.provider_action(body, chat=chat)

    assert name == "__batch__"
    assert [call[1]["version_id"] for call in calls] == [
        "version-source",
        "version-target",
    ]
    assert all(
        call[1]["progress_intent"] == {
            "capability": "data.version.inspect",
            "objective": "核验指定输入版本的对象、语义和 lineage",
            "template_key": "data.version.inspect",
        }
        for call in calls
    )


def test_v11_ambiguity_selection_uses_top_level_dataset_description(
    fixed_provider,
):
    items = [{
        "role": "user",
        "content": "请选择描述为正式采样的北区监测样本-ambiguity-1。",
    }]
    _append_result(items, "discover_datasets", {"data": {
        "items": [
            {
                "datasetId": "dataset-draft",
                "title": "北区监测样本-ambiguity-1",
                "description": "临时采样",
                "currentVersion": "version-draft",
            },
            {
                "datasetId": "dataset-official",
                "title": "北区监测样本-ambiguity-1",
                "description": "正式采样",
                "currentVersion": "version-official",
            },
        ],
        "nextCursor": None,
    }})

    name, arguments = fixed_provider.provider_action(
        {
            "input": items,
            "tools": [{"name": tool_name} for tool_name in {
                "discover_datasets",
                "inspect_data_version",
                "inspect_data_table",
                "record_plan",
                "run_summary",
                "submit_reply",
            }],
        },
        chat=False,
    )

    assert name == "inspect_data_version"
    assert arguments == {"version_id": "version-official"}


def test_yaozhanbao_natural_language_discovers_confirms_and_explains_results(
    fixed_provider,
):
    items = [{
        "role": "user",
        "content": "请运行腰站堡迁移学习开发案例，优先使用系统中的标准化资料。",
    }]
    tool_names = {
        "discover_datasets",
        "inspect_data_version",
        "record_plan",
        "run_transfer_learning_analysis",
        "submit_reply",
    }

    def action():
        return fixed_provider.provider_action(
            {
                "input": items,
                "tools": [{"name": name} for name in tool_names],
            },
            chat=False,
        )

    name, arguments = action()
    assert name == "discover_datasets"
    assert arguments == {
        "query": "迁移输入",
        "tags": [],
        "after": None,
        "limit": 50,
    }

    datasets = []
    for index, logical_name in enumerate(
        fixed_provider.YAOZHANBAO_INPUT_BINDINGS
    ):
        datasets.append({
            "title": fixed_provider.YAOZHANBAO_STANDARD_DATASET_PREFIX + logical_name,
            "currentVersion": f"version-{logical_name}-new",
            "createdAt": f"2026-10-13T00:00:{index:02d}Z",
            "tags": ["标准化资料", "yaozhanbao-transfer-case"],
        })
    datasets.append({
        "title": fixed_provider.YAOZHANBAO_STANDARD_DATASET_PREFIX + "workbook",
        "currentVersion": "version-workbook-old",
        "createdAt": "2026-10-12T00:00:00Z",
        "tags": ["标准化资料", "yaozhanbao-transfer-case"],
    })
    _append_result(items, name, {"data": {"items": datasets, "nextCursor": None}})

    name, inspections = action()
    assert name == "__batch__"
    expected_versions = {
        f"version-{logical_name}-new"
        for logical_name in fixed_provider.YAOZHANBAO_INPUT_BINDINGS
    }
    assert {call[1]["version_id"] for call in inspections} == expected_versions
    assert "version-workbook-old" not in expected_versions
    for inspection_name, inspection_arguments in inspections:
        _append_result(items, inspection_name, {"data": {
            "status": "OK",
            "versionId": inspection_arguments["version_id"],
            "objects": [{"format": "json"}],
        }})

    name, plan = action()
    assert name == "record_plan"
    assert plan["steps"][0]["tool"] == "run_transfer_learning_analysis"
    assert "标准化案例输入" in plan["reason"]
    _append_result(items, name, {"data": {"revision": 1}})

    name, model_arguments = action()
    assert name == "run_transfer_learning_analysis"
    assert model_arguments["workbookVersionId"] == "version-workbook-new"
    assert model_arguments["pretrain.epochs"] == 2
    assert model_arguments["finetune.epochs"] == 2
    assert model_arguments["prediction-points.sourceCrs"] == "EPSG:4490"
    assert model_arguments["map-points.sourceCrs"] == "EPSG:4490"
    assert model_arguments["extent.paddingMeters"] == 1000
    assert model_arguments["map-month.targetTime"] == "2024-12-01"
    _append_result(items, name, {
        "status": "COMPLETE",
        "workflow_run_id": "workflow-run-yaozhanbao",
        "outputs": [{"kind": "data_version"} for _ in range(25)],
    })

    name, reply = action()
    assert name == "submit_reply"
    submitted = SubmittedReplyV6.model_validate(reply)
    assert submitted.include_execution_status
    assert submitted.referenced_workflow_runs == ["workflow-run-yaozhanbao"]
    assert "25 项" in submitted.details[1].text
    assert "历史归档和标准化处理" in submitted.details[2].text


def test_yaozhanbao_search_finishes_standardized_pages_before_synthetic_fallback(
    fixed_provider,
):
    items = [{"role": "user", "content": "请运行腰站堡迁移学习案例"}]
    tool_names = {
        "discover_datasets", "inspect_data_version", "record_plan",
        "run_transfer_learning_analysis", "submit_reply",
    }

    def action():
        return fixed_provider.provider_action(
            {"input": items, "tools": [{"name": name} for name in tool_names]},
            chat=False,
        )

    name, arguments = action()
    assert name == "discover_datasets"
    logical_names = list(fixed_provider.YAOZHANBAO_INPUT_BINDINGS)
    first_page = [
        {
            "title": fixed_provider.YAOZHANBAO_SYNTHETIC_DATASET_PREFIX + logical_name,
            "currentVersion": "synthetic-" + logical_name,
            "createdAt": "2026-10-13T00:00:00Z",
            "tags": ["SYNTHETIC_TEST_ONLY"],
        }
        for logical_name in logical_names
    ] + [
        {
            "title": fixed_provider.YAOZHANBAO_STANDARD_DATASET_PREFIX + logical_name,
            "currentVersion": "standard-" + logical_name,
            "createdAt": "2026-10-13T00:00:01Z",
            "tags": ["yaozhanbao-transfer-case"],
        }
        for logical_name in logical_names[:5]
    ]
    _append_result(items, name, {"data": {"items": first_page, "nextCursor": "page-2"}})

    name, arguments = action()
    assert name == "discover_datasets"
    assert arguments["after"] == "page-2"
    second_page = [
        {
            "title": fixed_provider.YAOZHANBAO_STANDARD_DATASET_PREFIX + logical_name,
            "currentVersion": "standard-" + logical_name,
            "createdAt": "2026-10-13T00:00:02Z",
            "tags": ["yaozhanbao-transfer-case"],
        }
        for logical_name in logical_names[5:]
    ]
    _append_result(items, name, {"data": {"items": second_page, "nextCursor": None}})

    name, inspections = action()
    assert name == "__batch__"
    assert {call[1]["version_id"] for call in inspections} == {
        "standard-" + logical_name for logical_name in logical_names
    }


@pytest.mark.parametrize("protocol", ["responses", "chat_completions"])
def test_real_sdk_wire_knowledge_and_submit_reply(fixed_provider, protocol, monkeypatch):
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1")
    requests = []

    class RecordingHandler(fixed_provider.Handler):
        def _responses(self, body):
            requests.append(body)
            super()._responses(body)

        def _chat(self, body):
            requests.append(body)
            super()._chat(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), RecordingHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def probe():
        base_url = f"http://127.0.0.1:{server.server_port}/v1"
        async with AsyncOpenAI(
            base_url=base_url,
            api_key="test-key",
            max_retries=0,
            http_client=httpx2.AsyncClient(trust_env=False),
        ) as client:
            model = (OpenAIResponsesModel("test-model", client) if protocol == "responses"
                     else OpenAIChatCompletionsModel("test-model", client))
            settings = ModelSettings(reasoning=Reasoning(effort="high"), max_tokens=2048)
            text = "请根据项目知识解释数据字典和方法说明"
            items = [{"role": "user", "content": text}]
            hit = {"versionId": "demo-guide-v1", "unitId": "unit-0",
                   "title": "合成项目数据字典与方法说明",
                   "checksum": "sha256:" + "b" * 64}
            content = "PRIVATE_TOOL_SENTINEL 不应复制到跨 Turn 答复正文"
            responses = [
                {"data": [hit]},
                {"data": {"status": "OK", "items": [
                    {**hit, "status": "OK", "content": content},
                ]}},
            ]
            for index, name in enumerate(("retrieve_evidence", "read_evidence", "submit_reply")):
                phase_tools = {
                    "retrieve_evidence": ("retrieve_evidence", "read_evidence", "submit_reply"),
                    "read_evidence": ("read_evidence", "submit_reply"),
                    "submit_reply": ("submit_reply",),
                }[name]
                result = await model.get_response(
                    "Test tool phase", items, settings, [_tool(tool) for tool in phase_tools], None, [], ModelTracing.DISABLED,
                    previous_response_id=None, conversation_id=None, prompt=None,
                )
                assert len(result.output) == 1
                call = result.output[0]
                assert call.type == "function_call" and call.name == name
                if name == "submit_reply":
                    submitted = SubmittedReplyV6.model_validate_json(call.arguments)
                    assert submitted.conclusion is not None
                    assert submitted.conclusion.citations[0].version_id == "demo-guide-v1"
                    assert submitted.conclusion.citations[0].unit_id == "unit-0"
                    assert submitted.conclusion.citations[0].checksum == hit["checksum"]
                    assert "PRIVATE_TOOL_SENTINEL" not in call.arguments
                    break
                items.append(call.model_dump(exclude_none=True))
                items.append({"type": "function_call_output", "call_id": call.call_id,
                              "output": json.dumps(responses[index])})
            assert fixed_provider.verify_evidence(text, {
                "kind": "demo", "observations": ["retrieve_evidence", "read_evidence"],
                "knowledge_content": content,
            }) == {"observed_inputs": "PASS", "replay_references": "PASS"}
            with pytest.raises(AssertionError):
                fixed_provider.verify_evidence(text, {
                    "kind": "demo", "observations": ["read_evidence"],
                    "knowledge_content": "incorrect body",
                })
            assert len(requests) == 3
            final = requests[-1]
            assert final.get("tools") and "response_format" not in final
            if protocol == "responses":
                assert final["reasoning"]["effort"] == "high"
                assert final["instructions"] == "Test tool phase"
            else:
                assert final["reasoning_effort"] == "high"
                assert final["messages"][0]["content"] == "Test tool phase"

    try:
        asyncio.run(probe())
    finally:
        server.shutdown()
        server.server_close()
        thread.join()














@pytest.mark.parametrize("status", ["REJECTED", "FAILED"])
@pytest.mark.parametrize("tool", ["run_summary", "run_monitoring_trend", "run_flow_field"])
def test_submission_references_authoritative_failed_or_rejected_execution(fixed_provider, status, tool):
    items = [{"role": "user", "content": "做描述统计"}]
    _append_result(items, tool, "试验拒绝执行" if status == "REJECTED" else {
        "status": status, "error": "WORKER_FAILED",
    })
    unresolved = [] if status == "REJECTED" else ["WORKER_FAILED"]
    submitted = SubmittedReplyV6.model_validate(fixed_provider._submit_reply({
        "message": "执行结束，请查看实际状态。", "workflow_runs": [], "unresolved_items": unresolved,
    }, items))
    assert submitted.include_execution_status
    assert not submitted.referenced_workflow_runs
    assert len(submitted.unresolved_items) == len(unresolved)


def test_plain_rejection_accepts_the_operator_reason_only_for_execution_tools(fixed_provider):
    assert fixed_provider._parse_tool_output("试验拒绝执行", rejection=True) == {"status": "REJECTED"}
    assert fixed_provider._parse_tool_output("参数需要调整", rejection=True) == {"status": "REJECTED"}
    for value, rejection in (("试验拒绝执行", False), ("", True), ("[]", True)):
        with pytest.raises((ValueError, SyntaxError, AssertionError)):
            fixed_provider._parse_tool_output(value, rejection=rejection)


@pytest.mark.parametrize("tool,subject", [
    ("run_monitoring_trend", "变化趋势"), ("run_flow_field", "流场"),
])
def test_groundwater_fixed_rejection_reaches_submission_without_execution(fixed_provider, tool, subject):
    items = [{"role": "user", "content": f"请按项目约定分析测区监测的{subject}。"}]
    _append_result(items, tool, "试验拒绝执行")
    action = fixed_provider.provider_action({"instructions": "tool phase", "input": items,
        "tools": [{"name": tool}, {"name": "submit_reply"}]}, chat=False)
    assert action[0] == "submit_reply"
    submitted = SubmittedReplyV6.model_validate(action[1])
    assert submitted.include_execution_status
    assert submitted.referenced_workflow_runs == []
    assert submitted.unresolved_items == []
