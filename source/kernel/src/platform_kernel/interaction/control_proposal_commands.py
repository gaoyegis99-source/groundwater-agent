"""Durable state commands for control proposals.

This module owns the transaction and event ordering for proposal lifecycle
commands.  It deliberately delegates all target resolution and read-model
projection to their dedicated modules.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
from uuid import uuid4

from ..database import append_event, connect_database, utc_now
from ..errors import ConfirmationConflictError, ResourceNotFoundError
from .actors import DEFAULT_LOCAL_ACTOR_SUBJECT
from .control_proposal_queries import proposal_view
from .control_proposal_targets import control_target_is_current, resolve_control_target


def create_control_request(
    connection: sqlite3.Connection,
    *,
    session,
    request_id: str,
    message: str,
    intent: str,
) -> sqlite3.Row:
    """Create an out-of-band proposal without consuming the Session Turn slot."""
    agent_run_id = str(uuid4())
    proposal_id = str(uuid4())
    target, reason, task_revision = resolve_control_target(connection, session, intent)
    created_at = utc_now()
    status = "CONTROL_PENDING" if target is not None else "CONTROL_COMPLETE"
    clarification = None if target is not None else reason
    control_intent = intent if target is not None else None
    response_kind = "CONTROL" if target is not None else "NEEDS_CLARIFICATION"
    connection.execute(
        """INSERT INTO message_requests(
               agent_run_id,session_id,request_id,goal_revision,message,input_references_json,
               requested_mode,routing_source,workflow_draft_id,corrects_request_id,
               routing_status,intent_decision,dialogue_policy,semantic_intent,
               task_action,control_intent,clarification_reason,resolved_mode,
               clarification_required,route_provider,route_model,route_trace_id,
               route_input_tokens,route_output_tokens,route_total_tokens,route_attempts,
               route_provider_requests,unknown_cost_attempts,route_started_at,
               route_finished_at,route_duration_ms,error_code,created_at
           ) VALUES (?,?,?,?,?,'[]','AUTO','RULE',NULL,NULL,?,?,NULL,'UNSPECIFIED',
                     'NONE',?,?,NULL,?,NULL,NULL,NULL,0,0,0,0,0,0,?,?,0,NULL,?)""",
        (
            agent_run_id,
            session["session_id"],
            request_id,
            session["goal_revision"],
            message,
            status,
            response_kind,
            control_intent,
            clarification,
            int(clarification is not None),
            created_at,
            created_at,
            created_at,
        ),
    )
    append_event(
        connection,
        session["session_id"],
        "intent_routing.resolved",
        {
            "request_id": request_id,
            "agent_run_id": agent_run_id,
            "goal_revision": session["goal_revision"],
            "response_kind": response_kind,
            "semantic_intent": "UNSPECIFIED",
            "task_action": "NONE",
            "control_intent": control_intent,
            "clarification_reason": clarification,
            "routing_source": "RULE",
        },
    )
    if target is not None:
        target_type, target_id = target
        connection.execute(
            """INSERT INTO control_proposals(
                   proposal_id,session_id,source_request_id,intent,target_type,
                   target_id,task_revision,status,decision_request_id,created_at,decided_at
               ) VALUES (?,?,?,?,?,?,?,'PENDING',NULL,?,NULL)""",
            (
                proposal_id,
                session["session_id"],
                request_id,
                intent,
                target_type,
                target_id,
                task_revision,
                created_at,
            ),
        )
        append_event(
            connection,
            session["session_id"],
            "control.proposed",
            {
                "proposal_id": proposal_id,
                "source_request_id": request_id,
                "intent": intent,
                "target_type": target_type,
                "target_id": target_id,
                "task_revision": task_revision,
            },
        )
    return connection.execute(
        "SELECT * FROM message_requests WHERE agent_run_id=?",
        (agent_run_id,),
    ).fetchone()


def create_status_request(
    connection: sqlite3.Connection,
    *,
    session,
    request_id: str,
    message: str,
) -> sqlite3.Row:
    """Persist a read-only status request using the historic event contract."""
    agent_run_id = str(uuid4())
    created_at = utc_now()
    connection.execute(
        """INSERT INTO message_requests(
               agent_run_id,session_id,request_id,goal_revision,message,input_references_json,
               requested_mode,routing_source,workflow_draft_id,corrects_request_id,
               routing_status,intent_decision,dialogue_policy,semantic_intent,
               task_action,control_intent,clarification_reason,resolved_mode,
               clarification_required,route_provider,route_model,route_trace_id,
               route_input_tokens,route_output_tokens,route_total_tokens,route_attempts,
               route_provider_requests,unknown_cost_attempts,route_started_at,
               route_finished_at,route_duration_ms,error_code,created_at
           ) VALUES (?,?,?,?,?,'[]','AUTO','RULE',NULL,NULL,'CONTROL_COMPLETE','DIALOGUE',
                     'READ_ONLY','STATUS_QUERY','NONE',NULL,NULL,'DIALOGUE',0,
                     'kernel','state-rules','rule-status',0,0,0,0,0,0,?,?,0,NULL,?)""",
        (
            agent_run_id,
            session["session_id"],
            request_id,
            session["goal_revision"],
            message,
            created_at,
            created_at,
            created_at,
        ),
    )
    append_event(
        connection,
        session["session_id"],
        "intent_routing.resolved",
        {
            "request_id": request_id,
            "agent_run_id": agent_run_id,
            "goal_revision": session["goal_revision"],
            "response_kind": "DIALOGUE",
            "semantic_intent": "STATUS_QUERY",
            "task_action": "NONE",
            "dialogue_policy": "READ_ONLY",
            "routing_source": "RULE",
        },
    )
    return connection.execute(
        "SELECT * FROM message_requests WHERE agent_run_id=?",
        (agent_run_id,),
    ).fetchone()


def claim_control_proposal(
    database: Path,
    session_id: str,
    project_id: str,
    proposal_id: str,
    request_id: str,
    decision_subject: str = DEFAULT_LOCAL_ACTOR_SUBJECT,
) -> dict[str, object]:
    """Claim a pending proposal, retaining the established event sequence."""
    stale = False
    result = None
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        require_session(connection, session_id, project_id)
        row = connection.execute(
            "SELECT * FROM control_proposals WHERE session_id=? AND proposal_id=?",
            (session_id, proposal_id),
        ).fetchone()
        if row is None:
            raise ResourceNotFoundError("CONTROL_PROPOSAL_NOT_FOUND")
        if row["decision_request_id"] is not None:
            if row["decision_request_id"] != request_id:
                raise ConfirmationConflictError("CONTROL_PROPOSAL_ALREADY_DECIDED")
            return proposal_view(row)
        if row["status"] != "PENDING":
            raise ConfirmationConflictError("CONTROL_PROPOSAL_NOT_PENDING")
        if not control_target_is_current(connection, row):
            mark_control_proposal(connection, row, "STALE", request_id, "control.stale")
            stale = True
        else:
            connection.execute(
                """UPDATE control_proposals
                   SET status='CONFIRMED',decision_request_id=?,decided_at=?
                   WHERE proposal_id=? AND status='PENDING'""",
                (request_id, utc_now(), proposal_id),
            )
            append_event(
                connection,
                session_id,
                "control.confirmed",
                {
                    "proposal_id": proposal_id,
                    "intent": row["intent"],
                    "target_type": row["target_type"],
                    "target_id": row["target_id"],
                    "decision_subject": decision_subject,
                },
            )
            result = proposal_view(
                connection.execute(
                    "SELECT * FROM control_proposals WHERE proposal_id=?",
                    (proposal_id,),
                ).fetchone()
            )
    if stale:
        raise ConfirmationConflictError("CONTROL_PROPOSAL_STALE")
    assert result is not None
    return result


def dismiss_control_proposal(
    database: Path,
    session_id: str,
    project_id: str,
    proposal_id: str,
    request_id: str,
    decision_subject: str = DEFAULT_LOCAL_ACTOR_SUBJECT,
) -> dict[str, object]:
    """Dismiss a pending proposal using the established conflict semantics."""
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        require_session(connection, session_id, project_id)
        row = connection.execute(
            "SELECT * FROM control_proposals WHERE session_id=? AND proposal_id=?",
            (session_id, proposal_id),
        ).fetchone()
        if row is None:
            raise ResourceNotFoundError("CONTROL_PROPOSAL_NOT_FOUND")
        if row["decision_request_id"] is not None:
            if row["decision_request_id"] != request_id or row["status"] != "DISMISSED":
                raise ConfirmationConflictError("CONTROL_PROPOSAL_ALREADY_DECIDED")
            return proposal_view(row)
        if row["status"] != "PENDING":
            raise ConfirmationConflictError("CONTROL_PROPOSAL_NOT_PENDING")
        mark_control_proposal(
            connection,
            row,
            "DISMISSED",
            request_id,
            "control.dismissed",
            decision_subject=decision_subject,
        )
        connection.execute(
            """UPDATE message_requests SET routing_status='CONTROL_COMPLETE'
               WHERE session_id=? AND request_id=?""",
            (session_id, row["source_request_id"]),
        )
        return proposal_view(
            connection.execute(
                "SELECT * FROM control_proposals WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
        )


def finish_control_proposal(
    database: Path,
    proposal_id: str,
    *,
    failed: bool = False,
) -> None:
    """Record terminal processing state without rewriting existing terminals."""
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM control_proposals WHERE proposal_id=?",
            (proposal_id,),
        ).fetchone()
        if row is None or row["status"] in {"COMPLETE", "FAILED"}:
            return
        status = "FAILED" if failed else "COMPLETE"
        connection.execute(
            "UPDATE control_proposals SET status=?,decided_at=? WHERE proposal_id=?",
            (status, utc_now(), proposal_id),
        )
        connection.execute(
            """UPDATE message_requests SET routing_status='CONTROL_COMPLETE',
                      error_code=? WHERE session_id=? AND request_id=?""",
            (
                "CONTROL_ACTION_FAILED" if failed else None,
                row["session_id"],
                row["source_request_id"],
            ),
        )
        append_event(
            connection,
            row["session_id"],
            "control.failed" if failed else "control.complete",
            {"proposal_id": proposal_id, "intent": row["intent"]},
        )


def mark_control_proposal(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    status: str,
    request_id: str,
    event: str,
    *,
    decision_subject: str | None = None,
) -> None:
    """Apply a lifecycle state and append the matching durable event."""
    connection.execute(
        """UPDATE control_proposals SET status=?,decision_request_id=?,decided_at=?
           WHERE proposal_id=?""",
        (status, request_id, utc_now(), row["proposal_id"]),
    )
    facts = {"proposal_id": row["proposal_id"], "intent": row["intent"]}
    if decision_subject is not None:
        facts["decision_subject"] = decision_subject
    append_event(connection, row["session_id"], event, facts)


def require_session(
    connection: sqlite3.Connection,
    session_id: str,
    project_id: str,
) -> sqlite3.Row:
    """Return the owned session or raise the historic resource error."""
    row = connection.execute(
        "SELECT * FROM sessions WHERE session_id=? AND project_id=?",
        (session_id, project_id),
    ).fetchone()
    if row is None:
        raise ResourceNotFoundError("SESSION_NOT_FOUND")
    return row
