from __future__ import annotations

import asyncio
from builtins import ExceptionGroup
from dataclasses import replace
import hashlib
import json
import time
from uuid import uuid4

from agents.testing import ScriptedModel, ModelStep, assistant_message, function_call
from kernel.tests.reply_testing import submit_reply
from agents.usage import Usage
from fastapi.testclient import TestClient
import httpx
import pytest

from platform_kernel.api import create_app
from platform_kernel.composition import PlatformApplication
from platform_kernel.database import connect_database
from platform_kernel.errors import AgentRunError
from platform_kernel.agent_runtime.models import AgentPlanStepV2
from platform_kernel.agent_runtime.persistence import save_plan
from platform_kernel.settings_types import ExecutableModelFieldRequirement
from platform_kernel.workflow_execution.models import WorkflowTickResult


def _final(message="done"):
    return submit_reply(message)


def _plan(tools):
    return function_call(
        "record_plan",
        {
            "goal": "execute",
            "steps": [
                {"step_id": str(index), "tool": tool, "purpose": "execute"}
                for index, tool in enumerate(tools)
            ],
            "reason": "requested",
        },
        call_id="plan",
    )


def _inspect():
    return ModelStep(output=[
        function_call(
            "inspect_data_version", {"version_id": version_id},
            call_id=f"inspect-{version_id}",
        )
        for version_id in ("a", "b", "resource-1")
    ])


def _prepare(resource_id="resource-1"):
    return ModelStep(output=[function_call(
        "prepare_task_inputs",
        {
            "target_tool": "run_summary",
            "arguments": {"resource_id": resource_id},
        },
        call_id=f"prepare-{resource_id}",
    )])


def _data_response(request):
    body = json.loads(request.content)
    version_id = body["arguments"]["version_id"]
    return httpx.Response(200, json={"data": {
        "status": "OK", "versionId": version_id,
        "currentVersion": (
            "replacement-version" if version_id == "old" else version_id
        ),
        "datasetId": f"dataset-{version_id}",
        "semanticId": f"semantics-{version_id}",
        "objects": [{"objectId": f"object-{version_id}", "role": "raw",
            "mediaType": "text/csv", "format": "csv", "size": 8,
            "checksum": "sha256:" + "a" * 64}],
    }})


def _application(
    tmp_path, kernel_settings, model, handler, capabilities="summary",
    settings_transform=None,
):
    settings = kernel_settings(capabilities)
    if settings_transform is not None:
        settings = settings_transform(settings)
    def routed_handler(request):
        if request.url.host == "data-service":
            return _data_response(request)
        if request.url.path == "/internal/health/ready":
            return httpx.Response(200, json={
                "status": "UP",
                "definitionSetDigest": settings.definition_set_digest,
            })
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(routed_handler))
    def route_response(call):
        current = json.dumps(call.input[-1], ensure_ascii=False)
        execution = any(
            marker in current
            for marker in ('"run"', '"old"', '"fail"', '"invalid"')
        )
        literal = (
            "EXECUTE_ON_DATA|NONE|NONE" if execution else "CONSULT|NONE|NONE"
        )
        return ModelStep(output=[assistant_message(literal)])

    router_model = ScriptedModel(
        [ModelStep.respond(route_response) for _ in range(100)]
    )
    application = PlatformApplication(
        settings=settings, model=model, client=client,
        intent_router_model=router_model,
    )
    return application, create_app(application=application)


def _post_message(client, session_id, message, requested_mode="AUTO"):
    return client.post(
        f"/api/v12/sessions/{session_id}/messages",
        json={
            "requestId": str(uuid4()),
            "message": message,
            "requestedMode": requested_mode,
        },
    )


def _turn(client, application, session_id, message, *, read_only=False):
    read_only = (
        read_only
        or message == "advise"
        or message.startswith("Explain the literal string")
    )
    accepted = _post_message(client, session_id, message, "AUTO")
    if accepted.status_code != 202:
        return accepted, None
    agent_run_id = accepted.json()["agentRunId"]

    route = application.interaction.claim_route(agent_run_id)
    if route is not None:
        application.interaction.resolve_route(
            route.agent_run_id,
            "DIALOGUE",
            "READ_ONLY" if read_only else "EXECUTION_PROPOSAL",
            provider="test",
            model="fixed-router",
            trace_id=f"route-{route.agent_run_id}",
            provider_requests=1,
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
        )

    async def drive():
        while await application.interaction.tick():
            pass
        await application.interaction.resume_pending_start(
            session_id, propagate_error=False
        )

    if read_only or route is not None:
        asyncio.run(drive())
    for _ in range(200):
        with connect_database(application.database) as connection:
            row = connection.execute(
                "SELECT status FROM agent_runs WHERE agent_run_id=?",
                (agent_run_id,),
            ).fetchone()
        if row is not None and row["status"] in {
            "COMPLETE", "FAILED", "WAITING_CONFIRMATION", "WAITING_EXECUTION",
            "CANCELLED", "SUPERSEDED",
        }:
            return accepted, client.get(f"/api/v12/sessions/{session_id}")
        time.sleep(0.01)
    raise AssertionError("agent start did not reach a durable run state")


def _start_workflow(client, application, calls=1):
    session = client.post("/api/v12/sessions", json={"projectId": "p"}).json()
    accepted, response = _turn(client, application, session["sessionId"], "run")
    assert accepted.status_code == 202
    planned = response.json()
    assert planned["confirmation"] is not None
    confirmation_id = planned["confirmation"]["confirmationId"]
    confirmed = client.post(
        f"/api/v12/sessions/{session['sessionId']}/confirm",
        json={"confirmationId": confirmation_id},
    )
    assert confirmed.status_code == 202
    assert confirmed.json()["agentSteps"][-1] == "WAITING_EXECUTION"
    assert len(confirmed.json()["workflowRunIds"]) == calls
    return session["sessionId"], planned["agentRunId"], confirmation_id


async def _collect_tick(application):
    results = [result async for result in application.workflow_execution.tick()]
    return WorkflowTickResult(
        sum(result.processed_count for result in results),
        tuple(dict.fromkeys(
            run_id for result in results for run_id in result.ready_agent_run_ids
        )),
    )


def _tick(application):
    result = asyncio.run(_collect_tick(application))
    with connect_database(application.database) as connection:
        connection.execute("UPDATE workflow_dispatches SET next_poll_at = '0000'")
    if not result.ready_agent_run_ids:
        result = asyncio.run(_collect_tick(application))
    for run_id in result.ready_agent_run_ids:
        if application.agent_runtime.reconcile_execution(run_id):
            run = asyncio.run(application.agent_runtime.resume_execution(run_id))
            application.interaction.project(run)
    return result.ready_agent_run_ids


def test_advisory_product_finishes_without_workflow(tmp_path, kernel_settings) -> None:
    model = ScriptedModel([ModelStep(output=[_final("advice")])])
    application, app = _application(
        tmp_path,
        kernel_settings,
        model,
        lambda _request: httpx.Response(500),
        "groundwater",
    )
    client = TestClient(app)
    session = client.post("/api/v12/sessions", json={"projectId": "p"}).json()
    accepted, response = _turn(
        client, application, session["sessionId"], "advise"
    )
    assert accepted.status_code == 202 and response.status_code == 200
    assert response.json()["agentOutput"]["message"] == "advice"
    with connect_database(application.database) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] == 0
        )


@pytest.mark.parametrize("literal", ["<|endoftext|>", "<|endofprompt|>"])
def test_special_token_text_preserves_session_availability(
    tmp_path, kernel_settings, literal
) -> None:
    model = ScriptedModel([
        ModelStep(output=[_final(f"Literal text: {literal}")]),
        ModelStep(output=[_final("next answer")]),
    ])
    application, app = _application(
        tmp_path, kernel_settings, model,
        lambda _request: httpx.Response(500), "groundwater",
    )
    client = TestClient(app)
    session = client.post("/api/v12/sessions", json={"projectId": "p"}).json()
    session_id = session["sessionId"]
    accepted, response = _turn(
        client,
        application,
        session_id,
        f"Explain the literal string {literal}",
    )
    assert accepted.status_code == 202 and response.status_code == 200
    assert response.json()["agentOutput"]["message"] == f"Literal text: {literal}"
    accepted, response = _turn(
        client, application, session_id, "continue", read_only=True
    )
    assert accepted.status_code == 202 and response.status_code == 200
    assert response.json()["agentOutput"]["message"] == "next answer"
    with connect_database(application.database) as connection:
        assert [row[0] for row in connection.execute(
            "SELECT status FROM agent_runs ORDER BY goal_revision"
        )] == ["COMPLETE", "COMPLETE"]


def test_agent_calls_only_executor_and_replays_result_resources(
    tmp_path, kernel_settings
) -> None:
    model = ScriptedModel(
        [
            _inspect(),
            _prepare(),
            ModelStep(output=[_plan(["run_summary"])]),
            ModelStep(
                output=[
                    function_call(
                        "run_summary",
                        {"resource_id": "resource-1"},
                        call_id="call-1",
                    )
                ]
            ),
            ModelStep(output=[_final()]),
        ]
    )
    submitted = []
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "POST" and request.url.path.endswith("/jobs"):
            body = json.loads(request.content)
            submitted.append(body)
            return httpx.Response(202, json=_job(body["requestId"], "QUEUED"))
        if request.method == "GET":
            return httpx.Response(
                200,
                json=_job(
                    submitted[0]["requestId"],
                    "SUCCEEDED",
                        outputs=[
                            {
                                "kind": "data_version",
                                "service": "data-primary",
                                "datasetId": "dataset-bundle",
                                "versionId": "bundle-1",
                                "semanticId": "semantics-bundle",
                                "objectId": "object-bundle",
                            "mediaType": "application/zip",
                            "format": "zip",
                            "sizeBytes": 1,
                                "checksum": "sha256:" + "a" * 64,
                            },
                            {
                                "kind": "data_version",
                                "service": "data-primary",
                                "datasetId": "dataset-map",
                                "versionId": "map-image-1",
                                "semanticId": "semantics-map",
                                "objectId": "object-map",
                            "mediaType": "image/png",
                            "format": "png",
                            "sizeBytes": 1,
                                "checksum": "sha256:" + "b" * 64,
                            "outputName": "map-image",
                        },
                    ],
                ),
            )
        return httpx.Response(500)

    application, app = _application(tmp_path, kernel_settings, model, handler)
    client = TestClient(app)
    session_id, agent_run_id, confirmation_id = _start_workflow(client, application)
    assert (
        client.post(
            f"/api/v12/sessions/{session_id}/confirm",
            json={"confirmationId": confirmation_id},
        ).status_code
        == 202
    )
    assert _tick(application) == (agent_run_id,)
    session = client.get(f"/api/v12/sessions/{session_id}").json()
    assert session["agentOutput"]["message"] == (
        "已完成 1 项分析；生成 2 份结果，可在分析结果中查看或下载。"
    )
    assert session["agentOutput"]["workflowRuns"] == session["workflowRunIds"]
    assert [item["versionId"] for item in session["remoteOutputs"]] == [
        "bundle-1",
        "map-image-1",
    ]
    assert session["remoteOutputs"][1]["kind"] == "data_version"
    assert "map-image-1" in json.dumps(model.calls[-1].input)
    assert {
        tool.name for call in model.calls for tool in call.tools
    } == {
        "record_plan", "prepare_task_inputs", "submit_reply", "update_task",
        "inspect_data_version",
        "run_summary",
    }
    preparation = session["preparation"]
    assert preparation["status"] == "READY"
    assert preparation["disposition"] == "CONSUMED"
    assert preparation["entries"][0]["targetId"] == "groundwater.summary"
    assert preparation["entries"][0]["inputs"]["resource_id"]["versionId"] == "resource-1"
    assert {request.url.host for request in requests} == {"executor-service"}
    assert all(
        request.url.path.startswith("/internal/execution/v6/jobs")
        for request in requests
    )


    assert submitted[0]["projectId"] == "p"
    assert submitted[0]["requestId"] == session["workflowRunIds"][0]
    assert submitted[0]["arguments"] == {
        "axis": "columns",
        "resource_id": "resource-1",
    }
    assert submitted[0]["inputReferences"]["resource_id"]["versionId"] == "resource-1"
    with connect_database(application.database) as connection:
        context_rows = connection.execute(
            """SELECT purpose,status,manifest_json FROM context_views
               WHERE request_agent_run_id=? ORDER BY created_at""",
            (agent_run_id,),
        ).fetchall()
    assert "AGENT_RESUME" in {row["purpose"] for row in context_rows}
    assert {row["status"] for row in context_rows} == {"COMPLETE"}
    resume_manifest = next(
        row["manifest_json"] for row in context_rows
        if row["purpose"] == "AGENT_RESUME"
    )
    assert "RUN_STATE_ENVELOPE" in resume_manifest
    assert "SOURCE_WATERMARK" in resume_manifest
    assert "map-image-1" not in resume_manifest


def test_explicit_preparation_rejects_later_argument_drift(
    tmp_path, kernel_settings
) -> None:
    model = ScriptedModel([
        _inspect(),
        _prepare(),
        ModelStep(output=[_plan(["run_summary"])]),
        ModelStep(output=[function_call(
            "run_summary",
            {"resource_id": "resource-1", "axis": "rows"},
            call_id="changed-after-prepare",
        )]),
    ])

    def no_executor(request):
        raise AssertionError(f"unexpected executor request: {request.url}")

    application, app = _application(
        tmp_path, kernel_settings, model, no_executor
    )
    client = TestClient(app)
    session_id = client.post(
        "/api/v12/sessions", json={"projectId": "p"}
    ).json()["sessionId"]
    _, response = _turn(client, application, session_id, "run")

    assert response.json()["agentSteps"][-1] == "FAILED"
    assert response.json()["preparation"]["status"] == "READY"
    assert response.json()["preparation"]["entries"][0]["targetId"] == "groundwater.summary"
    with connect_database(application.database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM confirmations"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("structured", "provider_group", "expected_status"),
    [
        (False, None, "WAITING_CONFIRMATION"),
        (False, "", "WAITING_CONFIRMATION"),
        (True, "well", "WAITING_CONFIRMATION"),
        (False, "well", "FAILED"),
    ],
)
def test_ungrouped_summary_preserves_prepared_arguments(
    tmp_path, kernel_settings, structured, provider_group, expected_status,
) -> None:
    def optional_grouping(settings):
        base = settings.models[0]
        schema = {**base.input_schema, "properties": {
            **base.input_schema["properties"],
            "groupField": {"type": "string", "minLength": 1},
        }}
        checksum = "sha256:" + hashlib.sha256(
            json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return replace(settings, models=(replace(
            base, input_schema=schema, input_schema_checksum=checksum,
            field_requirements=(ExecutableModelFieldRequirement(
                parameter="groupField", input_parameter="resource_id",
                identifier="name", allowed_types=("string",),
            ),),
        ),))

    arguments = {"resource_id": "resource-1"}
    model = ScriptedModel([
        _inspect(), _prepare(), ModelStep(output=[_plan(["run_summary"])]),
        ModelStep(output=[function_call(
            "run_summary", {**arguments, "groupField": provider_group},
            call_id="summary-proposal",
        )]),
        ModelStep(output=[_final()]),
    ])
    submitted = []

    def executor(request):
        if request.method == "POST":
            submitted.append(json.loads(request.content))
            return httpx.Response(202, json=_job(submitted[-1]["requestId"], "QUEUED"))
        return httpx.Response(200, json=_job(submitted[-1]["requestId"], "SUCCEEDED"))

    application, app = _application(
        tmp_path, kernel_settings, model, executor,
        settings_transform=optional_grouping,
    )
    client = TestClient(app)
    session_id = client.post("/api/v12/sessions", json={"projectId": "p"}).json()["sessionId"]
    message = "run"
    if structured:
        message = "调用工具 run_summary\n[Model execution] " + json.dumps({
            "tool": "run_summary", "arguments": arguments,
        })
    _, response = _turn(client, application, session_id, message)
    state = response.json()
    assert state["agentSteps"][-1] == expected_status
    assert state["workflowRunIds"] == []
    assert submitted == []
    if expected_status == "FAILED":
        assert state["confirmation"] is None
        return

    confirmation = state["confirmation"]
    assert confirmation["status"] == "PENDING"
    expected_arguments = {**arguments, "axis": "columns"}
    assert confirmation["actions"][0]["arguments"] == expected_arguments
    approved = client.post(
        f"/api/v12/sessions/{session_id}/confirm",
        json={"confirmationId": confirmation["confirmationId"]},
    )
    assert approved.status_code == 202
    assert _tick(application) == (state["agentRunId"],)
    assert [job["arguments"] for job in submitted] == [expected_arguments]
    finished = client.get(f"/api/v12/sessions/{session_id}").json()
    assert finished["agentSteps"][-1] == "COMPLETE"


def test_minimax_aliases_are_canonicalized_at_preparation_and_confirmation(
    tmp_path, kernel_settings,
) -> None:
    def minimax_camel_contract(settings):
        base = settings.models[0]
        schema = {
            "type": "object",
            "properties": {
                "resourceId": {"type": "string"},
                "axis": {"type": "string"},
            },
            "required": ["resourceId"],
            "additionalProperties": False,
        }
        return replace(
            settings,
            agent_provider="minimax",
            agent_protocol="chat_completions",
            models=(replace(
                base,
                input_schema=schema,
                input_schema_checksum="sha256:" + hashlib.sha256(
                    json.dumps(
                        schema, sort_keys=True, separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
                data_input_parameters=("resourceId",),
            ),),
        )

    model = ScriptedModel([
        _inspect(),
        ModelStep(output=[function_call(
            "prepare_task_inputs",
            {
                "target_tool": "run_summary",
                "arguments": {"resource_id": "resource-1"},
            },
            call_id="prepare-alias",
        )]),
        ModelStep(output=[_plan(["run_summary"])]),
        ModelStep(output=[function_call(
            "run_summary",
            {"resource_id": "resource-1"},
            call_id="run-alias",
        )]),
    ])
    application, app = _application(
        tmp_path,
        kernel_settings,
        model,
        lambda request: (_ for _ in ()).throw(
            AssertionError(f"unexpected executor request: {request.url}")
        ),
        settings_transform=minimax_camel_contract,
    )
    client = TestClient(app)
    session_id = client.post(
        "/api/v12/sessions", json={"projectId": "p"},
    ).json()["sessionId"]

    _, response = _turn(client, application, session_id, "run")

    payload = response.json()
    assert payload["agentSteps"][-1] == "WAITING_CONFIRMATION"
    assert payload["confirmation"]["actions"][0]["arguments"] == {
        "resourceId": "resource-1",
        "axis": "columns",
    }
    entry = payload["preparation"]["entries"][0]
    assert entry["status"] == "READY"
    assert entry["inputs"]["resourceId"]["versionId"] == "resource-1"

    with connect_database(application.database) as connection:
        stored = connection.execute(
            "SELECT rowid,input_references_json FROM confirmation_actions "
            "WHERE confirmation_id=?",
            (payload["confirmation"]["confirmationId"],),
        ).fetchone()
        references = json.loads(stored["input_references_json"])
        reference = references["resourceId"]
        connection.execute(
            "UPDATE confirmation_actions SET input_references_json=? WHERE rowid=?",
            (json.dumps({"basin_boundaryVersionId": reference}), stored["rowid"]),
        )
    detail_keys = client.get(f"/api/v12/sessions/{session_id}").json()[
        "confirmation"
    ]["actions"][0]["inputReferences"].keys()
    history_keys = client.get(
        f"/api/v12/sessions/{session_id}/messages"
    ).json()["turns"][0]["confirmations"][0]["actions"][0][
        "inputReferences"
    ].keys()
    assert list(detail_keys) == list(history_keys) == ["basin_boundaryVersionId"]


def test_current_required_preparation_blocks_a_superseded_data_version(
    tmp_path, kernel_settings
) -> None:
    model = ScriptedModel([
        ModelStep(output=[function_call(
            "inspect_data_version", {"version_id": "old"}, call_id="inspect-old",
        )]),
        ModelStep(output=[function_call(
            "prepare_task_inputs",
            {
                "target_tool": "run_summary",
                "arguments": {"resource_id": "old"},
                "selection_policy": "CURRENT_REQUIRED",
            },
            call_id="prepare-old",
        )]),
        ModelStep(output=[_plan(["run_summary"])]),
        ModelStep(output=[function_call(
            "run_summary", {"resource_id": "old"}, call_id="run-old",
        )]),
    ])

    application, app = _application(
        tmp_path, kernel_settings, model,
        lambda request: (_ for _ in ()).throw(
            AssertionError(f"unexpected executor request: {request.url}")
        ),
    )
    client = TestClient(app)
    session_id = client.post(
        "/api/v12/sessions", json={"projectId": "p"}
    ).json()["sessionId"]
    _, response = _turn(client, application, session_id, "old")

    assert response.status_code == 200
    assert response.json()["agentSteps"][-1] == "FAILED"
    preparation = response.json()["preparation"]
    entry, = preparation["entries"]
    assert preparation["status"] == "BLOCKED"
    assert entry["selectionPolicy"] == "CURRENT_REQUIRED"
    assert entry["blockingReasons"] == ["DATA_VERSION_NOT_CURRENT"]
    assert next(
        check for check in entry["checks"]
        if check["kind"] == "SELECTION_POLICY"
    ) == {
            "kind": "SELECTION_POLICY",
            "status": "FAIL",
            "input": "resource_id",
            "versionId": "old",
            "semanticId": None,
            "currentVersion": "replacement-version",
            "operatorCatalogDigest": None,
        "count": None,
    }
    with connect_database(application.database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM confirmations"
        ).fetchone()[0] == 0


def test_field_metadata_block_is_persisted_before_confirmation(
    tmp_path, kernel_settings
) -> None:
    def field_contract(settings):
        base = settings.models[0]
        schema = {
            "type": "object",
            "properties": {
                "resource_id": {"type": "string"},
                "axis": {"type": "string"},
                "column": {"type": "string"},
            },
            "required": ["resource_id", "column"],
            "additionalProperties": False,
        }
        checksum = "sha256:" + hashlib.sha256(
            json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return replace(
            settings,
            models=(
                replace(
                    base,
                    input_schema=schema,
                    input_schema_checksum=checksum,
                    field_requirements=(
                        ExecutableModelFieldRequirement(
                            parameter="column",
                            input_parameter="resource_id",
                            identifier="name",
                            allowed_types=("any",),
                        ),
                    ),
                ),
            ),
        )

    model = ScriptedModel(
        [
            _inspect(),
            ModelStep(
                output=[
                    function_call(
                        "prepare_task_inputs",
                        {
                            "target_tool": "run_summary",
                            "arguments": {
                                "resource_id": "resource-1",
                                "column": "level",
                            },
                        },
                        call_id="prepare-field",
                    )
                ]
            ),
            ModelStep(output=[_plan(["run_summary"])]),
            ModelStep(
                output=[
                    function_call(
                        "run_summary",
                        {"resource_id": "resource-1", "column": "level"},
                        call_id="run-field",
                    )
                ]
            ),
        ]
    )
    application, app = _application(
        tmp_path,
        kernel_settings,
        model,
        lambda request: (_ for _ in ()).throw(
            AssertionError(f"unexpected executor request: {request.url}")
        ),
        settings_transform=field_contract,
    )
    client = TestClient(app)
    session_id = client.post(
        "/api/v12/sessions", json={"projectId": "p"}
    ).json()["sessionId"]

    _, response = _turn(client, application, session_id, "run")

    payload = response.json()
    assert payload["agentSteps"][-1] == "FAILED"
    entry, = payload["preparation"]["entries"]
    assert entry["blockingReasons"] == ["FIELD_METADATA_UNAVAILABLE"]
    assert any(
        check["kind"] == "FIELD_EXISTS" and check["status"] == "UNKNOWN"
        for check in entry["checks"]
    )
    with connect_database(application.database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM confirmations"
        ).fetchone()[0] == 0


def test_models_sharing_a_workflow_keep_their_identity_through_dispatch(
    kernel_settings,
) -> None:
    settings = kernel_settings()
    variants = tuple(
        replace(
            settings.models[0],
            model_id=f"groundwater.summary.{axis}",
            model_checksum="sha256:" + hashlib.sha256(axis.encode()).hexdigest(),
            tool_name=f"summarize_{axis}",
            default_parameters={"axis": axis},
        )
        for axis in ("columns", "rows")
    )
    model = ScriptedModel(
        [
            _inspect(),
            ModelStep(output=[_plan([item.tool_name for item in variants])]),
            ModelStep(
                output=[
                    function_call(
                        item.tool_name,
                        {"resource_id": "resource-1"},
                        call_id=f"call-{index}",
                    )
                    for index, item in enumerate(variants)
                ]
            ),
            ModelStep(output=[_final()]),
        ]
    )
    submitted = []

    def handler(request):
        if request.url.host == "data-service":
            return _data_response(request)
        if request.method == "GET" and request.url.path == "/internal/health/ready":
            return httpx.Response(
                200,
                json={
                    "status": "UP",
                    "definitionSetDigest": replace(
                        settings, models=variants
                    ).definition_set_digest,
                },
            )
        assert request.method == "POST"
        body = json.loads(request.content)
        submitted.append(body)
        return httpx.Response(202, json=_job(body["requestId"], "SUCCEEDED"))

    application = PlatformApplication(
        settings=replace(settings, models=variants),
        model=model,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    client = TestClient(create_app(application=application))
    _, agent_run_id, _ = _start_workflow(client, application, calls=2)
    runs = application.workflow_execution.for_agent(agent_run_id)
    assert [(run.model_id, run.model_checksum) for run in runs] == [
        (item.model_id, item.model_checksum) for item in variants
    ]
    assert {run.workflow_id for run in runs} == {variants[0].workflow_id}
    assert _tick(application) == (agent_run_id,)
    assert len(submitted) == 1
    assert application.agent_runtime.get(agent_run_id).status == "WAITING_EXECUTION"
    assert _tick(application) == (agent_run_id,)
    assert application.agent_runtime.get(agent_run_id).status == "COMPLETE"
    for body, definition, run in zip(submitted, variants, runs, strict=True):
        assert body["modelId"] == definition.model_id
        assert body["modelChecksum"] == definition.model_checksum
        assert body["workflowId"] == definition.workflow_id
        assert body["workflowChecksum"] == definition.workflow_checksum
        assert body["requestId"] == run.run_id
        assert body["arguments"]["axis"] == definition.default_parameters["axis"]
        assert "workflow" not in body and "nodes" not in body


def test_new_turn_is_rejected_while_execution_is_pending(
    tmp_path, kernel_settings
) -> None:
    model = ScriptedModel(
        [
            _inspect(),
            ModelStep(output=[_plan(["run_summary"])]),
            ModelStep(
                output=[
                    function_call("run_summary", {"resource_id": "a"}, call_id="call")
                ]
            ),
        ]
    )
    application, app = _application(
        tmp_path, kernel_settings, model, lambda _request: httpx.Response(500)
    )
    client = TestClient(app)
    session_id, _, _ = _start_workflow(client, application)
    response = _post_message(client, session_id, "replace")
    assert response.status_code == 409
    assert response.json()["code"] == "AGENT_EXECUTION_IN_PROGRESS"


def test_new_turn_supersedes_only_pending_confirmation(
    tmp_path, kernel_settings
) -> None:
    model = ScriptedModel(
        [
            _inspect(),
            ModelStep(output=[_plan(["run_summary"])]),
            ModelStep(
                output=[
                    function_call("run_summary", {"resource_id": "a"}, call_id="old")
                ]
            ),
            ModelStep(output=[_final("new turn")]),
        ]
    )
    application, app = _application(
        tmp_path, kernel_settings, model, lambda _request: httpx.Response(500)
    )
    client = TestClient(app)
    session = client.post("/api/v12/sessions", json={"projectId": "p"}).json()
    old_accepted, old_response = _turn(
        client, application, session["sessionId"], "old"
    )
    assert old_accepted.status_code == 202
    old = old_response.json()
    current_accepted, current = _turn(
        client, application, session["sessionId"], "new", read_only=True
    )
    assert current_accepted.status_code == 202 and current.status_code == 200
    assert current.json()["agentOutput"]["message"] == "new turn"
    old_run = application.agent_runtime.get(old["agentRunId"])
    assert old_run.status == "SUPERSEDED"
    assert old_run.actions[0].status == "SUPERSEDED"
    with connect_database(application.database) as connection:
        confirmation = connection.execute(
            "SELECT status FROM confirmations WHERE confirmation_id = ?",
            (old["confirmation"]["confirmationId"],),
        ).fetchone()
        assert confirmation["status"] == "SUPERSEDED"
        assert (
            connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] == 0
        )


def test_reject_creates_no_job_and_sdk_continues(tmp_path, kernel_settings) -> None:
    model = ScriptedModel(
        [
            _inspect(),
            ModelStep(output=[_plan(["run_summary"])]),
            ModelStep(
                output=[
                    function_call("run_summary", {"resource_id": "a"}, call_id="call")
                ]
            ),
            ModelStep(output=[_final("rejected")]),
        ]
    )
    application, app = _application(
        tmp_path, kernel_settings, model, lambda _request: httpx.Response(500)
    )
    client = TestClient(app)
    session = client.post("/api/v12/sessions", json={"projectId": "p"}).json()
    accepted, planned_response = _turn(
        client, application, session["sessionId"], "run"
    )
    assert accepted.status_code == 202
    planned = planned_response.json()
    response = client.post(
        f"/api/v12/sessions/{session['sessionId']}/reject",
        json={
            "confirmationId": planned["confirmation"]["confirmationId"],
            "reason": "SYNTHETIC_TEST_ONLY: 拒绝路径验证",
        },
    )
    assert response.status_code == 200
    assert response.json()["agentOutput"] is None
    assert asyncio.run(application.interaction.tick())
    session = client.get(f"/api/v12/sessions/{session['sessionId']}").json()
    assert session["agentOutput"]["message"] == "已拒绝 1 项分析。"
    directory = client.get("/api/v12/sessions").json()["items"][0]
    assert directory["title"].startswith("【验证会话】")
    with connect_database(application.database) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] == 0
        )
        assert (
            connection.execute("SELECT status FROM agent_actions").fetchone()["status"]
            == "REJECTED"
        )


def test_corrupt_run_state_fails_with_stable_error(tmp_path, kernel_settings) -> None:
    model = ScriptedModel(
        [
            _inspect(),
            ModelStep(output=[_plan(["run_summary"])]),
            ModelStep(
                output=[
                    function_call("run_summary", {"resource_id": "a"}, call_id="call")
                ]
            ),
        ]
    )
    application, app = _application(
        tmp_path, kernel_settings, model, lambda _request: httpx.Response(500)
    )
    client = TestClient(app)
    session = client.post("/api/v12/sessions", json={"projectId": "p"}).json()
    accepted, planned_response = _turn(
        client, application, session["sessionId"], "run"
    )
    assert accepted.status_code == 202
    planned = planned_response.json()
    agent_run_id = planned["agentRunId"]
    with connect_database(application.database) as connection:
        connection.execute(
            "UPDATE agent_run_state SET state_json = '{' WHERE agent_run_id = ?",
            (agent_run_id,),
        )

    with pytest.raises(AgentRunError, match="AGENT_RUN_STATE_INVALID"):
        asyncio.run(
            application.agent_runtime.reject(
                agent_run_id, "p", "no", tool_call_ids=("call",)
            )
        )

    run = application.agent_runtime.get(agent_run_id)
    assert run.status == "FAILED"
    assert run.error == "AGENT_RUN_STATE_INVALID"
    with connect_database(application.database) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM agent_run_state WHERE agent_run_id = ?",
                (agent_run_id,),
            ).fetchone()
            is None
        )


def test_unknown_replay_action_fails_with_stable_error(
    tmp_path, kernel_settings
) -> None:
    model = ScriptedModel(
        [
            _inspect(),
            ModelStep(output=[_plan(["run_summary"])]),
            ModelStep(
                output=[
                    function_call("run_summary", {"resource_id": "a"}, call_id="call")
                ]
            ),
        ]
    )
    application, app = _application(
        tmp_path, kernel_settings, model, lambda _request: httpx.Response(500)
    )
    client = TestClient(app)
    _, agent_run_id, _ = _start_workflow(client, application)
    with connect_database(application.database) as connection:
        row = connection.execute(
            "SELECT state_json FROM agent_run_state WHERE agent_run_id = ?",
            (agent_run_id,),
        ).fetchone()
        changed = row["state_json"].replace('"call"', '"unknown"')
        assert changed != row["state_json"]
        connection.execute(
            "UPDATE agent_run_state SET state_json = ? WHERE agent_run_id = ?",
            (changed, agent_run_id),
        )

    with pytest.raises(AgentRunError, match="AGENT_RUN_STATE_INVALID"):
        asyncio.run(application.agent_runtime.resume_execution(agent_run_id))

    run = application.agent_runtime.get(agent_run_id)
    assert run.status == "FAILED"
    assert run.error == "AGENT_RUN_STATE_INVALID"


def test_first_failed_action_prevents_second_remote_submission(
    tmp_path, kernel_settings
) -> None:
    model = ScriptedModel(
        [
            _inspect(),
            _prepare("a"),
            _prepare("b"),
            ModelStep(output=[_plan(["run_summary", "run_summary"])]),
            ModelStep(
                output=[
                    function_call(
                        "run_summary", {"resource_id": "a"}, call_id="call-1"
                    ),
                    function_call(
                        "run_summary", {"resource_id": "b"}, call_id="call-2"
                    ),
                ]
            ),
            ModelStep(output=[_final("failed")]),
        ]
    )
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            body = json.loads(request.content)
            return httpx.Response(202, json=_job(body["requestId"], "QUEUED"))
        return httpx.Response(
            200,
            json=_job(
                requests[0] and json.loads(requests[0].content)["requestId"],
                "FAILED",
                error="EXECUTOR_FAILED",
            ),
        )

    application, app = _application(tmp_path, kernel_settings, model, handler)
    client = TestClient(app)
    session_id, agent_run_id, _ = _start_workflow(
        client, application, calls=2
    )
    preparation = client.get(
        f"/api/v12/sessions/{session_id}/preparation"
    ).json()
    assert preparation["disposition"] == "EXECUTING"
    assert [
        entry["inputs"]["resource_id"]["versionId"]
        for entry in preparation["entries"]
    ] == ["a", "b"]
    assert _tick(application) == (agent_run_id,)
    run = application.agent_runtime.get(agent_run_id)
    assert run.status == "FAILED"
    assert [action.status for action in run.actions] == ["FAILED", "FAILED"]
    assert sum(request.method == "POST" for request in requests) == 1


def test_observation_tool_injects_project_and_validates_response(
    tmp_path, kernel_settings
) -> None:
    model = ScriptedModel(
        [
            ModelStep(
                output=[
                    function_call(
                        "retrieve_evidence",
                        {"query": "unique-query-sentinel"},
                        call_id="observe",
                    )
                ]
            ),
            ModelStep(output=[_final("observed")]),
        ]
    )
    observed = []

    def handler(request):
        observed.append((json.loads(request.content), request.headers))
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "knowledgeId": "unique-id-sentinel",
                        "title": "unique-title-sentinel",
                        "summary": "unique-summary-sentinel",
                        "chunkIndex": 0,
                        "checksum": "sha256:" + "a" * 64,
                        "score": 1.0,
                    }
                ]
            },
        )

    application, app = _application(
        tmp_path, kernel_settings, model, handler, "groundwater"
    )
    with TestClient(app) as client:
        session = client.post(
            "/api/v12/sessions", json={"projectId": "p"}
        ).json()
        accepted, response = _turn(
            client, application, session["sessionId"], "inspect", read_only=True
        )
        assert accepted.status_code == 202 and response.status_code == 200
    assert observed[0][0] == {
        "callId": "observe",
        "projectId": "p",
        "arguments": {"query": "unique-query-sentinel"},
            "actor": {
                "subject": "local-research-admin",
                "roles": ["researcher"],
            },
    }
    assert observed[0][1]["X-Trace-ID"]
    with connect_database(application.database) as connection:
        trace_payloads = [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT payload_json FROM agent_spans ORDER BY rowid"
            )
        ]
    trace_payload = json.dumps(trace_payloads)
    assert "unique-query-sentinel" not in trace_payload
    assert "unique-id-sentinel" not in trace_payload
    assert "unique-title-sentinel" not in trace_payload
    assert "unique-summary-sentinel" not in trace_payload
    observation_span = next(
        item
        for item in trace_payloads
        if item["span_data"].get("type") == "function"
        and item["span_data"].get("name") == "retrieve_evidence"
    )
    assert observation_span["span_data"]["input"] is None
    assert observation_span["span_data"]["output"] is None


def test_knowledge_tools_research_stale_and_search_again_on_next_turn(
    tmp_path, kernel_settings
) -> None:
    old_checksum = "sha256:" + "a" * 64
    current_checksum = "sha256:" + "b" * 64
    model = ScriptedModel(
        [
            ModelStep(
                output=[
                    function_call(
                        "retrieve_evidence", {"query": "first query"}, call_id="s1"
                    )
                ]
            ),
            ModelStep(
                output=[
                    function_call(
                            "read_evidence",
                            {"refs": [{"version_id": "version", "unit_id": "unit",
                                "checksum": old_checksum}]},
                        call_id="r1",
                    )
                ]
            ),
            ModelStep(
                output=[
                        function_call(
                            "retrieve_evidence", {"query": "first query refreshed"}, call_id="s2"
                        )
                ]
            ),
            ModelStep(
                output=[
                    function_call(
                        "read_evidence",
                        {"refs": [{"version_id": "version", "unit_id": "unit",
                            "checksum": current_checksum}]},
                        call_id="r2",
                    )
                ]
            ),
            ModelStep(output=[function_call("submit_reply", {
                "conclusion": {
                    "text": "first answer", "citations": [{
                            "version_id": "version", "unit_id": "unit",
                        "checksum": current_checksum,
                    }],
                }, "details": [], "unresolved_items": [], "referenced_workflow_runs": [],
                "include_execution_status": False,
                "interaction_options": [], "workflow_draft": None,
            }, call_id="reply-1")]),
            ModelStep(
                output=[
                    function_call(
                        "retrieve_evidence", {"query": "second query"}, call_id="s3"
                    )
                ]
            ),
            ModelStep(
                output=[
                    function_call(
                            "read_evidence",
                            {"refs": [{"version_id": "version", "unit_id": "unit",
                                "checksum": current_checksum}]},
                        call_id="r3",
                    )
                ]
            ),
            ModelStep(output=[function_call("submit_reply", {
                "conclusion": {
                    "text": "second answer", "citations": [{
                            "version_id": "version", "unit_id": "unit",
                        "checksum": current_checksum,
                    }],
                }, "details": [], "unresolved_items": [], "referenced_workflow_runs": [],
                "include_execution_status": False,
                "interaction_options": [], "workflow_draft": None,
            }, call_id="reply-2")]),
        ]
    )
    requests = []
    search_count = 0

    def handler(request):
        nonlocal search_count
        body = json.loads(request.content)
        requests.append(body)
        if request.url.path.endswith("/retrieve_evidence"):
            search_count += 1
            checksum = old_checksum if search_count == 1 else current_checksum
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                                "knowledgeId": "doc",
                            "title": "untrusted-title",
                            "summary": "untrusted-summary",
                                "chunkIndex": 0,
                            "checksum": checksum,
                            "score": 1.0,
                        }
                    ]
                },
            )
        expected = body["arguments"]["refs"][0]["checksum"]
        if expected == old_checksum:
            return httpx.Response(
                200,
                json={
                    "data": {"items": [{
                            "status": "STALE", "versionId": "version",
                            "unitId": "unit", "checksum": old_checksum,
                        "content": None,
                    }]}
                },
            )
        return httpx.Response(
            200,
            json={
                "data": {"items": [{
                        "status": "OK", "bindingMatch": "EXACT", "versionId": "version", "unitId": "unit",
                        "knowledgeId": "doc", "content": "secret-observation-content",
                    "locator": {"title": "untrusted-title"},
                    "checksum": current_checksum,
                }]}
            },
        )

    application, app = _application(
        tmp_path, kernel_settings, model, handler, "groundwater"
    )
    with TestClient(app) as client:
        capabilities = client.get("/api/v12/capabilities").json()
        assert [item["name"] for item in capabilities["observationTools"]] == [
            "retrieve_evidence",
            "read_evidence",
        ]
        assert all(
            set(item) == {"name", "description", "inputSchema", "outputSchema"}
            for item in capabilities["observationTools"]
        )
        session = client.post(
            "/api/v12/sessions", json={"projectId": "p"}
        ).json()
        first_accepted, first_response = _turn(
            client, application, session["sessionId"], "first", read_only=True
        )
        second_accepted, second_response = _turn(
            client, application, session["sessionId"], "second", read_only=True
        )
        assert first_accepted.status_code == second_accepted.status_code == 202
        first = first_response.json()
        second = second_response.json()

    assert [request["callId"] for request in requests] == [
        "s1",
        "r1",
        "s2",
        "r2",
        "s3",
        "r3",
    ]
    assert all(request["projectId"] == "p" for request in requests)
    assert search_count == 3
    second_turn_items = [call for call in model.calls if call.tools][5].input
    second_turn_first_input = json.dumps(second_turn_items)
    assert "secret-observation-content" not in second_turn_first_input
    assert "untrusted-summary" not in second_turn_first_input
    # A human source title remains in the retained assistant turn. Observation
    # bodies, technical identities and tool results do not carry into this turn.
    assistant_turn, = [item for item in second_turn_items if item.get("role") == "assistant"]
    assert assistant_turn["content"][0]["text"] == first["agentOutput"]["message"]
    assert first["agentOutput"]["message"] == "first answer\n\n依据：untrusted-title"
    assert "/api/" not in first["agentOutput"]["message"]
    assert "sha256:" not in first["agentOutput"]["message"]
    assert all(item.get("type") != "function_call_output" for item in second_turn_items)
    assert "untrusted evidence" in model.calls[0].system_instructions
    assert "On STALE" in next(
        tool.description for tool in model.calls[0].tools if tool.name == "retrieve_evidence"
    )
    with connect_database(application.database) as connection:
        remaining = connection.execute(
            "SELECT agent_run_id FROM agent_run_state WHERE agent_run_id IN (?, ?)",
            (first["agentRunId"], second["agentRunId"]),
        ).fetchall()
    assert remaining == []


def test_session_events_are_persisted_and_ordered(tmp_path, kernel_settings) -> None:
    model = ScriptedModel([ModelStep(output=[_final()])])
    application, app = _application(
        tmp_path,
        kernel_settings,
        model,
        lambda _request: httpx.Response(500),
        "groundwater",
    )
    client = TestClient(app)
    session = client.post("/api/v12/sessions", json={"projectId": "p"}).json()
    accepted, _ = _turn(
        client, application, session["sessionId"], "hi", read_only=True
    )
    assert accepted.status_code == 202
    events = application.interaction.events_after(session["sessionId"], 0)
    assert [item["event_type"] for item in events if item["event_type"] != "agent.activity"] == [
        "session.created",
        "turn.accepted",
        "intent_routing.requested",
        "intent_routing.resolved",
        "agent.running",
        "agent.complete",
    ]
    activities = [item["data"] for item in events if item["event_type"] == "agent.activity"]
    assert [item["status"] for item in activities if item["kind"] == "SEGMENT"] == [
        "QUEUED", "RUNNING", "COMPLETE",
    ]
    assert [item["status"] for item in activities if item["kind"] == "MODEL_REQUEST"] == [
        "RUNNING", "COMPLETE",
    ]
    assert (
        application.interaction.events_after(
            session["sessionId"], events[1]["event_id"]
        )
        == events[2:]
    )


def test_dispatch_retries_communication_with_stable_request_id(
    tmp_path, kernel_settings
) -> None:
    model = ScriptedModel(
        [
            _inspect(),
            ModelStep(output=[_plan(["run_summary"])]),
            ModelStep(
                output=[
                    function_call("run_summary", {"resource_id": "a"}, call_id="call")
                ]
            ),
            ModelStep(output=[_final()]),
        ]
    )
    submissions = []

    def handler(request):
        if "/by-request/" in request.url.path:
            return _executor_problem(404, "JOB_NOT_FOUND")
        if request.method == "POST":
            body = json.loads(request.content)
            submissions.append(body["requestId"])
            if len(submissions) == 1:
                return _executor_problem(503, "EXECUTOR_UNAVAILABLE")
            return httpx.Response(202, json=_job(body["requestId"], "QUEUED"))
        return httpx.Response(200, json=_job(submissions[-1], "SUCCEEDED"))

    application, app = _application(tmp_path, kernel_settings, model, handler)
    client = TestClient(app)
    session_id, agent_run_id, _ = _start_workflow(client, application)
    result = asyncio.run(_collect_tick(application))
    assert result.processed_count == 1
    assert result.ready_agent_run_ids == ()
    with connect_database(application.database) as connection:
        dispatch = connection.execute("SELECT * FROM workflow_dispatches").fetchone()
        assert dispatch["state"] == "SUBMIT_UNKNOWN"
        assert dispatch["last_communication_error"].startswith(
            "EXECUTION_SERVICE_UNAVAILABLE"
        )
        connection.execute("UPDATE workflow_dispatches SET next_poll_at = '0000'")
    result = asyncio.run(_collect_tick(application))
    assert result.processed_count == 1
    assert result.ready_agent_run_ids == ()
    with connect_database(application.database) as connection:
        connection.execute("UPDATE workflow_dispatches SET next_poll_at = '0000'")
    result = asyncio.run(_collect_tick(application))
    assert result.ready_agent_run_ids == (agent_run_id,)
    assert submissions[0] == submissions[1]
    assert application.agent_runtime.reconcile_execution(agent_run_id)
    run = asyncio.run(application.agent_runtime.resume_execution(agent_run_id))
    application.interaction.project(run)
    assert client.get(f"/api/v12/sessions/{session_id}").json()["agentOutput"]


def test_dispatch_rejects_mismatched_job_request_id(tmp_path, kernel_settings) -> None:
    model = ScriptedModel(
        [
            _inspect(),
            ModelStep(output=[_plan(["run_summary"])]),
            ModelStep(
                output=[
                    function_call("run_summary", {"resource_id": "a"}, call_id="call")
                ]
            ),
        ]
    )

    def handler(request):
        assert request.method == "POST"
        return httpx.Response(202, json=_job("different-run", "QUEUED"))

    application, app = _application(tmp_path, kernel_settings, model, handler)
    client = TestClient(app)
    _start_workflow(client, application)
    result = asyncio.run(_collect_tick(application))
    assert result.processed_count == 1
    assert result.ready_agent_run_ids == ()

    with connect_database(application.database) as connection:
        dispatch = connection.execute("SELECT * FROM workflow_dispatches").fetchone()
        workflow = connection.execute("SELECT * FROM workflow_runs").fetchone()
    assert dispatch["state"] == "SUBMIT_UNKNOWN"
    assert dispatch["claim_state"] is None
    assert dispatch["last_communication_error"] == (
        "EXECUTION_SERVICE_UNAVAILABLE:EXECUTION_JOB_REQUEST_MISMATCH"
    )
    assert dispatch["remote_job_id"] is None
    assert workflow["remote_job_id"] is None


def test_deadline_times_out_without_submitting_blocked_work(
    tmp_path, kernel_settings
) -> None:
    model = ScriptedModel(
        [
            _inspect(),
            ModelStep(output=[_plan(["run_summary"])]),
            ModelStep(
                output=[
                    function_call("run_summary", {"resource_id": "a"}, call_id="call")
                ]
            ),
            ModelStep(output=[_final("timed out")]),
        ]
    )
    requests = []
    application, app = _application(
        tmp_path,
        kernel_settings,
        model,
        lambda request: requests.append(request) or httpx.Response(500),
    )
    client = TestClient(app)
    _, agent_run_id, _ = _start_workflow(client, application)
    with connect_database(application.database) as connection:
        connection.execute(
            "UPDATE workflow_dispatches SET deadline_at = '2000-01-01T00:00:00Z'"
        )
    result = asyncio.run(_collect_tick(application))
    assert result.ready_agent_run_ids == (agent_run_id,)
    assert application.agent_runtime.reconcile_execution(agent_run_id)
    run = asyncio.run(application.agent_runtime.resume_execution(agent_run_id))
    assert run.status == "FAILED"
    assert run.actions[0].error == "WORKFLOW_DEADLINE_EXCEEDED"
    assert requests == []
    workflow_run = application.workflow_execution.for_agent(agent_run_id)[0]
    assert workflow_run.remote_job_id is None


def test_immediate_job_completion_is_atomic_with_workflow_event(
    tmp_path, kernel_settings
) -> None:
    model = ScriptedModel([
        _inspect(),
        ModelStep(output=[_plan(["run_summary"])]),
        ModelStep(output=[function_call(
            "run_summary", {"resource_id": "a"}, call_id="call"
        )]),
    ])

    def handler(request):
        body = json.loads(request.content)
        return httpx.Response(202, json=_job(body["requestId"], "SUCCEEDED"))

    application, app = _application(tmp_path, kernel_settings, model, handler)
    _, agent_run_id, _ = _start_workflow(TestClient(app), application)
    with connect_database(application.database) as connection:
        connection.execute("""CREATE TRIGGER reject_terminal_event
            BEFORE INSERT ON session_events WHEN NEW.event_type = 'workflow.succeeded'
            BEGIN SELECT RAISE(ABORT, 'terminal event unavailable'); END""")

    with pytest.raises(ExceptionGroup):
        asyncio.run(_collect_tick(application))

    workflow_run = application.workflow_execution.for_agent(agent_run_id)[0]
    assert workflow_run.status == "QUEUED"
    assert workflow_run.remote_job_id is None
    with connect_database(application.database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM session_events WHERE event_type = 'job.succeeded'"
        ).fetchone()[0] == 0


def test_restart_reconciles_terminal_job_before_sdk_resume(
    tmp_path, kernel_settings
) -> None:
    model = ScriptedModel(
        [
            _inspect(),
            ModelStep(output=[_plan(["run_summary"])]),
            ModelStep(
                output=[
                    function_call("run_summary", {"resource_id": "a"}, call_id="call")
                ]
            ),
            ModelStep(output=[_final("recovered")]),
        ]
    )
    request_id = None

    def handler(request):
        nonlocal request_id
        if request.method == "POST":
            request_id = json.loads(request.content)["requestId"]
            return httpx.Response(202, json=_job(request_id, "QUEUED"))
        return httpx.Response(200, json=_job(request_id, "SUCCEEDED"))

    application, app = _application(tmp_path, kernel_settings, model, handler)
    client = TestClient(app)
    session_id, agent_run_id, _ = _start_workflow(client, application)
    result = asyncio.run(_collect_tick(application))
    assert result.processed_count == 1
    assert result.ready_agent_run_ids == ()
    with connect_database(application.database) as connection:
        connection.execute("UPDATE workflow_dispatches SET next_poll_at = '0000'")
    result = asyncio.run(_collect_tick(application))
    assert result.ready_agent_run_ids == (agent_run_id,)
    assert application.agent_runtime.reconcile_execution(agent_run_id)
    claimed = application.agent_runtime.get(agent_run_id)
    with connect_database(application.database) as connection:
        connection.execute(
            "UPDATE agent_runs SET status = 'RUNNING', steps_json = ? "
            "WHERE agent_run_id = ?",
            (json.dumps([*claimed.steps, "RUNNING"]), agent_run_id),
        )
    assert application.agent_runtime.get(agent_run_id).status == "RUNNING"

    restarted = PlatformApplication(
        settings=kernel_settings(),
        model=model,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    async def recover():
        await restarted.runtime.start()
        await restarted.runtime.stop()

    asyncio.run(recover())
    assert restarted.agent_runtime.get(agent_run_id).status == "COMPLETE"
    assert (
        restarted.interaction.get(session_id).session.agent_output.message
        == "已完成 1 项分析。"
    )


def test_token_usage_counts_only_new_responses_after_execution_resume(
    tmp_path, kernel_settings
) -> None:
    model = ScriptedModel(
        [
            _inspect(),
            ModelStep(
                output=[_plan(["run_summary"])],
                usage=Usage(input_tokens=1, output_tokens=2, total_tokens=3),
            ),
            ModelStep(
                output=[
                    function_call("run_summary", {"resource_id": "a"}, call_id="call")
                ],
                usage=Usage(input_tokens=4, output_tokens=5, total_tokens=9),
            ),
            ModelStep(
                output=[_final()],
                usage=Usage(input_tokens=6, output_tokens=7, total_tokens=13),
            ),
        ]
    )
    request_id = None

    def handler(request):
        nonlocal request_id
        if request.method == "POST":
            request_id = json.loads(request.content)["requestId"]
            return httpx.Response(202, json=_job(request_id, "QUEUED"))
        return httpx.Response(200, json=_job(request_id, "SUCCEEDED"))

    application, app = _application(tmp_path, kernel_settings, model, handler)
    client = TestClient(app)
    _, agent_run_id, _ = _start_workflow(client, application)
    waiting = application.agent_runtime.get(agent_run_id)
    assert (waiting.input_tokens, waiting.output_tokens, waiting.total_tokens) == (
        5,
        7,
        12,
    )
    assert _tick(application) == (agent_run_id,)
    completed = application.agent_runtime.get(agent_run_id)
    assert (
        completed.input_tokens,
        completed.output_tokens,
        completed.total_tokens,
    ) == (11, 14, 25)


@pytest.mark.parametrize("job_status", ["SUCCEEDED", "FAILED"])
def test_minimax_terminalizes_execution_without_provider_acknowledgement(
    tmp_path, kernel_settings, job_status,
) -> None:
    model = ScriptedModel([
        _inspect(),
        ModelStep(output=[_plan(["run_summary"])]),
        ModelStep(output=[
            function_call("run_summary", {"resource_id": "a"}, call_id="call")
        ]),
    ])
    request_id = None

    def handler(request):
        nonlocal request_id
        if request.method == "POST":
            request_id = json.loads(request.content)["requestId"]
            return httpx.Response(202, json=_job(request_id, "QUEUED"))
        return httpx.Response(200, json=_job(
            request_id,
            job_status,
            error="OPERATOR_FAILED" if job_status == "FAILED" else None,
        ))

    application, app = _application(
        tmp_path,
        kernel_settings,
        model,
        handler,
        settings_transform=lambda settings: replace(
            settings,
            agent_provider="minimax",
            agent_protocol="chat_completions",
        ),
    )
    client = TestClient(app)
    session_id, agent_run_id, _ = _start_workflow(client, application)

    assert _tick(application) == (agent_run_id,)

    completed = application.agent_runtime.get(agent_run_id)
    session = client.get(f"/api/v12/sessions/{session_id}").json()
    expected_status = "COMPLETE" if job_status == "SUCCEEDED" else "FAILED"
    assert completed.status == expected_status
    assert len(model.calls) == 3
    assert session["agentSteps"][-1] == expected_status
    assert session["workflowRunIds"] == [completed.actions[0].workflow_run_id]
    if job_status == "SUCCEEDED":
        assert "已完成" in session["agentOutput"]["message"]
    else:
        assert completed.error == "OPERATOR_FAILED"
        assert "执行未完成" in session["agentOutput"]["message"]
    assert "开发调试案例" not in session["agentOutput"]["message"]


def test_minimax_terminal_summary_deduplicates_inputs_and_uses_action_plans(
    tmp_path, kernel_settings,
) -> None:
    model = ScriptedModel([
        _inspect(),
        ModelStep(output=[_plan(["run_summary"] * 3)]),
        ModelStep(output=[
            function_call(
                "run_summary",
                {"resource_id": "a", "axis": "columns"},
                call_id="call-a",
            ),
            function_call(
                "run_summary",
                {"resource_id": "b", "axis": "rows"},
                call_id="call-b",
            ),
            function_call(
                "run_summary",
                {"resource_id": "a", "axis": "flatten"},
                call_id="call-a-again",
            ),
        ]),
    ])
    jobs = {}

    def handler(request):
        if request.method == "POST":
            request_id = json.loads(request.content)["requestId"]
            job_id = f"job-{len(jobs) + 1}"
            jobs[job_id] = request_id
            return httpx.Response(202, json={
                **_job(request_id, "QUEUED"),
                "jobId": job_id,
            })
        job_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={
            **_job(jobs[job_id], "SUCCEEDED"),
            "jobId": job_id,
        })

    application, app = _application(
        tmp_path,
        kernel_settings,
        model,
        handler,
        settings_transform=lambda settings: replace(
            settings,
            agent_provider="minimax",
            agent_protocol="chat_completions",
        ),
    )
    client = TestClient(app)
    session_id, agent_run_id, _ = _start_workflow(
        client, application, calls=3,
    )
    second_revision = save_plan(
        application.database,
        agent_run_id,
        "second execution",
        [AgentPlanStepV2(
            step_id="later",
            tool="run_summary",
            purpose="execute later step",
        )],
        "persisted second plan",
    )
    with connect_database(application.database) as connection:
        connection.execute(
            "UPDATE agent_actions SET plan_revision=? "
            "WHERE agent_run_id=? AND tool_call_id='call-a-again'",
            (second_revision, agent_run_id),
        )

    for _ in range(4):
        _tick(application)
        completed = application.agent_runtime.get(agent_run_id)
        if completed.status in {"COMPLETE", "FAILED"}:
            break
    assert completed.status == "COMPLETE"
    message = client.get(
        f"/api/v12/sessions/{session_id}"
    ).json()["agentOutput"]["message"]
    assert "已使用 2 项冻结输入" in message
    assert "execute" in message
    assert "second execution" in message
    assert "开发调试案例" not in message


def test_invalid_final_keeps_usage_and_forms_failed_terminal(
    tmp_path, kernel_settings
) -> None:
    model = ScriptedModel(
        [
            ModelStep(
                output=[
                    submit_reply('bad', refs=['missing'])
                ],
                usage=Usage(input_tokens=5, output_tokens=7, total_tokens=12),
            ),
            ModelStep(
                output=[
                    submit_reply('still bad', refs=['missing'])
                ],
                usage=Usage(input_tokens=5, output_tokens=7, total_tokens=12),
            ),
        ]
    )
    application, app = _application(
        tmp_path,
        kernel_settings,
        model,
        lambda _request: httpx.Response(500),
        "groundwater",
    )
    client = TestClient(app)
    session = client.post("/api/v12/sessions", json={"projectId": "p"}).json()
    response, _ = _turn(client, application, session["sessionId"], "fail")
    assert response.status_code == 202
    run = application.agent_runtime.get(response.json()["agentRunId"])
    assert run.status == "FAILED"
    assert (run.input_tokens, run.output_tokens, run.total_tokens) == (10, 14, 24)


def test_model_tool_surrogate_forms_failed_terminal(tmp_path, kernel_settings) -> None:
    model = ScriptedModel(
        [
            ModelStep(
                output=[
                    function_call(
                        "record_plan",
                        {
                            "goal": "\ud800",
                            "steps": [],
                            "reason": "invalid",
                        },
                        call_id="invalid-plan",
                    )
                ],
                usage=Usage(input_tokens=2, output_tokens=3, total_tokens=5),
            )
        ]
    )
    application, app = _application(
        tmp_path,
        kernel_settings,
        model,
        lambda _request: httpx.Response(500),
        "groundwater",
    )
    client = TestClient(app)
    session = client.post("/api/v12/sessions", json={"projectId": "p"}).json()
    response, _ = _turn(client, application, session["sessionId"], "invalid")
    assert response.status_code == 202
    run = application.agent_runtime.get(response.json()["agentRunId"])
    assert (run.status, run.error) == ("FAILED", "AGENT_MODEL_OUTPUT_INVALID")
    assert (run.input_tokens, run.output_tokens, run.total_tokens) == (2, 3, 5)


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_nonfinite_model_argument_fails_without_persisting_confirmation(
    kernel_settings, number
) -> None:
    settings = kernel_settings()
    definition = settings.models[0]
    schema = {
        **definition.input_schema,
        "properties": {
            **definition.input_schema["properties"],
            "threshold": {"type": "number"},
        },
    }
    settings = replace(settings, models=(replace(definition, input_schema=schema),))
    model = ScriptedModel([
        _inspect(),
        ModelStep(output=[_plan(["run_summary"])]),
        ModelStep(
            output=[function_call(
                "run_summary",
                '{"resource_id":"a","threshold":' + number + '}',
                call_id="invalid-number",
            )],
            usage=Usage(input_tokens=2, output_tokens=3, total_tokens=5),
        ),
        ModelStep(output=[_final("next answer")]),
    ])
    application = PlatformApplication(
        settings=settings, model=model,
        client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: _data_response(request)
            if request.url.host == "data-service" else httpx.Response(500)
        )),
    )
    client = TestClient(create_app(application=application))
    session = client.post("/api/v12/sessions", json={"projectId": "p"}).json()
    session_id = session["sessionId"]
    response, _ = _turn(client, application, session_id, "run")
    assert response.status_code == 202
    run = application.agent_runtime.get(response.json()["agentRunId"])
    assert (run.status, run.error) == ("FAILED", "AGENT_MODEL_OUTPUT_INVALID")
    assert (run.input_tokens, run.output_tokens, run.total_tokens) == (2, 3, 5)
    with connect_database(application.database) as connection:
        for table in ("agent_actions", "agent_run_state", "confirmations", "workflow_runs"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert client.get(f"/api/v12/sessions/{session_id}").status_code == 200
    accepted, response = _turn(
        client, application, session_id, "continue", read_only=True
    )
    assert accepted.status_code == 202 and response.status_code == 200
    assert response.json()["agentOutput"]["message"] == "next answer"


def _job(request_id, status, outputs=None, error=None):
    return {
        "jobId": "job-1",
        "requestId": request_id,
        "status": status,
        "stage": status,
        "error": error,
        "outputs": outputs or [],
        "serviceVersion": "1",
        "createdAt": None,
        "startedAt": None,
        "finishedAt": None,
    }


def _executor_problem(status: int, code: str) -> httpx.Response:
    return httpx.Response(
        status,
        headers={"content-type": "application/problem+json"},
        json={
            "type": f"urn:platform:problem:{code.lower().replace('_', '-')}",
            "title": "Executor fixture failure",
            "status": status,
            "detail": "The executor fixture rejected the request.",
            "code": code,
            "requestId": "fixture-request",
            "traceId": "fixture-trace",
            "errors": [],
        },
    )


@pytest.mark.parametrize("remote_exists", [True, False])
def test_unknown_submit_after_deadline_waits_for_authoritative_lookup(
    tmp_path, kernel_settings, monkeypatch, remote_exists
):
    model = ScriptedModel([
        _inspect(),
        ModelStep(output=[_plan(["run_summary"])]),
        ModelStep(output=[function_call("run_summary", {"resource_id": "a"}, call_id="call")]),
    ])
    requests, run_ids = [], []
    unavailable, finished, expired = True, False, False

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            run_ids.append(json.loads(request.content)["requestId"])
            return _executor_problem(503, "EXECUTOR_UNAVAILABLE")
        if unavailable:
            return _executor_problem(503, "EXECUTOR_UNAVAILABLE")
        if "/by-request/" in request.url.path and not remote_exists:
            return _executor_problem(404, "JOB_NOT_FOUND")
        return httpx.Response(200, json=_job(run_ids[0], "SUCCEEDED" if finished else "RUNNING"))

    monkeypatch.setattr("platform_kernel.workflow_execution.owner._expired", lambda _: expired)
    application, app = _application(tmp_path, kernel_settings, model, handler)
    session_id, agent_run_id, _ = _start_workflow(TestClient(app), application)

    def tick():
        with connect_database(application.database) as connection:
            connection.execute("UPDATE workflow_dispatches SET next_poll_at='0000'")
        return asyncio.run(_collect_tick(application))

    assert tick().ready_agent_run_ids == ()
    expired = True
    application.workflow_execution.recover_interrupted()
    assert tick().ready_agent_run_ids == ()
    assert application.workflow_execution.for_agent(agent_run_id)[0].status == "QUEUED"
    unavailable = False
    outcome = tick()
    if remote_exists:
        assert outcome.ready_agent_run_ids == ()
        assert application.workflow_execution.for_agent(agent_run_id)[0].status == "RUNNING"
        finished = True
        assert tick().ready_agent_run_ids == (agent_run_id,)
        assert application.workflow_execution.for_agent(agent_run_id)[0].status == "SUCCEEDED"
    else:
        assert outcome.ready_agent_run_ids == (agent_run_id,)
        assert application.workflow_execution.for_agent(agent_run_id)[0].status == "TIMED_OUT"
    assert len(run_ids) == 1
    assert all(request.method == "GET" for request in requests[1:])
    assert tick().processed_count == 0
    event_type = "workflow.succeeded" if remote_exists else "workflow.timed_out"
    with connect_database(application.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM session_events WHERE session_id=? AND event_type=?", (session_id, event_type)).fetchone()[0] == 1


def test_lookup_crossing_deadline_requires_another_post_deadline_confirmation(tmp_path, kernel_settings, monkeypatch):
    model = ScriptedModel([
        _inspect(),
        ModelStep(output=[_plan(["run_summary"])]),
        ModelStep(output=[function_call("run_summary", {"resource_id": "a"}, call_id="call")]),
    ])
    expired, run_id, lookups = False, None, 0

    def handler(request):
        nonlocal expired, run_id, lookups
        if request.method == "POST":
            run_id = json.loads(request.content)["requestId"]
            return _executor_problem(503, "EXECUTOR_UNAVAILABLE")
        lookups += 1
        if lookups == 1:
            # This 404 was decided before the deadline, while the initial
            # submission could still create its Job. Its response arrives late.
            expired = True
            return _executor_problem(404, "JOB_NOT_FOUND")
        return httpx.Response(200, json=_job(run_id, "RUNNING"))

    monkeypatch.setattr("platform_kernel.workflow_execution.owner._expired", lambda _: expired)
    application, app = _application(tmp_path, kernel_settings, model, handler)
    _, agent_run_id, _ = _start_workflow(TestClient(app), application)
    assert asyncio.run(_collect_tick(application)).ready_agent_run_ids == ()
    with connect_database(application.database) as connection:
        connection.execute("UPDATE workflow_dispatches SET next_poll_at='0000'")
    assert asyncio.run(_collect_tick(application)).ready_agent_run_ids == ()
    assert lookups == 2
    assert application.workflow_execution.for_agent(agent_run_id)[0].status == "RUNNING"
