from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import sqlite3

from ...contracts import DataVersionRefV2
from ...database import connect_database
from ...application.task_api import (
    preparation_in_connection,
    task_in_connection,
)
from ..memory import ExecutionActionV1, ExecutionContextV1, MemoryRecord, MemoryUpdate
from ..models import AgentRun, MemorySummaryV3
from .projection import action_status, stable_error


@dataclass(frozen=True)
class InitialContextSources:
    history: tuple[tuple[int, str, str], ...]
    memory: MemoryRecord | None
    execution_context: ExecutionContextV1
    request_context: dict[str, object]
    task_context: dict[str, object] | None
    preparation_context: dict[str, object] | None
    durable_request: bool


@dataclass(frozen=True)
class RouteContextSources:
    history: tuple[tuple[int, str, str], ...]
    memory: MemoryRecord | None
    task_context: dict[str, object] | None


def load_route_context_sources(
    database: Path,
    *,
    session_id: str,
    before_goal_revision: int,
) -> RouteContextSources:
    """Load Router history, memory and task from one SQLite read snapshot."""
    with connect_database(database) as connection:
        connection.execute("BEGIN")
        memory_row = connection.execute(
            "SELECT through_goal_revision,summary_json FROM agent_memory WHERE session_id=?",
            (session_id,),
        ).fetchone()
        rows = connection.execute(
            """SELECT goal_revision,role,content FROM messages
               WHERE session_id=? AND goal_revision<? AND goal_revision IN (
                   SELECT goal_revision FROM messages
                   WHERE session_id=? AND goal_revision<?
                   GROUP BY goal_revision
                   HAVING SUM(CASE WHEN role='assistant' THEN 1 ELSE 0 END)>0
               ) ORDER BY message_id""",
            (
                session_id, before_goal_revision,
                session_id, before_goal_revision,
            ),
        ).fetchall()
        task = task_in_connection(connection, session_id)
    return RouteContextSources(
        history=tuple(
            (row["goal_revision"], row["role"], row["content"])
            for row in rows
        ),
        memory=(
            None if memory_row is None else MemoryRecord(
                through_goal_revision=memory_row["through_goal_revision"],
                summary=MemorySummaryV3.model_validate_json(
                    memory_row["summary_json"]
                ),
            )
        ),
        task_context=task,
    )


def load_initial_context_sources(
    database: Path,
    *,
    agent_run_id: str,
    session_id: str,
    before_goal_revision: int,
) -> InitialContextSources:
    """Take every cross-turn source from one SQLite read snapshot."""
    with connect_database(database) as connection:
        connection.execute("BEGIN")
        memory_row = connection.execute(
            "SELECT through_goal_revision,summary_json FROM agent_memory WHERE session_id=?",
            (session_id,),
        ).fetchone()
        request_row = connection.execute(
            """SELECT a.*,COALESCE(m.clarification_required,0) clarification_required,
                      COALESCE(m.input_references_json,'[]') input_references_json
               FROM agent_requests a LEFT JOIN message_requests m
                 ON m.agent_run_id=a.agent_run_id WHERE a.agent_run_id=?""",
            (agent_run_id,),
        ).fetchone()
        durable_request = connection.execute(
            "SELECT 1 FROM message_requests WHERE agent_run_id=?",
            (agent_run_id,),
        ).fetchone() is not None
        history_rows = connection.execute(
            """SELECT goal_revision,role,content FROM messages
               WHERE session_id=? AND goal_revision<? AND goal_revision IN (
                   SELECT goal_revision FROM messages
                   WHERE session_id=? AND goal_revision<?
                   GROUP BY goal_revision
                   HAVING SUM(CASE WHEN role='assistant' THEN 1 ELSE 0 END)>0
               ) ORDER BY message_id""",
            (
                session_id, before_goal_revision,
                session_id, before_goal_revision,
            ),
        ).fetchall()
        execution_rows = connection.execute(
            """SELECT ar.goal_revision, aa.tool, aa.model_id, aa.model_version,
                        aa.model_checksum, aa.workflow_id, aa.workflow_version,
                        aa.workflow_checksum, aa.status, aa.error,
                        wr.run_id AS workflow_run_id, wr.status AS workflow_status,
                        wr.outputs_json AS workflow_outputs_json,
                        wr.error AS workflow_error
               FROM agent_runs AS ar
               JOIN agent_actions AS aa ON aa.agent_run_id = ar.agent_run_id
               LEFT JOIN workflow_runs AS wr ON wr.agent_run_id = aa.agent_run_id
                 AND wr.tool_call_id = aa.tool_call_id
                 AND wr.attempt = (SELECT MAX(w2.attempt) FROM workflow_runs w2
                                   WHERE w2.agent_run_id=aa.agent_run_id
                                     AND w2.tool_call_id=aa.tool_call_id)
               WHERE ar.session_id = ? AND ar.goal_revision < ?
               ORDER BY ar.goal_revision, aa.position, aa.tool_call_id""",
            (session_id, before_goal_revision),
        ).fetchall()
        task = task_in_connection(connection, session_id)
        preparation = preparation_in_connection(
            connection, session_id, current_goal_revision=before_goal_revision
        )
    memory = None if memory_row is None else MemoryRecord(
        through_goal_revision=memory_row["through_goal_revision"],
        summary=MemorySummaryV3.model_validate_json(memory_row["summary_json"]),
    )
    if request_row is None:
        request_context: dict[str, object] = {
            "mode": "DIALOGUE",
            "dialogue_policy": "READ_ONLY",
            "clarification_only": False,
        }
    else:
        request_context = dict(request_row)
        request_context["clarification_only"] = bool(
            request_context.pop("clarification_required")
        )
        request_context["input_references"] = json.loads(
            request_context.pop("input_references_json")
        )
    return InitialContextSources(
        history=tuple(
            (row["goal_revision"], row["role"], row["content"])
            for row in history_rows
        ),
        memory=memory,
        execution_context=_execution_context(execution_rows),
        request_context=request_context,
        task_context=task,
        preparation_context=preparation,
        durable_request=durable_request,
    )


def load_memory(database: Path, session_id: str) -> MemoryRecord | None:
    with connect_database(database) as connection:
        row = connection.execute(
            "SELECT through_goal_revision,summary_json FROM agent_memory WHERE session_id = ?", (session_id,)
        ).fetchone()
    if row is None:
        return None
    return MemoryRecord(
        through_goal_revision=row["through_goal_revision"],
        summary=MemorySummaryV3.model_validate_json(row["summary_json"]),
    )


def load_execution_context(
    database: Path,
    session_id: str,
    *,
    before_goal_revision: int,
) -> ExecutionContextV1:
    with connect_database(database) as connection:
        rows = connection.execute(
            """SELECT ar.goal_revision, aa.tool, aa.model_id, aa.model_version,
                        aa.model_checksum, aa.workflow_id, aa.workflow_version,
                        aa.workflow_checksum, aa.status, aa.error,
                        wr.run_id AS workflow_run_id, wr.status AS workflow_status,
                        wr.outputs_json AS workflow_outputs_json,
                        wr.error AS workflow_error
               FROM agent_runs AS ar
               JOIN agent_actions AS aa ON aa.agent_run_id = ar.agent_run_id
               LEFT JOIN workflow_runs AS wr ON wr.agent_run_id = aa.agent_run_id
                 AND wr.tool_call_id = aa.tool_call_id
                 AND wr.attempt = (SELECT MAX(w2.attempt) FROM workflow_runs w2
                                   WHERE w2.agent_run_id=aa.agent_run_id
                                     AND w2.tool_call_id=aa.tool_call_id)
               WHERE ar.session_id = ? AND ar.goal_revision < ?
               ORDER BY ar.goal_revision, aa.position, aa.tool_call_id""",
            (session_id, before_goal_revision),
        ).fetchall()
    return _execution_context(rows)


def _execution_context(rows) -> ExecutionContextV1:
    return ExecutionContextV1(
        tuple(
            ExecutionActionV1(
                goal_revision=row["goal_revision"],
                tool=row["tool"],
                model_id=row["model_id"],
                model_version=row["model_version"],
                model_checksum=row["model_checksum"],
                workflow_id=row["workflow_id"],
                workflow_version=row["workflow_version"],
                workflow_checksum=row["workflow_checksum"],
                status=action_status(row["status"], row["workflow_status"]),
                workflow_run_id=row["workflow_run_id"],
                outputs=tuple(
                    DataVersionRefV2.from_storage_dict(value).as_storage_dict()
                    for value in json.loads(row["workflow_outputs_json"] or "[]")
                ),
                error=stable_error(row["workflow_error"] if row["workflow_status"] else row["error"]),
            )
            for row in rows
        )
    )


def list_session_results(
    database: Path,
    session_id: str,
    project_id: str,
    current_revision: int,
    after_revision: int,
) -> dict[str, object]:
    """Project persisted action facts across at most 20 goal revisions."""
    with connect_database(database) as connection:
        last_revision = min(current_revision, after_revision + 20)
        rows = (
            connection.execute(
                """SELECT ar.goal_revision, ar.agent_run_id, aa.tool_call_id,
                          ar.status AS agent_status, aa.position,
                          aa.status AS decision_status, aa.model_id,
                          wr.run_id AS workflow_run_id,
                          wr.status AS workflow_status, wr.outputs_json, wr.attempt
                   FROM agent_runs AS ar
                   JOIN agent_actions AS aa ON aa.agent_run_id = ar.agent_run_id
                   LEFT JOIN workflow_runs AS wr ON wr.agent_run_id = aa.agent_run_id
                     AND wr.tool_call_id = aa.tool_call_id
                     AND wr.attempt = (SELECT MAX(w2.attempt) FROM workflow_runs w2
                                       WHERE w2.agent_run_id=aa.agent_run_id
                                         AND w2.tool_call_id=aa.tool_call_id)
                   WHERE ar.session_id = ? AND ar.project_id = ?
                     AND ar.goal_revision > ?
                     AND ar.goal_revision <= ?
                   ORDER BY ar.goal_revision, aa.position, aa.tool_call_id""",
                (session_id, project_id, after_revision, last_revision),
            ).fetchall()
            if last_revision > after_revision
            else ()
        )
        items = [
            {
                "goal_revision": row["goal_revision"],
                "agent_run_id": row["agent_run_id"],
                "tool_call_id": row["tool_call_id"],
                "agent_status": row["agent_status"],
                "position": row["position"],
                "status": action_status(row["decision_status"], row["workflow_status"]),
                "model_id": row["model_id"],
                "workflow_run_id": row["workflow_run_id"],
                "attempt": row["attempt"],
                "remote_outputs": [
                    DataVersionRefV2.from_storage_dict(value).as_storage_dict()
                    for value in json.loads(row["outputs_json"] or "[]")
                ],
            }
            for row in rows
        ]
        return {
            "session_id": session_id,
            "project_id": project_id,
            "items": items,
            "next_cursor": last_revision if last_revision < current_revision else None,
        }


def list_execution_facts(
    database: Path,
    current_agent_run_id: str,
    *,
    after_goal_revision: int,
    limit: int,
    statuses: tuple[str, ...] | None,
) -> dict[str, object]:
    """Return a body-free authoritative page scoped by the current AgentRun."""
    if type(after_goal_revision) is not int or after_goal_revision < 0:
        raise ValueError("EXECUTION_FACT_CURSOR_INVALID")
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("EXECUTION_FACT_LIMIT_INVALID")
    valid_statuses = {
        "PENDING", "REJECTED", "QUEUED", "RUNNING", "COMPLETE", "FAILED",
    }
    if statuses is not None and (
        not statuses or any(item not in valid_statuses for item in statuses)
    ):
        raise ValueError("EXECUTION_FACT_STATUS_INVALID")
    with connect_database(database) as connection:
        owner = connection.execute(
            "SELECT session_id,project_id,goal_revision FROM agent_runs WHERE agent_run_id=?",
            (current_agent_run_id,),
        ).fetchone()
        if owner is None:
            raise ValueError("AGENT_RUN_NOT_FOUND")
        last_revision = min(
            owner["goal_revision"] - 1, after_goal_revision + limit
        )
        rows = connection.execute(
            """SELECT ar.goal_revision, aa.tool, aa.model_id, aa.model_version,
                      aa.model_checksum, aa.workflow_id, aa.workflow_version,
                      aa.workflow_checksum, aa.status AS decision_status, aa.error,
                      wr.run_id AS workflow_run_id, wr.status AS workflow_status,
                      wr.outputs_json, wr.error AS workflow_error
               FROM agent_runs ar
               JOIN agent_actions aa ON aa.agent_run_id=ar.agent_run_id
               LEFT JOIN workflow_runs wr ON wr.agent_run_id=aa.agent_run_id
                 AND wr.tool_call_id=aa.tool_call_id
                 AND wr.attempt=(SELECT MAX(w2.attempt) FROM workflow_runs w2
                                 WHERE w2.agent_run_id=aa.agent_run_id
                                   AND w2.tool_call_id=aa.tool_call_id)
               WHERE ar.session_id=? AND ar.project_id=?
                 AND ar.goal_revision>? AND ar.goal_revision<=?
               ORDER BY ar.goal_revision,aa.position,aa.tool_call_id""",
            (
                owner["session_id"], owner["project_id"],
                after_goal_revision, last_revision,
            ),
        ).fetchall() if last_revision > after_goal_revision else ()
        facts = [_execution_fact(row) for row in rows]
        if statuses is not None:
            allowed = set(statuses)
            facts = [item for item in facts if item["status"] in allowed]
        return {
            "schemaVersion": "execution-facts.v1",
            "after_goal_revision": after_goal_revision,
            "through_goal_revision": last_revision,
            "items": facts,
            "next_after_goal_revision": (
                last_revision
                if last_revision < owner["goal_revision"] - 1 else None
            ),
        }


def get_execution_fact(
    database: Path,
    current_agent_run_id: str,
    workflow_run_id: str,
) -> dict[str, object]:
    """Read one WorkflowRun projection without parameters or observation bodies."""
    with connect_database(database) as connection:
        owner = connection.execute(
            "SELECT session_id,project_id,goal_revision FROM agent_runs WHERE agent_run_id=?",
            (current_agent_run_id,),
        ).fetchone()
        if owner is None:
            raise ValueError("AGENT_RUN_NOT_FOUND")
        row = connection.execute(
            """SELECT ar.goal_revision, aa.tool, aa.model_id, aa.model_version,
                      aa.model_checksum, aa.workflow_id, aa.workflow_version,
                      aa.workflow_checksum, aa.status AS decision_status, aa.error,
                      wr.run_id AS workflow_run_id, wr.status AS workflow_status,
                      wr.outputs_json, wr.error AS workflow_error
               FROM workflow_runs wr
               JOIN agent_runs ar ON ar.agent_run_id=wr.agent_run_id
               JOIN agent_actions aa ON aa.agent_run_id=wr.agent_run_id
                 AND aa.tool_call_id=wr.tool_call_id
               WHERE wr.run_id=? AND ar.session_id=? AND ar.project_id=?
                 AND ar.goal_revision<?""",
            (
                workflow_run_id, owner["session_id"], owner["project_id"],
                owner["goal_revision"],
            ),
        ).fetchone()
    if row is None:
        return {"schemaVersion": "execution-fact.v1", "status": "NOT_FOUND"}
    return {
        "schemaVersion": "execution-fact.v1",
        "status": "OK",
        "fact": _execution_fact(row),
    }


def _execution_fact(row) -> dict[str, object]:
    status = action_status(row["decision_status"], row["workflow_status"])
    return {
        "goal_revision": row["goal_revision"],
        "tool": row["tool"],
        "model": {
            "id": row["model_id"],
            "version": row["model_version"],
            "checksum": row["model_checksum"],
        },
        "workflow": {
            "id": row["workflow_id"],
            "version": row["workflow_version"],
            "checksum": row["workflow_checksum"],
        },
        "status": status,
        "workflow_run_id": row["workflow_run_id"],
        "outputs": [
            DataVersionRefV2.from_storage_dict(value).as_storage_dict()
            for value in json.loads(row["outputs_json"] or "[]")
        ],
        "error": stable_error(
            row["workflow_error"]
            if row["workflow_status"] else row["error"]
        ),
    }


def allowed_workflow_run_ids(database: Path, run: AgentRun) -> frozenset[str]:
    with connect_database(database) as connection:
        rows = connection.execute(
            """SELECT wr.run_id FROM workflow_runs AS wr
               JOIN agent_runs ar ON ar.agent_run_id = wr.agent_run_id
               WHERE ar.session_id = ?
                 AND (ar.agent_run_id = ? OR ar.goal_revision < ?)""",
            (run.session_id, run.agent_run_id, run.goal_revision),
        ).fetchall()
    return frozenset(row["run_id"] for row in rows)


def save_memory_connection(
    connection: sqlite3.Connection,
    session_id: str,
    update: MemoryUpdate | None,
) -> None:
    if update is None:
        return
    if update.through_goal_revision <= update.expected_through_goal_revision:
        raise ValueError("AGENT_MEMORY_UPDATE_INVALID")
    summary_json = update.summary.model_dump_json()
    if update.expected_through_goal_revision == 0:
        saved = connection.execute(
            """INSERT INTO agent_memory(
                   session_id, through_goal_revision, summary_json
               ) VALUES (?, ?, ?) ON CONFLICT(session_id) DO NOTHING""",
            (session_id, update.through_goal_revision, summary_json),
        )
    else:
        saved = connection.execute(
            """UPDATE agent_memory
               SET through_goal_revision = ?, summary_json = ?
               WHERE session_id = ? AND through_goal_revision = ?""",
            (
                update.through_goal_revision,
                summary_json,
                session_id,
                update.expected_through_goal_revision,
            ),
        )
    if saved.rowcount != 1:
        raise ValueError("AGENT_MEMORY_COMPARE_AND_SET_FAILED")
