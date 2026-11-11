from __future__ import annotations

from contextlib import asynccontextmanager
import sqlite3

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from uuid import uuid4
from platform_protocol_contracts import (
    CounterSample,
    GaugeSample,
    enforce_problem_media_type,
    install_http_metrics,
    install_http_tracing,
)

from .composition import ExecutorApplication
from .errors import ExecutionServiceError, ExecutorUnavailableError, JobNotFoundError
from .problems import (
    PROBLEM_RESPONSE,
    http_problem,
    problem_response,
    request_validation_problem,
)
from .routers import (
    register_execution_routes,
    register_system_routes,
    register_worker_routes,
)
from .database import is_sqlite_busy_error, runtime_metrics


def create_app(application: ExecutorApplication) -> FastAPI:
    product_id = getattr(application, "product_id", "platform")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await application.runtime.start()
        try:
            yield
        finally:
            await application.runtime.close()

    app = FastAPI(
        title="Platform Executor Service",
        version="6",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
        responses={status: PROBLEM_RESPONSE for status in (404, 409, 422, 500, 503)},
    )
    app.state.product_id = product_id

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.trace_id = getattr(request.state, "trace_id", None) or (
            request.headers.get("X-Trace-ID") or uuid4().hex
        )
        request.state.request_id = request.headers.get("X-Request-ID") or uuid4().hex
        response = await call_next(request)
        response.headers["X-Trace-ID"] = request.state.trace_id
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    app.add_exception_handler(RequestValidationError, request_validation_problem)
    app.add_exception_handler(StarletteHTTPException, http_problem)

    @app.exception_handler(JobNotFoundError)
    async def not_found(request: Request, error: JobNotFoundError):
        return problem_response(
            "job-not-found",
            "Job not found",
            404,
            "The requested Job was not found.",
            str(error),
            request_id=request.state.request_id,
            trace_id=request.state.trace_id,
            product_id=product_id,
        )

    @app.exception_handler(ExecutionServiceError)
    async def service_error(request: Request, error: ExecutionServiceError):
        status = 409 if str(error) in {"LEASE_STALE", "JOB_REQUEST_ID_CONFLICT", "JOB_PUBLICATION_COMMITTING"} else 422
        return problem_response(
            "execution-request-invalid",
            "Execution request invalid",
            status,
            "The execution request does not match the contract.",
            str(error),
            request_id=request.state.request_id,
            trace_id=request.state.trace_id,
            product_id=product_id,
        )

    @app.exception_handler(ExecutorUnavailableError)
    async def unavailable(request: Request, error: ExecutorUnavailableError):
        return problem_response(
            "executor-unavailable",
            "Executor unavailable",
            503,
            "The Executor worker or local store is unavailable.",
            str(error),
            request_id=request.state.request_id,
            trace_id=request.state.trace_id,
            product_id=product_id,
        )

    @app.exception_handler(sqlite3.OperationalError)
    async def database_busy(request: Request, error: sqlite3.OperationalError):
        if not is_sqlite_busy_error(error):
            raise error
        return await unavailable(request, ExecutorUnavailableError("EXECUTOR_DATABASE_BUSY"))

    register_system_routes(app, application)
    register_execution_routes(app, application)
    register_worker_routes(app, application)

    def metric_samples():
        snapshot = runtime_metrics(application.database)
        recovery = application.control.recovery.metrics()
        samples = []
        for queue in ("jobs", "nodes"):
            for state, count in snapshot[queue].items():
                samples.append(GaugeSample(
                    "platform_queue_items", float(count),
                    {"service": "executor", "queue": queue, "state": state},
                    "Persisted queue items by service, queue and state.",
                ))
        samples.append(GaugeSample(
            "platform_queue_oldest_seconds", float(snapshot["oldest"]),
            {"service": "executor", "queue": "jobs"},
            "Age of the oldest queued item.",
        ))
        samples.append(CounterSample(
            "platform_sqlite_busy_total", snapshot["sqlite_busy"],
            {"service": "executor"}, "SQLite busy or locked failures.",
        ))
        samples.append(CounterSample(
            "platform_queue_retries_total", snapshot["retries"],
            {"service": "executor", "queue": "nodes"},
            "Persisted node attempts after the first claim, including lease recovery.",
        ))
        for state, count in (
            ("expired_active", snapshot["expired_leases"]),
            ("lost_retained", snapshot["lost_leases"]),
        ):
            samples.append(GaugeSample(
                "platform_executor_lease_attempts", float(count),
                {"service": "executor", "state": state},
                "Expired active or retained lost lease attempts.",
            ))
        samples.append(CounterSample(
            "platform_executor_lease_lost_total", recovery["lease_lost"],
            {"service": "executor"},
            "Lease attempts durably marked lost by this control process.",
        ))
        for kind, count in (
            ("attempt_workspace", recovery["attempt_workspace_removed"]),
            ("orphan_object", recovery["orphan_object_removed"]),
        ):
            samples.append(CounterSample(
                "platform_executor_gc_removed_total", count,
                {"service": "executor", "kind": kind},
                "Filesystem entries removed by recovery in this control process.",
            ))
        for runtime, slots in snapshot["slots"].items():
            samples.append(GaugeSample(
                "platform_worker_slots", float(slots),
                {"service": "executor", "runtime": runtime, "state": "total"},
                "Worker slots by runtime and state.",
            ))
            samples.append(GaugeSample(
                "platform_worker_slots", float(snapshot["active"].get(runtime, 0)),
                {"service": "executor", "runtime": runtime, "state": "active"},
                "Worker slots by runtime and state.",
            ))
        for state, count in snapshot["remote_statuses"].items():
            samples.append(GaugeSample(
                "platform_executor_remote_tasks",
                float(count),
                {"service": "executor", "state": state},
                "Persisted remote task evidence by remote state.",
            ))
        samples.append(GaugeSample(
            "platform_executor_remote_termination_unconfirmed",
            float(snapshot["remote_unconfirmed_terminations"]),
            {"service": "executor"},
            "Terminal remote nodes without confirmed remote termination.",
        ))
        for code, count in snapshot["remote_failures"].items():
            samples.append(GaugeSample(
                "platform_executor_remote_failures",
                float(count),
                {"service": "executor", "code": code},
                "Persisted failed remote nodes by stable error code.",
            ))
        return tuple(samples)

    install_http_metrics(app, service="executor", gauges=metric_samples)
    install_http_tracing(app, service="executor")

    original_openapi = app.openapi
    app.openapi = lambda: enforce_problem_media_type(original_openapi())
    return app
