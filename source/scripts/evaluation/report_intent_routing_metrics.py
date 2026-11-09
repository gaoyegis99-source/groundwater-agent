"""Write an aggregate, read-only intent-routing operations report."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3


# The report is intentionally read-only.  Databases must have passed the
# verified Kernel v29 -> v30 -> v31 -> v32 -> v33 -> v34 upgrades before reporting
# rather than letting analytics accept an obsolete or unfrozen layout.
SCHEMA_VERSION = 34
DECISIONS = (
    "DIALOGUE",
    "WORKFLOW_DRAFT",
    "CONTROL",
    "NEEDS_CLARIFICATION",
)


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 6)


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return round(ordered[lower] * (upper - index) + ordered[upper] * (index - lower))


def _elapsed_ms(started_at: str | None, finished_at: str | None) -> int | None:
    if started_at is None or finished_at is None:
        return None
    started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    finished = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    return max(0, int((finished - started).total_seconds() * 1000))


def _latency_summary(values: list[int | None]) -> dict[str, int | None]:
    samples = [value for value in values if value is not None]
    return {
        "sample_count": len(samples),
        "p50": _percentile(samples, 0.50),
        "p95": _percentile(samples, 0.95),
    }


def _connect_read_only(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        database.resolve().as_uri() + "?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    return connection


def build_report(database: Path) -> dict[str, object]:
    with _connect_read_only(database) as connection:
        connection.execute("BEGIN")
        version_rows = connection.execute(
            "SELECT version FROM schema_version"
        ).fetchall()
        if len(version_rows) != 1 or version_rows[0]["version"] != SCHEMA_VERSION:
            raise ValueError("KERNEL_SCHEMA_VERSION_UNSUPPORTED")
        auto_rows = connection.execute(
            """SELECT mr.agent_run_id,mr.intent_decision,mr.routing_status,mr.routing_source,
                      mr.route_duration_ms,
                      route_provider_requests,route_input_tokens,
                      route_output_tokens,route_total_tokens,
                      unknown_cost_attempts,mr.created_at,mr.route_started_at,
                      mr.route_finished_at,ar.started_at AS agent_started_at,
                      ar.finished_at AS agent_finished_at
               FROM message_requests AS mr
               LEFT JOIN agent_runs AS ar ON ar.agent_run_id=mr.agent_run_id
               WHERE mr.requested_mode='AUTO'"""
        ).fetchall()
        event_rows = connection.execute(
            """SELECT json_extract(payload_json,'$.agent_run_id') AS agent_run_id,
                      event_type,created_at,payload_json
               FROM session_events
               WHERE json_extract(payload_json,'$.agent_run_id') IS NOT NULL
               ORDER BY event_id"""
        ).fetchall()
        corrected_auto_requests = connection.execute(
            """SELECT COUNT(*) FROM (
                   SELECT DISTINCT original.session_id,original.request_id
                   FROM message_requests AS correction
                   JOIN message_requests AS original
                     ON original.session_id=correction.session_id
                    AND original.request_id=correction.corrects_request_id
                   WHERE correction.corrects_request_id IS NOT NULL
                     AND original.requested_mode='AUTO'
                     AND original.intent_decision IS NOT NULL
               )"""
        ).fetchone()[0]

    decided = [row for row in auto_rows if row["intent_decision"] is not None]
    distribution = {
        decision: sum(row["intent_decision"] == decision for row in decided)
        for decision in DECISIONS
    }
    clarification_count = distribution["NEEDS_CLARIFICATION"]
    source_distribution = {
        source: sum(row["routing_source"] == source for row in auto_rows)
        for source in ("AUTO", "MODEL", "RULE", "EXPLICIT")
    }
    latencies = [
        row["route_duration_ms"]
        for row in decided
        if row["route_duration_ms"] is not None
    ]
    failed_count = sum(row["routing_status"] == "FAILED" for row in auto_rows)
    pending_count = len(auto_rows) - len(decided) - failed_count
    events_by_run: dict[str, list[sqlite3.Row]] = {}
    for event in event_rows:
        events_by_run.setdefault(event["agent_run_id"], []).append(event)
    feedback_types_excluded = {"turn.accepted", "intent_routing.requested"}
    first_feedback: list[int | None] = []
    longest_silence: list[int | None] = []
    for row in auto_rows:
        visible = [
            event
            for event in events_by_run.get(row["agent_run_id"], [])
            if event["event_type"] not in feedback_types_excluded
        ]
        first_feedback.append(
            _elapsed_ms(row["created_at"], visible[0]["created_at"])
            if visible
            else None
        )
        timestamps = [row["created_at"], *(event["created_at"] for event in visible)]
        gaps = [
            _elapsed_ms(left, right)
            for left, right in zip(timestamps, timestamps[1:], strict=False)
        ]
        longest_silence.append(max(gaps) if gaps else None)
    agent_model_requests = sum(
        json.loads(event["payload_json"]).get("kind") == "MODEL_REQUEST"
        and json.loads(event["payload_json"]).get("status") == "QUEUED"
        for event in event_rows
        if event["event_type"] == "agent.activity"
    )
    return {
        "schemaVersion": "intent-routing-operations-report.v2",
        "generated_at": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "database_schema_version": SCHEMA_VERSION,
        "scope": {"requested_mode": "AUTO"},
        "counts": {
            "auto_requests": len(auto_rows),
            "decided_requests": len(decided),
            "failed_requests": failed_count,
            "pending_requests": pending_count,
        },
        "decision_distribution": distribution,
        "routing_source_distribution": source_distribution,
        "rule_hit_rate": _ratio(source_distribution["RULE"], len(auto_rows)),
        "model_router_call_rate": _ratio(
            sum(row["route_provider_requests"] > 0 for row in auto_rows),
            len(auto_rows),
        ),
        "clarification_rate": {
            "rate": _ratio(clarification_count, len(decided)),
            "clarification_requests": clarification_count,
            "decided_requests": len(decided),
        },
        "observed_misroute_lower_bound": {
            "rate": _ratio(corrected_auto_requests, len(decided)),
            "corrected_auto_requests": corrected_auto_requests,
            "decided_auto_requests": len(decided),
            "definition": (
                "Distinct decided AUTO requests referenced by corrects_request_id "
                "divided by all decided AUTO requests. Ordinary clarification "
                "replies are excluded. This is a lower bound, not production accuracy."
            ),
        },
        "accept_to_decision_latency_ms": {
            "sample_count": len(latencies),
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
        },
        "interaction_latency_ms": {
            "definitions": {
                "first_effective_feedback": "First persisted event after acceptance other than turn.accepted or intent_routing.requested.",
                "longest_feedback_gap": "Largest gap between acceptance and consecutive effective persisted events.",
            },
            "accept_to_router_start": _latency_summary(
                [
                    _elapsed_ms(row["created_at"], row["route_started_at"])
                    for row in auto_rows
                ]
            ),
            "router_finish_to_agent_start": _latency_summary(
                [
                    _elapsed_ms(row["route_finished_at"], row["agent_started_at"])
                    for row in auto_rows
                ]
            ),
            "first_effective_feedback": _latency_summary(first_feedback),
            "longest_feedback_gap": _latency_summary(longest_silence),
            "accept_to_terminal": _latency_summary(
                [
                    _elapsed_ms(row["created_at"], row["agent_finished_at"])
                    for row in auto_rows
                ]
            ),
        },
        "provider_cost_units": {
            "basis": "request and token counts only; no currency conversion",
            "request_count": sum(row["route_provider_requests"] for row in auto_rows),
            "agent_model_request_count": agent_model_requests,
            "unknown_call_count": sum(
                row["unknown_cost_attempts"] for row in auto_rows
            ),
            "tokens": {
                "input": sum(row["route_input_tokens"] for row in auto_rows),
                "output": sum(row["route_output_tokens"] for row in auto_rows),
                "total": sum(row["route_total_tokens"] for row in auto_rows),
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(args.database)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
