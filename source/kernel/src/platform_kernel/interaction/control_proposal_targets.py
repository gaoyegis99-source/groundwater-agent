"""Read-only target resolution for durable control proposals."""

from __future__ import annotations

import sqlite3


def task_revision(connection: sqlite3.Connection, session_id: str) -> int | None:
    row = connection.execute(
        "SELECT revision FROM tasks WHERE session_id=?",
        (session_id,),
    ).fetchone()
    return None if row is None else row["revision"]


def resolve_control_target(
    connection: sqlite3.Connection, session: sqlite3.Row, intent: str
) -> tuple[tuple[str, str] | None, str | None, int | None]:
    """Return the unique durable target, or the clarification reason to expose."""

    if intent in {"APPROVE_CONFIRMATION", "REJECT_CONFIRMATION"}:
        rows = connection.execute(
            """SELECT c.confirmation_id,ct.task_revision
               FROM confirmations c LEFT JOIN confirmation_tasks ct
                 ON ct.confirmation_id=c.confirmation_id
               WHERE c.session_id=? AND c.status='PENDING'
                 AND c.decision IS NULL ORDER BY c.rowid""",
            (session["session_id"],),
        ).fetchall()
        targets = [("CONFIRMATION", row["confirmation_id"]) for row in rows]
        revisions = [row["task_revision"] for row in rows]
    elif intent == "CANCEL_EXECUTION":
        rows = connection.execute(
            """SELECT DISTINCT agent_run_id FROM workflow_runs
               WHERE session_id=? AND status IN ('QUEUED','RUNNING')""",
            (session["session_id"],),
        ).fetchall()
        targets = [("AGENT_RUN", row["agent_run_id"]) for row in rows]
        revisions = [task_revision(connection, session["session_id"])] * len(rows)
    else:
        candidates = []
        if session["pending_agent_run_id"] is not None:
            candidates.append(session["pending_agent_run_id"])
        if (
            session["agent_run_id"] is not None
            and connection.execute(
                "SELECT 1 FROM agent_runs WHERE agent_run_id=? AND status='RUNNING'",
                (session["agent_run_id"],),
            ).fetchone()
            is not None
        ):
            candidates.append(session["agent_run_id"])
        targets = [("AGENT_RUN", value) for value in dict.fromkeys(candidates)]
        revisions = [task_revision(connection, session["session_id"])] * len(targets)
    if len(targets) == 1:
        return targets[0], None, revisions[0]
    return (
        None,
        "CONTROL_TARGET_MISSING" if not targets else "CONTROL_TARGET_AMBIGUOUS",
        None,
    )


def control_target_is_current(
    connection: sqlite3.Connection, proposal: sqlite3.Row
) -> bool:
    """Check the proposal's immutable target against current lifecycle facts."""

    current_revision = task_revision(connection, proposal["session_id"])
    if (
        proposal["task_revision"] is not None
        and current_revision != proposal["task_revision"]
    ):
        return False
    if proposal["target_type"] == "CONFIRMATION":
        return (
            connection.execute(
                """SELECT 1 FROM confirmations WHERE confirmation_id=?
               AND session_id=? AND status='PENDING' AND decision IS NULL""",
                (proposal["target_id"], proposal["session_id"]),
            ).fetchone()
            is not None
        )
    session = connection.execute(
        "SELECT * FROM sessions WHERE session_id=?",
        (proposal["session_id"],),
    ).fetchone()
    if proposal["intent"] == "CANCEL_EXECUTION":
        return (
            connection.execute(
                """SELECT 1 FROM workflow_runs WHERE session_id=? AND agent_run_id=?
               AND status IN ('QUEUED','RUNNING')""",
                (proposal["session_id"], proposal["target_id"]),
            ).fetchone()
            is not None
        )
    if proposal["target_id"] == session["pending_agent_run_id"]:
        return True
    return (
        proposal["target_id"] == session["agent_run_id"]
        and connection.execute(
            "SELECT 1 FROM agent_runs WHERE agent_run_id=? AND status='RUNNING'",
            (proposal["target_id"],),
        ).fetchone()
        is not None
    )
