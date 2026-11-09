#!/usr/bin/env python3
"""Report a body-free task preparation and execution funnel from Kernel v34."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


SCHEMA_VERSION = 34
DISCOVERY_TOOLS = {
    "get_data_inventory",
    "discover_datasets",
    "discover_datasets",
    "retrieve_evidence",
    "lookup_knowledge",
    "list_operators",
}
INSPECTION_TOOLS = {
    "inspect_data_version",
    "inspect_data_table",
    "read_evidence",
    "get_operator",
}


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 6)


def build_report(database: Path) -> dict[str, object]:
    uri = database.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        versions = connection.execute("SELECT version FROM schema_version").fetchall()
        if len(versions) != 1 or versions[0]["version"] != SCHEMA_VERSION:
            raise ValueError("KERNEL_SCHEMA_VERSION_UNSUPPORTED")
        runs = connection.execute(
            """SELECT a.agent_run_id,a.status,r.dialogue_policy,r.resolved_mode
               FROM agent_runs AS a
               LEFT JOIN message_requests AS r ON r.agent_run_id=a.agent_run_id"""
        ).fetchall()
        preparations = connection.execute(
            "SELECT agent_run_id,status,snapshot_json FROM task_preparations"
        ).fetchall()
        action_runs = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT agent_run_id FROM agent_actions"
            )
        }
        confirmation_rows = connection.execute(
            "SELECT agent_run_id,status FROM confirmations"
        ).fetchall()
        workflow_rows = connection.execute(
            "SELECT agent_run_id,status FROM workflow_runs"
        ).fetchall()
        activities = connection.execute(
            """SELECT json_extract(payload_json,'$.agent_run_id') AS agent_run_id,
                      json_extract(payload_json,'$.tool_name') AS tool_name,
                      json_extract(payload_json,'$.status') AS status
               FROM session_events WHERE event_type='agent.activity'
                 AND json_extract(payload_json,'$.kind')='TOOL'"""
        ).fetchall()

    preparation_by_run = {
        row["agent_run_id"]: json.loads(row["snapshot_json"]) for row in preparations
    }
    relevant = {
        row["agent_run_id"]
        for row in runs
        if row["dialogue_policy"] == "EXECUTION_PROPOSAL"
        or row["resolved_mode"] == "WORKFLOW_DRAFT"
        or row["agent_run_id"] in preparation_by_run
        or row["agent_run_id"] in action_runs
    }
    completed_tools: dict[str, set[str]] = {}
    for row in activities:
        if (
            row["agent_run_id"] in relevant
            and row["status"] == "COMPLETE"
            and isinstance(row["tool_name"], str)
        ):
            completed_tools.setdefault(row["agent_run_id"], set()).add(row["tool_name"])
    confirmations_by_status: dict[str, set[str]] = {}
    for row in confirmation_rows:
        confirmations_by_status.setdefault(row["status"], set()).add(
            row["agent_run_id"]
        )
    workflows_by_status: dict[str, set[str]] = {}
    for row in workflow_rows:
        workflows_by_status.setdefault(row["status"], set()).add(row["agent_run_id"])
    prepared = set(preparation_by_run) & relevant
    ready = {
        run_id for run_id in prepared if preparation_by_run[run_id]["status"] == "READY"
    }
    blocked = prepared - ready
    stages = {
        "relevant_runs": len(relevant),
        "discovery_completed": sum(
            bool(tools & DISCOVERY_TOOLS) for tools in completed_tools.values()
        ),
        "inspection_completed": sum(
            bool(tools & INSPECTION_TOOLS) for tools in completed_tools.values()
        ),
        "explicit_preparation_completed": sum(
            "prepare_task_inputs" in tools for tools in completed_tools.values()
        ),
        "durable_preparation": len(prepared),
        "ready_preparation": len(ready),
        "blocked_preparation": len(blocked),
        "action_frozen": len(action_runs & relevant),
        "confirmation_created": len(
            {row["agent_run_id"] for row in confirmation_rows} & relevant
        ),
        "confirmation_confirmed": len(
            confirmations_by_status.get("CONFIRMED", set()) & relevant
        ),
        "confirmation_rejected": len(
            confirmations_by_status.get("REJECTED", set()) & relevant
        ),
        "workflow_succeeded": len(
            workflows_by_status.get("SUCCEEDED", set()) & relevant
        ),
        "workflow_failed": len(workflows_by_status.get("FAILED", set()) & relevant),
        "agent_complete": sum(
            row["agent_run_id"] in relevant and row["status"] == "COMPLETE"
            for row in runs
        ),
        "agent_failed": sum(
            row["agent_run_id"] in relevant and row["status"] == "FAILED"
            for row in runs
        ),
    }
    blockers = Counter(
        reason
        for run_id in blocked
        for entry in preparation_by_run[run_id]["entries"]
        for reason in entry["blocking_reasons"]
    )
    targets = Counter(
        entry["target_id"]
        for run_id in prepared
        for entry in preparation_by_run[run_id]["entries"]
    )
    denominator = stages["relevant_runs"]
    return {
        "schemaVersion": "task-success-operations-report.v1",
        "generated_at": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "database_schema_version": SCHEMA_VERSION,
        "scope": {
            "definition": (
                "execution proposals, workflow drafts, or runs with durable "
                "preparation/actions"
            ),
            "accuracy_claim": False,
        },
        "stages": stages,
        "rates": {
            key: _ratio(value, denominator)
            for key, value in stages.items()
            if key != "relevant_runs"
        },
        "blocking_reasons": dict(sorted(blockers.items())),
        "prepared_targets": dict(sorted(targets.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    encoded = (
        json.dumps(build_report(args.database), ensure_ascii=False, indent=2) + "\n"
    )
    if args.output is None:
        print(encoded, end="")
    else:
        args.output.write_text(encoded, encoding="utf-8")


if __name__ == "__main__":
    main()
