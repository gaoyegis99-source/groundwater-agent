"""Read-only projections for control proposals and their source messages."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from ..contracts import DataVersionRefV2
from ..database import connect_database
from ..errors import ResourceNotFoundError


def proposal_view(row: sqlite3.Row) -> dict[str, object]:
    return {
        key: row[key]
        for key in (
            "proposal_id",
            "session_id",
            "source_request_id",
            "intent",
            "target_type",
            "target_id",
            "task_revision",
            "status",
            "decision_request_id",
            "created_at",
            "decided_at",
        )
    }


def list_control_proposals_connection(
    connection: sqlite3.Connection,
    session_id: str,
) -> list[dict[str, object]]:
    return [
        proposal_view(row)
        for row in connection.execute(
            "SELECT * FROM control_proposals WHERE session_id=? ORDER BY rowid",
            (session_id,),
        ).fetchall()
    ]


def confirmed_control_proposals(database: Path) -> tuple[dict[str, object], ...]:
    """Return claimed proposals whose durable side effect still needs reconciliation."""

    with connect_database(database) as connection:
        rows = connection.execute(
            """SELECT * FROM control_proposals WHERE status='CONFIRMED'
               ORDER BY rowid"""
        ).fetchall()
    return tuple(proposal_view(row) for row in rows)


def control_status_snapshot(
    connection: sqlite3.Connection, session_id: str
) -> dict[str, object]:
    session = connection.execute(
        "SELECT * FROM sessions WHERE session_id=?",
        (session_id,),
    ).fetchone()
    state = "IDLE"
    waiting_reason = None
    if session["pending_agent_run_id"] is not None:
        state, waiting_reason = "ANALYZING", "ANALYSIS_IN_PROGRESS"
    elif (
        connection.execute(
            "SELECT 1 FROM workflow_runs WHERE session_id=? AND status IN ('QUEUED','RUNNING')",
            (session_id,),
        ).fetchone()
        is not None
    ):
        state, waiting_reason = "EXECUTING", "EXECUTION_IN_PROGRESS"
    elif (
        connection.execute(
            "SELECT 1 FROM confirmations WHERE session_id=? AND status='PENDING'",
            (session_id,),
        ).fetchone()
        is not None
    ):
        state, waiting_reason = "WAITING_CONFIRMATION", "CONFIRMATION_REQUIRED"
    elif session["agent_run_id"] is not None:
        row = connection.execute(
            "SELECT status FROM agent_runs WHERE agent_run_id=?",
            (session["agent_run_id"],),
        ).fetchone()
        if row is not None:
            state = row["status"]
    outputs: dict[str, dict[str, object]] = {}
    for row in connection.execute(
        """SELECT outputs_json FROM workflow_runs
           WHERE session_id=? AND status='SUCCEEDED' ORDER BY rowid""",
        (session_id,),
    ).fetchall():
        for value in json.loads(row["outputs_json"]):
            reference = DataVersionRefV2.from_storage_dict(value).as_storage_dict()
            outputs[reference["version_id"]] = reference
    return {
        "state": state,
        "waiting_reason": waiting_reason,
        "results": list(outputs.values()),
    }


def list_control_messages_connection(
    connection: sqlite3.Connection,
    session_id: str,
) -> list[dict[str, object]]:
    rows = connection.execute(
        """SELECT * FROM message_requests
           WHERE session_id=? AND routing_source='RULE'
             AND routing_status IN ('CONTROL_PENDING','CONTROL_COMPLETE')
           ORDER BY rowid""",
        (session_id,),
    ).fetchall()
    if not rows:
        return []
    snapshot = control_status_snapshot(connection, session_id)
    return [
        {
            "request_id": row["request_id"],
            "message": row["message"],
            "response_kind": row["intent_decision"],
            "semantic_intent": row["semantic_intent"],
            "control_intent": row["control_intent"],
            "clarification_reason": row["clarification_reason"],
            "status_snapshot": snapshot["state"],
            "waiting_reason": snapshot["waiting_reason"],
            "results": snapshot["results"],
            "status": row["routing_status"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def control_source_message(database: Path, proposal_id: str) -> str:
    with connect_database(database) as connection:
        row = connection.execute(
            """SELECT r.message FROM control_proposals p JOIN message_requests r
                 ON r.session_id=p.session_id AND r.request_id=p.source_request_id
               WHERE p.proposal_id=?""",
            (proposal_id,),
        ).fetchone()
    if row is None:
        raise ResourceNotFoundError("CONTROL_PROPOSAL_NOT_FOUND")
    return row["message"]
