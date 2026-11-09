from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from threading import Lock
import time
from uuid import uuid4
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from platform_embedding_client import EmbeddingClient, UpstreamServiceError
from platform_protocol_contracts import (
    CounterSample,
    GaugeSample,
    decode_display_name_header,
    enforce_problem_media_type,
    install_http_metrics,
    install_http_tracing,
)

from .config import Settings
from .database import Database
from .errors import KnowledgeProblemError
from .problems import PROBLEM_RESPONSE, problem_response, validation_problem
from .retrieval import EvidenceService
from .routers import (
    register_document_routes,
    register_import_routes,
    register_retrieval_routes,
    register_system_routes,
    register_tool_routes,
)

PREFIX = "/knowledge/v3"
MAX_BODY = 16 * 1024 * 1024
LOGGER = logging.getLogger(__name__)


class ObservedEmbeddings:
    def __init__(self, delegate):
        self.delegate = delegate
        self._lock = Lock()
        self._calls = 0
        self._seconds = 0.0

    def embed(self, values):
        started = time.perf_counter()
        try:
            return self.delegate.embed(values)
        finally:
            with self._lock:
                self._calls += 1
                self._seconds += time.perf_counter() - started

    def snapshot(self) -> tuple[int, float]:
        with self._lock:
            average = 0.0 if self._calls == 0 else self._seconds / self._calls
            return self._calls, average

    def ready(self):
        return self.delegate.ready()

    def close(self):
        return self.delegate.close()


def create_app(settings=None, embeddings=None):
    settings = settings or Settings.from_env()
    db = Database(settings, pool_min=1, pool_max=10, pool_timeout=3)
    embeddings = ObservedEmbeddings(
        embeddings or EmbeddingClient(
            settings.embedding_url, health_path=settings.embedding_health_path
        )
    )
    service = EvidenceService(db, embeddings)

    @asynccontextmanager
    async def lifespan(_app):
        LOGGER.info(
            "Knowledge API pool configured min=%d max=%d wait_seconds=%d",
            1,
            10,
            3,
        )
        db.open()
        try:
            db.check()
            yield
        finally:
            db.close()
            embeddings.close()

    app = FastAPI(
        title="Independent Knowledge Service",
        version="2.0.0",
        lifespan=lifespan,
        responses={
            status: PROBLEM_RESPONSE
            for status in (400, 401, 403, 404, 409, 413, 422, 500, 502, 503)
        },
    )
    app.state.db = db

    @app.exception_handler(KnowledgeProblemError)
    async def problem_handler(request, error):
        response = problem_response(
            error.code,
            error.status,
            "The Knowledge request could not be completed.",
            trace_id=getattr(request.state, "trace_id", None),
            request_id=getattr(request.state, "request_id", None),
            product_id=settings.product,
        )
        if error.code == "DATABASE_POOL_EXHAUSTED":
            response.headers["Retry-After"] = "5"
        return response

    @app.exception_handler(UpstreamServiceError)
    async def embedding_error(request, error):
        return problem_response(
            str(error),
            503,
            "The embedding provider is unavailable.",
            trace_id=getattr(request.state, "trace_id", None),
            request_id=getattr(request.state, "request_id", None),
            product_id=settings.product,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return validation_problem(
            error,
            trace_id=getattr(request.state, "trace_id", None),
            request_id=getattr(request.state, "request_id", None),
            product_id=settings.product,
        )

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        request.state.trace_id = getattr(request.state, "trace_id", None) or (
            request.headers.get("X-Trace-ID") or uuid4().hex
        )
        request.state.request_id = request.headers.get("X-Request-ID") or uuid4().hex
        supplied_project = request.headers.get("X-Project-ID")
        if request.url.path.startswith(PREFIX):
            if settings.identity_mode == "gateway":
                subject = request.headers.get("X-Platform-Subject", "")
                display_name = decode_display_name_header(
                    request.headers.get("X-Platform-Display-Name", "")
                )
                roles = {
                    item.strip()
                    for item in request.headers.get("X-Platform-Roles", "").split(",")
                    if item.strip()
                }
                if (
                    not supplied_project
                    or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}", subject)
                    is None
                    or not display_name
                    or len(display_name.encode("utf-8")) > 160
                    or any(
                        ord(character) < 32 or ord(character) == 127
                        for character in display_name
                    )
                    or not roles
                    or not roles <= {"researcher", "admin", "viewer"}
                ):
                    return problem_response(
                        "AUTHENTICATION_REQUIRED",
                        401,
                        "A trusted user identity is required.",
                        trace_id=request.state.trace_id,
                        request_id=request.state.request_id,
                        product_id=settings.product,
                    )
            else:
                subject = settings.local_identity.subject
                display_name = settings.local_identity.display_name
                roles = set(settings.local_identity.roles)
            if (supplied_project or settings.project) != settings.project:
                return problem_response(
                    "PROJECT_MISMATCH",
                    403,
                    "The request project differs from the deployment project.",
                    trace_id=request.state.trace_id,
                    request_id=request.state.request_id,
                    product_id=settings.product,
                )
            if not roles.intersection(
                {"researcher", "admin", "viewer"}
            ):
                return problem_response(
                    "ROLE_FORBIDDEN",
                    403,
                    "The authenticated user is not allowed to access project knowledge.",
                    trace_id=request.state.trace_id,
                    request_id=request.state.request_id,
                    product_id=settings.product,
                )
            if (
                request.method not in {"GET", "HEAD", "OPTIONS"}
                and not (
                    request.method == "POST"
                    and request.url.path
                    in {
                        PREFIX + "/retrievals",
                        PREFIX + "/evidence/read",
                        PREFIX + "/evidence/validate",
                        PREFIX + "/lookup",
                    }
                )
                and roles == {"viewer"}
            ):
                return problem_response(
                    "ROLE_FORBIDDEN",
                    403,
                    "The authenticated auditor has read-only access.",
                    trace_id=request.state.trace_id,
                    request_id=request.state.request_id,
                    product_id=settings.product,
                )
            request.state.actor = {
                "subject": subject,
                "display_name": display_name,
                "roles": sorted(roles),
                "trace_id": request.state.trace_id,
                "request_id": request.state.request_id,
            }
        if request.method in {"POST", "PUT", "PATCH"}:
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BODY:
                    return problem_response(
                        "REQUEST_TOO_LARGE",
                        413,
                        "The request body exceeds the configured limit.",
                        trace_id=request.state.trace_id,
                        request_id=request.state.request_id,
                        product_id=settings.product,
                    )
                chunks.append(chunk)
            request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'"
        )
        response.headers["X-Trace-ID"] = request.state.trace_id
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    def writable():
        if settings.read_only:
            raise KnowledgeProblemError("READ_ONLY", 403)

    register_system_routes(app, settings, db, embeddings, max_body=MAX_BODY)
    register_import_routes(app, db, writable)
    register_document_routes(app, db, writable)
    register_retrieval_routes(app, db, service)
    register_tool_routes(app, settings, service)

    def metric_samples():
        states, oldest, retries = db.import_metrics()
        samples = [
            GaugeSample(
                "platform_queue_items", float(count),
                {"service": "knowledge", "queue": "imports", "state": state},
                "Persisted queue items by service, queue and state.",
            )
            for state, count in states.items()
        ]
        samples.append(GaugeSample(
            "platform_queue_oldest_seconds", oldest,
            {"service": "knowledge", "queue": "imports"},
            "Age of the oldest queued item.",
        ))
        samples.append(CounterSample(
            "platform_queue_retries_total", retries,
            {"service": "knowledge", "queue": "imports"},
            "Persisted import claims after the first claim, including lease recovery.",
        ))
        samples.extend((
            GaugeSample(
                "platform_worker_slots", 1,
                {"service": "knowledge", "runtime": "import", "state": "total"},
                "Worker slots by runtime and state.",
            ),
            GaugeSample(
                "platform_worker_slots",
                float(states.get("VALIDATING", 0) + states.get("INDEXING", 0)),
                {"service": "knowledge", "runtime": "import", "state": "active"},
                "Worker slots by runtime and state.",
            ),
        ))
        calls, average = embeddings.snapshot()
        samples.append(CounterSample(
            "platform_embedding_requests_total", calls, {},
            "Embedding requests made by this service process.",
        ))
        samples.append(GaugeSample(
            "platform_embedding_latency_seconds", average, {},
            "Average Embedding request latency in this service process.",
        ))
        pool_stats = db.pool_stats()
        for key in ("pool_size", "pool_available", "requests_waiting"):
            value = pool_stats.get(key)
            if isinstance(value, int):
                samples.append(GaugeSample(
                    "platform_database_pool", float(value),
                    {"service": "knowledge", "measure": key},
                    "Database connection pool state.",
                ))
        wait_ms = pool_stats.get("requests_wait_ms")
        if isinstance(wait_ms, int):
            samples.append(CounterSample(
                "platform_database_pool_wait_milliseconds_total", wait_ms,
                {"service": "knowledge"}, "Database pool connection wait time.",
            ))
        return tuple(samples)

    install_http_metrics(app, service="knowledge", gauges=metric_samples)
    install_http_tracing(app, service="knowledge")
    original_openapi = app.openapi
    app.openapi = lambda: enforce_problem_media_type(original_openapi())
    return app
