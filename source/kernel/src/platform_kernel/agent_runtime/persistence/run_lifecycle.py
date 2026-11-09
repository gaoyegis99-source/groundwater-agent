from __future__ import annotations

import json
import logging
from pathlib import Path
import sqlite3

from ...application.ports import AgentProjectionPort
from ...database import append_event, connect_database, duration_ms, utc_now
from ..memory import MemoryUpdate
from ..models import AgentFinalV5, AgentRun
from ..result_processing import PreparedAction
from .context import save_memory_connection
from .timeline import close_activities_connection, project_activities
from .types import StoredRunState, UsageDelta
from ...application.task_api import (
    commit_updates,
    refresh_task,
    save_preparation_connection,
)

from .run_queries import _project_current, get_run_connection


_LOGGER = logging.getLogger(__name__)


def confirmation_is_current(
    connection: sqlite3.Connection, agent_run_id: str
) -> bool:
    if _stop_fenced(connection, agent_run_id):
        return False
    row = connection.execute(
        """SELECT status FROM agent_runs
           WHERE agent_run_id = ? AND status = 'WAITING_CONFIRMATION'""",
        (agent_run_id,),
    ).fetchone()
    return row is not None


def create_run(
    database: Path,
    *,
    agent_run_id: str,
    session_id: str,
    project_id: str,
    goal_revision: int,
    provider: str,
    model: str,
    trace_id: str,
    started_at: str,
) -> None:
    with connect_database(database) as connection:
        connection.execute(
            """INSERT INTO agent_runs(
                   agent_run_id, session_id, project_id, goal_revision,
                   provider, model, status, steps_json, output_json,
                   input_tokens, output_tokens, total_tokens, trace_id, error,
                   created_at, started_at, finished_at, duration_ms
               ) VALUES (?, ?, ?, ?, ?, ?, 'RUNNING', '[\"RUNNING\"]', NULL,
                         0, 0, 0, ?, NULL, ?, ?, NULL, NULL)""",
            (
                agent_run_id,
                session_id,
                project_id,
                goal_revision,
                provider,
                model,
                trace_id,
                started_at,
                started_at,
            ),
        )
        append_event(
            connection, session_id, "agent.running", {"agent_run_id": agent_run_id}
        )


def save_waiting_result(
    database: Path,
    run: AgentRun,
    actions: tuple[PreparedAction, ...],
    state_payload: dict[str, object],
    usage: UsageDelta,
    memory_update: MemoryUpdate | None,
    agent_projection: AgentProjectionPort,
    task_updates: list | None = None,
    preparation: tuple[str, tuple[dict[str, object], ...]] | None = None,
) -> None:
    state_json = json.dumps(
        state_payload, ensure_ascii=False, separators=(",", ":")
    )
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if _stop_fenced(connection, run.agent_run_id):
            cancel_run_connection(connection, run.agent_run_id, utc_now())
            refresh_task(connection, run.session_id)
            return
        save_memory_connection(connection, run.session_id, memory_update)
        for action in actions:
            connection.execute(
                """INSERT INTO agent_actions(
                       tool_call_id, agent_run_id, position, tool,
                       model_id, model_version, model_checksum,
                       workflow_id, workflow_version, workflow_checksum,
                       plan_revision, arguments_json, input_references_json,
                       schema_checksum, fingerprint,
                       status, error
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                             'PENDING', NULL)""",
                (
                    action.tool_call_id,
                    run.agent_run_id,
                    action.position,
                    action.tool,
                    action.model_id,
                    action.model_version,
                    action.model_checksum,
                    action.workflow_id,
                    action.workflow_version,
                    action.workflow_checksum,
                    action.plan_revision,
                    action.arguments_json,
                    action.input_references_json,
                    action.schema_checksum,
                    action.fingerprint,
                ),
            )
        connection.execute(
            "INSERT OR REPLACE INTO agent_run_state VALUES (?, ?)",
            (run.agent_run_id, state_json),
        )
        transitioned = connection.execute(
            """UPDATE agent_runs
               SET status = 'WAITING_CONFIRMATION',
                   steps_json = ?,
                   input_tokens = input_tokens + ?,
                   output_tokens = output_tokens + ?,
                   total_tokens = total_tokens + ?
               WHERE agent_run_id = ? AND status = 'RUNNING'""",
            (
                json.dumps([*run.steps, "WAITING_CONFIRMATION"]),
                usage.input_tokens,
                usage.output_tokens,
                usage.total_tokens,
                run.agent_run_id,
            ),
        )
        if transitioned.rowcount != 1:
            raise ValueError("AGENT_RUN_TRANSITION_INVALID")
        close_activities_connection(connection, run, "COMPLETE")
        append_event(
            connection,
            run.session_id,
            "agent.waiting_confirmation",
            {"agent_run_id": run.agent_run_id},
        )
        commit_updates(connection, run, task_updates)
        if preparation is not None:
            tool_call_id, snapshots = preparation
            save_preparation_connection(
                connection,
                agent_run_id=run.agent_run_id,
                tool_call_id=tool_call_id,
                snapshots=snapshots,
            )
        _project_current(connection, run.agent_run_id, agent_projection)


def mark_execution_queued(
    connection: sqlite3.Connection,
    run: AgentRun,
) -> None:
    for action in run.actions:
        if action.status != "PENDING":
            continue
        updated = connection.execute(
            """UPDATE agent_actions SET status = 'APPROVED'
               WHERE agent_run_id = ? AND tool_call_id = ? AND status = 'PENDING'""",
            (
                run.agent_run_id,
                action.tool_call_id,
            ),
        )
        if updated.rowcount != 1:
            raise ValueError("AGENT_ACTION_QUEUE_INVALID")
    updated = connection.execute(
        """UPDATE agent_runs SET status = 'WAITING_EXECUTION', steps_json = ?
           WHERE agent_run_id = ? AND status = 'WAITING_CONFIRMATION'""",
        (
            json.dumps([*run.steps, "WAITING_EXECUTION"]),
            run.agent_run_id,
        ),
    )
    if updated.rowcount != 1:
        raise ValueError("AGENT_EXECUTION_QUEUE_INVALID")
    append_event(
        connection,
        run.session_id,
        "agent.waiting_execution",
        {"agent_run_id": run.agent_run_id},
    )


def fail_workflow_definition_unavailable(
    connection: sqlite3.Connection,
    run: AgentRun,
    agent_projection: AgentProjectionPort,
) -> None:
    code = "WORKFLOW_DEFINITION_UNAVAILABLE"
    finished_at = utc_now()
    connection.execute(
        """UPDATE agent_actions SET status = 'FAILED', error = ?
           WHERE agent_run_id = ? AND status = 'PENDING'""",
        (code, run.agent_run_id),
    )
    transitioned = connection.execute(
        """UPDATE agent_runs SET status = 'FAILED', steps_json = ?, error = ?,
               finished_at = ?, duration_ms = ?
           WHERE agent_run_id = ? AND status = 'WAITING_CONFIRMATION'""",
        (
            json.dumps([*run.steps, "FAILED"]),
            code,
            finished_at,
            duration_ms(run.started_at, finished_at),
            run.agent_run_id,
        ),
    )
    if transitioned.rowcount != 1:
        raise ValueError("AGENT_RUN_TRANSITION_INVALID")
    connection.execute(
        "DELETE FROM agent_run_state WHERE agent_run_id = ?",
        (run.agent_run_id,),
    )
    close_activities_connection(connection, run, "FAILED", code)
    append_event(
        connection,
        run.session_id,
        "agent.failed",
        {"agent_run_id": run.agent_run_id, "error": code},
    )
    _project_current(connection, run.agent_run_id, agent_projection)


def claim_state(
    database: Path,
    run: AgentRun,
    *,
    expected_status: str,
    reject_pending: bool = False,
) -> StoredRunState | None:
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if _stop_fenced(connection, run.agent_run_id):
            return None
        claimed = connection.execute(
            """UPDATE agent_runs SET status = 'RUNNING', steps_json = ?
               WHERE agent_run_id = ? AND status = ?""",
            (json.dumps([*run.steps, "RUNNING"]), run.agent_run_id, expected_status),
        )
        if claimed.rowcount != 1:
            return None
        if reject_pending:
            connection.execute(
                """UPDATE agent_actions SET status = 'REJECTED'
                   WHERE agent_run_id = ? AND status = 'PENDING'""",
                (run.agent_run_id,),
            )
        row = connection.execute(
            "SELECT state_json FROM agent_run_state WHERE agent_run_id = ?",
            (run.agent_run_id,),
        ).fetchone()
        if row is None:
            raise ValueError("AGENT_RUN_STATE_INVALID")
        append_event(connection, run.session_id, "agent.running", {
            "agent_run_id": run.agent_run_id, "goal_revision": run.goal_revision,
        })
        return StoredRunState(row["state_json"])


def finish_run(
    database: Path,
    run: AgentRun,
    *,
    agent_projection: AgentProjectionPort,
    status: str,
    output: AgentFinalV5 | None,
    usage: UsageDelta,
    error: str | None,
    memory_update: MemoryUpdate | None = None,
    workflow_draft: dict | None = None,
    preparation: tuple[str, tuple[dict[str, object], ...]] | None = None,
    failed_activity_id: str | None = None,
    diagnostics: dict | None = None,
    task_updates: list | None = None,
    continuation_stop: str | None = None,
) -> None:
    finished_at = utc_now()
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if _stop_fenced(connection, run.agent_run_id):
            cancel_run_connection(connection, run.agent_run_id, utc_now())
            refresh_task(connection, run.session_id)
            return
        save_memory_connection(connection, run.session_id, memory_update)
        transitioned = connection.execute(
            """UPDATE agent_runs
               SET status = ?, steps_json = ?, output_json = ?,
                   input_tokens = input_tokens + ?,
                   output_tokens = output_tokens + ?,
                   total_tokens = total_tokens + ?, error = ?,
                   finished_at = ?, duration_ms = ?
               WHERE agent_run_id = ? AND status = ?""",
            (
                status,
                json.dumps([*run.steps, status]),
                None if output is None else output.model_dump_json(),
                usage.input_tokens,
                usage.output_tokens,
                usage.total_tokens,
                error,
                finished_at,
                duration_ms(run.started_at, finished_at),
                run.agent_run_id,
                run.status,
            ),
        )
        if transitioned.rowcount != 1:
            raise ValueError("AGENT_RUN_TRANSITION_INVALID")
        connection.execute(
            "DELETE FROM agent_run_state WHERE agent_run_id = ?",
            (run.agent_run_id,),
        )
        close_activities_connection(connection, run,
                          "INTERRUPTED" if error == "PROCESS_INTERRUPTED" else status, error,
                          failed_activity_id)
        activities = project_activities(
            connection, run.session_id, (run.agent_run_id,)
        )[run.agent_run_id]
        stored = connection.execute(
            "SELECT input_tokens,output_tokens,total_tokens,duration_ms "
            "FROM agent_runs WHERE agent_run_id=?",
            (run.agent_run_id,),
        ).fetchone()
        metrics = {
            "request_count": sum(
                item["kind"] == "MODEL_REQUEST" for item in activities
            ),
            "observation_call_count": sum(
                item["kind"] == "TOOL"
                and item["purpose"] in {"OBSERVATION", "CATALOG"}
                for item in activities
            ),
            "input_tokens": stored["input_tokens"],
            "output_tokens": stored["output_tokens"],
            "total_tokens": stored["total_tokens"],
            "duration_ms": stored["duration_ms"],
        }
        append_event(
            connection,
            run.session_id,
            f"agent.{status.lower()}",
            {"agent_run_id": run.agent_run_id, "error": error,
             "agent_metrics": metrics,
             **({"continuation_stop": continuation_stop} if continuation_stop else {}),
             **({"diagnostics": diagnostics} if diagnostics is not None else {})},
        )
        _LOGGER.info(
            "agent terminal run_id=%s status=%s error=%s reason=%s schema_issues=%s requests=%d reads=%d "
            "input_tokens=%d output_tokens=%d total_tokens=%d duration_ms=%d",
            run.agent_run_id, status, error,
            diagnostics.get("reason") if isinstance(diagnostics, dict) else None,
            diagnostics.get("issues") if isinstance(diagnostics, dict) else None,
            metrics["request_count"], metrics["observation_call_count"],
            metrics["input_tokens"], metrics["output_tokens"],
            metrics["total_tokens"], metrics["duration_ms"],
        )
        commit_updates(connection, run, task_updates)
        if preparation is not None:
            tool_call_id, snapshots = preparation
            save_preparation_connection(
                connection,
                agent_run_id=run.agent_run_id,
                tool_call_id=tool_call_id,
                snapshots=snapshots,
            )
        _project_current(connection, run.agent_run_id, agent_projection)
        if workflow_draft is not None:
            publish_workflow_draft(connection, run.agent_run_id, workflow_draft)
        refresh_task(connection, run.session_id)


def fail_run(
    database: Path,
    run: AgentRun,
    code: str,
    agent_projection: AgentProjectionPort,
) -> None:
    finish_run(
        database,
        run,
        status="FAILED",
        output=None,
        usage=UsageDelta(0, 0, 0),
        error=code,
        agent_projection=agent_projection,
    )


def supersede_waiting_run(
    database: Path, run: AgentRun, agent_projection: AgentProjectionPort
) -> bool:
    finished_at = utc_now()
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        claimed = connection.execute(
            """UPDATE agent_runs
               SET status = 'SUPERSEDED', steps_json = ?, error = NULL,
                   finished_at = ?, duration_ms = ?
               WHERE agent_run_id = ? AND status = 'WAITING_CONFIRMATION'""",
            (
                json.dumps([*run.steps, "SUPERSEDED"]),
                finished_at,
                duration_ms(run.started_at, finished_at),
                run.agent_run_id,
            ),
        )
        if claimed.rowcount != 1:
            return False
        connection.execute(
            """UPDATE agent_actions SET status = 'SUPERSEDED'
               WHERE agent_run_id = ? AND status = 'PENDING'""",
            (run.agent_run_id,),
        )
        connection.execute(
            "DELETE FROM agent_run_state WHERE agent_run_id = ?",
            (run.agent_run_id,),
        )
        close_activities_connection(connection, run, "INTERRUPTED")
        append_event(
            connection,
            run.session_id,
            "agent.superseded",
            {"agent_run_id": run.agent_run_id},
        )
        _project_current(connection, run.agent_run_id, agent_projection)
        return True


def publish_workflow_draft(connection, agent_run_id: str, payload: dict) -> None:
    row = connection.execute(
        "SELECT * FROM workflow_drafts WHERE agent_run_id=?", (agent_run_id,),
    ).fetchone()
    changed = connection.execute(
        "UPDATE workflow_drafts SET payload_json=? WHERE agent_run_id=? AND payload_json IS NULL",
        (json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False), agent_run_id),
    ).rowcount
    if changed != 1:
        raise ValueError("WORKFLOW_DRAFT_PUBLICATION_INVALID")
    append_event(connection, row["session_id"], "workflow_draft.published", {
        "workflow_draft_id": row["draft_id"], "agent_run_id": agent_run_id,
    })


def cancel_run_connection(connection, run_id, now):
    """Terminalize Agent state inside the interaction control transaction."""
    run = get_run_connection(connection, run_id)
    if run is None or run.status not in {"RUNNING", "WAITING_CONFIRMATION", "WAITING_EXECUTION"}:
        return
    connection.execute(
        "UPDATE agent_runs SET status='CANCELLED',error='AGENT_STOPPED',finished_at=?,steps_json=?,duration_ms=? WHERE agent_run_id=?",
        (now, json.dumps([*run.steps, "CANCELLED"]), duration_ms(run.started_at, now), run_id),
    )
    connection.execute("UPDATE agent_actions SET status='CANCELLED',error='AGENT_STOPPED' WHERE agent_run_id=? AND status='PENDING'", (run_id,))
    connection.execute("DELETE FROM agent_run_state WHERE agent_run_id=?", (run_id,))
    close_activities_connection(connection, run, "INTERRUPTED", "AGENT_STOPPED")
    append_event(connection, run.session_id, "agent.cancelled", {"agent_run_id": run_id, "error": "AGENT_STOPPED"})


def _stop_fenced(connection, run_id):
    return connection.execute("SELECT 1 FROM run_controls WHERE agent_run_id=?", (run_id,)).fetchone() is not None
