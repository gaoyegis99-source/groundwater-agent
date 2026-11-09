"""Probe the Task ↔ WorkflowRun handoff through the real Kernel HTTP API."""

from __future__ import annotations

import asyncio
import json
import time

from agents.testing import ModelStep, ScriptedModel, function_call
from fastapi.testclient import TestClient
import httpx
import pytest

from kernel.tests.test_kernel_lifecycle import (
    _application,
    _final,
    _inspect,
    _job,
    _plan,
    _prepare,
    _tick,
    _turn,
)
from platform_kernel.database import connect_database


GOAL = "请运行摘要并交付 summary 结果"
OUTPUT = {
    "kind": "data_version",
    "service": "data-primary",
    "datasetId": "dataset-probe",
    "versionId": "version-probe",
    "semanticId": "semantic-probe",
    "objectId": "object-probe",
    "mediaType": "application/json",
    "format": "json",
    "sizeBytes": 2,
    "checksum": "sha256:" + "a" * 64,
    "outputName": "summary",
}


@pytest.mark.parametrize("remote_status", ["SUCCEEDED", "FAILED"])
def test_workflow_task_http_probe(kernel_settings, tmp_path, remote_status):
    """Compare HTTP responses, durable execution, and derived task progress."""
    observations = []
    submitted = []
    workflow_id = None
    workflow_agent_run_id = None

    def link_result(_call):
        assert workflow_id is not None and workflow_agent_run_id is not None
        return ModelStep(
            output=[
                function_call(
                    "update_task",
                    {
                        "expected_revision": 1,
                        "objective_quote": None,
                        "constraints": [],
                        "criteria": [],
                        "evidence": [
                            {
                                "criterion_id": "summary-result",
                                "agent_run_id": workflow_agent_run_id,
                                "workflow_run_id": workflow_id,
                            }
                        ],
                    },
                    call_id="task-result-link",
                )
            ]
        )

    steps = [
        ModelStep(
            output=[
                function_call(
                    "update_task",
                    {
                        "expected_revision": 0,
                        "objective_quote": GOAL,
                        "constraints": [],
                        "criteria": [
                            {
                                "id": "summary-result",
                                "text": "summary 结果",
                                "source_quote": "summary 结果",
                                "kind": "RESULT",
                                "model_id": "groundwater.summary",
                                "output_names": ["summary"],
                            }
                        ],
                        "evidence": [],
                    },
                    call_id="task-create",
                )
            ]
        ),
        ModelStep(output=[_final("任务已登记")]),
        _inspect(),
        _prepare(),
        ModelStep(output=[_plan(["run_summary"])]),
        ModelStep(
            output=[
                function_call(
                    "run_summary",
                    {"resource_id": "resource-1"},
                    call_id="summary-run",
                )
            ]
        ),
        # The resumed execution segment is final-reply-only. Evidence must be
        # linked in a later ordinary turn, never through that SDK checkpoint.
        ModelStep(output=[_final()]),
        ModelStep.respond(link_result),
        ModelStep(output=[_final("已登记执行结果")]),
    ]

    def executor(request):
        if request.method == "POST" and request.url.path.endswith("/jobs"):
            body = json.loads(request.content)
            submitted.append(body["requestId"])
            return httpx.Response(202, json=_job(body["requestId"], "QUEUED"))
        if request.method == "GET" and submitted:
            return httpx.Response(
                200,
                json=_job(
                    submitted[0],
                    remote_status,
                    outputs=[OUTPUT] if remote_status == "SUCCEEDED" else [],
                    error="SYNTHETIC_EXECUTION_FAILED"
                    if remote_status == "FAILED"
                    else None,
                ),
            )
        raise AssertionError("Unexpected executor request")

    application, api = _application(
        tmp_path,
        kernel_settings,
        ScriptedModel(steps),
        executor,
    )
    client = TestClient(api)

    def probe(operation, method, path, expected, **kwargs):
        started = time.perf_counter()
        response = client.request(method, path, **kwargs)
        observations.append(
            {
                "operation": operation,
                "http_status": response.status_code,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            }
        )
        assert response.status_code == expected, response.text
        return response.json()

    try:
        session = probe(
            "create_session",
            "POST",
            "/api/v12/sessions",
            200,
            json={"projectId": "p"},
        )
        session_id = session["sessionId"]
        started = time.perf_counter()
        accepted, completed = _turn(
            client, application, session_id, GOAL, read_only=True
        )
        observations.append(
            {
                "operation": "create_task_turn",
                "http_status": accepted.status_code,
                "return_status": completed.status_code,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            }
        )
        assert accepted.status_code == 202 and completed.status_code == 200
        assert completed.json()["agentOutput"] is not None
        task_path = f"/api/v12/sessions/{session_id}/task"
        initial_task = probe("task_initial", "GET", task_path, 200)["task"]
        assert initial_task["progress"]["state"] == "ACTIVE"
        assert initial_task["progress"]["criteria"][0]["satisfied"] is False

        started = time.perf_counter()
        accepted, pending = _turn(client, application, session_id, "run")
        observations.append(
            {
                "operation": "start_workflow_turn",
                "http_status": accepted.status_code,
                "return_status": pending.status_code,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            }
        )
        assert accepted.status_code == 202 and pending.status_code == 200
        workflow_agent_run_id = accepted.json()["agentRunId"]
        confirmation_id = pending.json()["confirmation"]["confirmationId"]
        waiting_task = probe("task_waiting", "GET", task_path, 200)["task"]
        assert waiting_task["progress"]["state"] == "WAITING_CONFIRMATION"

        confirmation_path = f"/api/v12/sessions/{session_id}/confirm"
        confirmed = probe(
            "confirm",
            "POST",
            confirmation_path,
            202,
            json={"confirmationId": confirmation_id},
        )
        assert confirmed["agentSteps"][-1] == "WAITING_EXECUTION"
        (workflow_id,) = confirmed["workflowRunIds"]
        replay = probe(
            "confirm_replay",
            "POST",
            confirmation_path,
            202,
            json={"confirmationId": confirmation_id},
        )
        assert replay["workflowRunIds"] == [workflow_id]
        executing_task = probe("task_executing", "GET", task_path, 200)["task"]
        assert executing_task["progress"]["state"] == "EXECUTING"

        workflow_path = f"/api/v12/workflow-runs/{workflow_id}"
        queued = probe(
            "workflow_queued",
            "GET",
            workflow_path,
            200,
            params={"projectId": "p"},
        )
        assert queued["status"] == "QUEUED"
        probe(
            "workflow_wrong_project",
            "GET",
            workflow_path,
            404,
            params={"projectId": "other"},
        )

        started = time.perf_counter()
        ready = _tick(application)
        observations.append(
            {
                "operation": "workflow_tick_and_agent_return",
                "ready_agent_count": len(ready),
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            }
        )
        final_workflow = probe(
            "workflow_final",
            "GET",
            workflow_path,
            200,
            params={"projectId": "p"},
        )
        assert final_workflow["status"] == remote_status
        assert len(final_workflow["outputs"]) == (
            1 if remote_status == "SUCCEEDED" else 0
        )
        unlinked_task = probe("task_before_link", "GET", task_path, 200)["task"]
        assert unlinked_task["progress"]["lifecycle"] == "ACTIVE"
        assert unlinked_task["progress"]["criteria"][0]["satisfied"] is False
        if remote_status == "SUCCEEDED":
            assert unlinked_task["progress"]["state"] == "ACTIVE"
            assert unlinked_task["progress"]["blockingReasons"] == []
        else:
            assert unlinked_task["progress"]["state"] == "BLOCKED"
            assert (
                "SYNTHETIC_EXECUTION_FAILED"
                in unlinked_task["progress"]["blockingReasons"]
            )
        unlinked_session = probe(
            "session_before_link",
            "GET",
            f"/api/v12/sessions/{session_id}",
            200,
        )
        assert unlinked_session["agentSteps"][-1] == (
            "COMPLETE" if remote_status == "SUCCEEDED" else "FAILED"
        )
        assert unlinked_session["task"]["state"] == unlinked_task["progress"]["state"]
        assert len(unlinked_session["remoteOutputs"]) == (
            1 if remote_status == "SUCCEEDED" else 0
        )

        started = time.perf_counter()
        accepted, linked = _turn(
            client, application, session_id, "请登记执行结果", read_only=True
        )
        observations.append(
            {
                "operation": "link_result_turn",
                "http_status": accepted.status_code,
                "return_status": linked.status_code,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            }
        )
        assert accepted.status_code == 202 and linked.status_code == 200
        final_task = probe("task_after_link", "GET", task_path, 200)["task"]
        assert final_task["progress"]["lifecycle"] == (
            "COMPLETE" if remote_status == "SUCCEEDED" else "ACTIVE"
        )
        assert final_task["progress"]["criteria"][0]["satisfied"] is (
            remote_status == "SUCCEEDED"
        )
        final_session = probe(
            "session_final",
            "GET",
            f"/api/v12/sessions/{session_id}",
            200,
        )
        assert final_session["task"]["state"] == final_task["progress"]["state"]
        assert final_session["task"]["lifecycle"] == final_task["progress"]["lifecycle"]
        assert submitted == [workflow_id]
        with connect_database(application.database) as connection:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM workflow_runs WHERE session_id=?",
                    (session_id,),
                ).fetchone()[0]
                == 1
            )
        print(
            "WORKFLOW_TASK_PROBE="
            + json.dumps(
                {
                    "scenario": remote_status.lower(),
                    "probe_assertions": "PASS",
                    "integration_result": "PASS",
                    "task_states": [
                        initial_task["progress"]["state"],
                        waiting_task["progress"]["state"],
                        executing_task["progress"]["state"],
                        unlinked_task["progress"]["state"],
                        final_task["progress"]["state"],
                    ],
                    "workflow_final": final_workflow["status"],
                    "before_link_blocking_reasons": unlinked_task["progress"][
                        "blockingReasons"
                    ],
                    "executor_submissions": len(submitted),
                    "observations": observations,
                },
                ensure_ascii=False,
            )
        )
    finally:
        client.close()
        asyncio.run(application.client.aclose())
