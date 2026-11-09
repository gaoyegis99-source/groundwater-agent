from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import math
import re
from threading import Lock
import time
from typing import Any


_NAME = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*\Z")
_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(values: Mapping[str, str]) -> str:
    if not values:
        return ""
    return "{" + ",".join(
        f'{key}="{_escape(value)}"' for key, value in sorted(values.items())
    ) + "}"


@dataclass(frozen=True)
class GaugeSample:
    name: str
    value: float
    labels: Mapping[str, str]
    help: str

    def __post_init__(self) -> None:
        if _NAME.fullmatch(self.name) is None or not math.isfinite(self.value):
            raise ValueError("METRIC_SAMPLE_INVALID")


@dataclass(frozen=True)
class CounterSample:
    name: str
    value: int
    labels: Mapping[str, str]
    help: str

    def __post_init__(self) -> None:
        if _NAME.fullmatch(self.name) is None or self.value < 0:
            raise ValueError("METRIC_SAMPLE_INVALID")


class MetricsRegistry:
    """Small, dependency-free OpenMetrics registry with bounded label domains."""

    def __init__(self, service: str) -> None:
        self.service = service
        self._lock = Lock()
        self._requests: dict[tuple[str, str, str], int] = defaultdict(int)
        self._duration_count: dict[tuple[str, str], int] = defaultdict(int)
        self._duration_sum: dict[tuple[str, str], float] = defaultdict(float)
        self._duration_buckets: dict[tuple[str, str, float], int] = defaultdict(int)

    def observe_request(
        self, method: str, route: str, status: int, duration_seconds: float
    ) -> None:
        status_class = f"{status // 100}xx"
        key = (method, route, status_class)
        histogram = (method, route)
        with self._lock:
            self._requests[key] += 1
            self._duration_count[histogram] += 1
            self._duration_sum[histogram] += duration_seconds
            for bucket in _BUCKETS:
                if duration_seconds <= bucket:
                    self._duration_buckets[method, route, bucket] += 1

    def render(
        self, samples: tuple[GaugeSample | CounterSample, ...] = ()
    ) -> str:
        with self._lock:
            requests = dict(self._requests)
            counts = dict(self._duration_count)
            sums = dict(self._duration_sum)
            buckets = dict(self._duration_buckets)
        lines = [
            "# HELP platform_http_requests_total HTTP requests by stable route and status class.",
            "# TYPE platform_http_requests_total counter",
        ]
        for (method, route, status), value in sorted(requests.items()):
            lines.append(
                "platform_http_requests_total"
                + _labels({"service": self.service, "method": method, "route": route, "status": status})
                + f" {value}"
            )
        lines.extend([
            "# HELP platform_http_request_duration_seconds HTTP request duration.",
            "# TYPE platform_http_request_duration_seconds histogram",
        ])
        for method, route in sorted(counts):
            base = {"service": self.service, "method": method, "route": route}
            for bucket in _BUCKETS:
                lines.append(
                    "platform_http_request_duration_seconds_bucket"
                    + _labels({**base, "le": str(bucket)})
                    + f" {buckets.get((method, route, bucket), 0)}"
                )
            lines.append(
                "platform_http_request_duration_seconds_bucket"
                + _labels({**base, "le": "+Inf"})
                + f" {counts[(method, route)]}"
            )
            lines.append(
                "platform_http_request_duration_seconds_count"
                + _labels(base)
                + f" {counts[(method, route)]}"
            )
            lines.append(
                "platform_http_request_duration_seconds_sum"
                + _labels(base)
                + f" {sums[(method, route)]:.9f}"
            )
        gauges: dict[str, list[GaugeSample]] = defaultdict(list)
        counters: dict[str, list[CounterSample]] = defaultdict(list)
        for sample in samples:
            target = counters if isinstance(sample, CounterSample) else gauges
            target[sample.name].append(sample)
        for name, counter_samples in sorted(counters.items()):
            lines.extend([
                f"# HELP {name} {counter_samples[0].help}",
                f"# TYPE {name} counter",
            ])
            for sample in sorted(
                counter_samples, key=lambda item: sorted(item.labels.items())
            ):
                lines.append(f"{name}{_labels(sample.labels)} {sample.value}")
        for name, gauge_samples in sorted(gauges.items()):
            lines.extend([
                f"# HELP {name} {gauge_samples[0].help}",
                f"# TYPE {name} gauge",
            ])
            for sample in sorted(
                gauge_samples, key=lambda item: sorted(item.labels.items())
            ):
                lines.append(f"{name}{_labels(sample.labels)} {sample.value:g}")
        lines.append("# EOF")
        return "\n".join(lines) + "\n"


def install_http_metrics(
    app: Any,
    *,
    service: str,
    gauges: Callable[[], tuple[GaugeSample | CounterSample, ...]] | None = None,
) -> MetricsRegistry:
    # Imported lazily so the wire-contract package stays usable by non-HTTP clients.
    from fastapi import Request
    from fastapi.responses import PlainTextResponse

    registry = MetricsRegistry(service)

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        if request.url.path == "/internal/metrics":
            return await call_next(request)
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route_object = request.scope.get("route")
            route = getattr(route_object, "path", None) or "unmatched"
            registry.observe_request(
                request.method, route, status, time.perf_counter() - started
            )

    @app.get("/internal/metrics", include_in_schema=False)
    def internal_metrics():
        samples = () if gauges is None else gauges()
        return PlainTextResponse(
            registry.render(samples), media_type="application/openmetrics-text; version=1.0.0"
        )

    return registry
