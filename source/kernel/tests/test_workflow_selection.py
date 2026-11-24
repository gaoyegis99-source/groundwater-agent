from __future__ import annotations

from dataclasses import replace
import asyncio
import hashlib
import json

from agents.testing import ModelStep, function_call
import httpx
import pytest

from kernel.tests.test_recommendations import _application, _drain, _inspect
from platform_kernel.agent_runtime import preparation_tool
from platform_kernel.agent_runtime.mode import resolve_mode_capabilities
from platform_kernel.agent_runtime.persistence.run_queries import load_request_context
from platform_kernel.database import connect_database
from platform_kernel.errors import ConfirmationConflictError
from platform_kernel.interaction.continuations import verify_continuation_review
from platform_kernel.workflow_selection import require_selected_arguments


def _request(model):
    return {
        "requestId": "preset-request",
        "message": "请准备所选工作流",
        "requestedMode": "DIALOGUE",
        "workflowSelection": {
            "modelId": model.model_id,
            "modelVersion": model.model_version,
            "modelChecksum": model.model_checksum,
            "workflowId": model.workflow_id,
            "workflowVersion": model.workflow_version,
            "workflowChecksum": model.workflow_checksum,
            "inputBindings": {model.data_input_parameters[0]: 0},
        },
        "inputReferences": [{
            "kind": "data_version", "service": "data-primary", "datasetId": "dataset",
            "versionId": "version", "semanticId": "semantic", "objectId": "object",
            "mediaType": "text/csv", "format": "csv", "sizeBytes": 9, "checksum": "sha256:" + "a" * 64,
        }],
    }


def test_selected_preset_is_durable_and_skips_intent_routing(kernel_settings):
    app, client, _ = _application(kernel_settings, [])
    model = app.settings.models[0]
    target_input = model.data_input_parameters[0]
    session = client.post('/api/v12/sessions', json={"projectId": "p"}).json()['sessionId']
    route = f'/api/v12/sessions/{session}/messages'
    request = _request(model)
    posted = client.post(route, json=request)
    assert posted.status_code == 202, posted.text
    receipt = posted.json()
    assert receipt['routingStatus'] == 'DECIDED'
    context = load_request_context(app.database, receipt['agentRunId'])
    assert context['dialogue_policy'] == 'EXECUTION_PROPOSAL'
    alternative = replace(model, model_id="another", tool_name="different")
    assert resolve_mode_capabilities(context, (model, alternative)).models == (model,)
    selected = context['workflow_selection']
    require_selected_arguments(selected, {target_input: 'version'}, selected['inputs'])
    with pytest.raises(ValueError, match='WORKFLOW_SELECTION_ARGUMENTS_CHANGED'):
        require_selected_arguments(selected, {target_input: 'other'})
    with pytest.raises(ValueError, match='WORKFLOW_SELECTION_DATA_CHANGED'):
        require_selected_arguments(selected, {target_input: 'version'}, {target_input: {**selected['inputs'][target_input], 'semantic_id': 'other'}})
    assert client.post(route, json=request).json() == receipt
    request['workflowSelection']['inputBindings'] = {}
    assert client.post(route, json=request).status_code == 409
    request['requestId'] = 'wrong-model-version'
    request['workflowSelection']['modelVersion'] = '0.0.0'
    assert client.post(route, json=request).status_code == 409
    with connect_database(app.database) as connection:
        assert connection.execute('SELECT COUNT(*) FROM workflow_runs').fetchone()[0] == 0


@pytest.mark.parametrize(
    "selected, proposed",
    [(0, 0.0), (3.0, 3), ({"thresholds": [0, 2.5], "enabled": False}, {"enabled": False, "thresholds": [0.0, 2.5]})],
)
def test_selected_parameters_accept_equivalent_numeric_representations(selected, proposed):
    selection = {"parameters": {"parameter": selected}, "inputs": {}}
    require_selected_arguments(selection, {"parameter": proposed})


@pytest.mark.parametrize(
    "selected, proposed",
    [
        (0, False),
        (False, 0.0),
        (0, "0"),
        (0, -0.0),
        (0.0, 0.25),
        (2**53 + 1, float(2**53 + 1)),
        ({"enabled": True}, {"enabled": 1}),
        ({"thresholds": [0, 1]}, {"thresholds": [1, 0]}),
    ],
)
def test_selected_parameters_reject_changed_values_and_types(selected, proposed):
    selection = {"parameters": {"parameter": selected}, "inputs": {}}
    with pytest.raises(ValueError, match="WORKFLOW_SELECTION_ARGUMENTS_CHANGED"):
        require_selected_arguments(selection, {"parameter": proposed})

@pytest.mark.parametrize('status', ['BLOCKED', 'REVIEW_REQUIRED'])
def test_workflow_continuation_reads_exact_published_review(status):
    content = json.dumps({'status': status}).encode()
    report = {'size_bytes': len(content), 'checksum': 'sha256:' + hashlib.sha256(content).hexdigest(),
              'version_id': 'version', 'object_id': 'object'}

    def upstream(request):
        assert request.url.path == '/data/v3/versions/version/objects/object/content'
        assert request.headers['X-Project-ID'] == 'p'
        assert request.headers['X-Platform-Subject'] == 'researcher1'
        return httpx.Response(200, content=content)

    async def verify():
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            await verify_continuation_review(client, 'http://data:8000', 'p', 'researcher1', '研究员', report)

    if status == 'BLOCKED':
        with pytest.raises(ConfirmationConflictError, match='WORKFLOW_CONTINUATION_BLOCKED'):
            asyncio.run(verify())
    else:
        asyncio.run(verify())


@pytest.mark.parametrize("scenario", ["missing", "missing_data", "existing", "complete_arguments"])
def test_preset_clarification_records_only_missing_contract_inputs(kernel_settings, scenario):
    def settings_with_required_field(capabilities="summary"):
        settings = kernel_settings(capabilities)
        if not settings.models:
            return settings
        model, = settings.models
        schema = {
            **model.input_schema,
            "properties": {**model.input_schema["properties"], "valueField": {"type": "string"}},
            "required": [*model.input_schema["required"], "valueField"],
        }
        return replace(settings, models=(replace(model, input_schema=schema),))

    steps = [_inspect(resource_id="version")]
    if scenario == "existing":
        steps.append(ModelStep(output=[function_call("prepare_task_inputs", {
            "target_tool": "run_summary", "arguments": {"resource_id": "version"},
        }, call_id="check-fields")]))
    steps.append(ModelStep(output=[function_call("submit_reply", {
        "conclusion": {"text": "请确认要使用的数值列。", "citations": []},
        "details": [], "referenced_workflow_runs": [],
        "unresolved_items": [{"text": "请指定监测表中用于统计的数值列。", "citations": []}],
        "include_execution_status": False, "interaction_options": [], "workflow_draft": None,
    }, call_id="ask-for-field")]))
    app, client, _ = _application(settings_with_required_field, steps)
    model, = app.settings.models
    session = client.post("/api/v12/sessions", json={"projectId": "p"}).raise_for_status().json()
    route = f"/api/v12/sessions/{session['sessionId']}"
    request = _request(model)
    if scenario == "complete_arguments":
        request["workflowSelection"]["parameters"] = {"valueField": "value"}
    elif scenario == "missing_data":
        request["workflowSelection"]["parameters"] = {"valueField": "value"}
        request["workflowSelection"]["inputBindings"] = {}
    response = client.post(route + "/messages", json=request)
    assert response.status_code == 202, response.text
    _drain(app, session["sessionId"])

    snapshot = client.get(route).raise_for_status().json()
    assert snapshot["agentSteps"][-1] == "COMPLETE"
    assert snapshot["agentOutput"]["unresolvedItems"]
    assert snapshot["confirmation"] is None and snapshot["workflowRunIds"] == []
    preparation = snapshot["preparation"]
    if scenario == "complete_arguments":
        assert preparation is None
    else:
        assert preparation is not None and preparation["status"] == "BLOCKED"
        assert preparation["goalRevision"] == snapshot["goalRevision"]
        assert preparation["freshForCurrentTurn"]
        assert preparation["disposition"] == "ACTIVE"
        assert preparation["revision"] == 1
        entry, = preparation["entries"]
        assert entry["targetId"] == model.model_id
        issue, = entry["blockingIssues"]
        if scenario == "missing_data":
            assert (issue["kind"], issue["input"]) == ("MISSING_DATA", "resource_id")
        else:
            assert (issue["kind"], issue["parameter"]) == ("MISSING_PARAMETER", "valueField")
            assert "valueField" not in entry["parameters"]
        assert not entry["inputs"] and not entry["checks"]
        assert client.get(route + "/preparation").raise_for_status().json() == preparation


def test_preset_clarification_keeps_data_derived_profile_fields_for_preparation(kernel_settings):
    model, = kernel_settings().models
    selection = {
        "parameters": {},
        "inputs": {"resource_id": {"version_id": "version"}},
        "business_capability": {
            "requiredParameters": [{"name": "stationField"}, {"name": "eventThreshold"}],
            "metadataParameters": {"stationField": "GROUP_FIELD"},
            "argumentSources": {},
        },
    }
    snapshot = preparation_tool.preset_clarification_snapshot(selection, model)
    assert snapshot is not None
    issue, = snapshot["blocking_issues"]
    assert (issue["code"], issue["parameter"]) == ("CAPABILITY_PARAMETER_REQUIRED", "eventThreshold")
    assert not snapshot["inputs"] and not snapshot["checks"]
    selection["parameters"] = {"eventThreshold": 0}
    assert preparation_tool.preset_clarification_snapshot(selection, model) is None
