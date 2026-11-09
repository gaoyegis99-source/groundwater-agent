"""Shared message-request records, validation, and row projections."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3

from ..database import append_event
from ..errors import ConfirmationConflictError, ResourceNotFoundError

EXPLICIT_MODES = frozenset({"DIALOGUE", "WORKFLOW_DRAFT"})
INTENT_DECISIONS = frozenset({
    "DIALOGUE", "WORKFLOW_DRAFT", "CONTROL", "NEEDS_CLARIFICATION",
})


@dataclass(frozen=True)
class IntentRouteRequest:
    request_id: str
    session_id: str
    project_id: str
    agent_run_id: str
    goal_revision: int
    message: str
    input_references: tuple[dict[str, object], ...]
    route_attempt: int


def materialize_workflow_draft(
    connection: sqlite3.Connection, *, session_id: str, request_id: str,
    message: str, goal_revision: int, agent_run_id: str,
    workflow_draft_id: str | None, created_at: str,
) -> None:
    if workflow_draft_id is None:
        return
    connection.execute(
        "INSERT INTO workflow_drafts VALUES (?,?,?,?,?,?,NULL,?)",
        (workflow_draft_id, session_id, request_id, message, goal_revision,
         agent_run_id, created_at),
    )
    append_event(connection, session_id, "workflow_draft.requested", {
        "workflow_draft_id": workflow_draft_id, "agent_run_id": agent_run_id,
        "goal_revision": goal_revision,
    })


def supersede_pending_confirmation(connection, session_id: str) -> None:
    connection.execute(
        """UPDATE confirmations SET decision='SUPERSEDE',decision_state='REQUESTED'
           WHERE session_id=? AND status='PENDING' AND decision IS NULL""",
        (session_id,),
    )


def require_request_shape(requested_mode: str) -> None:
    if requested_mode not in EXPLICIT_MODES | {"AUTO"}:
        raise ValueError("MESSAGE_REQUEST_MODE_INVALID")


def require_same_request(
    row, message: str, requested_mode: str, corrects_request_id: str | None,
    input_references_json: str,
) -> None:
    if (
        row["message"], row["requested_mode"], row["corrects_request_id"],
        row["input_references_json"],
    ) != (
        message, requested_mode, corrects_request_id, input_references_json,
    ):
        raise ConfirmationConflictError("MESSAGE_REQUEST_CONFLICT")


def require_correction(
    connection: sqlite3.Connection, session_id: str, request_id: str,
    message: str, requested_mode: str, corrects_request_id: str | None,
) -> None:
    if corrects_request_id is None:
        return
    if corrects_request_id == request_id or requested_mode not in EXPLICIT_MODES:
        raise ConfirmationConflictError("MESSAGE_CORRECTION_INVALID")
    original = connection.execute(
        "SELECT * FROM message_requests WHERE session_id=? AND request_id=?",
        (session_id, corrects_request_id),
    ).fetchone()
    if (
        original is None or original["requested_mode"] != "AUTO"
        or original["intent_decision"] is None or original["routing_status"] == "FAILED"
        or (original["intent_decision"] != "NEEDS_CLARIFICATION"
            and original["intent_decision"] == requested_mode)
        or original["message"] != message
    ):
        raise ConfirmationConflictError("MESSAGE_CORRECTION_INVALID")


def session_row(connection: sqlite3.Connection, session_id: str, project_id: str):
    row = connection.execute(
        "SELECT * FROM sessions WHERE session_id=?", (session_id,)
    ).fetchone()
    if row is None or row["project_id"] != project_id:
        raise ResourceNotFoundError("SESSION_NOT_FOUND")
    return row


def route_from_row(row) -> IntentRouteRequest:
    return IntentRouteRequest(
        request_id=row["request_id"], session_id=row["session_id"],
        project_id=row["project_id"], agent_run_id=row["agent_run_id"],
        goal_revision=row["goal_revision"], message=row["message"],
        input_references=tuple(json.loads(row["input_references_json"])),
        route_attempt=row["route_attempts"],
    )


def response_from_row(row) -> dict[str, object]:
    intent = None if row["intent_decision"] is None else {
        "schemaVersion": "intent-decision.v4",
        "responseKind": row["intent_decision"],
        "semanticIntent": row["semantic_intent"],
        "taskAction": row["task_action"],
        "dialoguePolicy": row["dialogue_policy"],
        "controlIntent": row["control_intent"],
        "clarificationReason": row["clarification_reason"],
    }
    return {
        "schemaVersion": "message-request.v5", "request": normalized_request(row),
        "request_id": row["request_id"], "session_id": row["session_id"],
        "goal_revision": row["goal_revision"], "agent_run_id": row["agent_run_id"],
        "requested_mode": row["requested_mode"], "routing_source": row["routing_source"],
        "routing_status": row["routing_status"], "intent_decision": row["intent_decision"],
        "intent": intent,
        "semantic_intent": row["semantic_intent"],
        "task_action": row["task_action"],
        "control_intent": row["control_intent"],
        "clarification_reason": row["clarification_reason"],
        "dialogue_policy": row["dialogue_policy"],
        "resolved_mode": row["resolved_mode"],
        "clarification_required": bool(row["clarification_required"]),
        "workflow_draft_id": row["workflow_draft_id"],
        "corrects_request_id": row["corrects_request_id"], "error_code": row["error_code"],
    }


def receipt_from_row(row, corrects_agent_run_id=None) -> dict[str, object]:
    response = response_from_row(row)
    response["corrects_agent_run_id"] = corrects_agent_run_id
    if row["requested_mode"] == "AUTO" and row["routing_source"] != "RULE":
        response.update(
            routing_status="ROUTING_REQUESTED", intent_decision=None,
            dialogue_policy=None,
            resolved_mode=None, clarification_required=False,
            workflow_draft_id=None, error_code=None,
        )
    return response


def normalized_request(row) -> dict[str, object]:
    return {
        "request_id": row["request_id"], "message": row["message"],
        "requested_mode": row["requested_mode"],
        "input_references": json.loads(row["input_references_json"]),
        "corrects_request_id": row["corrects_request_id"],
    }
