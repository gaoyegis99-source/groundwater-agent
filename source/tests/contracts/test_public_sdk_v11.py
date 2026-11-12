from __future__ import annotations

import json

import httpx
import pytest

from groundwater_api_client import GroundwaterClient, models
from platform_protocol_contracts import ApiProblem


def _json(status: int, value: object) -> httpx.Response:
    return httpx.Response(
        status,
        content=json.dumps(value).encode(),
        headers={"Content-Type": "application/json"},
    )


def _handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["X-Client-Type"] == "sdk"
    path = request.url.raw_path.decode().split("?", 1)[0]
    if path == "/api/v12/sessions":
        assert request.url.params.get("cursor") != ""
        if request.url.params.get("cursor") == "999":
            return httpx.Response(
                422,
                json={
                    "type": "urn:groundwater:problem:validation-error",
                    "title": "Validation failed",
                    "status": 422,
                    "detail": "cursor is invalid",
                    "code": "VALIDATION_ERROR",
                    "requestId": "request-1",
                    "traceId": "trace-1",
                    "errors": [{"pointer": "/cursor", "message": "invalid"}],
                },
                headers={"Content-Type": "application/problem+json"},
            )
        return _json(200, {"items": [], "nextCursor": None})
    if path == "/api/v12/data/queries/query-1" and request.method == "GET":
        return _json(200, {
            "queryId": "query-1", "state": "QUEUED", "error": None,
            "receipt": None, "expiresAt": None, "resultAvailable": False,
            "result": None,
        })
    if path == "/api/v12/data/queries/query-1/cancel":
        return _json(200, {"queryId": "query-1", "state": "CANCELLED"})
    if path == "/api/v12/data/versions/version%2F1/objects/object%2F1/content":
        return httpx.Response(
            200,
            content=b"content",
            headers={"Content-Type": "application/octet-stream", "ETag": '"sha256:x"'},
        )
    if path == "/api/v12/knowledge/retrievals":
        assert json.loads(request.content)["query"] == "water"
        return _json(200, {
            "schemaVersion": "evidence-bundle.v1", "receiptId": "receipt-1",
            "status": "NO_MATCH", "items": [], "coverage": {},
            "budget": {"maxUnits": 8, "maxChars": 16000},
            "timingsMs": {}, "knowledgeRevision": 1,
            "indexGeneration": "generation-1", "observedAt": "2026-10-15T00:00:00Z",
        })
    if path == "/api/v12/sessions/session%2F1/events":
        assert dict(request.url.params) == {"afterEventId": "17"}
        return httpx.Response(
            200,
            content=b"event: heartbeat\ndata: {}\n\n",
            headers={"Content-Type": "text/event-stream"},
        )
    raise AssertionError(f"unexpected request: {request.method} {request.url}")


def test_public_sdk_covers_session_data_knowledge_stream_and_cancel() -> None:
    transport = httpx.MockTransport(_handler)
    with httpx.Client(transport=transport) as http, GroundwaterClient(
        "http://gateway.test", client=http
    ) as client:
        assert client.list_sessions().items == []
        assert client.query("query-1").query_id == "query-1"
        assert client.cancel_query("query-1").state == "CANCELLED"
        assert b"".join(client.download_object("version/1", "object/1")) == b"content"
        evidence = client.retrieve(models.KnowledgeNextRetrievalRequest(query="water", mode=models.Mode.hybrid))
        assert evidence.status.value == "NO_MATCH"
        assert b"heartbeat" in b"".join(client.events("session/1", cursor=17))


def test_public_sdk_exposes_problem_details_and_requires_gateway_origin() -> None:
    transport = httpx.MockTransport(_handler)
    with httpx.Client(transport=transport) as http, GroundwaterClient(
        "http://gateway.test", client=http
    ) as client:
        with pytest.raises(ApiProblem) as caught:
            client.list_sessions(cursor=999)
        assert caught.value.problem.code == "VALIDATION_ERROR"
        assert caught.value.problem.errors[0].pointer == "/cursor"
    with pytest.raises(ValueError, match="SERVICE_ORIGIN_INVALID"):
        GroundwaterClient("http://gateway.test/api/v12")


def test_public_sdk_password_login_and_explicit_confirmation() -> None:
    identity = {"subject": "researcher", "displayName": "Researcher", "roles": ["researcher"]}
    session = {
        "sessionId": "session-1", "projectId": "project", "goalRevision": 1,
        "agentRunId": "agent-1", "workflowDraftId": None, "agentOutput": None,
        "workflowRunIds": [], "remoteOutputs": [], "agentSteps": ["WAITING_CONFIRMATION"],
        "controlProposals": [], "task": None,
        "confirmation": {"confirmationId": "confirmation-1", "status": "PENDING"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Client-Type"] == "cli"
        if request.url.path.endswith("/auth/login"):
            assert json.loads(request.content) == {"username": "researcher", "password": "test-only"}
            return httpx.Response(
                200, json={"currentUser": identity, "csrfToken": "test-csrf"},
                headers={"Set-Cookie": "session=example; Path=/"},
            )
        assert request.headers["Cookie"] == "session=example"
        if request.url.path.endswith("/confirm"):
            assert request.headers["X-CSRF-Token"] == "test-csrf"
            assert json.loads(request.content) == {"confirmationId": "confirmation-1"}
            return _json(202, {**session, "confirmation": {"status": "CONFIRMED"}})
        assert request.url.path == "/api/v12/sessions/session-1"
        assert "X-CSRF-Token" not in request.headers
        return _json(200, {**session, "preparation": None})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http, GroundwaterClient(
        "http://gateway.test", client_type="cli", client=http
    ) as client:
        assert client.login("researcher", "test-only").subject == "researcher"
        assert client.session("session-1").confirmation == session["confirmation"]
        assert client.confirm("session-1", "confirmation-1").confirmation == {"status": "CONFIRMED"}


def test_public_sdk_token_catalog_and_exact_object_reference() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        assert "X-CSRF-Token" not in request.headers
        if request.url.path.endswith("/datasets"):
            assert dict(request.url.params) == {"query": "water", "cursor": "page-2", "limit": "20"}
            return _json(200, {"items": [], "matchBasis": "catalog", "nextCursor": None})
        assert request.url.path == "/api/v12/data/versions/version-1"
        return _json(200, {
            "createdAt": "2026-10-01T00:00:00Z", "currentVersion": "version-2",
            "datasetId": "dataset-1", "descriptor": {}, "inputVersions": [],
            "objects": [
                {"objectId": "query", "role": "query", "size": 4, "checksum": "sha256:" + "1" * 64,
                 "format": "parquet", "mediaType": "application/vnd.apache.parquet"},
                {"objectId": "raw", "role": "raw", "size": 5, "checksum": "sha256:" + "2" * 64,
                 "format": "csv", "mediaType": "text/csv"},
            ],
            "project": "project", "publicationId": "publication-1", "semanticId": "semantic-1",
            "semantics": {}, "tags": [], "title": "Water", "versionId": "version-1",
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as http, GroundwaterClient(
        "http://gateway.test", api_token="test-token", client=http
    ) as client:
        assert client.list_datasets(query="water", cursor="page-2").items == []
        reference = client.input_reference("version-1")
        assert (reference.version_id, reference.semantic_id, reference.object_id) == ("version-1", "semantic-1", "raw")
        assert reference.size_bytes == 5
        assert client.input_reference("version-1", "query").object_id == "query"
        with pytest.raises(ValueError, match="DATA_OBJECT_NOT_FOUND"):
            client.input_reference("version-1", "missing")


def test_public_sdk_reads_workflow_run_in_an_explicit_project() -> None:
    project_id = "groundwater-demo"
    output = {"outputName": "summary", "versionId": "version-2"}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.raw_path.split(b"?", 1)[0] == b"/api/v12/workflow-runs/run%2F1"
        assert dict(request.url.params) == {"projectId": project_id}
        return _json(200, {
            "runId": "run/1",
            "agentRunId": "agent-1",
            "toolCallId": "tool-call-1",
            "position": 0,
            "projectId": project_id,
            "modelId": "groundwater.summary",
            "modelVersion": "3.1.0",
            "modelChecksum": "sha256:" + "1" * 64,
            "workflowId": "groundwater.summary",
            "workflowVersion": "1.1.0",
            "workflowChecksum": "sha256:" + "2" * 64,
            "executionService": "executor",
            "executorId": "workflow-dag",
            "arguments": {"versionId": "version-1", "axis": "columns"},
            "status": "SUCCEEDED",
            "remoteJobId": "job-1",
            "outputs": [output],
            "error": None,
            "createdAt": "2026-11-12T00:00:00Z",
            "startedAt": "2026-11-12T00:00:01Z",
            "finishedAt": "2026-11-12T00:00:02Z",
            "durationMs": 1000,
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as http, GroundwaterClient(
        "http://gateway.test", client=http
    ) as client:
        result = client.workflow_run("run/1", project_id=project_id)
        assert isinstance(result, models.KernelWorkflowRunResponse)
        assert result.run_id == "run/1"
        assert result.project_id == project_id
        assert result.status == "SUCCEEDED"
        assert result.outputs == [output]


def test_public_sdk_event_stream_reports_public_problem() -> None:
    problem = {
        "type": "urn:groundwater:problem:session-not-found",
        "title": "Session not found",
        "status": 404,
        "detail": "The session could not be read.",
        "code": "SESSION_NOT_FOUND",
        "requestId": "request-1",
        "traceId": "trace-1",
        "errors": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(
            404,
            stream=httpx.ByteStream(json.dumps(problem).encode()),
            headers={"Content-Type": "application/problem+json"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http, GroundwaterClient(
        "http://gateway.test", api_token="test-token", client=http
    ) as client:
        with pytest.raises(ApiProblem) as caught:
            next(client.events("missing"))
        assert caught.value.problem.code == "SESSION_NOT_FOUND"
        assert caught.value.problem.request_id == "request-1"
