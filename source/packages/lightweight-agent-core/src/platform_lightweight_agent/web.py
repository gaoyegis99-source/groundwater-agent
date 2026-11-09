"""Optional localhost Web surface for inspecting and controlling V6 runs.

The module deliberately imports FastAPI and Uvicorn only when a caller asks to
create or serve the app.  The V6 core remains usable in an empty Python
environment, while an embedding program can add a small local debug/approval
surface over the same :class:`WorkflowEngine` instance used by its CLI or API.
"""

from __future__ import annotations

import hmac
import secrets
from functools import partial
from importlib import resources
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .engine import WorkflowEngine
from .host import HostRuntime
from .journal import (
    EventCursor,
    EventCursorError,
    RunConflictError,
    RunExecutionMismatchError,
    RunJournalError,
    RunListCursor,
    RunNotFoundError,
    RunQueryCursorError,
    RunQueryFilter,
    RunQueryUnsupportedError,
)
from .runtime_contracts import ToolExecutionResult, WorkflowRunError
from .spec import (
    ASYNC_SCHEMA_VERSIONS,
    ApprovalDecision,
    WorkflowSpec,
    WorkflowValidationError,
    canonical_json,
)

_LOCALHOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_CONTROL_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_SESSION_HEADER = "x-v6-local-session"
_DEFAULT_REQUEST_BYTES = 1 * 1024 * 1024
_DEFAULT_DETAIL_RESPONSE_BYTES = 4 * 1024 * 1024
_DEFAULT_SSE_PING_SECONDS = 15.0
_DEFAULT_SSE_SEND_TIMEOUT_SECONDS = 15.0
_TERMINAL_RUN_STATUSES = frozenset({"SUCCEEDED", "BLOCKED", "CANCELLED"})
_CONSOLE_ASSETS = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}


class _InvalidWebRequestError(WorkflowValidationError):
    """A malformed HTTP request whose body must not be echoed back."""


class _EventCursorConflictError(RunConflictError):
    """Two request cursor sources describe different event positions."""

    code = "EVENT_CURSOR_CONFLICT"

    def __init__(self) -> None:
        super().__init__(self.code)


@dataclass(frozen=True)
class LocalWebSecurity:
    """Small same-machine boundary for the optional local Web surface.

    A local listener is still susceptible to Host-header and browser-origin
    confusion.  The default accepts only loopback host names, accepts an
    ``Origin`` only when it names one of those hosts, and requires a
    process-local session token for every state-changing endpoint.  An
    embedding application can pass its own fixed token; if omitted,
    :func:`create_web_app` creates one and stores it only at
    ``app.state.v6_local_session_token`` for its trusted owner.
    """

    allowed_hosts: frozenset[str] = _LOCALHOSTS
    allowed_origins: frozenset[str] | None = None
    session_token: str | None = None

    def __post_init__(self) -> None:
        hosts = frozenset(_normalized_host(item) for item in self.allowed_hosts)
        if not hosts:
            raise WorkflowValidationError("local Web security requires allowed hosts")
        object.__setattr__(self, "allowed_hosts", hosts)
        if self.allowed_origins is not None:
            origins = frozenset(_normalized_origin(item) for item in self.allowed_origins)
            if any(urlsplit(item).hostname not in hosts for item in origins):
                raise WorkflowValidationError(
                    "local Web origins must use an allowed local host"
                )
            object.__setattr__(self, "allowed_origins", origins)
        if self.session_token is not None and (
            not isinstance(self.session_token, str)
            or not self.session_token
            or len(self.session_token.encode("utf-8")) > 1_024
        ):
            raise WorkflowValidationError("local Web session token is invalid")


def create_web_app(
    runtime: WorkflowEngine | HostRuntime,
    *,
    workflow: WorkflowSpec | None = None,
    security: LocalWebSecurity | None = None,
    max_request_bytes: int = _DEFAULT_REQUEST_BYTES,
    max_detail_response_bytes: int = _DEFAULT_DETAIL_RESPONSE_BYTES,
    sse_ping_seconds: float = _DEFAULT_SSE_PING_SECONDS,
    sse_send_timeout_seconds: float = _DEFAULT_SSE_SEND_TIMEOUT_SECONDS,
) -> Any:
    """Create an optional FastAPI app over one caller-owned V6 runtime.

    The routes expose the core's ``run``, ``resume``, ``cancel``, ``events``
    and manual reconciliation operations.  Pass a ``HostRuntime`` built from
    ``RuntimeAssembly`` when the local Web surface must share process-wide
    limits with other embedding entry points.  The routes do not install
    tools, accept credentials, or create a second persistence layer.
    A caller serving it locally should retain ``app.state.v6_local_session_token``
    and provide it in ``X-V6-Local-Session`` for control requests. Applications
    exposing the app beyond localhost must place it behind their own
    authenticated edge instead of weakening this local policy.
    """

    for name, value in (
        ("max_request_bytes", max_request_bytes),
        ("max_detail_response_bytes", max_detail_response_bytes),
    ):
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 1 <= value <= 64 * 1024 * 1024
        ):
            raise WorkflowValidationError(f"{name} must be between 1 byte and 64 MiB")
    for name, value in (
        ("sse_ping_seconds", sse_ping_seconds),
        ("sse_send_timeout_seconds", sse_send_timeout_seconds),
    ):
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not 0 < float(value) <= 300
        ):
            raise WorkflowValidationError(f"{name} must be between 0 and 300 seconds")
    try:
        from fastapi import FastAPI, Header, Query, Request
        from fastapi.exceptions import RequestValidationError
        from fastapi.responses import JSONResponse, Response
        from sse_starlette.sse import EventSourceResponse
    except ImportError as error:
        raise WorkflowValidationError(
            "Web support is optional; install platform-lightweight-agent-core[web]"
        ) from error

    app = FastAPI(
        title="V6 lightweight Agent runs",
        version="v6-light-web-v3",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    local_security = security or LocalWebSecurity()
    session_token = local_security.session_token or secrets.token_urlsafe(32)
    # This is intentionally an in-memory embedding value, never a journal
    # field, event payload, response body, or log value.
    app.state.v6_local_session_token = session_token

    def console_asset(name: str) -> Any:
        """Serve only wheel-bundled, fixed console assets.

        No user-controlled filesystem path reaches this route.  The console
        never embeds a session token, and its CSP forbids CDN/remote script
        loading so the local control boundary remains small and inspectable.
        """

        media_type = _CONSOLE_ASSETS.get(name)
        if media_type is None:
            return Response(status_code=404)
        try:
            content = (
                resources.files("platform_lightweight_agent")
                .joinpath("console", name)
                .read_bytes()
            )
        except (FileNotFoundError, ModuleNotFoundError):
            return Response(status_code=404)
        return Response(
            content=content,
            media_type=media_type,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": (
                    "default-src 'self'; connect-src 'self'; style-src 'self'; "
                    "script-src 'self'; base-uri 'none'; form-action 'self'; "
                    "frame-ancestors 'none'"
                ),
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.middleware("http")
    async def local_request_boundary(request: Any, call_next: Any) -> Any:
        host = _request_host(request.headers.get("host"))
        if host is None or host not in local_security.allowed_hosts:
            return JSONResponse(status_code=400, content={"code": "LOCAL_HOST_REJECTED"})
        origin = request.headers.get("origin")
        if origin is not None and not _origin_allowed(origin, local_security):
            return JSONResponse(
                status_code=403, content={"code": "LOCAL_ORIGIN_REJECTED"}
            )
        if request.method.upper() in _CONTROL_METHODS:
            supplied = request.headers.get(_SESSION_HEADER)
            if not isinstance(supplied, str) or not hmac.compare_digest(
                supplied, session_token
            ):
                return JSONResponse(
                    status_code=403, content={"code": "LOCAL_SESSION_REQUIRED"}
                )
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except ValueError:
                    return JSONResponse(
                        status_code=400, content={"code": "INVALID_REQUEST"}
                    )
                if declared_size > max_request_bytes:
                    return JSONResponse(
                        status_code=413, content={"code": "REQUEST_TOO_LARGE"}
                    )
            body = await request.body()
            if len(body) > max_request_bytes:
                return JSONResponse(
                    status_code=413, content={"code": "REQUEST_TOO_LARGE"}
                )
        return await call_next(request)

    async def run_sync(function: Any, *args: Any, **kwargs: Any) -> Any:
        import anyio

        return await anyio.to_thread.run_sync(partial(function, *args, **kwargs))

    async def run_for_execution(
        function: Any, run_id: str, expected_execution_id: str | None
    ) -> Any:
        """Call old embedding overrides without an unnecessary new keyword."""

        if expected_execution_id is None:
            return await run_sync(function, run_id)
        return await run_sync(
            function, run_id, expected_execution_id=expected_execution_id
        )

    def detail_response(mapping: Mapping[str, Any]) -> Any:
        serialized = canonical_json(mapping).encode("utf-8")
        if len(serialized) > max_detail_response_bytes:
            return JSONResponse(
                status_code=413,
                content={
                    "code": "RUN_DETAIL_TOO_LARGE",
                    "hint": "Use the summary, explanation, or paged events endpoint.",
                },
            )
        return JSONResponse(content=dict(mapping))

    def control_response(snapshot: Any, *, status_code: int = 200) -> Any:
        """Keep mutation acknowledgements bounded without hiding success."""

        mapping = snapshot.to_mapping()
        if len(canonical_json(mapping).encode("utf-8")) > max_detail_response_bytes:
            return JSONResponse(
                status_code=status_code,
                content={
                    "run_id": snapshot.run_id,
                    "execution_id": snapshot.execution_id,
                    "status": snapshot.status,
                    "revision": snapshot.revision,
                    "code": "RUN_DETAIL_TOO_LARGE",
                    "detail": "omitted; fetch the summary or a bounded detail view",
                },
            )
        return JSONResponse(status_code=status_code, content=mapping)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, __: RequestValidationError) -> JSONResponse:
        # FastAPI's default response can echo a rejected body.  Workflow
        # inputs are credential-free by contract, but keep this boundary safe
        # even for malformed requests that have not reached the kernel yet.
        return JSONResponse(status_code=422, content={"code": "INVALID_REQUEST"})

    @app.exception_handler(RunNotFoundError)
    async def run_not_found(_: Request, __: RunNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"code": "RUN_NOT_FOUND"})

    @app.exception_handler(RunConflictError)
    async def run_conflict(_: Request, __: RunConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"code": "RUN_CONFLICT"})

    @app.exception_handler(RunExecutionMismatchError)
    async def run_execution_mismatch(
        _: Request, __: RunExecutionMismatchError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"code": "RUN_EXECUTION_MISMATCH"})

    @app.exception_handler(_EventCursorConflictError)
    async def event_cursor_conflict(
        _: Request, __: _EventCursorConflictError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"code": "EVENT_CURSOR_CONFLICT"})

    @app.exception_handler(EventCursorError)
    async def invalid_event_cursor(_: Request, __: EventCursorError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"code": "EVENT_CURSOR_INVALID"})

    @app.exception_handler(RunQueryCursorError)
    async def invalid_run_query_cursor(
        _: Request, __: RunQueryCursorError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"code": "RUN_QUERY_CURSOR_INVALID"})

    @app.exception_handler(RunQueryUnsupportedError)
    async def unsupported_run_query(
        _: Request, __: RunQueryUnsupportedError
    ) -> JSONResponse:
        return JSONResponse(status_code=501, content={"code": "RUN_QUERY_UNSUPPORTED"})

    @app.exception_handler(RunJournalError)
    async def journal_unavailable(_: Request, __: RunJournalError) -> JSONResponse:
        return JSONResponse(
            status_code=503, content={"code": "RUN_JOURNAL_UNAVAILABLE"}
        )

    @app.exception_handler(WorkflowValidationError)
    @app.exception_handler(WorkflowRunError)
    async def rejected_workflow(_: Request, __: Exception) -> JSONResponse:
        return JSONResponse(status_code=422, content={"code": "WORKFLOW_REJECTED"})

    @app.exception_handler(_InvalidWebRequestError)
    async def invalid_payload(_: Request, __: _InvalidWebRequestError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"code": "INVALID_REQUEST"})

    @app.get("/healthz")
    @app.get("/v2/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok", "schema_version": "v6-light-web-v3"}

    @app.get("/console", include_in_schema=False)
    @app.get("/console/", include_in_schema=False)
    async def console_index() -> Any:
        return console_asset("index.html")

    @app.get("/console/assets/{asset_name}", include_in_schema=False)
    async def console_assets(asset_name: str) -> Any:
        return console_asset(asset_name)

    @app.post("/runs", status_code=201)
    @app.post("/v2/runs", status_code=201)
    async def start_run(payload: dict[str, Any]) -> Any:
        _only_fields(payload, {"workflow", "inputs", "run_id"})
        if workflow is not None:
            if "workflow" in payload:
                _reject()
            selected = workflow
        elif "workflow" in payload:
            selected = WorkflowSpec.from_mapping(payload["workflow"])
        else:
            _reject()
        inputs = payload.get("inputs", {})
        if not isinstance(inputs, Mapping):
            _reject()
        run_id = payload.get("run_id")
        if run_id is not None and not isinstance(run_id, str):
            _reject()
        snapshot = await runtime.run_async(selected, dict(inputs), run_id=run_id)
        return control_response(snapshot, status_code=201)

    @app.get("/v2/runs")
    async def list_runs(
        status: list[str] | None = Query(default=None),
        workflow_id: str | None = Query(default=None),
        pending_only: bool = Query(default=False),
        include_archived: bool = Query(default=False),
        cursor: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        query_cursor = RunListCursor.from_token(cursor) if cursor is not None else None
        page = await run_sync(
            runtime.list_runs,
            RunQueryFilter(
                statuses=tuple(status or ()),
                workflow_id=workflow_id,
                pending_only=pending_only,
                include_archived=include_archived,
            ),
            query_cursor,
            limit,
        )
        return page.to_mapping()

    @app.get("/runs/{run_id}")
    @app.get("/runs/{run_id}/detail")
    @app.get("/v2/runs/{run_id}/detail")
    async def run_snapshot(
        run_id: str, expected_execution_id: str | None = Query(default=None)
    ) -> Any:
        snapshot = await run_for_execution(
            runtime.get, run_id, expected_execution_id
        )
        return detail_response(snapshot.to_mapping())

    @app.get("/v2/runs/{run_id}/view")
    async def run_view(
        run_id: str, expected_execution_id: str | None = Query(default=None)
    ) -> Any:
        view = await run_for_execution(runtime.view, run_id, expected_execution_id)
        return detail_response(view.to_mapping())

    @app.get("/v2/runs/{run_id}")
    @app.get("/runs/{run_id}/summary")
    @app.get("/v2/runs/{run_id}/summary")
    async def run_summary(
        run_id: str, expected_execution_id: str | None = Query(default=None)
    ) -> dict[str, Any]:
        return (await run_for_execution(runtime.explain, run_id, expected_execution_id)).to_mapping()

    @app.get("/runs/{run_id}/explanation")
    @app.get("/v2/runs/{run_id}/explanation")
    async def run_explanation(
        run_id: str, expected_execution_id: str | None = Query(default=None)
    ) -> dict[str, Any]:
        return (await run_for_execution(runtime.explain, run_id, expected_execution_id)).to_mapping()

    @app.get("/runs/{run_id}/recovery-preview")
    @app.get("/v2/runs/{run_id}/recovery-preview")
    async def recovery_preview(
        run_id: str, expected_execution_id: str | None = Query(default=None)
    ) -> dict[str, Any]:
        return (
            await run_for_execution(
                runtime.preview_recovery, run_id, expected_execution_id
            )
        ).to_mapping()

    @app.get("/runs/{run_id}/events")
    @app.get("/v2/runs/{run_id}/events")
    async def run_events(
        run_id: str,
        after: int | None = Query(default=None, ge=0),
        cursor: str | None = Query(default=None),
        expected_execution_id: str | None = Query(default=None),
        limit: int = Query(default=256, ge=1, le=1000),
    ) -> list[dict[str, Any]]:
        event_cursor = _query_event_cursor(
            cursor=cursor,
            after=after,
            expected_execution_id=expected_execution_id,
        )
        return [
            event.to_mapping()
            for event in await run_sync(
                runtime.events,
                run_id,
                after=0 if after is None else after,
                limit=limit,
                expected_execution_id=(
                    event_cursor.execution_id
                    if event_cursor is not None
                    else expected_execution_id
                ),
                cursor=event_cursor,
            )
        ]

    @app.get("/runs/{run_id}/events/stream")
    @app.get("/v2/runs/{run_id}/events/stream")
    async def stream_events(
        run_id: str,
        after: int | None = Query(default=None, ge=0),
        cursor: str | None = Query(default=None),
        expected_execution_id: str | None = Query(default=None),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> Any:
        event_cursor = _sse_event_cursor(
            last_event_id=last_event_id,
            query_cursor=cursor,
            after=after,
            expected_execution_id=expected_execution_id,
        )
        status = await run_for_execution(
            runtime.status,
            run_id,
            (
                event_cursor.execution_id
                if event_cursor is not None
                else expected_execution_id
            ),
        )
        if event_cursor is None:
            event_cursor = EventCursor(
                status.execution_id, 0 if after is None else after
            )
        if (
            status.status in _TERMINAL_RUN_STATUSES
            and event_cursor.sequence >= status.last_sequence
        ):
            return Response(status_code=204, headers={"Cache-Control": "no-store"})
        async def persisted_events():  # type: ignore[no-untyped-def]
            last_cursor = event_cursor
            try:
                async for event in runtime.stream_events(
                    run_id,
                    cursor=event_cursor,
                ):
                    last_cursor = event.cursor
                    yield {
                        "id": event.cursor.to_token(),
                        "event": event.type,
                        "data": canonical_json(event.to_mapping()),
                    }
                terminal = await run_sync(
                    runtime.status,
                    run_id,
                    expected_execution_id=event_cursor.execution_id,
                )
                if terminal.status in _TERMINAL_RUN_STATUSES:
                    yield {
                        "id": last_cursor.to_token(),
                        "event": "v6-terminal",
                        "data": canonical_json(
                            {
                                "run_id": run_id,
                                "execution_id": terminal.execution_id,
                                "status": terminal.status,
                                "cursor": last_cursor.to_mapping(),
                            }
                        ),
                    }
            except (RunNotFoundError, RunExecutionMismatchError, RunJournalError) as error:
                code = getattr(error, "code", None)
                yield {
                    "event": "v6-stream-error",
                    "data": canonical_json(
                        {
                            "code": (
                                code
                                if isinstance(code, str)
                                else "RUN_STREAM_INTERRUPTED"
                            )
                        }
                    ),
                }
                return
            except Exception:  # noqa: BLE001 - response headers are already live
                yield {
                    "event": "v6-stream-error",
                    "data": canonical_json({"code": "RUN_STREAM_INTERRUPTED"}),
                }
                return
        return EventSourceResponse(
            persisted_events(),
            ping=float(sse_ping_seconds),
            send_timeout=float(sse_send_timeout_seconds),
        )

    @app.post("/runs/{run_id}/approval")
    @app.post("/v2/runs/{run_id}/approval")
    async def approve(run_id: str, payload: dict[str, Any]) -> Any:
        _only_fields(payload, {"approval_id", "approved", "expected_execution_id"})
        decision = payload.get("approved")
        if not isinstance(decision, bool):
            _reject()
        expected_execution_id = payload.get("expected_execution_id")
        if expected_execution_id is not None and not isinstance(
            expected_execution_id, str
        ):
            _reject()
        inspection = await run_for_execution(
            runtime.inspection, run_id, expected_execution_id
        )
        workflow = WorkflowSpec.from_mapping(inspection.workflow)
        if workflow.schema_version in ASYNC_SCHEMA_VERSIONS:
            approval_id = payload.get("approval_id")
            if not isinstance(approval_id, str):
                _reject()
            snapshot = await runtime.resume_async(
                run_id,
                approvals=(ApprovalDecision(approval_id, decision),),
                expected_execution_id=expected_execution_id,
            )
            return control_response(snapshot)
        if "approval_id" in payload:
            _reject()
        return control_response(
            await _resume_legacy(
                runtime,
                run_id,
                decision,
                expected_execution_id=expected_execution_id,
            )
        )

    @app.post("/runs/{run_id}/cancel")
    @app.post("/v2/runs/{run_id}/cancel")
    async def cancel(run_id: str, payload: dict[str, Any] | None = None) -> Any:
        body = {} if payload is None else payload
        _only_fields(body, {"reason", "expected_execution_id"})
        reason = body.get("reason", "cancelled from local web")
        if not isinstance(reason, str) or not reason:
            _reject()
        expected_execution_id = body.get("expected_execution_id")
        if expected_execution_id is not None and not isinstance(
            expected_execution_id, str
        ):
            _reject()
        inspection = await run_for_execution(
            runtime.inspection, run_id, expected_execution_id
        )
        workflow = WorkflowSpec.from_mapping(inspection.workflow)
        if workflow.schema_version in ASYNC_SCHEMA_VERSIONS:
            requested = await runtime.request_cancel_async(
                run_id,
                reason=reason,
                expected_execution_id=expected_execution_id,
            )
            return control_response(requested, status_code=202)
        return control_response(
            await run_sync(
                runtime.cancel,
                run_id,
                reason=reason,
                expected_execution_id=expected_execution_id,
            )
        )

    @app.post("/runs/{run_id}/actions/{action_id}/reconcile")
    @app.post("/v2/runs/{run_id}/actions/{action_id}/reconcile")
    async def reconcile(
        run_id: str, action_id: str, payload: dict[str, Any]
    ) -> Any:
        _only_fields(
            payload, {"succeeded", "output", "error", "expected_execution_id"}
        )
        expected_execution_id = payload.get("expected_execution_id")
        if expected_execution_id is not None and not isinstance(
            expected_execution_id, str
        ):
            _reject()
        result = ToolExecutionResult.from_mapping(
            {
                "action_id": action_id,
                **{
                    key: value
                    for key, value in payload.items()
                    if key != "expected_execution_id"
                },
            }
        )
        return control_response(
            await runtime.reconcile_async(
                run_id,
                action_id,
                result,
                expected_execution_id=expected_execution_id,
            )
        )

    return app


def serve_web_app(
    runtime: WorkflowEngine | HostRuntime,
    *,
    workflow: WorkflowSpec | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    session_token: str | None = None,
) -> None:
    """Run the optional app only on a loopback address by default.

    A public deployment requires an explicit embedding application with
    authentication and its own network policy; the lightweight CLI never turns
    a local developer journal into an unauthenticated network service.
    """

    if host not in _LOCALHOSTS:
        raise WorkflowValidationError("the V6 local Web server must bind to localhost")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65_535:
        raise WorkflowValidationError("the V6 local Web server port is invalid")
    if not isinstance(session_token, str) or not session_token:
        raise WorkflowValidationError(
            "the V6 local Web server requires an explicit session token"
        )
    try:
        import uvicorn
    except ImportError as error:
        raise WorkflowValidationError(
            "Web serving is optional; install platform-lightweight-agent-core[web]"
        ) from error
    uvicorn.run(
        create_web_app(
            runtime,
            workflow=workflow,
            security=LocalWebSecurity(session_token=session_token),
        ),
        host=host,
        port=port,
    )


def _only_fields(payload: Mapping[str, object], allowed: set[str]) -> None:
    if set(payload) - allowed:
        _reject()


def _reject() -> None:
    # The response is a fixed code rather than an echo of the submitted body.
    raise _InvalidWebRequestError("invalid local Web request")


def _validate_expected_execution_id(value: str | None) -> None:
    if value is not None:
        # EventCursor applies the same public execution-id bounds used by the
        # Journal without requiring a fake run name at the HTTP edge.
        EventCursor(value, 0)


def _query_event_cursor(
    *,
    cursor: str | None,
    after: int | None,
    expected_execution_id: str | None,
) -> EventCursor | None:
    _validate_expected_execution_id(expected_execution_id)
    if cursor is None:
        return None
    parsed = EventCursor.from_token(cursor)
    if after is not None:
        raise _EventCursorConflictError()
    if (
        expected_execution_id is not None
        and expected_execution_id != parsed.execution_id
    ):
        raise RunExecutionMismatchError()
    return parsed


def _sse_event_cursor(
    *,
    last_event_id: str | None,
    query_cursor: str | None,
    after: int | None,
    expected_execution_id: str | None,
) -> EventCursor | None:
    """Resolve reconnect state without treating a bare sequence as portable.

    ``Last-Event-ID`` is authoritative for a browser reconnect.  A matching
    query cursor is tolerated for proxy/client implementations that send both;
    a mismatch is rejected rather than silently choosing one position.
    """

    _validate_expected_execution_id(expected_execution_id)
    header_cursor = (
        EventCursor.from_token(last_event_id)
        if last_event_id is not None
        else None
    )
    query = (
        EventCursor.from_token(query_cursor)
        if query_cursor is not None
        else None
    )
    if header_cursor is not None and query is not None and header_cursor != query:
        raise _EventCursorConflictError()
    selected = header_cursor if header_cursor is not None else query
    if selected is not None:
        if after is not None and after != selected.sequence:
            raise _EventCursorConflictError()
        if (
            expected_execution_id is not None
            and expected_execution_id != selected.execution_id
        ):
            raise RunExecutionMismatchError()
    return selected


def _normalized_host(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise WorkflowValidationError("local Web host is invalid")
    candidate = value.strip().lower()
    if candidate != value.lower() or "://" in candidate or "/" in candidate:
        raise WorkflowValidationError("local Web host is invalid")
    # Configuration accepts a bare host only; ports belong in request headers
    # and do not change local-host authorization.
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    if candidate not in _LOCALHOSTS and not all(
        character.isalnum() or character in {"-", "."} for character in candidate
    ):
        raise WorkflowValidationError("local Web host is invalid")
    return candidate


def _request_host(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = urlsplit("//" + value)
    except ValueError:
        return None
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return None
    hostname = parsed.hostname
    if hostname is None:
        return None
    try:
        return _normalized_host(hostname)
    except WorkflowValidationError:
        return None


def _normalized_origin(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise WorkflowValidationError("local Web origin is invalid")
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise WorkflowValidationError("local Web origin is invalid") from error
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise WorkflowValidationError("local Web origin is invalid")
    host = _normalized_host(parsed.hostname)
    try:
        port = parsed.port
    except ValueError as error:
        raise WorkflowValidationError("local Web origin is invalid") from error
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc += f":{port}"
    return f"{parsed.scheme}://{netloc}"


def _origin_allowed(origin: str, security: LocalWebSecurity) -> bool:
    try:
        normalized = _normalized_origin(origin)
        parsed = urlsplit(normalized)
        host = parsed.hostname
    except (WorkflowValidationError, ValueError):
        return False
    if security.allowed_origins is not None:
        return normalized in security.allowed_origins
    return host in security.allowed_hosts


async def _resume_legacy(
    runtime: WorkflowEngine | HostRuntime,
    run_id: str,
    approved: bool,
    *,
    expected_execution_id: str | None = None,
) -> Any:
    try:
        import anyio
    except ImportError as error:
        raise WorkflowValidationError(
            "the asynchronous Web surface requires the workflow extra"
        ) from error
    return await anyio.to_thread.run_sync(
        lambda: runtime.resume(
            run_id,
            approved=approved,
            expected_execution_id=expected_execution_id,
        )
    )
