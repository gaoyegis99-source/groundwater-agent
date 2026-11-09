from __future__ import annotations

import json
import logging
from pathlib import Path
import sqlite3

from ...application.ports import AgentProjectionPort
from ...database import connect_database
from ...errors import ResourceNotFoundError
from ..models import AgentRun
from .projection import action_status, project_run
from .types import ReplayedAction


_LOGGER = logging.getLogger(__name__)


def execution_reply_facts(database: Path, agent_run_id: str) -> dict[str, object]:
    """Project the current run's execution identity for terminal rendering."""
    with connect_database(database) as connection:
        action_count = connection.execute(
            "SELECT COUNT(*) FROM agent_actions WHERE agent_run_id=?",
            (agent_run_id,),
        ).fetchone()[0]
        rows = connection.execute(
            "SELECT run_id FROM workflow_runs WHERE agent_run_id=? ORDER BY attempt,run_id",
            (agent_run_id,),
        ).fetchall()
    return {
        "has_action": action_count > 0,
        "workflow_run_ids": [row["run_id"] for row in rows],
    }


def load_replayed_action(
    database: Path, agent_run_id: str, tool_call_id: str
) -> ReplayedAction | None:
    with connect_database(database) as connection:
        row = connection.execute(
            """SELECT aa.*, wr.run_id AS workflow_run_id,
                      wr.status AS workflow_status, wr.outputs_json,
                      wr.arguments_json AS executed_arguments_json,
                      wr.error AS workflow_error
               FROM agent_actions AS aa
               LEFT JOIN workflow_runs AS wr ON wr.agent_run_id = aa.agent_run_id
                 AND wr.tool_call_id = aa.tool_call_id
                 AND wr.attempt = (SELECT MAX(w2.attempt) FROM workflow_runs w2
                                   WHERE w2.agent_run_id=aa.agent_run_id
                                     AND w2.tool_call_id=aa.tool_call_id)
               WHERE aa.agent_run_id = ? AND aa.tool_call_id = ?""",
            (agent_run_id, tool_call_id),
        ).fetchone()
    if row is None:
        return None
    return ReplayedAction(
        arguments=json.loads(row["arguments_json"]),
        executed_arguments=(None if row["executed_arguments_json"] is None else
                            json.loads(row["executed_arguments_json"])),
        status=action_status(row["status"], row["workflow_status"]),
        workflow_run_id=row["workflow_run_id"],
        outputs=json.loads(row["outputs_json"] or "[]"),
        error=row["workflow_error"] if row["workflow_status"] else row["error"],
    )


def agent_run_blocks_turn(
    connection: sqlite3.Connection, agent_run_id: str
) -> bool:
    row = connection.execute(
        "SELECT status FROM agent_runs WHERE agent_run_id = ?",
        (agent_run_id,),
    ).fetchone()
    return row["status"] in {"RUNNING", "WAITING_EXECUTION"}


def get_run(database: Path, agent_run_id: str, project_id: str | None = None) -> AgentRun:
    with connect_database(database) as connection:
        return get_run_connection(connection, agent_run_id, project_id)


def get_run_connection(
    connection: sqlite3.Connection,
    agent_run_id: str,
    project_id: str | None = None,
) -> AgentRun:
    """Read one AgentRun inside the caller's existing SQLite snapshot."""
    query = "SELECT * FROM agent_runs WHERE agent_run_id = ?"
    arguments: tuple[object, ...] = (agent_run_id,)
    if project_id is not None:
        query += " AND project_id = ?"
        arguments = (agent_run_id, project_id)
    row = connection.execute(query, arguments).fetchone()
    if row is None:
        raise ResourceNotFoundError("AGENT_RUN_NOT_FOUND")
    return project_run(connection, row)


def find_run(database: Path, agent_run_id: str) -> AgentRun | None:
    try:
        return get_run(database, agent_run_id)
    except ResourceNotFoundError:
        return None


def trace_id_for_run(database: Path, agent_run_id: str) -> str | None:
    """Return the Agent-owned trace identity without exposing Agent storage."""

    with connect_database(database) as connection:
        row = connection.execute(
            "SELECT trace_id FROM agent_runs WHERE agent_run_id=?",
            (agent_run_id,),
        ).fetchone()
    return None if row is None else str(row["trace_id"])


def kernel_case_state(database: Path, agent_run_id: str) -> dict[str, object] | None:
    with connect_database(database) as connection:
        row = connection.execute(
            "SELECT state_json FROM agent_run_state WHERE agent_run_id=?",
            (agent_run_id,),
        ).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row["state_json"])
    except (TypeError, json.JSONDecodeError):
        # A malformed generic Agent checkpoint is handled by claim_state(), which
        # converts it into the stable AGENT_RUN_STATE_INVALID terminal failure.
        # Case detection must not bypass that recovery boundary.
        return None
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "kernel-case-state.v1"
        or not isinstance(value.get("case_id"), str)
    ):
        return None
    return value


def runs_with_status(database: Path, *statuses: str) -> tuple[AgentRun, ...]:
    if not statuses:
        return ()
    placeholders = ",".join("?" for _ in statuses)
    with connect_database(database) as connection:
        rows = connection.execute(
            f"SELECT * FROM agent_runs WHERE status IN ({placeholders}) ORDER BY rowid",
            statuses,
        ).fetchall()
        return tuple(project_run(connection, row) for row in rows)


def _project_current(
    connection: sqlite3.Connection,
    agent_run_id: str,
    agent_projection: AgentProjectionPort,
) -> None:
    # An in-progress confirmation decision projects its next batch or final
    # answer in finalize_decision(), together with that decision's status.
    decision = connection.execute(
        """SELECT 1 FROM confirmations WHERE agent_run_id = ?
           AND status = 'PENDING' AND decision_state = 'RUNNING' LIMIT 1""",
        (agent_run_id,),
    ).fetchone()
    if decision is not None:
        return
    row = connection.execute(
        "SELECT * FROM agent_runs WHERE agent_run_id = ?", (agent_run_id,)
    ).fetchone()
    if row is None:
        raise ValueError("AGENT_RUN_MISSING")
    agent_projection.project_agent_run(connection, project_run(connection, row))


def load_request_context(database: Path, agent_run_id: str) -> dict:
    with connect_database(database) as connection:
        row = connection.execute(
            """SELECT a.*,
                      COALESCE(m.clarification_required, 0)
                      AS clarification_required,
                      COALESCE(m.input_references_json, '[]') AS input_references_json
                 FROM agent_requests AS a
                 LEFT JOIN message_requests AS m
                   ON m.agent_run_id = a.agent_run_id
                WHERE a.agent_run_id = ?""",
            (agent_run_id,),
        ).fetchone()
        # Direct AgentRuntime invocations are ordinary dialogue. Session requests
        # reserve their explicit mode before any asynchronous compute can start.
        if row is None:
            return {
                "mode": "DIALOGUE",
                "dialogue_policy": "READ_ONLY",
                "clarification_only": False,
            }
        context = dict(row)
        context["clarification_only"] = bool(
            context.pop("clarification_required")
        )
        context["input_references"] = json.loads(
            context.pop("input_references_json")
        )
        return context


def ensure_dialogue_request(
    database: Path, agent_run_id: str, dialogue_policy: str,
) -> None:
    if dialogue_policy not in {"READ_ONLY", "EXECUTION_PROPOSAL"}:
        raise ValueError("AGENT_REQUEST_MODE_INVALID")
    with connect_database(database) as connection:
        row = connection.execute(
            "SELECT mode,dialogue_policy FROM agent_requests WHERE agent_run_id=?",
            (agent_run_id,),
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO agent_requests VALUES (?, 'DIALOGUE', NULL, ?)",
                (agent_run_id, dialogue_policy),
            )
