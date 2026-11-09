from __future__ import annotations

import sqlite3
from typing import TypedDict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock


SCHEMA_VERSION = 9
_SQLITE_BUSY_LOCK = Lock()
_SQLITE_BUSY_TOTAL = 0
SCHEMA = """
CREATE TABLE schema_version(version INTEGER NOT NULL);
CREATE TABLE executor_jobs (
    job_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    request_json TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    model_checksum TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    workflow_version TEXT NOT NULL,
    workflow_checksum TEXT NOT NULL,
    max_parallel_nodes INTEGER NOT NULL CHECK(max_parallel_nodes > 0),
    status TEXT NOT NULL CHECK(status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED','TIMED_OUT')),
    stage TEXT NOT NULL,
    error TEXT,
    outputs_json TEXT NOT NULL,
    commit_digest TEXT,
    publication_id TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1)),
    deadline_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX executor_jobs_status_idx ON executor_jobs(status, created_at);
CREATE TABLE job_nodes (
    job_id TEXT NOT NULL REFERENCES executor_jobs(job_id),
    node_id TEXT NOT NULL,
    operator_id TEXT NOT NULL,
    operator_version TEXT NOT NULL,
    implementation_revision TEXT NOT NULL,
    runtime TEXT NOT NULL CHECK(runtime IN ('native','qgis','remote_http')),
    remote_binding_json TEXT NOT NULL DEFAULT '{}',
    remote_state_json TEXT NOT NULL DEFAULT '{}',
    memory_limit_bytes INTEGER NOT NULL CHECK(memory_limit_bytes > 0),
    timeout_seconds INTEGER NOT NULL CHECK(timeout_seconds > 0),
    status TEXT NOT NULL CHECK(status IN ('WAITING','READY','LEASED','RUNNING','SUCCEEDED','FAILED','CANCELLED','SKIPPED')),
    dependencies_json TEXT NOT NULL,
    input_bindings_json TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    output_contracts_json TEXT NOT NULL,
    outputs_json TEXT NOT NULL,
    values_json TEXT NOT NULL,
    lost_count INTEGER NOT NULL DEFAULT 0 CHECK(lost_count >= 0),
    error TEXT,
    started_at TEXT,
    finished_at TEXT,
    PRIMARY KEY(job_id, node_id)
);
CREATE INDEX job_nodes_ready_idx ON job_nodes(status, runtime, job_id, node_id);
CREATE TABLE node_attempts (
    attempt_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK(attempt_number > 0),
    worker_id TEXT NOT NULL,
    lease_id TEXT NOT NULL UNIQUE,
    lease_token TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('LEASED','RUNNING','SUCCEEDED','FAILED','LOST','CANCELLED')),
    error_code TEXT,
    stdout_tail TEXT NOT NULL DEFAULT '',
    stderr_tail TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE(job_id, node_id, attempt_number),
    FOREIGN KEY(job_id, node_id) REFERENCES job_nodes(job_id, node_id)
);
CREATE INDEX node_attempts_lease_idx ON node_attempts(status, lease_expires_at);
CREATE TABLE worker_heartbeats (
    worker_id TEXT PRIMARY KEY,
    runtime TEXT NOT NULL CHECK(runtime IN ('native','qgis','remote_http')),
    catalog_digest TEXT NOT NULL,
    slots INTEGER NOT NULL CHECK(slots > 0),
    memory_bytes INTEGER NOT NULL CHECK(memory_bytes > 0),
    last_seen_at TEXT NOT NULL
);
CREATE TABLE content_objects (
    checksum TEXT PRIMARY KEY,
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    relative_path TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE job_object_pins (
    job_id TEXT NOT NULL REFERENCES executor_jobs(job_id),
    checksum TEXT NOT NULL REFERENCES content_objects(checksum),
    purpose TEXT NOT NULL,
    PRIMARY KEY(job_id, checksum, purpose)
);
"""


@contextmanager
def connect_database(path: Path) -> Iterator[sqlite3.Connection]:
    global _SQLITE_BUSY_TOTAL
    connection = sqlite3.connect(path, timeout=30)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        with connection:
            yield connection
    except sqlite3.OperationalError as error:
        if "locked" in str(error).lower() or "busy" in str(error).lower():
            with _SQLITE_BUSY_LOCK:
                _SQLITE_BUSY_TOTAL += 1
        raise
    finally:
        connection.close()


def is_sqlite_busy_error(error: BaseException) -> bool:
    """Return whether SQLite rejected work because another writer owns the lock."""
    return isinstance(error, sqlite3.OperationalError) and any(
        marker in str(error).lower() for marker in ("locked", "busy")
    )


def check_database(path: Path) -> None:
    try:
        with connect_database(path) as connection:
            connection.execute("SELECT 1").fetchone()
    except sqlite3.Error as error:
        raise RuntimeError("EXECUTOR_DATABASE_UNAVAILABLE") from error


class RuntimeMetrics(TypedDict):
    jobs: dict[str, int]
    nodes: dict[str, int]
    slots: dict[str, int]
    active: dict[str, int]
    oldest: float
    sqlite_busy: int
    retries: int
    expired_leases: int
    lost_leases: int
    remote_statuses: dict[str, int]
    remote_unconfirmed_terminations: int
    remote_failures: dict[str, int]


def runtime_metrics(path: Path) -> RuntimeMetrics:
    with connect_database(path) as connection:
        jobs = {
            row["status"]: row["count"]
            for row in connection.execute(
                "SELECT status,count(*) AS count FROM executor_jobs GROUP BY status"
            )
        }
        nodes = {
            row["status"]: row["count"]
            for row in connection.execute(
                "SELECT status,count(*) AS count FROM job_nodes GROUP BY status"
            )
        }
        workers = connection.execute(
            "SELECT runtime,sum(slots) AS slots FROM worker_heartbeats GROUP BY runtime"
        ).fetchall()
        active = connection.execute(
            "SELECT runtime,count(*) AS count FROM job_nodes "
            "WHERE status IN ('LEASED','RUNNING') GROUP BY runtime"
        ).fetchall()
        oldest = connection.execute(
            "SELECT created_at FROM executor_jobs WHERE status='QUEUED' "
            "ORDER BY created_at LIMIT 1"
        ).fetchone()
        retries = connection.execute(
            "SELECT count(*) AS count FROM node_attempts WHERE attempt_number>1"
        ).fetchone()["count"]
        expired_leases = connection.execute(
            "SELECT count(*) AS count FROM node_attempts "
            "WHERE status IN ('LEASED','RUNNING') AND lease_expires_at < ?",
            (utc_now(),),
        ).fetchone()["count"]
        lost_leases = connection.execute(
            "SELECT count(*) AS count FROM node_attempts WHERE status='LOST'"
        ).fetchone()["count"]
        remote_statuses = connection.execute(
            """SELECT COALESCE(json_extract(remote_state_json,'$.remote_status'),'not_started') AS state,
                      count(*) AS count
                 FROM job_nodes WHERE runtime='remote_http' GROUP BY state"""
        ).fetchall()
        remote_unconfirmed = connection.execute(
            """SELECT count(*) AS count FROM job_nodes
                WHERE runtime='remote_http' AND status IN ('FAILED','CANCELLED')
                  AND remote_state_json <> '{}'
                  AND COALESCE(
                      json_extract(remote_state_json,'$.remote_termination_confirmed'),0
                  ) <> 1"""
        ).fetchone()["count"]
        remote_failures = connection.execute(
            """SELECT error,count(*) AS count FROM job_nodes
                WHERE runtime='remote_http' AND status='FAILED' AND error IS NOT NULL
                GROUP BY error"""
        ).fetchall()
    age = 0.0
    if oldest is not None:
        age = max(
            0.0,
            (datetime.now(timezone.utc) - datetime.fromisoformat(
                oldest["created_at"].replace("Z", "+00:00")
            )).total_seconds(),
        )
    return {
        "jobs": jobs,
        "nodes": nodes,
        "slots": {row["runtime"]: row["slots"] for row in workers},
        "active": {row["runtime"]: row["count"] for row in active},
        "oldest": age,
        "sqlite_busy": _sqlite_busy_total(),
        "retries": retries,
        "expired_leases": expired_leases,
        "lost_leases": lost_leases,
        "remote_statuses": {
            row["state"]: row["count"] for row in remote_statuses
        },
        "remote_unconfirmed_terminations": remote_unconfirmed,
        "remote_failures": {
            row["error"]: row["count"] for row in remote_failures
        },
    }


def _sqlite_busy_total() -> int:
    with _SQLITE_BUSY_LOCK:
        return _SQLITE_BUSY_TOTAL


def initialize_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect_database(path) as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if not tables:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
            connection.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
            return
        if "schema_version" not in tables:
            raise RuntimeError("SCHEMA_VERSION_UNSUPPORTED")
        rows = connection.execute("SELECT version FROM schema_version").fetchall()
        if len(rows) == 1 and rows[0]["version"] == 8:
            _migrate_v8_to_v9(connection)
            rows = connection.execute("SELECT version FROM schema_version").fetchall()
        if len(rows) != 1 or rows[0]["version"] != SCHEMA_VERSION:
            raise RuntimeError("SCHEMA_VERSION_UNSUPPORTED")
        if _schema_signature(connection) != _expected_schema_signature():
            raise RuntimeError("SCHEMA_VERSION_UNSUPPORTED")
        connection.execute("PRAGMA journal_mode = WAL")


def _migrate_v8_to_v9(connection: sqlite3.Connection) -> None:
    """Perform the only supported stopped upgrade, preserving queued evidence."""
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "CREATE TABLE job_nodes_v8_backup AS SELECT * FROM job_nodes"
        )
        connection.execute("DROP INDEX job_nodes_ready_idx")
        connection.execute("DROP TABLE job_nodes")
        connection.execute(_expected_schema_sql("table", "job_nodes"))
        connection.execute(
            """INSERT INTO job_nodes(
                   job_id,node_id,operator_id,operator_version,implementation_revision,
                   runtime,memory_limit_bytes,timeout_seconds,status,dependencies_json,
                   input_bindings_json,parameters_json,output_contracts_json,outputs_json,
                   values_json,lost_count,error,started_at,finished_at
               ) SELECT
                   job_id,node_id,operator_id,operator_version,implementation_revision,
                   runtime,memory_limit_bytes,timeout_seconds,status,dependencies_json,
                   input_bindings_json,parameters_json,output_contracts_json,outputs_json,
                   values_json,lost_count,error,started_at,finished_at
                 FROM job_nodes_v8_backup"""
        )
        connection.execute("DROP TABLE job_nodes_v8_backup")
        connection.execute(_expected_schema_sql("index", "job_nodes_ready_idx"))

        connection.execute(
            "CREATE TABLE worker_heartbeats_v8_backup AS SELECT * FROM worker_heartbeats"
        )
        connection.execute("DROP TABLE worker_heartbeats")
        connection.execute(_expected_schema_sql("table", "worker_heartbeats"))
        connection.execute(
            "INSERT INTO worker_heartbeats SELECT * FROM worker_heartbeats_v8_backup"
        )
        connection.execute("DROP TABLE worker_heartbeats_v8_backup")
        connection.execute("UPDATE schema_version SET version=9")
        connection.execute("COMMIT")
    except sqlite3.Error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


def _schema_signature(connection: sqlite3.Connection) -> tuple[tuple[object, ...], ...]:
    return tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*' ORDER BY type, name"
        )
    )


def _expected_schema_signature() -> tuple[tuple[object, ...], ...]:
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(SCHEMA)
        return _schema_signature(connection)


def _expected_schema_sql(kind: str, name: str) -> str:
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(SCHEMA)
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type=? AND name=?",
            (kind, name),
        ).fetchone()
    if row is None or not isinstance(row[0], str):
        raise RuntimeError("SCHEMA_VERSION_UNSUPPORTED")
    return row[0]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
