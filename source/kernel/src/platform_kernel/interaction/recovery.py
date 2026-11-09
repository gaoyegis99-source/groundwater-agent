from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from ..application.task_api import bind_confirmation
from ..database import append_event, connect_database, utc_now
from ..errors import ConfirmationConflictError, ResourceNotFoundError
from .actors import DEFAULT_LOCAL_ACTOR_SUBJECT
from .state_projection import confirmation_from_row


def create_recovery_confirmation(
    database: Path,
    *,
    session_id: str,
    workflow_run_id: str,
    project_id: str,
    request_id: str,
    expected_goal_revision: int,
    source: dict[str, object] | None,
    decision_subject: str = DEFAULT_LOCAL_ACTOR_SUBJECT,
):
    request_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "workflow_run_id": workflow_run_id,
                "expected_goal_revision": expected_goal_revision,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT * FROM confirmations WHERE session_id=? AND request_id=?",
            (session_id, request_id),
        ).fetchone()
        if existing is not None:
            if existing["request_fingerprint"] != request_fingerprint:
                raise ConfirmationConflictError("WORKFLOW_RECOVERY_CONFLICT")
            return confirmation_from_row(connection, existing)
        session = connection.execute(
            "SELECT * FROM sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if session is None or session["project_id"] != project_id:
            raise ResourceNotFoundError("SESSION_NOT_FOUND")
        if session["goal_revision"] != expected_goal_revision:
            raise ConfirmationConflictError("WORKFLOW_RECOVERY_NOT_CURRENT")
        if source is None or source["agent_run_id"] != session["agent_run_id"]:
            raise ConfirmationConflictError("WORKFLOW_RECOVERY_NOT_ALLOWED")
        pending = connection.execute(
            "SELECT 1 FROM confirmations WHERE agent_run_id=? AND status='PENDING'",
            (source["agent_run_id"],),
        ).fetchone()
        if pending is not None:
            raise ConfirmationConflictError("WORKFLOW_RECOVERY_CONFLICT")
        source_actions = connection.execute(
            """SELECT ca.* FROM confirmation_actions ca
               WHERE ca.confirmation_id=? ORDER BY ca.position""",
            (source["confirmation_id"],),
        ).fetchall()
        selected_ids = tuple(source["tool_call_ids"])
        actions = [
            action
            for action in source_actions
            if action["tool_call_id"] in selected_ids
        ]
        actions.sort(key=lambda action: selected_ids.index(action["tool_call_id"]))
        if not actions:
            raise ConfirmationConflictError("WORKFLOW_RECOVERY_NOT_ALLOWED")
        confirmation_id = str(uuid4())
        revision = connection.execute(
            "SELECT COALESCE(MAX(revision),0)+1 FROM confirmations WHERE agent_run_id=?",
            (source["agent_run_id"],),
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO confirmations(
                   confirmation_id,session_id,goal_revision,agent_run_id,status,reason,
                   decision,decision_state,revision,kind,replaces_confirmation_id,
                   source_workflow_run_id,request_id,request_fingerprint,created_at
               ) VALUES(?,?,?,?, 'PENDING',NULL,NULL,NULL,?,'RECOVERY',NULL,?,?,?,?)""",
            (
                confirmation_id,
                session_id,
                expected_goal_revision,
                source["agent_run_id"],
                revision,
                workflow_run_id,
                request_id,
                request_fingerprint,
                utc_now(),
            ),
        )
        for position, action in enumerate(actions):
            values = tuple(
                action[key]
                for key in (
                    "tool_call_id",
                    "tool",
                    "model_id",
                    "model_version",
                    "model_checksum",
                    "workflow_id",
                    "workflow_version",
                    "workflow_checksum",
                    "input_schema_checksum",
                    "fingerprint",
                    "arguments_json",
                    "input_references_json",
                )
            )
            connection.execute(
                "INSERT INTO confirmation_actions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (confirmation_id, position, *values),
            )
        bind_confirmation(connection, confirmation_id, session_id)
        append_event(
            connection,
            session_id,
            "confirmation.recovery_created",
            {
                "confirmation_id": confirmation_id,
                "source_workflow_run_id": workflow_run_id,
                "revision": revision,
                "status": "PENDING",
                "decision_subject": decision_subject,
            },
        )
        row = connection.execute(
            "SELECT * FROM confirmations WHERE confirmation_id=?",
            (confirmation_id,),
        ).fetchone()
        return confirmation_from_row(connection, row)
