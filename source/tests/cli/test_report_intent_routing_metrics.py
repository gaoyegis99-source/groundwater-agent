from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest

from platform_kernel.database import connect_database, initialize_database
from platform_kernel.interaction.intent_route_queue import (
    claim_intent_route,
    recover_intent_routes,
)
from platform_kernel.application.route_commands import (
    fail_intent_route,
    resolve_intent_route,
)
from platform_kernel.interaction.message_requests import create_message_request
from platform_kernel.interaction.persistence import create_session


from scripts.evaluation import report_intent_routing_metrics as reporting


def _create_auto(
    database: Path,
    *,
    session_id: str,
    request_id: str,
    message: str,
    decision: str,
    duration_ms: int,
    recover_before_decision: bool = False,
) -> str:
    create_session(database, session_id, "p", f"report-{session_id}")
    receipt, _ = create_message_request(
        database,
        session_id,
        "p",
        request_id,
        message,
        "AUTO",
        corrects_request_id=None,
    )
    run_id = receipt["agent_run_id"]
    assert claim_intent_route(database, run_id) is not None
    if recover_before_decision:
        recover_intent_routes(database)
        assert claim_intent_route(database, run_id) is not None
    resolve_intent_route(
        database,
        run_id,
        decision,
        "READ_ONLY" if decision == "DIALOGUE" else None,
        provider="deepseek",
        model="router",
        trace_id=f"trace-{request_id}",
        provider_requests=1,
        input_tokens=10,
        output_tokens=2,
        total_tokens=12,
    )
    with connect_database(database) as connection:
        connection.execute(
            "UPDATE message_requests SET route_duration_ms=? WHERE agent_run_id=?",
            (duration_ms, run_id),
        )
    return run_id


def _release_session(database: Path, session_id: str) -> None:
    with connect_database(database) as connection:
        connection.execute(
            """UPDATE sessions
               SET pending_agent_run_id=NULL,agent_start_state=NULL
               WHERE session_id=?""",
            (session_id,),
        )


def _seed(database: Path) -> None:
    initialize_database(database)
    rows = (
        ("dialogue", "DIALOGUE", 10),
        ("method-comparison", "DIALOGUE", 20),
        ("draft", "WORKFLOW_DRAFT", 30),
        ("clarification", "NEEDS_CLARIFICATION", 40),
    )
    for request_id, decision, duration in rows:
        _create_auto(
            database,
            session_id=f"session-{request_id}",
            request_id=request_id,
            message=f"private-message-{request_id}",
            decision=decision,
            duration_ms=duration,
        )
    _create_auto(
        database,
        session_id="session-recovered",
        request_id="recovered",
        message="private-message-recovered",
        decision="DIALOGUE",
        duration_ms=50,
        recover_before_decision=True,
    )

    _release_session(database, "session-method-comparison")
    create_message_request(
        database,
        "session-method-comparison",
        "p",
        "correction",
        "private-message-method-comparison",
        "WORKFLOW_DRAFT",
        corrects_request_id="method-comparison",
    )
    _release_session(database, "session-clarification")
    create_message_request(
        database,
        "session-clarification",
        "p",
        "ordinary-clarification-reply",
        "这是对澄清问题的普通回答",
        "DIALOGUE",
        corrects_request_id=None,
    )

    create_session(database, "session-failed", "p", "report-session-failed")
    failed, _ = create_message_request(
        database,
        "session-failed",
        "p",
        "failed",
        "private-message-failed",
        "AUTO",
        corrects_request_id=None,
    )
    assert claim_intent_route(database, failed["agent_run_id"]) is not None
    fail_intent_route(
        database,
        failed["agent_run_id"],
        "AGENT_PROVIDER_UNAVAILABLE",
        provider="deepseek",
        model="router",
        trace_id="trace-failed",
        provider_requests=1,
        input_tokens=3,
        output_tokens=0,
        total_tokens=3,
    )

    create_session(database, "session-pending", "p", "report-session-pending")
    create_message_request(
        database,
        "session-pending",
        "p",
        "pending",
        "private-message-pending",
        "AUTO",
        corrects_request_id=None,
    )


def test_report_aggregates_v34_without_messages_reasoning_or_currency(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    database = tmp_path / "kernel.sqlite3"
    _seed(database)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    output = tmp_path / "routing-report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "report_intent_routing_metrics.py",
            "--database",
            str(database),
            "--output",
            str(output),
        ],
    )

    reporting.main()

    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schemaVersion"] == "intent-routing-operations-report.v2"
    assert report["database_schema_version"] == 34
    assert report["counts"] == {
        "auto_requests": 7,
        "decided_requests": 5,
        "failed_requests": 1,
        "pending_requests": 1,
    }
    assert report["decision_distribution"] == {
        "DIALOGUE": 3,
        "WORKFLOW_DRAFT": 1,
        "CONTROL": 0,
        "NEEDS_CLARIFICATION": 1,
    }
    assert report["routing_source_distribution"] == {
        "AUTO": 2,
        "MODEL": 5,
        "RULE": 0,
        "EXPLICIT": 0,
    }
    assert report["rule_hit_rate"] == 0
    assert report["model_router_call_rate"] == 0.857143
    assert report["clarification_rate"] == {
        "rate": 0.2,
        "clarification_requests": 1,
        "decided_requests": 5,
    }
    lower_bound = report["observed_misroute_lower_bound"]
    assert lower_bound["rate"] == 0.2
    assert lower_bound["corrected_auto_requests"] == 1
    assert lower_bound["decided_auto_requests"] == 5
    assert "not production accuracy" in lower_bound["definition"]
    assert report["accept_to_decision_latency_ms"] == {
        "sample_count": 5,
        "p50": 30,
        "p95": 48,
    }
    interaction = report["interaction_latency_ms"]
    assert interaction["accept_to_router_start"]["sample_count"] == 6
    assert interaction["first_effective_feedback"]["sample_count"] == 6
    assert interaction["longest_feedback_gap"]["sample_count"] == 6
    assert interaction["router_finish_to_agent_start"]["sample_count"] == 1
    assert interaction["accept_to_terminal"]["sample_count"] == 1
    assert report["provider_cost_units"] == {
        "basis": "request and token counts only; no currency conversion",
        "request_count": 6,
        "agent_model_request_count": 0,
        "unknown_call_count": 1,
        "tokens": {"input": 53, "output": 10, "total": 63},
    }
    encoded = json.dumps(report, ensure_ascii=False)
    assert "private-message" not in encoded
    assert "普通回答" not in encoded
    assert "trace-" not in encoded
    assert "currency" in encoded
    assert "money" not in encoded
    assert json.loads(capsys.readouterr().out) == report


def test_report_rejects_non_v34_kernel_database(tmp_path: Path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    with connect_database(database) as connection:
        connection.execute("UPDATE schema_version SET version=15")

    with pytest.raises(ValueError, match="KERNEL_SCHEMA_VERSION_UNSUPPORTED"):
        reporting.build_report(database)
