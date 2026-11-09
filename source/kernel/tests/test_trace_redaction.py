from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from types import SimpleNamespace

import pytest

from platform_kernel.agent_runtime.sdk_execution.execution import _build_run_config
from platform_kernel.agent_runtime.tracing import (
    SQLiteTraceProcessor,
    load_trace,
    prune_traces,
    redact_span_payload,
)
from platform_kernel.database import connect_database, initialize_database


def _span(span_data, *, error=None):
    return {
        "object": "trace.span", "id": "span", "trace_id": "trace", "parent_id": None,
        "started_at": "2026-09-01T00:00:00.000Z", "ended_at": "2026-09-01T00:00:00.150Z",
        "span_data": span_data, "error": error, "unknown": "secret-top-level",
    }


def test_sdk_disables_sensitive_tracing_but_preserves_metadata() -> None:
    config = _build_run_config(
        SimpleNamespace(product_id="groundwater"), session_id="session", project_id="project",
        agent_run_id="run", trace_id="trace_" + "a" * 32, goal_revision=1,
    )
    assert config.tracing_disabled is False
    assert config.trace_include_sensitive_data is False
    assert config.trace_metadata["agent_run_id"] == "run"


@pytest.mark.parametrize("kind,name", [
    ("function", "list_resources"), ("function", "inspect_resource_table"),
    ("function", "list_knowledge_documents"), ("function", "list_knowledge_chunks"),
    ("function", "run_summary"), ("generation", None), ("response", None),
    ("custom", "agent.segment"), ("future-sdk-span", None),
])
def test_every_span_type_persists_only_allowlisted_metadata(tmp_path, kind, name):
    payload = _span({
        "type": kind, "name": name, "input": "secret-message",
        "output": {"sample_rows": ["secret-cell"]}, "model": "secret-model",
        "model_config": {"key": "secret-key"}, "response_id": "resp-id",
        "usage": {"input_tokens": 11, "output_tokens": 3, "total_tokens": 14, "secret": "secret-usage"},
        "data": {"phase": "initial", "status": "FAILED", "error_code": "AGENT_CONTEXT_TOO_LARGE", "body": "secret-body"},
    }, error={"message": "secret-exception", "data": {"arguments": "secret-arguments"}})
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    SQLiteTraceProcessor(database).on_span_end(SimpleNamespace(export=lambda: payload))
    redacted = load_trace(database, "trace")[0]
    assert "secret" not in json.dumps(redacted)
    assert redacted["duration_ms"] == 150 and redacted["status"] == "FAILED"
    data = redacted["span_data"]
    assert data["type"] == kind and data["input"] is None and data["output"] is None
    assert data["usage"] == {"input_tokens": 11, "output_tokens": 3, "total_tokens": 14}
    if kind == "custom":
        assert data["data"] == {"phase": "initial", "status": "FAILED", "error_code": "AGENT_CONTEXT_TOO_LARGE"}
    if name is not None:
        assert data["name"] == name
    assert payload["span_data"]["input"] == "secret-message"


def test_custom_unknown_diagnostics_and_raw_response_are_not_persisted():
    payload = _span({"type": "custom", "data": {
        "phase": "secret-phase", "status": "secret-status", "error_code": "AGENT_secret",
    }, "response": {"output": "secret-text"}})
    assert "secret" not in json.dumps(redact_span_payload(payload))


def test_trace_metadata_does_not_store_arbitrary_sdk_fields(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)

    class Trace:
        def export(self):
            return {
                "id": "trace",
                "workflow_name": "agent",
                "group_id": "session",
                "metadata": {
                    "agent_run_id": "run",
                    "session_id": "session",
                    "input": "secret-input",
                    "api_key": "secret-key",
                },
            }

    SQLiteTraceProcessor(database).on_trace_start(Trace())
    assert "secret" not in json.dumps(load_trace(database, "trace"))


def test_prune_only_terminal_traces_older_than_seven_days(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    cases = [
        ("old", "COMPLETE", "2026-09-20T23:59:59.999Z"),
        ("failed", "FAILED", "2026-09-20T00:00:00.000Z"),
        ("superseded", "SUPERSEDED", "2026-09-20T00:00:00.000Z"),
        ("boundary", "COMPLETE", "2026-09-21T00:00:00.000Z"),
        ("running", "RUNNING", None),
        ("waiting", "WAITING_EXECUTION", None),
    ]
    with connect_database(database) as connection:
        for run_id, status, finished_at in cases:
            connection.execute(
                """INSERT INTO agent_runs VALUES (
                    ?, 'session', 'project', 1, 'test', 'test', ?, '[]', NULL,
                    0, 0, 0, ?, NULL, 'start', 'start', ?, NULL)""",
                (run_id, status, run_id, finished_at),
            )
            connection.execute(
                "INSERT INTO agent_traces VALUES (?, ?, '{}')", (run_id, run_id)
            )
            connection.execute(
                "INSERT INTO agent_spans VALUES (?, ?, '{}')", (run_id, run_id)
            )
    assert prune_traces(database, now=datetime(2026, 9, 28, tzinfo=timezone.utc)) == 3
    with connect_database(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 6
        assert {
            row[0] for row in connection.execute("SELECT trace_id FROM agent_traces")
        } == {"boundary", "running", "waiting"}
        assert {
            row[0] for row in connection.execute("SELECT trace_id FROM agent_spans")
        } == {"boundary", "running", "waiting"}
    assert prune_traces(database, now=datetime(2026, 9, 28, tzinfo=timezone.utc)) == 0


def test_trace_pruning_is_atomic(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    with connect_database(database) as connection:
        connection.execute("""INSERT INTO agent_runs VALUES (
            'run', 'session', 'project', 1, 'test', 'test', 'COMPLETE', '[]', NULL,
            0, 0, 0, 'trace', NULL, 'start', 'start', '2026-09-01T00:00:00.000Z', NULL)""")
        connection.execute("INSERT INTO agent_traces VALUES ('trace', 'run', '{}')")
        connection.execute("INSERT INTO agent_spans VALUES ('span', 'trace', '{}')")
        connection.execute("""CREATE TRIGGER reject_trace_delete BEFORE DELETE ON agent_traces
            BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="test failure"):
        prune_traces(database, now=datetime(2026, 9, 28, tzinfo=timezone.utc))
    with connect_database(database) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM agent_traces").fetchone()[0] == 1
        )
        assert connection.execute("SELECT COUNT(*) FROM agent_spans").fetchone()[0] == 1
