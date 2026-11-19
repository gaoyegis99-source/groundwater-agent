from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from ..application.query_dtos import AgentRunView
from ..contracts import DataVersionRefV2
from ..database import append_event, connect_database, utc_now
from .models import Confirmation, ConfirmationAction
from .output_steps import freeze_output_steps
from ..application.task_api import bind_confirmation


def apply_agent_run(database: Path, run: AgentRunView, *, models=(), output_steps=()) -> bool:
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        return project_agent_run(connection, run, models=models, output_steps=output_steps)


def project_agent_run(connection: sqlite3.Connection, run: AgentRunView, *, models=(), output_steps=()) -> bool:
    session = connection.execute(
        "SELECT * FROM sessions WHERE session_id = ?", (run.session_id,)
    ).fetchone()
    if session is None or session["goal_revision"] != run.goal_revision:
        return False
    connection.execute(
        """UPDATE sessions SET agent_run_id = ?, pending_agent_run_id = NULL,
               agent_start_state = NULL WHERE session_id = ?""",
        (run.agent_run_id, run.session_id),
    )
    if run.output is not None:
        existing = connection.execute(
            """SELECT 1 FROM messages
               WHERE session_id = ? AND goal_revision = ? AND role = 'assistant'""",
            (run.session_id, run.goal_revision),
        ).fetchone()
        if existing is None:
            connection.execute(
                """INSERT INTO messages(session_id, goal_revision, role, content)
                   VALUES (?, ?, 'assistant', ?)""",
                (run.session_id, run.goal_revision, run.output.message),
            )
    if run.status == "WAITING_CONFIRMATION" and any(
        action.status == "PENDING" for action in run.actions
    ):
        ensure_confirmation(connection, run, models=models, output_steps=output_steps)
    return True


def upsert_assistant_message(
    connection: sqlite3.Connection,
    session_id: str,
    goal_revision: int,
    content: str,
) -> None:
    """Replace the Turn reply or create it while retaining interaction ownership."""
    updated = connection.execute(
        """UPDATE messages SET content=?
           WHERE session_id=? AND goal_revision=? AND role='assistant'""",
        (content, session_id, goal_revision),
    ).rowcount
    if updated == 0:
        connection.execute(
            """INSERT INTO messages(session_id,goal_revision,role,content)
               VALUES(?,?,'assistant',?)""",
            (session_id, goal_revision, content),
        )


def activity_counts(
    connection: sqlite3.Connection,
    session_id: str,
    agent_run_id: str,
) -> tuple[int, int]:
    """Count persisted Provider and observation activities without exposing bodies."""
    rows = connection.execute(
        """SELECT json_extract(payload_json,'$.kind') AS kind, COUNT(*) AS total
           FROM session_events
           WHERE session_id=? AND event_type='agent.activity'
             AND json_extract(payload_json,'$.agent_run_id')=?
             AND json_extract(payload_json,'$.kind') IN ('MODEL_REQUEST','TOOL')
           GROUP BY kind""",
        (session_id, agent_run_id),
    ).fetchall()
    counts = {row["kind"]: row["total"] for row in rows}
    return counts.get("MODEL_REQUEST", 0), counts.get("TOOL", 0)


def ensure_confirmation(connection: sqlite3.Connection, run: AgentRunView, *, models=(), output_steps=()) -> str:
    actions = tuple(action for action in run.actions if action.status == "PENDING")
    action_ids = tuple(action.tool_call_id for action in actions)
    rows = connection.execute(
        """SELECT confirmation_id FROM confirmations
           WHERE session_id = ? AND goal_revision = ? AND status = 'PENDING'
           ORDER BY rowid DESC""",
        (run.session_id, run.goal_revision),
    ).fetchall()
    for row in rows:
        if confirmation_action_ids(connection, row["confirmation_id"]) == action_ids:
            return row["confirmation_id"]
    confirmation_id = str(uuid4())
    connection.execute(
        """INSERT INTO confirmations(
               confirmation_id, session_id, goal_revision, agent_run_id,
               status, reason, decision, decision_state, revision, kind,
               replaces_confirmation_id, source_workflow_run_id, request_id,
               request_fingerprint, created_at
           ) VALUES (?, ?, ?, ?, 'PENDING', NULL, NULL, NULL, 1, 'INITIAL',
                     NULL, NULL, NULL, NULL, ?)""",
        (confirmation_id, run.session_id, run.goal_revision, run.agent_run_id, utc_now()),
    )
    bind_confirmation(connection, confirmation_id, run.session_id)
    for position, action in enumerate(actions):
        connection.execute(
            """INSERT INTO confirmation_actions(
                   confirmation_id, position, tool_call_id, tool,
                   model_id, model_version, model_checksum,
                   workflow_id, workflow_version, workflow_checksum,
                   input_schema_checksum, fingerprint, arguments_json,
                   input_references_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                confirmation_id,
                position,
                action.tool_call_id,
                action.tool,
                action.model_id,
                action.model_version,
                action.model_checksum,
                action.workflow_id,
                action.workflow_version,
                action.workflow_checksum,
                action.schema_checksum,
                action.fingerprint,
                json.dumps(
                    action.arguments,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                json.dumps(
                    {name: reference.as_storage_dict() for name, reference in action.input_references.items()},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )
    freeze_output_steps(connection, confirmation_id, actions, models, output_steps)
    append_event(
        connection,
        run.session_id,
        "confirmation.pending",
        {"confirmation_id": confirmation_id, "agent_run_id": run.agent_run_id},
    )
    return confirmation_id


def confirmation_action_ids(
    connection: sqlite3.Connection, confirmation_id: str
) -> tuple[str, ...]:
    rows = connection.execute(
        """SELECT tool_call_id FROM confirmation_actions
           WHERE confirmation_id = ? ORDER BY position""",
        (confirmation_id,),
    ).fetchall()
    return tuple(row["tool_call_id"] for row in rows)


def confirmation_from_row(connection, row) -> Confirmation:
    actions = connection.execute(
        """SELECT * FROM confirmation_actions
           WHERE confirmation_id = ? ORDER BY position""",
        (row["confirmation_id"],),
    ).fetchall()
    decision = connection.execute(
        """SELECT created_at FROM session_events
           WHERE session_id=? AND event_type='confirmation.processing'
             AND json_extract(payload_json,'$.confirmation_id')=?
           ORDER BY event_id LIMIT 1""",
        (row["session_id"], row["confirmation_id"]),
    ).fetchone() if row["decision"] is not None else None
    return Confirmation(
        confirmation_id=row["confirmation_id"],
        goal_revision=row["goal_revision"],
        status=row["status"],
        actions=tuple(
            ConfirmationAction(
                tool_call_id=item["tool_call_id"],
                tool=item["tool"],
                model_id=item["model_id"],
                model_version=item["model_version"],
                model_checksum=item["model_checksum"],
                workflow_id=item["workflow_id"],
                workflow_version=item["workflow_version"],
                workflow_checksum=item["workflow_checksum"],
                input_schema_checksum=item["input_schema_checksum"],
                fingerprint=item["fingerprint"],
                arguments=json.loads(item["arguments_json"]),
                input_references={
                    name: DataVersionRefV2.from_storage_dict(reference)
                    for name, reference in json.loads(item["input_references_json"]).items()
                },
            )
            for item in actions
        ),
        agent_run_id=row["agent_run_id"],
        reason=row["reason"],
        revision=row["revision"],
        kind=row["kind"],
        replaces_confirmation_id=row["replaces_confirmation_id"],
        source_workflow_run_id=row["source_workflow_run_id"],
        request_id=row["request_id"],
        created_at=row["created_at"],
        decision_requested_at=decision["created_at"] if decision else None,
        published_output_steps=tuple(
            {
                "tool_call_id": step["tool_call_id"],
                "source_tool_call_id": step["source_tool_call_id"],
                **{key: plan[key] for key in ("model_id", "output", "outputs", "arguments")},
                **({"error_code": step["error_code"]} if step["error_code"] else {}),

            }
            for step in connection.execute(
                """SELECT p.*, (SELECT json_extract(e.payload_json,'$.error_code')
                                FROM session_events e
                                WHERE e.session_id=? AND e.event_type='workflow.output_step_blocked'
                                  AND json_extract(e.payload_json,'$.tool_call_id')=p.tool_call_id
                                  AND json_extract(e.payload_json,'$.confirmation_id')=p.confirmation_id
                                ORDER BY e.event_id DESC LIMIT 1) AS error_code
                   FROM published_output_steps p WHERE p.confirmation_id=? ORDER BY p.rowid""",
                (row["session_id"], row["confirmation_id"]),
            )
            for plan in (json.loads(step["plan_json"]),)
        ),
    )
