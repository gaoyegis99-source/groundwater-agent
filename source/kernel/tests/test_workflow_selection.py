from __future__ import annotations

from dataclasses import replace
import asyncio
import hashlib
import json

import httpx
import pytest

from kernel.tests.test_recommendations import _application
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
