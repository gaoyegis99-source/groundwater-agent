"""Shared immutable events and bounded recorder state for pressure probes."""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass, field
from .environment import _safe_route, percentile

@dataclass
class ProbeEvent:
    phase: str
    operation: str
    direction: str
    route: str
    status: str
    duration_ms: float
    field_names: tuple[str, ...] = ()
    payload_digest: str | None = None
    stable_error_code: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": "kernel-probe-event.v1",
            "phase": self.phase,
            "operation": self.operation,
            "direction": self.direction,
            "route": _safe_route(self.route),
            "status": self.status,
            "durationMs": round(self.duration_ms, 3),
            "fieldNames": list(self.field_names),
            "payloadDigest": self.payload_digest,
            "stableErrorCode": self.stable_error_code,
        }


@dataclass
class ProbeRecorder:
    events: list[ProbeEvent] = field(default_factory=list)
    failures: list[dict[str, object]] = field(default_factory=list)
    latencies: list[float] = field(default_factory=list)

    def event(self, event: ProbeEvent) -> None:
        self.events.append(event)
        if event.duration_ms > 0:
            self.latencies.append(event.duration_ms / 1000)

    def fail(self, code: str, *, phase: str, operation: str, detail: str) -> None:
        self.failures.append({
            "code": code,
            "phase": phase,
            "operation": operation,
            "detail": detail[:240],
        })

    def assert_fields(
        self,
        value: object,
        expected: set[str],
        *,
        phase: str,
        operation: str,
    ) -> None:
        if not isinstance(value, dict):
            self.fail("PROBE_RESPONSE_NOT_OBJECT", phase=phase, operation=operation, detail="response is not an object")
            return
        actual = set(value)
        if actual != expected:
            self.fail(
                "PROBE_UNEXPECTED_FIELD",
                phase=phase,
                operation=operation,
                detail=f"expected={sorted(expected)} actual={sorted(actual)}",
            )

    def summary(self) -> dict[str, object]:
        operation_counts = Counter(
            f"{event.phase}:{event.operation}:{event.status}" for event in self.events
        )
        return {
            "eventCount": len(self.events),
            "operationCounts": dict(sorted(operation_counts.items())),
            "events": [event.as_dict() for event in self.events[:512]],
            "failureCount": len(self.failures),
            "failures": self.failures[:32],
            "latencySeconds": {
                "count": len(self.latencies),
                "p50": round(percentile(self.latencies, 0.50), 4),
                "p95": round(percentile(self.latencies, 0.95), 4),
                "max": round(max(self.latencies, default=0.0), 4),
            },
        }
