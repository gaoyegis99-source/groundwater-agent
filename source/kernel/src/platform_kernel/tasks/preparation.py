"""Durable, compact task preparation facts; observation bodies stay transient."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from ..database import append_event, connect_database, utc_now
from ..errors import ResourceNotFoundError
from .models import TaskPreparationEntryV1, TaskPreparationV1


def save_preparation(
    database: Path,
    *,
    agent_run_id: str,
    tool_call_id: str,
    snapshot: dict[str, object],
) -> dict[str, object]:
    """Replace the run head with one explicit preparation entry."""
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        preparation = save_preparation_connection(
            connection,
            agent_run_id=agent_run_id,
            tool_call_id=tool_call_id,
            snapshots=(snapshot,),
        )
        return {
            **preparation["entries"][0],
            "preparation_id": preparation["preparation_id"],
            "revision": preparation["revision"],
        }


def save_preparation_connection(
    connection,
    *,
    agent_run_id: str,
    tool_call_id: str,
    snapshots: tuple[dict[str, object], ...],
) -> dict[str, object]:
    if not snapshots:
        raise ValueError("TASK_PREPARATION_ENTRIES_REQUIRED")
    entries = [
        TaskPreparationEntryV1.model_validate(snapshot).model_dump()
        for snapshot in snapshots
    ]
    run = connection.execute(
        "SELECT session_id,goal_revision FROM agent_runs WHERE agent_run_id=?",
        (agent_run_id,),
    ).fetchone()
    if run is None:
        raise ValueError("TASK_PREPARATION_RUN_MISSING")
    replay = connection.execute(
        """SELECT snapshot_json FROM task_preparation_revisions
           WHERE agent_run_id=? AND tool_call_id=?""",
        (agent_run_id, tool_call_id),
    ).fetchone()
    if replay is not None:
        value = json.loads(replay["snapshot_json"])
        if value.get("entries") != entries:
            raise ValueError("TASK_PREPARATION_REQUEST_CONFLICT")
        return value
    task = connection.execute(
        "SELECT task_id,revision FROM tasks WHERE session_id=?",
        (run["session_id"],),
    ).fetchone()
    current = connection.execute(
        "SELECT preparation_id,revision FROM task_preparations WHERE agent_run_id=?",
        (agent_run_id,),
    ).fetchone()
    preparation_id = str(uuid4()) if current is None else current["preparation_id"]
    revision = 1 if current is None else current["revision"] + 1
    now = utc_now()
    value = TaskPreparationV1.model_validate({
        "preparation_id": preparation_id,
        "revision": revision,
        "session_id": run["session_id"],
        "agent_run_id": agent_run_id,
        "goal_revision": run["goal_revision"],
        "task_id": None if task is None else task["task_id"],
        "base_task_revision": None if task is None else task["revision"],
        "status": (
            "BLOCKED"
            if any(entry["status"] == "BLOCKED" for entry in entries)
            else "READY"
        ),
        "entries": entries,
        "fresh_for_current_turn": True,
        "disposition": "ACTIVE",
    }).model_dump()
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if current is None:
        connection.execute(
            """INSERT INTO task_preparations VALUES (
               ?,?,?,?,?,?,?,?,?,?)""",
            (
                preparation_id, run["session_id"], agent_run_id,
                value["task_id"], value["base_task_revision"], revision,
                value["status"], encoded, now, now,
            ),
        )
    else:
        connection.execute(
            """UPDATE task_preparations SET revision=?,status=?,snapshot_json=?,updated_at=?
               WHERE preparation_id=?""",
            (
                revision, value["status"], encoded, now, preparation_id,
            ),
        )
    connection.execute(
        "INSERT INTO task_preparation_revisions VALUES (?,?,?,?,?,?)",
        (preparation_id, revision, agent_run_id, tool_call_id, encoded, now),
    )
    append_event(
        connection,
        run["session_id"],
        "task.preparation_revised",
        {
            "preparation_id": preparation_id,
            "preparation_revision": revision,
            "status": value["status"],
            "entry_count": len(entries),
        },
    )
    return value


def preparation_in_connection(
    connection, session_id: str, *, current_goal_revision: int | None = None
) -> dict[str, object] | None:
    row = connection.execute(
        """SELECT p.snapshot_json,p.agent_run_id,a.status AS agent_status
           FROM task_preparations AS p
           JOIN agent_runs AS a ON a.agent_run_id=p.agent_run_id
           WHERE p.session_id=? ORDER BY p.updated_at DESC,p.rowid DESC LIMIT 1""",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    value = json.loads(row["snapshot_json"])
    value["fresh_for_current_turn"] = (
        current_goal_revision is None
        or value["goal_revision"] == current_goal_revision
    )
    actions = connection.execute(
        "SELECT status FROM agent_actions WHERE agent_run_id=? ORDER BY position",
        (row["agent_run_id"],),
    ).fetchall()
    action_statuses = {item["status"] for item in actions}
    if not value["fresh_for_current_turn"]:
        value["disposition"] = "STALE"
    elif "REJECTED" in action_statuses:
        value["disposition"] = "REJECTED"
    elif row["agent_status"] in {"FAILED"}:
        value["disposition"] = "FAILED"
    elif row["agent_status"] in {"CANCELLED", "SUPERSEDED"}:
        value["disposition"] = "ABANDONED"
    elif row["agent_status"] == "WAITING_CONFIRMATION":
        value["disposition"] = "WAITING_CONFIRMATION"
    elif row["agent_status"] == "COMPLETE":
        value["disposition"] = "CONSUMED"
    elif action_statuses.intersection({"APPROVED", "QUEUED", "RUNNING"}):
        value["disposition"] = "EXECUTING"
    elif actions:
        value["disposition"] = "CONSUMED"
    else:
        value["disposition"] = "ACTIVE"
    return TaskPreparationV1.model_validate(value).model_dump()


def get_preparation(database: Path, session_id: str, project_id: str):
    with connect_database(database) as connection:
        connection.execute("BEGIN")
        session = connection.execute(
            "SELECT project_id,goal_revision FROM sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if session is None or session["project_id"] != project_id:
            raise ResourceNotFoundError("SESSION_NOT_FOUND")
        return preparation_in_connection(
            connection, session_id, current_goal_revision=session["goal_revision"]
        )
