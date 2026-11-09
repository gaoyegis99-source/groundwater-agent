"""Small W3C Trace Context and fail-open OTLP/HTTP support.

The implementation deliberately records only a fixed HTTP attribute allow-list.
Request bodies, prompts, credentials, query strings, and path parameter values are
never placed in spans.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass
import json
import logging
import os
from queue import Empty, Full, Queue
import re
import secrets
from threading import Event, Lock, Thread
import time
from typing import Any
from urllib.parse import urlsplit

import httpx


_TRACEPARENT = re.compile(
    r"(?P<version>[0-9a-f]{2})-(?P<trace>[0-9a-f]{32})-"
    r"(?P<parent>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})\Z"
)
_HEX_TRACE = re.compile(r"[0-9a-f]{32}\Z")
_CURRENT: ContextVar["TraceContext | None"] = ContextVar(
    "platform_trace_context", default=None
)
_LOGGER = logging.getLogger("platform.telemetry")


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    trace_flags: str = "01"

    @property
    def traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-{self.trace_flags}"


def _legacy_trace_id(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.lower().removeprefix("trace_").replace("-", "")
    if _HEX_TRACE.fullmatch(candidate) and candidate != "0" * 32:
        return candidate
    return None


def parse_traceparent(value: str | None) -> tuple[str, str, str] | None:
    match = _TRACEPARENT.fullmatch((value or "").strip().lower())
    if (
        match is None
        or match["version"] == "ff"
        or match["trace"] == "0" * 32
        or match["parent"] == "0" * 16
    ):
        return None
    return match["trace"], match["parent"], match["flags"]


def server_trace_context(headers: Mapping[str, str]) -> TraceContext:
    parsed = parse_traceparent(headers.get("traceparent"))
    if parsed is not None:
        trace_id, parent_span_id, flags = parsed
    else:
        trace_id = _legacy_trace_id(headers.get("x-trace-id")) or secrets.token_hex(16)
        parent_span_id, flags = None, "01"
    return TraceContext(trace_id, secrets.token_hex(8), parent_span_id, flags)


def trace_context_for_id(trace_id: str) -> TraceContext:
    normalized = _legacy_trace_id(trace_id) or secrets.token_hex(16)
    return TraceContext(normalized, secrets.token_hex(8), None, "01")


def current_trace_context() -> TraceContext | None:
    return _CURRENT.get()


def activate_trace_context(context: TraceContext) -> Token:
    return _CURRENT.set(context)


def reset_trace_context(token: Token) -> None:
    _CURRENT.reset(token)


def inject_trace_headers(
    headers: Mapping[str, str] | None = None,
    *,
    context: TraceContext | None = None,
) -> dict[str, str]:
    result = dict(headers or {})
    active = context or current_trace_context()
    if active is not None and not any(key.lower() == "traceparent" for key in result):
        result["traceparent"] = active.traceparent
    return result


class OtlpHttpSpanExporter:
    """Bounded, daemon-backed OTLP/HTTP JSON exporter.

    Queue saturation and collector failures increment local counters and drop spans;
    neither can block or fail the application request.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        headers: Mapping[str, str] | None = None,
        queue_size: int = 2048,
        timeout: float = 2.0,
    ) -> None:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("OTLP_ENDPOINT_INVALID")
        self.endpoint = (
            endpoint.rstrip("/")
            if parsed.path.rstrip("/").endswith("/v1/traces")
            else endpoint.rstrip("/") + "/v1/traces"
        )
        self.headers = {"Content-Type": "application/json", **dict(headers or {})}
        self.timeout = timeout
        self._queue: Queue[dict | None] = Queue(maxsize=queue_size)
        self._stop = Event()
        self._lock = Lock()
        self._exported = 0
        self._failed = 0
        self._dropped = 0
        self._thread = Thread(target=self._run, name="otlp-http-exporter", daemon=True)
        self._thread.start()

    def export(self, payload: dict) -> None:
        try:
            self._queue.put_nowait(payload)
        except Full:
            with self._lock:
                self._dropped += 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "exported": self._exported,
                "failed": self._failed,
                "dropped": self._dropped,
                "queued": self._queue.qsize(),
            }

    def close(self, timeout: float = 2.0) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except Full:
            pass
        self._thread.join(timeout=timeout)

    def _run(self) -> None:
        with httpx.Client(timeout=self.timeout, trust_env=False) as client:
            while not self._stop.is_set() or not self._queue.empty():
                try:
                    payload = self._queue.get(timeout=0.2)
                except Empty:
                    continue
                if payload is None:
                    continue
                success = False
                for delay in (0.0, 0.2, 0.5):
                    if delay:
                        time.sleep(delay)
                    try:
                        response = client.post(
                            self.endpoint,
                            content=json.dumps(payload, separators=(",", ":")),
                            headers=self.headers,
                        )
                        if 200 <= response.status_code < 300:
                            success = True
                            break
                    except httpx.HTTPError:
                        pass
                with self._lock:
                    if success:
                        self._exported += 1
                    else:
                        self._failed += 1


def _otlp_headers(value: str | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in (value or "").split(","):
        if not item.strip():
            continue
        key, separator, content = item.partition("=")
        if not separator or not key.strip():
            continue
        headers[key.strip()] = content.strip()
    return headers


def exporter_from_environment() -> OtlpHttpSpanExporter | None:
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or os.environ.get(
        "OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    if not endpoint:
        return None
    try:
        return OtlpHttpSpanExporter(
            endpoint,
            headers=_otlp_headers(os.environ.get("OTEL_EXPORTER_OTLP_HEADERS")),
        )
    except ValueError:
        return None


def _attribute(key: str, value: str | int) -> dict:
    field = "intValue" if isinstance(value, int) else "stringValue"
    return {"key": key, "value": {field: value}}


def server_span_payload(
    *,
    service: str,
    context: TraceContext,
    method: str,
    route: str,
    status: int,
    started_ns: int,
    ended_ns: int,
) -> dict:
    span = {
        "traceId": context.trace_id,
        "spanId": context.span_id,
        "name": f"{method} {route}",
        "kind": "SPAN_KIND_SERVER",
        "startTimeUnixNano": str(started_ns),
        "endTimeUnixNano": str(ended_ns),
        "attributes": [
            _attribute("http.request.method", method),
            _attribute("http.route", route),
            _attribute("http.response.status_code", status),
        ],
        "status": {
            "code": "STATUS_CODE_ERROR" if status >= 500 else "STATUS_CODE_UNSET"
        },
    }
    if context.parent_span_id is not None:
        span["parentSpanId"] = context.parent_span_id
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [_attribute("service.name", service)]
                },
                "scopeSpans": [
                    {
                        "scope": {
                            "name": "platform-protocol-contracts",
                            "version": "2.0.0",
                        },
                        "spans": [span],
                    }
                ],
            }
        ]
    }


def internal_span_payload(
    *,
    service: str,
    context: TraceContext,
    name: str,
    attributes: Mapping[str, str | int],
    error: bool,
    started_ns: int,
    ended_ns: int,
) -> dict:
    """Build a non-HTTP span from an explicit safe attribute allow-list."""

    span = {
        "traceId": context.trace_id,
        "spanId": context.span_id,
        "name": name,
        "kind": "SPAN_KIND_INTERNAL",
        "startTimeUnixNano": str(started_ns),
        "endTimeUnixNano": str(ended_ns),
        "attributes": [
            _attribute(key, value) for key, value in sorted(attributes.items())
        ],
        "status": {
            "code": "STATUS_CODE_ERROR" if error else "STATUS_CODE_UNSET"
        },
    }
    if context.parent_span_id is not None:
        span["parentSpanId"] = context.parent_span_id
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [_attribute("service.name", service)]
                },
                "scopeSpans": [
                    {
                        "scope": {
                            "name": "platform-protocol-contracts",
                            "version": "2.0.0",
                        },
                        "spans": [span],
                    }
                ],
            }
        ]
    }


def install_http_tracing(
    app: Any,
    *,
    service: str,
    exporter: Any | None = None,
) -> Any | None:
    """Install server-span creation and W3C response propagation on a FastAPI app."""

    from fastapi import Request

    selected_exporter = exporter if exporter is not None else exporter_from_environment()

    @app.middleware("http")
    async def tracing_middleware(request: Request, call_next):
        context = server_trace_context(request.headers)
        token = activate_trace_context(context)
        request.state.trace_id = context.trace_id
        request.state.span_id = context.span_id
        request.state.traceparent = context.traceparent
        started_ns = time.time_ns()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["traceparent"] = context.traceparent
            response.headers["X-Trace-ID"] = context.trace_id
            return response
        finally:
            ended_ns = time.time_ns()
            route_object = request.scope.get("route")
            route = getattr(route_object, "path", None) or "unmatched"
            _LOGGER.info(
                "http_request service=%s method=%s route=%s status=%d "
                "trace_id=%s span_id=%s duration_ms=%.3f",
                service,
                request.method,
                route,
                status,
                context.trace_id,
                context.span_id,
                (ended_ns - started_ns) / 1_000_000,
            )
            if selected_exporter is not None and int(context.trace_flags, 16) & 1:
                try:
                    selected_exporter.export(
                        server_span_payload(
                            service=service,
                            context=context,
                            method=request.method,
                            route=route,
                            status=status,
                            started_ns=started_ns,
                            ended_ns=ended_ns,
                        )
                    )
                except Exception:  # exporter implementations are always fail-open
                    pass
            reset_trace_context(token)

    app.state.otlp_exporter = selected_exporter
    return selected_exporter
