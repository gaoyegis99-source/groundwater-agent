from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib

import httpx
import pytest

from agents.testing import ScriptedModel
from platform_kernel.composition import PlatformApplication
from platform_kernel.contracts import JobStatusV6, DataVersionRefV2
from platform_kernel.settings import (
    ExecutionCaseDefinition,
    ExecutionCaseInputRole,
    ObservationToolDefinition,
)
from platform_kernel.workflow_execution.models import DispatchWork
from platform_kernel.workflow_execution.persistence import get_run, save_job_status
from platform_kernel.agent_runtime.case_resolver import (
    matches_execution_case_request,
    matches_explicit_model_tool_request,
    parse_comparison_terms,
    parse_explicit_model_tool_request,
    parse_required_response_terms,
    requires_project_evidence,
)


def test_comparison_terms_preserve_user_named_business_methods():
    assert parse_comparison_terms(
        "比较监测趋势分析和地下水流场分析：只给区别和建议。"
    ) == ("监测趋势分析", "地下水流场分析")
    assert parse_comparison_terms("请介绍监测趋势分析。") is None


def test_required_response_terms_preserve_enumerated_unit_items():
    assert parse_required_response_terms(
        "请直接说明水头、高程和地下水埋深使用什么单位；不要执行分析。"
    ) == ("水头", "高程", "地下水埋深")
    assert parse_required_response_terms(
        "What units do head, elevation and depth to water use?"
    ) == ("head", "elevation", "depth to water")
    assert parse_required_response_terms("请说明地下水位是什么。") == ()


def test_project_convention_request_requires_project_evidence():
    assert requires_project_evidence("请按项目约定执行监测趋势分析。")
    assert requires_project_evidence(
        "Run this using the project conventions."
    )
    assert not requires_project_evidence("请执行监测趋势分析。")


def _with_case_search(settings):
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "tag": {"type": ["string", "null"]},
            "limit": {"type": ["integer", "null"]},
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    search = ObservationToolDefinition(
        name="discover_datasets", description="search", service="data",
        path="/internal/tools/v3/discover_datasets", input_schema=schema,
        output_schema={"type": "object"}, input_schema_checksum="test",
        output_schema_checksum="test", timeout_seconds=10,
    )
    return replace(settings, observation_tools=(search, *settings.observation_tools))


def test_case_rule_requires_alias_and_explicit_positive_execution(kernel_settings) -> None:
    settings = replace(kernel_settings("summary"), execution_cases=(
        ExecutionCaseDefinition(
            case_id="test-case", aliases=(
                "腰站堡迁移学习开发案例", "腰站堡的迁移学习模型", "Y01",
            ),
            model_id="groundwater.summary", model_version="1.1.0",
            dataset_tag="case:test-case",
            input_roles=(ExecutionCaseInputRole("table", "resource_id"),),
            default_profile="standard", profiles={"standard": {}},
        ),
    ))
    assert matches_execution_case_request(
        settings, "请运行腰站堡迁移学习开发案例"
    )
    assert matches_execution_case_request(settings, "执行腰站堡的迁移学习模型")
    assert matches_execution_case_request(settings, "execute Y01")
    assert not matches_execution_case_request(settings, "请说明腰站堡迁移学习开发案例")
    assert not matches_execution_case_request(settings, "请停止运行腰站堡迁移学习开发案例")


def test_explicit_installed_model_tool_request_is_deterministic_execution_intent(
    kernel_settings,
) -> None:
    settings = kernel_settings("summary")

    assert matches_explicit_model_tool_request(
        settings, "请使用工具 run_summary，并先提出执行方案等待确认。"
    )
    assert matches_explicit_model_tool_request(
        settings, "Please invoke tool run_summary after checking the input."
    )
    assert not matches_explicit_model_tool_request(
        settings, "请说明 run_summary 工具的参数。"
    )
    assert not matches_explicit_model_tool_request(
        settings, "请不要使用工具 run_summary。"
    )
    assert not matches_explicit_model_tool_request(
        settings, "请使用工具 run_not_installed。"
    )

    parsed = parse_explicit_model_tool_request(
        settings,
        '请使用工具 run_summary。\n[Model execution] '
        '{"tool":"run_summary","arguments":{"resource_id":"version-1"}}',
    )
    assert parsed is not None
    assert (parsed.tool, parsed.arguments) == (
        "run_summary", {"resource_id": "version-1"},
    )
    assert parse_explicit_model_tool_request(
        settings,
        '请使用工具 run_summary。\n[Model execution] '
        '{"tool":"unknown","arguments":{}}',
    ) is None


def test_exact_case_creates_confirmation_without_agent_model_call(kernel_settings) -> None:
    base = kernel_settings("summary")
    case = ExecutionCaseDefinition(
        case_id="test-case",
        aliases=("固定案例",),
        model_id="groundwater.summary",
        model_version="1.1.0",
        dataset_tag="case:test-case",
        input_roles=(ExecutionCaseInputRole("table", "resource_id"),),
        default_profile="standard",
        profiles={"standard": {}, "development_fast": {}},
    )
    settings = replace(_with_case_search(base), execution_cases=(case,))
    checksum = "sha256:" + hashlib.sha256(b"value\n1\n").hexdigest()

    def transport(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        if request.url.path.endswith("discover_datasets"):
            return httpx.Response(200, request=request, json={"data": {
                "items": [{
                    "datasetId": "dataset-1", "currentVersion": "version-1",
                    "tags": [
                        "case:test-case", "case-batch:receipt-1", "input:table",
                    ],
                }],
                "nextCursor": None,
            }})
        assert request.url.path.endswith("inspect_data_version")
        assert body["arguments"] == {"version_id": "version-1"}
        return httpx.Response(200, request=request, json={"data": {
            "status": "OK", "versionId": "version-1", "datasetId": "dataset-1",
            "semanticId": "semantic-1", "objects": [{
                "objectId": "object-1", "role": "raw", "format": "csv",
                "mediaType": "text/csv", "size": 8, "checksum": checksum,
            }],
        }})

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    application = PlatformApplication(
        settings=settings,
        model=ScriptedModel([]),
        client=client,
    )
    session = application.interaction.create_session().session
    view = asyncio.run(application.interaction.turn(
        session.session_id,
        "请运行固定案例",
        dialogue_policy="EXECUTION_PROPOSAL",
    ))

    assert view.agent_steps[-1] == "WAITING_CONFIRMATION"
    assert view.session.confirmation is not None
    assert len(view.session.confirmation.actions) == 1
    action = view.session.confirmation.actions[0]
    assert action.arguments == {"resource_id": "version-1", "axis": "columns"}
    assert action.input_references["resource_id"].version_id == "version-1"
    run = application.agent_runtime.get(view.session.agent_run_id)
    assert run.input_tokens == run.output_tokens == run.total_tokens == 0
    assert run.plans[0].reason.startswith("Kernel 已按案例 test-case 核验 1 项同批输入")
    assert run.plans[0].goal == "执行固定案例并发布可核验成果"
    assert "训练" not in run.plans[0].reason
    rejected = asyncio.run(application.interaction.reject(
        session.session_id, view.session.confirmation.confirmation_id, "取消执行",
    ))
    assert rejected.session.agent_output is None
    while asyncio.run(application.interaction.tick()):
        pass
    completed_view = application.interaction.get(session.session_id)
    completed = completed_view.session
    asyncio.run(client.aclose())
    assert completed_view.agent_steps[-1] == "COMPLETE"
    assert completed.agent_output.message == "已拒绝 1 项分析。"
    assert completed.workflow_run_ids == ()
    assert [item.status for item in application.agent_runtime.get(
        view.session.agent_run_id
    ).actions] == ["REJECTED"]


def test_duplicate_case_role_falls_back_to_generic_agent(kernel_settings) -> None:
    base = kernel_settings("summary")
    settings = replace(_with_case_search(base), execution_cases=(ExecutionCaseDefinition(
        case_id="test-case", aliases=("固定案例",),
        model_id="groundwater.summary", model_version="1.1.0",
        dataset_tag="case:test-case",
        input_roles=(ExecutionCaseInputRole("table", "resource_id"),),
        default_profile="standard", profiles={"standard": {}},
    ),))

    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("discover_datasets"):
            item = {
                "datasetId": "dataset-1", "currentVersion": "version-1",
                "tags": ["case:test-case", "case-batch:receipt-1", "input:table"],
            }
            return httpx.Response(200, request=request, json={"data": {
                "items": [item, {**item, "datasetId": "dataset-2"}], "nextCursor": None,
            }})
        raise AssertionError("duplicate role must stop exact resolution")

    # The empty scripted model proves the resolver returned to the generic path:
    # it fails for lack of a provider step instead of silently creating an action.
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    model = ScriptedModel([])
    application = PlatformApplication(settings=settings, model=model, client=client)
    session = application.interaction.create_session().session
    try:
        asyncio.run(application.interaction.turn(
            session.session_id, "运行固定案例", dialogue_policy="EXECUTION_PROPOSAL",
        ))
    except Exception:
        pass
    finally:
        asyncio.run(client.aclose())
    assert len(model.calls) == 1
    assert application.interaction.get(session.session_id).session.confirmation is None


@pytest.mark.parametrize("job_status", ["SUCCEEDED", "FAILED"])
def test_kernel_case_finalizes_from_persisted_workflow_facts(kernel_settings, job_status) -> None:
    base = kernel_settings("summary")
    settings = replace(_with_case_search(base), execution_cases=(ExecutionCaseDefinition(
        case_id="test-case", aliases=("固定案例",),
        model_id="groundwater.summary", model_version="1.1.0",
        dataset_tag="case:test-case",
        input_roles=(ExecutionCaseInputRole("table", "resource_id"),),
        default_profile="standard", profiles={"standard": {}},
    ),))
    checksum = "sha256:" + "a" * 64

    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("discover_datasets"):
            return httpx.Response(200, request=request, json={"data": {
                "items": [{
                    "datasetId": "dataset-1", "currentVersion": "version-1",
                    "tags": ["case:test-case", "case-batch:receipt-1", "input:table"],
                }], "nextCursor": None,
            }})
        if request.url.path.endswith("inspect_data_version"):
            return httpx.Response(200, request=request, json={"data": {
                "status": "OK", "versionId": "version-1", "datasetId": "dataset-1",
                "semanticId": "semantic-1", "objects": [{
                    "objectId": "object-1", "role": "raw", "format": "csv",
                    "mediaType": "text/csv", "size": 1, "checksum": checksum,
                }],
            }})
        raise AssertionError(request.url.path)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    application = PlatformApplication(settings=settings, model=ScriptedModel([]), client=client)
    session = application.interaction.create_session().session
    waiting = asyncio.run(application.interaction.turn(
        session.session_id, "运行固定案例", dialogue_policy="EXECUTION_PROPOSAL",
    ))
    confirmation = waiting.session.confirmation
    confirmed = asyncio.run(application.interaction.confirm(
        session.session_id, confirmation.confirmation_id,
    ))
    workflow_id = confirmed.session.workflow_run_ids[0]
    workflow = get_run(application.database, "p", workflow_id)
    output = DataVersionRefV2(
        kind="data_version", service="data-primary", dataset_id="result-dataset",
        version_id="result-version", semantic_id="result-semantic",
        object_id="result-object", media_type="application/json", format_id="json",
        size_bytes=2, checksum="sha256:" + "b" * 64, output_name="summary",
    )
    assert save_job_status(
        application.database,
        DispatchWork(workflow, "SUBMITTED", "future"),
        JobStatusV6(
            job_id="job-1", request_id=workflow.run_id, status=job_status,
            outputs=(output,) if job_status == "SUCCEEDED" else (),
            error="OPERATOR_FAILED" if job_status == "FAILED" else None,
        ),
    )
    # Recovery must use the approved plan and arguments even if the deployment's
    # case aliases and model defaults have since changed.
    changed_settings = replace(
        settings,
        execution_cases=(replace(settings.execution_cases[0], aliases=("另一个案例",)),),
        models=(replace(settings.models[0], default_parameters={"axis": "rows"}),),
    )
    recovered = PlatformApplication(settings=changed_settings, model=ScriptedModel([]), client=client)
    final = asyncio.run(recovered.agent_runtime.resume_execution(
        confirmed.session.agent_run_id,
    ))
    asyncio.run(client.aclose())
    assert final.status == ("COMPLETE" if job_status == "SUCCEEDED" else "FAILED")
    assert final.total_tokens == 0
    assert final.output.workflow_runs == [workflow.run_id]
    assert "执行固定案例并发布可核验成果" in final.output.message
    assert "另一个案例" not in final.output.message
    assert "腰站堡" not in final.output.message
    assert "训练" not in final.output.message
    assert "地下水" not in final.output.message
    assert final.actions[0].arguments["axis"] == "columns"
    if job_status == "SUCCEEDED":
        assert "发布了 1 项可核验成果" in final.output.message
        assert "本次确认时冻结的快照" in final.output.message
    else:
        assert "执行未完成" in final.output.message
        assert "OPERATOR_FAILED" in final.output.message
