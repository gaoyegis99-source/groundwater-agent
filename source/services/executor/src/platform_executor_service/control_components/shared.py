from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from ..artifacts import ArtifactStore
from ..contracts import DataVersionRefV2, JobStatusV6
from ..data_client import DataClient
from ..database import utc_now
from ..orchestration import NodeSnapshot, TaskFailed, WorkflowSnapshot, transition


@dataclass
class ControlContext:
    database: Path
    artifacts: ArtifactStore
    config: dict
    data_client: DataClient
    project_id: str
    catalog_digest: str
    models: dict[tuple[str, str], dict]


class ControlComponent:
    def __init__(self, context: ControlContext) -> None:
        self.context = context

    @property
    def database(self) -> Path:
        return self.context.database

    @property
    def artifacts(self) -> ArtifactStore:
        return self.context.artifacts

    @property
    def config(self) -> dict:
        return self.context.config

    @property
    def data_client(self) -> DataClient:
        return self.context.data_client

    @property
    def project_id(self) -> str:
        return self.context.project_id

    @property
    def catalog_digest(self) -> str:
        return self.context.catalog_digest

    @property
    def models(self) -> dict[tuple[str, str], dict]:
        return self.context.models

    def _workflow_snapshot(self, connection, job_id: str) -> WorkflowSnapshot:
        job = connection.execute(
            "SELECT status, stage FROM executor_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        rows = connection.execute(
            "SELECT node_id, status, dependencies_json FROM job_nodes WHERE job_id = ?",
            (job_id,),
        ).fetchall()
        return WorkflowSnapshot(
            status=job["status"],
            stage=job["stage"],
            nodes=tuple(
                NodeSnapshot(
                    node_id=row["node_id"],
                    status=row["status"],
                    dependencies=tuple(json.loads(row["dependencies_json"])),
                )
                for row in rows
            ),
        )

    def _advance(self, connection, job_id: str, event) -> None:
        commands = transition(self._workflow_snapshot(connection, job_id), event)
        for node_id in commands.ready_node_ids:
            connection.execute(
                "UPDATE job_nodes SET status = 'READY' WHERE job_id = ? AND node_id = ? AND status = 'WAITING'",
                (job_id, node_id),
            )

    def _fail_node(self, connection, job_id: str, node_id: str, error: str) -> None:
        now = utc_now()
        commands = transition(
            self._workflow_snapshot(connection, job_id), TaskFailed(node_id)
        )
        connection.execute(
            "UPDATE job_nodes SET status = 'FAILED', error = ?, finished_at = ? WHERE job_id = ? AND node_id = ?",
            (error, now, job_id, node_id),
        )
        for skipped_node_id in commands.skipped_node_ids:
            connection.execute(
                "UPDATE job_nodes SET status = 'SKIPPED', error = 'UPSTREAM_FAILED', finished_at = ? WHERE job_id = ? AND node_id = ? AND status IN ('WAITING','READY')",
                (now, job_id, skipped_node_id),
            )
        connection.execute(
            "UPDATE executor_jobs SET status = 'FAILED', stage = 'DAG', error = ?, finished_at = ? WHERE job_id = ? AND status = 'RUNNING'",
            (error, now, job_id),
        )
        connection.execute(
            "UPDATE node_attempts SET status='CANCELLED', error_code=?, finished_at=? WHERE job_id=? AND status IN ('LEASED','RUNNING')",
            (error, now, job_id),
        )
        connection.execute(
            "UPDATE job_nodes SET status='CANCELLED', error=?, finished_at=? WHERE job_id=? AND status IN ('WAITING','READY','LEASED','RUNNING')",
            (error, now, job_id),
        )
        connection.execute(
            "DELETE FROM job_object_pins WHERE job_id = ? AND purpose NOT LIKE 'prepare:%'",
            (job_id,),
        )
        self.artifacts.prune_unpinned(connection)

    def _visible_status(self, row) -> JobStatusV6:
        return self._status(row)

    @staticmethod
    def _status(row) -> JobStatusV6:
        return JobStatusV6(
            job_id=row["job_id"],
            request_id=row["request_id"],
            status=row["status"],
            stage=row["stage"],
            error=(
                None if row["status"] == "RUNNING" and row["stage"] == "COMMIT_PENDING"
                else row["error"]
            ),
            outputs=tuple(
                DataVersionRefV2.from_storage_dict(item)
                for item in (
                    [] if row["stage"] == "COMMIT_PENDING"
                    else json.loads(row["outputs_json"])
                )
            ),
            service_version="6.1.0",
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )


def _after(seconds: int) -> str:
    return (
        (datetime.now(timezone.utc) + timedelta(seconds=seconds))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
