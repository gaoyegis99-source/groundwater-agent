"""Read Task and execution links through a WorkItem's owned Sessions."""

from sqlite3 import Connection


_OWNED_SESSIONS = """
WITH linked_sessions AS (
    SELECT s.session_id, s.title, s.archived_at, l.linked_at, s.project_id
    FROM work_item_sessions l
    JOIN sessions s ON s.session_id = l.session_id
    WHERE l.work_item_id = ? AND s.project_id = ? AND s.owner_subject = ?
)
"""


def require_session_link_scope(
    connection: Connection, session_id: str, project_id: str, subject: str
) -> bool:
    return connection.execute(
        """SELECT 1 FROM sessions
           WHERE session_id = ? AND project_id = ? AND owner_subject = ?""",
        (session_id, project_id, subject),
    ).fetchone() is not None


def linked_workflow_run(
    connection: Connection, work_item_id: str, project_id: str, subject: str, run_id: str
) -> bool:
    return connection.execute(
        _OWNED_SESSIONS + """
        SELECT 1 FROM linked_sessions s
        JOIN workflow_runs w ON w.session_id = s.session_id AND w.project_id = s.project_id
        JOIN agent_runs a ON a.agent_run_id = w.agent_run_id
            AND a.session_id = s.session_id AND a.project_id = s.project_id
        WHERE w.run_id = ?""",
        (work_item_id, project_id, subject, run_id),
    ).fetchone() is not None


def session_projections(
    connection: Connection, work_item_id: str, project_id: str, subject: str
) -> dict:
    scope = (work_item_id, project_id, subject)
    sessions = connection.execute(
        _OWNED_SESSIONS + """
        SELECT session_id, title, archived_at, linked_at FROM linked_sessions
        ORDER BY linked_at, session_id""",
        scope,
    ).fetchall()
    tasks = connection.execute(
        _OWNED_SESSIONS + """
        SELECT t.task_id, t.session_id, t.revision, t.lifecycle AS status
        FROM linked_sessions s JOIN tasks t ON t.session_id = s.session_id
        ORDER BY t.session_id""",
        scope,
    ).fetchall()
    task_runs = connection.execute(
        _OWNED_SESSIONS + """
        SELECT t.task_id, r.task_revision, r.agent_run_id
        FROM linked_sessions s JOIN tasks t ON t.session_id = s.session_id
        JOIN task_runs r ON r.task_id = t.task_id
        JOIN agent_runs a ON a.agent_run_id = r.agent_run_id
            AND a.session_id = s.session_id AND a.project_id = s.project_id
        ORDER BY t.task_id, r.task_revision, r.agent_run_id""",
        scope,
    ).fetchall()
    agent_runs = connection.execute(
        _OWNED_SESSIONS + """
        SELECT a.agent_run_id, a.session_id, a.goal_revision FROM linked_sessions s
        JOIN agent_runs a ON a.session_id = s.session_id AND a.project_id = s.project_id
        ORDER BY a.session_id, a.goal_revision, a.created_at, a.agent_run_id""",
        scope,
    ).fetchall()
    workflow_runs = connection.execute(
        _OWNED_SESSIONS + """
        SELECT w.run_id, w.agent_run_id, w.session_id,
               w.workflow_id, w.workflow_version, w.workflow_checksum
        FROM linked_sessions s
        JOIN workflow_runs w ON w.session_id = s.session_id AND w.project_id = s.project_id
        JOIN agent_runs a ON a.agent_run_id = w.agent_run_id
            AND a.session_id = s.session_id AND a.project_id = s.project_id
        ORDER BY w.session_id, w.created_at, w.run_id""",
        scope,
    ).fetchall()
    return {
        "sessions": [dict(row) for row in sessions],
        "tasks": [dict(row) for row in tasks],
        "task_runs": [dict(row) for row in task_runs],
        "agent_runs": [dict(row) for row in agent_runs],
        "workflow_runs": [
            {
                "run_id": row["run_id"],
                "agent_run_id": row["agent_run_id"],
                "session_id": row["session_id"],
                "workflow_ref": {
                    "id": row["workflow_id"],
                    "version": row["workflow_version"],
                    "checksum": row["workflow_checksum"],
                },
            }
            for row in workflow_runs
        ],
    }
