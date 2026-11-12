from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from ..contracts import JobStatusV6
from ..database import connect_database, utc_now
from ..errors import JobNotFoundError
from .shared import ControlComponent


class ExecutionQueries(ControlComponent):
    def by_request(self, project_id: str, request_id: str) -> JobStatusV6:
        with connect_database(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM executor_jobs WHERE request_id=?", (request_id,)
            ).fetchone()
            if (
                row is None
                or project_id != self.project_id
                or json.loads(row["request_json"])["project_id"] != project_id
            ):
                raise JobNotFoundError("JOB_NOT_FOUND")
        return self._visible_status(row)

    def get(self, job_id: str) -> JobStatusV6:
        with connect_database(self.database) as connection:
            row = connection.execute(
                "SELECT * FROM executor_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None or json.loads(row["request_json"])["project_id"] != self.project_id:
            raise JobNotFoundError("JOB_NOT_FOUND")
        return self._visible_status(row)

    def cancel(self, job_id: str) -> JobStatusV6:
        with connect_database(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM executor_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None or json.loads(row["request_json"])["project_id"] != self.project_id:
                raise JobNotFoundError("JOB_NOT_FOUND")
            if row["status"] in {"QUEUED", "RUNNING"}:
                if row["stage"] in {"FINALIZING", "COMMIT_PENDING"}:
                    connection.execute(
                        """UPDATE executor_jobs
                           SET cancel_requested=1, error='JOB_CANCELLED'
                           WHERE job_id=? AND status='RUNNING'""",
                        (job_id,),
                    )
                else:
                    now = utc_now()
                    connection.execute(
                        "UPDATE executor_jobs SET status = 'CANCELLED', stage = 'CANCELLED', error = 'JOB_CANCELLED', cancel_requested = 1, finished_at = ? WHERE job_id = ?",
                        (now, job_id),
                    )
                    connection.execute(
                        "UPDATE job_nodes SET status = 'CANCELLED', error = 'JOB_CANCELLED', finished_at = ? WHERE job_id = ? AND status IN ('WAITING','READY','LEASED','RUNNING')",
                        (now, job_id),
                    )
                    connection.execute(
                        "UPDATE node_attempts SET status='CANCELLED', error_code='JOB_CANCELLED', finished_at=? WHERE job_id=? AND status IN ('LEASED','RUNNING')",
                        (now, job_id),
                    )
                    connection.execute(
                        "DELETE FROM job_object_pins WHERE job_id=? AND purpose NOT LIKE 'prepare:%'",
                        (job_id,),
                    )
                    self.artifacts.prune_unpinned(connection)
            current = connection.execute(
                "SELECT * FROM executor_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._visible_status(current)

    def runtime_statuses(self) -> dict[str, str]:
        threshold = (
            (datetime.now(timezone.utc) - timedelta(seconds=30))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        with connect_database(self.database) as connection:
            runtimes = {
                row["runtime"]
                for row in connection.execute(
                    "SELECT runtime FROM worker_heartbeats WHERE catalog_digest = ? AND last_seen_at >= ?",
                    (self.catalog_digest, threshold),
                )
            }
        return {
            runtime: ("UP" if runtime in runtimes else "UNAVAILABLE")
            for runtime in self.config["workerRuntimes"]
        }

    def ready(self) -> bool:
        return all(value == "UP" for value in self.runtime_statuses().values())

    def node_diagnostics(self, job_id: str) -> list[dict]:
        with connect_database(self.database) as connection:
            job = connection.execute(
                "SELECT request_json FROM executor_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if job is None or json.loads(job["request_json"])["project_id"] != self.project_id:
                raise JobNotFoundError("JOB_NOT_FOUND")
            rows = connection.execute(
                """SELECT n.*, (SELECT COUNT(*) FROM node_attempts a
                    WHERE a.job_id = n.job_id AND a.node_id = n.node_id) AS attempt_count
                    FROM job_nodes n WHERE n.job_id = ? ORDER BY n.node_id""", (job_id,)
            ).fetchall()
            return [dict(row) for row in rows]
