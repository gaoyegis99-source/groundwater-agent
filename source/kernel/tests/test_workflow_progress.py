from __future__ import annotations

import json
import sqlite3

from fastapi.testclient import TestClient
import httpx
import pytest

from platform_kernel.api import create_app
from platform_kernel.composition import PlatformApplication
from platform_kernel.contracts import JobStatusV6, DataVersionRefV2
from platform_kernel.database import connect_database, read_session_events, utc_now
from platform_kernel.interaction.persistence import create_session
from platform_kernel.application.query_dtos import ConfirmedAction
from platform_kernel.workflow_execution.projection import session_execution_tasks
from platform_kernel.workflow_execution.models import DispatchWork
from platform_kernel.workflow_execution.persistence.queue import service_target_digest
from platform_kernel.workflow_execution.persistence import (
    enqueue_batch, get_run, release_communication_error, save_job_status, save_rejection,
)


def _batch(settings, *, session_id="session", run_id="agent", count=2, project_id="p", **session_options):
    database = settings.data_root / "kernel.sqlite3"
    create_session(database, session_id, project_id, **session_options)
    model = settings.models[0]
    actions = tuple(ConfirmedAction(
        tool_call_id=f"call-{run_id}-{index}", position=index, tool=model.tool_name,
        model_id=model.model_id, model_version=model.model_version,
        model_checksum=model.model_checksum, workflow_id=model.workflow_id,
        workflow_version=model.workflow_version, workflow_checksum=model.workflow_checksum,
        input_schema_checksum=model.input_schema_checksum,
        arguments={"resource_id": "SECRET_ARGUMENT"},
    ) for index in range(count))
    with connect_database(database) as connection:
        now = utc_now()
        connection.execute(
            """INSERT INTO agent_runs(
                   agent_run_id,session_id,project_id,goal_revision,provider,model,
                   status,steps_json,input_tokens,output_tokens,total_tokens,trace_id,
                   created_at,started_at
               ) VALUES (?,?,?,1,'fixed','model','WAITING_EXECUTION','[]',0,0,0,'trace',?,?)""",
            (run_id, session_id, project_id, now, now),
        )
        runs = enqueue_batch(
            connection, agent_run_id=run_id, session_id=session_id, project_id=project_id,
            actions=actions, models={model.model_id: model},
            services={service.name: service for service in settings.services},
        )
    return database, runs


def _save(database, run, status, *, stage=None, outputs=(), error=None):
    current = get_run(database, run.project_id, run.run_id)
    return save_job_status(database, DispatchWork(current, "SUBMITTED", "future"), JobStatusV6(
        job_id=f"job-{run.run_id}", request_id=run.run_id, status=status,
        stage=stage, outputs=outputs, error=error,
    ))


def _events(database, event_type, session_id="session"):
    with connect_database(database) as connection:
        return read_session_events(connection, session_id, event_type)


def _tasks(database, run_ids=("agent",), session_id="session", project_id="p"):
    with connect_database(database) as connection:
        connection.execute("BEGIN")
        return session_execution_tasks(connection, session_id, project_id, run_ids)


def test_publication_lookup_checks_run_ownership_and_job_identity(kernel_settings):
    settings = kernel_settings()
    database, (run,) = _batch(settings, count=1)
    calls = []
    reply = {"jobId": f"job-{run.run_id}", "requestId": run.run_id,
             "publicationId": "publication-1", "state": "READY"}
    upstream_status = 200

    def executor(request):
        calls.append(request.url.path)
        return httpx.Response(upstream_status, json=reply)

    application = PlatformApplication(settings=settings, client=httpx.AsyncClient(
        transport=httpx.MockTransport(executor),
    ))
    client = TestClient(create_app(application=application))
    path = f"/api/v12/workflow-runs/{run.run_id}/publication"
    project = {"projectId": "p"}
    unknown = {"workflowRunId": run.run_id, "publicationId": None, "state": None}
    assert client.get(path, params=project).json() == unknown
    assert calls == []
    _save(database, run, "RUNNING", stage="COMMIT_PENDING")
    assert client.get(path, params=project).json() == {
        **unknown, "publicationId": "publication-1", "state": "READY",
    }
    reply["requestId"] = "different-run"
    assert client.get(path, params=project).json() == unknown
    upstream_status = 503
    assert client.get(path, params=project).status_code == 503
    assert client.get(path, params={"projectId": "other"}).status_code == 404
    _, (other,) = _batch(
        settings, count=1, session_id="other-session", run_id="other-agent", owner_subject="other-owner",
    )
    assert client.get(f"/api/v12/workflow-runs/{other.run_id}/publication", params=project).status_code == 404
    assert len(calls) == 3


def test_enqueue_freezes_service_endpoint_digest(kernel_settings):
    settings = kernel_settings()
    database, (run,) = _batch(settings, count=1)
    with connect_database(database) as connection:
        target_digest = connection.execute(
            "SELECT target_digest FROM workflow_service_bindings WHERE workflow_run_id=?",
            (run.run_id,),
        ).fetchone()[0]
    service = next(item for item in settings.services if item.name == run.execution_service)
    assert target_digest == service_target_digest(service)
    assert service.internal_base_url not in target_digest


def test_workflow_stages_change_once_and_terminal_state_wins(kernel_settings):
    database, (run, later) = _batch(kernel_settings())
    assert [task["stage"] for task in _tasks(database)["agent"]] == ["QUEUED", "QUEUED"]
    for status, stage in [
        ("QUEUED", None), ("QUEUED", None), ("RUNNING", "DAG"), ("RUNNING", "DAG"),
        ("RUNNING", "FINALIZING"), ("RUNNING", "COMMIT_PENDING"),
    ]:
        assert not _save(database, run, status, stage=stage)
    events = _events(database, "workflow.stage_changed")
    assert [item["data"] for item in events] == [
        {
            "workflow_run_id": run.run_id, "agent_run_id": "agent", "stage": stage,
            "model_id": run.model_id, "model_version": run.model_version,
            "model_checksum": run.model_checksum, "workflow_id": run.workflow_id,
            "workflow_version": run.workflow_version, "workflow_checksum": run.workflow_checksum,
        }
        for stage in ("QUEUED", "EXECUTING", "PUBLISHING")
    ]
    running = _tasks(database)["agent"][0]
    assert running["status"] == "RUNNING"
    assert running["stage"] == "PUBLISHING"
    assert running["output_count"] == 0
    output = DataVersionRefV2(
        kind="data_version", service="data-primary", dataset_id="dataset-results",
        version_id="SECRET_OUTPUT_VERSION", semantic_id="semantics-results",
        object_id="object-output", media_type="application/json", format_id="json",
        size_bytes=4, checksum="output", output_name="summary",
    )
    assert _save(database, run, "SUCCEEDED", stage="SUCCEEDED", outputs=(output,))
    complete, queued = _tasks(database)["agent"]
    assert complete["status"] == "SUCCEEDED"
    assert complete["stage"] is None
    assert complete["output_count"] == 1
    assert complete["started_at"] is not None and complete["finished_at"] is not None
    assert complete["duration_ms"] >= 0
    assert queued["workflow_run_id"] == later.run_id and queued["stage"] == "QUEUED"
    assert _events(database, "workflow.stage_changed") == events
    assert "SECRET" not in json.dumps(complete)


def test_communication_changes_are_deduplicated_and_clear_on_recovery(kernel_settings):
    database, (run,) = _batch(kernel_settings(), count=1)
    release_communication_error(database, run.run_id, "EXECUTION_SERVICE_UNAVAILABLE:private detail")
    release_communication_error(database, run.run_id, "EXECUTION_SERVICE_UNAVAILABLE:another detail")
    assert _tasks(database)["agent"][0]["communication_error"] == "EXECUTION_SERVICE_UNAVAILABLE"
    assert not _save(database, run, "RUNNING", stage="DAG")
    assert not _save(database, run, "RUNNING", stage="DAG")
    release_communication_error(database, run.run_id, "EXECUTION_SERVICE_UNAVAILABLE:last detail")
    assert _save(database, run, "FAILED", error="WORKFLOW_FAILED")
    events = _events(database, "workflow.communication_changed")
    assert [item["data"]["error_code"] for item in events] == [
        "EXECUTION_SERVICE_UNAVAILABLE", None, "EXECUTION_SERVICE_UNAVAILABLE", None,
    ]
    assert "detail" not in json.dumps(events)
    assert _tasks(database)["agent"][0]["communication_error"] is None


def test_blocked_successors_have_explicit_cause_but_submit_failures_do_not(kernel_settings):
    database, (first, second) = _batch(kernel_settings())
    save_rejection(database, first, "EXECUTION_REQUEST_INVALID")
    tasks = _tasks(database)["agent"]
    assert [task["workflow_run_id"] for task in tasks] == [first.run_id, second.run_id]
    assert [task["status"] for task in tasks] == ["FAILED", "FAILED"]
    assert tasks[0]["blocked_by_workflow_run_id"] is None
    assert tasks[1]["blocked_by_workflow_run_id"] == first.run_id
    assert all(task["output_count"] == 0 for task in tasks)
    assert all(task["error_code"] == "EXECUTION_REQUEST_INVALID" for task in tasks)
    assert tasks[1]["duration_ms"] == 0
    assert _events(database, "workflow.failed")[-1]["data"]["blocked_by_workflow_run_id"] == first.run_id


@pytest.mark.parametrize("event_type", ["workflow.stage_changed", "workflow.communication_changed"])
def test_progress_event_failure_rolls_back_job_state_and_other_progress(kernel_settings, event_type):
    database, (run,) = _batch(kernel_settings(), count=1)
    if event_type == "workflow.communication_changed":
        release_communication_error(database, run.run_id, "EXECUTION_SERVICE_UNAVAILABLE:private")
    with connect_database(database) as connection:
        connection.execute(
            "CREATE TRIGGER fail_progress BEFORE INSERT ON session_events "
            f"WHEN NEW.event_type = '{event_type}' "
            "BEGIN SELECT RAISE(ABORT, 'event unavailable'); END",
        )
    with pytest.raises(sqlite3.IntegrityError, match="event unavailable"):
        _save(database, run, "RUNNING", stage="DAG")
    stored = get_run(database, "p", run.run_id)
    assert stored.status == "QUEUED" and stored.remote_job_id is None
    assert _events(database, "workflow.stage_changed") == []
    with connect_database(database) as connection:
        dispatch = connection.execute("SELECT * FROM workflow_dispatches").fetchone()
    assert dispatch["state"] == "REQUESTED"
    if event_type == "workflow.communication_changed":
        assert dispatch["last_communication_error"] == "EXECUTION_SERVICE_UNAVAILABLE:private"


def test_task_projection_filters_session_project_and_run_without_writes(kernel_settings):
    settings = kernel_settings()
    database, _ = _batch(settings)
    _batch(settings, session_id="other", run_id="other-agent", count=1)
    _batch(settings, session_id="foreign", run_id="foreign-agent", count=1, project_id="other-project")
    before = _events(database, "workflow.queued")
    assert set(_tasks(database, ("agent", "other-agent"))) == {"agent"}
    assert _tasks(database, ("other-agent",)) == {}
    assert _tasks(database, project_id="other-project") == {}
    assert _tasks(database, ()) == {}
    assert _tasks(database, ("foreign-agent",), session_id="foreign") == {}
    assert _events(database, "workflow.queued") == before
