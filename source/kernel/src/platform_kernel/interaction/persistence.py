from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from ..application.agent_api import agent_run_blocks_turn
from ..database import append_event, connect_database
from ..errors import ConfirmationConflictError, ResourceNotFoundError
from .actors import DEFAULT_LOCAL_ACTOR_SUBJECT


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    project_id: str
    owner_subject: str
    goal_revision: int
    agent_run_id: str | None
    pending_agent_run_id: str | None
    workflow_draft_id: str | None


@dataclass(frozen=True)
class AgentStart:
    session_id: str
    project_id: str
    owner_subject: str
    goal_revision: int
    agent_run_id: str


def load_rejected_confirmations(database: Path, agent_run_id: str) -> tuple[dict[str, object], ...]:
    """Read this run's rejected decisions, including one still being finalized."""
    with connect_database(database) as connection:
        rows = connection.execute(
            """SELECT c.confirmation_id, c.reason, ca.tool_call_id, ca.tool,
                      ca.model_id, ca.model_version
               FROM confirmations AS c
               JOIN confirmation_actions AS ca
                 ON ca.confirmation_id = c.confirmation_id
               WHERE c.agent_run_id = ? AND c.decision = 'REJECT'
                 AND c.status IN ('PENDING', 'REJECTED')
               ORDER BY c.rowid, ca.position""",
            (agent_run_id,),
        ).fetchall()
    decisions: dict[str, dict[str, object]] = {}
    for row in rows:
        decision = decisions.setdefault(row["confirmation_id"], {
            "confirmation_id": row["confirmation_id"],
            "reason": row["reason"],
            "actions": [],
        })
        decision["actions"].append({
            "tool_call_id": row["tool_call_id"],
            "tool": row["tool"],
            "model_id": row["model_id"],
            "model_version": row["model_version"],
        })
    return tuple(decisions.values())


def create_session(
    database: Path, session_id: str, project_id: str,
    owner_subject: str = DEFAULT_LOCAL_ACTOR_SUBJECT,
) -> None:
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """INSERT INTO sessions(
                   session_id, project_id, owner_subject, title, archived_at,
                   goal_revision, agent_run_id,
                   pending_agent_run_id, agent_start_state
               ) VALUES (?, ?, ?, NULL, NULL, 0, NULL, NULL, NULL)""",
            (session_id, project_id, owner_subject),
        )
        append_event(
            connection,
            session_id,
            "session.created",
            {
                "session_id": session_id,
                "project_id": project_id,
                "owner_subject": owner_subject,
            },
        )


def get_session(database: Path, session_id: str) -> SessionRecord:
    with connect_database(database) as connection:
        row = connection.execute(
            "SELECT s.*, COALESCE(r.workflow_draft_id,mr.workflow_draft_id) AS workflow_draft_id FROM sessions s "
            "LEFT JOIN agent_requests r ON r.agent_run_id=COALESCE(s.pending_agent_run_id,s.agent_run_id) "
            "LEFT JOIN message_requests mr ON mr.session_id=s.session_id AND mr.goal_revision=s.goal_revision "
            "AND mr.routing_status NOT IN ('CONTROL_PENDING','CONTROL_COMPLETE') "
            "WHERE s.session_id = ?", (session_id,)
        ).fetchone()
    if row is None:
        raise ResourceNotFoundError("SESSION_NOT_FOUND")
    return SessionRecord(
        session_id=row["session_id"],
        project_id=row["project_id"],
        owner_subject=row["owner_subject"],
        goal_revision=row["goal_revision"],
        agent_run_id=row["agent_run_id"],
        pending_agent_run_id=row["pending_agent_run_id"],
        workflow_draft_id=row["workflow_draft_id"],
    )


def reserve_turn(
    database: Path,
    session_id: str,
    message: str,
    pending_agent_run_id: str,
    project_id: str,
    *,
    dialogue_policy: str = "READ_ONLY",
) -> str | None:
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        return reserve_turn_connection(
            connection, session_id, message, pending_agent_run_id, project_id,
            dialogue_policy=dialogue_policy,
        )


def reserve_turn_connection(
    connection, session_id: str, message: str, pending_agent_run_id: str,
    project_id: str, *, mode: str | None = "DIALOGUE",
    workflow_draft_id: str | None = None,
    start_state: str = "REQUESTED",
    request_id: str | None = None,
    requested_mode: str | None = None,
    dialogue_policy: str | None = None,
    supersede_confirmation: bool = True,
) -> str | None:
    session = connection.execute(
        "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if session is None:
        raise ResourceNotFoundError("SESSION_NOT_FOUND")
    if session["project_id"] != project_id:
        raise ResourceNotFoundError("SESSION_NOT_FOUND")
    if session["pending_agent_run_id"] is not None:
        raise ConfirmationConflictError("AGENT_EXECUTION_IN_PROGRESS")
    if session["agent_run_id"] is not None and agent_run_blocks_turn(
        connection, session["agent_run_id"]
    ):
        raise ConfirmationConflictError("AGENT_EXECUTION_IN_PROGRESS")
    confirmation = connection.execute(
        """SELECT * FROM confirmations
           WHERE session_id = ? AND goal_revision = ? AND status = 'PENDING'
           ORDER BY rowid DESC LIMIT 1""",
        (session_id, session["goal_revision"]),
    ).fetchone()
    superseded = None
    if confirmation is not None and supersede_confirmation:
        if confirmation["decision"] not in {None, "SUPERSEDE"}:
            raise ConfirmationConflictError("CONFIRMATION_DECISION_IN_PROGRESS")
        connection.execute(
            """UPDATE confirmations
               SET decision = 'SUPERSEDE', decision_state = 'REQUESTED'
               WHERE confirmation_id = ?""",
            (confirmation["confirmation_id"],),
        )
        superseded = confirmation["confirmation_id"]
    revision = session["goal_revision"] + 1
    claimed = connection.execute(
        """UPDATE sessions SET goal_revision = ?, agent_run_id = NULL,
               pending_agent_run_id = ?, agent_start_state = ?
           WHERE session_id = ? AND goal_revision = ?
             AND agent_run_id IS ? AND pending_agent_run_id IS ?""",
        (
            revision,
            pending_agent_run_id,
            start_state,
            session_id,
            session["goal_revision"],
            session["agent_run_id"],
            session["pending_agent_run_id"],
        ),
    )
    if claimed.rowcount != 1:
        raise ConfirmationConflictError("AGENT_EXECUTION_IN_PROGRESS")
    connection.execute(
        """INSERT INTO messages(session_id, goal_revision, role, content)
           VALUES (?, ?, 'user', ?)""",
        (session_id, revision, message),
    )
    append_event(
        connection,
        session_id,
        "turn.accepted",
        {"goal_revision": revision, "agent_run_id": pending_agent_run_id,
         "request_id": request_id, "requested_mode": requested_mode,
         "dialogue_policy": dialogue_policy,
         "workflow_draft_id": workflow_draft_id,
         "actor_subject": session["owner_subject"]},
    )
    if mode is not None:
        connection.execute(
            "INSERT INTO agent_requests VALUES (?, ?, ?, ?)",
            (pending_agent_run_id, mode, workflow_draft_id, dialogue_policy),
        )
    return superseded



def recover_agent_starts(database: Path) -> tuple[str, ...]:
    with connect_database(database) as connection:
        connection.execute(
            """UPDATE sessions SET agent_start_state = 'REQUESTED'
               WHERE pending_agent_run_id IS NOT NULL
                 AND agent_start_state = 'RUNNING'"""
        )
        rows = connection.execute(
            """SELECT s.session_id FROM sessions AS s
               LEFT JOIN message_requests AS r
                 ON r.agent_run_id = s.pending_agent_run_id
               WHERE s.pending_agent_run_id IS NOT NULL
                 AND s.agent_start_state = 'REQUESTED'
                 AND (r.agent_run_id IS NULL OR r.routing_status IN ('DECIDED','START_REQUESTED'))
                 AND NOT EXISTS (
                     SELECT 1 FROM confirmations AS c
                     WHERE c.session_id=s.session_id
                       AND c.decision_state='REQUESTED'
                 )
               ORDER BY s.rowid"""
        ).fetchall()
    return tuple(row["session_id"] for row in rows)


def pending_agent_start(database: Path, session_id: str) -> AgentStart | None:
    with connect_database(database) as connection:
        row = connection.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    if row is None or row["pending_agent_run_id"] is None:
        return None
    return AgentStart(
        session_id=session_id,
        project_id=row["project_id"],
        owner_subject=row["owner_subject"],
        goal_revision=row["goal_revision"],
        agent_run_id=row["pending_agent_run_id"],
    )


def claim_agent_start(database: Path, start: AgentStart) -> str | None:
    with connect_database(database) as connection:
        claimed = connection.execute(
            """UPDATE sessions SET agent_start_state = 'RUNNING'
               WHERE session_id = ? AND goal_revision = ?
                 AND pending_agent_run_id = ?
                 AND agent_start_state = 'REQUESTED'
                 AND NOT EXISTS (
                     SELECT 1 FROM message_requests AS r
                     WHERE r.agent_run_id = ?
                       AND r.routing_status NOT IN ('DECIDED','START_REQUESTED')
                 )
                 AND NOT EXISTS (
                     SELECT 1 FROM confirmations AS c
                     WHERE c.session_id = ?
                       AND c.decision_state = 'REQUESTED'
                 )""",
            (
                start.session_id,
                start.goal_revision,
                start.agent_run_id,
                start.agent_run_id,
                start.session_id,
            ),
        ).rowcount
        message = connection.execute(
            """SELECT content FROM messages
               WHERE session_id = ? AND goal_revision = ? AND role = 'user'
               ORDER BY message_id DESC LIMIT 1""",
            (start.session_id, start.goal_revision),
        ).fetchone()
    if claimed == 0:
        return None
    return message["content"]


def history(database: Path, session_id: str, *, after_revision: int) -> tuple[tuple[int, str, str], ...]:
    with connect_database(database) as connection:
        rows = connection.execute(
            """SELECT goal_revision, role, content FROM messages
               WHERE session_id = ? AND goal_revision > ? AND goal_revision IN (
                   SELECT goal_revision FROM messages WHERE session_id = ? AND goal_revision > ?
                   GROUP BY goal_revision
                   HAVING SUM(CASE WHEN role = 'assistant' THEN 1 ELSE 0 END) > 0
               ) ORDER BY message_id""",
            (session_id, after_revision, session_id, after_revision),
        ).fetchall()
    return tuple(
        (row["goal_revision"], row["role"], row["content"]) for row in rows
    )


def events_after(
    database: Path, session_id: str, event_id: int
) -> tuple[dict[str, object], ...]:
    with connect_database(database) as connection:
        rows = connection.execute(
            """SELECT * FROM session_events
               WHERE session_id = ? AND event_id > ? ORDER BY event_id""",
            (session_id, event_id),
        ).fetchall()
    return tuple(
        {
            "event_id": row["event_id"],
            "event_type": row["event_type"],
            "data": json.loads(row["payload_json"]),
            "created_at": row["created_at"],
        }
        for row in rows
    )


def requested_agent_starts(database: Path, limit: int | None = None) -> tuple[str, ...]:
    if limit is not None and limit < 1:
        return ()
    limit_clause = "" if limit is None else " LIMIT ?"
    parameters = () if limit is None else (limit,)
    with connect_database(database) as connection:
        rows = connection.execute(
            """SELECT s.session_id FROM sessions AS s
               LEFT JOIN message_requests AS r
                 ON r.agent_run_id = s.pending_agent_run_id
               WHERE s.pending_agent_run_id IS NOT NULL
                 AND s.agent_start_state = 'REQUESTED'
                 AND (r.agent_run_id IS NULL OR r.routing_status IN ('DECIDED','START_REQUESTED'))
                 AND NOT EXISTS (
                     SELECT 1 FROM confirmations AS c
                     WHERE c.session_id=s.session_id
                       AND c.decision_state='REQUESTED'
                 )
               ORDER BY s.rowid""" + limit_clause,
            parameters,
        ).fetchall()
    return tuple(row["session_id"] for row in rows)
