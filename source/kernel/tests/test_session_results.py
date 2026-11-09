from __future__ import annotations

import json

from fastapi.testclient import TestClient
from platform_kernel.api import create_app
from platform_kernel.composition import PlatformApplication
from platform_kernel.database import connect_database

from agents.testing import ScriptedModel


def _client(kernel_settings):
    application = PlatformApplication(
        settings=kernel_settings(), model=ScriptedModel([])
    )
    return application, TestClient(create_app(application=application))


def _wire_reference(value):
    return {
        "kind": value["kind"],
        "service": value["service"],
        "datasetId": value["dataset_id"],
        "versionId": value["version_id"],
        "semanticId": value["semantic_id"],
        "objectId": value["object_id"],
        "mediaType": value["media_type"],
        "format": value["format"],
        "sizeBytes": value["size_bytes"],
        "checksum": value["checksum"],
    }


def _insert_run(
    connection, session_id, revision, *, status="COMPLETE", project_id="p"
):
    run_id = f"run-{session_id}-{revision}"
    connection.execute(
        """INSERT INTO agent_runs(
               agent_run_id, session_id, project_id, goal_revision,
               provider, model, status, steps_json, output_json,
               input_tokens, output_tokens, total_tokens, trace_id, error,
               created_at, started_at, finished_at, duration_ms
           ) VALUES (?, ?, ?, ?, 'fixed', 'model', ?, '[]', NULL,
                     0, 0, 0, ?, NULL, 'now', 'now', 'now', 0)""",
        (run_id, session_id, project_id, revision, status, f"trace-{run_id}"),
    )
    return run_id


def _insert_action(
    connection, run_id, position, *, status, outputs=(), workflow_run_id=None
):
    connection.execute(
        """INSERT INTO agent_actions(
               tool_call_id, agent_run_id, position, tool,
               model_id, model_version, model_checksum,
               workflow_id, workflow_version, workflow_checksum,
               plan_revision, arguments_json, input_references_json,
               schema_checksum, fingerprint,
               status, error
           ) VALUES (?, ?, ?, 'run_summary', 'groundwater.summary', '1.1.0',
                     'sha256:0000000000000000000000000000000000000000000000000000000000000000', 'groundwater.summary', '1.0.0',
                     'sha256:1111111111111111111111111111111111111111111111111111111111111111', 1, ?, '{}', 'sha256:schema', 'fingerprint',
                     ?, NULL)""",
        (
            f"call-{run_id}-{position}",
            run_id,
            position,
            json.dumps({"private": "SECRET_ARGUMENT"}),
            "APPROVED" if workflow_run_id else status,
        ),
    )
    if workflow_run_id is not None:
        owner = connection.execute(
            "SELECT session_id, project_id FROM agent_runs WHERE agent_run_id = ?",
            (run_id,),
        ).fetchone()
        workflow_status = "SUCCEEDED" if status == "COMPLETE" else status
        connection.execute(
            """INSERT INTO workflow_runs(
                   run_id, agent_run_id, session_id, tool_call_id, position,
                   project_id, model_id, model_version, model_checksum,
                   workflow_id, workflow_version, workflow_checksum,
                   execution_service, executor_id, arguments_json, status,
                   input_references_json,
                   remote_job_id, outputs_json, error, created_at,
                   started_at, finished_at, duration_ms
               ) VALUES (?, ?, ?, ?, ?, ?, 'groundwater.summary', '1.1.0',
                         'sha256:0000000000000000000000000000000000000000000000000000000000000000', 'groundwater.summary', '1.0.0',
                         'sha256:1111111111111111111111111111111111111111111111111111111111111111', 'executor', 'workflow-dag', '{}', ?, '{}',
                         NULL, ?, NULL, 'now', 'now', 'now', 0)""",
            (workflow_run_id, run_id, owner["session_id"],
             f"call-{run_id}-{position}", position, owner["project_id"],
             workflow_status, json.dumps(list(outputs))),
        )


def test_session_results_pages_revisions_including_empty_pages(
    kernel_settings,
) -> None:
    application, client = _client(kernel_settings)
    session = client.post(
        "/api/v12/sessions", json={"projectId": "p"}
    ).json()
    session_id = session["sessionId"]
    reference = {
        "kind": "data_version",
        "service": "data-primary",
        "dataset_id": "dataset-results",
        "version_id": "result-41",
        "semantic_id": "semantics-results",
        "object_id": "object-result-41",
        "media_type": "application/zip",
        "format": "zip",
        "size_bytes": 12,
            "checksum": "sha256:" + "a" * 64,
    }
    with connect_database(application.database) as connection:
        for revision in range(1, 42):
            run_id = _insert_run(
                connection,
                session_id,
                revision,
                status="FAILED" if revision == 41 else "COMPLETE",
            )
        _insert_action(
            connection,
            run_id,
            2,
            status="REJECTED",
        )
        _insert_action(
            connection,
            run_id,
            1,
            status="COMPLETE",
            outputs=(reference,),
            workflow_run_id="workflow-41",
        )
        connection.execute(
            "UPDATE sessions SET goal_revision = 41, agent_run_id = ? "
            "WHERE session_id = ?",
            (run_id, session_id),
        )
        before = connection.execute(
            "SELECT COUNT(*) FROM session_events"
        ).fetchone()[0]

    url = f"/api/v12/sessions/{session_id}/results"
    first = client.get(url)
    assert first.status_code == 200
    assert first.json() == {
        "sessionId": session_id,
        "projectId": "p",
        "items": [],
        "nextCursor": 20,
    }
    second = client.get(url, params={"cursor": 20})
    assert second.status_code == 200
    assert second.json()["items"] == []
    assert second.json()["nextCursor"] == 40
    third = client.get(url, params={"cursor": 40})
    assert third.status_code == 200
    assert third.json() == {
        "sessionId": session_id,
        "projectId": "p",
        "items": [
            {
                "goalRevision": 41,
                "agentRunId": run_id,
                "agentStatus": "FAILED",
                "position": 1,
                "toolCallId": f"call-{run_id}-1",
                "status": "COMPLETE",
                "modelId": "groundwater.summary",
                    "workflowRunId": "workflow-41",
                    "attempt": 1,
                "remoteOutputs": [_wire_reference(reference)],
            },
            {
                "goalRevision": 41,
                "agentRunId": run_id,
                "agentStatus": "FAILED",
                "position": 2,
                "toolCallId": f"call-{run_id}-2",
                "status": "REJECTED",
                "modelId": "groundwater.summary",
                    "workflowRunId": None,
                    "attempt": None,
                "remoteOutputs": [],
            },
        ],
        "nextCursor": None,
    }
    assert "SECRET_ARGUMENT" not in third.text
    assert "run_summary" not in third.text
    with connect_database(application.database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM session_events"
        ).fetchone()[0] == before
    application.interaction.message(
        session_id, "status-with-data-result", "查看状态", "AUTO",
    )
    status_message = application.interaction.messages(
        session_id, None, 50,
    )["control_messages"][-1]
    assert status_message["results"] == [reference]
    _restarted, restarted_client = _client(kernel_settings)
    assert restarted_client.get(url, params={"cursor": 40}).json() == (
        third.json()
    )


def test_session_results_missing_session_and_invalid_cursor(kernel_settings) -> None:
    _application, client = _client(kernel_settings)
    assert client.get("/api/v12/sessions/missing/results").json()["code"] == (
        "SESSION_NOT_FOUND"
    )
    session = client.post(
        "/api/v12/sessions", json={"projectId": "p"}
    ).json()
    url = f"/api/v12/sessions/{session['sessionId']}/results"
    assert client.get(url).json()["nextCursor"] is None
    for cursor in ("-1", "wrong"):
        response = client.get(url, params={"cursor": cursor})
        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_ERROR"


def test_session_results_keep_prior_turn_outputs_after_new_turn(
    kernel_settings,
) -> None:
    application, client = _client(kernel_settings)
    session_id = client.post(
        "/api/v12/sessions", json={"projectId": "p"}
    ).json()["sessionId"]
    output = {
        "kind": "data_version",
        "service": "data-primary",
        "dataset_id": "dataset-results",
        "version_id": "older-result",
        "semantic_id": "semantics-results",
        "object_id": "object-older-result",
        "media_type": "application/zip",
        "format": "zip",
        "size_bytes": 9,
        "checksum": "sha256:" + "b" * 64,
    }
    with connect_database(application.database) as connection:
        first = _insert_run(connection, session_id, 1)
        _insert_action(
            connection,
            first,
            1,
            status="COMPLETE",
            outputs=(output,),
            workflow_run_id="older-workflow",
        )
        second = _insert_run(connection, session_id, 2)
        connection.execute(
            "UPDATE sessions SET goal_revision = 2, agent_run_id = ? "
            "WHERE session_id = ?",
            (second, session_id),
        )
    assert client.get(f"/api/v12/sessions/{session_id}").json()[
        "remoteOutputs"
    ] == []
    results = client.get(f"/api/v12/sessions/{session_id}/results").json()
    assert results["nextCursor"] is None
    assert len(results["items"]) == 1
    assert results["items"][0]["goalRevision"] == 1
    assert results["items"][0]["remoteOutputs"] == [_wire_reference(output)]


def test_session_results_remain_session_scoped(kernel_settings) -> None:
    application, client = _client(kernel_settings)
    sessions = [
        client.post("/api/v12/sessions", json={"projectId": "p"}).json()["sessionId"]
        for _ in range(2)
    ]
    with connect_database(application.database) as connection:
        for index, session_id in enumerate(sessions):
            run_id = _insert_run(connection, session_id, 1)
            _insert_action(
                connection,
                run_id,
                1,
                status="COMPLETE",
                workflow_run_id=f"workflow-{index}",
            )
            connection.execute(
                "UPDATE sessions SET goal_revision = 1, agent_run_id = ? "
                "WHERE session_id = ?",
                (run_id, session_id),
            )
    for index, session_id in enumerate(sessions):
        result = client.get(f"/api/v12/sessions/{session_id}/results").json()
        assert result["sessionId"] == session_id
        assert result["projectId"] == "p"
        assert len(result["items"]) == 1
        assert result["items"][0]["agentRunId"] == f"run-{session_id}-1"
        assert result["items"][0]["workflowRunId"] == f"workflow-{index}"
    assert client.post(
        "/api/v12/sessions", json={"projectId": "other"}
    ).status_code == 404
