"""Contract tests for the deterministic Kernel sandbox pressure driver."""

import json

import httpx

from scripts.evaluation.kernel_sandbox_pressure.context import ProbeEvent, ProbeRecorder
from scripts.evaluation.kernel_sandbox_pressure.environment import (
    CHECKSUM,
    PROJECT,
    _digest,
    _safe_route,
    _seen_tool,
)
from scripts.evaluation.kernel_sandbox_pressure.faults import ProbeTransport


def test_probe_event_contains_only_structural_metadata() -> None:
    event = ProbeEvent(
        phase="test",
        operation="probe",
        direction="outbound",
        route="sandbox://provider",
        status="OK",
        duration_ms=1.25,
        field_names=("arguments", "projectId"),
        payload_digest=_digest({"message": "secret body"}),
    )
    encoded = json.dumps(event.as_dict(), ensure_ascii=False)
    assert event.as_dict()["schemaVersion"] == "kernel-probe-event.v1"
    assert "secret body" not in encoded
    assert "projectId" in encoded  # field names are safe, values are not recorded


def test_probe_digest_is_stable_and_redacts_values() -> None:
    value = {"message": "private", "sessionId": "session-secret"}
    assert _digest(value) == _digest({"sessionId": "session-secret", "message": "private"})
    assert "private" not in _digest(value)
    assert "session-secret" not in _digest(value)


def test_probe_rejects_unknown_response_fields() -> None:
    probes = ProbeRecorder()
    probes.assert_fields(
        {"data": {}, "unexpected": True},
        {"data"},
        phase="contract",
        operation="observation",
    )
    assert probes.failures[0]["code"] == "PROBE_UNEXPECTED_FIELD"


def test_observation_transport_checks_envelope_and_trace() -> None:
    probes = ProbeRecorder()
    transport = ProbeTransport(probes, "sha256:" + "b" * 64)
    request = httpx.Request(
        "POST",
        "http://data-service/internal/tools/inspect_data_version",
        headers={"X-Trace-ID": "trace-probe"},
        json={
            "callId": "call-probe",
            "projectId": PROJECT,
            "arguments": {"version_id": "sandbox-version"},
        },
    )
    response = transport(request)
    assert response.status_code == 200
    assert set(response.json()) == {"data"}
    assert response.json()["data"]["objects"][0]["checksum"] == CHECKSUM
    assert not probes.failures


def test_table_observation_transport_uses_version_identity_only() -> None:
    probes = ProbeRecorder()
    transport = ProbeTransport(probes, "sha256:" + "b" * 64)
    request = httpx.Request(
        "POST",
        "http://data-service/internal/tools/inspect_data_table",
        headers={"X-Trace-ID": "trace-probe"},
        json={
            "callId": "call-probe",
            "projectId": PROJECT,
            "arguments": {"version_id": "sandbox-version"},
        },
    )

    response = transport(request)

    assert response.status_code == 200
    assert not probes.failures


def test_executor_transport_preserves_request_identity_and_status_contract() -> None:
    probes = ProbeRecorder()
    transport = ProbeTransport(probes, "sha256:" + "c" * 64)
    body = {
        "requestId": "request-probe",
        "projectId": PROJECT,
        "modelId": "sandbox-model",
        "modelVersion": "1",
        "modelChecksum": "sha256:" + "d" * 64,
        "workflowId": "workflow-probe",
        "workflowVersion": "1",
        "workflowChecksum": "sha256:" + "e" * 64,
        "executorId": "sandbox-executor",
        "arguments": {},
        "inputReferences": [],
        "deadlineAt": "2026-01-01T00:05:00Z",
    }
    submit = transport(
        httpx.Request(
            "POST",
            "http://executor-control/internal/execution/v6/jobs",
            json=body,
        )
    )
    assert submit.status_code == 202
    job_id = submit.json()["jobId"]
    fetched = transport(
        httpx.Request(
            "GET",
            f"http://executor-control/internal/execution/v6/jobs/{job_id}",
        )
    )
    assert fetched.status_code == 200
    assert fetched.json()["requestId"] == body["requestId"]
    assert fetched.json()["status"] == "SUCCEEDED"
    assert not probes.failures


def test_tool_detection_ignores_argument_text() -> None:
    assert not _seen_tool(
        [{"type": "function_call", "name": "record_plan", "arguments": {}}],
        "prepare_task_inputs",
    )
    assert _seen_tool(
        [{"type": "function_call", "name": "prepare_task_inputs", "arguments": {}}],
        "prepare_task_inputs",
    )


def test_probe_route_and_transport_summary_redact_ids_and_expose_domains() -> None:
    assert _safe_route("/api/v11/sessions/3f7d7f38-1f32-4efb-9a17-9f3f7a1e9d10/events") == "/api/v11/sessions/{id}/events"
    probes = ProbeRecorder()
    transport = ProbeTransport(probes, "sha256:" + "b" * 64)
    request = httpx.Request(
        "POST",
        "http://data-service/internal/tools/inspect_data_version",
        headers={"X-Trace-ID": "trace-probe"},
        json={"callId": "call-probe", "projectId": PROJECT, "arguments": {"version_id": "v"}},
    )
    transport(request)
    summary = transport.transport_summary()
    assert summary["data"]["count"] == 1
    assert summary["data"]["routes"] == {"POST /internal/tools/inspect_data_version": 1}
    assert summary["data"]["argumentFields"] == {"version_id": 1}
    assert "call-probe" not in json.dumps(summary)
