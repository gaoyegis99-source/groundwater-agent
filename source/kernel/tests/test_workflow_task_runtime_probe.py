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
from platform_kernel.access.identity import RequestIdentity
from platform_kernel.api.dependencies import current_identity
from platform_protocol_contracts import build_problem_details


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
    diagnostics_requests = []
    diagnostic_mode = "normal"

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
        if request.url.path.endswith("/nodes"):
            diagnostics_requests.append(request.url.path)
            if diagnostic_mode == "down":
                raise httpx.ConnectError("private upstream URL", request=request)
            if diagnostic_mode == "missing":
                problem = build_problem_details("JOB_NOT_FOUND", 404, "Missing job")
                return httpx.Response(404, json=problem.model_dump(by_alias=True),
                                      headers={"Content-Type": "application/problem+json"})
            attempts = {} if diagnostic_mode == "legacy" else {"attemptCount": 2}
            return httpx.Response(200, json={"nodes": [{
                "nodeId": "summarize", "operatorId": "table.summary", "runtime": "native",
                "status": remote_status, **attempts,
                "startedAt": "2026-11-12T00:00:00Z", "finishedAt": "2026-11-12T00:00:01Z",
                "error": "private raw error" if diagnostic_mode == "legacy" else (
                    "WORKER_LOST" if remote_status == "FAILED" else None
                ),
                "parametersJson": "private", "remoteStateJson": "private",
            }]})
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
        nodes_path = f"{workflow_path}/nodes"
        assert probe("nodes_pending", "GET", nodes_path, 200, params={"projectId": "p"}) == {
            "workflowRunId": workflow_id, "availability": "NOT_DISPATCHED", "nodes": [],
        }
        assert diagnostics_requests == []
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
        probe("nodes_wrong_project", "GET", nodes_path, 404, params={"projectId": "other"})
        api.dependency_overrides[current_identity] = lambda: RequestIdentity(
            "other-researcher", "Other", frozenset({"researcher"}),
        )
        try:
            probe("nodes_wrong_owner", "GET", nodes_path, 404, params={"projectId": "p"})
        finally:
            del api.dependency_overrides[current_identity]
        assert diagnostics_requests == []
        nodes = probe("nodes_final", "GET", nodes_path, 200, params={"projectId": "p"})
        assert nodes == {"workflowRunId": workflow_id, "availability": "AVAILABLE", "nodes": [{
            "nodeId": "summarize", "operatorId": "table.summary", "runtime": "native",
            "status": remote_status, "attemptCount": 2,
            "startedAt": "2026-11-12T00:00:00Z", "finishedAt": "2026-11-12T00:00:01Z",
            "errorCode": "WORKER_LOST" if remote_status == "FAILED" else None,
        }]}
        assert diagnostics_requests == [f"/internal/execution/v6/jobs/{final_workflow['remoteJobId']}/nodes"]
        with connect_database(application.database) as connection:
            binding = connection.execute(
                "SELECT target_digest FROM workflow_service_bindings WHERE workflow_run_id=?", (workflow_id,)
            ).fetchone()[0]
            connection.execute(
                "UPDATE workflow_service_bindings SET target_digest=? WHERE workflow_run_id=?",
                ("sha256:" + "f" * 64, workflow_id),
            )
        assert probe("nodes_target_changed", "GET", nodes_path, 200, params={"projectId": "p"}) == {
            "workflowRunId": workflow_id, "availability": "UNAVAILABLE", "nodes": [],
        }
        assert len(diagnostics_requests) == 1
        with connect_database(application.database) as connection:
            connection.execute(
                "UPDATE workflow_service_bindings SET target_digest=? WHERE workflow_run_id=?", (binding, workflow_id)
            )
        diagnostic_mode = "missing"
        assert probe("nodes_job_missing", "GET", nodes_path, 200, params={"projectId": "p"}) == {
            "workflowRunId": workflow_id, "availability": "UNAVAILABLE", "nodes": [],
        }
        diagnostic_mode = "legacy"
        old_node = probe("nodes_old_executor", "GET", nodes_path, 200, params={"projectId": "p"})["nodes"][0]
        assert old_node["attemptCount"] is None
        assert old_node["errorCode"] == "WORKFLOW_NODE_FAILED"
        diagnostic_mode = "down"
        problem = probe("nodes_upstream_down", "GET", nodes_path, 503, params={"projectId": "p"})
        assert problem["code"] == "WORKFLOW_NODE_DIAGNOSTICS_UNAVAILABLE"
        assert "private upstream URL" not in json.dumps(problem)
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
