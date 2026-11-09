from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from platform_protocol_contracts import (
    TraceContext,
    activate_trace_context,
    inject_trace_headers,
    install_http_tracing,
    parse_traceparent,
    reset_trace_context,
    server_trace_context,
)


class Exporter:
    def __init__(self, *, fail: bool = False) -> None:
        self.payloads = []
        self.fail = fail

    def export(self, payload):
        if self.fail:
            raise RuntimeError("collector unavailable")
        self.payloads.append(payload)


def test_traceparent_validation_and_injection() -> None:
    incoming = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
    assert parse_traceparent(incoming) == (
        "0123456789abcdef0123456789abcdef",
        "0123456789abcdef",
        "01",
    )
    assert parse_traceparent("00-" + "0" * 32 + "-0123456789abcdef-01") is None
    assert parse_traceparent("garbage") is None
    context = server_trace_context({"traceparent": incoming})
    assert context.trace_id == "0123456789abcdef0123456789abcdef"
    assert context.parent_span_id == "0123456789abcdef"
    assert context.span_id != context.parent_span_id

    token = activate_trace_context(context)
    try:
        assert inject_trace_headers({"X-Test": "yes"}) == {
            "X-Test": "yes",
            "traceparent": context.traceparent,
        }
    finally:
        reset_trace_context(token)


def test_http_tracing_exports_only_allowlisted_route_attributes() -> None:
    exporter = Exporter()
    app = FastAPI()
    install_http_tracing(app, service="test-service", exporter=exporter)

    @app.post("/things/{thing_id}")
    def thing(thing_id: str, request: Request):
        return {
            "thing": thing_id,
            "traceId": request.state.trace_id,
            "spanId": request.state.span_id,
        }

    incoming = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
    with TestClient(app) as client:
        response = client.post(
            "/things/private-value?credential=secret",
            headers={"traceparent": incoming},
            json={"prompt": "must never be exported"},
        )
    assert response.status_code == 200
    assert response.headers["traceparent"].startswith(
        "00-0123456789abcdef0123456789abcdef-"
    )
    span = exporter.payloads[0]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert span["parentSpanId"] == "0123456789abcdef"
    assert span["name"] == "POST /things/{thing_id}"
    serialized = str(exporter.payloads[0])
    assert "private-value" not in serialized
    assert "credential" not in serialized
    assert "secret" not in serialized
    assert "prompt" not in serialized


def test_exporter_failure_is_request_fail_open() -> None:
    app = FastAPI()
    install_http_tracing(app, service="test", exporter=Exporter(fail=True))

    @app.get("/ok")
    def ok():
        return {"ok": True}

    with TestClient(app) as client:
        response = client.get("/ok")
    assert response.status_code == 200
    assert parse_traceparent(response.headers["traceparent"]) is not None


def test_explicit_traceparent_is_not_overwritten() -> None:
    context = TraceContext("1" * 32, "2" * 16, None)
    token = activate_trace_context(context)
    try:
        supplied = "00-" + "3" * 32 + "-" + "4" * 16 + "-01"
        assert inject_trace_headers({"Traceparent": supplied}) == {
            "Traceparent": supplied
        }
    finally:
        reset_trace_context(token)
