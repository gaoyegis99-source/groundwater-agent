from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from ..application.query_dtos import AgentRunView, ConfirmedAction
from ..database import append_event, connect_database
from ..errors import ConfirmationConflictError, ResourceNotFoundError
from .access import require_execution_capacity_connection
from .state_projection import project_agent_run
from ..contracts import DataVersionRefV2
from ..application.task_api import require_current_confirmation
from .actors import DEFAULT_LOCAL_ACTOR_SUBJECT


_FINAL_STATUS = {
    "CONFIRM": "CONFIRMED",
    "REJECT": "REJECTED",
    "SUPERSEDE": "SUPERSEDED",
}


@dataclass(frozen=True)
class ClaimedDecision:
    confirmation_id: str
    session_id: str
    project_id: str
    agent_run_id: str
    decision: str
    decision_subject: str
    reason: str | None
    tool_call_ids: tuple[str, ...]
    kind: str
    actions: tuple[ConfirmedAction, ...]


def request_decision(
    database: Path,
    session_id: str,
    confirmation_id: str,
    decision: str,
    reason: str | None,
    project_id: str,
    decision_subject: str = DEFAULT_LOCAL_ACTOR_SUBJECT,
) -> bool:
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        session = connection.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        row = connection.execute(
            """SELECT * FROM confirmations
               WHERE session_id = ? AND confirmation_id = ?""",
            (session_id, confirmation_id),
        ).fetchone()
        if session is None:
            raise ResourceNotFoundError("SESSION_NOT_FOUND")
        if session["project_id"] != project_id:
            raise ResourceNotFoundError("SESSION_NOT_FOUND")
        if row is None or row["goal_revision"] != session["goal_revision"]:
            raise ConfirmationConflictError("CONFIRMATION_NOT_CURRENT")
        if decision == "CONFIRM":
            if connection.execute(
                "SELECT 1 FROM run_controls WHERE agent_run_id=? AND status='REQUESTED'",
                (row["agent_run_id"],),
            ).fetchone():
                raise ConfirmationConflictError("AGENT_STOP_REQUESTED")
            require_current_confirmation(connection, confirmation_id)
        if row["status"] == _FINAL_STATUS[decision]:
            return False
        if row["status"] != "PENDING":
            raise ConfirmationConflictError("CONFIRMATION_NOT_CURRENT")
        if row["decision"] is not None and row["decision"] != decision:
            raise ConfirmationConflictError("CONFIRMATION_NOT_CURRENT")
        if row["decision"] is None:
            if decision == "CONFIRM":
                require_execution_capacity_connection(
                    connection,
                    project_id,
                    session["owner_subject"],
                    confirmation_id,
                )
            connection.execute(
                """UPDATE confirmations
                   SET decision = ?, decision_subject = ?,
                       decision_state = 'REQUESTED', reason = ?
                   WHERE confirmation_id = ?""",
                (decision, decision_subject, reason, confirmation_id),
            )
            append_event(
                connection,
                session_id,
                "confirmation.processing",
                {
                    "confirmation_id": confirmation_id,
                    "agent_run_id": row["agent_run_id"],
                    "goal_revision": row["goal_revision"],
                    "decision": decision,
                    "decision_subject": decision_subject,
                    "decision_state": "REQUESTED",
                },
            )
            return True
        return row["decision_state"] == "REQUESTED"


def recover_requested(database: Path) -> tuple[str, ...]:
    with connect_database(database) as connection:
        connection.execute(
            """UPDATE confirmations SET decision_state = 'REQUESTED'
               WHERE status = 'PENDING' AND decision_state = 'RUNNING'"""
        )
        rows = connection.execute(
            """SELECT confirmation_id FROM confirmations
               WHERE status = 'PENDING' AND decision_state = 'REQUESTED'
               ORDER BY rowid"""
        ).fetchall()
    return tuple(row["confirmation_id"] for row in rows)


def next_requested(database: Path) -> str | None:
    with connect_database(database) as connection:
        row = connection.execute(
            """SELECT confirmation_id FROM confirmations
               WHERE status = 'PENDING' AND decision_state = 'REQUESTED'
               ORDER BY rowid LIMIT 1"""
        ).fetchone()
    return None if row is None else row["confirmation_id"]


def has_requested(database: Path) -> bool:
    """Cheap admission probe used by the bounded runtime scheduler."""

    with connect_database(database) as connection:
        return (
            connection.execute(
                """SELECT 1 FROM confirmations
                   WHERE status='PENDING' AND decision_state='REQUESTED'
                   LIMIT 1"""
            ).fetchone()
            is not None
        )


def claim_decision(database: Path, confirmation_id: str) -> ClaimedDecision | None:
    with connect_database(database) as connection:
        claimed = connection.execute(
            """UPDATE confirmations SET decision_state = 'RUNNING'
               WHERE confirmation_id = ? AND status = 'PENDING'
                 AND decision_state = 'REQUESTED'
                 AND NOT EXISTS (SELECT 1 FROM run_controls WHERE run_controls.agent_run_id=confirmations.agent_run_id AND run_controls.status IN ('REQUESTED','PROCESSING'))""",
            (confirmation_id,),
        ).rowcount
        if claimed == 0:
            return None
        row = connection.execute(
            "SELECT * FROM confirmations WHERE confirmation_id = ?",
            (confirmation_id,),
        ).fetchone()
        if row["decision"] == "CONFIRM":
            require_current_confirmation(connection, confirmation_id)
        session = connection.execute(
            "SELECT project_id FROM sessions WHERE session_id = ?",
            (row["session_id"],),
        ).fetchone()
        actions = connection.execute(
            """SELECT * FROM confirmation_actions
               WHERE confirmation_id = ? ORDER BY position""",
            (confirmation_id,),
        ).fetchall()
    return ClaimedDecision(
        confirmation_id=confirmation_id,
        session_id=row["session_id"],
        project_id=session["project_id"],
        agent_run_id=row["agent_run_id"],
        decision=row["decision"],
        decision_subject=row["decision_subject"],
        reason=row["reason"],
        tool_call_ids=tuple(action["tool_call_id"] for action in actions),
        kind=row["kind"],
        actions=tuple(
            ConfirmedAction(
                tool_call_id=action["tool_call_id"],
                position=action["position"],
                tool=action["tool"],
                model_id=action["model_id"],
                model_version=action["model_version"],
                model_checksum=action["model_checksum"],
                workflow_id=action["workflow_id"],
                workflow_version=action["workflow_version"],
                workflow_checksum=action["workflow_checksum"],
                input_schema_checksum=action["input_schema_checksum"],
                arguments=json.loads(action["arguments_json"]),
                input_references={
                    name: DataVersionRefV2.from_storage_dict(reference)
                    for name, reference in json.loads(
                        action["input_references_json"]
                    ).items()
                },
            )
            for action in actions
        ),
    )


def finalize_decision(
    database: Path, decision: ClaimedDecision, run: AgentRunView
) -> bool:
    status = _FINAL_STATUS[decision.decision]
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        changed = connection.execute(
            """UPDATE confirmations SET status = ?, decision_state = NULL
               WHERE confirmation_id = ? AND status = 'PENDING'""",
            (status, decision.confirmation_id),
        ).rowcount
        if not changed:
            return False
        append_event(
            connection,
            decision.session_id,
            f"confirmation.{status.lower()}",
            {
                "confirmation_id": decision.confirmation_id,
                "decision_subject": decision.decision_subject,
            },
        )
        return project_agent_run(connection, run)
