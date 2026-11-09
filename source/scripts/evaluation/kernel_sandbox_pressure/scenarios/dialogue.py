"""Dialogue, HTTP contract, and bounded-load pressure scenarios."""
from __future__ import annotations
import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
import time
import httpx
from fastapi.testclient import TestClient
from platform_kernel.api import create_app
from platform_kernel.composition import PlatformApplication
from platform_kernel.database import connect_database
from ..context import ProbeEvent, ProbeRecorder
from ..environment import PROJECT, _digest, latency_stats, percentile
from ..faults import ProbeTransport
from ..probe import RouterModel, ScenarioModel, _attach_readiness_transport, _build_application, _settings, _wait_for_state

async def _run_in_process(root: Path, probes: ProbeRecorder) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="kernel-sandbox-") as temporary:
        settings = _settings(root, Path(temporary))
        transport = ProbeTransport(probes, settings.definition_set_digest)
        client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        model = ScenarioModel(probes)
        application = PlatformApplication(
            settings=settings,
            model=model,
            client=client,
            intent_router_model=RouterModel(steps=128),
        )
        await application.start()
        sessions: list[tuple[str, str, str]] = []
        try:
            for index in range(20):
                mode = "DIALOGUE" if index < 10 else "EXECUTION" if index < 18 else "STATUS"
                subject = f"sandbox-user-{index:02d}"
                session = application.interaction.create_session(subject).session.session_id
                sessions.append((session, subject, mode))
                probes.event(ProbeEvent(
                    phase="state",
                    operation="session.create",
                    direction="internal",
                    route="sqlite://sessions",
                    status="CREATED",
                    duration_ms=0,
                    field_names=("owner", "project", "state"),
                    payload_digest=_digest({"state": "CREATED"}),
                ))

            started = time.monotonic()
            accept_latencies: list[float] = []
            async def send(item: tuple[str, str, str]):
                session, subject, mode = item
                item_started = time.monotonic()
                if mode == "STATUS":
                    result = await asyncio.to_thread(application.interaction.get, session)
                    accept_latencies.append(time.monotonic() - item_started)
                    return result
                requested_mode = "DIALOGUE" if mode == "DIALOGUE" else "AUTO"
                text = (
                    f"sandbox-knowledge-chat-{subject}"
                    if mode == "DIALOGUE" and int(subject.rsplit("-", 1)[-1]) < 2
                    else f"sandbox-{('chat' if mode == 'DIALOGUE' else 'exec')}-{subject}"
                )
                result = await asyncio.to_thread(
                    application.interaction.message,
                    session,
                    f"sandbox-request-{subject}",
                    text,
                    requested_mode,
                    owner_subject=subject,
                )
                accept_latencies.append(time.monotonic() - item_started)
                return result

            await asyncio.gather(*(send(item) for item in sessions))
            accept_seconds = time.monotonic() - started
            probes.event(ProbeEvent(
                phase="state",
                operation="agent_run.accept",
                direction="internal",
                route="sqlite://agent_runs",
                status="ACCEPTED",
                duration_ms=accept_seconds * 1000,
                field_names=("agentRun", "state"),
                payload_digest=_digest({"state": "ACCEPTED", "count": 18}),
            ))
            execution = []
            for session, subject, mode in sessions:
                if mode != "EXECUTION":
                    continue
                result = await _wait_for_state(application, session, {"WAITING_CONFIRMATION", "WAITING_EXECUTION", "COMPLETE"})
                view = result["view"]
                if view.session.confirmation is not None:
                    probes.event(ProbeEvent(
                        phase="state",
                        operation="confirmation.pending",
                        direction="internal",
                        route="sqlite://confirmations",
                        status="PENDING",
                        duration_ms=0,
                        field_names=("confirmation", "state"),
                        payload_digest=_digest({"state": "PENDING"}),
                    ))
                    await application.interaction.confirm(session, view.session.confirmation.confirmation_id, subject)
                    # A repeated decision is an idempotent no-op; it must not
                    # allocate another WorkflowRun or publish a second job.
                    await application.interaction.confirm(
                        session, view.session.confirmation.confirmation_id, subject
                    )
                    probes.event(ProbeEvent(
                        phase="state",
                        operation="confirmation.confirm",
                        direction="internal",
                        route="sqlite://confirmations",
                        status="CONFIRMED",
                        duration_ms=0,
                        field_names=("confirmation", "state"),
                        payload_digest=_digest({"state": "CONFIRMED"}),
                    ))
                execution.append(result["status"])
            for session, _subject, mode in sessions:
                if mode == "DIALOGUE":
                    await _wait_for_state(application, session, {"COMPLETE"})
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                states = [application.interaction.get(session).agent_steps[-1:] for session, _subject, mode in sessions if mode == "EXECUTION"]
                if states and all(item and item[-1] == "COMPLETE" for item in states):
                    break
                await asyncio.sleep(0.03)
            probes.event(ProbeEvent(
                phase="state",
                operation="workflow_run.complete",
                direction="internal",
                route="sqlite://workflow_runs",
                status="SUCCEEDED",
                duration_ms=0,
                field_names=("workflowRun", "state"),
                payload_digest=_digest({"state": "SUCCEEDED", "count": 8}),
            ))
            with connect_database(application.database) as connection:
                counts = {
                    "sessions": connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
                    "agentRuns": connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0],
                    "workflowRuns": connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0],
                    "messages": connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                }
            snapshot = application.metric_snapshot()
            return {
                "status": "PASS" if not probes.failures else "FAILED",
                "users": len(sessions),
                "acceptedElapsedSeconds": round(accept_seconds, 4),
                "acceptLatencySeconds": latency_stats(accept_latencies),
                "modelPeakConcurrency": model.peak,
                "configuredAgentConcurrency": settings.agent_concurrency,
                "definitionSetDigest": settings.definition_set_digest,
                "transportCalls": transport.transport_summary(),
                "storeCounts": counts,
                "metrics": snapshot,
                "criteria": {
                    "twentyUsersCreated": len(sessions) == 20,
                    "agentConcurrencyBounded": model.peak <= settings.agent_concurrency,
                    "noDuplicateWorkflowRuns": counts["workflowRuns"] == 8,
                    "noSqliteBusy": snapshot["sqlite_busy"] == 0,
                    "acceptP95Under3s": percentile(accept_latencies, 0.95) <= 3.0,
                },
            }
        finally:
            await application.close()
            await client.aclose()


def _run_http_probe(root: Path, probes: ProbeRecorder) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="kernel-http-sandbox-") as temporary:
        settings = _settings(root, Path(temporary))
        transport = ProbeTransport(probes, settings.definition_set_digest)
        client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        application = PlatformApplication(
            settings=settings,
            model=ScenarioModel(probes, steps=128),
            client=client,
            intent_router_model=RouterModel(steps=64),
        )
        _attach_readiness_transport(application, client)
        app = create_app(application=application)
        with TestClient(app) as http:
            headers = {
                "X-Project-ID": PROJECT,
                "X-Trace-ID": "trace-http-probe",
                "X-Request-ID": "request-http-probe",
            }
            capabilities = http.get("/api/v12/capabilities", headers=headers)
            if capabilities.status_code != 200:
                probes.fail("PROBE_CAPABILITIES_INVALID", phase="http", operation="capabilities", detail=str(capabilities.status_code))
            session_response = http.post("/api/v12/sessions", headers=headers, json={"projectId": PROJECT})
            if session_response.status_code != 200:
                probes.fail("PROBE_SESSION_CREATE_INVALID", phase="http", operation="create_session", detail=str(session_response.status_code))
            session_id = session_response.json().get("sessionId")
            request_id = "sandbox-http-request"
            message = http.post(
                f"/api/v12/sessions/{session_id}/messages",
                headers=headers,
                json={"requestId": request_id, "message": "sandbox-chat-http", "requestedMode": "DIALOGUE"},
            )
            replay = http.post(
                f"/api/v12/sessions/{session_id}/messages",
                headers=headers,
                json={"requestId": request_id, "message": "sandbox-chat-http", "requestedMode": "DIALOGUE"},
            )
            conflict = http.post(
                f"/api/v12/sessions/{session_id}/messages",
                headers=headers,
                json={"requestId": request_id, "message": "different-payload", "requestedMode": "DIALOGUE"},
            )
            if message.status_code != 202 or replay.status_code != 202 or message.json() != replay.json():
                probes.fail("PROBE_IDEMPOTENCY_MISMATCH", phase="http", operation="create_message", detail="replay differs")
            if conflict.status_code != 409 or conflict.json().get("code") != "MESSAGE_REQUEST_CONFLICT":
                probes.fail("PROBE_CONFLICT_RESPONSE_INVALID", phase="http", operation="create_message", detail=str(conflict.status_code))
            reserved_session = http.post(
                "/api/v12/sessions",
                headers=headers,
                json={"projectId": PROJECT},
            ).json()["sessionId"]
            capacity_session = http.post(
                "/api/v12/sessions",
                headers=headers,
                json={"projectId": PROJECT},
            ).json()["sessionId"]
            with connect_database(application.database) as connection:
                connection.execute(
                    "UPDATE sessions SET pending_agent_run_id=? WHERE session_id=?",
                    ("sandbox-capacity-reservation", reserved_session),
                )
            capacity = http.post(
                f"/api/v12/sessions/{capacity_session}/messages",
                headers=headers,
                json={"requestId": "capacity-probe", "message": "sandbox-capacity", "requestedMode": "DIALOGUE"},
            )
            with connect_database(application.database) as connection:
                connection.execute(
                    "UPDATE sessions SET pending_agent_run_id=NULL WHERE session_id=?",
                    (reserved_session,),
                )
            if capacity.status_code != 429 or capacity.headers.get("Retry-After") != "5":
                probes.fail(
                    "PROBE_CAPACITY_RESPONSE_INVALID",
                    phase="http",
                    operation="create_message",
                    detail=f"{capacity.status_code}:{capacity.json().get('code')}",
                )
            ready = http.get("/api/v12/ready", headers=headers)
            metrics = http.get("/internal/metrics", headers=headers)
            health = http.get("/api/v12/health", headers=headers)
            wrong_project = http.get(f"/api/v12/sessions/{session_id}", headers={"X-Project-ID": "other-project"})
            missing_session = http.get("/api/v12/sessions/missing-session", headers=headers)
            if wrong_project.status_code != 403:
                probes.fail("PROBE_OWNER_BOUNDARY_INVALID", phase="http", operation="get_session", detail=str(wrong_project.status_code))
            if missing_session.status_code != 404:
                probes.fail("PROBE_NOT_FOUND_RESPONSE_INVALID", phase="http", operation="get_session", detail=str(missing_session.status_code))
            ready_payload = ready.json() if ready.headers.get("content-type", "").startswith("application/json") else {}
            ready_components = ready_payload.get("components", {}) if isinstance(ready_payload, dict) else {}
            if ready.status_code != 200 or not isinstance(ready_components, dict) or any(
                not isinstance(value, dict) or value.get("status") != "UP"
                for value in ready_components.values()
            ):
                probes.fail("PROBE_READINESS_STATUS_INVALID", phase="http", operation="ready", detail=str(ready.status_code))
            if metrics.status_code != 200 or "platform_" not in metrics.text:
                probes.fail("PROBE_METRICS_UNAVAILABLE", phase="http", operation="metrics", detail=str(metrics.status_code))
            if health.status_code != 200:
                probes.fail("PROBE_HEALTH_INVALID", phase="http", operation="health", detail=str(health.status_code))
            if not message.headers.get("X-Request-ID") or not message.headers.get("X-Trace-ID"):
                probes.fail("PROBE_TRACE_ID_MISSING", phase="http", operation="create_message", detail="response headers")
            event_lines: list[str] = []
            sse_first_started = time.monotonic()
            # The production endpoint deliberately keeps a connection open for
            # 25 seconds.  Shorten that bounded test window without changing
            # production code or waiting for the keepalive deadline.
            from platform_kernel.api.routes import session_streams as session_routes

            original_clock = session_routes.time

            class _ProbeClock:
                def __init__(self) -> None:
                    self.calls = 0

                def monotonic(self) -> float:
                    self.calls += 1
                    value = original_clock.monotonic()
                    return value if self.calls <= 2 else value + 30

                def __getattr__(self, name: str):
                    return getattr(original_clock, name)

            session_routes.time = _ProbeClock()
            try:
                with http.stream("GET", f"/api/v12/sessions/{session_id}/events?afterEventId=0", headers=headers) as stream:
                    for line in stream.iter_lines():
                        event_lines.append(line)
                        if len(event_lines) >= 3:
                            break
            finally:
                session_routes.time = original_clock
            sse_first_seconds = time.monotonic() - sse_first_started
            original_settings = application.settings
            application.settings = replace(original_settings, identity_mode="gateway")
            try:
                unauthenticated = http.get("/api/v12/capabilities", headers={"X-Project-ID": PROJECT})
                auditor = http.get(
                    "/api/v12/sessions",
                    headers={
                        "X-Project-ID": PROJECT,
                        "X-Platform-Subject": "sandbox-auditor",
                        "X-Platform-Display-Name": "Sandbox Auditor",
                        "X-Platform-Roles": "viewer",
                    },
                )
            finally:
                application.settings = original_settings
            if unauthenticated.status_code != 401:
                probes.fail("PROBE_AUTH_RESPONSE_INVALID", phase="http", operation="capabilities", detail=str(unauthenticated.status_code))
            if auditor.status_code != 403:
                probes.fail("PROBE_ROLE_RESPONSE_INVALID", phase="http", operation="list_sessions", detail=str(auditor.status_code))
            reconnect_lines: list[str] = []
            sse_reconnect_started = time.monotonic()
            session_routes.time = _ProbeClock()
            try:
                with http.stream(
                    "GET",
                    f"/api/v12/sessions/{session_id}/events",
                    headers={**headers, "Last-Event-ID": "0"},
                ) as stream:
                    for line in stream.iter_lines():
                        reconnect_lines.append(line)
                        if len(reconnect_lines) >= 3:
                            break
            finally:
                session_routes.time = original_clock
            sse_reconnect_seconds = time.monotonic() - sse_reconnect_started
            if not event_lines or not reconnect_lines:
                probes.fail(
                    "PROBE_DROPPED_EVENT",
                    phase="http",
                    operation="sse_reconnect",
                    detail="no SSE body observed",
                )
            sse_metrics = application.metric_snapshot()["sse"]
            return {
                "status": "PASS" if not probes.failures else "FAILED",
                "capabilitiesStatus": capabilities.status_code,
                "messageStatus": message.status_code,
                "readyStatus": ready.status_code,
                "readyComponents": {
                    str(name): value.get("status")
                    for name, value in ready_components.items()
                    if isinstance(value, dict)
                },
                "metricsStatus": metrics.status_code,
                "healthStatus": health.status_code,
                "authStatus": unauthenticated.status_code,
                "auditorStatus": auditor.status_code,
                "conflictStatus": conflict.status_code,
                "capacityStatus": capacity.status_code,
                "missingStatus": missing_session.status_code,
                "sseLinesObserved": len(event_lines),
                "sseReconnectLinesObserved": len(reconnect_lines),
                "sseFirstSeconds": round(sse_first_seconds, 4),
                "sseReconnectSeconds": round(sse_reconnect_seconds, 4),
                "sseMetrics": sse_metrics,
                "criteria": {
                    "readyHealthy": ready.status_code == 200,
                    "sseReconnectObserved": bool(reconnect_lines),
                    "sseReconnectUnder10s": sse_reconnect_seconds <= 10.0,
                },
            }
        asyncio.run(client.aclose())


async def _run_http_load(root: Path, probes: ProbeRecorder) -> dict[str, object]:
    """Exercise the same public ASGI boundary with concurrent synthetic users."""
    with tempfile.TemporaryDirectory(prefix="kernel-http-load-") as temporary:
        application, transport, client = _build_application(root, Path(temporary), probes)
        api = create_app(application=application)
        await application.start()
        message_latencies: list[float] = []
        create_latencies: list[float] = []
        queue_waits: list[float] = []
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=api), base_url="http://kernel"
            ) as http:
                def headers(index: int) -> dict[str, str]:
                    return {
                        "X-Project-ID": PROJECT,
                        "X-Trace-ID": f"load-trace-{index}",
                        "X-Request-ID": f"load-request-{index}",
                    }

                async def create(index: int) -> httpx.Response:
                    started = time.monotonic()
                    response = await http.post(
                        "/api/v12/sessions", headers=headers(index), json={"projectId": PROJECT}
                    )
                    create_latencies.append(time.monotonic() - started)
                    return response

                created = await asyncio.gather(*(create(index) for index in range(20)))
                sessions = [
                    response.json().get("sessionId")
                    for response in created
                    if response.status_code == 200
                ]
                if len(sessions) != 20:
                    probes.fail(
                        "PROBE_HTTP_LOAD_SESSION_CREATE",
                        phase="http_load",
                        operation="create_session",
                        detail=f"created={len(sessions)}",
                    )

                async def message(index: int, session_id: str) -> httpx.Response:
                    started = time.monotonic()
                    response = await http.post(
                        f"/api/v12/sessions/{session_id}/messages",
                        headers=headers(index),
                        json={
                            "requestId": f"load-message-{index}",
                            "message": "sandbox-exec" if index >= 10 else "sandbox-chat",
                            "requestedMode": "AUTO" if index >= 10 else "DIALOGUE",
                        },
                    )
                    message_latencies.append(time.monotonic() - started)
                    return response

                message_responses = await asyncio.gather(
                    *(message(index, sessions[index]) for index in range(min(18, len(sessions))))
                )
                unexpected = [
                    response for response in message_responses
                    if response.status_code not in {202, 429}
                    or (response.status_code == 429 and not response.headers.get("Retry-After"))
                ]
                if unexpected:
                    probes.fail(
                        "PROBE_HTTP_LOAD_MESSAGE_ACCEPT",
                        phase="http_load",
                        operation="create_message",
                        detail=str(sorted({response.status_code for response in message_responses})),
                    )

                async def observe_queue(index: int, session_id: str) -> None:
                    if message_responses[index].status_code != 202:
                        return
                    started = time.monotonic()
                    deadline = started + 5
                    while time.monotonic() < deadline:
                        response = await http.get(
                            f"/api/v12/sessions/{session_id}", headers=headers(index)
                        )
                        payload = response.json() if response.status_code == 200 else {}
                        steps = payload.get("agentSteps", payload.get("agent_steps", []))
                        if isinstance(steps, list) and steps:
                            queue_waits.append(time.monotonic() - started)
                            return
                        await asyncio.sleep(0.02)

                await asyncio.gather(
                    *(observe_queue(index, sessions[index]) for index in range(min(18, len(sessions))))
                )

                async def status(index: int, session_id: str) -> httpx.Response:
                    return await http.get(f"/api/v12/sessions/{session_id}", headers=headers(index))

                status_responses = await asyncio.gather(
                    *(status(index, sessions[index]) for index in range(18, min(20, len(sessions))))
                )
                if any(response.status_code != 200 for response in status_responses):
                    probes.fail(
                        "PROBE_HTTP_LOAD_STATUS_READ",
                        phase="http_load",
                        operation="get_session",
                        detail=str(sorted({response.status_code for response in status_responses})),
                    )
            stats = latency_stats(message_latencies)
            return {
                "status": "PASS" if not probes.failures else "FAILED",
                "users": len(sessions),
                "messages": len(message_responses),
                "statusReads": len(status_responses),
                "messageAcceptanceSeconds": stats,
                "queueWaitSeconds": latency_stats(queue_waits),
                "sessionCreationSeconds": latency_stats(create_latencies),
                "transportCalls": transport.transport_summary(),
                "criteria": {
                    "twentyUsers": len(sessions) == 20,
                    "eighteenMessagesHandled": len(message_responses) == 18 and not unexpected,
                    "capacityIsExplicit": any(
                        response.status_code == 429 and response.headers.get("Retry-After")
                        for response in message_responses
                    ) or all(response.status_code == 202 for response in message_responses),
                    "twoStatusReads": len(status_responses) == 2 and all(
                        response.status_code == 200 for response in status_responses
                    ),
                    "acceptP95Under3s": stats["p95"] <= 3.0,
                    "queueObservationComplete": len(queue_waits) == sum(
                        response.status_code == 202 for response in message_responses
                    ),
                },
            }
        finally:
            await application.close()
            await client.aclose()
