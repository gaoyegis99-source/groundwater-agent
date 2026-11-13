from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel
from agents.testing import ScriptedModel
import httpx
from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate as jsonschema_validate
import pytest

from platform_kernel.agent_runtime.observation_client import ObservationClient
from platform_kernel.agent_runtime.evidence import collect_turn_evidence
from platform_kernel.agent_runtime.mode import resolve_mode_capabilities
from platform_kernel.agent_runtime.provider import (
    build_agent_provider,
    build_model_settings,
)
from platform_kernel.agent_runtime.provider_arguments import (
    normalize_model_arguments,
    normalize_model_tool_arguments,
)
from platform_kernel.agent_runtime.persistence.timeline import _public_diagnostics
from platform_kernel.agent_runtime import preparation_tool as preparation_tool_module
from platform_kernel.agent_runtime.preparation_tool import (
    _schema_blocking_reasons,
    build_preparation_tool,
)
from platform_kernel.agent_runtime.result_processing import (
    _PreparedArgumentsMismatchError,
    _argument_difference_paths,
    _processing_diagnostics,
    _select_prepared_arguments,
)
from platform_kernel.agent_runtime.sdk_execution.activity_projection import (
    _minimax_final_reply_json,
)
from platform_kernel.agent_runtime.tooling import build_agent_tools
from platform_kernel.agent_runtime.tooling.observations import (
    _normalize_observation_arguments,
    _observation_description,
)
from platform_kernel.agent_runtime.tooling.read_governance import (
    _schema_repair_instruction,
)
from platform_kernel.agent_runtime.tooling.replies import (
    _record_read_evidence, _record_retrieved_evidence,
)
from platform_kernel.contracts import JobRequestV6
from platform_kernel.composition import PlatformApplication
from platform_kernel.errors import AgentRunError, UpstreamServiceError
from platform_kernel.runtime import KernelRuntime
from platform_kernel.tasks.models import TaskPreparationEntryV1
from platform_kernel.workflow_execution.execution_client import (
    ExecutionClient,
    ExecutionClientError,
)
from platform_kernel.workflow_execution.models import WorkflowTickResult


def _job(request_id: str) -> dict[str, object]:
    return {
        "jobId": "job-1",
        "requestId": request_id,
        "status": "QUEUED",
        "stage": None,
        "error": None,
        "outputs": [],
        "serviceVersion": None,
        "createdAt": None,
        "startedAt": None,
        "finishedAt": None,
    }


def test_mode_capabilities_are_the_single_tool_policy(kernel_settings) -> None:
    settings = kernel_settings()
    models = settings.models
    dialogue = resolve_mode_capabilities(None, models)
    draft = resolve_mode_capabilities({"mode": "WORKFLOW_DRAFT"}, models)
    executable = resolve_mode_capabilities(
        {
            "mode": "DIALOGUE",
            "dialogue_policy": "EXECUTION_PROPOSAL",
        },
        models,
    )

    assert (dialogue.allow_plan, dialogue.allow_model_tools) == (False, False)
    assert dialogue.models == models
    assert (executable.allow_plan, executable.allow_model_tools) == (True, True)
    assert draft.read_only and draft.allow_operator_catalog and draft.models == ()

    observations = {item.name for item in settings.observation_tools}

    def tool_names(capabilities):
        return {
            tool.name
            for tool in build_agent_tools(
                Path("unused.sqlite3"),
                settings.services,
                settings.observation_tools,
                models,
                object(),
                operator_catalog=settings.operator_catalog,
                capabilities=capabilities,
            )
        }

    facts = {"list_execution_facts", "get_execution_fact"}
    assert tool_names(dialogue) == {
        "submit_reply",
        "update_task",
        *facts,
        *observations,
    }
    assert tool_names(executable) == {
        "record_plan",
        "prepare_task_inputs",
        "submit_reply",
        "update_task",
        *facts,
        *observations,
        *(model.tool_name for model in models),
    }
    assert tool_names(draft) == {
        "submit_reply",
        "update_task",
        "list_operators",
        "get_operator",
        *facts,
        *observations,
    }


def test_current_turn_evidence_replaces_stale_observations() -> None:
    evidence = collect_turn_evidence(
        [
            {"type": "function_call", "call_id": "k1", "name": "read_evidence"},
            {
                "type": "function_call_output",
                "call_id": "k1",
                "output": {
                    "data": {
                        "items": [
                            {
                                "versionId": "version",
                                "unitId": "unit",
                                "checksum": "sha256:old",
                                "status": "OK", "bindingMatch": "EXACT",
                                "content": "old",
                                "knowledgeId": "card",
                            }
                        ],
                    }
                },
            },
            {"type": "function_call", "call_id": "k2", "name": "read_evidence"},
            {
                "type": "function_call_output",
                "call_id": "k2",
                "output": {
                    "data": {
                        "items": [
                            {
                                "versionId": "version",
                                "unitId": "unit",
                                "checksum": "sha256:old",
                                "status": "STALE",
                                "content": None,
                            }
                        ],
                    }
                },
            },
            {"type": "function_call", "call_id": "r1", "name": "inspect_data_version"},
            {
                "type": "function_call_output",
                "call_id": "r1",
                "output": {
                    "data": {
                        "status": "OK",
                        "datasetId": "dataset",
                        "versionId": "version",
                        "semanticId": "semantics",
                        "objects": [
                            {
                                "objectId": "object",
                                "role": "raw",
                                "checksum": "sha256:" + "a" * 64,
                                "mediaType": "text/csv",
                                "format": "csv",
                                "size": 12,
                            }
                        ],
                    }
                },
            },
            {"type": "function_call", "call_id": "r2", "name": "inspect_data_table"},
            {
                "type": "function_call_output",
                "call_id": "r2",
                "output": {
                    "data": {
                        "status": "OK",
                        "datasetId": "dataset",
                        "versionId": "version",
                        "semanticId": "semantics",
                        "objects": [
                            {
                                "objectId": "object",
                                "role": "raw",
                                "checksum": "sha256:" + "a" * 64,
                                "mediaType": "text/csv",
                                "format": "csv",
                                "size": 12,
                            }
                        ],
                        "columns": [],
                        "rows": [],
                    }
                },
            },
            {"type": "function_call", "call_id": "o1", "name": "get_operator"},
            {
                "type": "function_call_output",
                "call_id": "o1",
                "output": {
                    "operator_id": "table.profile",
                    "operator_version": "1.0.0",
                    "implementation_revision": "sha256:old-implementation",
                },
            },
            {"type": "function_call", "call_id": "o2", "name": "get_operator"},
            {
                "type": "function_call_output",
                "call_id": "o2",
                "output": {
                    "operator_id": "table.profile",
                    "operator_version": "1.0.0",
                    "implementation_revision": "sha256:implementation",
                },
            },
        ]
    )

    assert evidence.knowledge == {}
    assert evidence.data_versions["version"].format_id == "csv"
    assert evidence.operators == frozenset(
        {("table.profile", "1.0.0", "sha256:implementation")}
    )


@pytest.mark.parametrize(
    ("binding_match", "trusted"),
    [
        ("GENERAL", True),
        ("EXACT", True),
        ("NOT_CHECKED", False),
        ("MISMATCH", False),
        (None, False),
    ],
)
def test_knowledge_binding_match_gates_both_reply_and_turn_evidence(
    binding_match: str | None, trusted: bool
) -> None:
    item = {
        "versionId": "version", "unitId": "unit", "checksum": "sha256:new",
        "status": "OK", "content": "private knowledge", "bindingMatch": binding_match,
    }
    response = {"data": {"items": [item]}}
    control = {"knowledge_evidence": [["version", "unit", "sha256:old"]]}

    _record_read_evidence(control, response)
    evidence = collect_turn_evidence([
        {"type": "function_call", "call_id": "read", "name": "read_evidence"},
        {"type": "function_call_output", "call_id": "read", "output": response},
    ])

    assert control["knowledge_evidence"] == (
        [["version", "unit", "sha256:new"]] if trusted else []
    )
    assert evidence.knowledge == (
        {("version", "unit", "sha256:new"): "知识依据"} if trusted else {}
    )


def test_exact_retrieval_carries_binding_trust_into_historical_read() -> None:
    identity = {
        "versionId": "version", "unitId": "unit",
        "checksum": "sha256:" + "a" * 64,
    }
    retrieved = {"data": {"items": [{
        **identity, "status": "OK", "bindingMatch": "EXACT",
        "content": "retrieved evidence",
    }]}}
    historical = {"data": {"items": [{
        **identity, "status": "OK", "bindingMatch": "NOT_CHECKED",
        "knowledgeId": "card", "locator": {"title": "资料卡"},
        "content": "fully read evidence",
    }]}}
    control = {
        "knowledge_evidence": [], "knowledge_exact_candidates": [],
    }

    _record_retrieved_evidence(control, retrieved)
    _record_read_evidence(control, historical)
    evidence = collect_turn_evidence([
        {"type": "function_call", "call_id": "search", "name": "retrieve_evidence"},
        {"type": "function_call_output", "call_id": "search", "output": retrieved},
        {"type": "function_call", "call_id": "read", "name": "read_evidence"},
        {"type": "function_call_output", "call_id": "read", "output": historical},
    ])

    expected = (
        identity["versionId"], identity["unitId"], identity["checksum"],
    )
    assert control["knowledge_evidence"] == [list(expected)]
    assert control["knowledge_exact_candidates"] == []
    assert evidence.knowledge == {expected: "资料卡"}


@pytest.mark.parametrize("stale_first", [False, True])
def test_stale_checksum_does_not_discard_a_different_exact_read(stale_first) -> None:
    identity = {"versionId": "version", "unitId": "unit", "checksum": "sha256:" + "a" * 64}
    retrieved = {"data": {"items": [{
        **identity, "status": "OK", "bindingMatch": "EXACT", "content": "candidate",
    }]}}
    exact = {**identity, "status": "OK", "bindingMatch": "NOT_CHECKED", "content": "full evidence"}
    stale = {**identity, "checksum": "sha256:" + "b" * 64, "status": "STALE", "content": None}
    response = {"data": {"items": [stale, exact] if stale_first else [exact, stale]}}
    control = {"knowledge_evidence": [], "knowledge_exact_candidates": []}

    _record_retrieved_evidence(control, retrieved)
    _record_read_evidence(control, response)
    evidence = collect_turn_evidence([
        {"type": "function_call", "call_id": "search", "name": "retrieve_evidence"},
        {"type": "function_call_output", "call_id": "search", "output": retrieved},
        {"type": "function_call", "call_id": "read", "name": "read_evidence"},
        {"type": "function_call_output", "call_id": "read", "output": response},
    ])

    expected = (identity["versionId"], identity["unitId"], identity["checksum"])
    assert (control["knowledge_evidence"], evidence.knowledge) == (
        [list(expected)], {expected: "知识依据"},
    )


def test_unavailable_table_observation_is_not_trusted_as_data_evidence() -> None:
    evidence = collect_turn_evidence(
        [
            {"type": "function_call", "call_id": "table", "name": "inspect_data_table"},
            {
                "type": "function_call_output",
                "call_id": "table",
                "output": {
                    "data": {
                        "status": "NOT_QUERYABLE",
                        "versionId": "version",
                    }
                },
            },
        ]
    )

    assert evidence.data_versions == {}


def test_retry_required_tool_output_is_not_trusted_as_turn_evidence() -> None:
    evidence = collect_turn_evidence([
        {"type": "function_call", "call_id": "bad", "name": "get_operator"},
        {
            "type": "function_call_output",
            "call_id": "bad",
            "output": {
                "status": "RETRY_REQUIRED",
                "reason": "OPERATOR_UNKNOWN",
                "instruction": "Use an exact catalog identity.",
            },
        },
    ])

    assert evidence.operators == frozenset()


def test_observation_client_injects_context_and_validates_response(
    kernel_settings,
) -> None:
    settings = kernel_settings()
    definition = settings.observation_tools[0]
    service = settings.services_by_name[definition.service]
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": {"status": "OK"}})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ObservationClient(http_client)
    result = asyncio.run(
        client.invoke(
            definition,
            service,
            call_id="call-1",
            project_id="project-1",
            trace_id="trace-1",
            arguments={"version_id": "version"},
        )
    )
    asyncio.run(http_client.aclose())

    assert result == {"data": {"status": "OK"}}
    assert json.loads(requests[0].content) == {
        "callId": "call-1",
        "projectId": "project-1",
        "arguments": {"version_id": "version"},
        "actor": {
            "subject": "local-research-admin",
            "roles": ["researcher"],
        },
    }
    assert requests[0].headers["X-Trace-ID"] == "trace-1"


def test_observation_client_rejects_invalid_output(kernel_settings) -> None:
    settings = kernel_settings()
    definition = settings.observation_tools[0]
    service = settings.services_by_name[definition.service]
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"unexpected": []})
        )
    )
    with pytest.raises(UpstreamServiceError, match="OBSERVATION_RESPONSE_INVALID"):
        asyncio.run(
            ObservationClient(http_client).invoke(
                definition,
                service,
                call_id="call",
                project_id="project",
                trace_id="trace",
                arguments={"query": "water"},
            )
        )
    asyncio.run(http_client.aclose())


@pytest.mark.parametrize(
    "status,exception,code",
    [
        (404, AgentRunError, "OBSERVATION_RESOURCE_NOT_FOUND"),
        (422, AgentRunError, "OBSERVATION_ARGUMENTS_INVALID"),
        (503, UpstreamServiceError, "OBSERVATION_SERVICE_UNAVAILABLE"),
        (400, UpstreamServiceError, "OBSERVATION_RESPONSE_INVALID"),
        (401, UpstreamServiceError, "OBSERVATION_RESPONSE_INVALID"),
        (409, UpstreamServiceError, "OBSERVATION_RESPONSE_INVALID"),
        (500, UpstreamServiceError, "OBSERVATION_RESPONSE_INVALID"),
    ],
)
def test_observation_client_maps_http_failure(
    kernel_settings, status, exception, code
) -> None:
    settings = kernel_settings()
    definition = settings.observation_tools[0]
    service = settings.services_by_name[definition.service]
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            status, json={"code": "PRIVATE_RESPONSE", "detail": "PRIVATE_BODY"}
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(exception, match=code) as error:
        asyncio.run(
            ObservationClient(http_client).invoke(
                definition,
                service,
                call_id="call",
                project_id="project",
                trace_id="trace",
                arguments={"query": "water"},
            )
        )
    asyncio.run(http_client.aclose())
    assert str(error.value) == code
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__
    if status == 422:
        assert error.value.diagnostics == {
            "reason": "OBSERVATION_ARGUMENTS_INVALID",
        }
    assert len(requests) == 1


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadTimeout])
def test_observation_transport_failure_is_unavailable_without_retry(
    kernel_settings, failure
):
    settings = kernel_settings()
    definition = settings.observation_tools[0]
    requests = []

    def handler(request):
        requests.append(request)
        raise failure("PRIVATE_TRANSPORT_DETAIL", request=request)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(
        UpstreamServiceError, match="OBSERVATION_SERVICE_UNAVAILABLE"
    ) as error:
        asyncio.run(
            ObservationClient(http_client).invoke(
                definition,
                settings.services_by_name[definition.service],
                call_id="call",
                project_id="project",
                trace_id="trace",
                arguments={"query": "water"},
            )
        )
    asyncio.run(http_client.aclose())
    assert str(error.value) == "OBSERVATION_SERVICE_UNAVAILABLE"
    assert error.value.__cause__ is None
    assert len(requests) == 1


def test_execution_client_owns_job_protocol_transport(kernel_settings) -> None:
    service = kernel_settings().services_by_name["executor"]
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append((request.method, request.url.path))
        if request.url.path.endswith("/cancel"):
            return httpx.Response(202, json=_job("run-1"))
        request_id = (
            json.loads(request.content)["requestId"]
            if request.method == "POST"
            else "run-1"
        )
        return httpx.Response(
            202 if request.method == "POST" else 200,
            json=_job(request_id),
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ExecutionClient(http_client)
    request = JobRequestV6(
        request_id="run-1",
        project_id="project",
        model_id="model",
        model_version="1.0.0",
        model_checksum="sha256:0000000000000000000000000000000000000000000000000000000000000000",
        workflow_id="workflow",
        workflow_version="1.0.0",
        workflow_checksum="sha256:1111111111111111111111111111111111111111111111111111111111111111",
        executor_id="executor",
        arguments={},
        input_references={},
        deadline_at="2030-01-01T00:00:00Z",
    )
    assert asyncio.run(client.submit(service, request)).request_id == "run-1"
    assert asyncio.run(client.get(service, "job-1")).job_id == "job-1"
    asyncio.run(client.cancel(service, "job-1"))
    asyncio.run(http_client.aclose())
    assert paths == [
        ("POST", "/internal/execution/v6/jobs"),
        ("GET", "/internal/execution/v6/jobs/job-1"),
        ("POST", "/internal/execution/v6/jobs/job-1/cancel"),
    ]


def test_execution_client_rejects_invalid_protocol_response(kernel_settings) -> None:
    service = kernel_settings().services_by_name["executor"]
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"status": "QUEUED"})
        )
    )
    with pytest.raises(ExecutionClientError):
        asyncio.run(ExecutionClient(http_client).get(service, "job-1"))
    asyncio.run(http_client.aclose())


def test_provider_module_selects_model_and_settings(kernel_settings) -> None:
    deepseek = kernel_settings()
    deepseek_provider = build_agent_provider(deepseek)
    assert isinstance(deepseek_provider.model, OpenAIResponsesModel)
    assert build_model_settings(deepseek).extra_body is None
    assert build_model_settings(deepseek).max_tokens == 4096
    flash = replace(deepseek, agent_model="deepseek-flash", agent_protocol="responses")
    assert build_model_settings(flash).tool_choice == "required"
    assert build_model_settings(flash).reasoning.effort == "none"

    minimax = replace(
        deepseek, agent_provider="minimax", agent_protocol="chat_completions"
    )
    minimax_provider = build_agent_provider(minimax)
    assert isinstance(minimax_provider.model, OpenAIChatCompletionsModel)
    assert build_model_settings(minimax).extra_body == {"reasoning_split": True}
    assert build_model_settings(minimax).tool_choice == "required"
    assert build_model_settings(minimax).parallel_tool_calls is False
    assert build_model_settings(minimax).include_usage is True
    assert build_model_settings(minimax).max_tokens == 4096

    minimax_m3 = replace(minimax, agent_model="MiniMax-M3")
    assert build_model_settings(minimax_m3).extra_body == {
        "reasoning_split": False,
    }

    asyncio.run(deepseek_provider.close())
    asyncio.run(minimax_provider.close())


def test_minimax_m3_final_reply_strips_native_thinking() -> None:
    control = {"knowledge_evidence": []}

    encoded = _minimax_final_reply_json(
        "<think>internal reasoning</think>\n给用户的结论。",
        control,
    )

    assert encoded is not None
    assert json.loads(encoded)["conclusion"]["text"] == "给用户的结论。"
    assert _minimax_final_reply_json("<think>unfinished", control) is None


def test_minimax_binding_repair_instruction_is_actionable() -> None:
    instruction = _schema_repair_instruction(
        "retrieve_evidence",
        {"issues": [{"path": "/binding", "validator": "type"}]},
    )

    assert "binding to null" in instruction
    assert "project_id, dataset_id, version_id and semantic_id" in instruction
    assert _schema_repair_instruction("inspect_data_version", {}) == (
        "Call inspect_data_version once more with arguments matching its schema."
    )


def test_minimax_discovery_cursor_repair_instruction_requires_omission_or_digits() -> (
    None
):
    instruction = _schema_repair_instruction(
        "discover_datasets",
        {"issues": [{"path": "/after", "validator": "pattern"}]},
    )

    assert "omit after entirely" in instruction
    assert "JSON null" in instruction
    assert "digits only" in instruction


def test_supported_provider_read_description_requires_exact_refs(kernel_settings):
    settings = kernel_settings("knowledge")
    definition = next(
        tool for tool in settings.observation_tools
        if tool.name == "read_evidence"
    )

    deepseek_description = _observation_description(definition, "deepseek")
    minimax_description = _observation_description(definition, "minimax")

    assert "Completed retrieval refs only" in deepseek_description
    assert "Copy only exact" in minimax_description
    assert all(
        field in description
        for description in (deepseek_description, minimax_description)
        for field in ("version_id", "unit_id", "checksum")
    )


def test_deepseek_table_description_forbids_object_identity() -> None:
    definition = SimpleNamespace(
        name="inspect_data_table",
        description="Inspect a queryable table.",
    )

    description = _observation_description(definition, "deepseek")

    assert "Pass only version_id" in description
    assert "never pass object_id or dataset_id" in description


def test_read_evidence_repair_instruction_forbids_retrieval_fields() -> None:
    instruction = _schema_repair_instruction(
        "read_evidence",
        {"issues": [{"path": "/", "validator": "additionalProperties"}]},
    )

    assert "refs and optional budget only" in instruction
    assert "version_id, unit_id and checksum" in instruction
    assert "do not include query, scope, binding" in instruction


def test_table_repair_instruction_accepts_version_identity_only() -> None:
    instruction = _schema_repair_instruction(
        "inspect_data_table",
        {"issues": [{"path": "/", "validator": "additionalProperties"}]},
    )

    assert "containing version_id only" in instruction
    assert "completed inspect_data_version call" in instruction
    assert "do not include object_id, dataset_id" in instruction


def test_deepseek_observation_normalization_drops_only_unknown_schema_siblings() -> None:
    schema = {
        "type": "object",
        "properties": {
            "version_id": {"type": "string"},
            "progress_intent": {"type": "object"},
        },
        "required": ["version_id", "progress_intent"],
        "additionalProperties": False,
    }
    complete = {
        "version_id": "version-a",
        "progress_intent": {"objective": "检查字段"},
        "dataset_id": "provider-annotation",
    }

    assert _normalize_observation_arguments(
        complete,
        provider="deepseek",
        tool_name="inspect_data_table",
        accepts_budget=False,
        input_schema=schema,
    ) == {
        "version_id": "version-a",
        "progress_intent": {"objective": "检查字段"},
    }
    incomplete = {"version_id": "version-a", "dataset_id": "still-invalid"}
    assert _normalize_observation_arguments(
        incomplete,
        provider="deepseek",
        tool_name="inspect_data_table",
        accepts_budget=False,
        input_schema=schema,
    ) is incomplete


def test_minimax_general_retrieval_discards_inapplicable_binding() -> None:
    partial = {"dataset_id": "invented", "field_id": "f1"}

    assert _normalize_observation_arguments(
        {"query": "water", "scope": "general", "binding": partial},
        provider="minimax",
        tool_name="retrieve_evidence",
        accepts_budget=True,
    ) == {
        "query": "water",
        "scope": "general",
        "binding": None,
        "budget": {"max_units": 8, "max_chars": 16000},
    }
    assert (
        _normalize_observation_arguments(
            {"query": "water", "scope": "bound", "binding": partial},
            provider="minimax",
            tool_name="retrieve_evidence",
            accepts_budget=False,
        )["binding"]
        == partial
    )


@pytest.mark.parametrize(
    "cursor",
    [None, "", "  ", "null", "None", "undefined", "unset", "not set", "n/a", "未设置"],
)
def test_minimax_discovery_omits_unset_pagination_values(cursor) -> None:
    assert _normalize_observation_arguments(
        {"query": "monitoring", "limit": None, "after": cursor},
        provider="minimax",
        tool_name="discover_datasets",
        accepts_budget=False,
    ) == {"query": "monitoring"}
    supplied = {"query": "monitoring", "limit": None}
    assert (
        _normalize_observation_arguments(
            supplied,
            provider="deepseek",
            tool_name="discover_datasets",
            accepts_budget=False,
        )
        is supplied
    )


def test_minimax_discovery_preserves_real_and_invalid_cursors_for_validation() -> None:
    for cursor in ("20", "page-2"):
        assert _normalize_observation_arguments(
            {"query": "monitoring", "after": cursor},
            provider="minimax",
            tool_name="discover_datasets",
            accepts_budget=False,
        ) == {"query": "monitoring", "after": cursor}


def test_public_observation_diagnostics_match_frontend_issue_contract() -> None:
    assert _public_diagnostics(
        {
            "diagnostics": {
                "reason": "OBSERVATION_ARGUMENTS_INVALID",
                "issues": [
                    {
                        "path": "/after",
                        "validator": "pattern",
                        "observed_type": "str",
                    }
                ],
            }
        }
    ) == {
        "reason": "OBSERVATION_ARGUMENTS_INVALID",
        "issues": [{"path": "/after", "type": "pattern"}],
    }


def test_public_workflow_diagnostics_expose_only_stable_code_and_path() -> None:
    assert _public_diagnostics(
        {
            "diagnostics": {
                "kind": "WORKFLOW_VALIDATION",
                "code": "WORKFLOW_DRAFT_EVIDENCE_INVALID",
                "path": "/knowledge_refs/0",
                "private_detail": "must not be projected",
            }
        }
    ) == {
        "reason": "WORKFLOW_VALIDATION",
        "issues": [{
            "path": "/knowledge_refs/0",
            "type": "WORKFLOW_DRAFT_EVIDENCE_INVALID",
        }],
    }


def test_minimax_model_numeric_strings_are_normalized_by_exact_schema_type() -> None:
    schema = {
        "type": "object",
        "properties": {
            "threshold": {"type": "number", "exclusiveMinimum": 0},
            "count": {"type": "integer", "minimum": 1},
            "label": {"type": "string"},
        },
    }
    supplied = {
        "threshold": "3.5",
        "count": "12",
        "label": "3.5",
        "unknown": "8",
        "nonfinite": "1e400",
    }

    assert normalize_model_arguments(
        supplied,
        schema,
        provider="minimax",
    ) == {
        "threshold": 3.5,
        "count": 12,
        "label": "3.5",
        "unknown": "8",
        "nonfinite": "1e400",
    }
    assert (
        normalize_model_arguments(
            supplied,
            schema,
            provider="deepseek",
        )
        is supplied
    )
    assert normalize_model_tool_arguments(
        {"arguments": {"threshold": "3.5", "count": "12", "label": "x"}},
        schema,
        provider="minimax",
    ) == {"threshold": 3.5, "count": 12, "label": "x"}
    assert normalize_model_tool_arguments(
        {
            "arguments": json.dumps(
                {
                    "threshold": "3.5",
                    "count": "12",
                    "label": "x",
                }
            )
        },
        schema,
        provider="minimax",
    ) == {"threshold": 3.5, "count": 12, "label": "x"}
    schema_with_arguments = {
        "type": "object",
        "properties": {"arguments": {"type": "object"}},
    }
    wrapped = {"arguments": {"threshold": "3.5"}}
    assert (
        normalize_model_tool_arguments(
            wrapped,
            schema_with_arguments,
            provider="minimax",
        )
        == wrapped
    )


def test_minimax_repeated_identical_array_is_normalized_only_for_scalar_schema() -> (
    None
):
    schema = {
        "type": "object",
        "properties": {
            "versionId": {"type": "string"},
            "versions": {"type": "array", "items": {"type": "string"}},
            "optional": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
            },
        },
    }

    assert normalize_model_arguments(
        {
            "versionId": ["same", "same"],
            "versions": ["same", "same"],
            "optional": ["same", "same"],
        },
        schema,
        provider="minimax",
    ) == {
        "versionId": "same",
        "versions": ["same", "same"],
        "optional": "same",
    }
    differing = {"versionId": ["first", "second"]}
    assert (
        normalize_model_arguments(
            differing,
            schema,
            provider="minimax",
        )
        == differing
    )


def test_minimax_model_names_use_only_unique_schema_derived_aliases() -> None:
    schema = {
        "type": "object",
        "properties": {
            "workbookVersionId": {"type": "string"},
            "basin_boundaryVersionId": {"type": "string"},
            "extent.padding_meters": {"type": "number"},
            "valueField": {"type": "string"},
            "sourceFilterVersionId": {"type": "string"},
        },
        "additionalProperties": False,
    }

    assert normalize_model_arguments(
        {
            "workbook_version_id": "workbook",
            "basin_boundary_version_id": "basin",
            "extent_padding_meters": "10",
            "value_field": "level",
            "source-filter_version_id": "filter",
            "unknown_field": "preserved",
        },
        schema,
        provider="minimax",
    ) == {
        "workbookVersionId": "workbook",
        "basin_boundaryVersionId": "basin",
        "extent.padding_meters": 10,
        "valueField": "level",
        "sourceFilterVersionId": "filter",
        "unknown_field": "preserved",
    }


def test_minimax_nested_objects_follow_only_dotted_schema_paths() -> None:
    schema = {
        "type": "object",
        "properties": {
            "extent.padding_meters": {"type": "number"},
            "extent.layer": {"type": "string"},
            "workbookVersionId": {"type": "string"},
            "options": {
                "type": "object",
                "properties": {"mode": {"type": "string"}},
            },
        },
        "additionalProperties": False,
    }

    assert normalize_model_arguments(
        {
            "extent": {"padding_meters": "10", "layer": "boundary"},
            "workbook_version_id": "version",
            "options": {"mode": "strict"},
            "unknown": {"field": "preserved-for-validation"},
        },
        schema,
        provider="minimax",
    ) == {
        "extent.padding_meters": 10,
        "extent.layer": "boundary",
        "workbookVersionId": "version",
        "options": {"mode": "strict"},
        "unknown.field": "preserved-for-validation",
    }

    collision = normalize_model_arguments(
        {
            "extent.padding_meters": 10,
            "extent": {"paddingMeters": "11"},
        },
        schema,
        provider="minimax",
    )
    assert collision == {
        "extent.padding_meters": 10,
        "extent.paddingMeters": "11",
    }


def test_minimax_nested_array_items_follow_referenced_schema() -> None:
    schema = {
        "type": "object",
        "$defs": {
            "Step": {
                "type": "object",
                "properties": {
                    "step_id": {"type": "string"},
                    "retry_count": {"type": "integer"},
                },
                "additionalProperties": False,
            },
        },
        "properties": {
            "steps": {
                "type": "array",
                "items": {"$ref": "#/$defs/Step"},
            },
        },
    }

    assert normalize_model_arguments(
        {
            "steps": [{"stepId": "summary", "retryCount": "2"}],
        },
        schema,
        provider="minimax",
    ) == {
        "steps": [{"step_id": "summary", "retry_count": 2}],
    }


@pytest.mark.parametrize("provider", ["deepseek", "minimax"])
def test_executable_arguments_drop_activity_control_metadata(provider) -> None:
    schema = {
        "type": "object",
        "properties": {"versionId": {"type": "string"}},
        "required": ["versionId"],
        "additionalProperties": False,
    }

    assert normalize_model_tool_arguments(
        {
            "versionId": "version-observed",
            "progress_intent": {
                "capability": "groundwater.monitoring.trend",
                "objective": "计算监测趋势",
                "template_key": "groundwater.monitoring.trend",
            },
        },
        schema,
        provider=provider,
    ) == {"versionId": "version-observed"}


def test_executable_business_property_named_progress_intent_is_preserved() -> None:
    schema = {
        "type": "object",
        "properties": {"progress_intent": {"type": "string"}},
        "required": ["progress_intent"],
        "additionalProperties": False,
    }

    assert normalize_model_tool_arguments(
        {"progress_intent": "business-value"}, schema, provider="deepseek",
    ) == {"progress_intent": "business-value"}


def test_minimax_model_name_aliases_do_not_hide_ambiguity_or_duplicates() -> None:
    ambiguous_schema = {
        "type": "object",
        "properties": {
            "valueField": {"type": "string"},
            "value.field": {"type": "string"},
        },
    }
    ambiguous = {"value_field": "level"}
    assert (
        normalize_model_arguments(
            ambiguous,
            ambiguous_schema,
            provider="minimax",
        )
        == ambiguous
    )

    exact_and_alias = {"valueField": "level", "value_field": "other"}
    assert (
        normalize_model_arguments(
            exact_and_alias,
            {
                "type": "object",
                "properties": {
                    "valueField": {"type": "string"},
                },
            },
            provider="minimax",
        )
        == exact_and_alias
    )


def test_argument_drift_diagnostics_contain_paths_without_values() -> None:
    paths = _argument_difference_paths(
        {"input": {"versionId": "private-expected"}, "count": 2},
        {"input": {"version_id": "private-actual"}, "count": 3},
    )
    diagnostics = _processing_diagnostics(_PreparedArgumentsMismatchError(paths))

    assert diagnostics == {
        "reason": "TASK_PREPARATION_REQUIRED",
        "issues": [
            {"path": "/count", "type": "prepared_argument_mismatch"},
            {"path": "/input/versionId", "type": "prepared_argument_mismatch"},
            {"path": "/input/version_id", "type": "prepared_argument_mismatch"},
        ],
    }
    assert "private" not in json.dumps(diagnostics)


def test_minimax_single_ready_preparation_is_authoritative_for_execution() -> None:
    prepared = [
        ({"resource_id": "prepared", "threshold": 2}, "PINNED"),
    ]

    assert _select_prepared_arguments(
        {"resource_id": "rewritten", "threshold": 3},
        prepared,
        provider="minimax",
    ) == (0, prepared[0][0], "PINNED")


def test_prepared_argument_binding_never_guesses_between_candidates() -> None:
    prepared = [
        ({"resource_id": "first"}, "PINNED"),
        ({"resource_id": "second"}, "CURRENT_REQUIRED"),
    ]

    with pytest.raises(_PreparedArgumentsMismatchError) as failure:
        _select_prepared_arguments(
            {"resource_id": "rewritten"},
            prepared,
            provider="minimax",
        )

    assert failure.value.paths == ("/resource_id",)


def test_non_minimax_prepared_argument_binding_remains_exact() -> None:
    prepared = [({"resource_id": "prepared"}, "PINNED")]

    with pytest.raises(_PreparedArgumentsMismatchError):
        _select_prepared_arguments(
            {"resource_id": "rewritten"},
            prepared,
            provider="deepseek",
        )


@pytest.mark.parametrize(
    "value,expected",
    [
        (
            {"required": "present", "invented placeholder": "private-value"},
            ["MODEL_ARGUMENTS_INVALID:/invented placeholder:additionalProperties"],
        ),
        (
            {},
            ["MODEL_ARGUMENTS_INVALID:/required:required"],
        ),
    ],
)
def test_preparation_schema_diagnostics_name_paths_without_values(
    value,
    expected,
) -> None:
    schema = {
        "type": "object",
        "properties": {"required": {"type": "string"}},
        "required": ["required"],
        "additionalProperties": False,
    }
    with pytest.raises(JsonSchemaValidationError) as failure:
        jsonschema_validate(value, schema)

    diagnostics = _schema_blocking_reasons(failure.value)

    assert diagnostics == expected
    assert "private-value" not in json.dumps(diagnostics)


def test_explicit_structured_request_arguments_bind_preparation() -> None:
    request = {
        "target_tool": "run_summary",
        "arguments": {"resource_id": "model-rewritten"},
    }
    binding = {
        "tool": "run_summary",
        "arguments": {"resource_id": "user-version", "axis": "rows"},
    }

    assert (
        preparation_tool_module._explicit_request_arguments(
            request,
            binding,
        )
        == binding["arguments"]
    )
    assert (
        preparation_tool_module._explicit_request_arguments(
            {**request, "target_tool": "other_tool"},
            binding,
        )
        == request["arguments"]
    )


def _field_preparation_result(
    kernel_settings,
    monkeypatch,
    *,
    primary_fields,
    other_fields=(),
    field_value="level",
    identifier="name",
    allowed_types=("number",),
    primary_has_descriptor=True,
    primary_semantics=None,
    semantic_requirements=(),
    additional_field_requirements=(),
    extra_schema_properties=None,
    extra_arguments=None,
):
    settings = kernel_settings()
    extra_schema_properties = extra_schema_properties or {}
    extra_arguments = extra_arguments or {}
    schema = {
        "type": "object",
        "properties": {
            "primaryVersionId": {"type": "string"},
            "otherVersionId": {"type": "string"},
            "valueField": {"type": "string"},
            **extra_schema_properties,
        },
        "required": [
            "primaryVersionId", "otherVersionId", "valueField",
            *extra_schema_properties,
        ],
        "additionalProperties": False,
    }
    model = replace(
        settings.models[0],
        input_schema=schema,
        default_parameters={},
        data_input_parameters=("primaryVersionId", "otherVersionId"),
        field_requirements=(
            SimpleNamespace(
                parameter="valueField",
                input_parameter="primaryVersionId",
                identifier=identifier,
                allowed_types=allowed_types,
            ),
            *additional_field_requirements,
        ),
        semantic_requirements=tuple(semantic_requirements),
    )

    def save(_database, *, agent_run_id, tool_call_id, snapshot):
        validated = TaskPreparationEntryV1.model_validate(snapshot).model_dump()
        return {**validated, "preparation_id": "preparation", "revision": 1}

    monkeypatch.setattr(preparation_tool_module, "save_preparation", save)

    def inspect(request):
        body = json.loads(request.content)
        version_id = body["arguments"]["version_id"]
        data = {
            "status": "OK",
            "versionId": version_id,
            "currentVersion": version_id,
            "datasetId": f"dataset-{version_id}",
            "semanticId": f"semantics-{version_id}",
            "objects": [
                {
                    "objectId": f"object-{version_id}",
                    "role": "raw",
                    "mediaType": "text/csv",
                    "format": "csv",
                    "size": 8,
                    "checksum": "sha256:" + "a" * 64,
                }
            ],
        }
        if version_id == "primary" and primary_has_descriptor:
            data["descriptor"] = {"fields": list(primary_fields)}
            if primary_semantics is not None:
                data["semantics"] = primary_semantics
        elif version_id == "other":
            data["descriptor"] = {"fields": list(other_fields)}
        return httpx.Response(200, json={"data": data})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(inspect))
    tool = build_preparation_tool(
        settings.data_root / "kernel.sqlite3",
        observations=settings.observation_tools,
        services_by_name=settings.services_by_name,
        models=(model,),
        observation_client=ObservationClient(http_client),
    )
    context = SimpleNamespace(
        context={
            "agent_run_id": "run",
            "project_id": "p",
            "trace_id": "trace",
            "governance": {"knowledge_evidence": []},
        },
        tool_call_id="call",
    )
    result = asyncio.run(
        tool.on_invoke_tool(
            context,
            json.dumps(
                {
                    "target_tool": model.tool_name,
                    "arguments": {
                        "primaryVersionId": "primary",
                        "otherVersionId": "other",
                        "valueField": field_value,
                        **extra_arguments,
                    },
                }
            ),
        )
    )
    asyncio.run(http_client.aclose())
    return result


def test_preparation_field_lookup_is_isolated_to_its_exact_data_version(
    kernel_settings, monkeypatch
) -> None:
    result = _field_preparation_result(
        kernel_settings,
        monkeypatch,
        primary_fields=(
            {"fieldId": "f0", "name": "levl", "type": "double"},
        ),
        other_fields=(
            {"fieldId": "f0", "name": "level", "type": "double"},
        ),
    )

    assert result["status"] == "BLOCKED"
    assert result["blocking_reasons"] == ["FIELD_NOT_FOUND"]
    issue, = result["blocking_issues"]
    assert issue["code"] == "FIELD_NOT_FOUND"
    assert issue["input"] == "primaryVersionId"
    assert issue["parameter"] == "valueField"
    assert issue["candidates"] == [
        {"field_id": "f0", "name": "levl", "type": "double"}
    ]


def test_preparation_honors_declared_field_identifier_without_id_fallback(
    kernel_settings, monkeypatch
) -> None:
    result = _field_preparation_result(
        kernel_settings,
        monkeypatch,
        primary_fields=(
            {"fieldId": "f0", "name": "level", "type": "double"},
        ),
        field_value="f0",
        identifier="name",
    )

    assert result["status"] == "BLOCKED"
    assert result["blocking_reasons"] == ["FIELD_NOT_FOUND"]


def test_preparation_blocks_incompatible_field_type(
    kernel_settings, monkeypatch
) -> None:
    result = _field_preparation_result(
        kernel_settings,
        monkeypatch,
        primary_fields=(
            {"fieldId": "f0", "name": "level", "type": "bool"},
        ),
    )

    assert result["status"] == "BLOCKED"
    assert result["blocking_reasons"] == ["FIELD_TYPE_MISMATCH"]
    issue, = result["blocking_issues"]
    assert issue["code"] == "FIELD_TYPE_MISMATCH"
    assert issue["actual_type"] == "bool"
    assert issue["allowed_types"] == ["number"]


def test_preparation_marks_missing_field_descriptor_unknown_and_blocks(
    kernel_settings, monkeypatch
) -> None:
    result = _field_preparation_result(
        kernel_settings,
        monkeypatch,
        primary_fields=(),
        primary_has_descriptor=False,
    )

    assert result["status"] == "BLOCKED"
    assert result["blocking_reasons"] == ["FIELD_METADATA_UNAVAILABLE"]
    assert any(
        check["kind"] == "FIELD_EXISTS" and check["status"] == "UNKNOWN"
        for check in result["checks"]
    )


@pytest.mark.parametrize("spatial_first", [False, True])
def test_same_turn_preparation_candidates_survive_refresh_and_replay(kernel_settings, spatial_first):
    from platform_kernel.database import connect_database, utc_now
    from platform_kernel.interaction.persistence import create_session
    from platform_kernel.tasks.preparation import get_preparation, save_preparation

    settings = kernel_settings()
    database = settings.data_root / "kernel.sqlite3"
    session, agent = "preparation-session", "preparation-agent"
    create_session(database, session, settings.project_id)
    with connect_database(database) as connection:
        connection.execute("UPDATE sessions SET goal_revision=1 WHERE session_id=?", (session,))
        connection.execute(
            """INSERT INTO agent_runs(agent_run_id,session_id,project_id,goal_revision,
               provider,model,status,steps_json,input_tokens,output_tokens,total_tokens,
               trace_id,created_at,started_at)
               VALUES (?,?,?,1,'fixed','model','RUNNING','[]',0,0,0,'trace',?,?)""",
            (agent, session, settings.project_id, utc_now(), utc_now()),
        )

    base = settings.models[0]
    trend = replace(base, tool_name="prepare_trend", model_id="test.trend")
    spatial = replace(
        base, tool_name="prepare_spatial", model_id="test.spatial",
        input_schema={**base.input_schema, "required": ["resource_id", "xField", "yField"],
                      "properties": {**base.input_schema["properties"], "xField": {"type": "string"},
                                     "yField": {"type": "string"}}},
    )

    async def check_branches():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "data": {"status": "OK", "versionId": "monitoring", "currentVersion": "monitoring",
                     "datasetId": "observations", "semanticId": "semantics",
                     "descriptor": {"fields": [{"fieldId": "time", "name": "time", "type": "string"},
                                               {"fieldId": "level", "name": "level", "type": "double"}]},
                     "objects": [{"objectId": "monitoring-object", "role": "raw", "mediaType": "text/csv",
                                  "format": "csv", "size": 32, "checksum": "sha256:" + "a" * 64}]},
        }))) as client:
            tool = build_preparation_tool(
                database, observations=settings.observation_tools, services_by_name=settings.services_by_name,
                models=(trend, spatial), observation_client=ObservationClient(client),
            )
            branches = [(trend, "READY"), (spatial, "BLOCKED")]
            if spatial_first:
                branches.reverse()
            for revision, (model, status) in enumerate(branches, start=1):
                result = await tool.on_invoke_tool(
                    SimpleNamespace(context={"agent_run_id": agent, "project_id": settings.project_id,
                                             "trace_id": "trace", "governance": {"knowledge_evidence": []}},
                                    tool_call_id=f"preparation-{revision}"),
                    json.dumps({"target_tool": model.tool_name, "arguments": {"resource_id": "monitoring"}}),
                )
                assert result["status"] == status, result
                if status == "BLOCKED":
                    assert any(issue.get("path") in {"xField", "yField"} or issue.get("parameter") in {"xField", "yField"}
                               for issue in result["blocking_issues"]), result
                head = get_preparation(database, session, settings.project_id)
                assert head["revision"] == revision
                assert [(entry["target_id"], entry["status"]) for entry in head["entries"]] == [
                    (checked.model_id, state) for checked, state in branches[:revision]
                ]
            first, status = branches[0]
            replay = await tool.on_invoke_tool(
                SimpleNamespace(context={"agent_run_id": agent, "project_id": settings.project_id,
                                         "trace_id": "trace", "governance": {"knowledge_evidence": []}},
                                tool_call_id="preparation-1"),
                json.dumps({"target_tool": first.tool_name, "arguments": {"resource_id": "monitoring"}}),
            )
            assert (replay["target_id"], replay["status"], replay["revision"]) == (first.model_id, status, 1)
            assert get_preparation(database, session, settings.project_id) == head

    asyncio.run(check_branches())
    with connect_database(database) as connection:
        snapshots = connection.execute(
            "SELECT snapshot_json FROM task_preparation_revisions WHERE agent_run_id=? ORDER BY revision", (agent,),
        ).fetchall()
    assert [(entry["target_id"], entry["status"]) for row in snapshots
            for entry in json.loads(row["snapshot_json"])["entries"]] == (
        [("test.spatial", "BLOCKED"), ("test.trend", "READY")] if spatial_first
        else [("test.trend", "READY"), ("test.spatial", "BLOCKED")]
    )
    second = json.loads(snapshots[1]["snapshot_json"])
    with connect_database(database) as connection:
        # Simulate the head written by a deployment that stored only the latest check.
        connection.execute(
            "UPDATE task_preparations SET snapshot_json=?,status=? WHERE agent_run_id=?",
            (snapshots[1]["snapshot_json"], second["status"], agent),
        )
    current = get_preparation(database, session, settings.project_id)
    assert len(current["entries"]) == 2
    assert current["status"] == "BLOCKED"

    trend_entry = next(entry for entry in current["entries"] if entry["target_id"] == trend.model_id)
    blocked_trend = {**trend_entry, "status": "BLOCKED", "blocking_reasons": ["DATA_UNAVAILABLE"]}
    rechecked = save_preparation(
        database, agent_run_id=agent, tool_call_id="recheck-trend", snapshot=blocked_trend,
    )
    updated = get_preparation(database, session, settings.project_id)
    assert len(updated["entries"]) == 2
    assert {entry["status"] for entry in updated["entries"]} == {"BLOCKED"}
    with pytest.raises(ValueError, match="TASK_PREPARATION_REQUEST_CONFLICT"):
        save_preparation(database, agent_run_id=agent, tool_call_id="recheck-trend", snapshot=trend_entry)
    assert get_preparation(database, session, settings.project_id) == updated
    evidence = collect_turn_evidence([
        {"type": "function_call", "call_id": "latest", "name": "prepare_task_inputs"},
        {"type": "function_call", "call_id": "earlier", "name": "prepare_task_inputs"},
        {"type": "function_call_output", "call_id": "latest",
         "output": preparation_tool_module._public_result(rechecked)},
        {"type": "function_call_output", "call_id": "earlier",
         "output": preparation_tool_module._public_result({
             **trend_entry, "preparation_id": current["preparation_id"], "revision": 1,
         })},
    ])
    assert evidence.prepared_targets == {trend.model_id}
    assert not evidence.prepared_arguments[trend.model_id]

    with connect_database(database) as connection:
        connection.execute("UPDATE sessions SET goal_revision=2 WHERE session_id=?", (session,))
        connection.execute(
            """INSERT INTO agent_runs(agent_run_id,session_id,project_id,goal_revision,
               provider,model,status,steps_json,input_tokens,output_tokens,total_tokens,
               trace_id,created_at,started_at)
               SELECT 'next-turn',session_id,project_id,2,provider,model,'RUNNING',
               steps_json,0,0,0,trace_id,created_at,started_at FROM agent_runs WHERE agent_run_id=?""",
            (agent,),
        )
    stale = get_preparation(database, session, settings.project_id)
    assert stale["disposition"] == "STALE"
    assert stale["fresh_for_current_turn"] is False
    save_preparation(database, agent_run_id="next-turn", tool_call_id="new-check", snapshot=trend_entry)
    save_preparation(database, agent_run_id=agent, tool_call_id="late-check", snapshot=trend_entry)
    latest = get_preparation(database, session, settings.project_id)
    assert latest["agent_run_id"] == "next-turn"
    assert latest["entries"] == [trend_entry]
    assert latest["fresh_for_current_turn"] is True


def test_preparation_accepts_matching_field_and_preserves_snapshot_sections(
    kernel_settings, monkeypatch
) -> None:
    result = _field_preparation_result(
        kernel_settings,
        monkeypatch,
        primary_fields=(
            {"fieldId": "f7", "name": "level", "type": "double"},
        ),
    )

    assert result["status"] == "READY"
    assert set(result["data_references"]) == {
        "primaryVersionId",
        "otherVersionId",
    }
    assert result["parameters"] == {"valueField": "level"}
    assert result["parameter_sources"] == {"valueField": "PROPOSED"}
    assert result["knowledge_references"] == []
    assert any(
        check["kind"] == "FIELD_TYPE" and check["status"] == "PASS"
        for check in result["checks"]
    )


def test_preparation_distinguishes_equivalent_convertible_and_missing_field_units(
    kernel_settings, monkeypatch
) -> None:
    requirement = SimpleNamespace(
        kind="fieldUnit",
        input_parameter="primaryVersionId",
        field_parameter="valueField",
        expected_parameter="valueUnit",
        start_parameter=None,
        end_parameter=None,
    )
    equivalent = _field_preparation_result(
        kernel_settings,
        monkeypatch,
        primary_fields=(
            {"fieldId": "f7", "name": "level", "type": "double"},
        ),
        primary_semantics={"fields": {"f7": {"unit": "metres"}}},
        semantic_requirements=(requirement,),
        extra_schema_properties={"valueUnit": {"type": "string"}},
        extra_arguments={"valueUnit": "m"},
    )
    assert equivalent["status"] == "READY"
    assert any(
        check["kind"] == "FIELD_UNIT"
        and check["status"] == "PASS"
        and check["actual_unit"] == "metres"
        and check["expected_unit"] == "m"
        and check["relationship"] == "EQUIVALENT"
        for check in equivalent["checks"]
    )

    conversion_required = _field_preparation_result(
        kernel_settings,
        monkeypatch,
        primary_fields=(
            {"fieldId": "f7", "name": "level", "type": "double"},
        ),
        primary_semantics={"fields": {"f7": {"unit": "ft"}}},
        semantic_requirements=(requirement,),
        extra_schema_properties={"valueUnit": {"type": "string"}},
        extra_arguments={"valueUnit": "m"},
    )
    assert conversion_required["status"] == "BLOCKED"
    assert conversion_required["blocking_reasons"] == [
        "UNIT_CONVERSION_REQUIRED"
    ]
    assert any(
        check["kind"] == "FIELD_UNIT"
        and check["status"] == "FAIL"
        and check["relationship"] == "CONVERTIBLE"
        for check in conversion_required["checks"]
    )

    missing = _field_preparation_result(
        kernel_settings,
        monkeypatch,
        primary_fields=(
            {"fieldId": "f7", "name": "level", "type": "double"},
        ),
        primary_semantics={"fields": {"f7": {"meaning": "groundwater level"}}},
        semantic_requirements=(requirement,),
        extra_schema_properties={"valueUnit": {"type": "string"}},
        extra_arguments={"valueUnit": "m"},
    )
    assert missing["status"] == "READY"
    assert missing["blocking_reasons"] == []
    assert any(
        check["kind"] == "FIELD_UNIT" and check["status"] == "UNKNOWN"
        for check in missing["checks"]
    )


def test_preparation_blocks_known_unit_and_crs_mismatches(
    kernel_settings, monkeypatch
) -> None:
    requirements = (
        SimpleNamespace(
            kind="fieldUnit",
            input_parameter="primaryVersionId",
            field_parameter="valueField",
            expected_parameter="valueUnit",
            start_parameter=None,
            end_parameter=None,
        ),
        SimpleNamespace(
            kind="crs",
            input_parameter="primaryVersionId",
            field_parameter=None,
            expected_parameter="sourceCrs",
            start_parameter=None,
            end_parameter=None,
        ),
    )
    result = _field_preparation_result(
        kernel_settings,
        monkeypatch,
        primary_fields=(
            {"fieldId": "f7", "name": "level", "type": "double"},
        ),
        primary_semantics={
            "fields": {"f7": {"unit": "s"}},
            "crs": "EPSG:4326",
        },
        semantic_requirements=requirements,
        extra_schema_properties={
            "valueUnit": {"type": "string"},
            "sourceCrs": {"type": "string"},
        },
        extra_arguments={"valueUnit": "m", "sourceCrs": "EPSG:32650"},
    )

    assert result["status"] == "BLOCKED"
    assert result["blocking_reasons"] == ["UNIT_NOT_COMPARABLE", "CRS_MISMATCH"]
    assert [issue["code"] for issue in result["blocking_issues"]] == [
        "UNIT_NOT_COMPARABLE",
        "CRS_MISMATCH",
    ]


def test_preparation_validates_crs_identity_and_projected_metric_parameters(
    kernel_settings, monkeypatch
) -> None:
    requirements = (
        SimpleNamespace(
            kind="crs",
            input_parameter="primaryVersionId",
            field_parameter=None,
            expected_parameter="sourceCrs",
            parameter=None,
            crs_requirement=None,
            start_parameter=None,
            end_parameter=None,
        ),
        SimpleNamespace(
            kind="crsParameter",
            input_parameter=None,
            field_parameter=None,
            expected_parameter=None,
            parameter="targetCrs",
            crs_requirement="projectedMetric",
            start_parameter=None,
            end_parameter=None,
        ),
    )
    ready = _field_preparation_result(
        kernel_settings,
        monkeypatch,
        primary_fields=(
            {"fieldId": "f7", "name": "level", "type": "double"},
        ),
        primary_semantics={
            "fields": {"f7": {"unit": "m"}},
            "crs": "urn:ogc:def:crs:EPSG::4326",
        },
        semantic_requirements=requirements,
        extra_schema_properties={
            "sourceCrs": {"type": "string"},
            "targetCrs": {"type": "string"},
        },
        extra_arguments={
            "sourceCrs": "EPSG:4326",
            "targetCrs": "EPSG:32650",
        },
    )

    assert ready["status"] == "READY"
    assert any(
        check["kind"] == "CRS" and check["status"] == "PASS"
        for check in ready["checks"]
    )
    assert any(
        check["kind"] == "CRS_PARAMETER" and check["status"] == "PASS"
        for check in ready["checks"]
    )

    invalid = _field_preparation_result(
        kernel_settings,
        monkeypatch,
        primary_fields=(
            {"fieldId": "f7", "name": "level", "type": "double"},
        ),
        primary_semantics={
            "fields": {"f7": {"unit": "m"}},
            "crs": "not-a-crs",
        },
        semantic_requirements=requirements,
        extra_schema_properties={
            "sourceCrs": {"type": "string"},
            "targetCrs": {"type": "string"},
        },
        extra_arguments={
            "sourceCrs": "EPSG:4326",
            "targetCrs": "EPSG:4326",
        },
    )

    assert invalid["status"] == "BLOCKED"
    assert invalid["blocking_reasons"] == [
        "CRS_INVALID",
        "PROJECTED_METRIC_CRS_REQUIRED",
    ]


def test_preparation_checks_declared_vertical_basis_without_inferring_missing_facts(
    kernel_settings, monkeypatch
) -> None:
    requirement = SimpleNamespace(
        kind="verticalBasis",
        input_parameter="primaryVersionId",
        field_parameter=None,
        expected_parameter=None,
        parameter=None,
        crs_requirement=None,
        start_parameter=None,
        end_parameter=None,
        reference_field_parameter="valueField",
        depth_field_parameter="depthField",
        reference_meanings=("reference_elevation", "land_surface_elevation"),
        depth_meanings=("depth_to_water", "depth_below_reference_surface"),
    )
    depth_requirement = SimpleNamespace(
        parameter="depthField",
        input_parameter="primaryVersionId",
        identifier="name",
        allowed_types=("number",),
    )
    common = {
        "primary_fields": (
            {"fieldId": "elevation", "name": "level", "type": "double"},
            {"fieldId": "depth", "name": "depth", "type": "double"},
        ),
        "semantic_requirements": (requirement,),
        "additional_field_requirements": (depth_requirement,),
        "extra_schema_properties": {"depthField": {"type": "string"}},
        "extra_arguments": {"depthField": "depth"},
    }
    ready = _field_preparation_result(
        kernel_settings,
        monkeypatch,
        primary_semantics={
            "fields": {
                "elevation": {"meaning": "reference_elevation", "unit": "m"},
                "depth": {"meaning": "depth_to_water", "unit": "m"},
            },
            "verticalDatum": "CGCS2000 height datum",
        },
        **common,
    )
    assert ready["status"] == "READY"
    assert any(
        check["kind"] == "VERTICAL_BASIS" and check["status"] == "PASS"
        for check in ready["checks"]
    )

    swapped = _field_preparation_result(
        kernel_settings,
        monkeypatch,
        primary_semantics={
            "fields": {
                "elevation": {"meaning": "depth_to_water", "unit": "m"},
                "depth": {"meaning": "reference_elevation", "unit": "m"},
            },
            "verticalDatum": "CGCS2000 height datum",
        },
        **common,
    )
    assert swapped["status"] == "BLOCKED"
    assert swapped["blocking_reasons"] == ["VERTICAL_BASIS_MISMATCH"]

    unknown = _field_preparation_result(
        kernel_settings,
        monkeypatch,
        primary_semantics={
            "fields": {
                "elevation": {"meaning": "ground level", "unit": "m"},
                "depth": {"unit": "m"},
            }
        },
        **common,
    )
    assert unknown["status"] == "READY"
    assert any(
        check["kind"] == "VERTICAL_BASIS" and check["status"] == "UNKNOWN"
        for check in unknown["checks"]
    )


def test_preparation_checks_time_type_timezone_and_requested_range(
    kernel_settings, monkeypatch
) -> None:
    requirements = (
        SimpleNamespace(
            kind="timeField",
            input_parameter="primaryVersionId",
            field_parameter="valueField",
            expected_parameter=None,
            start_parameter=None,
            end_parameter=None,
        ),
        SimpleNamespace(
            kind="timeRange",
            input_parameter="primaryVersionId",
            field_parameter="valueField",
            expected_parameter=None,
            start_parameter="expectedStart",
            end_parameter="expectedEnd",
        ),
    )
    result = _field_preparation_result(
        kernel_settings,
        monkeypatch,
        primary_fields=(
            {"fieldId": "time", "name": "level", "type": "timestamp[ms]"},
        ),
        allowed_types=("date-time",),
        primary_semantics={
            "fields": {"time": {"meaning": "observation time"}},
            "timezone": "Asia/Shanghai",
            "timeRange": "2020-01-01/2020-12-31",
        },
        semantic_requirements=requirements,
        extra_schema_properties={
            "expectedStart": {"type": "string"},
            "expectedEnd": {"type": "string"},
        },
        extra_arguments={
            "expectedStart": "2019-12-31",
            "expectedEnd": "2020-06-30",
        },
    )

    assert result["status"] == "BLOCKED"
    assert result["blocking_reasons"] == ["TIME_RANGE_NOT_COVERED"]
    assert any(
        check["kind"] == "TIME_FIELD_PARSEABILITY" and check["status"] == "PASS"
        for check in result["checks"]
    )
    assert any(
        check["kind"] == "TIMEZONE" and check["status"] == "PASS"
        for check in result["checks"]
    )
    assert any(
        check["kind"] == "TIME_RANGE" and check["status"] == "FAIL"
        for check in result["checks"]
    )


def test_preparation_keeps_missing_crs_timezone_and_range_unknown(
    kernel_settings, monkeypatch
) -> None:
    requirements = (
        SimpleNamespace(
            kind="crs",
            input_parameter="primaryVersionId",
            field_parameter=None,
            expected_parameter=None,
            start_parameter=None,
            end_parameter=None,
        ),
        SimpleNamespace(
            kind="timeField",
            input_parameter="primaryVersionId",
            field_parameter="valueField",
            expected_parameter=None,
            start_parameter=None,
            end_parameter=None,
        ),
        SimpleNamespace(
            kind="timeRange",
            input_parameter="primaryVersionId",
            field_parameter="valueField",
            expected_parameter=None,
            start_parameter="expectedStart",
            end_parameter="expectedEnd",
        ),
    )
    result = _field_preparation_result(
        kernel_settings,
        monkeypatch,
        primary_fields=(
            {"fieldId": "time", "name": "level", "type": "timestamp[ms]"},
        ),
        allowed_types=("date-time",),
        primary_semantics={"fields": {"time": {"meaning": "observation_time"}}},
        semantic_requirements=requirements,
        extra_schema_properties={
            "expectedStart": {"type": "string"},
            "expectedEnd": {"type": "string"},
        },
        extra_arguments={
            "expectedStart": "2020-01-01",
            "expectedEnd": "2020-12-31",
        },
    )

    assert result["status"] == "READY"
    assert result["blocking_reasons"] == []
    statuses = {
        check["kind"]: check["status"]
        for check in result["checks"]
        if check["kind"] in {"CRS", "TIMEZONE", "TIME_RANGE"}
    }
    assert statuses == {
        "CRS": "UNKNOWN",
        "TIMEZONE": "UNKNOWN",
        "TIME_RANGE": "UNKNOWN",
    }


def test_preparation_tool_returns_path_diagnostics_for_extra_model_argument(
    kernel_settings,
    monkeypatch,
) -> None:
    settings = kernel_settings()
    captured = []

    def save(_database, *, agent_run_id, tool_call_id, snapshot):
        captured.append((agent_run_id, tool_call_id, snapshot))
        return {
            **snapshot,
            "preparation_id": "preparation",
            "revision": 1,
        }

    monkeypatch.setattr(preparation_tool_module, "save_preparation", save)
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(
                AssertionError(f"unexpected request: {request.url}")
            )
        )
    )
    tool = build_preparation_tool(
        settings.data_root / "kernel.sqlite3",
        observations=settings.observation_tools,
        services_by_name=settings.services_by_name,
        models=settings.models,
        observation_client=ObservationClient(http_client),
        provider="minimax",
    )
    context = SimpleNamespace(
        context={"agent_run_id": "run", "project_id": "p", "trace_id": "trace"},
        tool_call_id="call",
    )

    result = asyncio.run(
        tool.on_invoke_tool(
            context,
            json.dumps(
                {
                    "target_tool": "run_summary",
                    "arguments": {
                        "resource_id": "version",
                        "invented": "private-value",
                    },
                }
            ),
        )
    )
    asyncio.run(http_client.aclose())

    assert result["blocking_reasons"] == [
        "MODEL_ARGUMENTS_INVALID:/invented:additionalProperties"
    ]
    assert result["blocking_issues"] == [
        {
            "code": "MODEL_ARGUMENTS_INVALID",
            "kind": "INVALID_PARAMETER",
            "parameter": "invented",
            "path": "/invented",
            "validator": "additionalProperties",
        }
    ]
    assert captured[0][0:2] == ("run", "call")
    assert "private-value" not in json.dumps(result["blocking_reasons"])


def test_preparation_issue_preserves_colons_in_parameter_name(
    kernel_settings,
    monkeypatch,
) -> None:
    settings = kernel_settings()

    def save(_database, *, agent_run_id, tool_call_id, snapshot):
        return {**snapshot, "preparation_id": "preparation", "revision": 1}

    model = settings.models[0]
    model = replace(
        model,
        input_schema={
            "type": "object",
            "properties": {"value:field": {"type": "string"}},
            "required": ["value:field"],
            "additionalProperties": False,
        },
        data_input_parameters=(),
    )
    monkeypatch.setattr(preparation_tool_module, "save_preparation", save)
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(500))
    )
    tool = build_preparation_tool(
        settings.data_root / "kernel.sqlite3",
        observations=settings.observation_tools,
        services_by_name=settings.services_by_name,
        models=(model,),
        observation_client=ObservationClient(http_client),
    )
    context = SimpleNamespace(
        context={"agent_run_id": "run", "project_id": "p", "trace_id": "trace"},
        tool_call_id="call",
    )

    result = asyncio.run(
        tool.on_invoke_tool(
            context,
            json.dumps(
                {
                    "target_tool": model.tool_name,
                    "arguments": {},
                }
            ),
        )
    )
    asyncio.run(http_client.aclose())

    assert result["blocking_issues"] == [
        {
            "code": "MODEL_ARGUMENTS_INVALID",
            "kind": "MISSING_PARAMETER",
            "parameter": "value:field",
            "path": "/value:field",
            "validator": "required",
        }
    ]


def test_injected_model_does_not_build_provider(kernel_settings, monkeypatch) -> None:
    def fail_provider(_settings):
        raise AssertionError("provider factory called")

    monkeypatch.setattr(
        "platform_kernel.composition.build_agent_provider_pool", fail_provider
    )
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(500))
    )
    model = ScriptedModel([])
    application = PlatformApplication(
        settings=kernel_settings("knowledge"),
        model=model,
        client=http_client,
    )
    assert application.agent_runtime.sdk_execution.model is model
    asyncio.run(http_client.aclose())


def test_application_closes_owned_provider_but_not_injected_http_client(
    kernel_settings, monkeypatch
) -> None:
    class ProviderPool:
        def __init__(self):
            self.model = ScriptedModel([])
            self.closed = False

        async def close(self):
            self.closed = True

    provider = ProviderPool()
    monkeypatch.setattr(
        "platform_kernel.composition.build_agent_provider_pool",
        lambda _settings: provider,
    )
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(500))
    )
    application = PlatformApplication(
        settings=kernel_settings("knowledge"),
        client=http_client,
    )

    asyncio.run(application.close())

    assert provider.closed
    assert not http_client.is_closed
    asyncio.run(http_client.aclose())


def test_runtime_reports_failed_dispatcher_and_propagates_error() -> None:
    recovered = []

    class Workflow:
        def recover_interrupted(self):
            recovered.append("workflow")

        async def tick(self):
            yield WorkflowTickResult(0, ())

    class Agent:
        def prune_traces(self):
            return 0

        def recover_interrupted(self):
            recovered.append("agent")

        def reply_waiting_runs(self):
            return ()

        def execution_waiting_runs(self):
            return ()

    class Interaction:
        def has_pending_decision(self):
            return True

        def requested_routes(self, _limit=None):
            return ()

        def requested_starts(self, _limit=None):
            return ()

        def prepare_recovery(self):
            recovered.append("interaction")
            return ()

        async def recover_control_proposals(self):
            return None

        async def tick(self):
            raise RuntimeError("dispatcher failed")

    async def exercise() -> None:
        class Router:
            async def route(self, **_kwargs):
                raise AssertionError("intent router must not run")

        runtime = KernelRuntime(
            Interaction(), Agent(), Workflow(), Router(), task_concurrency=8
        )
        await runtime.start()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert recovered == ["workflow", "agent", "interaction"]
        assert not runtime.is_ready()
        with pytest.raises(RuntimeError, match="dispatcher failed"):
            await runtime.stop()

    asyncio.run(exercise())


def test_application_closes_owned_http_client_after_dispatcher_failure(
    kernel_settings,
) -> None:
    application = PlatformApplication(
        settings=kernel_settings("knowledge"),
        model=ScriptedModel([]),
    )
    owned_client = application.client

    async def fail_tick():
        raise RuntimeError("dispatcher failed")

    application.interaction.tick = fail_tick
    application.interaction.has_pending_decision = lambda: True

    async def exercise() -> None:
        await application.start()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not application.is_ready()
        with pytest.raises(RuntimeError, match="dispatcher failed"):
            await application.close()
        assert owned_client.is_closed

    asyncio.run(exercise())
