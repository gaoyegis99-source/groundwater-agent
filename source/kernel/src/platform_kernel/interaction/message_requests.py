"""Creation and lookup of durable unified message requests."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from ..database import append_event, connect_database, utc_now
from ..errors import ConfirmationConflictError, ResourceNotFoundError
from .control_persistence import validate_correction
from .control_intents import rule_intent
from .control_proposal_commands import create_control_request, create_status_request
from .message_request_common import (
    materialize_workflow_draft,
    receipt_from_row,
    require_correction,
    require_request_shape,
    require_same_request,
    response_from_row,
    session_row,
)
from .persistence import reserve_turn_connection
from .access import require_turn_capacity_connection
from .actors import DEFAULT_LOCAL_ACTOR_SUBJECT


def create_message_request(
    database: Path, session_id: str, project_id: str, request_id: str,
    message: str, requested_mode: str, *, corrects_request_id: str | None,
    input_references: list[dict[str, object]] | None = None,
    corrects_agent_run_id: str | None = None,
    owner_subject: str = DEFAULT_LOCAL_ACTOR_SUBJECT,
    max_pending: int = 108,
) -> tuple[dict[str, object], str | None]:
    normalized_references = tuple(input_references or ())
    references_json = json.dumps(
        normalized_references, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        session = session_row(connection, session_id, project_id)
        existing = connection.execute(
            "SELECT * FROM message_requests WHERE session_id=? AND request_id=?",
            (session_id, request_id),
        ).fetchone()
        validate_correction(connection, session_id, request_id, corrects_agent_run_id, replay=existing is not None)
        if existing is not None:
            require_same_request(
                existing, message, requested_mode, corrects_request_id, references_json
            )
            return receipt_from_row(existing, corrects_agent_run_id), None
        require_request_shape(requested_mode)
        require_correction(
            connection, session_id, request_id, message, requested_mode,
            corrects_request_id,
        )
        fast_intent = (
            rule_intent(message)
            if requested_mode == "AUTO" and not normalized_references
            else None
        )
        pending_control = connection.execute(
            """SELECT 1 FROM control_proposals WHERE session_id=?
               AND status IN ('PENDING','CONFIRMED') LIMIT 1""",
            (session_id,),
        ).fetchone()
        if pending_control is not None and fast_intent != "STATUS_QUERY":
            raise ConfirmationConflictError("CONTROL_PROPOSAL_PENDING")
        if fast_intent == "STATUS_QUERY":
            row = create_status_request(
                connection, session=session, request_id=request_id, message=message,
            )
            return receipt_from_row(row, corrects_agent_run_id), None
        if fast_intent in {
            "APPROVE_CONFIRMATION", "REJECT_CONFIRMATION", "STOP_ANALYSIS",
            "CANCEL_EXECUTION",
        }:
            row = create_control_request(
                connection, session=session, request_id=request_id,
                message=message, intent=fast_intent,
            )
            return receipt_from_row(row, corrects_agent_run_id), None
        require_turn_capacity_connection(
            connection, project_id, owner_subject, session_id, max_pending,
        )
        agent_run_id = str(uuid4())
        workflow_draft_id = str(uuid4()) if requested_mode == "WORKFLOW_DRAFT" else None
        mode = None if requested_mode == "AUTO" else requested_mode
        dialogue_policy = "READ_ONLY" if requested_mode == "DIALOGUE" else None
        superseded = reserve_turn_connection(
            connection, session_id, message, agent_run_id, project_id, mode=mode,
            workflow_draft_id=workflow_draft_id,
            start_state="ROUTING_REQUESTED" if requested_mode == "AUTO" else "REQUESTED",
            request_id=request_id, requested_mode=requested_mode,
            dialogue_policy=dialogue_policy,
            supersede_confirmation=requested_mode != "AUTO",
        )
        goal_revision = session["goal_revision"] + 1
        created_at = utc_now()
        routing_status = "ROUTING_REQUESTED" if requested_mode == "AUTO" else "DECIDED"
        connection.execute(
            """INSERT INTO message_requests(
                   agent_run_id,session_id,request_id,goal_revision,message,input_references_json,
                   requested_mode,routing_source,workflow_draft_id,corrects_request_id,
                   routing_status,intent_decision,dialogue_policy,semantic_intent,
                   task_action,control_intent,clarification_reason,resolved_mode,
                   clarification_required,
                   route_provider,route_model,route_trace_id,route_input_tokens,
                   route_output_tokens,route_total_tokens,route_attempts,
                   route_provider_requests,unknown_cost_attempts,route_started_at,
                   route_finished_at,route_duration_ms,error_code,created_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,NULL,NULL,NULL,0,0,0,0,0,0,
                         NULL,NULL,NULL,NULL,?)""",
            (
                agent_run_id, session_id, request_id, goal_revision, message, references_json,
                requested_mode, "AUTO" if requested_mode == "AUTO" else "EXPLICIT",
                workflow_draft_id, corrects_request_id, routing_status,
                None if requested_mode == "AUTO" else requested_mode,
                dialogue_policy,
                (
                    None if requested_mode == "AUTO"
                    else "CREATE_WORKFLOW" if requested_mode == "WORKFLOW_DRAFT"
                    else "UNSPECIFIED"
                ),
                None if requested_mode == "AUTO" else "NONE",
                None,
                None,
                None if requested_mode == "AUTO" else requested_mode,
                created_at,
            ),
        )
        if requested_mode != "AUTO":
            materialize_workflow_draft(
                connection, session_id=session_id, request_id=request_id,
                message=message, goal_revision=goal_revision,
                agent_run_id=agent_run_id, workflow_draft_id=workflow_draft_id,
                created_at=created_at,
            )
            if corrects_request_id is not None:
                append_event(connection, session_id, "intent_routing.corrected", {
                    "request_id": request_id, "corrects_request_id": corrects_request_id,
                    "agent_run_id": agent_run_id, "goal_revision": goal_revision,
                    "resolved_mode": requested_mode,
                })
        else:
            append_event(connection, session_id, "intent_routing.requested", {
                "request_id": request_id, "agent_run_id": agent_run_id,
                "goal_revision": goal_revision,
            })
        row = connection.execute(
            "SELECT * FROM message_requests WHERE agent_run_id=?", (agent_run_id,)
        ).fetchone()
        return receipt_from_row(row, corrects_agent_run_id), superseded


def message_request(
    database: Path, session_id: str, project_id: str, request_id: str,
) -> dict[str, object]:
    with connect_database(database) as connection:
        session_row(connection, session_id, project_id)
        row = connection.execute(
            "SELECT * FROM message_requests WHERE session_id=? AND request_id=?",
            (session_id, request_id),
        ).fetchone()
    if row is None:
        raise ResourceNotFoundError("MESSAGE_REQUEST_NOT_FOUND")
    return response_from_row(row)
