"""Durable stop requests; Executor remains authoritative about publication races."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from ..database import append_event, begin_immediate, connect_database, utc_now
from ..errors import ConfirmationConflictError, ResourceNotFoundError, WriteLockUnavailableError
from ..application.task_api import refresh_task
from .actors import DEFAULT_LOCAL_ACTOR_SUBJECT


def stop_requested(database, run_id):
    with connect_database(database) as connection:
        return (
            connection.execute(
                """SELECT 1 FROM run_controls
                   WHERE agent_run_id=? AND status IN ('REQUESTED','PROCESSING')""",
                (run_id,),
            ).fetchone()
            is not None
        )


def request_stop(
    database,
    session_id,
    project_id,
    request_id,
    run_id,
    requested_by=DEFAULT_LOCAL_ACTOR_SUBJECT,
):
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        session = connection.execute(
            "SELECT * FROM sessions WHERE session_id=? AND project_id=?",
            (session_id, project_id),
        ).fetchone()
        if session is None:
            raise ResourceNotFoundError("SESSION_NOT_FOUND")
        existing = connection.execute(
            "SELECT * FROM run_controls WHERE session_id=? AND request_id=?",
            (session_id, request_id),
        ).fetchone()
        if existing:
            if existing["agent_run_id"] != run_id:
                raise ConfirmationConflictError("CONTROL_REQUEST_CONFLICT")
            return dict(existing)
        if run_id not in {session["agent_run_id"], session["pending_agent_run_id"]}:
            raise ConfirmationConflictError("CONTROL_TARGET_NOT_CURRENT")
        connection.execute(
            """INSERT INTO run_controls(
                   session_id,request_id,agent_run_id,requested_by,status,created_at
               ) VALUES (?,?,?,?,'REQUESTED',?)""",
            (session_id, request_id, run_id, requested_by, utc_now()),
        )
        append_event(
            connection,
            session_id,
            "control.requested",
            {
                "agent_run_id": run_id,
                "request_id": request_id,
                "requested_by": requested_by,
            },
        )
        return {
            "session_id": session_id,
            "request_id": request_id,
            "agent_run_id": run_id,
            "status": "REQUESTED",
            "requested_by": requested_by,
        }


def _lease_until(seconds: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=seconds)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def claim_controls(database, owner, *, limit=32, lease_seconds=30, wait=True):
    """Claim a bounded FIFO batch, reclaiming only expired work.

    SQLite's ``BEGIN IMMEDIATE`` serializes selection and generation increment.
    Every later completion must present the returned owner/generation pair.
    """

    if not isinstance(owner, str) or not owner or limit < 1 or lease_seconds < 1:
        raise ValueError("CONTROL_CLAIM_INVALID")
    with connect_database(database) as connection:
        # Most dispatcher polls have no eligible controls. Keep that path
        # read-only so unrelated auth/workflow writes cannot block it. This is
        # only a hint: claim eligibility is re-read under the write lock below.
        candidate = connection.execute(
            """SELECT 1 FROM run_controls
               WHERE status='REQUESTED'
                  OR (status='PROCESSING' AND (claim_until IS NULL OR claim_until<=?))
               LIMIT 1""",
            (utc_now(),),
        ).fetchone()
        if candidate is None:
            return ()
        if not begin_immediate(connection, wait=wait):
            raise WriteLockUnavailableError("KERNEL_WRITE_BUSY")
        now = utc_now()
        rows = connection.execute(
            """SELECT * FROM run_controls
               WHERE status='REQUESTED'
                  OR (status='PROCESSING' AND (claim_until IS NULL OR claim_until<=?))
               ORDER BY created_at, session_id, request_id
               LIMIT ?""",
            (now, limit),
        ).fetchall()
        claimed = []
        for row in rows:
            generation = int(row["claim_generation"]) + 1
            claim_until = _lease_until(lease_seconds)
            changed = connection.execute(
                """UPDATE run_controls
                   SET status='PROCESSING',claim_owner=?,claim_generation=?,claim_until=?
                   WHERE session_id=? AND request_id=?
                     AND (status='REQUESTED'
                          OR (status='PROCESSING' AND (claim_until IS NULL OR claim_until<=?)))""",
                (
                    owner,
                    generation,
                    claim_until,
                    row["session_id"],
                    row["request_id"],
                    now,
                ),
            ).rowcount
            if changed:
                claimed.append(
                    dict(row)
                    | {
                        "status": "PROCESSING",
                        "claim_owner": owner,
                        "claim_generation": generation,
                        "claim_until": claim_until,
                    }
                )
        return tuple(claimed)


def pending_controls(database):
    """Read-only FIFO inventory retained for diagnostics and tests."""

    with connect_database(database) as connection:
        return tuple(
            dict(row)
            for row in connection.execute(
                """SELECT * FROM run_controls WHERE status='REQUESTED'
                   ORDER BY created_at, session_id, request_id"""
            )
        )


def release_control(database, control):
    """Return a still-owned control request to the durable FIFO queue."""

    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        return (
            connection.execute(
                """UPDATE run_controls
                   SET status='REQUESTED',claim_owner=NULL,claim_until=NULL
                   WHERE session_id=? AND request_id=? AND status='PROCESSING'
                     AND claim_owner=? AND claim_generation=? AND claim_until>?""",
                (
                    control["session_id"],
                    control["request_id"],
                    control["claim_owner"],
                    control["claim_generation"],
                    utc_now(),
                ),
            ).rowcount
            == 1
        )


def finalize_stop(database, control, *, wait=True):
    """Fence work, returning None when this actor no longer owns the control."""
    run_id, session_id = control["agent_run_id"], control["session_id"]
    required_claim = {"claim_owner", "claim_generation"}
    if not required_claim <= control.keys() or not control["claim_owner"]:
        raise ValueError("CONTROL_CLAIM_REQUIRED")
    with connect_database(database) as c:
        if not begin_immediate(c, wait=wait):
            raise WriteLockUnavailableError("KERNEL_WRITE_BUSY")
        now = utc_now()
        owned = c.execute(
            """SELECT 1 FROM run_controls
               WHERE session_id=? AND request_id=? AND status='PROCESSING'
                 AND claim_owner=? AND claim_generation=? AND claim_until>?""",
            (
                session_id,
                control["request_id"],
                control["claim_owner"],
                control["claim_generation"],
                now,
            ),
        ).fetchone()
        if owned is None:
            return None
        if c.execute(
            "SELECT 1 FROM workflow_runs WHERE agent_run_id=? AND status IN ('QUEUED','RUNNING')",
            (run_id,),
        ).fetchone():
            c.execute(
                """UPDATE run_controls
                   SET status='REQUESTED',claim_owner=NULL,claim_until=NULL
                   WHERE session_id=? AND request_id=? AND status='PROCESSING'
                     AND claim_owner=? AND claim_generation=?""",
                (
                    session_id,
                    control["request_id"],
                    control["claim_owner"],
                    control["claim_generation"],
                ),
            )
            return False
        from ..application.agent_api import (
            cancel_run_connection,
            get_run_connection,
        )
        from .state_projection import project_agent_run

        if c.execute(
            "SELECT 1 FROM agent_runs WHERE agent_run_id=?", (run_id,)
        ).fetchone():
            cancel_run_connection(c, run_id, now)
            project_agent_run(c, get_run_connection(c, run_id))
        c.execute(
            "UPDATE confirmations SET status='SUPERSEDED',decision='SUPERSEDE',decision_state=NULL WHERE agent_run_id=? AND status='PENDING'",
            (run_id,),
        )
        c.execute(
            "UPDATE message_requests SET routing_status='FAILED',error_code='AGENT_STOPPED' WHERE agent_run_id=? AND routing_status IN ('ROUTING_REQUESTED','ROUTING_RUNNING','DECIDED','START_REQUESTED') AND NOT EXISTS (SELECT 1 FROM agent_runs WHERE agent_run_id=?)",
            (run_id, run_id),
        )
        c.execute(
            "UPDATE sessions SET pending_agent_run_id=NULL,agent_start_state=NULL WHERE session_id=? AND pending_agent_run_id=?",
            (session_id, run_id),
        )
        c.execute(
            """UPDATE run_controls
               SET status='COMPLETE',claim_owner=NULL,claim_until=NULL
               WHERE session_id=? AND request_id=? AND status='PROCESSING'
                 AND claim_owner=? AND claim_generation=? AND claim_until>?""",
            (
                session_id,
                control["request_id"],
                control["claim_owner"],
                control["claim_generation"],
                now,
            ),
        )
        append_event(
            c,
            session_id,
            "control.complete",
            {
                "agent_run_id": run_id,
                "request_id": control["request_id"],
                "requested_by": control["requested_by"],
            },
        )
        refresh_task(c, session_id)
    return True


def validate_correction(connection, session_id, request_id, target, *, replay=False):
    existing = connection.execute(
        "SELECT target_agent_run_id FROM message_corrections WHERE session_id=? AND request_id=?",
        (session_id, request_id),
    ).fetchone()
    if existing:
        if existing[0] != target:
            raise ConfirmationConflictError("MESSAGE_REQUEST_CONFLICT")
        return
    if target is None:
        return
    if replay:
        raise ConfirmationConflictError("MESSAGE_REQUEST_CONFLICT")
    row = connection.execute(
        "SELECT status FROM agent_runs WHERE session_id=? AND agent_run_id=?",
        (session_id, target),
    ).fetchone()
    if row is None:
        # Router/admission reserves the AgentRun ID before AgentRun creation.
        # A stopped reservation is still a durable correction target.
        reservation = connection.execute(
            "SELECT routing_status FROM message_requests WHERE session_id=? AND agent_run_id=?",
            (session_id, target),
        ).fetchone()
        if reservation is None:
            raise ResourceNotFoundError("AGENT_RUN_NOT_FOUND")
        if reservation["routing_status"] != "FAILED":
            raise ConfirmationConflictError("CORRECTION_REQUIRES_STOP_OR_QUEUE")
    elif row["status"] in {"RUNNING", "WAITING_EXECUTION"}:
        raise ConfirmationConflictError("CORRECTION_REQUIRES_STOP_OR_QUEUE")
    connection.execute(
        "INSERT INTO message_corrections VALUES (?,?,?)",
        (session_id, request_id, target),
    )


class ControlRequests:
    """Persistence boundary consumed by the runtime supervisor."""

    def __init__(self, database):
        self.database = database
        self.owner = f"kernel-runtime-{uuid4().hex}"

    def claim(self, *, limit=32):
        return claim_controls(self.database, self.owner, limit=limit, wait=False)

    def finalize(self, control):
        return finalize_stop(self.database, control, wait=False)

    def deciding(self, run_id):
        with connect_database(self.database) as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM confirmations WHERE agent_run_id=? AND status='PENDING' AND decision_state='RUNNING'",
                    (run_id,),
                ).fetchone()
                is not None
            )

    def requested(self, run_id):
        return stop_requested(self.database, run_id)
