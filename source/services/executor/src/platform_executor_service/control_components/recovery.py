from __future__ import annotations

import logging
from threading import Lock

from ..database import connect_database, is_sqlite_busy_error, utc_now
from ..orchestration import JobAdmitted, LeaseLost, transition
from .shared import ControlComponent, ControlContext

logger = logging.getLogger(__name__)


class RecoveryControl(ControlComponent):
    def __init__(self, context: ControlContext) -> None:
        super().__init__(context)
        self._metrics_lock = Lock()
        self._reconcile_lock = Lock()
        self._metrics = {
            "lease_lost": 0,
            "attempt_workspace_removed": 0,
            "orphan_object_removed": 0,
        }

    def metrics(self) -> dict[str, int]:
        with self._metrics_lock:
            return dict(self._metrics)

    def _record(self, name: str, count: int) -> None:
        if count:
            with self._metrics_lock:
                self._metrics[name] += count

    def reconcile(self) -> None:
        # Worker claims and the runtime supervisor may request reconciliation at
        # the same time. Recovery is idempotent, so one in-process owner is
        # sufficient and avoids making every polling Worker contend for the
        # SQLite write lock.
        if not self._reconcile_lock.acquire(blocking=False):
            return
        try:
            self._reconcile()
        except BaseException as error:
            if not is_sqlite_busy_error(error):
                raise
            # A heartbeat or completion owns the database briefly. The next
            # supervisor tick (or claim) retries recovery from durable state.
            logger.warning("executor reconciliation deferred: sqlite busy")
        finally:
            self._reconcile_lock.release()

    def _reconcile(self) -> None:
        now = utc_now()
        lost_count = 0
        with connect_database(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            expired = connection.execute(
                "SELECT * FROM node_attempts WHERE status IN ('LEASED','RUNNING') AND lease_expires_at < ?",
                (now,),
            ).fetchall()
            for attempt in expired:
                changed = connection.execute(
                    "UPDATE node_attempts SET status = 'LOST', error_code = 'WORKER_LOST', finished_at = ? WHERE attempt_id = ? AND status IN ('LEASED','RUNNING')",
                    (now, attempt["attempt_id"]),
                ).rowcount
                if not changed:
                    continue
                lost_count += 1
                node = connection.execute(
                    "SELECT lost_count FROM job_nodes WHERE job_id = ? AND node_id = ?",
                    (attempt["job_id"], attempt["node_id"]),
                ).fetchone()
                commands = transition(
                    self._workflow_snapshot(connection, attempt["job_id"]),
                    LeaseLost(attempt["node_id"], node["lost_count"]),
                )
                if commands.retry_node_ids:
                    connection.execute(
                        "UPDATE job_nodes SET status = 'READY', lost_count = 1 WHERE job_id = ? AND node_id = ?",
                        (attempt["job_id"], attempt["node_id"]),
                    )
                else:
                    self._fail_node(
                        connection, attempt["job_id"], attempt["node_id"], "WORKER_LOST"
                    )
            overdue = connection.execute(
                "SELECT job_id FROM executor_jobs WHERE status = 'RUNNING' AND stage NOT IN ('FINALIZING','COMMIT_PENDING') AND julianday(deadline_at) <= julianday(?)",
                (now,),
            ).fetchall()
            for job in overdue:
                connection.execute(
                    "UPDATE executor_jobs SET status = 'TIMED_OUT', stage = 'TIMED_OUT', error = 'JOB_DEADLINE_EXCEEDED', cancel_requested = 1, finished_at = ? WHERE job_id = ?",
                    (now, job["job_id"]),
                )
                connection.execute(
                    "UPDATE job_nodes SET status = 'CANCELLED', error = 'JOB_DEADLINE_EXCEEDED', finished_at = ? WHERE job_id = ? AND status IN ('WAITING','READY','LEASED','RUNNING')",
                    (now, job["job_id"]),
                )
                connection.execute(
                    "UPDATE node_attempts SET status='CANCELLED', error_code='JOB_DEADLINE_EXCEEDED', finished_at=? WHERE job_id=? AND status IN ('LEASED','RUNNING')",
                    (now, job["job_id"]),
                )
                connection.execute(
                    "DELETE FROM job_object_pins WHERE job_id=? AND purpose NOT LIKE 'prepare:%'",
                    (job["job_id"],),
                )
            if overdue:
                self.artifacts.prune_unpinned(connection)
        self._record("lease_lost", lost_count)
        with self.artifacts.object_publication():
            with connect_database(self.database) as connection:
                removed_objects = self.artifacts.prune_orphan_files(connection)
        self._record("orphan_object_removed", removed_objects)
        with connect_database(self.database) as connection:
            for workspace in self.artifacts.work_root.glob("*/*/*"):
                job_id, node_id, lease_id = workspace.relative_to(
                    self.artifacts.work_root
                ).parts
                if job_id == ".preparing":
                    continue
                # A claim renames its workspace before committing its lease.
                # Only a persisted terminal attempt permits deletion.
                attempt = connection.execute(
                    """SELECT job_id, node_id, lease_id FROM node_attempts
                       WHERE lease_id=? AND job_id=? AND node_id=?
                         AND status NOT IN ('LEASED','RUNNING')""",
                    (lease_id, job_id, node_id),
                ).fetchone()
                if attempt is not None:
                    if self.artifacts.remove_attempt_workspace(attempt):
                        self._record("attempt_workspace_removed", 1)

    def recover(self) -> None:
        self._record(
            "attempt_workspace_removed", self.artifacts.recover_workspaces()
        )
        lost_count = 0
        with connect_database(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM job_object_pins WHERE purpose LIKE 'prepare:%'"
            )
            active = connection.execute(
                "SELECT * FROM node_attempts WHERE status IN ('LEASED','RUNNING')"
            ).fetchall()
            for attempt in active:
                changed = connection.execute(
                    "UPDATE node_attempts SET status = 'LOST', error_code = 'CONTROL_RESTART', finished_at = ? WHERE attempt_id = ? AND status IN ('LEASED','RUNNING')",
                    (utc_now(), attempt["attempt_id"]),
                ).rowcount
                if not changed:
                    continue
                lost_count += 1
                node = connection.execute(
                    "SELECT lost_count FROM job_nodes WHERE job_id = ? AND node_id = ?",
                    (attempt["job_id"], attempt["node_id"]),
                ).fetchone()
                commands = transition(
                    self._workflow_snapshot(connection, attempt["job_id"]),
                    LeaseLost(attempt["node_id"], node["lost_count"]),
                )
                if commands.retry_node_ids:
                    connection.execute(
                        "UPDATE job_nodes SET status = 'READY', lost_count = 1 WHERE job_id = ? AND node_id = ?",
                        (attempt["job_id"], attempt["node_id"]),
                    )
                else:
                    self._fail_node(
                        connection, attempt["job_id"], attempt["node_id"], "WORKER_LOST"
                    )
            jobs = connection.execute(
                "SELECT job_id FROM executor_jobs WHERE status = 'RUNNING'"
            ).fetchall()
            for job in jobs:
                self._advance(connection, job["job_id"], JobAdmitted())
            self.artifacts.prune_unpinned(connection)
        self._record("lease_lost", lost_count)
        with self.artifacts.object_publication():
            with connect_database(self.database) as connection:
                removed_objects = self.artifacts.prune_orphan_files(connection)
        self._record("orphan_object_removed", removed_objects)
