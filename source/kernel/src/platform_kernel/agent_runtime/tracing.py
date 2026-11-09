from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from agents import set_trace_provider
from agents.tracing import Span, Trace, TracingProcessor
from agents.tracing.provider import DefaultTraceProvider

from ..database import connect_database


_TERMINAL_RUN_STATUSES = ("COMPLETE", "FAILED", "SUPERSEDED")
_KERNEL_ERROR_CODES = frozenset(
    {
        "AGENT_ACTION_NOT_TERMINAL",
        "AGENT_ACTION_REPLAY_INVALID",
        "AGENT_BUDGET_EXCEEDED",
        "AGENT_CONTEXT_TOO_LARGE",
        "AGENT_DEADLINE_EXCEEDED",
        "AGENT_MAX_TURNS_EXCEEDED",
        "AGENT_OBSERVATION_LOOP",
        "AGENT_INTENT_ROUTING_INVALID",
        "AGENT_MEMORY_COMPACTION_INVALID",
        "AGENT_MODEL_OUTPUT_INVALID",
        "AGENT_PLAN_ACTION_MISMATCH",
        "AGENT_PLAN_INVALID",
        "AGENT_PLAN_REQUIRED",
        "AGENT_PROVIDER_UNAVAILABLE",
        "AGENT_REJECTION_ACTIONS_INVALID",
        "AGENT_RUN_STATE_INVALID",
        "AGENT_SDK_CONFIGURATION_INVALID",
        "OBSERVATION_SERVICE_UNAVAILABLE",
        "OBSERVATION_RESPONSE_INVALID",
    }
)


def install_trace_processors(processors: list[TracingProcessor]) -> None:
    """Install only local processors, without constructing the SDK exporter.

    Calling the SDK's ``set_trace_processors`` as the first tracing operation
    first creates its networked default exporter.  The kernel persists a
    redacted local trace instead, so that exporter is both unnecessary and an
    undeclared ambient-network path.
    """

    provider = DefaultTraceProvider()
    provider.set_processors(processors)
    set_trace_provider(provider)


class SQLiteTraceProcessor(TracingProcessor):
    def __init__(self, database: Path) -> None:
        self.database = database

    def on_trace_start(self, trace: Trace) -> None:
        payload = trace.export()
        if payload is None:
            return
        redacted = redact_trace_payload(payload)
        agent_run_id = redacted["metadata"]["agent_run_id"]
        with connect_database(self.database) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO agent_traces VALUES (?, ?, ?)",
                (
                    redacted["id"],
                    agent_run_id,
                    json.dumps(redacted, ensure_ascii=False),
                ),
            )

    def on_trace_end(self, trace: Trace) -> None:
        self.on_trace_start(trace)

    def on_span_start(self, span: Span[object]) -> None:
        pass

    def on_span_end(self, span: Span[object]) -> None:
        payload = span.export()
        if payload is None:
            return
        redacted = redact_span_payload(payload)
        with connect_database(self.database) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO agent_spans VALUES (?, ?, ?)",
                (
                    redacted["id"],
                    redacted["trace_id"],
                    json.dumps(redacted, ensure_ascii=False),
                ),
            )

    def shutdown(self) -> None:
        pass

    def force_flush(self) -> None:
        pass


def redact_trace_payload(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    allowed_metadata = {
        key: metadata[key]
        for key in ("agent_run_id", "goal_revision", "product_id")
        if key in metadata
    }
    return {
        "object": "trace",
        "id": payload["id"],
        "workflow_name": payload.get("workflow_name"),
        "group_id": payload.get("group_id"),
        "metadata": allowed_metadata,
    }


def redact_span_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist only metadata, for every SDK span type and every tool."""
    source = payload.get("span_data")
    source = source if isinstance(source, dict) else {}
    data = {
        key: source[key]
        for key in ("type", "name", "response_id")
        if isinstance(source.get(key), str)
    }
    data.update(input=None, output=None)
    usage = source.get("usage")
    if isinstance(usage, dict):
        data["usage"] = {
            key: usage[key]
            for key in ("input_tokens", "output_tokens", "total_tokens", "requests")
            if type(usage.get(key)) is int and usage[key] >= 0
        }
    if source.get("type") == "custom":
        details = source.get("data")
        details = details if isinstance(details, dict) else {}
        allowed = {}
        if details.get("phase") in {"initial", "resume", "compaction"}:
            allowed["phase"] = details["phase"]
        if details.get("status") in {"RUNNING", "COMPLETE", "SUCCEEDED", "FAILED"}:
            allowed["status"] = details["status"]
        code = details.get("error_code")
        if isinstance(code, str) and code in _KERNEL_ERROR_CODES:
            allowed["error_code"] = code
        usage = details.get("usage")
        if isinstance(usage, dict):
            allowed["usage"] = {
                key: usage[key]
                for key in ("input_tokens", "output_tokens", "total_tokens")
                if type(usage.get(key)) is int and usage[key] >= 0
            }
        data["data"] = allowed
    error = None
    if payload.get("error") is not None:
        error = {"message": "Span failed", "data": {"code": "SPAN_FAILED"}}
    return {
        "object": "trace.span",
        "id": payload["id"],
        "trace_id": payload["trace_id"],
        "parent_id": payload.get("parent_id"),
        "started_at": payload.get("started_at"),
        "ended_at": payload.get("ended_at"),
        "duration_ms": _duration_ms(payload.get("started_at"), payload.get("ended_at")),
        "status": "FAILED" if error is not None else "COMPLETE",
        "span_data": data,
        "error": error,
    }


def load_trace(database: Path, trace_id: str) -> tuple[dict[str, Any], ...]:
    with connect_database(database) as connection:
        trace = connection.execute(
            "SELECT payload_json FROM agent_traces WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
        spans = connection.execute(
            """SELECT payload_json FROM agent_spans WHERE trace_id = ?
               ORDER BY rowid""",
            (trace_id,),
        ).fetchall()
    items = [] if trace is None else [json.loads(trace["payload_json"])]
    items.extend(json.loads(span["payload_json"]) for span in spans)
    return tuple(items)


def prune_traces(database: Path, *, now: datetime | None = None) -> int:
    current = now or datetime.now(timezone.utc)
    cutoff = (
        (current - timedelta(days=7))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    placeholders = ",".join("?" for _ in _TERMINAL_RUN_STATUSES)
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            f"""SELECT t.trace_id FROM agent_traces t
                JOIN agent_runs r ON r.agent_run_id = t.agent_run_id
                WHERE r.status IN ({placeholders})
                  AND r.finished_at IS NOT NULL AND r.finished_at < ?""",
            (*_TERMINAL_RUN_STATUSES, cutoff),
        ).fetchall()
        trace_ids = tuple(row["trace_id"] for row in rows)
        for trace_id in trace_ids:
            connection.execute(
                "DELETE FROM agent_spans WHERE trace_id = ?", (trace_id,)
            )
            connection.execute(
                "DELETE FROM agent_traces WHERE trace_id = ?", (trace_id,)
            )
    return len(trace_ids)


def _duration_ms(started_at: object, ended_at: object) -> int | None:
    if not isinstance(started_at, str) or not isinstance(ended_at, str):
        return None
    started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    ended = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    return round((ended - started).total_seconds() * 1000)
