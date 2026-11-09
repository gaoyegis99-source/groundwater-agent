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
        assert b"heartbeat" in b"".join(client.events("session/1"))


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
