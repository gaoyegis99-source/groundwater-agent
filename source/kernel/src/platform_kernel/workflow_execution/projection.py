"""Read-only task metadata in the caller's Session-history snapshot."""

import re
import sqlite3

from ..database import read_session_events
from .persistence import communication_error_code


def session_execution_tasks(
    connection: sqlite3.Connection, session_id: str, project_id: str,
    agent_run_ids: tuple[str, ...],
) -> dict[str, list[dict]]:
    if not agent_run_ids:
        return {}
    placeholders = ",".join("?" for _ in agent_run_ids)
    rows = connection.execute(
        """SELECT w.run_id AS workflow_run_id, w.agent_run_id, w.tool_call_id,
                  w.position, w.confirmation_id, w.attempt,
                  w.retry_of_workflow_run_id, w.model_id, w.model_version, w.workflow_id,
                  w.workflow_version, w.status, w.created_at, w.started_at,
                  w.finished_at, w.duration_ms, w.error,
                  json_array_length(w.outputs_json) AS output_count,
                  d.last_communication_error
           FROM workflow_runs w
           JOIN workflow_dispatches d ON d.workflow_run_id = w.run_id
           WHERE w.session_id = ? AND w.project_id = ?
             AND w.agent_run_id IN (""" + placeholders + ") ORDER BY w.position, w.attempt",
        (session_id, project_id, *agent_run_ids),
    ).fetchall()
    stages = {
        event["data"]["workflow_run_id"]: event["data"]["stage"]
        for event in read_session_events(connection, session_id, "workflow.stage_changed")
    }
    blocked = {
        event["data"]["workflow_run_id"]: event["data"]["blocked_by_workflow_run_id"]
        for event in read_session_events(connection, session_id, "workflow.failed")
        if "blocked_by_workflow_run_id" in event["data"]
    }
    tasks: dict[str, list[dict]] = {}
    for row in rows:
        task = {key: row[key] for key in (
            "workflow_run_id", "agent_run_id", "tool_call_id", "position", "model_id",
            "model_version", "workflow_id", "workflow_version", "status", "created_at",
            "started_at", "finished_at", "duration_ms", "output_count",
            "confirmation_id", "attempt", "retry_of_workflow_run_id",
        )}
        latest = connection.execute(
            "SELECT MAX(attempt) FROM workflow_runs WHERE agent_run_id=? AND tool_call_id=?",
            (row["agent_run_id"], row["tool_call_id"]),
        ).fetchone()[0]
        task["is_latest_attempt"] = row["attempt"] == latest
        task["stage"] = (
            "QUEUED" if row["status"] == "QUEUED" else stages.get(row["workflow_run_id"])
        ) if row["status"] in {"QUEUED", "RUNNING"} else None
        error = row["error"]
        task["error_code"] = error if error is None or re.fullmatch(r"[A-Z][A-Z0-9_]*", error) else "WORKFLOW_FAILED"
        task["communication_error"] = communication_error_code(row["last_communication_error"])
        task["blocked_by_workflow_run_id"] = blocked.get(row["workflow_run_id"])
        tasks.setdefault(row["agent_run_id"], []).append(task)
    return tasks
