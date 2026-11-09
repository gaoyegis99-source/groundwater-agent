from contextlib import asynccontextmanager
import logging
import threading

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from platform_protocol_contracts import enforce_problem_media_type
from platform_protocol_contracts import (
    CounterSample,
    GaugeSample,
    install_http_metrics,
    install_http_tracing,
)

from ..adapters.locking import runtime_lock
from ..composition import Application
from ..errors import DataProblemError
from .boundary import install_boundary
from .problems import PROBLEM_RESPONSE, problem_response, validation_problem
from .routers import (
    register_catalog_routes,
    register_content_routes,
    register_preview_routes,
    register_mlops_routes,
    register_publication_routes,
    register_quality_routes,
    register_query_routes,
    register_semantic_routes,
    register_system_routes,
    register_tool_routes,
)


LOGGER = logging.getLogger(__name__)


def create_app(settings):
    service = Application(settings)
    upload_slots = threading.BoundedSemaphore(settings.upload_slots)

    @asynccontextmanager
    async def lifespan(_app):
        with runtime_lock(settings.root):
            LOGGER.info(
                "Data API pool configured min=%d max=%d wait_seconds=%d",
                1,
                10,
                3,
            )
            service.db.open()
            try:
                service.db.check()
                yield
            finally:
                service.db.close()

    app = FastAPI(
        title="Data Service", version="3.0.0", lifespan=lifespan,
        responses={status: PROBLEM_RESPONSE for status in (400, 401, 403, 404, 409, 413, 422, 429, 500, 503)},
    )
    app.state.service = service

    @app.exception_handler(DataProblemError)
    async def problem_handler(request, error):
        response = problem_response(
            error.code, error.status, "The Data request could not be completed.",
            trace_id=getattr(request.state, "trace_id", None),
            request_id=getattr(request.state, "request_id", None),
            product_id=settings.product,
        )
        if error.code in {"PROJECT_UPLOAD_LIMIT", "DATABASE_POOL_EXHAUSTED"}:
            response.headers["Retry-After"] = "5"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, error):
        return validation_problem(
            error,
            trace_id=getattr(request.state, "trace_id", None),
            request_id=getattr(request.state, "request_id", None),
            product_id=settings.product,
        )

    install_boundary(app, settings)
    register_system_routes(app, settings, service)
    register_catalog_routes(app, settings, service)
    register_tool_routes(app, settings, service)
    register_semantic_routes(app, settings, service)
    register_publication_routes(
        app, settings, service, upload_slots,
    )
    register_query_routes(app, settings, service)
    register_preview_routes(app, settings, service)
    register_mlops_routes(app, settings, service)
    register_quality_routes(app, settings, service)
    register_content_routes(app, settings, service)

    def metric_samples():
        states, oldest, retries, publications, outbox = service.db.operation_metrics(
            settings.project
        )
        samples = [
            GaugeSample(
                "platform_queue_items",
                float(count),
                {"service": "data", "queue": "operations", "state": state},
                "Persisted queue items by service, queue and state.",
            )
            for state, count in states.items()
        ]
        samples.append(GaugeSample(
            "platform_queue_oldest_seconds", oldest,
            {"service": "data", "queue": "operations"},
            "Age of the oldest queued item.",
        ))
        samples.append(CounterSample(
            "platform_queue_retries_total", retries,
            {"service": "data", "queue": "operations"},
            "Persisted operation claims after the first claim, including lease recovery.",
        ))
        samples.extend(
            GaugeSample(
                "platform_publications", float(count), {"state": state},
                "Data publications by stage.",
            )
            for state, count in publications.items()
        )
        samples.extend(
            GaugeSample(
                "platform_outbox_events",
                float(count),
                {"service": "data", "state": state},
                "Transactional outbox events by delivery state.",
            )
            for state, count in outbox.items()
        )
        samples.extend((
            GaugeSample(
                "platform_worker_slots", float(settings.worker_slots),
                {"service": "data", "runtime": "data", "state": "total"},
                "Worker slots by runtime and state.",
            ),
            GaugeSample(
                "platform_worker_slots", float(states.get("RUNNING", 0)),
                {"service": "data", "runtime": "data", "state": "active"},
                "Worker slots by runtime and state.",
            ),
        ))
        pool_stats = service.db.pool_stats()
        for key in ("pool_size", "pool_available", "requests_waiting"):
            value = pool_stats.get(key)
            if isinstance(value, int):
                samples.append(GaugeSample(
                    "platform_database_pool",
                    float(value),
                    {"service": "data", "measure": key},
                    "Database connection pool state.",
                ))
        wait_ms = pool_stats.get("requests_wait_ms")
        if isinstance(wait_ms, int):
            samples.append(CounterSample(
                "platform_database_pool_wait_milliseconds_total", wait_ms,
                {"service": "data"}, "Database pool connection wait time.",
            ))
        return tuple(samples)

    install_http_metrics(app, service="data", gauges=metric_samples)
    install_http_tracing(app, service="data")

    original_openapi = app.openapi
    app.openapi = lambda: enforce_problem_media_type(original_openapi())
    return app
