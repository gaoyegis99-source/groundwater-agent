"""Atomic intent-routing commands spanning Interaction and Agent state."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from ..database import append_event, connect_database, duration_ms, utc_now
from ..errors import ResourceNotFoundError
from ..interaction.message_request_common import (
    INTENT_DECISIONS,
    materialize_workflow_draft,
    response_from_row,
    supersede_pending_confirmation,
)
from .transaction_commands import transaction_command


@transaction_command(
    name="resolve-intent-route",
    tables=(
        "agent_requests",
        "workflow_drafts",
        "confirmations",
        "message_requests",
        "sessions",
        "session_events",
    ),
)
def resolve_intent_route(
    database: Path,
    agent_run_id: str,
    decision,
    dialogue_policy: str | None = None,
    *,
    provider: str,
    model: str,
    trace_id: str,
    provider_requests: int,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    routing_source: str = "MODEL",
) -> dict[str, object]:
    if isinstance(decision, str):
        response_kind = decision
        semantic_intent = (
            "CREATE_WORKFLOW"
            if decision == "WORKFLOW_DRAFT"
            else "UNKNOWN"
            if decision == "NEEDS_CLARIFICATION"
            else "EXECUTE_ON_DATA"
            if dialogue_policy == "EXECUTION_PROPOSAL"
            else "UNSPECIFIED"
        )
        task_action = "NONE"
        control_intent = None
        clarification_reason = (
            "REQUEST_KIND_AMBIGUOUS" if decision == "NEEDS_CLARIFICATION" else None
        )
    else:
        response_kind = decision.response_kind
        dialogue_policy = decision.dialogue_policy
        semantic_intent = decision.semantic_intent
        task_action = decision.task_action
        control_intent = decision.control_intent
        clarification_reason = decision.clarification_reason
    if response_kind not in INTENT_DECISIONS or response_kind == "CONTROL":
        raise ValueError("INTENT_DECISION_INVALID")
    finished_at = utc_now()
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM message_requests WHERE agent_run_id=?", (agent_run_id,)
        ).fetchone()
        if row is None:
            raise ResourceNotFoundError("MESSAGE_REQUEST_NOT_FOUND")
        if row["routing_status"] in {"DECIDED", "START_REQUESTED"}:
            if (
                row["intent_decision"] != response_kind
                or row["dialogue_policy"] != dialogue_policy
            ):
                raise ValueError("INTENT_ROUTE_STATE_INVALID")
            return response_from_row(row)
        if row["requested_mode"] != "AUTO" or row["routing_status"] != "ROUTING_RUNNING":
            raise ValueError("INTENT_ROUTE_STATE_INVALID")
        mode = "DIALOGUE" if response_kind == "NEEDS_CLARIFICATION" else response_kind
        workflow_draft_id = str(uuid4()) if mode == "WORKFLOW_DRAFT" else None
        created_at = utc_now()
        connection.execute(
            "INSERT INTO agent_requests VALUES (?,?,?,?)",
            (agent_run_id, mode, workflow_draft_id, dialogue_policy),
        )
        materialize_workflow_draft(
            connection,
            session_id=row["session_id"],
            request_id=row["request_id"],
            message=row["message"],
            goal_revision=row["goal_revision"],
            agent_run_id=agent_run_id,
            workflow_draft_id=workflow_draft_id,
            created_at=created_at,
        )
        supersede_pending_confirmation(connection, row["session_id"])
        route_duration = duration_ms(row["created_at"], finished_at)
        connection.execute(
            """UPDATE message_requests
               SET routing_status='DECIDED',intent_decision=?,dialogue_policy=?,
                   semantic_intent=?,task_action=?,control_intent=?,
                   clarification_reason=?,resolved_mode=?,routing_source=?,
                   clarification_required=?,workflow_draft_id=?,route_provider=?,
                   route_model=?,route_trace_id=?,route_input_tokens=?,
                   route_output_tokens=?,route_total_tokens=?,route_provider_requests=?,
                   route_finished_at=?,route_duration_ms=?,error_code=NULL
               WHERE agent_run_id=?""",
            (
                response_kind,
                dialogue_policy,
                semantic_intent,
                task_action,
                control_intent,
                clarification_reason,
                mode,
                routing_source,
                int(response_kind == "NEEDS_CLARIFICATION"),
                workflow_draft_id,
                provider,
                model,
                trace_id,
                input_tokens,
                output_tokens,
                total_tokens,
                provider_requests,
                finished_at,
                route_duration,
                agent_run_id,
            ),
        )
        if connection.execute(
            """UPDATE sessions SET agent_start_state='REQUESTED'
               WHERE session_id=? AND pending_agent_run_id=?
                 AND agent_start_state='ROUTING_RUNNING'""",
            (row["session_id"], agent_run_id),
        ).rowcount != 1:
            raise ValueError("INTENT_ROUTE_SESSION_STATE_INVALID")
        append_event(
            connection,
            row["session_id"],
            "intent_routing.resolved",
            {
                "request_id": row["request_id"],
                "agent_run_id": agent_run_id,
                "goal_revision": row["goal_revision"],
                "response_kind": response_kind,
                "semantic_intent": semantic_intent,
                "task_action": task_action,
                "dialogue_policy": dialogue_policy,
                "resolved_mode": mode,
                "provider": provider,
                "model": model,
                "workflow_draft_id": workflow_draft_id,
            },
        )
        connection.execute(
            """UPDATE message_requests SET routing_status='START_REQUESTED'
               WHERE agent_run_id=? AND routing_status='DECIDED'""",
            (agent_run_id,),
        )
        return response_from_row(
            connection.execute(
                "SELECT * FROM message_requests WHERE agent_run_id=?", (agent_run_id,)
            ).fetchone()
        )


@transaction_command(
    name="fail-intent-route",
    tables=("message_requests", "agent_runs", "sessions", "session_events"),
)
def fail_intent_route(
    database: Path,
    agent_run_id: str,
    error_code: str,
    *,
    provider: str,
    model: str,
    trace_id: str,
    provider_requests: int,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
) -> dict[str, object]:
    finished_at = utc_now()
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM message_requests WHERE agent_run_id=?", (agent_run_id,)
        ).fetchone()
        if row is None:
            raise ResourceNotFoundError("MESSAGE_REQUEST_NOT_FOUND")
        if row["routing_status"] == "FAILED":
            if row["error_code"] != error_code:
                raise ValueError("INTENT_ROUTE_STATE_INVALID")
            return response_from_row(row)
        if row["requested_mode"] != "AUTO" or row["routing_status"] != "ROUTING_RUNNING":
            raise ValueError("INTENT_ROUTE_STATE_INVALID")
        route_duration = duration_ms(row["created_at"], finished_at)
        run_duration = duration_ms(row["route_started_at"], finished_at)
        connection.execute(
            """UPDATE message_requests
               SET routing_status='FAILED',route_provider=?,route_model=?,route_trace_id=?,
                   route_input_tokens=?,route_output_tokens=?,route_total_tokens=?,
                   route_provider_requests=?,route_finished_at=?,route_duration_ms=?,
                   error_code=? WHERE agent_run_id=?""",
            (
                provider,
                model,
                trace_id,
                input_tokens,
                output_tokens,
                total_tokens,
                provider_requests,
                finished_at,
                route_duration,
                error_code,
                agent_run_id,
            ),
        )
        connection.execute(
            """INSERT INTO agent_runs(
                   agent_run_id,session_id,project_id,goal_revision,provider,model,
                   status,steps_json,output_json,input_tokens,output_tokens,total_tokens,
                   trace_id,error,created_at,started_at,finished_at,duration_ms
               ) SELECT r.agent_run_id,r.session_id,s.project_id,r.goal_revision,?,?,
                        'FAILED',?,NULL,?,?,?,?,?,r.created_at,r.route_started_at,?,?
                   FROM message_requests AS r JOIN sessions AS s ON s.session_id=r.session_id
                   WHERE r.agent_run_id=?""",
            (
                provider,
                model,
                json.dumps(["FAILED"], separators=(",", ":")),
                input_tokens,
                output_tokens,
                total_tokens,
                trace_id,
                error_code,
                finished_at,
                run_duration,
                agent_run_id,
            ),
        )
        if connection.execute(
            """UPDATE sessions SET agent_run_id=?,pending_agent_run_id=NULL,
                      agent_start_state=NULL
               WHERE session_id=? AND pending_agent_run_id=?
                 AND agent_start_state='ROUTING_RUNNING'""",
            (agent_run_id, row["session_id"], agent_run_id),
        ).rowcount != 1:
            raise ValueError("INTENT_ROUTE_SESSION_STATE_INVALID")
        append_event(
            connection,
            row["session_id"],
            "intent_routing.failed",
            {
                "request_id": row["request_id"],
                "agent_run_id": agent_run_id,
                "goal_revision": row["goal_revision"],
                "error_code": error_code,
                "provider": provider,
                "model": model,
            },
        )
        append_event(
            connection,
            row["session_id"],
            "agent.failed",
            {
                "agent_run_id": agent_run_id,
                "goal_revision": row["goal_revision"],
                "error": error_code,
            },
        )
        return response_from_row(
            connection.execute(
                "SELECT * FROM message_requests WHERE agent_run_id=?", (agent_run_id,)
            ).fetchone()
        )
