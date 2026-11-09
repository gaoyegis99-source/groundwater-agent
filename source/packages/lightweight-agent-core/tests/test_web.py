from __future__ import annotations

import time

import anyio
import httpx
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient
from platform_lightweight_agent import (
    HostRuntime,
    InMemoryRunJournal,
    LocalToolExecutor,
    LocalWebSecurity,
    NodeContract,
    RuntimeAssembly,
    RunRetentionPolicy,
    SQLiteRunJournal,
    ToolExecutionUncertainError,
    ToolReference,
    ValueBinding,
    WorkflowEngine,
    WorkflowSpec,
    WorkflowValidationError,
    approval_step,
    create_web_app,
    serve_web_app,
    tool_step,
    workflow,
)
from platform_lightweight_agent.journal import RunNotFoundError

TOOL = ToolReference("web.echo", "1", "sha256:" + "7" * 64)
_SESSION_TOKEN = "web-test-session-token"


def _app(engine, *, workflow=None):  # type: ignore[no-untyped-def]
    return create_web_app(
        engine,
        workflow=workflow,
        security=LocalWebSecurity(
            allowed_hosts=frozenset({"testserver"}),
            session_token=_SESSION_TOKEN,
        ),
    )


def _secured_client(engine, *, workflow=None) -> TestClient:  # type: ignore[no-untyped-def]
    return TestClient(
        _app(engine, workflow=workflow), headers={"X-V6-Local-Session": _SESSION_TOKEN}
    )


def _client() -> TestClient:
    executor = LocalToolExecutor()
    executor.register(TOOL, lambda arguments: {"echo": arguments})
    return _secured_client(WorkflowEngine(InMemoryRunJournal(), executor, {}))


def _approval_workflow() -> dict[str, object]:
    return workflow(
        "web-approval",
        "1",
        [approval_step("review", "approve?")],
        schema_version="v6-light-workflow-v1",
    ).to_mapping()


def _v3_approval_workflow() -> dict[str, object]:
    empty = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    approved = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"approved": {"type": "boolean"}},
        "required": ["approved"],
        "additionalProperties": False,
    }
    return workflow(
        "web-v3-approval",
        "1",
        [
            approval_step(
                "review",
                "approve?",
                contract=NodeContract(empty, approved, {}),
            )
        ],
        schema_version="v6-light-workflow-v3",
        input_schema=empty,
        output_schema=approved,
        output_bindings={
            "/approved": ValueBinding("step_output", "/approved", "review")
        },
    ).to_mapping()


def test_web_inspects_events_and_controls_the_same_engine_run() -> None:
    client = _client()
    response = client.post(
        "/runs", json={"workflow": _approval_workflow(), "run_id": "web-run"}
    )
    assert response.status_code == 201
    assert response.json()["status"] == "WAITING_APPROVAL"

    events = client.get("/runs/web-run/events")
    assert events.status_code == 200
    assert events.json()[-1]["type"] == "APPROVAL_REQUESTED"

    complete = client.post("/runs/web-run/approval", json={"approved": True})
    assert complete.status_code == 200
    assert complete.json()["status"] == "SUCCEEDED"
    assert client.get("/runs/web-run").json()["revision"] == complete.json()["revision"]


def test_web_can_run_the_configured_project_workflow_without_resubmitting_it() -> None:
    engine = WorkflowEngine(InMemoryRunJournal(), LocalToolExecutor(), {})
    configured = workflow(
        "configured-web",
        "1",
        [approval_step("review", "approve?")],
        schema_version="v6-light-workflow-v1",
    )
    client = _secured_client(engine, workflow=configured)

    started = client.post("/v2/runs", json={"run_id": "configured-run"})

    assert started.status_code == 201
    assert started.json()["workflow"]["workflow_id"] == "configured-web"
    assert started.json()["status"] == "WAITING_APPROVAL"
    rejected = client.post(
        "/v2/runs",
        json={"workflow": _approval_workflow(), "run_id": "replacement"},
    )
    assert rejected.status_code == 422
    assert rejected.json() == {"code": "INVALID_REQUEST"}


def test_web_lists_compact_runs_and_serves_wheel_bundled_console() -> None:
    engine = WorkflowEngine(InMemoryRunJournal(), LocalToolExecutor(), {})
    engine.run(
        workflow(
            "console-flow",
            "1",
            [approval_step("review", "approve?")],
            schema_version="v6-light-workflow-v1",
        ),
        {},
        run_id="console-run",
    )
    client = _secured_client(engine)

    listed = client.get("/v2/runs?pending_only=true")
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert item["run_id"] == "console-run"
    assert item["pending_approval_count"] == 1
    assert "state" not in item

    invalid = client.get("/v2/runs?cursor=not-a-cursor")
    assert invalid.status_code == 422
    assert invalid.json() == {"code": "RUN_QUERY_CURSOR_INVALID"}

    console = client.get("/console")
    assert console.status_code == 200
    assert "V6 local run console" in console.text
    assert _SESSION_TOKEN not in console.text
    assert "script-src 'self'" in console.headers["content-security-policy"]
    script = client.get("/console/assets/app.js")
    assert script.status_code == 200
    assert "localStorage" not in script.text
    assert "innerHTML" not in script.text
    assert "STEP_STARTED" in script.text
    assert "WORKFLOW_OUTPUT_ACCEPTED" in script.text
    assert 'id="business-status"' in console.text
    assert 'id="result-files"' in console.text


def test_web_reports_an_optional_run_query_as_unsupported() -> None:
    class NoQueryJournal(InMemoryRunJournal):
        list_runs = None  # type: ignore[assignment]

    client = _secured_client(WorkflowEngine(NoQueryJournal(), LocalToolExecutor(), {}))
    response = client.get("/v2/runs")
    assert response.status_code == 501
    assert response.json() == {"code": "RUN_QUERY_UNSUPPORTED"}


@pytest.mark.parametrize("storage", ["memory", "sqlite"])
@pytest.mark.parametrize(
    "business,status",
    [("APPROVED", "SUCCEEDED"), ("BLOCKED", "SUCCEEDED"), (None, "BLOCKED")],
)
def test_run_view_is_one_revision_and_separates_business_from_execution(
    tmp_path, monkeypatch, storage, business, status
) -> None:
    from platform_lightweight_agent import RunSnapshot

    journal = (
        InMemoryRunJournal()
        if storage == "memory"
        else SQLiteRunJournal(tmp_path / "view.sqlite")
    )
    definition = _v3_approval_workflow()
    definition["output_schema"] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "business_status": {"enum": ["APPROVED", "BLOCKED"]},
            "reason_code": {"type": ["string", "null"]},
        },
        "required": ["business_status", "reason_code"],
        "additionalProperties": False,
    }
    output = (
        {
            "business_status": business,
            "reason_code": "INVALID_CSV" if business == "BLOCKED" else None,
        }
        if business
        else None
    )
    journal.create(
        RunSnapshot(
            run_id="view-run",
            execution_id="view-execution",
            revision=7,
            workflow=definition,
            inputs={},
            driver_bindings={},
            status=status,
            state={
                "completed": ["review"],
                "v3": {
                    "step_states": {"review": "COMPLETED"},
                    "output": output,
                    "step_outputs": {"large": {"data": "x" * 100_000}},
                },
                "failure": {"code": "TOOL_FAILED", "step_id": "calculate"}
                if not business
                else None,
            },
        ),
        "SEEDED",
        {},
    )
    monkeypatch.setattr(
        journal,
        "get",
        lambda *_args, **_kwargs: pytest.fail("view hydrated the entire snapshot"),
    )
    client = _secured_client(WorkflowEngine(journal, LocalToolExecutor(), {}))
    response = client.get("/v2/runs/view-run/view?expected_execution_id=view-execution")
    assert response.status_code == 200
    view = response.json()
    assert view["revision"] == view["explanation"]["revision"] == 7
    assert view["execution_status"] == status
    assert view["output"] == output
    assert view["business_result"] == (
        {"status": business, "reason_code": output["reason_code"]} if business else None
    )
    assert "x" * 100 not in response.text
    assert {"step_id": "review", "status": "COMPLETED"} in view["progress"]
    assert (
        client.get("/v2/runs/view-run/view?expected_execution_id=stale").status_code
        == 409
    )
    assert "output" not in client.get("/v2/runs/view-run/explanation").json()


def test_run_view_does_not_infer_a_generic_business_outcome() -> None:
    engine = WorkflowEngine(InMemoryRunJournal(), LocalToolExecutor(), {})
    engine.run(
        workflow(
            "generic",
            "1",
            [approval_step("review", "approve?")],
            schema_version="v6-light-workflow-v1",
        ),
        {},
        run_id="generic",
    )
    view = _secured_client(engine).get("/v2/runs/generic/view").json()
    assert view["business_result"] is None
    assert view["execution_status"] == "WAITING_APPROVAL"
    assert view["explanation"]["pending_approvals"]


def test_web_can_share_a_host_runtime_assembly() -> None:
    host = HostRuntime(RuntimeAssembly(InMemoryRunJournal(), LocalToolExecutor()))
    try:
        client = _secured_client(host)
        response = client.post(
            "/runs", json={"workflow": _v3_approval_workflow(), "run_id": "host-web"}
        )

        assert response.status_code == 201
        approval_id = next(iter(response.json()["state"]["v3"]["pending_approvals"]))
        complete = client.post(
            "/runs/host-web/approval",
            json={"approval_id": approval_id, "approved": True},
        )
        assert complete.status_code == 200
        # Host-backed v3 controls acknowledge the durable decision immediately;
        # the independent execution plane consumes it asynchronously.
        assert complete.json()["status"] == "RUNNING"
        for _ in range(50):
            if host.get("host-web").status == "SUCCEEDED":
                break
            time.sleep(0.01)
        else:
            raise AssertionError("host continuation did not consume approval")
    finally:
        host.close(mode="wait")


def test_host_web_invalid_control_does_not_cancel_or_advance_a_run() -> None:
    host = HostRuntime(RuntimeAssembly(InMemoryRunJournal(), LocalToolExecutor()))
    try:
        client = _secured_client(host)
        started = client.post(
            "/runs", json={"workflow": _v3_approval_workflow(), "run_id": "web-bad"}
        )
        assert started.status_code == 201
        rejected = client.post(
            "/runs/web-bad/approval",
            json={"approval_id": "approval-not-pending", "approved": True},
        )
        assert rejected.status_code == 422
        persisted = host.get("web-bad")
    finally:
        host.close(mode="wait")

    assert persisted.status == "WAITING_APPROVAL"
    assert persisted.revision == started.json()["revision"]
    assert persisted.state["v3"]["cancel_requested"] is False


def test_web_reconciles_without_echoing_sensitive_request_values() -> None:
    executor = LocalToolExecutor()

    def uncertain(arguments: dict) -> dict:
        del arguments
        raise ToolExecutionUncertainError("remote receipt lost")

    executor.register(TOOL, uncertain)
    client = _secured_client(WorkflowEngine(InMemoryRunJournal(), executor, {}))
    definition = workflow(
        "web-reconcile",
        "1",
        [tool_step("write", TOOL, {}, approval_required=False)],
        schema_version="v6-light-workflow-v1",
    ).to_mapping()
    started = client.post(
        "/runs", json={"workflow": definition, "run_id": "web-reconcile"}
    )
    assert started.status_code == 201
    assert started.json()["status"] == "RECONCILING"
    action_id = next(iter(started.json()["state"]["actions"]))

    resolved = client.post(
        f"/runs/web-reconcile/actions/{action_id}/reconcile",
        json={"succeeded": True, "output": {"confirmed": True}},
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "SUCCEEDED"

    rejected = client.post(
        "/runs",
        json={"workflow": _approval_workflow(), "inputs": {"api_key": "must-not-echo"}},
    )
    assert rejected.status_code == 422
    assert rejected.json() == {"code": "WORKFLOW_REJECTED"}
    assert "must-not-echo" not in rejected.text


def test_local_web_server_refuses_a_public_bind() -> None:
    with pytest.raises(WorkflowValidationError, match="localhost"):
        serve_web_app(
            WorkflowEngine(InMemoryRunJournal(), LocalToolExecutor(), {}),
            host="0.0.0.0",
        )


def test_local_web_rejects_host_origin_and_missing_control_session() -> None:
    engine = WorkflowEngine(InMemoryRunJournal(), LocalToolExecutor(), {})
    app = _app(engine)
    untrusted = TestClient(app)

    missing_session = untrusted.post(
        "/runs", json={"workflow": _approval_workflow(), "run_id": "no-session"}
    )
    assert missing_session.status_code == 403
    assert missing_session.json() == {"code": "LOCAL_SESSION_REQUIRED"}
    with pytest.raises(RunNotFoundError):
        engine.get("no-session")

    bad_host = untrusted.get("/healthz", headers={"Host": "attacker.invalid"})
    assert bad_host.status_code == 400
    assert bad_host.json() == {"code": "LOCAL_HOST_REJECTED"}

    bad_origin = untrusted.get(
        "/healthz", headers={"Origin": "https://attacker.invalid"}
    )
    assert bad_origin.status_code == 403
    assert bad_origin.json() == {"code": "LOCAL_ORIGIN_REJECTED"}

    trusted = _secured_client(engine)
    assert (
        trusted.get("/healthz", headers={"Origin": "http://testserver"}).status_code
        == 200
    )


def test_local_web_server_requires_an_explicit_control_session_token() -> None:
    with pytest.raises(WorkflowValidationError, match="session token"):
        serve_web_app(WorkflowEngine(InMemoryRunJournal(), LocalToolExecutor(), {}))


def test_v3_web_approval_requires_id_and_cancel_is_accepted() -> None:
    client = _client()
    started = client.post(
        "/runs", json={"workflow": _v3_approval_workflow(), "run_id": "web-v3"}
    )
    assert started.status_code == 201
    approval_id = next(iter(started.json()["state"]["v3"]["pending_approvals"]))

    missing = client.post("/runs/web-v3/approval", json={"approved": True})
    assert missing.status_code == 422
    complete = client.post(
        "/runs/web-v3/approval",
        json={"approval_id": approval_id, "approved": True},
    )
    assert complete.status_code == 200
    assert complete.json()["status"] == "SUCCEEDED"

    waiting = client.post(
        "/runs", json={"workflow": _v3_approval_workflow(), "run_id": "web-cancel"}
    )
    assert waiting.json()["status"] == "WAITING_APPROVAL"
    cancelled = client.post("/runs/web-cancel/cancel", json={})
    assert cancelled.status_code == 202
    assert cancelled.json()["status"] == "CANCEL_REQUESTED"


def test_web_rejects_oversized_requests_and_bounds_full_detail() -> None:
    engine = WorkflowEngine(InMemoryRunJournal(), LocalToolExecutor(), {})
    limited = create_web_app(
        engine,
        security=LocalWebSecurity(
            allowed_hosts=frozenset({"testserver"}),
            session_token=_SESSION_TOKEN,
        ),
        max_request_bytes=128,
        max_detail_response_bytes=256,
    )
    client = TestClient(limited, headers={"X-V6-Local-Session": _SESSION_TOKEN})
    rejected = client.post(
        "/runs",
        json={"workflow": _approval_workflow(), "inputs": {"blob": "x" * 512}},
    )
    assert rejected.status_code == 413
    assert rejected.json() == {"code": "REQUEST_TOO_LARGE"}

    engine.run(
        workflow(
            "large-detail",
            "1",
            [approval_step("review", "approve?")],
            schema_version="v6-light-workflow-v1",
        ),
        {"blob": "x" * 512},
        run_id="large-detail",
    )
    detail = client.get("/runs/large-detail/detail")
    assert detail.status_code == 413
    assert detail.json()["code"] == "RUN_DETAIL_TOO_LARGE"
    assert client.get("/runs/large-detail/summary").status_code == 200
    compact = client.get("/v2/runs/large-detail")
    assert compact.status_code == 200
    assert compact.json()["schema_version"] == "v6-run-explanation-v1"

    control_app = create_web_app(
        engine,
        security=LocalWebSecurity(
            allowed_hosts=frozenset({"testserver"}),
            session_token=_SESSION_TOKEN,
        ),
        max_request_bytes=8_192,
        max_detail_response_bytes=256,
    )
    control_client = TestClient(
        control_app, headers={"X-V6-Local-Session": _SESSION_TOKEN}
    )
    started = control_client.post(
        "/runs",
        json={
            "workflow": _approval_workflow(),
            "run_id": "large-control",
            "inputs": {"blob": "x" * 512},
        },
    )
    assert started.status_code == 201
    assert started.json()["code"] == "RUN_DETAIL_TOO_LARGE"
    assert started.json()["status"] == "WAITING_APPROVAL"


def test_sse_uses_instance_bound_last_event_id_and_terminal_204() -> None:
    engine = WorkflowEngine(InMemoryRunJournal(), LocalToolExecutor(), {})
    client = _secured_client(engine)
    started = client.post(
        "/runs", json={"workflow": _approval_workflow(), "run_id": "sse-finished"}
    )
    assert started.status_code == 201
    assert (
        client.post("/runs/sse-finished/approval", json={"approved": True}).status_code
        == 200
    )
    events = engine.events("sse-finished")
    first = events[0].cursor.to_token()
    final = events[-1].cursor.to_token()

    replay = client.get(
        "/runs/sse-finished/events/stream",
        headers={"Last-Event-ID": first},
    )
    assert replay.status_code == 200
    assert f"id: {events[1].cursor.to_token()}" in replay.text
    assert "event: v6-terminal" in replay.text
    assert "event: RUN_STARTED" not in replay.text

    caught_up = client.get(
        "/runs/sse-finished/events/stream",
        headers={"Last-Event-ID": final},
    )
    assert caught_up.status_code == 204

    invalid = client.get(
        "/runs/sse-finished/events/stream",
        headers={"Last-Event-ID": "not-an-event-cursor"},
    )
    assert invalid.status_code == 422
    assert invalid.json() == {"code": "EVENT_CURSOR_INVALID"}

    conflicting = client.get(
        f"/runs/sse-finished/events/stream?cursor={final}",
        headers={"Last-Event-ID": first},
    )
    assert conflicting.status_code == 409
    assert conflicting.json() == {"code": "EVENT_CURSOR_CONFLICT"}

    missing = client.get("/runs/not-present/events/stream")
    assert missing.status_code == 404
    assert missing.json() == {"code": "RUN_NOT_FOUND"}


def test_web_rejects_old_execution_id_after_run_name_is_reused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    journal = SQLiteRunJournal(tmp_path / "web-reused.sqlite")
    engine = WorkflowEngine(journal, LocalToolExecutor(), {})
    client = _secured_client(engine)
    first = client.post(
        "/runs", json={"workflow": _approval_workflow(), "run_id": "web-reused"}
    )
    assert first.status_code == 201
    assert (
        client.post("/runs/web-reused/approval", json={"approved": True}).status_code
        == 200
    )
    old_execution_id = first.json()["execution_id"]
    old_cursor = engine.events("web-reused")[-1].cursor.to_token()
    journal.apply_retention(
        RunRetentionPolicy(archive_after_seconds=0, purge_after_seconds=0),
        now=time.time() + 60,
    )
    replacement = client.post(
        "/runs", json={"workflow": _approval_workflow(), "run_id": "web-reused"}
    )
    assert replacement.status_code == 201
    assert replacement.json()["execution_id"] != old_execution_id

    event_page = client.get(f"/runs/web-reused/events?cursor={old_cursor}")
    assert event_page.status_code == 409
    assert event_page.json() == {"code": "RUN_EXECUTION_MISMATCH"}

    stream = client.get(
        "/runs/web-reused/events/stream",
        headers={"Last-Event-ID": old_cursor},
    )
    assert stream.status_code == 409
    assert stream.json() == {"code": "RUN_EXECUTION_MISMATCH"}

    stale_cancel = client.post(
        "/runs/web-reused/cancel",
        json={"expected_execution_id": old_execution_id},
    )
    assert stale_cancel.status_code == 409
    assert stale_cancel.json() == {"code": "RUN_EXECUTION_MISMATCH"}
    assert engine.get("web-reused").status == "WAITING_APPROVAL"


@pytest.mark.anyio
async def test_sse_disconnect_releases_subscription_and_ping_is_not_journaled() -> None:
    engine = WorkflowEngine(InMemoryRunJournal(), LocalToolExecutor(), {})
    waiting = await engine.run_async(
        WorkflowSpec.from_mapping(_v3_approval_workflow()),
        {},
        run_id="sse-waiting",
    )
    assert waiting.status == "WAITING_APPROVAL"
    before = len(engine.events(waiting.run_id))
    app = create_web_app(
        engine,
        security=LocalWebSecurity(
            allowed_hosts=frozenset({"testserver"}), session_token=_SESSION_TOKEN
        ),
        sse_ping_seconds=0.01,
        sse_send_timeout_seconds=0.1,
    )
    disconnect = anyio.Event()
    request_seen = False
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        nonlocal request_seen
        if not request_seen:
            request_seen = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)
        body = message.get("body", b"")
        if isinstance(body, bytes) and b"ping" in body:
            disconnect.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/runs/sse-waiting/events/stream",
        "raw_path": b"/runs/sse-waiting/events/stream",
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
        "root_path": "",
    }
    with anyio.fail_after(2):
        await app(scope, receive, send)

    assert any(
        isinstance(message.get("body"), bytes) and b"ping" in message["body"]
        for message in sent
    )
    assert len(engine.events(waiting.run_id)) == before
    assert engine.get(waiting.run_id).status == "WAITING_APPROVAL"
    assert not engine._v3_event_watchers.get(waiting.run_id)


@pytest.mark.anyio
async def test_slow_sync_inspection_does_not_block_the_web_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = WorkflowEngine(InMemoryRunJournal(), LocalToolExecutor(), {})
    engine.run(
        workflow(
            "slow-read",
            "1",
            [approval_step("review", "approve?")],
            schema_version="v6-light-workflow-v1",
        ),
        {},
        run_id="slow-read",
    )
    original = engine.explain

    def slow_explain(run_id: str):  # type: ignore[no-untyped-def]
        time.sleep(0.2)
        return original(run_id)

    monkeypatch.setattr(engine, "explain", slow_explain)
    app = _app(engine)
    transport = httpx.ASGITransport(app=app)
    response: dict[str, httpx.Response] = {}
    started = time.perf_counter()
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"X-V6-Local-Session": _SESSION_TOKEN},
    ) as client:
        async with anyio.create_task_group() as group:

            async def read() -> None:
                response["value"] = await client.get("/runs/slow-read/explanation")

            group.start_soon(read)
            await anyio.sleep(0.03)
            elapsed_while_reading = time.perf_counter() - started

    assert elapsed_while_reading < 0.15
    assert response["value"].status_code == 200
