from __future__ import annotations

import hashlib
import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

from test_sustainability_tasks import sustainability_payload


def digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def upload_json(client: TestClient, document: dict) -> dict:
    content = json.dumps(document, ensure_ascii=False).encode()
    created = client.post(
        "/api/v1/uploads",
        json={
            "name": "assessment-input.json",
            "sizeBytes": len(content),
            "sha256": digest(content),
            "mediaType": "application/json",
            "format": "json",
        },
    )
    assert created.status_code == 201
    session = created.json()
    part = client.put(
        f"/api/v1/uploads/{session['uploadId']}/parts/0",
        content=content,
        headers={"X-Content-SHA256": digest(content)},
    )
    assert part.status_code == 200
    completed = client.post(f"/api/v1/uploads/{session['uploadId']}/complete")
    assert completed.status_code == 200
    return completed.json()


def test_business_routes_require_bearer(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        database_path=tmp_path / "tasks.sqlite3",
        results_dir=tmp_path / "results",
        log_dir=tmp_path / "logs",
        log_level="WARNING",
    )
    with TestClient(create_app(settings)) as anonymous:
        assert anonymous.get("/healthz").status_code == 200
        assert anonymous.get("/readyz").status_code == 200
        assert anonymous.get("/version").status_code == 200
        rejected = anonymous.get("/api/v1/capabilities")
        assert rejected.status_code == 401
        assert rejected.headers["www-authenticate"] == "Bearer"


def test_remote_operation_upload_submit_reconcile_and_result(client):
    artifact = upload_json(client, sustainability_payload())
    request = {
        "operationType": "sustainability.evaluate",
        "clientRequestId": "workflow-run-1:node-1",
        "inputs": {
            "assessment_input": {
                "artifactId": artifact["artifactId"],
                "mediaType": artifact["mediaType"],
                "format": artifact["format"],
                "sizeBytes": artifact["sizeBytes"],
                "sha256": artifact["sha256"],
            }
        },
        "parameters": {},
    }
    accepted = client.post("/api/v1/operations", json=request)
    assert accepted.status_code == 202
    operation_id = accepted.json()["operationId"]
    reconciled = client.get(
        "/api/v1/operations/by-client-request/workflow-run-1:node-1"
    )
    assert reconciled.status_code == 200
    assert reconciled.json()["operationId"] == operation_id
    assert reconciled.json()["status"] == "succeeded"

    replay = client.post("/api/v1/operations", json=request)
    assert replay.status_code == 202
    assert replay.json()["operationId"] == operation_id
    assert replay.json()["idempotentReplay"] is True

    results = client.get(f"/api/v1/operations/{operation_id}/results")
    assert results.status_code == 200
    manifest = results.json()
    assert manifest["values"]["sustainability_result"]["final_grade"] == "I"
    result_artifact = manifest["artifacts"][0]
    downloaded = client.get(result_artifact["downloadUrl"])
    assert len(downloaded.content) == result_artifact["sizeBytes"]
    assert digest(downloaded.content) == result_artifact["sha256"]
    receipt = client.get(f"/api/v1/operations/{operation_id}/receipt").json()
    assert receipt["remoteTaskId"] == operation_id
    assert receipt["status"] == "succeeded"
    assert "api_key" not in json.dumps(receipt)


def test_pending_cancel_is_truthful(client):
    from app.sustainability import SustainabilityTaskInput

    service = client.app.state.sustainability_task_service
    task, _ = service.create_task(
        SustainabilityTaskInput.model_validate(
            sustainability_payload(client_request_id="cancel-before-run")
        )
    )
    cancelled = client.post(
        f"/api/v1/sustainability/tasks/{task['task_id']}/cancel"
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["cancellation_accepted"] is True
    assert cancelled.json()["remote_termination_confirmed"] is True
    assert service.run_task(task["task_id"]) is None


def test_running_cancel_does_not_claim_remote_termination(client):
    from app.sustainability import SustainabilityTaskInput

    repository = client.app.state.sustainability_task_repository
    service = client.app.state.sustainability_task_service
    task, _ = service.create_task(
        SustainabilityTaskInput.model_validate(
            sustainability_payload(client_request_id="already-running")
        )
    )
    assert repository.claim_pending(task["task_id"]) is True
    response = client.post(
        f"/api/v1/sustainability/tasks/{task['task_id']}/cancel"
    )
    assert response.status_code == 200
    assert response.json()["cancellation_accepted"] is False
    assert response.json()["remote_termination_confirmed"] is False
