"""Interaction-option contract tests and shared Agent test helpers."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from uuid import uuid4

from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call
from fastapi.testclient import TestClient
import httpx
from pydantic import ValidationError
import pytest

from platform_kernel.api import create_app
from platform_kernel.agent_runtime.models import InteractionOptionV1, SubmittedReplyV6
from platform_kernel.composition import PlatformApplication
from platform_kernel.database import connect_database
from platform_kernel.settings import ObservationToolDefinition


KNOWLEDGE = {
    "version_id": "knowledge-version-1",
    "unit_id": "unit-0",
    "checksum": "sha256:" + "1" * 64,
}
RESOURCE = {"id": "table-1", "dataset_id": "dataset-1", "semantic_id": "semantics-1", "status": "OK", "objects": [{"id": "object-1", "role": "raw", "format": "csv", "media_type": "text/csv", "size": 8, "checksum": "sha256:" + "2" * 64}]}


def _reply(_unused=None, *, cited=True, options=None, call_id="reply"):
    return ModelStep(output=[function_call("submit_reply", {
        "conclusion": {"text": "Method comparison prepared.", "citations": [KNOWLEDGE] if cited else []},
        "details": [],
        "unresolved_items": [], "referenced_workflow_runs": [],
        "include_execution_status": False,
        "interaction_options": options or [], "workflow_draft": None,
    }, call_id=call_id)])


def _read(call_id="read", checksum=KNOWLEDGE["checksum"]):
    return ModelStep(output=[function_call("read_evidence", {"refs": [{"version_id": KNOWLEDGE["version_id"], "unit_id": KNOWLEDGE["unit_id"], "checksum": checksum}]}, call_id=call_id)])


def _inspect(resource_id="table-1", call_id="inspect"):
    return ModelStep(output=[function_call(
        "inspect_data_version", {"version_id": resource_id}, call_id=call_id,
    )])


def _plan(tools=("run_summary",), call_id="plan"):
    return ModelStep(output=[function_call("record_plan", {
        "goal": "Execute the requested analysis.",
        "reason": "The current evidence supports this batch.",
        "steps": [{"step_id": str(index), "tool": tool, "purpose": "Perform this step."}
                  for index, tool in enumerate(tools)],
    }, call_id=call_id)])


def _execute(tool="run_summary", resource_id="table-1", call_id="run"):
    return ModelStep(output=[function_call(
        tool, {"resource_id": resource_id}, call_id=call_id,
    )])


def _application(kernel_settings, steps, handler=None):
    models = kernel_settings().models
    settings = kernel_settings("knowledge")
    inspect_schema = {"type": "object", "properties": {"version_id": {"type": "string"}},
                      "required": ["version_id"], "additionalProperties": False}
    inspect_result = {"type": "object"}
    inspect = ObservationToolDefinition(
        "inspect_data_version", "Inspect exact Data version metadata.", "data",
        "/internal/tools/v3/inspect_data_version", inspect_schema, inspect_result,
        "sha256:in", "sha256:out", 10,
    )

    def service(request):
        if handler is not None:
            response = handler(request)
            if response is not None:
                return response
        if request.url.path.endswith("read_evidence"):
            return httpx.Response(200, json={"data": {"status": "OK", "items": [{
                "versionId": KNOWLEDGE["version_id"],
                "unitId": KNOWLEDGE["unit_id"],
                "checksum": KNOWLEDGE["checksum"],
                "status": "OK", "bindingMatch": "EXACT", "knowledgeId": "model-card",
                "content": "PRIVATE BODY: summarize tables.",
            }]}})
        if request.url.path.endswith("inspect_data_version"):
            version_id = json.loads(request.content)["arguments"]["version_id"]
            item = RESOURCE["objects"][0]
            return httpx.Response(200, json={"data": {
                "versionId": version_id,
                "datasetId": RESOURCE["dataset_id"],
                "semanticId": RESOURCE["semantic_id"],
                "status": RESOURCE["status"],
                "objects": [{
                    "objectId": item["id"],
                    "role": item["role"],
                    "format": item["format"],
                    "size": item["size"],
                    "checksum": item["checksum"],
                    "mediaType": item["media_type"],
                }],
            }})
        raise AssertionError(f"Unexpected downstream call: {request.url}")

    model = ScriptedModel(steps)
    route = assistant_message(json.dumps({
        "schemaVersion": "intent-decision.v4",
        "responseKind": "DIALOGUE",
        "semanticIntent": "EXECUTE_ON_DATA",
        "taskAction": "NONE",
        "dialoguePolicy": "EXECUTION_PROPOSAL",
        "controlIntent": None,
        "clarificationReason": None,
    }))
    application = PlatformApplication(
        settings=replace(settings, models=models, observation_tools=(*settings.observation_tools, inspect)),
        model=model, client=httpx.AsyncClient(transport=httpx.MockTransport(service)),
        intent_router_model=ScriptedModel([
            ModelStep(output=[route]) for _ in range(20)
        ]),
    )
    return application, TestClient(create_app(application=application)), model


def _drain(application, session_id):
    async def drive():
        while await application.interaction.tick():
            pass
        await application.interaction.resume_pending_start(session_id, propagate_error=False)
    return asyncio.run(drive())


def _message(
    client, session_id, message, *, requested_mode="DIALOGUE", request_id=None,
    **_unused,
):
    return client.post(f"/api/v12/sessions/{session_id}/messages", json={
        "requestId": request_id or str(uuid4()), "message": message,
        "requestedMode": requested_mode,
    })


def _dialogue(application, client, session_id, message):
    response = _message(client, session_id, message)
    assert response.status_code == 202, response.text
    _drain(application, session_id)
    return client.get(f"/api/v12/sessions/{session_id}")


def test_interaction_options_are_trimmed_bounded_and_unique():
    valid = {"label": "比较方法", "description": "比较资料要求与限制", "message": "请比较可行方法。"}
    assert InteractionOptionV1.model_validate(valid).message == "请比较可行方法。"
    for value in (
        {**valid, "label": " 比较方法"},
        {**valid, "label": "x" * 25},
        {**valid, "description": ""},
    ):
        with pytest.raises(ValidationError):
            InteractionOptionV1.model_validate(value)
    with pytest.raises(ValidationError):
        SubmittedReplyV6.model_validate({
            "conclusion": None, "details": [], "unresolved_items": [],
            "referenced_workflow_runs": [],
            "include_execution_status": False, "interaction_options": [valid, valid],
            "workflow_draft": None,
        })


def test_completed_dialogue_discards_interaction_options_without_clarification(
    kernel_settings,
):
    options = [{
        "label": "比较趋势方法", "description": "比较资料要求和输出差异",
        "message": "请比较当前资料可用的趋势分析方法，并说明各自限制。",
    }]
    app, client, _ = _application(kernel_settings, [
        _reply(cited=False, options=options, call_id="reply-1"),
    ])
    session_id = client.post("/api/v12/sessions", json={"projectId": "p"}).json()["sessionId"]
    response = _message(client, session_id, "推荐适合的趋势分析方法")
    assert response.status_code == 202
    _drain(app, session_id)
    run = app.agent_runtime.get(response.json()["agentRunId"])
    assert (run.status, run.error) == ("COMPLETE", None)
    assert run.output is not None and run.output.interaction_options == []
    history = client.get(f"/api/v12/sessions/{session_id}/messages").json()
    assert history["turns"][0]["interactionOptions"] == []
    with connect_database(app.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM confirmations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] == 0
