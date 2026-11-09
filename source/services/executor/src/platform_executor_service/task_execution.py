from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .contracts import canonical_json
from .errors import ExecutionServiceError


LOG_TAIL_BYTES = 64 * 1024


@dataclass(frozen=True)
class WorkerCapacity:
    worker_id: str
    runtime: str
    slots: int
    memory_bytes: int

    @classmethod
    def from_body(cls, body: dict) -> WorkerCapacity:
        expected = {
            "worker_id",
            "runtime",
            "catalog_digest",
            "slots",
            "memory_bytes",
        }
        if set(body) != expected or body.get("runtime") not in {
            "native", "qgis", "remote_http"
        }:
            raise ExecutionServiceError("WORKER_HEARTBEAT_INVALID")
        worker_id, slots, memory_bytes = (
            body["worker_id"],
            body["slots"],
            body["memory_bytes"],
        )
        if (
            not isinstance(worker_id, str)
            or not worker_id
            or isinstance(slots, bool)
            or not isinstance(slots, int)
            or slots <= 0
            or isinstance(memory_bytes, bool)
            or not isinstance(memory_bytes, int)
            or memory_bytes <= 0
        ):
            raise ExecutionServiceError("WORKER_HEARTBEAT_INVALID")
        return cls(worker_id, body["runtime"], slots, memory_bytes)


@dataclass(frozen=True)
class ReadyTaskSpec:
    job_id: str
    node_id: str
    operator_id: str
    operator_version: str
    implementation_revision: str
    runtime: str
    memory_limit_bytes: int
    timeout_seconds: int
    parameters: dict
    output_contracts: list[dict]
    traceparent: str
    remote_binding: dict
    remote_state: dict

    @classmethod
    def from_row(cls, row) -> ReadyTaskSpec:
        return cls(
            job_id=row["job_id"],
            node_id=row["node_id"],
            operator_id=row["operator_id"],
            operator_version=row["operator_version"],
            implementation_revision=row["implementation_revision"],
            runtime=row["runtime"],
            memory_limit_bytes=row["memory_limit_bytes"],
            timeout_seconds=row["timeout_seconds"],
            parameters=json.loads(row["parameters_json"]),
            output_contracts=json.loads(row["output_contracts_json"]),
            traceparent=row["traceparent"],
            remote_binding=json.loads(row["remote_binding_json"]),
            remote_state=json.loads(row["remote_state_json"]),
        )


@dataclass(frozen=True)
class TaskLease:
    attempt_id: str
    lease_id: str
    lease_token: str
    lease_expires_at: str
    attempt_number: int
    worker_id: str
    task: ReadyTaskSpec
    workspace: Path
    inputs: dict

    def worker_payload(self) -> dict:
        return {
            "lease_id": self.lease_id,
            "lease_token": self.lease_token,
            "lease_expires_at": self.lease_expires_at,
            "job_id": self.task.job_id,
            "node_id": self.task.node_id,
            "attempt": self.attempt_number,
            "operator_id": self.task.operator_id,
            "operator_version": self.task.operator_version,
            "implementation_revision": self.task.implementation_revision,
            "runtime": self.task.runtime,
            "timeout_seconds": self.task.timeout_seconds,
            "memory_limit_bytes": self.task.memory_limit_bytes,
            "parameters": self.task.parameters,
            "inputs": self.inputs,
            "workspace": str(self.workspace),
            "output_contracts": self.task.output_contracts,
            "traceparent": self.task.traceparent,
            "remote_binding": self.task.remote_binding,
            "remote_state": self.task.remote_state,
        }


@dataclass(frozen=True)
class TaskOutcome:
    status: str
    values_json: str | None = None
    error_code: str | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""

    @classmethod
    def succeeded(cls, body: dict, output_contracts: list[dict]) -> TaskOutcome:
        values = body.get("values")
        value_ports = {
            item["name"] for item in output_contracts if item["kind"] == "value"
        }
        if not isinstance(values, dict) or set(values) != value_ports:
            raise ExecutionServiceError("NODE_OUTPUT_INVALID")
        try:
            values_json = canonical_json(values).decode("utf-8")
        except (TypeError, ValueError) as cause:
            raise ExecutionServiceError("NODE_OUTPUT_INVALID") from cause
        return cls(
            status="SUCCEEDED",
            values_json=values_json,
            stdout_tail=_tail(body.get("stdout", "")),
            stderr_tail=_tail(body.get("stderr", "")),
        )

    @classmethod
    def failed(cls, body: dict) -> TaskOutcome:
        error_code = body.get("error_code")
        if not isinstance(error_code, str) or not error_code:
            raise ExecutionServiceError("WORKER_FAILURE_INVALID")
        return cls(
            status="FAILED",
            error_code=error_code,
            stdout_tail=_tail(body.get("stdout", "")),
            stderr_tail=_tail(body.get("stderr", "")),
        )


def select_ready_tasks(connection, worker: WorkerCapacity, now: str):
    active = connection.execute(
        """SELECT COUNT(*) AS count, COALESCE(SUM(n.memory_limit_bytes), 0) AS memory
           FROM node_attempts a JOIN job_nodes n ON n.job_id=a.job_id AND n.node_id=a.node_id
           WHERE a.worker_id=? AND a.status IN ('LEASED','RUNNING')""",
        (worker.worker_id,),
    ).fetchone()
    if active["count"] >= worker.slots:
        return []
    return connection.execute(
        """SELECT n.*,COALESCE(
             json_extract(j.request_json,'$.traceparent'),
             '00-11111111111111111111111111111111-2222222222222222-01'
           ) AS traceparent
           FROM job_nodes n JOIN executor_jobs j ON j.job_id=n.job_id
           WHERE n.status='READY' AND n.runtime=? AND n.memory_limit_bytes<=?
             AND j.status='RUNNING' AND j.cancel_requested=0
             AND julianday(j.deadline_at)>julianday(?)
             AND (SELECT COUNT(*) FROM job_nodes active_node
                  WHERE active_node.job_id=n.job_id
                  AND active_node.status IN ('LEASED','RUNNING')) < j.max_parallel_nodes
           ORDER BY
             CASE WHEN EXISTS (
                 SELECT 1 FROM node_attempts prior
                 WHERE prior.job_id=n.job_id
             ) THEN 1 ELSE 0 END,
             (SELECT COUNT(*) FROM job_nodes active_node
              WHERE active_node.job_id=n.job_id
              AND active_node.status IN ('LEASED','RUNNING')),
             COALESCE((SELECT MAX(prior.started_at) FROM node_attempts prior
                       WHERE prior.job_id=n.job_id), ''),
             j.created_at,
             n.node_id""",
        (worker.runtime, worker.memory_bytes - active["memory"], now),
    ).fetchall()


def require_lease(connection, lease_id: str, body: dict, now: str):
    token = body.get("lease_token")
    row = connection.execute(
        """SELECT a.* FROM node_attempts a
           JOIN executor_jobs j ON j.job_id = a.job_id
           JOIN job_nodes n ON n.job_id = a.job_id AND n.node_id = a.node_id
           WHERE a.lease_id = ? AND j.status = 'RUNNING'
             AND j.cancel_requested=0 AND julianday(j.deadline_at)>julianday(?)
             AND n.status IN ('LEASED','RUNNING')""",
        (lease_id, now),
    ).fetchone()
    if (
        row is None
        or row["lease_token"] != token
        or row["status"] not in {"LEASED", "RUNNING"}
        or row["lease_expires_at"] < now
    ):
        raise ExecutionServiceError("LEASE_STALE")
    return row


def _tail(value: object) -> str:
    if not isinstance(value, str):
        raise ExecutionServiceError("WORKER_LOG_INVALID")
    encoded = value.encode("utf-8")[-LOG_TAIL_BYTES:]
    return encoded.decode("utf-8", errors="ignore")
