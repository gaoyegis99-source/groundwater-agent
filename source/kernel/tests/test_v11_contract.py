from __future__ import annotations


import asyncio
from dataclasses import replace
import json
import time
from uuid import uuid4

from agents.testing import ScriptedModel, ModelStep
from kernel.tests.reply_testing import submit_reply
from fastapi.testclient import TestClient
import httpx
import pytest
from starlette.requests import Request

from platform_kernel.api import create_app
from platform_kernel.api.routes.sessions import events
from platform_kernel.composition import PlatformApplication
from platform_kernel.database import append_event, connect_database
from platform_kernel.readiness import ReadinessReport


def _application(kernel_settings, tmp_path):
    model = ScriptedModel(
        [
            ModelStep(
                output=[
                    submit_reply('done')
                ]
            )
        ]
    )
    return PlatformApplication(
        settings=kernel_settings("knowledge"),
        model=model,
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None)),
    )


def test_v12_is_public_and_v11_is_gone(tmp_path, kernel_settings) -> None:
    application = _application(kernel_settings, tmp_path)
    with TestClient(create_app(application=application)) as client:
        assert client.get("/internal/health").status_code == 200
        assert client.get("/api/v12/health").status_code == 200
        retired = client.get("/api/v11/health")
        assert retired.status_code == 410
        assert retired.json()["code"] == "API_VERSION_RETIRED"
        assert client.get("/api/v6/health").status_code == 404


def test_openapi_metadata_reports_public_api_v12(tmp_path, kernel_settings) -> None:
    application = _application(kernel_settings, tmp_path)
    with TestClient(create_app(application=application)) as client:
        assert client.get("/openapi.json").json()["info"]["version"] == "12"


def test_sse_replays_after_last_event_id_and_heartbeat_is_not_persisted(
    tmp_path, kernel_settings
) -> None:
    application = _application(kernel_settings, tmp_path)
    session = application.interaction.create_session().session
    with connect_database(application.database) as connection:
        first_id = connection.execute(
            "SELECT MAX(event_id) FROM session_events"
        ).fetchone()[0]
        second_id = append_event(
            connection,
            session.session_id,
            "test.changed",
            {"value": 1},
        )

    first = asyncio.run(_first_sse_chunk(application, session.session_id, first_id))
    assert first.startswith(f"id: {second_id}\nevent: test.changed\n")
    assert '"schemaVersion":"session-event.v20"' in first

    heartbeat = asyncio.run(
        _first_sse_chunk(application, session.session_id, second_id)
    )
    assert heartbeat == ": heartbeat\n\n"
    with connect_database(application.database) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM session_events WHERE session_id = ?",
            (session.session_id,),
        ).fetchone()[0]
    assert count == 2


def test_trace_is_projected_without_api_key(tmp_path, kernel_settings) -> None:
    application = _application(kernel_settings, tmp_path)
    with TestClient(create_app(application=application)) as client:
        session = client.post(
            "/api/v12/sessions", json={"projectId": "p"}
        ).json()
        accepted = client.post(
            f"/api/v12/sessions/{session['sessionId']}/messages",
            json={
                "requestId": str(uuid4()),
                "message": "complete",
                "requestedMode": "DIALOGUE",
            },
        )
        assert accepted.status_code == 202
        completed = accepted.json()
        for _ in range(200):
            run = client.get(
                f"/api/v12/agent-runs/{completed['agentRunId']}",
                params={"projectId": "p"},
            )
            if run.status_code == 200 and run.json()["status"] == "COMPLETE":
                break
            time.sleep(0.01)
        assert run.status_code == 200 and run.json()["status"] == "COMPLETE"
        response = client.get(
            f"/api/v12/agent-runs/{completed['agentRunId']}/trace",
            params={"projectId": "p"},
        )
        assert response.status_code == 200
        assert response.json()["traceId"]
        providers = client.get(
            f"/api/v12/agent-runs/{completed['agentRunId']}/provider-requests",
            params={"projectId": "p"},
        )
        assert providers.status_code == 200
        attempts = providers.json()["items"]
        assert attempts
        assert all(
            set(item) == {
                "providerRequestId", "stage", "provider", "model", "protocol",
                "attempt", "outcome", "errorClass", "inputTokens",
                "outputTokens", "totalTokens", "latencyMs", "createdAt",
            }
            for item in attempts
        )
        assert all(item["outcome"] == "SUCCEEDED" for item in attempts)
    with connect_database(application.database) as connection:
        payloads = [
            row[0]
            for row in connection.execute("SELECT payload_json FROM agent_traces")
        ]
        payloads.extend(
            row[0] for row in connection.execute("SELECT payload_json FROM agent_spans")
        )
    assert payloads
    assert "test-key" not in json.dumps(payloads)


def test_only_one_platform_lifespan_is_active_in_a_process(
    tmp_path, kernel_settings
) -> None:
    first = _application(kernel_settings, tmp_path)
    second_settings = replace(
        kernel_settings("knowledge"), data_root=tmp_path / "second"
    )
    second = PlatformApplication(
        settings=second_settings,
        model=ScriptedModel([]),
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(500))
        ),
    )
    first_client = TestClient(create_app(application=first))
    second_client = TestClient(create_app(application=second))
    with first_client:
        with pytest.raises(
            RuntimeError, match="MULTIPLE_PLATFORM_APPLICATIONS_UNSUPPORTED"
        ):
            with second_client:
                pass
    with second_client:
        assert second_client.get("/api/v12/health").status_code == 200


def test_health_distinguishes_liveness_and_dispatcher_readiness(
    tmp_path, kernel_settings, monkeypatch
) -> None:
    application = _application(kernel_settings, tmp_path)
    app = create_app(application=application)
    with TestClient(app) as client:
        assert client.get("/internal/health/live").status_code == 200
        assert client.get("/internal/health/ready").status_code == 200
        monkeypatch.setattr(application, "is_ready", lambda: False)
        unavailable = client.get("/internal/health/ready")
    assert unavailable.status_code == 503
    assert unavailable.headers["content-type"].startswith("application/problem+json")
    assert unavailable.json()["code"] == "KERNEL_NOT_READY"


def test_public_health_is_liveness_and_public_ready_is_aggregate(
    tmp_path, kernel_settings, monkeypatch
) -> None:
    application = _application(kernel_settings, tmp_path)

    async def report():
        return ReadinessReport(
            False,
            {
                "status": "DOWN",
                "components": {
                    "kernel": {"status": "UP"},
                    "data": {"status": "DOWN"},
                },
            },
        )

    monkeypatch.setattr(application, "readiness_report", report)
    with TestClient(create_app(application=application)) as client:
        assert client.get("/api/v12/health").status_code == 200
        ready = client.get("/api/v12/ready")
    assert ready.status_code == 503
    assert ready.json()["components"]["data"]["status"] == "DOWN"


async def _first_sse_chunk(
    application: PlatformApplication, session_id: str, last_event_id: int | None,
    after_event_id: int = 0,
) -> str:
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(
        {"type": "http", "method": "GET", "path": "/", "headers": []},
        receive,
    )
    response = await events(
        session_id,
        request,
        last_event_id=last_event_id,
        after_event_id=after_event_id,
        application=application,
    )
    chunk = await anext(response.body_iterator)
    await response.body_iterator.aclose()
    return chunk.decode() if isinstance(chunk, bytes) else chunk


def test_sse_initial_snapshot_cursor_replays_gap_and_header_takes_precedence(tmp_path, kernel_settings):
    application = _application(kernel_settings, tmp_path)
    session_id = application.interaction.create_session().session.session_id
    snapshot = application.interaction.messages(session_id, None, 50)
    cursor = snapshot["event_cursor"]
    with connect_database(application.database) as connection:
        next_id = append_event(connection, session_id, "test.after_snapshot", {})
    chunk = asyncio.run(_first_sse_chunk(application, session_id, None, cursor))
    assert chunk.startswith(f"id: {next_id}\nevent: test.after_snapshot\n")
    assert asyncio.run(_first_sse_chunk(application, session_id, next_id, cursor)) == ": heartbeat\n\n"
    # A reconnect's explicit zero is still a header cursor, not a falsey default.
    chunk = asyncio.run(_first_sse_chunk(application, session_id, 0, next_id))
    assert "event: session.created" in chunk


@pytest.mark.parametrize("kwargs", [
    {"params": {"afterEventId": -1}}, {"params": {"afterEventId": "bad"}},
    {"headers": {"Last-Event-ID": "-1"}}, {"headers": {"Last-Event-ID": "bad"}},
])
def test_sse_rejects_invalid_cursors(tmp_path, kernel_settings, kwargs):
    application = _application(kernel_settings, tmp_path)
    session_id = application.interaction.create_session().session.session_id
    client = TestClient(create_app(application=application))
    response = client.get(f"/api/v12/sessions/{session_id}/events", **kwargs)
    assert response.status_code == 422
