from platform_protocol_contracts import CounterSample, GaugeSample, MetricsRegistry
from platform_protocol_contracts import install_http_metrics
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_openmetrics_uses_only_bounded_route_and_status_labels() -> None:
    registry = MetricsRegistry("kernel")
    registry.observe_request("GET", "/api/v12/sessions/{session_id}", 200, 0.02)
    registry.observe_request("GET", "/api/v12/sessions/{session_id}", 404, 0.03)

    output = registry.render((
        GaugeSample(
            "platform_queue_items",
            2,
            {"service": "kernel", "queue": "agent", "state": "QUEUED"},
            "Persisted queue items.",
        ),
        CounterSample(
            "platform_sse_connections_total", 3, {"kind": "reconnections"},
            "SSE reconnects.",
        ),
    ))

    assert 'route="/api/v12/sessions/{session_id}"' in output
    assert 'status="2xx"' in output
    assert 'status="4xx"' in output
    assert "session-" not in output
    assert output.endswith("# EOF\n")
    assert "# TYPE platform_sse_connections_total counter" in output


def test_internal_metrics_is_openmetrics_and_absent_from_openapi() -> None:
    app = FastAPI()

    @app.get("/items/{item_id}")
    def item(item_id: str):
        return {"item": item_id}

    install_http_metrics(app, service="test")
    with TestClient(app) as client:
        assert client.get("/items/private-value").status_code == 200
        response = client.get("/internal/metrics")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/openmetrics-text")
        assert 'route="/items/{item_id}"' in response.text
        assert "private-value" not in response.text
        assert "/internal/metrics" not in client.get("/openapi.json").json()["paths"]
