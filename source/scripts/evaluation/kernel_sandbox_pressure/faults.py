"""Deterministic HTTP fault transport used by pressure scenarios."""
from __future__ import annotations
from collections import Counter
import json
import re
import threading
import time
import httpx
from .context import ProbeEvent, ProbeRecorder
from .environment import CHECKSUM, PROJECT, VERSION, _digest, _safe_route

class ProbeTransport:
    def __init__(
        self,
        probes: ProbeRecorder,
        definition_digest: str,
        *,
        fault: str | None = None,
    ) -> None:
        self.probes = probes
        self.definition_digest = definition_digest
        self.fault = fault
        self.job_counter = 0
        self.jobs: dict[str, dict[str, object]] = {}
        self.cancelled_jobs: set[str] = set()
        self.calls = Counter()
        self.route_calls = Counter()
        self.service_route_calls = Counter()
        self.argument_fields = Counter()
        self._lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        started = time.monotonic()
        route = request.url.path
        operation = f"{request.method} {route}"
        self.route_calls[f"{request.method} {_safe_route(route)}"] += 1
        self.service_route_calls[
            f"{request.url.host} {request.method} {_safe_route(route)}"
        ] += 1
        try:
            if route.endswith("/internal/health/ready"):
                if self.fault == "readiness_down":
                    return self._response(
                        request,
                        {"status": "DOWN", "error": "SERVICE_NOT_READY"},
                        "readiness",
                        started,
                        503,
                    )
                digest = (
                    "sha256:" + "f" * 64
                    if self.fault == "executor_readiness_mismatch"
                    else self.definition_digest
                )
                body = {"status": "UP", "definitionSetDigest": digest}
                return self._response(request, body, "readiness", started)
            if request.url.host == "data-service" and route.startswith("/internal/tools/"):
                if self.fault == "data_503":
                    return self._response(
                        request,
                        {"code": "OBSERVATION_SERVICE_UNAVAILABLE"},
                        "observation",
                        started,
                        503,
                    )
                if self.fault == "observation_invalid":
                    return self._response(
                        request,
                        {"data": {"unexpected": True}},
                        "observation",
                        started,
                    )
                body = self._observation(request)
                return self._response(request, body, "observation", started)
            if request.url.host == "knowledge-next-service" and route.startswith("/internal/tools/"):
                body = json.loads(request.content or b"{}")
                if set(body) != {"callId", "projectId", "arguments"}:
                    self.probes.fail("PROBE_TOOL_ARGUMENT_MISMATCH", phase="knowledge", operation="invoke", detail="request envelope")
                if body.get("projectId") != PROJECT:
                    self.probes.fail("PROBE_PROJECT_ID_MISMATCH", phase="knowledge", operation="invoke", detail="project propagation")
                if not request.headers.get("X-Trace-ID"):
                    self.probes.fail("PROBE_TRACE_ID_MISSING", phase="knowledge", operation="invoke", detail="trace header")
                arguments = body.get("arguments") if isinstance(body.get("arguments"), dict) else {}
                self.argument_fields[
                    "knowledge:lookup_knowledge:" + ",".join(sorted(str(key) for key in arguments))
                ] += 1
                if route.endswith("/lookup_knowledge") and set(arguments) != {"key", "binding", "budget"}:
                    self.probes.fail("PROBE_TOOL_ARGUMENT_MISMATCH", phase="knowledge", operation="lookup_knowledge", detail="arguments")
                if self.fault == "knowledge_invalid_evidence":
                    return self._response(
                        request,
                        {"data": {"status": "NO_MATCH", "items": "invalid"}},
                        "knowledge_observation",
                        started,
                    )
                body = {
                    "data": {
                        "status": "NO_MATCH",
                        "receiptId": "sandbox-receipt",
                        "knowledgeRevision": 0,
                        "indexGeneration": "sandbox-index",
                        "observedAt": "2026-01-01T00:00:00Z",
                        "items": [],
                        "coverage": {},
                        "budget": {"max_chars": 100, "max_units": 1},
                        "timingsMs": {},
                    }
                }
                return self._response(request, body, "knowledge_observation", started)
            if request.url.host == "executor-control":
                if self.fault == "executor_submit_timeout" and request.method == "POST" and route.endswith("/jobs"):
                    raise httpx.ReadTimeout("sandbox executor timeout", request=request)
                return self._executor(request, started)
            self.probes.fail("PROBE_UNREGISTERED_NETWORK", phase="transport", operation=operation, detail=str(request.url))
            return httpx.Response(599, request=request, json={"error": "UNREGISTERED_NETWORK"})
        except httpx.ReadTimeout:
            raise
        except Exception as error:
            self.probes.fail("PROBE_HANDLER_EXCEPTION", phase="transport", operation=operation, detail=type(error).__name__)
            return httpx.Response(500, request=request, json={"error": "PROBE_HANDLER_EXCEPTION"})

    def _response(self, request, body, operation: str, started: float, status: int = 200) -> httpx.Response:
        self.probes.event(ProbeEvent(
            phase="transport",
            operation=operation,
            direction="inbound",
            route=request.url.path,
            status=str(status),
            duration_ms=(time.monotonic() - started) * 1000,
            field_names=tuple(sorted(body)) if isinstance(body, dict) else (),
            payload_digest=_digest(body),
        ))
        return httpx.Response(status, request=request, json=body)

    def _observation(self, request: httpx.Request) -> dict[str, object]:
        body = json.loads(request.content or b"{}")
        if set(body) != {"callId", "projectId", "arguments"}:
            self.probes.fail("PROBE_TOOL_ARGUMENT_MISMATCH", phase="observation", operation="invoke", detail="request envelope")
        if body.get("projectId") != PROJECT:
            self.probes.fail("PROBE_PROJECT_ID_MISMATCH", phase="observation", operation="invoke", detail="project propagation")
        if not request.headers.get("X-Trace-ID"):
            self.probes.fail("PROBE_TRACE_ID_MISSING", phase="observation", operation="invoke", detail="trace header")
        arguments = body.get("arguments") if isinstance(body.get("arguments"), dict) else {}
        tool_name = request.url.path.rsplit("/", 1)[-1]
        self.argument_fields[
            f"data:{tool_name}:" + ",".join(sorted(str(key) for key in arguments))
        ] += 1
        expected_arguments = {
            "get_data_inventory": set(),
            "inspect_data_version": {"version_id"},
            "inspect_data_table": {"version_id"},
        }.get(tool_name)
        if expected_arguments is not None and set(arguments) != expected_arguments:
            self.probes.fail(
                "PROBE_TOOL_ARGUMENT_MISMATCH",
                phase="observation",
                operation=tool_name,
                detail=f"expected={sorted(expected_arguments)} actual={sorted(arguments)}",
            )
        version_id = arguments.get("version_id", VERSION)
        if not isinstance(version_id, str):
            version_id = VERSION
        return {
            "data": {
                "status": "OK",
                "versionId": version_id,
                "currentVersion": version_id,
                "datasetId": "sandbox-dataset",
                "semanticId": "sandbox-semantic",
                "objects": [{
                    "objectId": "sandbox-object",
                    "role": "raw",
                    "mediaType": "text/csv",
                    "format": "csv",
                    "size": 16,
                    "checksum": CHECKSUM,
                }],
                "descriptor": {"fields": [{"fieldId": "value", "name": "value", "type": "number"}]},
                "semantics": {"status": "OK"},
            }
        }

    def _executor(self, request: httpx.Request, started: float) -> httpx.Response:
        route = request.url.path
        if request.method == "POST" and route.endswith("/jobs"):
            body = json.loads(request.content or b"{}")
            self.argument_fields[
                "executor:submit:" + ",".join(sorted(str(key) for key in body))
            ] += 1
            required = {
                "requestId", "projectId", "modelId", "modelVersion", "modelChecksum",
                "workflowId", "workflowVersion", "workflowChecksum", "executorId",
                "arguments", "inputReferences", "deadlineAt",
            }
            if set(body) != required:
                self.probes.fail("PROBE_EXECUTION_IDENTITY_MISMATCH", phase="executor", operation="submit", detail="job envelope")
            if body.get("projectId") != PROJECT:
                self.probes.fail("PROBE_PROJECT_ID_MISMATCH", phase="executor", operation="submit", detail="project propagation")
            with self._lock:
                self.job_counter += 1
                job_id = f"sandbox-job-{self.job_counter}"
                self.jobs[job_id] = body
            self.calls["submit"] += 1
            return self._response(request, self._job(job_id, body["requestId"], "QUEUED"), "executor_submit", started, 202)
        if "/jobs/by-request/" in route:
            request_id = route.rsplit("/", 1)[-1]
            for job_id, body in self.jobs.items():
                if body.get("requestId") == request_id:
                    return self._response(request, self._job(job_id, request_id, "SUCCEEDED"), "executor_get_by_request", started)
            return self._response(request, self._job("missing", request_id, "FAILED", error="JOB_NOT_FOUND"), "executor_get_by_request", started, 404)
        match = re.search(r"/jobs/([^/]+)(?:/cancel)?$", route)
        if match:
            job_id = match.group(1)
            body = self.jobs.get(job_id)
            if body is None:
                return self._response(request, self._job(job_id, "unknown", "FAILED", error="JOB_NOT_FOUND"), "executor_get", started, 404)
            if request.method == "POST":
                self.calls["cancel"] += 1
                self.cancelled_jobs.add(job_id)
                return self._response(request, self._job(job_id, body["requestId"], "CANCELLED"), "executor_cancel", started, 202)
            self.calls["get"] += 1
            if self.fault == "executor_hold" and job_id not in self.cancelled_jobs:
                return self._response(request, self._job(job_id, body["requestId"], "QUEUED"), "executor_get", started)
            if job_id in self.cancelled_jobs:
                return self._response(request, self._job(job_id, body["requestId"], "CANCELLED"), "executor_get", started)
            return self._response(request, self._job(job_id, body["requestId"], "SUCCEEDED"), "executor_get", started)
        return self._response(request, {"error": "UNKNOWN_EXECUTOR_ROUTE"}, "executor_unknown", started, 404)

    def transport_summary(self) -> dict[str, object]:
        by_service: dict[str, dict[str, object]] = {}
        service_names = {
            "data-service": "data",
            "knowledge-next-service": "knowledge",
            "executor-control": "executor",
        }
        for host, name in service_names.items():
            prefix = f"{host} "
            routes = {
                key[len(prefix):]: count
                for key, count in self.service_route_calls.items()
                if key.startswith(prefix)
            }
            arguments = {
                key.split(":", 2)[2]: count
                for key, count in self.argument_fields.items()
                if key.startswith(f"{name}:")
            }
            by_service[name] = {
                "count": sum(routes.values()),
                "routes": dict(sorted(routes.items())),
                "argumentFields": dict(sorted(arguments.items())),
            }
        return {
            **by_service,
            "executorOperations": dict(sorted(self.calls.items())),
            "allRoutes": dict(sorted(self.route_calls.items())),
        }

    @staticmethod
    def _job(job_id: str, request_id: str, status: str, *, error: str | None = None) -> dict[str, object]:
        return {
            "jobId": job_id,
            "requestId": request_id,
            "status": status,
            "stage": "COMPLETE" if status == "SUCCEEDED" else status,
            "error": error,
            "outputs": [],
            "serviceVersion": "sandbox-executor",
            "createdAt": "2026-01-01T00:00:00Z",
            "startedAt": "2026-01-01T00:00:00Z" if status != "QUEUED" else None,
            "finishedAt": "2026-01-01T00:00:01Z" if status in {"SUCCEEDED", "FAILED", "CANCELLED"} else None,
        }
