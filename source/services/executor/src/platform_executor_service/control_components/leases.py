from __future__ import annotations

import json
import os
import secrets
import shutil
from uuid import uuid4

from ..database import connect_database, utc_now
from ..errors import ExecutionServiceError
from ..task_execution import ReadyTaskSpec, TaskLease, WorkerCapacity, require_lease, select_ready_tasks
from .recovery import RecoveryControl
from .shared import ControlComponent, ControlContext, _after


class LeaseControl(ControlComponent):
    def __init__(self, context: ControlContext, recovery: RecoveryControl) -> None:
        super().__init__(context)
        self.recovery = recovery
    def heartbeat(self, body: dict) -> None:
        worker = WorkerCapacity.from_body(body)
        if body.get("catalog_digest") != self.catalog_digest:
            raise ExecutionServiceError("WORKER_CATALOG_MISMATCH")
        expected = self.config["workerRuntimes"].get(worker.runtime)
        if expected != {"slots": worker.slots, "memoryBytes": worker.memory_bytes}:
            raise ExecutionServiceError("WORKER_CAPACITY_MISMATCH")
        with connect_database(self.database) as connection:
            connection.execute(
                """INSERT INTO worker_heartbeats(
                       worker_id, runtime, catalog_digest, slots, memory_bytes, last_seen_at
                   ) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(worker_id) DO UPDATE SET
                       runtime=excluded.runtime, catalog_digest=excluded.catalog_digest,
                       slots=excluded.slots, memory_bytes=excluded.memory_bytes,
                       last_seen_at=excluded.last_seen_at""",
                (
                    worker.worker_id,
                    worker.runtime,
                    self.catalog_digest,
                    worker.slots,
                    worker.memory_bytes,
                    utc_now(),
                ),
            )

    def claim(self, body: dict) -> dict | None:
        worker = WorkerCapacity.from_body(body)
        self.heartbeat(body)
        self.recovery.reconcile()
        purpose = "prepare:" + str(uuid4())
        staging = (
            self.artifacts.work_root / ".preparing" / purpose.removeprefix("prepare:")
        )
        moved = None
        committed = False
        with connect_database(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            candidates = select_ready_tasks(connection, worker, utc_now())
            if not candidates:
                return None
            node = candidates[0]
            try:
                inputs = self.artifacts.input_snapshot(connection, node)
            except ExecutionServiceError as error:
                if str(error) != "CHECKPOINT_MISSING":
                    raise
                self._fail_node(connection, node["job_id"], node["node_id"], str(error))
                return None
            for item in inputs.values():
                connection.execute(
                    "INSERT OR IGNORE INTO job_object_pins(job_id,checksum,purpose) VALUES (?,?,?)",
                    (node["job_id"], item["checksum"], purpose),
                )
        try:
            try:
                self.artifacts.prepare_workspace(inputs, staging)
            except ExecutionServiceError as error:
                if str(error) != "CHECKPOINT_MISSING":
                    raise
                with connect_database(self.database) as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    if any(
                        (row["job_id"], row["node_id"])
                        == (node["job_id"], node["node_id"])
                        for row in select_ready_tasks(connection, worker, utc_now())
                    ):
                        self._fail_node(
                            connection, node["job_id"], node["node_id"], str(error)
                        )
                return None
            attempt_id = str(uuid4())
            lease_id, token = str(uuid4()), secrets.token_urlsafe(32)
            moved = self.artifacts.attempt_workspace(
                {
                    "job_id": node["job_id"],
                    "node_id": node["node_id"],
                    "lease_id": lease_id,
                }
            )
            moved.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, moved)
            with connect_database(self.database) as connection:
                connection.execute("BEGIN IMMEDIATE")
                if not any(
                    (row["job_id"], row["node_id"]) == (node["job_id"], node["node_id"])
                    for row in select_ready_tasks(connection, worker, utc_now())
                ):
                    return None
                attempt_number = (
                    connection.execute(
                        "SELECT COUNT(*) FROM node_attempts WHERE job_id=? AND node_id=?",
                        (node["job_id"], node["node_id"]),
                    ).fetchone()[0]
                    + 1
                )
                expires = _after(int(self.config["leaseSeconds"]))
                connection.execute(
                    "UPDATE job_nodes SET status='LEASED', started_at=COALESCE(started_at,?) WHERE job_id=? AND node_id=?",
                    (utc_now(), node["job_id"], node["node_id"]),
                )
                connection.execute(
                    """INSERT INTO node_attempts(attempt_id,job_id,node_id,attempt_number,
                       worker_id,lease_id,lease_token,lease_expires_at,status,started_at)
                       VALUES (?,?,?,?,?,?,?,?,'LEASED',?)""",
                    (
                        attempt_id,
                        node["job_id"],
                        node["node_id"],
                        attempt_number,
                        worker.worker_id,
                        lease_id,
                        token,
                        expires,
                        utc_now(),
                    ),
                )
            committed = True
            return TaskLease(
                attempt_id=attempt_id,
                lease_id=lease_id,
                lease_token=token,
                lease_expires_at=expires,
                attempt_number=attempt_number,
                worker_id=worker.worker_id,
                task=ReadyTaskSpec.from_row(node),
                workspace=moved,
                inputs=self.artifacts.workspace_inputs(inputs, moved),
            ).worker_payload()
        finally:
            with connect_database(self.database) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "DELETE FROM job_object_pins WHERE job_id=? AND purpose=?",
                    (node["job_id"], purpose),
                )
                self.artifacts.prune_unpinned(connection)
            if staging.exists():
                shutil.rmtree(staging)
            if moved is not None and not committed:
                shutil.rmtree(moved)

    def heartbeat_lease(self, lease_id: str, body: dict) -> None:
        with connect_database(self.database) as connection:
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = require_lease(connection, lease_id, body, utc_now())
            except ExecutionServiceError as error:
                if (
                    str(error) == "LEASE_STALE"
                    and body.get("remote_state") is not None
                    and self._record_terminal_remote_state(
                        connection, lease_id, body
                    )
                ):
                    return
                raise
            connection.execute(
                "UPDATE node_attempts SET status = 'RUNNING', lease_expires_at = ? WHERE lease_id = ?",
                (_after(int(self.config["leaseSeconds"])), lease_id),
            )
            connection.execute(
                "UPDATE job_nodes SET status = 'RUNNING' WHERE job_id = ? AND node_id = ? AND status IN ('LEASED','RUNNING')",
                (row["job_id"], row["node_id"]),
            )
            if body.get("remote_state") is not None:
                connection.execute(
                    "UPDATE job_nodes SET remote_state_json = ? WHERE job_id = ? AND node_id = ?",
                    (
                        json.dumps(
                            body["remote_state"],
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        row["job_id"],
                        row["node_id"],
                    ),
                )

    @staticmethod
    def _record_terminal_remote_state(connection, lease_id: str, body: dict) -> bool:
        """Accept cancellation evidence after Control has fenced the attempt.

        Cancellation makes an ordinary heartbeat stale immediately so that no
        result can be published.  A cancelled remote_http attempt may still use
        its original lease token once to persist truthful remote termination
        evidence; it cannot revive the lease or change job/node status.
        """
        row = connection.execute(
            """SELECT a.lease_token,a.status AS attempt_status,a.job_id,a.node_id,
                      n.runtime,j.status AS job_status
                 FROM node_attempts a
                 JOIN job_nodes n ON n.job_id=a.job_id AND n.node_id=a.node_id
                 JOIN executor_jobs j ON j.job_id=a.job_id
                WHERE a.lease_id=?""",
            (lease_id,),
        ).fetchone()
        if (
            row is None
            or row["lease_token"] != body.get("lease_token")
            or row["runtime"] != "remote_http"
            or row["attempt_status"] != "CANCELLED"
            or row["job_status"] not in {"CANCELLED", "TIMED_OUT", "FAILED"}
        ):
            return False
        connection.execute(
            "UPDATE job_nodes SET remote_state_json=? WHERE job_id=? AND node_id=?",
            (
                json.dumps(
                    body["remote_state"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                row["job_id"],
                row["node_id"],
            ),
        )
        return True
