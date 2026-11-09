from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
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

from ..composition import PlatformApplication, install_trace_processors
from ..errors import (
    AuthenticationRequiredError,
    AuthorizationDeniedError,
    CapacityLimitError,
    AgentRunError,
    ConfirmationConflictError,
    KernelUnavailableError,
    ResourceNotFoundError,
    UpstreamServiceError,
)
from .problems import (
    authentication_problem,
    authorization_problem,
    capacity_problem,
    agent_run_problem,
    confirmation_problem,
    http_problem,
    kernel_unavailable_problem,
    request_validation_problem,
    resource_not_found_problem,
    upstream_problem,
)
from .dependencies import (
    require_agent_boundary,
    require_session_boundary,
    require_supplied_project,
    require_workflow_boundary,
)
from .routes import (
    audit_router,
    agent_runs_router,
    sessions_router,
    system_router,
    workflow_runs_router,
    auth_router,
)


_active_platform: PlatformApplication | None = None
LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _active_platform
    platform = app.state.platform
    if _active_platform is not None:
        raise RuntimeError("MULTIPLE_PLATFORM_APPLICATIONS_UNSUPPORTED")
    _active_platform = platform
    install_trace_processors([platform.trace_processor])
    try:
        LOGGER.info(
            "Kernel capacity configured shared_concurrency=%d workflow_dispatch_concurrency=%d",
            platform.settings.agent_concurrency,
            platform.settings.workflow_dispatch_concurrency,
        )
        await platform.start()
        yield
    finally:
        try:
            await platform.close()
        finally:
            install_trace_processors([])
            _active_platform = None


def create_app(
    root: Path | None = None,
    *,
    application: PlatformApplication | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Platform Kernel",
        version="12",
        docs_url=None,
        redoc_url=None,
        lifespan=_lifespan,
    )
    app.state.platform = application or PlatformApplication(root)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.trace_id = getattr(request.state, "trace_id", None) or (
            request.headers.get("X-Trace-ID") or uuid4().hex
        )
        request.state.request_id = request.headers.get("X-Request-ID") or uuid4().hex
        if request.url.path.startswith("/api/v11/"):
            response = JSONResponse(
                {
                    "type": "urn:platform:problem:API_VERSION_RETIRED",
                    "title": "API version retired",
                    "status": 410,
                    "detail": "Public API v11 has been replaced by v12.",
                    "code": "API_VERSION_RETIRED",
                },
                status_code=410,
                media_type="application/problem+json",
            )
        else:
            response = await call_next(request)
        response.headers["X-Trace-ID"] = request.state.trace_id
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    app.add_exception_handler(RequestValidationError, request_validation_problem)
    app.add_exception_handler(StarletteHTTPException, http_problem)
    app.add_exception_handler(ResourceNotFoundError, resource_not_found_problem)
    app.add_exception_handler(ConfirmationConflictError, confirmation_problem)
    app.add_exception_handler(UpstreamServiceError, upstream_problem)
    app.add_exception_handler(AgentRunError, agent_run_problem)
    app.add_exception_handler(KernelUnavailableError, kernel_unavailable_problem)
    app.add_exception_handler(AuthenticationRequiredError, authentication_problem)
    app.add_exception_handler(AuthorizationDeniedError, authorization_problem)
    app.add_exception_handler(CapacityLimitError, capacity_problem)
    project_boundary = [Depends(require_supplied_project)]
    app.include_router(auth_router)
    app.include_router(system_router, dependencies=project_boundary)
    app.include_router(audit_router, dependencies=project_boundary)
    app.include_router(
        sessions_router,
        dependencies=project_boundary + [Depends(require_session_boundary)],
    )
    app.include_router(
        agent_runs_router,
        dependencies=project_boundary + [Depends(require_agent_boundary)],
    )
    app.include_router(
        workflow_runs_router,
        dependencies=project_boundary + [Depends(require_workflow_boundary)],
    )

    def metric_samples():
        snapshot = app.state.platform.metric_snapshot()
        samples = []
        for queue in ("agents", "workflows"):
            for state, count in snapshot[queue].items():
                samples.append(
                    GaugeSample(
                        "platform_queue_items",
                        float(count),
                        {"service": "kernel", "queue": queue, "state": state},
                        "Persisted queue items by service, queue and state.",
                    )
                )
        for phase, count in snapshot["scheduled"].items():
            samples.append(
                GaugeSample(
                    "platform_kernel_tasks",
                    float(count),
                    {"phase": phase},
                    "In-process Kernel tasks by phase.",
                )
            )
        for phase, count in snapshot["scheduled_queued"].items():
            samples.append(
                GaugeSample(
                    "platform_kernel_queued_tasks",
                    float(count),
                    {"phase": phase},
                    "Kernel task factories waiting for admission by phase.",
                )
            )
        for phase, count in snapshot["scheduled_rejected_total"].items():
            samples.append(
                CounterSample(
                    "platform_kernel_task_admission_rejected_total",
                    count,
                    {"phase": phase},
                    "Kernel task admissions refused because the identity was already active or shutdown began.",
                )
            )
        samples.append(
            GaugeSample(
                "platform_kernel_agent_concurrency_limit",
                float(snapshot["agent_concurrency"]),
                {},
                "Shared Agent, Router, and workflow dispatch concurrency limit.",
            )
        )
        samples.append(
            GaugeSample(
                "platform_kernel_workflow_dispatch_concurrency_limit",
                float(snapshot["workflow_dispatch_concurrency"]),
                {},
                "Configured local workflow dispatch concurrency limit.",
            )
        )
        samples.append(
            GaugeSample(
                "platform_kernel_reconciliation_required",
                float(snapshot["reconciliation_required"]),
                {},
                "Workflow dispatches requiring operator reconciliation after an unknown external outcome.",
            )
        )
        for stage, latency in snapshot["providers"].items():
            samples.append(
                GaugeSample(
                    "platform_provider_latency_milliseconds",
                    float(latency),
                    {"stage": stage},
                    "Average persisted provider latency by stage.",
                )
            )
        samples.append(
            GaugeSample(
                "platform_provider_unknown_requests",
                snapshot["provider_unknown"]["count"],
                {},
                "Provider attempts whose external outcome is unknown after recovery.",
            )
        )
        samples.append(
            GaugeSample(
                "platform_provider_unknown_oldest_age_seconds",
                snapshot["provider_unknown"]["oldest_age_seconds"],
                {},
                "Age of the oldest Provider attempt with unknown external outcome.",
            )
        )
        for kind, value in snapshot["sse"].items():
            samples.append(
                CounterSample(
                    "platform_sse_connections_total",
                    value,
                    {"kind": kind},
                    "SSE connections and client reconnections.",
                )
            )
        samples.append(
            CounterSample(
                "platform_sqlite_busy_total",
                snapshot["sqlite_busy"],
                {"service": "kernel"},
                "SQLite busy or locked failures.",
            )
        )
        return tuple(samples)

    install_http_metrics(app, service="kernel", gauges=metric_samples)
    install_http_tracing(app, service="kernel")
    original_openapi = app.openapi
    app.openapi = lambda: enforce_problem_media_type(original_openapi())
    return app
