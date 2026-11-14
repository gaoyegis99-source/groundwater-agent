"""Session-owned immutable authoring drafts and their validated definitions."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from ..database import append_event, connect_database, read_session_events, utc_now
from ..errors import ConfirmationConflictError, ResourceNotFoundError
from .persistence import reserve_turn_connection


def create_workflow_draft(database: Path, session_id: str, project_id: str,
                          request_id: str, message: str) -> str:
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _require_session(connection, session_id, project_id)
        existing = connection.execute(
            "SELECT * FROM workflow_drafts WHERE session_id=? AND request_id=?",
            (session_id, request_id),
        ).fetchone()
        if existing is not None:
            if existing["message"] != message:
                raise ConfirmationConflictError("WORKFLOW_DRAFT_REQUEST_CONFLICT")
            return existing["draft_id"]
        draft_id, run_id = str(uuid4()), str(uuid4())
        reserve_turn_connection(
            connection, session_id, message, run_id, project_id,
            mode="WORKFLOW_DRAFT", workflow_draft_id=draft_id,
        )
        revision = _require_session(connection, session_id, project_id)["goal_revision"]
        connection.execute(
            "INSERT INTO workflow_drafts VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
            (draft_id, session_id, request_id, message, revision, run_id, utc_now()),
        )
        append_event(connection, session_id, "workflow_draft.requested", {
            "workflow_draft_id": draft_id, "agent_run_id": run_id, "goal_revision": revision,
        })
        return draft_id


def get_workflow_draft(database: Path, session_id: str, project_id: str, draft_id: str) -> dict:
    with connect_database(database) as connection:
        connection.execute("BEGIN")
        _require_session(connection, session_id, project_id)
        row = connection.execute(
            "SELECT d.*, a.status, a.error FROM workflow_drafts d LEFT JOIN agent_runs a "
            "ON a.agent_run_id=d.agent_run_id WHERE d.session_id=? AND d.draft_id=?",
            (session_id, draft_id),
        ).fetchone()
        if row is None:
            raise ResourceNotFoundError("WORKFLOW_DRAFT_NOT_FOUND")
        failures = read_session_events(connection, session_id, "agent.failed", agent_run_ids=(row["agent_run_id"],))
        diagnostics = None if not failures else failures[-1]["data"].get("diagnostics")
        if diagnostics is not None and diagnostics.get("kind") != "WORKFLOW_VALIDATION":
            diagnostics = None
        payload = None if row["payload_json"] is None else json.loads(row["payload_json"])
        status = row["status"]
        if payload is not None and payload.get("execution_allowed") is True:
            status = "COMPLETE"
        elif status in {None, "RUNNING"}:
            status = "GENERATING"
        return {
            "schemaVersion": "workflow-draft-resource.v1", "draft_id": row["draft_id"],
            "session_id": session_id, "goal_revision": row["goal_revision"],
            "agent_run_id": row["agent_run_id"],
            "status": status,
            "error_code": row["error"] if status != "COMPLETE" else None,
            "diagnostics": diagnostics,
            "payload": payload,
        }


def frozen_composition(connection, agent_run_id: str, session_id: str, project_id: str) -> dict | None:
    row = connection.execute(
        """SELECT d.payload_json FROM workflow_drafts d JOIN sessions s ON s.session_id=d.session_id
           WHERE d.agent_run_id=? AND d.session_id=? AND s.project_id=?""",
        (agent_run_id, session_id, project_id),
    ).fetchone()
    if row is None or row["payload_json"] is None:
        return None
    draft = json.loads(row["payload_json"])
    return draft.get("composition") if draft.get("execution_allowed") is True else None


def _require_session(connection, session_id, project_id):
    row = connection.execute(
        "SELECT * FROM sessions WHERE session_id=? AND project_id=?", (session_id, project_id),
    ).fetchone()
    if row is None:
        raise ResourceNotFoundError("SESSION_NOT_FOUND")
    return row
