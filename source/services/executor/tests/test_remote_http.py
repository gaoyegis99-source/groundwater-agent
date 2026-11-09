from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from platform_executor_service.remote_http import (
    RemoteOperationError,
    RemoteOperationRunner,
)


SERVICE_VERSION = {
    "apiVersion": "remote-operation.v1",
    "serviceVersion": "0.7.0",
    "modelVersion": "sustainability-evaluation.v1.1",
}


def _runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    handler,
    *,
    operation_type: str = "sustainability.evaluate",
) -> tuple[RemoteOperationRunner, list[dict]]:
    source = tmp_path / "assessment.json"
    source.write_text('{"source":"case-1"}', encoding="utf-8")
    workspace = tmp_path / "workspace"
    (workspace / "outputs").mkdir(parents=True)
    secret = tmp_path / "secret"
    secret.write_text("k" * 64, encoding="utf-8")
    monkeypatch.setenv("TEST_REMOTE_BASE_URL", "https://remote.example")
    monkeypatch.setenv("TEST_REMOTE_ALLOWED_HOST", "remote.example")
    monkeypatch.setattr(
        RemoteOperationRunner,
        "_secret_path",
        staticmethod(lambda _name: secret),
    )
    states: list[dict] = []

    async def persist(state: dict) -> None:
        states.append(dict(state))

    config = {
        "remoteServices": {
            "groundwater-sustainability": {
                "serviceId": "groundwater-sustainability",
                "baseUrlEnvironment": "TEST_REMOTE_BASE_URL",
                "allowedHostEnvironment": "TEST_REMOTE_ALLOWED_HOST",
                "credentialSecret": "remote-api-key",
                "caBundleSecret": "remote-ca-bundle",
                "connectTimeoutSeconds": 5,
                "pollIntervalSeconds": 1,
                "apiVersion": "remote-operation.v1",
                "allowedServiceVersions": ["0.7.0"],
                "allowedModelVersions": ["sustainability-evaluation.v1.1"],
                "allowedOperations": ["sustainability.evaluate"],
            }
        }
    }
    lease = {
        "jobId": "job-1",
        "nodeId": "evaluate",
        "timeoutSeconds": 30,
        "remoteBinding": {
            "serviceId": "groundwater-sustainability",
            "operationType": operation_type,
        },
        "remoteState": {},
        "parameters": {},
        "inputs": {
            "assessment_input": [
                {
                    "path": str(source),
                    "mediaType": "application/json",
                    "format": "json",
                }
            ]
        },
        "workspace": str(workspace),
        "outputContracts": [
            {"name": "sustainability_result", "kind": "value"},
            {
                "name": "result_file",
                "kind": "artifact",
                "formatPairs": [
                    {"mediaType": "application/json", "format": "json"}
                ],
            },
            {
                "name": "run_receipt",
                "kind": "artifact",
                "formatPairs": [
                    {"mediaType": "application/json", "format": "json"}
                ],
            },
        ],
    }
    runner = RemoteOperationRunner(config, lease, persist)
    monkeypatch.setattr(
        runner,
        "_client",
        lambda: httpx.AsyncClient(
            base_url="https://remote.example",
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer " + "k" * 64},
        ),
    )
    return runner, states


def test_operation_must_be_enabled_by_deployment_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(RemoteOperationError, match="REMOTE_BINDING_INVALID"):
        _runner(
            tmp_path,
            monkeypatch,
            lambda _request: httpx.Response(500),
            operation_type="model.compile",
        )


def _successful_handler(result_bytes: bytes, *, advertised_digest: str | None = None):
    digest = advertised_digest or "sha256:" + hashlib.sha256(result_bytes).hexdigest()
    requests: list[httpx.Request] = []
    uploaded = bytearray()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if request.method == "GET" and path == "/version":
            assert request.headers.get("Authorization") == ""
            return httpx.Response(200, json=SERVICE_VERSION)
        if request.method == "GET" and path == "/api/v1/capabilities":
            return httpx.Response(
                200,
                json={
                    "operations": ["sustainability.evaluate"],
                    "uploads": {"maxArtifactBytes": 4096, "uploadPartBytes": 4},
                },
            )
        if request.method == "GET" and path.startswith(
            "/api/v1/operations/by-client-request/"
        ):
            return httpx.Response(404, json={"code": "NOT_FOUND"})
        if request.method == "POST" and path == "/api/v1/uploads":
            return httpx.Response(201, json={"uploadId": "upload-1", "partSizeBytes": 4})
        if request.method == "PUT" and path.startswith("/api/v1/uploads/upload-1/parts/"):
            uploaded.extend(request.content)
            expected = "sha256:" + hashlib.sha256(request.content).hexdigest()
            assert request.headers["X-Content-SHA256"] == expected
            return httpx.Response(200, json={"accepted": True})
        if request.method == "POST" and path == "/api/v1/uploads/upload-1/complete":
            actual = bytes(uploaded)
            return httpx.Response(
                200,
                json={
                    "artifactId": "artifact-1",
                    "mediaType": "application/json",
                    "format": "json",
                    "sizeBytes": len(actual),
                    "sha256": "sha256:" + hashlib.sha256(actual).hexdigest(),
                },
            )
        if request.method == "POST" and path == "/api/v1/operations":
            payload = json.loads(request.content)
            assert payload["operationType"] == "sustainability.evaluate"
            assert len(payload["clientRequestId"]) == 64
            assert payload["inputs"]["assessment_input"]["artifactId"] == "artifact-1"
            return httpx.Response(202, json={"operationId": "operation-1", "status": "succeeded"})
        if request.method == "GET" and path == "/api/v1/operations/operation-1/results":
            return httpx.Response(
                200,
                json={
                    "operationId": "operation-1",
                    "serviceVersion": SERVICE_VERSION["serviceVersion"],
                    "modelVersion": SERVICE_VERSION["modelVersion"],
                    "artifacts": [
                        {
                            "port": "result_file",
                            "mediaType": "application/json",
                            "format": "json",
                            "sizeBytes": len(result_bytes),
                            "sha256": digest,
                            "downloadUrl": "/api/v1/files/result.json",
                        }
                    ],
                    "values": {"sustainability_result": {"status": "ok"}},
                },
            )
        if request.method == "GET" and path == "/api/v1/files/result.json":
            return httpx.Response(200, content=result_bytes)
        if request.method == "GET" and path == "/api/v1/operations/operation-1/receipt":
            return httpx.Response(
                200,
                json={"remoteTaskId": "operation-1", "status": "succeeded"},
            )
        raise AssertionError(f"unexpected request: {request.method} {path}")

    return handler, requests


def test_remote_operation_uploads_once_verifies_results_and_writes_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result_bytes = b'{"result":"accepted"}'
    handler, requests = _successful_handler(result_bytes)
    runner, states = _runner(tmp_path, monkeypatch, handler)

    result = asyncio.run(runner.run())

    assert result.values == {"sustainability_result": {"status": "ok"}}
    assert {item["port"] for item in result.outputs} == {
        "result_file",
        "run_receipt",
    }
    assert sum(
        request.method == "POST" and request.url.path == "/api/v1/operations"
        for request in requests
    ) == 1
    receipt = json.loads((tmp_path / "workspace/outputs/run_receipt.json").read_text())
    assert receipt["remoteTaskId"] == "operation-1"
    assert receipt["remoteTerminationConfirmed"] is True
    assert "apiKey" not in receipt and "credential" not in receipt
    assert states[-1]["remoteStatus"] == "succeeded"


def test_ambiguous_submit_reconciles_without_blind_resubmission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"submit": 0, "reconcile": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            calls["submit"] += 1
            return httpx.Response(503, json={"code": "UNAVAILABLE"})
        calls["reconcile"] += 1
        return httpx.Response(404, json={"code": "NOT_FOUND"})

    runner, _ = _runner(tmp_path, monkeypatch, handler)

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    client = runner._client()
    try:
        with pytest.raises(RemoteOperationError) as raised:
            asyncio.run(
                runner._submit(
                    client,
                    {"operationType": "sustainability.evaluate"},
                    "stable-key",
                )
            )
    finally:
        asyncio.run(client.aclose())
    assert raised.value.code == "REMOTE_SUBMISSION_UNCERTAIN"
    assert calls["submit"] == 1
    assert calls["reconcile"] == 4


def test_download_checksum_mismatch_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler, _ = _successful_handler(
        b'{"result":"changed"}',
        advertised_digest="sha256:" + "0" * 64,
    )
    runner, _ = _runner(tmp_path, monkeypatch, handler)

    with pytest.raises(RemoteOperationError) as raised:
        asyncio.run(runner.run())

    assert raised.value.code == "REMOTE_INTEGRITY_FAILED"
    assert not (tmp_path / "workspace/outputs/result_file").exists()


def test_cancel_records_unconfirmed_remote_termination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/operations/operation-1/cancel"
        return httpx.Response(
            200,
            json={
                "status": "running",
                "cancellationAccepted": False,
                "remoteTerminationConfirmed": False,
            },
        )

    runner, states = _runner(tmp_path, monkeypatch, handler)
    runner.state = {
        "remoteTaskId": "operation-1",
        "requestSha256": "sha256:" + "1" * 64,
        "idempotencyKey": "stable-key",
        "remoteStatus": "running",
        "lastPolledAt": None,
        "remoteTerminationConfirmed": None,
    }
    client = runner._client()
    try:
        asyncio.run(runner._cancel_safely(client))
    finally:
        asyncio.run(client.aclose())

    assert runner.state["remoteTerminationConfirmed"] is False
    assert states[-1]["remoteTerminationConfirmed"] is False
