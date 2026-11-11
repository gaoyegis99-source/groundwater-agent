"""Deterministic offline Agent invariant replays over the real Kernel runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sys
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
from agents.testing import ModelStep, ScriptedModel, function_call

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
for _source_directory in (
    REPOSITORY_ROOT,
    REPOSITORY_ROOT / "kernel/src",
    REPOSITORY_ROOT / "packages/workflow-compiler/src",
):
    if str(_source_directory) not in sys.path:
        sys.path.insert(0, str(_source_directory))

from kernel.tests.catalog_testing import authoring_fixture  # noqa: E402
from kernel.tests.reply_testing import submit_reply  # noqa: E402
from platform_kernel.agent_runtime.tracing import (  # noqa: E402
    install_trace_processors,
)
from platform_kernel.composition import PlatformApplication  # noqa: E402
from platform_kernel.database import connect_database  # noqa: E402
from platform_kernel.settings import (  # noqa: E402
    ExecutableModelDefinition,
    IntentRouterSettings,
    KernelSettings,
    ObservationToolDefinition,
    ServiceBinding,
)
from platform_kernel.settings_types import (  # noqa: E402
    ExecutableModelFieldRequirement,
)


class AgentReplayRefusal(ValueError):  # noqa: N818 - stable CLI refusal family
    """Stable offline-replay boundary refusal suitable for CLI automation."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _checksum(value: object) -> str:
    return _sha256_bytes(_canonical_json(value))


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _load_case_index(path: Path) -> tuple[dict[str, dict[str, object]], str]:
    try:
        encoded = path.read_bytes()
        document = json.loads(encoded)
    except (OSError, ValueError) as error:
        raise AgentReplayRefusal("AGENT_REPLAY_CASES_INVALID") from error
    if not isinstance(document, dict) or set(document) != {"schemaVersion", "cases"}:
        raise AgentReplayRefusal("AGENT_REPLAY_CASES_INVALID")
    if document["schemaVersion"] != "agent-replay-cases.v1":
        raise AgentReplayRefusal("AGENT_REPLAY_CASES_INVALID")
    cases = document["cases"]
    if not isinstance(cases, dict) or not cases:
        raise AgentReplayRefusal("AGENT_REPLAY_CASES_INVALID")
    normalized: dict[str, dict[str, object]] = {}
    expected_keys = {
        "description",
        "expectedConfirmationCount",
        "expectedWorkflowRunCount",
        "invariants",
    }
    for case_id, definition in cases.items():
        if (
            not isinstance(case_id, str)
            or not case_id
            or not isinstance(definition, dict)
            or set(definition) != expected_keys
            or not isinstance(definition["description"], str)
            or not definition["description"]
            or type(definition["expectedConfirmationCount"]) is not int
            or definition["expectedConfirmationCount"] < 0
            or type(definition["expectedWorkflowRunCount"]) is not int
            or definition["expectedWorkflowRunCount"] < 0
            or not isinstance(definition["invariants"], list)
            or not definition["invariants"]
            or any(
                not isinstance(item, str) or not item
                for item in definition["invariants"]
            )
            or len(set(definition["invariants"])) != len(definition["invariants"])
        ):
            raise AgentReplayRefusal("AGENT_REPLAY_CASES_INVALID")
        normalized[case_id] = definition
    return normalized, _sha256_bytes(encoded)


def _observation_tool(
    name: str,
    description: str,
    service: str,
    path: str,
    input_schema: dict[str, object],
) -> ObservationToolDefinition:
    output_schema: dict[str, object] = {"type": "object"}
    return ObservationToolDefinition(
        name,
        description,
        service,
        path,
        input_schema,
        output_schema,
        _checksum(input_schema),
        _checksum(output_schema),
        10,
    )


def _settings(
    data_root: Path,
    *,
    provider: str = "deepseek",
    model_transform: Callable[[ExecutableModelDefinition], ExecutableModelDefinition]
    | None = None,
) -> KernelSettings:
    inspect_schema: dict[str, object] = {
        "type": "object",
        "properties": {"version_id": {"type": "string"}},
        "required": ["version_id"],
        "additionalProperties": False,
    }
    read_schema: dict[str, object] = {
        "type": "object",
        "properties": {"refs": {"type": "array", "items": {"type": "object"}}},
        "required": ["refs"],
        "additionalProperties": False,
    }
    model_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "resource_id": {"type": "string"},
            "axis": {"type": "string"},
        },
        "required": ["resource_id"],
        "additionalProperties": False,
    }
    model = ExecutableModelDefinition(
        model_id="fixture.summary",
        model_version="1.1.0",
        model_checksum="sha256:" + "0" * 64,
        workflow_id="fixture.summary",
        workflow_version="1.0.0",
        tool_name="run_summary",
        tool_description="Run summary statistics.",
        input_schema=model_schema,
        default_parameters={"axis": "columns"},
        execution_service="executor",
        executor_id="workflow-dag",
        deadline_seconds=60,
        workflow_checksum="sha256:" + "1" * 64,
        input_schema_checksum=_checksum(model_schema),
        final_outputs=("summary",),
        data_input_parameters=("resource_id",),
    )
    if model_transform is not None:
        model = model_transform(model)
    catalog_manifest, catalog = authoring_fixture()
    services = tuple(
        ServiceBinding(name, f"http://{name}-service:8000")
        for name in ("data", "knowledge-next", "executor")
    )
    data_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return KernelSettings(
        data_root=data_root,
        product_id="fixture-product",
        project_id="agent-replay",
        system_prompt="Offline deterministic Agent replay.",
        services=services,
        observation_tools=(
            _observation_tool(
                "inspect_data_version",
                "Inspect exact Data version metadata.",
                "data",
                "/internal/tools/v3/inspect_data_version",
                inspect_schema,
            ),
            _observation_tool(
                "read_evidence",
                "Read exact product knowledge units.",
                "knowledge-next",
                "/internal/tools/v4/read_evidence",
                read_schema,
            ),
        ),
        models=(model,),
        execution_cases=(),
        operator_catalog=catalog,
        operator_catalog_digest=str(catalog_manifest["operatorCatalogDigest"]),
        manifest_checksum="sha256:" + "2" * 64,
        intent_router=IntentRouterSettings(
            provider="deepseek",
            protocol="responses",
            base_url="http://intent-router.test/v1",
            model="scripted-router",
            context_window_tokens=8192,
            max_output_tokens=256,
            request_timeout_seconds=120,
            segment_timeout_seconds=300,
            api_key="offline-router-key",
        ),
        agent_provider=provider,
        agent_protocol="chat_completions" if provider == "minimax" else "responses",
        agent_base_url="http://agent.test/v1",
        agent_model="scripted-agent",
        agent_context_window_tokens=131072,
        agent_max_output_tokens=4096,
        agent_request_timeout_seconds=120,
        agent_segment_timeout_seconds=300,
        agent_api_key="offline-agent-key",
    )


def _data_document(
    version_id: str,
    *,
    fields: Sequence[dict[str, object]] | None = None,
) -> dict[str, object]:
    document: dict[str, object] = {
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
    if fields is not None:
        document["descriptor"] = {"fields": list(fields)}
    return document


def _read_evidence_document() -> dict[str, object]:
    return {
        "status": "OK",
        "items": [
            {
                "status": "OK",
                "bindingMatch": "EXACT",
                "versionId": "knowledge-version-1",
                "unitId": "unit-2",
                "knowledgeId": "fixture-method",
                "locator": {"title": "地下水方法说明"},
                "content": "覆盖范围是点集凸包，水头单位为米。",
                "checksum": "sha256:" + "b" * 64,
            }
        ],
    }


def _request_arguments(request: httpx.Request) -> dict[str, object]:
    try:
        body = json.loads(request.content)
    except (UnicodeError, ValueError) as error:
        raise AssertionError("OFFLINE_FIXTURE_REQUEST_INVALID") from error
    arguments = body.get("arguments") if isinstance(body, dict) else None
    if not isinstance(arguments, dict):
        raise AssertionError("OFFLINE_FIXTURE_ARGUMENTS_INVALID")
    return arguments


class _OfflineServices:
    def __init__(
        self,
        *,
        fields: Sequence[dict[str, object]] | None = None,
        execute: bool = False,
    ) -> None:
        self.fields = fields
        self.execute = execute
        self.requests: list[tuple[str, str, str]] = []
        self.executor_request_id: str | None = None
        self.definition_set_digest: str | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.host, request.url.path))
        if request.url.path.endswith("inspect_data_version"):
            version_id = _request_arguments(request).get("version_id")
            if not isinstance(version_id, str) or not version_id:
                raise AssertionError("OFFLINE_FIXTURE_VERSION_INVALID")
            return httpx.Response(
                200,
                json={"data": _data_document(version_id, fields=self.fields)},
            )
        if request.url.path.endswith("read_evidence"):
            return httpx.Response(200, json={"data": _read_evidence_document()})
        if (
            self.execute
            and request.url.host == "executor-service"
            and request.url.path == "/internal/health/ready"
        ):
            return httpx.Response(
                200,
                json={
                    "status": "UP",
                    "definitionSetDigest": self.definition_set_digest,
                },
            )
        if (
            self.execute
            and request.url.host == "executor-service"
            and request.method == "POST"
            and request.url.path.endswith("/jobs")
        ):
            body = json.loads(request.content)
            self.executor_request_id = body["requestId"]
            return httpx.Response(202, json=self._job("QUEUED"))
        if (
            self.execute
            and request.url.host == "executor-service"
            and request.method == "GET"
            and "/jobs/" in request.url.path
        ):
            return httpx.Response(200, json=self._job("SUCCEEDED"))
        raise AssertionError(
            f"OFFLINE_REPLAY_UNEXPECTED_HTTP:{request.method}:{request.url.host}:"
            f"{request.url.path}"
        )

    def _job(self, status: str) -> dict[str, object]:
        if self.executor_request_id is None:
            raise AssertionError("OFFLINE_EXECUTOR_REQUEST_ID_MISSING")
        return {
            "jobId": "offline-job-1",
            "requestId": self.executor_request_id,
            "status": status,
            "stage": status,
            "error": None,
            "outputs": [],
            "serviceVersion": "offline-fixture-v1",
            "createdAt": None,
            "startedAt": None,
            "finishedAt": None,
        }


def _application(
    data_root: Path,
    steps: Sequence[ModelStep],
    *,
    provider: str = "deepseek",
    fields: Sequence[dict[str, object]] | None = None,
    execute: bool = False,
    model_transform: Callable[[ExecutableModelDefinition], ExecutableModelDefinition]
    | None = None,
) -> tuple[PlatformApplication, ScriptedModel, httpx.AsyncClient, _OfflineServices]:
    settings = _settings(
        data_root,
        provider=provider,
        model_transform=model_transform,
    )
    services = _OfflineServices(fields=fields, execute=execute)
    services.definition_set_digest = settings.definition_set_digest
    client = httpx.AsyncClient(transport=httpx.MockTransport(services))
    model = ScriptedModel(list(steps))
    application = PlatformApplication(
        settings=settings,
        model=model,
        intent_router_model=ScriptedModel([]),
        client=client,
    )
    return application, model, client, services


def _inspect_step(version_id: str = "resource-1") -> ModelStep:
    return ModelStep(
        output=[
            function_call(
                "inspect_data_version",
                {"version_id": version_id},
                call_id=f"inspect-{version_id}",
            )
        ]
    )


def _prepare_step(arguments: dict[str, object]) -> ModelStep:
    return ModelStep(
        output=[
            function_call(
                "prepare_task_inputs",
                {"target_tool": "run_summary", "arguments": arguments},
                call_id="prepare-summary",
            )
        ]
    )


def _plan_step() -> ModelStep:
    return ModelStep(
        output=[
            function_call(
                "record_plan",
                {
                    "goal": "执行摘要分析",
                    "steps": [
                        {
                            "step_id": "summary",
                            "tool": "run_summary",
                            "purpose": "执行已准备的摘要分析",
                        }
                    ],
                    "reason": "用户明确要求执行。",
                },
                call_id="plan",
            )
        ]
    )


def _execute_step(arguments: dict[str, object]) -> ModelStep:
    return ModelStep(
        output=[
            function_call(
                "run_summary",
                arguments,
                call_id="run-summary",
            )
        ]
    )


def _citation(unit_id: str = "unit-2") -> dict[str, str]:
    return {
        "version_id": "knowledge-version-1",
        "unit_id": unit_id,
        "checksum": "sha256:" + "b" * 64,
    }


def _read_step() -> ModelStep:
    return ModelStep(
        output=[
            function_call(
                "read_evidence",
                {"refs": [_citation()]},
                call_id="read-evidence",
            )
        ]
    )


def _counts(application: PlatformApplication) -> dict[str, int]:
    with connect_database(application.database) as connection:
        return {
            "actions": connection.execute(
                "SELECT COUNT(*) FROM agent_actions"
            ).fetchone()[0],
            "confirmations": connection.execute(
                "SELECT COUNT(*) FROM confirmations"
            ).fetchone()[0],
            "providerRequests": connection.execute(
                "SELECT COUNT(*) FROM provider_requests"
            ).fetchone()[0],
            "workflowRuns": connection.execute(
                "SELECT COUNT(*) FROM workflow_runs"
            ).fetchone()[0],
        }


def _assertions(**conditions: bool) -> dict[str, str]:
    return {
        name: "PASS" if condition else "FAIL" for name, condition in conditions.items()
    }


def _snapshot(
    case_id: str,
    application: PlatformApplication,
    *,
    run_status: str,
    assertions: dict[str, str],
    facts: dict[str, object],
) -> dict[str, object]:
    return {
        "schemaVersion": "agent-replay-snapshot.v1",
        "caseId": case_id,
        "runStatus": run_status,
        "counts": _counts(application),
        "assertions": assertions,
        "facts": facts,
    }


async def _dialogue_no_execution(root: Path) -> dict[str, object]:
    application, model, client, services = _application(
        root,
        [ModelStep(output=[submit_reply("水头是相对统一基准面的水面高程。")])],
    )
    try:
        session_id = application.interaction.create_session().session.session_id
        view = await application.interaction.turn(
            session_id,
            "请解释地下水水头。",
            dialogue_policy="READ_ONLY",
        )
        run = application.agent_runtime.get(view.session.agent_run_id or "")
        counts = _counts(application)
        message = None if run.output is None else run.output.message
        return _snapshot(
            "dialogue-no-execution",
            application,
            run_status=run.status,
            assertions=_assertions(
                DIALOGUE_COMPLETES=run.status == "COMPLETE",
                DIALOGUE_NO_CONFIRMATION=counts["confirmations"] == 0,
                DIALOGUE_NO_WORKFLOW_RUN=counts["workflowRuns"] == 0,
            ),
            facts={
                "agentSteps": list(view.agent_steps),
                "message": message,
                "modelCalls": len(model.calls),
                "downstreamRequests": len(services.requests),
            },
        )
    finally:
        await client.aclose()


async def _execution_proposal_confirmation(root: Path) -> dict[str, object]:
    arguments = {"resource_id": "resource-1"}
    application, model, client, services = _application(
        root,
        [
            _inspect_step(),
            _prepare_step(arguments),
            _plan_step(),
            _execute_step(arguments),
        ],
    )
    try:
        session_id = application.interaction.create_session().session.session_id
        view = await application.interaction.turn(
            session_id,
            "请明确执行 resource-1 的摘要分析。",
            dialogue_policy="EXECUTION_PROPOSAL",
        )
        run = application.agent_runtime.get(view.session.agent_run_id or "")
        confirmation = view.session.confirmation
        counts = _counts(application)
        action_tools = (
            []
            if confirmation is None
            else [action.tool for action in confirmation.actions]
        )
        executor_requests = [
            item for item in services.requests if item[1] == "executor-service"
        ]
        return _snapshot(
            "execution-proposal-confirmation",
            application,
            run_status=run.status,
            assertions=_assertions(
                EXECUTION_STOPS_AT_CONFIRMATION=(
                    run.status == "WAITING_CONFIRMATION"
                    and confirmation is not None
                    and confirmation.status == "PENDING"
                ),
                CONFIRMATION_HAS_PREPARED_ACTION=(
                    action_tools == ["run_summary"] and counts["actions"] == 1
                ),
                CONFIRMATION_PRECEDES_WORKFLOW_RUN=(
                    counts["confirmations"] == 1
                    and counts["workflowRuns"] == 0
                    and not executor_requests
                ),
            ),
            facts={
                "actionTools": action_tools,
                "agentSteps": list(view.agent_steps),
                "confirmationStatus": (
                    None if confirmation is None else confirmation.status
                ),
                "modelCalls": len(model.calls),
            },
        )
    finally:
        await client.aclose()


def _missing_field_model(
    model: ExecutableModelDefinition,
) -> ExecutableModelDefinition:
    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "resource_id": {"type": "string"},
            "axis": {"type": "string"},
            "column": {"type": "string"},
        },
        "required": ["resource_id", "column"],
        "additionalProperties": False,
    }
    return replace(
        model,
        input_schema=schema,
        input_schema_checksum=_checksum(schema),
        field_requirements=(
            ExecutableModelFieldRequirement(
                parameter="column",
                input_parameter="resource_id",
                identifier="name",
                allowed_types=("number",),
            ),
        ),
    )


async def _missing_field_correction(root: Path) -> dict[str, object]:
    arguments = {"resource_id": "resource-1", "column": "level"}
    correction = "请把 column 从 level 修正为当前数据中的可用字段 levl。"
    application, model, client, _services = _application(
        root,
        [
            _inspect_step(),
            _prepare_step(arguments),
            ModelStep(
                output=[
                    submit_reply(
                        "字段 level 不存在，当前不能执行。",
                        unresolved=((correction, ()),),
                    )
                ]
            ),
        ],
        fields=({"fieldId": "f0", "name": "levl", "type": "double"},),
        model_transform=_missing_field_model,
    )
    try:
        session_id = application.interaction.create_session().session.session_id
        view = await application.interaction.turn(
            session_id,
            "请用 resource-1 的 level 字段执行摘要。",
            dialogue_policy="EXECUTION_PROPOSAL",
        )
        run = application.agent_runtime.get(view.session.agent_run_id or "")
        with connect_database(application.database) as connection:
            row = connection.execute(
                "SELECT status,snapshot_json FROM task_preparations"
            ).fetchone()
        preparation = {} if row is None else json.loads(row["snapshot_json"])
        entries = preparation.get("entries", [])
        blocking = [] if not entries else entries[0].get("blocking_reasons", [])
        unresolved = [] if run.output is None else list(run.output.unresolved_items)
        counts = _counts(application)
        return _snapshot(
            "missing-field-correction",
            application,
            run_status=run.status,
            assertions=_assertions(
                MISSING_FIELD_BLOCKS_EXECUTION=(
                    run.status == "COMPLETE"
                    and row is not None
                    and row["status"] == "BLOCKED"
                    and blocking == ["FIELD_NOT_FOUND"]
                    and counts["actions"] == 0
                    and counts["workflowRuns"] == 0
                ),
                MISSING_FIELD_REQUESTS_CORRECTION=(unresolved == [correction]),
                MISSING_FIELD_NO_CONFIRMATION=counts["confirmations"] == 0,
            ),
            facts={
                "agentSteps": list(view.agent_steps),
                "blockingReasons": blocking,
                "modelCalls": len(model.calls),
                "preparationStatus": None if row is None else row["status"],
                "unresolvedItems": unresolved,
            },
        )
    finally:
        await client.aclose()


_DRAFT_KNOWLEDGE = {
    "version_id": "knowledge-version-1",
    "unit_id": "unit-2",
    "checksum": "sha256:" + "b" * 64,
}
_DRAFT_REFERENCE = {
    "kind": "data_version",
    "service": "data-primary",
    "dataset_id": "dataset-resource-1",
    "version_id": "resource-1",
    "semantic_id": "semantics-resource-1",
    "object_id": "object-resource-1",
    "media_type": "text/csv",
    "format": "csv",
    "size_bytes": 8,
    "checksum": "sha256:" + "a" * 64,
}


def _operator_step(operator_id: str) -> ModelStep:
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
                call_id=f"operator-{operator_id}",
            )
        ]
    )


def _fan_out_draft_document() -> dict[str, object]:
    describe_parameters = {"axis": {"workflowParameter": "axis"}}
    return {
        "title": "先整理再并行分析",
        "summary": "排序后的数据分别进入两个最终分析节点。",
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
                    "nodeId": "trend",
                    "operatorId": "statistics.describe",
                    "operatorVersion": "1.0.0",
                    "inputs": {
                        "data": {"nodeOutput": {"nodeId": "sort", "port": "data"}}
                    },
                    "parameters": describe_parameters,
                },
                {
                    "nodeId": "gaps",
                    "operatorId": "statistics.describe",
                    "operatorVersion": "1.0.0",
                    "inputs": {
                        "data": {"nodeOutput": {"nodeId": "sort", "port": "data"}}
                    },
                    "parameters": describe_parameters,
                },
            ],
            "outputs": [
                {
                    "name": "trend_summary",
                    "nodeOutput": {"nodeId": "trend", "port": "summary"},
                },
                {
                    "name": "gap_summary",
                    "nodeOutput": {"nodeId": "gaps", "port": "summary"},
                },
            ],
        },
        "data_bindings": [{"input_name": "source", "data_ref": _DRAFT_REFERENCE}],
        "parameter_values": {"axis": "columns"},
        "parameter_sources": [
            {
                "name": "axis",
                "source": "OPERATOR_DEFAULT",
                "explanation": "采用算子默认值。",
            }
        ],
        "knowledge_refs": [_DRAFT_KNOWLEDGE],
        "unresolved_items": ["需要排序规则文件。"],
        "limitations": ["仅草稿，未执行计算。"],
    }


def _draft_reply_step(draft: dict[str, object]) -> ModelStep:
    return ModelStep(
        output=[
            function_call(
                "submit_reply",
                {
                    "conclusion": {
                        "text": "草稿已整理。",
                        "citations": [_DRAFT_KNOWLEDGE],
                    },
                    "details": [],
                    "unresolved_items": [],
                    "referenced_workflow_runs": [],
                    "include_execution_status": False,
                    "interaction_options": [],
                    "workflow_draft": draft,
                },
                call_id="draft-reply",
            )
        ]
    )


async def _fan_out_draft(root: Path) -> dict[str, object]:
    draft = _fan_out_draft_document()
    application, model, client, _services = _application(
        root,
        [
            _operator_step("table.sort"),
            _operator_step("statistics.describe"),
            _read_step(),
            _inspect_step(),
            _draft_reply_step(draft),
        ],
    )
    try:
        session_id = application.interaction.create_session().session.session_id
        initial = application.interaction.draft_workflow(
            session_id,
            "offline-fan-out",
            "请先整理数据，再分别完成趋势和缺测分析。",
        )
        await application.interaction.resume_pending_start(session_id)
        resource = application.interaction.workflow_draft(
            session_id, str(initial["draft_id"])
        )
        payload = resource.get("payload") or {}
        workflow = payload.get("workflow") or {}
        outputs = workflow.get("outputs") or []
        nodes = workflow.get("nodes") or []
        sink_ids = sorted(output["nodeOutput"]["nodeId"] for output in outputs)
        consumers = sorted(
            node["nodeId"]
            for node in nodes
            if node.get("inputs", {}).get("data", {}).get("nodeOutput")
            == {"nodeId": "sort", "port": "data"}
        )
        counts = _counts(application)
        run = application.agent_runtime.get(str(resource["agent_run_id"]))
        return _snapshot(
            "fan-out-draft",
            application,
            run_status=run.status,
            assertions=_assertions(
                FAN_OUT_PRESERVED=(
                    resource["status"] == "COMPLETE" and consumers == ["gaps", "trend"]
                ),
                FAN_OUT_HAS_TWO_OUTPUTS=sink_ids == ["gaps", "trend"],
                WORKFLOW_DRAFT_NO_EXECUTION=(
                    counts["confirmations"] == 0
                    and counts["workflowRuns"] == 0
                    and counts["actions"] == 0
                ),
            ),
            facts={
                "consumersOfSort": consumers,
                "executionAllowed": payload.get("execution_allowed"),
                "modelCalls": len(model.calls),
                "outputNodeIds": sink_ids,
                "resourceStatus": resource["status"],
            },
        )
    finally:
        await client.aclose()


async def _knowledge_read_citation(root: Path) -> dict[str, object]:
    application, model, client, _services = _application(
        root,
        [
            _read_step(),
            ModelStep(
                output=[
                    submit_reply(
                        "资料支持该结论。",
                        citations=(_citation("unit-not-read"),),
                        call_id="invalid-citation",
                    )
                ]
            ),
            ModelStep(
                output=[
                    submit_reply(
                        "资料支持该结论。",
                        citations=(_citation(),),
                        call_id="corrected-citation",
                    )
                ]
            ),
        ],
    )
    try:
        session_id = application.interaction.create_session().session.session_id
        view = await application.interaction.turn(
            session_id,
            "请根据本轮读取的方法说明回答。",
            dialogue_policy="READ_ONLY",
        )
        run = application.agent_runtime.get(view.session.agent_run_id or "")
        presentation = (
            {} if run.output is None else run.output.presentation.model_dump()
        )
        sources = presentation.get("sources", [])
        with connect_database(application.database) as connection:
            attempts = [
                {
                    "outcome": row["outcome"],
                    "errorClass": row["error_class"],
                }
                for row in connection.execute(
                    "SELECT outcome,error_class FROM provider_requests ORDER BY rowid"
                )
            ]
        counts = _counts(application)
        invalid_rejected = any(
            item
            == {
                "outcome": "FAILED",
                "errorClass": "PROTOCOL_OR_SCHEMA_INVALID",
            }
            for item in attempts
        )
        return _snapshot(
            "knowledge-read-citation",
            application,
            run_status=run.status,
            assertions=_assertions(
                UNREAD_CITATION_REJECTED=invalid_rejected and len(model.calls) == 3,
                CITATION_FROM_CURRENT_READ=(
                    run.status == "COMPLETE"
                    and [source.get("unit_id") for source in sources] == ["unit-2"]
                ),
                KNOWLEDGE_REPLY_NO_EXECUTION=(
                    counts["confirmations"] == 0 and counts["workflowRuns"] == 0
                ),
            ),
            facts={
                "agentSteps": list(view.agent_steps),
                "modelCalls": len(model.calls),
                "providerAttempts": attempts,
                "sourceUnitIds": [source.get("unit_id") for source in sources],
            },
        )
    finally:
        await client.aclose()


async def _drive_workflow(application: PlatformApplication, agent_run_id: str) -> None:
    for _ in range(8):
        results = [result async for result in application.workflow_execution.tick()]
        with connect_database(application.database) as connection:
            connection.execute("UPDATE workflow_dispatches SET next_poll_at='0000'")
        ready = tuple(
            dict.fromkeys(
                ready_id
                for result in results
                for ready_id in result.ready_agent_run_ids
            )
        )
        for ready_id in ready:
            if application.agent_runtime.reconcile_execution(ready_id):
                run = await application.agent_runtime.resume_execution(ready_id)
                application.interaction.project(run)
        if application.agent_runtime.get(agent_run_id).status in {"COMPLETE", "FAILED"}:
            return
    raise AgentReplayRefusal("AGENT_REPLAY_WORKFLOW_DID_NOT_TERMINATE")


async def _minimax_noncase_terminal(root: Path) -> dict[str, object]:
    arguments = {"resource_id": "resource-1"}
    application, model, client, services = _application(
        root,
        [
            _inspect_step(),
            _prepare_step(arguments),
            _plan_step(),
            _execute_step(arguments),
        ],
        provider="minimax",
        execute=True,
    )
    try:
        session_id = application.interaction.create_session().session.session_id
        waiting = await application.interaction.turn(
            session_id,
            "请执行 resource-1 的摘要分析。",
            dialogue_policy="EXECUTION_PROPOSAL",
        )
        agent_run_id = waiting.session.agent_run_id or ""
        confirmation = waiting.session.confirmation
        if confirmation is None:
            raise AgentReplayRefusal("AGENT_REPLAY_CONFIRMATION_MISSING")
        calls_before_confirmation = len(model.calls)
        await application.interaction.confirm(
            session_id,
            confirmation.confirmation_id,
        )
        await _drive_workflow(application, agent_run_id)
        run = application.agent_runtime.get(agent_run_id)
        view = application.interaction.get(session_id)
        message = None if run.output is None else run.output.message
        counts = _counts(application)
        workflow_statuses: list[str] = []
        with connect_database(application.database) as connection:
            workflow_statuses = [
                row["status"]
                for row in connection.execute(
                    "SELECT status FROM workflow_runs ORDER BY rowid"
                )
            ]
        return _snapshot(
            "minimax-noncase-terminal",
            application,
            run_status=run.status,
            assertions=_assertions(
                MINIMAX_NONCASE_TERMINAL=(
                    run.status == "COMPLETE"
                    and workflow_statuses == ["SUCCEEDED"]
                    and counts["confirmations"] == 1
                    and counts["workflowRuns"] == 1
                ),
                MINIMAX_NO_PROVIDER_ACK_REPLAY=(
                    len(model.calls) == calls_before_confirmation == 4
                ),
                MINIMAX_NO_SYNTHETIC_DISCLAIMER=(
                    isinstance(message, str)
                    and "已完成" in message
                    and "开发调试案例" not in message
                    and "SYNTHETIC" not in message
                    and application.settings.execution_cases == ()
                ),
            ),
            facts={
                "agentSteps": list(view.agent_steps),
                "executionCaseCount": len(application.settings.execution_cases),
                "message": message,
                "modelCalls": len(model.calls),
                "workflowStatuses": workflow_statuses,
                "executorRequests": len(
                    [
                        item
                        for item in services.requests
                        if item[1] == "executor-service"
                    ]
                ),
            },
        )
    finally:
        await client.aclose()


_CASE_RUNNERS: dict[str, Callable[[Path], Awaitable[dict[str, object]]]] = {
    "dialogue-no-execution": _dialogue_no_execution,
    "execution-proposal-confirmation": _execution_proposal_confirmation,
    "fan-out-draft": _fan_out_draft,
    "knowledge-read-citation": _knowledge_read_citation,
    "minimax-noncase-terminal": _minimax_noncase_terminal,
    "missing-field-correction": _missing_field_correction,
}


def _validate_snapshot(
    snapshot: dict[str, object],
    definition: dict[str, object],
) -> bool:
    counts = snapshot.get("counts")
    assertions = snapshot.get("assertions")
    if not isinstance(counts, dict) or not isinstance(assertions, dict):
        return False
    return (
        counts.get("confirmations") == definition["expectedConfirmationCount"]
        and counts.get("workflowRuns") == definition["expectedWorkflowRunCount"]
        and set(assertions) == set(definition["invariants"])
        and set(assertions.values()) == {"PASS"}
    )


def run_agent_replay(
    *,
    repository_root: Path,
    case_file: Path,
    run_directory: Path,
    case_ids: Sequence[str] | None,
    repetitions: int = 3,
) -> dict[str, object]:
    """Run selected ScriptedModel cases through the real Kernel state machine."""

    repository = repository_root.resolve()
    case_path = case_file.resolve()
    if not repository.is_dir():
        raise AgentReplayRefusal("AGENT_REPLAY_REPOSITORY_INVALID")
    if not case_path.is_relative_to(repository):
        raise AgentReplayRefusal("AGENT_REPLAY_CASES_OUTSIDE_REPOSITORY")
    if type(repetitions) is not int or not 1 <= repetitions <= 10:
        raise AgentReplayRefusal("AGENT_REPLAY_REPETITIONS_INVALID")
    cases, case_file_sha256 = _load_case_index(case_path)
    if set(cases) != set(_CASE_RUNNERS):
        raise AgentReplayRefusal("AGENT_REPLAY_CASE_IMPLEMENTATIONS_INVALID")
    selected = (
        tuple(sorted(cases)) if case_ids is None else tuple(dict.fromkeys(case_ids))
    )
    if not selected:
        raise AgentReplayRefusal("AGENT_REPLAY_CASE_REQUIRED")
    if any(case_id not in cases for case_id in selected):
        raise AgentReplayRefusal("AGENT_REPLAY_CASE_NOT_FOUND")

    output = run_directory.resolve()
    if not run_directory.is_absolute():
        raise AgentReplayRefusal("AGENT_REPLAY_OUTPUT_NOT_ABSOLUTE")
    if output.exists():
        raise AgentReplayRefusal("AGENT_REPLAY_OUTPUT_EXISTS")
    if not output.parent.is_dir():
        raise AgentReplayRefusal("AGENT_REPLAY_OUTPUT_PARENT_INVALID")

    install_trace_processors([])
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent)
    ).resolve()
    work = staging / ".work"
    results: dict[str, object] = {}
    try:
        for case_id in selected:
            snapshots: list[dict[str, object]] = []
            digests: list[str] = []
            for iteration in range(1, repetitions + 1):
                iteration_root = work / case_id / f"iteration-{iteration}"
                snapshot = asyncio.run(_CASE_RUNNERS[case_id](iteration_root))
                snapshots.append(snapshot)
                digest = _sha256_bytes(_canonical_json(snapshot))
                digests.append(digest)
                _write_json(
                    staging / "cases" / case_id / f"iteration-{iteration}.json",
                    snapshot,
                )
            stable = len(set(digests)) == 1
            valid = all(
                _validate_snapshot(snapshot, cases[case_id]) for snapshot in snapshots
            )
            results[case_id] = {
                "status": "PASS" if stable and valid else "FAILED",
                "stable": stable,
                "repetitions": repetitions,
                "snapshotSha256": digests[0],
                "iterationSnapshotSha256": digests,
                "definition": cases[case_id],
                "snapshot": snapshots[0],
            }
        shutil.rmtree(work, ignore_errors=True)
        status = (
            "PASS"
            if all(item["status"] == "PASS" for item in results.values())
            else "FAILED"
        )
        summary: dict[str, object] = {
            "schemaVersion": "agent-replay-run.v1",
            "status": status,
            "runDirectory": str(output),
            "caseFile": str(case_path.relative_to(repository)),
            "caseFileSha256": case_file_sha256,
            "selectedCases": list(selected),
            "repetitions": repetitions,
            "runtime": {
                "provider": "ScriptedModel",
                "network": "OFFLINE_MOCK_TRANSPORT",
                "pythonVersion": ".".join(map(str, sys.version_info[:3])),
                "defaultRuntimeMutation": "NONE",
            },
            "caseResults": results,
        }
        shutil.copy2(case_path, staging / "case-index.json")
        _write_json(staging / "summary.json", summary)
        staging.rename(output)
        return summary
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        install_trace_processors([])


__all__ = ("AgentReplayRefusal", "run_agent_replay")
