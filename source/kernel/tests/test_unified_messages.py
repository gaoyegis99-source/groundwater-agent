from __future__ import annotations

from kernel.tests.test_recommendations import _application, _drain, _reply
from platform_kernel.database import connect_database


def test_explicit_dialogue_receipt_is_v5_read_only_and_has_no_recommendation_fields(kernel_settings):
    _app, client, _ = _application(kernel_settings, [])
    session_id = client.post("/api/v12/sessions", json={"projectId": "p"}).json()["sessionId"]
    response = client.post(f"/api/v12/sessions/{session_id}/messages", json={
        "requestId": "request-1", "message": "hello", "requestedMode": "DIALOGUE",
    })
    assert response.status_code == 202
    receipt = response.json()
    assert receipt["schemaVersion"] == "message-request.v5"
    assert receipt["request"]["inputReferences"] == []
    assert receipt["intent"]["schemaVersion"] == "intent-decision.v4"
    assert receipt["semanticIntent"] == "UNSPECIFIED"
    assert receipt["taskAction"] == "NONE"
    assert receipt["dialoguePolicy"] == "READ_ONLY"
    assert receipt["resolvedMode"] == "DIALOGUE"
    assert receipt["routingSource"] == "EXPLICIT"
    assert "recommendation_id" not in receipt
    assert "selection_revision" not in receipt
    assert "recommendation_selection" not in receipt["request"]


def test_message_modes_are_closed_and_old_selection_shape_is_rejected(kernel_settings):
    _app, client, _ = _application(kernel_settings, [])
    session_id = client.post("/api/v12/sessions", json={"projectId": "p"}).json()["sessionId"]
    for mode in ("RECOMMENDATION", "SELECTION"):
        response = client.post(f"/api/v12/sessions/{session_id}/messages", json={
            "requestId": mode, "message": "legacy", "requestedMode": mode,
            "recommendationSelection": {
                "recommendationId": "old", "selectionRevision": 1,
            },
        })
        assert response.status_code == 422


def test_explicit_dialogue_agent_has_no_execution_or_plan_tools(kernel_settings):
    app, client, model = _application(kernel_settings, [_reply(cited=False)])
    session_id = client.post("/api/v12/sessions", json={"projectId": "p"}).json()["sessionId"]
    response = client.post(f"/api/v12/sessions/{session_id}/messages", json={
        "requestId": "read-only", "message": "比较这些方法", "requestedMode": "DIALOGUE",
    })
    assert response.status_code == 202
    _drain(app, session_id)

    tool_names = {tool.name for tool in model.calls[0].tools}
    assert "record_plan" not in tool_names
    assert not any(name.startswith("run_") for name in tool_names)
    with connect_database(app.database) as connection:
        counts = {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in ("agent_plans", "confirmations", "workflow_runs")
        }
    assert counts == {"agent_plans": 0, "confirmations": 0, "workflow_runs": 0}


def _input_reference(version: str = "version-1", object_id: str = "object-1"):
    return {
        "kind": "data_version", "service": "data-primary",
        "datasetId": "dataset-1", "versionId": version,
        "semanticId": "semantic-1", "objectId": object_id,
        "mediaType": "text/csv", "format": "csv", "sizeBytes": 12,
        "checksum": "sha256:" + "a" * 64,
    }


def test_message_v5_persists_structured_inputs_without_polluting_text(kernel_settings):
    _app, client, _ = _application(kernel_settings, [])
    session_id = client.post("/api/v12/sessions", json={"projectId": "p"}).json()["sessionId"]
    reference = _input_reference()
    response = client.post(f"/api/v12/sessions/{session_id}/messages", json={
        "requestId": "with-input", "message": "分析这个数据", "requestedMode": "DIALOGUE",
        "inputReferences": [reference],
    })
    assert response.status_code == 202
    assert response.json()["request"]["message"] == "分析这个数据"
    assert response.json()["request"]["inputReferences"] == [reference]

    history = client.get(f"/api/v12/sessions/{session_id}/messages").json()
    user = next(item for item in history["items"] if item["role"] == "user")
    assert user["content"] == "分析这个数据"
    assert user["inputReferences"] == [reference]


def test_message_v5_reference_identity_is_part_of_idempotency(kernel_settings):
    _app, client, _ = _application(kernel_settings, [])
    session_id = client.post("/api/v12/sessions", json={"projectId": "p"}).json()["sessionId"]
    body = {
        "requestId": "same", "message": "分析", "requestedMode": "DIALOGUE",
        "inputReferences": [_input_reference()],
    }
    assert client.post(f"/api/v12/sessions/{session_id}/messages", json=body).status_code == 202
    assert client.post(f"/api/v12/sessions/{session_id}/messages", json=body).status_code == 202
    changed = {**body, "inputReferences": [_input_reference("version-2", "object-2")]}
    assert client.post(f"/api/v12/sessions/{session_id}/messages", json=changed).status_code == 409


def test_message_v5_rejects_duplicates_and_attached_control_text_is_a_task(kernel_settings):
    app, client, router = _application(kernel_settings, [])
    session_id = client.post("/api/v12/sessions", json={"projectId": "p"}).json()["sessionId"]
    duplicate = {
        "requestId": "duplicate", "message": "分析", "requestedMode": "DIALOGUE",
        "inputReferences": [_input_reference(), _input_reference()],
    }
    assert client.post(f"/api/v12/sessions/{session_id}/messages", json=duplicate).status_code == 422
    response = client.post(f"/api/v12/sessions/{session_id}/messages", json={
        "requestId": "attached-confirm", "message": "确认", "requestedMode": "AUTO",
        "inputReferences": [_input_reference()],
    })
    assert response.status_code == 202
    assert response.json()["routingSource"] == "AUTO"
    with connect_database(app.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM control_proposals").fetchone()[0] == 0


def test_message_v5_accepts_twenty_unique_inputs_and_rejects_twenty_one(kernel_settings):
    _app, client, _ = _application(kernel_settings, [])
    session_id = client.post("/api/v12/sessions", json={"projectId": "p"}).json()["sessionId"]
    references = [_input_reference(f"version-{index}", f"object-{index}") for index in range(21)]
    accepted = client.post(f"/api/v12/sessions/{session_id}/messages", json={
        "requestId": "twenty", "message": "分析", "requestedMode": "DIALOGUE",
        "inputReferences": references[:20],
    })
    assert accepted.status_code == 202
    rejected = client.post(f"/api/v12/sessions/{session_id}/messages", json={
        "requestId": "twenty-one", "message": "分析", "requestedMode": "DIALOGUE",
        "inputReferences": references,
    })
    assert rejected.status_code == 422
