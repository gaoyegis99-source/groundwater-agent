from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from ...database import append_event, begin_immediate, connect_database, utc_now
from ...errors import ResourceNotFoundError, WorkflowDefinitionUnavailableError, WriteLockUnavailableError
from ...settings import ExecutableModelDefinition, ServiceBinding
from ...application.query_dtos import ConfirmedAction
from ..models import DispatchWork, WorkflowRun
from .records import row_to_run


def enqueue_batch(
    connection: sqlite3.Connection,
    *,
    agent_run_id: str,
    session_id: str,
    project_id: str,
    confirmation_id: str | None = None,
    actions: tuple[ConfirmedAction, ...],
    models: dict[str, ExecutableModelDefinition],
    services: dict[str, ServiceBinding],
) -> tuple[WorkflowRun, ...]:
    resolved = []
    for action in actions:
        model = models.get(action.model_id)
        if model is None or (
            model.tool_name,
            model.model_id,
            model.model_version,
            model.model_checksum,
            model.workflow_id,
            model.workflow_version,
            model.workflow_checksum,
            model.input_schema_checksum,
        ) != (
            action.tool,
            action.model_id,
            action.model_version,
            action.model_checksum,
            action.workflow_id,
            action.workflow_version,
            action.workflow_checksum,
            action.input_schema_checksum,
        ):
            raise WorkflowDefinitionUnavailableError("WORKFLOW_DEFINITION_UNAVAILABLE")
        service = services.get(model.execution_service)
        if service is None:
            raise WorkflowDefinitionUnavailableError("WORKFLOW_DEFINITION_UNAVAILABLE")
        resolved.append((action, model, service_target_digest(service)))
    created: list[WorkflowRun] = []
    now = utc_now()
    for index, (action, model, target_digest) in enumerate(resolved):
        run_id = str(uuid4())
        previous = connection.execute(
            """SELECT run_id, attempt FROM workflow_runs
               WHERE agent_run_id = ? AND tool_call_id = ?
               ORDER BY attempt DESC LIMIT 1""",
            (agent_run_id, action.tool_call_id),
        ).fetchone()
        attempt = 1 if previous is None else previous["attempt"] + 1
        retry_of = None if previous is None else previous["run_id"]
        deadline = (
            datetime.now(timezone.utc)
            + timedelta(seconds=model.deadline_seconds)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        connection.execute(
            """INSERT INTO workflow_runs(
                   run_id, agent_run_id, session_id, tool_call_id, confirmation_id,
                   attempt, retry_of_workflow_run_id, position, project_id,
                   model_id, model_version, model_checksum,
                   workflow_id, workflow_version, workflow_checksum, execution_service,
                   executor_id, arguments_json, input_references_json, status, remote_job_id,
                   outputs_json, error, created_at, started_at, finished_at,
                   duration_ms
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'QUEUED', NULL, '[]',
                         NULL, ?, NULL, NULL, NULL)""",
            (
                run_id,
                agent_run_id,
                session_id,
                action.tool_call_id,
                confirmation_id,
                attempt,
                retry_of,
                action.position,
                project_id,
                action.model_id,
                action.model_version,
                action.model_checksum,
                action.workflow_id,
                action.workflow_version,
                action.workflow_checksum,
                model.execution_service,
                model.executor_id,
                json.dumps(
                    action.arguments,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                json.dumps(
                    {name: reference.as_storage_dict() for name, reference in action.input_references.items()},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                now,
            ),
        )
        connection.execute(
            "INSERT INTO workflow_service_bindings(workflow_run_id,target_digest) "
            "VALUES (?,?)",
            (run_id, target_digest),
        )
        connection.execute(
            """INSERT INTO workflow_dispatches(
                   workflow_run_id, state, remote_job_id, deadline_at,
                   next_poll_at, last_communication_error, claim_state
               ) VALUES (?, ?, NULL, ?, ?, NULL, NULL)""",
            (run_id, "REQUESTED" if index == 0 else "BLOCKED", deadline, now),
        )
        created.append(get_run_connection(connection, project_id, run_id))
        append_event(
            connection,
            session_id,
            "workflow.queued",
            {
                "workflow_run_id": run_id,
                "confirmation_id": confirmation_id,
                "attempt": attempt,
                "retry_of_workflow_run_id": retry_of,
                "position": action.position,
                "model_id": action.model_id,
                "model_version": action.model_version,
                "model_checksum": action.model_checksum,
                "workflow_id": action.workflow_id,
                "workflow_version": action.workflow_version,
                "workflow_checksum": action.workflow_checksum,
            },
        )
        if attempt > 1:
            append_event(connection, session_id, "workflow.retry_queued", {
                "workflow_run_id": run_id,
                "confirmation_id": confirmation_id,
                "tool_call_id": action.tool_call_id,
                "attempt": attempt,
                "status": "QUEUED",
            })
    return tuple(created)


def service_target_digest(service: ServiceBinding) -> str:
    """Freeze the configured endpoint without persisting credentials in its URL."""

    return "sha256:" + hashlib.sha256(
        service.internal_base_url.rstrip("/").encode("utf-8")
    ).hexdigest()


def get_target_digest(database: Path, run_id: str) -> str | None:
    with connect_database(database) as connection:
        row = connection.execute(
            "SELECT target_digest FROM workflow_service_bindings WHERE workflow_run_id=?",
            (run_id,),
        ).fetchone()
    return None if row is None else str(row["target_digest"])


def recovery_source(
    database: Path, project_id: str, session_id: str, workflow_run_id: str,
) -> dict[str, object] | None:
    with connect_database(database) as connection:
        source = connection.execute(
            "SELECT * FROM workflow_runs WHERE run_id=? AND project_id=? AND session_id=?",
            (workflow_run_id, project_id, session_id),
        ).fetchone()
        if source is None or source["status"] not in {"FAILED", "TIMED_OUT", "CANCELLED"}:
            return None
        latest = connection.execute(
            """SELECT run_id FROM workflow_runs WHERE agent_run_id=? AND tool_call_id=?
               ORDER BY attempt DESC LIMIT 1""",
            (source["agent_run_id"], source["tool_call_id"]),
        ).fetchone()
        if latest["run_id"] != workflow_run_id:
            return None
        rows = connection.execute(
            """SELECT current.tool_call_id FROM workflow_runs current
               WHERE current.agent_run_id=? AND current.confirmation_id=?
                 AND current.position>=?
                 AND current.attempt=(SELECT MAX(other.attempt) FROM workflow_runs other
                     WHERE other.agent_run_id=current.agent_run_id
                       AND other.tool_call_id=current.tool_call_id)
                 AND (current.run_id=? OR current.status='FAILED')
               ORDER BY current.position""",
            (source["agent_run_id"], source["confirmation_id"],
             source["position"], workflow_run_id),
        ).fetchall()
        return {
            "run_id": source["run_id"], "agent_run_id": source["agent_run_id"],
            "confirmation_id": source["confirmation_id"],
            "tool_call_ids": tuple(row["tool_call_id"] for row in rows),
        }


def get_run(database: Path, project_id: str, run_id: str) -> WorkflowRun:
    with connect_database(database) as connection:
        return get_run_connection(connection, project_id, run_id)


def runs_for_agent(database: Path, agent_run_id: str) -> tuple[WorkflowRun, ...]:
    with connect_database(database) as connection:
        rows = connection.execute(
            """SELECT * FROM workflow_runs
               WHERE agent_run_id = ? ORDER BY position, attempt""",
            (agent_run_id,),
        ).fetchall()
    return tuple(row_to_run(row) for row in rows)


def get_run_connection(
    connection: sqlite3.Connection, project_id: str, run_id: str
) -> WorkflowRun:
    row = connection.execute(
        "SELECT * FROM workflow_runs WHERE project_id = ? AND run_id = ?",
        (project_id, run_id),
    ).fetchone()
    if row is None:
        raise ResourceNotFoundError("WORKFLOW_RUN_NOT_FOUND")
    return row_to_run(row)


def recover_dispatches(database: Path) -> None:
    with connect_database(database) as connection:
        connection.execute(
            """UPDATE workflow_dispatches
               SET state = 'SUBMIT_UNKNOWN', claim_state = NULL
               WHERE state = 'SUBMITTING'"""
        )
        connection.execute(
            "UPDATE workflow_dispatches SET claim_state = NULL WHERE claim_state IS NOT NULL"
        )


def claim_dispatch_batch(
    database: Path, limit: int, *, wait: bool = True
) -> tuple[DispatchWork, ...]:
    now = utc_now()
    with connect_database(database) as connection:
        if not begin_immediate(connection, wait=wait):
            raise WriteLockUnavailableError("KERNEL_WRITE_BUSY")
        rows = connection.execute(
            """SELECT d.*, w.project_id FROM workflow_dispatches d
               JOIN workflow_runs w ON w.run_id = d.workflow_run_id
               WHERE d.state IN ('REQUESTED', 'SUBMIT_UNKNOWN', 'SUBMITTED')
                 AND d.next_poll_at <= ? AND d.claim_state IS NULL
               ORDER BY w.created_at, w.position, w.run_id LIMIT ?""",
            (now, limit),
        ).fetchall()
        work = []
        for row in rows:
            claimed = connection.execute(
                """UPDATE workflow_dispatches SET claim_state = 'CLAIMED',
                       state = CASE WHEN state = 'REQUESTED'
                                    THEN 'SUBMITTING' ELSE state END
                   WHERE workflow_run_id = ? AND claim_state IS NULL""",
                (row["workflow_run_id"],),
            )
            if claimed.rowcount != 1:
                raise ValueError("WORKFLOW_DISPATCH_CLAIM_INVALID")
            run = get_run_connection(
                connection, row["project_id"], row["workflow_run_id"]
            )
            state = row["state"]
            work.append(DispatchWork(run, state, row["deadline_at"]))
    return tuple(work)
