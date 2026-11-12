from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sqlite3

from agents.testing import ModelStep, ScriptedModel, function_call
from fastapi.testclient import TestClient
import httpx
from jsonschema import Draft202012Validator
import pytest

from kernel.tests.test_recommendations import (
    KNOWLEDGE,
    RESOURCE,
    _application,
    _dialogue,
    _drain,
    _execute,
    _inspect,
    _message,
    _read,
)
from platform_kernel.api import create_app
from platform_kernel.api.wire import to_wire
from platform_kernel.agent_runtime.models import SubmittedReplyV6
from platform_kernel.agent_runtime.tooling.reply_submission import ReplySubmission
from platform_kernel.composition import PlatformApplication
from platform_kernel.database import connect_database, read_session_events


_RAW_OBJECT = RESOURCE["objects"][0]
REFERENCE = {
    "kind": "data_version",
    "service": "data-primary",
    "dataset_id": RESOURCE["dataset_id"],
    "version_id": RESOURCE["id"],
    "semantic_id": RESOURCE["semantic_id"],
    "object_id": _RAW_OBJECT["id"],
    "media_type": _RAW_OBJECT["media_type"],
    "format": _RAW_OBJECT["format"],
    "size_bytes": _RAW_OBJECT["size"],
    "checksum": _RAW_OBJECT["checksum"],
}


def _catalog_arguments(objective: str, **arguments):
    return {
        "progress_intent": {
            "capability": "workflow.operator.discovery",
            "objective": objective,
            "template_key": "workflow.operator.discovery",
        },
        **arguments,
    }


def _draft():
    return {
        "title": "先排序再统计",
        "summary": "组合两个已安装算子，排序规则文件尚未提供。",
        "graph": {
            "inputs": [
                {
                    "name": "source",
                    "cardinality": "one",
                    "mediaTypes": ["text/csv"],
                    "formats": ["csv"],
                },
                {
                    "name": "rules",
                    "cardinality": "one",
                    "mediaTypes": ["application/json"],
                    "formats": ["json"],
                },
            ],
            "parameters": ["axis"],
            "nodes": [
                {
                    "nodeId": "sort",
                    "operatorId": "table.sort",
                    "operatorVersion": "1.0.0",
                    "inputs": {
                        "data": {"workflowInput": "source"},
                        "rules": {"workflowInput": "rules"},
                    },
                    "parameters": {},
                },
                {
                    "nodeId": "describe",
                    "operatorId": "statistics.describe",
                    "operatorVersion": "1.0.0",
                    "inputs": {
                        "data": {"nodeOutput": {"nodeId": "sort", "port": "data"}}
                    },
                    "parameters": {"axis": {"workflowParameter": "axis"}},
                },
            ],
            "outputs": [
                {
                    "name": "summary",
                    "nodeOutput": {"nodeId": "describe", "port": "summary"},
                }
            ],
        },
        "data_bindings": [{"input_name": "source", "data_ref": REFERENCE}],
        "parameter_values": {"axis": "columns"},
        "parameter_sources": [
            {
                "name": "axis",
                "source": "OPERATOR_DEFAULT",
                "explanation": "采用算子默认值。",
            }
        ],
        "knowledge_refs": [KNOWLEDGE],
        "unresolved_items": ["需要排序规则文件。"],
        "limitations": ["仅草稿，未执行计算。"],
    }


def _fan_out_draft():
    draft = deepcopy(_draft())
    draft["title"] = "先整理再并行分析"
    draft["summary"] = "排序后的数据分别进入两个最终分析节点。"
    draft["graph"]["nodes"][1]["nodeId"] = "trend"
    gap = deepcopy(draft["graph"]["nodes"][1])
    gap["nodeId"] = "gaps"
    draft["graph"]["nodes"].append(gap)
    draft["graph"]["outputs"] = [
        {
            "name": "trend_summary",
            "nodeOutput": {"nodeId": "trend", "port": "summary"},
        },
        {
            "name": "gap_summary",
            "nodeOutput": {"nodeId": "gaps", "port": "summary"},
        },
    ]
    return draft


def _parallel_draft():
    draft = _fan_out_draft()
    draft["title"] = "并行统计"
    draft["summary"] = "同一时间范围的数据进入两个独立分析节点。"
    draft["graph"]["inputs"] = draft["graph"]["inputs"][:1]
    draft["graph"]["nodes"] = draft["graph"]["nodes"][1:]
    for node in draft["graph"]["nodes"]:
        node["inputs"]["data"] = {"workflowInput": "source"}
    draft["unresolved_items"] = []
    return draft


def _fan_in_draft():
    draft = deepcopy(_draft())
    draft["title"] = "双路排序后连接"
    draft["summary"] = "两路排序结果共同进入最终连接节点。"
    draft["graph"]["inputs"].append({
        "name": "join_rules",
        "cardinality": "one",
        "mediaTypes": ["application/json"],
        "formats": ["json"],
    })
    draft["graph"]["parameters"] = [
        "joinType", "cardinality", "maxOutputRows",
    ]
    left = draft["graph"]["nodes"][0]
    left["nodeId"] = "sort_left"
    right = deepcopy(left)
    right["nodeId"] = "sort_right"
    join = {
        "nodeId": "join",
        "operatorId": "table.join",
        "operatorVersion": "1.0.0",
        "inputs": {
            "left": {"nodeOutput": {"nodeId": "sort_left", "port": "data"}},
            "right": {"nodeOutput": {"nodeId": "sort_right", "port": "data"}},
            "rules": {"workflowInput": "join_rules"},
        },
        "parameters": {
            "joinType": {"workflowParameter": "joinType"},
            "cardinality": {"workflowParameter": "cardinality"},
            "maxOutputRows": {"workflowParameter": "maxOutputRows"},
        },
    }
    draft["graph"]["nodes"] = [left, right, join]
    draft["graph"]["outputs"] = [{
        "name": "joined_summary",
        "nodeOutput": {"nodeId": "join", "port": "summary"},
    }]
    draft["parameter_values"] = {
        "joinType": "left",
        "cardinality": "oneToOne",
        "maxOutputRows": 1000,
    }
    draft["parameter_sources"] = [
        {
            "name": name,
            "source": "USER",
            "explanation": "采用用户指定的连接设置。",
        }
        for name in draft["parameter_values"]
    ]
    draft["unresolved_items"] = ["需要排序规则文件和连接规则文件。"]
    return draft


def _operator(operator_id, *, call_id=None):
    return ModelStep(
        output=[
            function_call(
                "get_operator",
                {
                    "progress_intent": {
                        "capability": "workflow.operator.inspect",
                        "objective": f"核验 {operator_id} 算子的输入输出契约",
                        "template_key": "workflow.operator.inspect",
                    },
                    "operator_id": operator_id,
                    "version": "1.0.0",
                },
                call_id=call_id or operator_id,
            )
        ]
    )


def _reply(draft, *, cited=True, unresolved=False, call_id="reply"):
    citation = [KNOWLEDGE] if cited else []
    return ModelStep(
        output=[
            function_call(
                "submit_reply",
                {
                    "conclusion": {
                        "text": "草稿已整理。" if draft else "当前算子不能表达此需求。",
                        "citations": citation,
                    },
                    "details": [],
                    "unresolved_items": [
                        {"text": "需要当前目录没有的能力。", "citations": citation}
                    ]
                    if unresolved
                    else [],
                    "referenced_workflow_runs": [],
                    "include_execution_status": False,
                    "interaction_options": [],
                    "workflow_draft": draft,
                },
                call_id=call_id,
            )
        ]
    )


def _steps(draft=None):
    return [
        _operator("table.sort"),
        _operator("statistics.describe"),
        _read(),
        _inspect(),
        _reply(draft or _draft()),
    ]


def _app(kernel_settings, steps):
    app, _, model = _application(kernel_settings, steps)
    return app, TestClient(create_app(application=app)), model


def _create(
    client, *, session_id=None, request_id="draft-request", message="创建排序后统计草稿"
):
    if session_id is None:
        session_id = client.post("/api/v12/sessions", json={"projectId": "p"}).json()[
            "sessionId"
        ]
    response = _message(
        client,
        session_id,
        message,
        requested_mode="WORKFLOW_DRAFT",
        request_id=request_id,
    )
    assert response.status_code == 202, response.text
    base = f"/api/v12/sessions/{session_id}/workflow-drafts"
    path = f"{base}/{response.json()['workflowDraftId']}"
    return session_id, path, client.get(path).json()


def _no_execution(app):
    with connect_database(app.database) as connection:
        for table in ("confirmations", "agent_actions", "workflow_runs", "agent_plans"):
            assert (
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
            )


@pytest.mark.parametrize(
    ("execution_status", "reason"),
    [
        (False, "REPLY_CONCLUSION_REQUIRED"),
        (True, "REPLY_EXECUTION_STATUS_INVALID"),
    ],
)
def test_draft_requires_conclusion_and_cannot_mix_with_execution_status(
    kernel_settings,
    execution_status,
    reason,
):
    arguments = json.loads(_reply(_draft()).output[0].arguments)
    arguments["conclusion"] = None
    arguments["include_execution_status"] = execution_status
    reply = ModelStep(
        output=[
            function_call(
                "submit_reply",
                arguments,
                call_id="invalid-reply",
            )
        ]
    )
    app, client, _ = _app(kernel_settings, [reply])
    session_id, path, _ = _create(client)
    _drain(app, session_id)
    assert client.get(path).json()["status"] == "FAILED"
    turn = client.get(f"/api/v12/sessions/{session_id}/messages").json()["turns"][0]
    assert turn["diagnostics"] == {"reason": reason}
    assert turn["reply"] is None
    _no_execution(app)


def test_draft_freezes_new_graph_and_evidence_without_execution(kernel_settings):
    directory = ModelStep(
        output=[
            function_call(
                "list_operators",
                _catalog_arguments(
                    "查找可执行排序操作的分析算子",
                    query="sort",
                    domain=None,
                    after=None,
                    limit=20,
                ),
                call_id="catalog",
            )
        ]
    )
    app, client, model = _app(kernel_settings, [directory, *_steps()])
    sid, path, created = _create(client)
    assert created["status"] == "GENERATING" and created["payload"] is None
    assert client.get(path + "/export").status_code == 409
    assert (
        client.get(f"/api/v12/sessions/{sid}").json()["workflowDraftId"]
        == created["draftId"]
    )
    _drain(app, sid)
    resource = client.get(path).json()
    assert resource["status"] == "COMPLETE" and resource["errorCode"] is None
    payload = resource["payload"]
    assert payload["schemaVersion"] == "workflow-draft.v1"
    assert payload["workflow"]["workflowId"] == f"draft.{created['draftId']}"
    assert payload["workflow"]["version"] == "0.1.0"
    expected = to_wire(_draft())
    assert payload["dataBindings"] == expected["dataBindings"]
    assert payload["knowledgeRefs"] == expected["knowledgeRefs"]
    assert payload["missingInputs"] == [
        {
            "kind": "DATA",
            "name": "rules",
            "requiredBy": [{"nodeId": "sort", "port": "rules"}],
        }
    ]
    assert payload["executionAllowed"] is False
    assert [operator["operatorId"] for operator in payload["operators"]] == [
        "table.sort",
        "statistics.describe",
    ]
    assert all(
        operator["implementationRevision"].startswith("sha256:")
        for operator in payload["operators"]
    )
    exported = client.get(path + "/export")
    assert exported.status_code == 200 and exported.json() == payload
    assert exported.headers["content-disposition"].startswith("attachment;")
    assert all(
        {tool.name for tool in call.tools}
        == {
            "retrieve_evidence",
            "read_evidence",
            "inspect_data_version",
            "list_operators",
            "get_operator",
            "submit_reply",
            "update_task",
        }
        for call in model.calls
    )
    schema = next(
        tool for tool in model.calls[0].tools if tool.name == "submit_reply"
    ).params_json_schema
    assert not next(
        tool for tool in model.calls[0].tools if tool.name == "submit_reply"
    ).strict_json_schema
    catalog_tool = next(
        tool for tool in model.calls[0].tools if tool.name == "list_operators"
    )
    assert catalog_tool.strict_json_schema
    assert set(catalog_tool.params_json_schema["required"]) == {
        "progress_intent", "capability", "query", "domain", "after", "limit",
    }
    assert Draft202012Validator(schema).is_valid(
        json.loads(_reply(_draft()).output[0].arguments)
    )
    restored = client.get(f"/api/v12/sessions/{sid}/messages").json()
    assert restored["turns"][0]["workflowDraftId"] == created["draftId"]
    assert restored["turns"][0]["requestMode"] == "WORKFLOW_DRAFT"
    assert restored["turns"][0]["confirmations"] == []
    assert restored["preparation"]["status"] == "BLOCKED"
    assert restored["preparation"]["disposition"] == "CONSUMED"
    (entry,) = restored["preparation"]["entries"]
    assert entry["targetKind"] == "WORKFLOW_DRAFT"
    assert entry["targetId"] == f"draft.{created['draftId']}"
    assert entry["knowledgeRefs"] == to_wire([KNOWLEDGE])
    assert entry["blockingReasons"] == [
        "WORKFLOW_DRAFT_MISSING_DATA:rules",
        "WORKFLOW_DRAFT_UNRESOLVED_ITEMS",
    ]
    assert entry["blockingIssues"] == [
        {
            "code": "WORKFLOW_DRAFT_MISSING_DATA",
            "kind": "MISSING_DATA",
            "input": "rules",
            "parameter": None,
            "path": None,
            "validator": None,
        },
        {
            "code": "WORKFLOW_DRAFT_UNRESOLVED_ITEMS",
            "kind": "UNRESOLVED_ITEM",
            "input": None,
            "parameter": None,
            "path": None,
            "validator": None,
        },
    ]
    assert (
        client.get(f"/api/v12/sessions/{sid}/preparation").json()
        == restored["preparation"]
    )
    with connect_database(app.database) as connection:
        assert (
            connection.execute("SELECT version FROM schema_version").fetchone()[0] == 34
        )
        assert (
            "PRIVATE BODY"
            not in connection.execute(
                "SELECT payload_json FROM workflow_drafts"
            ).fetchone()[0]
        )
    assert "workflow_draft.published" in [
        item["event_type"] for item in app.interaction.events_after(sid, 0)
    ]
    _no_execution(app)


def test_draft_card_summary_is_compact_without_losing_draft_details(kernel_settings):
    draft = _draft()
    draft["summary"] = (
        "该草稿先按外部规则稳定排序，再对排序结果计算各数值列的描述统计，"
        "并保留数据版本、算子契约与参数来源。"
        "排序规则尚未提供，因此草稿不会执行，也不会把当前表格误当成规则文件；"
        "完整限制和缺项仍保存在结构化字段中。"
    )
    app, client, _ = _app(kernel_settings, _steps(draft))
    sid, path, _ = _create(client)

    _drain(app, sid)

    resource = client.get(path).json()
    assert resource["status"] == "COMPLETE", resource
    assert len(resource["payload"]["summary"]) <= 120
    assert resource["payload"]["summary"].endswith(("。", "…"))
    assert resource["payload"]["unresolvedItems"] == draft["unresolved_items"]
    assert resource["payload"]["limitations"] == draft["limitations"]
    _no_execution(app)


def test_deepseek_complete_draft_discards_provider_only_note_fields(
    kernel_settings,
):
    draft = _draft()
    draft["parameter_values_note"] = "Provider-only explanation"
    app, client, _ = _app(kernel_settings, _steps(draft))
    sid, path, _ = _create(client)

    _drain(app, sid)

    resource = client.get(path).json()
    assert resource["status"] == "COMPLETE", resource
    assert resource["payload"]["parameterValues"] == {"axis": "columns"}
    assert "parameterValuesNote" not in resource["payload"]
    _no_execution(app)


def test_unset_optional_operator_parameter_is_not_a_draft_gap(kernel_settings):
    draft = _draft()
    draft["graph"]["parameters"].append("describe_column")
    draft["graph"]["nodes"][1]["parameters"]["column"] = {
        "workflowParameter": "describe_column",
    }
    app, client, _ = _app(kernel_settings, _steps(draft))
    sid, path, _ = _create(client)

    _drain(app, sid)

    resource = client.get(path).json()
    assert resource["status"] == "COMPLETE", resource
    payload = resource["payload"]
    assert payload["missingInputs"] == [{
        "kind": "DATA",
        "name": "rules",
        "requiredBy": [{"nodeId": "sort", "port": "rules"}],
    }]
    assert payload["workflow"]["parameters"] == ["axis"]
    describe = next(
        node for node in payload["workflow"]["nodes"]
        if node["nodeId"] == "describe"
    )
    assert "column" not in describe["parameters"]
    _no_execution(app)


def test_draft_parameter_source_mismatch_keeps_stable_diagnostic_code(
    kernel_settings,
):
    draft = _draft()
    draft["parameter_sources"] = []
    app, client, model = _app(kernel_settings, [
        *_steps(draft),
        _reply(draft, call_id="reply-retry"),
    ])
    sid, path, _ = _create(client)

    _drain(app, sid)

    assert client.get(path).json()["status"] == "FAILED"
    turn = client.get(f"/api/v12/sessions/{sid}/messages").json()["turns"][0]
    assert turn["diagnostics"] == {
        "reason": "REPLY_SCHEMA_INVALID",
        "issues": [{
            "path": "/workflow_draft",
            "type": "value_error",
            "code": "WORKFLOW_DRAFT_PARAMETER_SOURCES_INVALID",
        }],
    }
    repair_input = json.dumps(model.calls[-1].input)
    assert "parameter_sources names unique" in repair_input
    assert "exactly equal to the parameter_values keys" in repair_input
    _no_execution(app)


def test_deepseek_draft_repairs_incompatible_data_binding_format(
    kernel_settings, monkeypatch,
):
    monkeypatch.setitem(_RAW_OBJECT, "media_type", "image/png")
    monkeypatch.setitem(_RAW_OBJECT, "format", "png")
    invalid = _draft()
    repaired = _draft()
    repaired["data_bindings"] = []
    app, client, model = _app(
        kernel_settings,
        [*_steps(invalid), _reply(repaired, call_id="reply-repair")],
    )
    sid, path, _ = _create(client)

    _drain(app, sid)

    resource = client.get(path).json()
    assert resource["status"] == "COMPLETE", resource
    repair_input = json.dumps(model.calls[-1].input)
    assert "DRAFT_DATA_FORMAT_INCOMPATIBLE" in repair_input
    assert "inspected Data media type and format" in repair_input
    assert model.calls[-1].model_settings.tool_choice == "submit_reply"
    _no_execution(app)


def test_deepseek_draft_repairs_one_disabled_read_after_discovery_closes(
    kernel_settings,
):
    searches = [
        ModelStep(output=[function_call(
            "list_operators",
            _catalog_arguments(
                f"查找第 {index + 1} 组候选分析算子",
                query=f"candidate-{index}",
            ),
            call_id=f"catalog-{index}",
        )])
        for index in range(11)
    ]
    stale = ModelStep(output=[function_call(
        "list_operators",
        _catalog_arguments("确认发现阶段关闭后不会继续检索", query="must-not-run"),
        call_id="closed-discovery-read",
    )])
    app, client, model = _app(
        kernel_settings,
        [*searches, stale, _reply(None, cited=False, unresolved=True)],
    )
    sid, path, _ = _create(client)

    _drain(app, sid)

    resource = client.get(path).json()
    assert resource["status"] == "COMPLETE"
    assert resource["payload"] is None
    assert len(model.calls) == 13
    assert model.calls[-1].model_settings.tool_choice == "submit_reply"
    assert model.calls[-1].model_settings.parallel_tool_calls is False
    assert "DRAFT_DISCOVERY_CLOSED" in json.dumps(model.calls[-1].input)
    with connect_database(app.database) as connection:
        attempts = [tuple(row) for row in connection.execute(
            "SELECT stage,attempt,outcome,error_class FROM provider_requests "
            "ORDER BY created_at,rowid"
        )]
    assert attempts[-2][1:] == (
        1, "FAILED", "PROTOCOL_OR_SCHEMA_INVALID",
    )
    assert attempts[-1][0] == attempts[-2][0]
    assert attempts[-1][1:] == (2, "SUCCEEDED", None)
    _no_execution(app)


def test_deepseek_draft_repairs_one_unknown_catalog_identity(kernel_settings):
    app, client, model = _app(
        kernel_settings,
        [_operator("table.unknown", call_id="unknown"), *_steps()],
    )
    sid, path, _ = _create(client)

    _drain(app, sid)

    resource = client.get(path).json()
    assert resource["status"] == "COMPLETE", resource
    assert model.calls[1].model_settings.tool_choice == "get_operator"
    assert model.calls[1].model_settings.parallel_tool_calls is False
    assert "OPERATOR_UNKNOWN" in json.dumps(model.calls[1].input)
    assert model.calls[2].model_settings.tool_choice is None
    with connect_database(app.database) as connection:
        attempts = [tuple(row) for row in connection.execute(
            "SELECT stage,attempt,outcome,error_class FROM provider_requests "
            "ORDER BY created_at,rowid"
        )]
    assert attempts[:2] == [
        ("AGENT_INITIAL:1", 1, "FAILED", "PROTOCOL_OR_SCHEMA_INVALID"),
        ("AGENT_INITIAL:1", 2, "SUCCEEDED", None),
    ]
    _no_execution(app)


def test_deepseek_draft_repairs_one_semantically_invalid_graph(kernel_settings):
    invalid = _draft()
    invalid["graph"]["inputs"][0] = {
        "name": "source",
        "cardinality": "one",
        "mediaTypes": ["application/json"],
        "formats": ["json"],
    }
    app, client, model = _app(
        kernel_settings,
        [*_steps(invalid), _reply(_draft(), call_id="reply-repair")],
    )
    sid, path, _ = _create(client)

    _drain(app, sid)

    resource = client.get(path).json()
    assert resource["status"] == "COMPLETE", resource
    assert model.calls[-1].model_settings.tool_choice == "submit_reply"
    assert model.calls[-1].model_settings.parallel_tool_calls is False
    repair_input = json.dumps(model.calls[-1].input)
    assert "AGENT_WORKFLOW_DRAFT_INVALID" in repair_input
    assert "WORKFLOW_PORT_INCOMPATIBLE" in repair_input
    assert "/graph/nodes/0/inputs/data" in repair_input
    with connect_database(app.database) as connection:
        attempts = [tuple(row) for row in connection.execute(
            "SELECT stage,attempt,outcome,error_class FROM provider_requests "
            "ORDER BY created_at,rowid"
        )]
    assert attempts[-2][0] == attempts[-1][0]
    assert attempts[-2][1:] == (
        1, "FAILED", "PROTOCOL_OR_SCHEMA_INVALID",
    )
    assert attempts[-1][1:] == (2, "SUCCEEDED", None)
    _no_execution(app)


def test_deepseek_draft_repairs_missing_workflow_input_declaration(kernel_settings):
    invalid = _draft()
    invalid["graph"]["inputs"] = invalid["graph"]["inputs"][:1]
    app, client, model = _app(
        kernel_settings,
        [*_steps(invalid), _reply(_draft(), call_id="reply-repair")],
    )
    sid, path, _ = _create(client)

    _drain(app, sid)

    resource = client.get(path).json()
    assert resource["status"] == "COMPLETE", resource
    assert model.calls[-1].model_settings.tool_choice == "submit_reply"
    assert model.calls[-1].model_settings.parallel_tool_calls is False
    repair_input = json.dumps(model.calls[-1].input)
    assert "AGENT_WORKFLOW_DRAFT_INVALID" in repair_input
    assert "WORKFLOW_PORT_INCOMPATIBLE" in repair_input
    assert "/graph/nodes/0/inputs/rules/workflowInput" in repair_input
    assert "no matching graph.inputs declaration" in repair_input
    assert "omit only its data_bindings entry" in repair_input
    with connect_database(app.database) as connection:
        attempts = [tuple(row) for row in connection.execute(
            "SELECT stage,attempt,outcome,error_class FROM provider_requests "
            "ORDER BY created_at,rowid"
        )]
    assert attempts[-2][0] == attempts[-1][0]
    assert attempts[-2][1:] == (
        1, "FAILED", "PROTOCOL_OR_SCHEMA_INVALID",
    )
    assert attempts[-1][1:] == (2, "SUCCEEDED", None)
    _no_execution(app)


def test_deepseek_draft_repairs_disconnected_sequence(
    kernel_settings,
):
    invalid = _draft()
    invalid["graph"]["inputs"].append({
        "name": "source_copy",
        "cardinality": "one",
        "mediaTypes": ["text/csv"],
        "formats": ["csv"],
    })
    invalid["graph"]["nodes"][1]["inputs"]["data"] = {
        "workflowInput": "source_copy",
    }
    invalid["graph"]["outputs"][0]["nodeOutput"] = {
        "nodeId": "sort",
        "port": "data",
    }
    app, client, model = _app(
        kernel_settings,
        [*_steps(invalid), _reply(_draft(), call_id="reply-repair")],
    )
    sid, path, _ = _create(
        client,
        message="请先按规则稳定排序，再计算各数值列的描述统计。",
    )

    _drain(app, sid)

    resource = client.get(path).json()
    assert resource["status"] == "COMPLETE", resource
    repair_input = json.dumps(model.calls[-1].input)
    assert "WORKFLOW_DRAFT_SEQUENCE_INVALID" in repair_input
    assert "upstream nodeOutput" in repair_input
    assert "Expose the requested results as named workflow outputs" in repair_input
    assert "explicitly requested intermediate outputs are valid" in repair_input
    assert model.calls[-1].model_settings.tool_choice == "submit_reply"
    _no_execution(app)


def test_deepseek_draft_allows_explicit_sequence_fan_out_and_multiple_outputs(
    kernel_settings,
):
    draft = _fan_out_draft()
    app, client, model = _app(kernel_settings, _steps(draft))
    sid, path, _ = _create(
        client,
        message="请先做数据 QA，再分别做趋势和缺测分析。",
    )

    _drain(app, sid)

    resource = client.get(path).json()
    assert resource["status"] == "COMPLETE", resource
    assert resource["errorCode"] is None
    assert {
        output["nodeOutput"]["nodeId"]
        for output in resource["payload"]["workflow"]["outputs"]
    } == {"trend", "gaps"}
    assert len(model.calls) == len(_steps(draft))
    _no_execution(app)


def test_deepseek_draft_allows_explicit_sequence_fan_in(kernel_settings):
    draft = _fan_in_draft()
    steps = [
        _operator("table.sort"),
        _operator("table.join"),
        _read(),
        _inspect(),
        _reply(draft),
    ]
    app, client, model = _app(kernel_settings, steps)
    sid, path, _ = _create(
        client,
        message="First sort the two copies, then join them.",
    )

    _drain(app, sid)

    resource = client.get(path).json()
    assert resource["status"] == "COMPLETE", resource
    assert resource["errorCode"] is None
    join = next(
        node
        for node in resource["payload"]["workflow"]["nodes"]
        if node["nodeId"] == "join"
    )
    assert {
        binding["nodeOutput"]["nodeId"]
        for binding in join["inputs"].values()
        if "nodeOutput" in binding
    } == {"sort_left", "sort_right"}
    assert len(model.calls) == len(steps)
    _no_execution(app)


def test_deepseek_draft_does_not_treat_temporal_after_as_sequence(
    kernel_settings,
):
    draft = _parallel_draft()
    steps = [
        _operator("statistics.describe"),
        _read(),
        _inspect(),
        _reply(draft),
    ]
    app, client, model = _app(kernel_settings, steps)
    sid, path, _ = _create(
        client,
        message="Use observations after 2020 for two independent summaries.",
    )

    _drain(app, sid)

    resource = client.get(path).json()
    assert resource["status"] == "COMPLETE", resource
    assert resource["errorCode"] is None
    assert len(model.calls) == len(steps)
    _no_execution(app)


def test_deepseek_draft_repairs_disconnected_explicit_english_sequence(
    kernel_settings,
):
    invalid = deepcopy(_draft())
    invalid["graph"]["nodes"][1]["inputs"]["data"] = {
        "workflowInput": "source",
    }
    app, client, model = _app(
        kernel_settings,
        [*_steps(invalid), _reply(_draft(), call_id="reply-repair")],
    )
    sid, path, _ = _create(
        client,
        message="First sort, then calculate statistics.",
    )

    _drain(app, sid)

    resource = client.get(path).json()
    assert resource["status"] == "COMPLETE", resource
    repair_input = json.dumps(model.calls[-1].input)
    assert "WORKFLOW_DRAFT_SEQUENCE_INVALID" in repair_input
    assert "upstream nodeOutput" in repair_input
    assert "only the final sink operation" not in repair_input
    assert model.calls[-1].model_settings.tool_choice == "submit_reply"
    _no_execution(app)


def test_sequence_check_rejects_connected_graph_in_reverse_requested_order():
    submitted = SubmittedReplyV6.model_validate_json(
        _reply(_draft()).output[0].arguments,
    )
    submission = ReplySubmission(
        Path("unused"),
        {"current_message": "First calculate statistics, then sort."},
        "deepseek",
    )

    assert submission._sequential_draft_issue(submitted.workflow_draft) == (
        "WORKFLOW_DRAFT_SEQUENCE_INVALID",
        "/graph/nodes",
    )


def test_deepseek_draft_repairs_untrusted_knowledge_reference(kernel_settings):
    invalid = _draft()
    invalid["knowledge_refs"] = [{**KNOWLEDGE, "unit_id": "other-unit"}]
    app, client, model = _app(
        kernel_settings,
        [*_steps(invalid), _reply(_draft(), call_id="reply-repair")],
    )
    sid, path, _ = _create(client)

    _drain(app, sid)

    resource = client.get(path).json()
    assert resource["status"] == "COMPLETE", resource
    repair_input = json.dumps(model.calls[-1].input)
    assert "WORKFLOW_DRAFT_EVIDENCE_INVALID" in repair_input
    assert "/knowledge_refs/0" in repair_input
    assert model.calls[-1].model_settings.tool_choice == "submit_reply"
    _no_execution(app)


def test_deepseek_draft_shares_one_retry_across_invalid_parallel_catalog_calls(
    kernel_settings,
):
    invalid = {
        "progress_intent": {
            "capability": "workflow.operator.discovery",
            "objective": "验证缺少检索条件时的协议修复",
            "template_key": "workflow.operator.discovery",
        },
        "capability": None,
        "query": None,
        "domain": None,
        "after": None,
        "limit": 20,
    }
    app, client, model = _app(
        kernel_settings,
        [
            ModelStep(output=[
                function_call(
                    "list_operators", invalid, call_id="invalid-catalog-1",
                ),
                function_call(
                    "list_operators", invalid, call_id="invalid-catalog-2",
                ),
            ]),
            ModelStep(output=[function_call(
                "list_operators",
                _catalog_arguments("修复检索条件并查找排序算子", query="sort"),
                call_id="catalog-repair",
            )]),
            *_steps(),
        ],
    )
    sid, path, _ = _create(client)

    _drain(app, sid)

    resource = client.get(path).json()
    assert resource["status"] == "COMPLETE", resource
    assert model.calls[1].model_settings.tool_choice == "list_operators"
    assert model.calls[1].model_settings.parallel_tool_calls is False
    assert json.dumps(model.calls[1].input).count("RETRY_REQUIRED") == 2
    assert model.calls[2].model_settings.tool_choice is None
    with connect_database(app.database) as connection:
        attempts = [tuple(row) for row in connection.execute(
            "SELECT stage,attempt,outcome,error_class FROM provider_requests "
            "ORDER BY created_at,rowid"
        )]
    assert attempts[:2] == [
        ("AGENT_INITIAL:1", 1, "FAILED", "PROTOCOL_OR_SCHEMA_INVALID"),
        ("AGENT_INITIAL:1", 2, "SUCCEEDED", None),
    ]
    _no_execution(app)


def test_deepseek_draft_catalog_identity_retry_is_bounded(kernel_settings):
    app, client, model = _app(
        kernel_settings,
        [
            _operator("table.unknown", call_id="unknown-1"),
            _operator("table.still-unknown", call_id="unknown-2"),
        ],
    )
    sid, path, _ = _create(client)

    _drain(app, sid)

    resource = client.get(path).json()
    assert resource["status"] == "FAILED"
    assert resource["errorCode"] == "AGENT_MODEL_OUTPUT_INVALID"
    with connect_database(app.database) as connection:
        failed = read_session_events(
            connection, sid, "agent.failed",
            agent_run_ids=(resource["agentRunId"],),
        )
    assert failed[-1]["data"]["diagnostics"] == {
        "reason": "OPERATOR_UNKNOWN",
    }
    assert len(model.calls) == 2
    _no_execution(app)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("dataset_id", "other"),
        ("version_id", "other"),
        ("semantic_id", "other"),
        ("object_id", "other"),
        ("checksum", "sha256:" + "3" * 64),
        ("media_type", "application/json"),
        ("format", "json"),
        ("size_bytes", 24),
    ],
)
def test_data_binding_uses_observed_version_as_authoritative_reference(
    kernel_settings, field, bad,
):
    draft = _draft()
    draft["data_bindings"] = deepcopy(draft["data_bindings"])
    draft["data_bindings"][0]["data_ref"][field] = bad
    steps = _steps(draft)
    if field in {"checksum", "media_type", "format"}:
        steps.append(_reply(draft, call_id="reply-retry"))
    app, client, _ = _app(kernel_settings, steps)
    sid, path, _ = _create(client)
    _drain(app, sid)
    resource = client.get(path).json()
    if field == "version_id":
        assert resource["errorCode"] == "AGENT_MODEL_OUTPUT_INVALID"
        assert resource["status"] == "FAILED"
        assert resource["payload"] is None
    else:
        assert resource["status"] == "COMPLETE", resource
        assert resource["payload"]["dataBindings"] == to_wire(
            _draft()["data_bindings"]
        )
    _no_execution(app)


@pytest.mark.parametrize(
    "change",
    [
        "unknown_operator",
        "bad_port",
        "cycle",
        "extra_parameter",
        "nonfinite",
        "authoritative_field",
        "unknown_knowledge",
    ],
)
def test_invalid_draft_never_publishes_or_executes(kernel_settings, change):
    draft = _draft()
    if change == "unknown_operator":
        draft["graph"]["nodes"][0]["operatorId"] = "not.installed"
    elif change == "bad_port":
        draft["graph"]["nodes"][1]["inputs"]["data"]["nodeOutput"]["port"] = "imagined"
    elif change == "cycle":
        draft["graph"]["nodes"][0]["inputs"]["data"] = {
            "nodeOutput": {"nodeId": "describe", "port": "summary"}
        }
    elif change == "extra_parameter":
        draft["parameter_values"]["invented"] = "value"
    elif change == "nonfinite":
        draft["parameter_values"]["axis"] = float("nan")
    elif change == "authoritative_field":
        draft["graph"]["nodes"][0]["implementationRevision"] = "sha256:" + "3" * 64
    else:
        draft["knowledge_refs"] = [{**KNOWLEDGE, "unit_id": "other-unit"}]
    steps = _steps(draft)
    if change in {
        "unknown_operator", "bad_port", "cycle", "extra_parameter",
        "nonfinite", "authoritative_field", "unknown_knowledge",
    }:
        steps.append(_reply(draft, call_id="reply-retry"))
    app, client, _ = _app(kernel_settings, steps)
    sid, path, _ = _create(client)
    _drain(app, sid)
    assert client.get(path).json()["errorCode"] == "AGENT_MODEL_OUTPUT_INVALID"
    assert client.get(path).json()["payload"] is None
    assert (
        client.get(f"/api/v12/sessions/{sid}/messages").json()["turns"][0]["status"]
        == "FAILED"
    )
    _no_execution(app)


def test_each_operator_requires_current_turn_detail_read(kernel_settings):
    app, client, _ = _app(
        kernel_settings,
        [_operator("table.sort"), _read(), _inspect(), _reply(_draft())],
    )
    sid, path, _ = _create(client)
    _drain(app, sid)
    assert client.get(path).json()["errorCode"] == "AGENT_MODEL_OUTPUT_INVALID"
    assert client.get(path).json()["payload"] is None
    assert client.get(path).json()["diagnostics"] == {
        "kind": "WORKFLOW_VALIDATION",
        "code": "WORKFLOW_DRAFT_OPERATOR_READ_REQUIRED",
        "path": "/graph/nodes/1",
    }


@pytest.mark.parametrize(
    "change,code,pointer",
    [
        ("port", "WORKFLOW_PORT_INCOMPATIBLE", "/graph/nodes/1/inputs/data/nodeOutput"),
        ("parameter", "DRAFT_PARAMETER_INVALID", "/parameter_values/axis"),
        ("data", "WORKFLOW_DRAFT_EVIDENCE_INVALID", "/data_bindings/0/data_ref"),
        ("knowledge", "WORKFLOW_DRAFT_EVIDENCE_INVALID", "/knowledge_refs/0"),
    ],
)
def test_validation_diagnostics_survive_restart_without_publishing_bodies(
    kernel_settings, change, code, pointer
):
    draft = _draft()
    if change == "port":
        draft["graph"]["nodes"][1]["inputs"]["data"]["nodeOutput"]["port"] = "missing"
    elif change == "parameter":
        draft["parameter_values"]["axis"] = "PRIVATE_INVALID_PARAMETER"
    elif change == "data":
        draft["data_bindings"][0]["data_ref"] = {**REFERENCE, "version_id": "other"}
    else:
        draft["knowledge_refs"] = [{**KNOWLEDGE, "unit_id": "other-unit"}]
    steps = _steps(draft)
    if change in {"port", "parameter", "knowledge"}:
        steps.append(_reply(draft, call_id="reply-retry"))
    app, client, _ = _app(kernel_settings, steps)
    sid, path, created = _create(client)
    _drain(app, sid)
    expected = {"kind": "WORKFLOW_VALIDATION", "code": code, "path": pointer}
    failed = client.get(path).json()
    assert failed["status"] == "FAILED" and failed["payload"] is None
    assert failed["diagnostics"] == expected
    events = [
        event
        for event in app.interaction.events_after(sid, 0)
        if event["event_type"] == "agent.failed"
    ]
    assert len(events) == 1 and events[0]["data"]["diagnostics"] == expected
    assert "PRIVATE" not in json.dumps(events)
    assert client.get(path + "/export").status_code == 409
    restored = PlatformApplication(
        settings=app.settings, model=ScriptedModel([]), client=app.client
    )
    restored_client = TestClient(create_app(application=restored))
    assert restored_client.get(path).json() == failed
    assert restored_client.get(path).json()["agentRunId"] == created["agentRunId"]
    _no_execution(restored)


def test_failure_event_and_diagnostics_commit_with_agent_state(kernel_settings):
    draft = _draft()
    draft["parameter_values"]["axis"] = "invalid"
    app, client, _ = _app(
        kernel_settings,
        [*_steps(draft), _reply(draft, call_id="reply-retry")],
    )
    sid, path, _ = _create(client)
    with connect_database(app.database) as connection:
        connection.execute("""CREATE TRIGGER reject_failure BEFORE INSERT ON session_events
            WHEN NEW.event_type='agent.failed'
            BEGIN SELECT RAISE(ABORT, 'failure event rejected'); END;""")
    with pytest.raises(sqlite3.IntegrityError, match="failure event rejected"):
        _drain(app, sid)
    failed = client.get(path).json()
    assert failed["status"] == "GENERATING" and failed["diagnostics"] is None
    with connect_database(app.database) as connection:
        assert connection.execute("SELECT status,error FROM agent_runs").fetchone()[
            :
        ] == ("RUNNING", None)
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM session_events WHERE event_type='agent.failed'"
            ).fetchone()[0]
            == 0
        )
    _no_execution(app)


@pytest.mark.parametrize(
    "status,code",
    [
        (404, "OBSERVATION_RESOURCE_NOT_FOUND"),
        (422, "OBSERVATION_ARGUMENTS_INVALID"),
        (503, "OBSERVATION_SERVICE_UNAVAILABLE"),
        (500, "OBSERVATION_RESPONSE_INVALID"),
    ],
)
def test_observation_failure_retains_stable_code_in_durable_draft(
    kernel_settings, status, code
):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, json={"code": "PRIVATE_RESPONSE", "detail": "PRIVATE_BODY"}
        )

    steps = (
        [_read(call_id="read-invalid-1"), _read(call_id="read-invalid-2")]
        if status == 422
        else [_read()]
    )
    app, client, model = _application(kernel_settings, steps, handler=handler)
    sid, path, _ = _create(client)
    _drain(app, sid)
    result = client.get(path).json()
    assert result["status"] == "FAILED" and result["errorCode"] == code
    assert result["diagnostics"] is None
    assert len(calls) == len(model.calls) == (2 if status == 422 else 1)
    assert "PRIVATE" not in json.dumps(app.interaction.events_after(sid, 0))
    _no_execution(app)


def test_failed_draft_releases_session_and_ordinary_turn_cannot_publish_a_draft(
    kernel_settings,
):
    app, client, _ = _app(
        kernel_settings,
        [
            _reply(None, cited=False, call_id="draft-invalid-1"),
            _reply(None, cited=False, call_id="draft-invalid-2"),
            _reply(None, cited=False, call_id="ordinary"),
            _reply(_draft(), cited=False, call_id="dialogue-invalid-1"),
            _reply(_draft(), cited=False, call_id="dialogue-invalid-2"),
        ],
    )
    sid, path, _ = _create(client)
    _drain(app, sid)
    assert client.get(path).json()["status"] == "FAILED"
    ordinary = _dialogue(app, client, sid, "another question")
    assert ordinary.status_code == 200 and ordinary.json()["workflowDraftId"] is None
    forbidden = _dialogue(app, client, sid, "not an authoring request")
    assert forbidden.status_code == 200
    assert (
        client.get(f"/api/v12/sessions/{sid}/messages").json()["turns"][-1]["errorCode"]
        == "AGENT_MODEL_OUTPUT_INVALID"
    )
    with connect_database(app.database) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM workflow_drafts").fetchone()[0]
            == 1
        )
    _no_execution(app)


def test_request_replay_conflicts_and_concurrent_creation_have_one_winner(
    kernel_settings,
):
    app, client, _ = _app(kernel_settings, _steps())
    sid, path, created = _create(client)
    assert _create(client, session_id=sid)[2]["agentRunId"] == created["agentRunId"]
    conflict = _message(
        client,
        sid,
        "changed",
        requested_mode="WORKFLOW_DRAFT",
        request_id="draft-request",
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "MESSAGE_REQUEST_CONFLICT"
    other = _message(
        client,
        sid,
        "new",
        requested_mode="WORKFLOW_DRAFT",
        request_id="other",
    )
    assert other.status_code == 409
    _drain(app, sid)
    assert _create(client, session_id=sid)[2]["draftId"] == created["draftId"]
    fresh = client.post("/api/v12/sessions", json={"projectId": "p"}).json()[
        "sessionId"
    ]

    def reserve(index):
        return _message(
            client,
            fresh,
            "compose",
            requested_mode="WORKFLOW_DRAFT",
            request_id=str(index),
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, [1, 2])) == [202, 409]
    _no_execution(app)


def test_null_draft_has_explicit_unmet_requirement_and_no_export(kernel_settings):
    app, client, _ = _app(
        kernel_settings,
        [_reply(None, cited=False, unresolved=True), _reply(None, cited=False)],
    )
    sid, path, created = _create(client)
    _drain(app, sid)
    assert client.get(path).json()["status"] == "COMPLETE"
    assert client.get(path).json()["payload"] is None
    assert client.get(path + "/export").status_code == 409
    turn = _dialogue(app, client, sid, "ordinary question")
    assert turn.status_code == 200 and turn.json()["workflowDraftId"] is None
    restored = client.get(f"/api/v12/sessions/{sid}/messages").json()
    assert [item["workflowDraftId"] for item in restored["turns"]] == [
        created["draftId"],
        None,
    ]
    _no_execution(app)


def test_draft_cannot_use_execution_interruption_even_if_a_tool_is_accidentally_added(
    kernel_settings, monkeypatch
):
    from platform_kernel.agent_runtime.sdk_execution import execution_builders
    from platform_kernel.agent_runtime.model_tool import build_model_tool

    original = execution_builders.build_agent_tools
    app, client, _ = _app(kernel_settings, [_execute()])

    def add_execution_tool(*args, **kwargs):
        return [
            *original(*args, **kwargs),
            build_model_tool(app.database, app.settings.models[0]),
        ]

    monkeypatch.setattr(execution_builders, "build_agent_tools", add_execution_tool)
    sid, path, _ = _create(client)
    _drain(app, sid)
    assert client.get(path).json()["errorCode"] == "AGENT_READ_ONLY_EXECUTION_FORBIDDEN"
    assert client.get(path).json()["status"] == "FAILED"
    _no_execution(app)


def test_publication_event_failure_rolls_back_draft_reply_and_terminal_state(
    kernel_settings,
):
    app, client, _ = _app(kernel_settings, _steps())
    sid, path, _ = _create(client)
    with connect_database(app.database) as connection:
        connection.executescript("""CREATE TRIGGER reject_draft BEFORE INSERT ON session_events
            WHEN NEW.event_type='workflow_draft.published'
            BEGIN SELECT RAISE(ABORT, 'draft publication failed'); END;""")
    with pytest.raises(sqlite3.IntegrityError, match="draft publication failed"):
        _drain(app, sid)
    with connect_database(app.database) as connection:
        assert (
            connection.execute("SELECT payload_json FROM workflow_drafts").fetchone()[0]
            is None
        )
        assert (
            connection.execute("SELECT status FROM agent_runs").fetchone()[0]
            == "RUNNING"
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM messages WHERE role='assistant'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM task_preparations").fetchone()[0]
            == 0
        )
    app.agent_runtime.recover_interrupted()
    assert client.get(path).json()["status"] == "FAILED"
    _no_execution(app)


def test_durable_start_and_completed_export_survive_restart(kernel_settings):
    app, client, _ = _app(kernel_settings, [])
    sid, path, created = _create(client)
    restarted = PlatformApplication(
        settings=app.settings, model=ScriptedModel(_steps()), client=app.client
    )
    assert restarted.interaction.prepare_recovery() == (sid,)
    _drain(restarted, sid)
    before = TestClient(create_app(application=restarted)).get(path + "/export").content
    again = PlatformApplication(
        settings=app.settings, model=ScriptedModel([]), client=app.client
    )
    restored = TestClient(create_app(application=again))
    assert restored.get(path + "/export").content == before
    assert restored.get(path).json()["agentRunId"] == created["agentRunId"]
    assert again.interaction.prepare_recovery() == ()
    _no_execution(again)


def test_runtime_claims_new_drafts_after_startup(kernel_settings):
    app, _, _ = _app(kernel_settings, _steps())

    async def exercise():
        await app.runtime.start()
        try:
            sid = app.interaction.create_session().session.session_id
            created = app.interaction.message(
                sid,
                "async",
                "compose",
                "WORKFLOW_DRAFT",
            )
            for _ in range(200):
                result = app.interaction.workflow_draft(
                    sid, created["workflow_draft_id"]
                )
                if result["status"] != "GENERATING":
                    break
                await asyncio.sleep(0.01)
            assert result["status"] == "COMPLETE"
        finally:
            await app.runtime.stop()

    asyncio.run(exercise())
    _no_execution(app)


def test_draft_reads_exports_and_creation_are_session_and_project_scoped(
    kernel_settings,
):
    app, client, _ = _app(kernel_settings, _steps())
    sid, path, created = _create(client)
    _drain(app, sid)
    other = client.post("/api/v12/sessions", json={"projectId": "p"}).json()[
        "sessionId"
    ]
    for suffix in ("", "/export"):
        assert client.get(path.replace(sid, other) + suffix).status_code == 404
        assert (
            client.get(path + suffix, params={"projectId": "foreign"}).status_code
                == 404
        )
    foreign = PlatformApplication(
        settings=replace(app.settings, project_id="foreign"),
        model=ScriptedModel([]),
        client=app.client,
    )
    foreign_client = TestClient(create_app(application=foreign))
    assert foreign_client.get(path).status_code == 404
    assert (
        _message(
            foreign_client,
            sid,
            "x",
            requested_mode="WORKFLOW_DRAFT",
            request_id="x",
        ).status_code
        == 404
    )
    assert client.get(path).json()["draftId"] == created["draftId"]


def test_authoring_context_uses_staged_discovery_and_old_draft_is_not_current_evidence(
    kernel_settings,
):
    app, client, model = _app(
        kernel_settings,
        [
            *_steps(),
            _reply(_draft(), cited=False),
            _reply(_draft(), cited=False, call_id="second-invalid-draft"),
        ],
    )
    app = PlatformApplication(
        settings=replace(
            app.settings,
            system_prompt="Legacy domain guidance: expose only published models; operator inventory is not exposed.",
        ),
        model=model,
        client=app.client,
    )
    client = TestClient(create_app(application=app))
    sid, first_path, _ = _create(client)
    _drain(app, sid)
    frozen = client.get(first_path).json()["payload"]
    first_call_count = len(model.calls)
    _, second_path, _ = _create(
        client, session_id=sid, request_id="new-draft", message="另建一份草稿"
    )
    _drain(app, sid)
    assert client.get(second_path).json()["status"] == "FAILED"
    assert client.get(second_path).json()["errorCode"] == "AGENT_MODEL_OUTPUT_INVALID"
    assert client.get(first_path).json()["payload"] == frozen
    call = model.calls[first_call_count]
    assert "PRIVATE BODY" not in json.dumps(call.input)
    assert all(
        item.get("type") not in {"function_call", "function_call_output"}
        for item in call.input
    )
    instructions = call.system_instructions
    stages = [
        "1. Establish intent and outputs.",
        "2. Discover Data versions, dictionaries and candidate operators.",
        "3. Read each candidate contract before selecting it.",
        "4. Check method prerequisites against observed facts.",
        "5. Compose the graph, gaps and evidence.",
        "6. Submit the draft or explain why no valid composition is available.",
    ]
    assert [instructions.index(stage) for stage in stages] == sorted(
        instructions.index(stage) for stage in stages
    )
    assert all(
        field in instructions
        for field in (
            "binding_rules",
            "many_inputs_supported",
            "value_outputs_feed_nodes",
            "value_outputs_can_be_final",
            "oneOf",
            "if/then/else",
            "OPERATOR_DEFAULT",
        )
    )
    assert "Treat a shared canonical Data version as one input" in instructions
    assert "do not prove that two versions have the same entities or" in instructions
    assert (
        "never use a simulated fact to fill a missing field or assumption in an observed version"
        in instructions
    )
    assert (
        "Do not replace that artifact with an inline parameter object" in instructions
    )
    assert (
        "leave the input unbound and describe its required contents as a gap"
        in instructions
    )
    assert (
        "format or CRS conversion must be an explicit compatible installed operator node"
        in instructions
    )
    assert (
        "are not current evidence: re-read operator details, Data versions and Knowledge in this Turn"
        in instructions
    )
    assert "take precedence over domain instructions about model-only" in instructions
    assert "Legacy domain guidance" in instructions
    assert all(
        value not in instructions
        for value in (
            "table.sort",
            "statistics.describe",
            RESOURCE["id"],
            "run_summary",
        )
    )
    assert all(tool.name not in {"record_plan", "run_summary"} for tool in call.tools)
    _no_execution(app)


def test_explicit_sequence_can_return_both_requested_intermediate_and_final_outputs(
    kernel_settings,
):
    draft = _draft()
    draft["graph"]["outputs"].append({
        "name": "sorted_table",
        "nodeOutput": {"nodeId": "sort", "port": "data"},
    })
    app, client, model = _app(
        kernel_settings, [*_steps(draft), _reply(draft, call_id="explicit-intermediate-repair")],
    )
    sid, path, _ = _create(
        client,
        message="请先按规则排序，再对排序表做描述统计；请把排序后的表和最终描述统计都作为输出。",
    )

    _drain(app, sid)

    resource = client.get(path).json()
    assert resource["status"] == "COMPLETE", resource["errorCode"]
    assert resource["payload"]["workflow"]["outputs"] == draft["graph"]["outputs"]
    assert len(model.calls) == len(_steps(draft))
    _no_execution(app)
