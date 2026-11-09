"""The public Kernel boundary enforces local project identity."""

from dataclasses import replace
import sqlite3
from uuid import uuid4

from agents.testing import ScriptedModel, ModelStep
from fastapi.testclient import TestClient
import httpx
import pytest

from kernel.tests.reply_testing import submit_reply
from platform_kernel.api import create_app
from platform_kernel.composition import PlatformApplication
from platform_kernel.database import connect_database, initialize_database
from platform_kernel.interaction.persistence import create_session


def _application(kernel_settings) -> PlatformApplication:
    model = ScriptedModel([ModelStep(output=[submit_reply("done")])])
    return PlatformApplication(
        settings=kernel_settings("knowledge"),
        model=model,
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(503))
        ),
    )


def test_public_routes_use_fixed_project_without_identity_headers(
    kernel_settings,
) -> None:
    app = _application(kernel_settings)
    with TestClient(create_app(application=app)) as client:
        capabilities = client.get("/api/v12/capabilities")
        assert capabilities.status_code == 200
        assert capabilities.json()["projectId"] == "p"
        assert capabilities.json()["currentUser"] == {
            "subject": "local-research-admin",
            "displayName": "Local Research Admin",
            "roles": ["admin"],
        }
        assert client.post("/internal/authz/v1/check", json={}).status_code == 404
        mismatched = client.post(
            "/api/v12/sessions", json={"projectId": "other"}
        )
        assert mismatched.status_code == 404
        assert mismatched.json()["code"] == "PROJECT_NOT_FOUND"
        created = client.post("/api/v12/sessions", json={"projectId": "p"})
        assert created.status_code == 200
        session_id = created.json()["sessionId"]
        assert client.get(f"/api/v12/sessions/{session_id}").status_code == 200
        for path in (
            "/api/v12/capabilities",
            f"/api/v12/sessions/{session_id}",
            f"/api/v12/sessions/{session_id}/results",
            f"/api/v12/sessions/{session_id}/events",
        ):
            response = client.get(path, params={"projectId": "other"})
            assert response.status_code == 404
            assert response.json()["code"] == "PROJECT_NOT_FOUND"
        response = client.get(
            f"/api/v12/sessions/{session_id}", headers={"X-Project-ID": "other"}
        )
        assert response.status_code == 404
        assert response.json()["code"] == "PROJECT_NOT_FOUND"
        response = client.get(
            "/api/v12/agent-runs/missing", params={"projectId": "other"}
        )
        assert response.status_code == 404
        assert response.json()["code"] == "PROJECT_NOT_FOUND"
        response = client.get(
            "/api/v12/workflow-runs/missing", params={"projectId": "other"}
        )
        assert response.status_code == 404
        assert response.json()["code"] == "PROJECT_NOT_FOUND"


def test_fresh_schema_has_research_identity_state(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    with connect_database(database) as connection:
        version = connection.execute("SELECT version FROM schema_version").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        session_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(sessions)")
        }
        confirmation_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(confirmations)")
        }
        confirmation_action_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(confirmation_actions)")
        }
        workflow_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(workflow_runs)")
        }
    assert version == 34
    assert "provider_requests" in tables
    assert "input_references_json" in confirmation_action_columns
    assert "input_references_json" in workflow_columns
    assert "owner_subject" in session_columns
    assert {"title", "archived_at"} <= session_columns
    assert "decision_subject" in confirmation_columns
    assert "initiator_principal_id" not in workflow_columns


def test_project_agent_queue_returns_retry_after_when_full(kernel_settings) -> None:
    settings = replace(
        kernel_settings("knowledge"), agent_concurrency=1, agent_queue_capacity=1
    )
    app = PlatformApplication(
        settings=settings,
        model=ScriptedModel([ModelStep(output=[submit_reply("done")])]),
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(503))
        ),
    )
    with TestClient(create_app(application=app)) as client:
        sessions = [
            client.post("/api/v12/sessions", json={"projectId": "p"}).json()[
                "sessionId"
            ]
            for _ in range(3)
        ]
        with connect_database(app.database) as connection:
            connection.executemany(
                "UPDATE sessions SET pending_agent_run_id=? WHERE session_id=?",
                ((f"pending-{index}", session) for index, session in enumerate(sessions[:2])),
            )
        response = client.post(
            f"/api/v12/sessions/{sessions[2]}/messages",
            json={
                "requestId": str(uuid4()),
                "message": "queued work",
                "requestedMode": "DIALOGUE",
            },
        )

    assert response.status_code == 429
    assert response.json()["code"] == "PROJECT_AGENT_QUEUE_FULL"
    assert response.headers["Retry-After"] == "5"


def test_session_owner_is_immutable_in_storage(kernel_settings) -> None:
    settings = kernel_settings()
    database = settings.data_root / "kernel.sqlite3"
    create_session(database, "owned", "p", "alice@example.test")
    with pytest.raises(sqlite3.IntegrityError, match="SESSION_OWNER_IMMUTABLE"):
        with connect_database(database) as connection:
            connection.execute(
                "UPDATE sessions SET owner_subject='bob@example.test' "
                "WHERE session_id='owned'"
            )


@pytest.mark.parametrize("version", [11, 13, 14, 15, 17, 18])
def test_old_schema_requires_isolated_new_store(tmp_path, version) -> None:
    database = tmp_path / "kernel.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE schema_version(version INTEGER NOT NULL);"
            f"INSERT INTO schema_version(version) VALUES ({version});"
        )
    with pytest.raises(ValueError, match="SCHEMA_VERSION_UNSUPPORTED"):
        initialize_database(database)


def test_capabilities_expose_input_gaps_without_claiming_scientific_suitability(
    kernel_settings,
):
    import json

    app = PlatformApplication(
        settings=kernel_settings("summary"),
        model=ScriptedModel([]),
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(503))
        ),
    )
    models = json.loads(app.capabilities_json)["models"]
    assert models
    for model, definition in zip(models, app.settings.models):
        assert model["requiredInputs"] == [
            name
            for name in definition.input_schema["required"]
            if name not in definition.default_parameters
        ]
        assert model["defaultParameters"] == definition.default_parameters
        assert model["finalOutputs"] == list(definition.final_outputs)
        assert model["applicabilityStatus"] == "REQUIRES_CURRENT_KNOWLEDGE"
