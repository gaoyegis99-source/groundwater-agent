from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

from ...contracts import JobStatusV6, DataVersionRefV2
from ...database import append_event, connect_database, duration_ms, read_session_events, utc_now
from ..models import DispatchWork, WorkflowRun
from .records import row_to_run


_PRE_SUBMIT_BLOCK_CODES = frozenset({
    "EXECUTOR_DEFINITION_SET_MISMATCH",
    "WORKFLOW_DEFINITION_UNAVAILABLE",
    "WORKFLOW_CONFIRMATION_NOT_CURRENT",
    "WORKFLOW_EXECUTION_TARGET_MISMATCH",
})


def save_job_status(database: Path, work: DispatchWork, status: JobStatusV6) -> bool:
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if status.status in {"QUEUED", "RUNNING"}:
            _record_stage(connection, work.run, status)
            _communication_changed(connection, work.run, None)
            connection.execute(
                """UPDATE workflow_dispatches
                   SET state = 'SUBMITTED', remote_job_id = ?, next_poll_at = ?,
                       last_communication_error = NULL, claim_state = NULL
                   WHERE workflow_run_id = ?""",
                (status.job_id, _next_poll(), work.run.run_id),
            )
            connection.execute(
                """UPDATE workflow_runs SET remote_job_id = ?, status = ?,
                       started_at = COALESCE(started_at, ?)
                   WHERE run_id = ?""",
                (status.job_id, status.status, utc_now(), work.run.run_id),
            )
            if work.run.remote_job_id is None or work.run.status != status.status:
                _event(connection, work.run, f"job.{status.status.lower()}",
                       status.status, status.job_id, status.error)
            return False
        if work.run.remote_job_id is None:
            connection.execute(
                "UPDATE workflow_runs SET started_at = COALESCE(started_at, ?) WHERE run_id = ?",
                (utc_now(), work.run.run_id),
            )
            _event(connection, work.run, f"job.{status.status.lower()}",
                   status.status, status.job_id, status.error)
        _finish_terminal(
            connection, work.run, status.status, status.job_id, status.outputs, status.error,
        )
    return True


def save_timeout(database: Path, run: WorkflowRun) -> None:
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _finish_terminal(
            connection, run, "TIMED_OUT", run.remote_job_id, (),
            "WORKFLOW_DEADLINE_EXCEEDED",
        )


def save_cancelled(database: Path, run: WorkflowRun) -> None:
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _finish_terminal(connection, run, "CANCELLED", run.remote_job_id, (), "AGENT_STOPPED")


def save_rejection(database: Path, run: WorkflowRun, code: str) -> None:
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _finish_terminal(connection, run, "FAILED", None, (), code)


def release_communication_error(database: Path, run_id: str, code: str | None) -> None:
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,),
        ).fetchone()
        _communication_changed(connection, row_to_run(row), code)
        connection.execute(
            """UPDATE workflow_dispatches
               SET state = CASE WHEN state = 'SUBMITTING' THEN 'SUBMIT_UNKNOWN' ELSE state END,
                   next_poll_at = ?, last_communication_error = ?, claim_state = NULL
               WHERE workflow_run_id = ?""",
            (
                _next_poll(
                    30
                    if code == "RECONCILIATION_REQUIRED"
                    or code in _PRE_SUBMIT_BLOCK_CODES
                    else 1
                ),
                code,
                run_id,
            ),
        )


def release_pre_submit_block(database: Path, run_id: str, code: str) -> None:
    """Release a claimed request that was proven not to have been submitted."""
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,),
        ).fetchone()
        _communication_changed(connection, row_to_run(row), code)
        connection.execute(
            """UPDATE workflow_dispatches
               SET state = 'REQUESTED', next_poll_at = ?,
                   last_communication_error = ?, claim_state = NULL
               WHERE workflow_run_id = ?""",
            (
                _next_poll(30 if code in _PRE_SUBMIT_BLOCK_CODES else 1),
                code,
                run_id,
            ),
        )


def _finish_terminal(
    connection: sqlite3.Connection,
    run: WorkflowRun,
    job_status: str,
    job_id: str | None,
    outputs: tuple[DataVersionRefV2, ...],
    error: str | None,
) -> None:
    _communication_changed(connection, run, None)
    succeeded = job_status == "SUCCEEDED"
    final_status = "SUCCEEDED" if succeeded else (
        job_status if job_status in {"TIMED_OUT", "CANCELLED"} else "FAILED"
    )
    finished = utc_now()
    outputs_json = json.dumps(
        [item.as_storage_dict() for item in outputs],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    started_at = run.started_at or run.created_at
    connection.execute(
        """UPDATE workflow_runs
           SET status = ?, remote_job_id = COALESCE(remote_job_id, ?),
               outputs_json = ?, error = ?, finished_at = ?, duration_ms = ?
           WHERE run_id = ?""",
        (
            final_status,
            job_id,
            outputs_json,
            error,
            finished,
            duration_ms(started_at, finished),
            run.run_id,
        ),
    )
    connection.execute(
        """UPDATE workflow_dispatches
           SET state = ?, remote_job_id = COALESCE(remote_job_id, ?),
               claim_state = NULL, last_communication_error = NULL
           WHERE workflow_run_id = ?""",
        (job_status, job_id, run.run_id),
    )
    _event(connection, run, f"workflow.{final_status.lower()}", job_status, job_id, error)
    if succeeded:
        connection.execute(
            """UPDATE workflow_dispatches SET state = 'REQUESTED'
               WHERE workflow_run_id = (
                   SELECT run_id FROM workflow_runs
                   WHERE agent_run_id = ? AND position > ? AND status = 'QUEUED'
                   ORDER BY position LIMIT 1
               ) AND state = 'BLOCKED'""",
            (run.agent_run_id, run.position),
        )
    else:
        blocked = connection.execute(
            """SELECT run_id, tool_call_id FROM workflow_runs
               WHERE agent_run_id = ? AND position > ? AND status = 'QUEUED'
               ORDER BY position""",
            (run.agent_run_id, run.position),
        ).fetchall()
        for item in blocked:
            blocked_status = "CANCELLED" if job_status == "CANCELLED" else "FAILED"
            connection.execute(
                """UPDATE workflow_runs SET status = ?,
                       error = ?, finished_at = ?, duration_ms = 0
                   WHERE run_id = ?""",
                (blocked_status, error, finished, item["run_id"]),
            )
            connection.execute(
                """UPDATE workflow_dispatches
                   SET state = ?, claim_state = NULL
                   WHERE workflow_run_id = ?""",
                (blocked_status, item["run_id"]),
            )
            append_event(
                connection,
                run.session_id,
                f"workflow.{blocked_status.lower()}",
                {
                    "workflow_run_id": item["run_id"],
                    "agent_run_id": run.agent_run_id,
                    "remote_job_id": None,
                    "status": blocked_status,
                    "error": error,
                    "blocked_by_workflow_run_id": run.run_id,
                },
            )
    if run.confirmation_id:
        _record_recovery_terminal(connection, run)


def _record_recovery_terminal(connection: sqlite3.Connection, run: WorkflowRun) -> None:
    if run.attempt <= 1 or run.confirmation_id is None:
        return
    rows = connection.execute(
        "SELECT status FROM workflow_runs WHERE confirmation_id=?",
        (run.confirmation_id,),
    ).fetchall()
    if not rows or any(row["status"] in {"QUEUED", "RUNNING"} for row in rows):
        return
    failed = any(row["status"] != "SUCCEEDED" for row in rows)
    event_type = "workflow.recovery_failed" if failed else "workflow.recovery_completed"
    existing = read_session_events(connection, run.session_id, event_type)
    if any(item["data"].get("confirmation_id") == run.confirmation_id for item in existing):
        return
    append_event(connection, run.session_id, event_type, {
        "confirmation_id": run.confirmation_id,
        "attempt": run.attempt,
        "status": "FAILED" if failed else "SUCCEEDED",
        "error": "WORKFLOW_FAILED" if failed else None,
    })


def _record_stage(
    connection: sqlite3.Connection, run: WorkflowRun, status: JobStatusV6,
) -> None:
    stage = "QUEUED" if status.status == "QUEUED" else {
        "DAG": "EXECUTING", "FINALIZING": "PUBLISHING", "COMMIT_PENDING": "PUBLISHING",
    }.get(status.stage)
    events = read_session_events(connection, run.session_id, "workflow.stage_changed")
    previous = next((item["data"]["stage"] for item in reversed(events)
                     if item["data"]["workflow_run_id"] == run.run_id), None)
    if stage != previous:
        append_event(connection, run.session_id, "workflow.stage_changed", {
            "workflow_run_id": run.run_id, "agent_run_id": run.agent_run_id,
            "model_id": run.model_id, "model_version": run.model_version,
            "model_checksum": run.model_checksum, "workflow_id": run.workflow_id,
            "workflow_version": run.workflow_version, "workflow_checksum": run.workflow_checksum,
            "stage": stage,
        })


def _communication_changed(
    connection: sqlite3.Connection, run: WorkflowRun, error: str | None,
) -> None:
    row = connection.execute(
        "SELECT last_communication_error FROM workflow_dispatches WHERE workflow_run_id = ?",
        (run.run_id,),
    ).fetchone()
    previous = communication_error_code(row["last_communication_error"])
    current = communication_error_code(error)
    if previous != current:
        append_event(connection, run.session_id, "workflow.communication_changed", {
            "workflow_run_id": run.run_id, "agent_run_id": run.agent_run_id,
            "model_id": run.model_id, "model_version": run.model_version,
            "model_checksum": run.model_checksum, "workflow_id": run.workflow_id,
            "workflow_version": run.workflow_version, "workflow_checksum": run.workflow_checksum,
            "error_code": current,
        })


def communication_error_code(error: str | None) -> str | None:
    if error is None:
        return None
    if error in _PRE_SUBMIT_BLOCK_CODES:
        return error
    if error == "RECONCILIATION_REQUIRED":
        return error
    return "EXECUTION_SERVICE_UNAVAILABLE"

def _next_poll(seconds: int = 1) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=seconds)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _event(
    connection: sqlite3.Connection,
    run: WorkflowRun,
    event_type: str,
    job_status: str,
    job_id: str | None,
    error: str | None,
) -> None:
    append_event(
        connection,
        run.session_id,
        event_type,
        {
            "workflow_run_id": run.run_id,
            "model_id": run.model_id,
            "model_version": run.model_version,
            "model_checksum": run.model_checksum,
            "workflow_id": run.workflow_id,
            "workflow_version": run.workflow_version,
            "workflow_checksum": run.workflow_checksum,
            "remote_job_id": job_id,
            "status": job_status,
            "error": error,
        },
    )
