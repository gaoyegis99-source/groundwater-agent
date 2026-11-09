from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from platform_kernel.database import (
    V29_SCHEMA_DIGEST,
    V30_SCHEMA_DIGEST,
    V31_SCHEMA_DIGEST,
    V32_SCHEMA_DIGEST,
    V33_SCHEMA_DIGEST,
    initialize_database,
    migrate_database,
)
from platform_kernel.agent_runtime.persistence.provider_requests import (
    finish_provider_request,
    provider_unknown_metrics,
    recover_provider_requests,
)


KERNEL_V34_SCHEMA_DIGEST = (
    "037ac6df53d0968713823e5c5519bd03905e448f5ee3dadef866a5d277838867"
)


def test_kernel_v34_sqlite_schema_is_frozen(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT GLOB 'sqlite_*' ORDER BY type,name"
        ).fetchall()
        schema_version = connection.execute(
            "SELECT version FROM schema_version"
        ).fetchone()[0]
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()

    encoded = json.dumps(
        rows, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    assert hashlib.sha256(encoded).hexdigest() == KERNEL_V34_SCHEMA_DIGEST
    assert schema_version == 34
    # Kernel versions its store with schema_version; changing the unused SQLite
    # pragma would still be a storage-contract change.
    assert user_version == 0
    assert foreign_key_errors == []


def _drop_v34_identity_schema(connection: sqlite3.Connection) -> None:
    connection.execute("DROP TABLE research_api_tokens")
    connection.execute("DROP TABLE research_sessions")
    connection.execute("DROP TABLE research_project_memberships")
    connection.execute("DROP TABLE research_users")


def test_explicit_migrate_database_upgrades_exact_v33_identity_layout(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        _drop_v34_identity_schema(connection)
        connection.execute("UPDATE schema_version SET version=33")
        rows = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT GLOB 'sqlite_*' ORDER BY type,name"
        ).fetchall()
    assert hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest() == V33_SCHEMA_DIGEST

    before = database.read_bytes()
    with pytest.raises(ValueError, match="SCHEMA_VERSION_UNSUPPORTED"):
        initialize_database(database)
    assert database.read_bytes() == before
    assert not list(tmp_path.glob("kernel.sqlite3.v33-preupgrade-*.sqlite3"))

    migrate_database(database)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 34
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='research_users'"
        ).fetchone() == ("research_users",)
    assert len(list(tmp_path.glob("kernel.sqlite3.v33-preupgrade-*.sqlite3"))) == 1


def test_explicit_migrate_database_upgrades_only_exact_v32_layout(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        _drop_v34_identity_schema(connection)
        connection.execute("DROP TABLE workflow_service_bindings")
        connection.execute("UPDATE schema_version SET version=32")
        rows = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT GLOB 'sqlite_*' ORDER BY type,name"
        ).fetchall()
    assert hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest() == V32_SCHEMA_DIGEST

    migrate_database(database)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 34
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='workflow_service_bindings'"
        ).fetchone() == ("workflow_service_bindings",)
    assert len(list(tmp_path.glob("kernel.sqlite3.v32-preupgrade-*.sqlite3"))) == 1


def test_explicit_migrate_database_refuses_tampered_v32_before_backup(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        _drop_v34_identity_schema(connection)
        connection.execute("DROP TABLE workflow_service_bindings")
        connection.execute("UPDATE schema_version SET version=32")
        connection.execute("CREATE TABLE unrecognized_v32_change(value TEXT)")
    before = database.read_bytes()

    with pytest.raises(ValueError, match="SCHEMA_VERSION_UNSUPPORTED"):
        migrate_database(database)

    assert database.read_bytes() == before
    assert not list(tmp_path.glob("kernel.sqlite3.v32-preupgrade-*.sqlite3"))


V31_PROVIDER_REQUESTS_SCHEMA = """CREATE TABLE provider_requests (
    provider_request_id TEXT PRIMARY KEY,
    agent_run_id TEXT,
    stage TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    protocol TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK(attempt IN (1,2)),
    outcome TEXT NOT NULL CHECK(outcome IN ('STARTED','SUCCEEDED','FAILED')),
    error_class TEXT,
    input_tokens INTEGER NOT NULL CHECK(input_tokens >= 0),
    output_tokens INTEGER NOT NULL CHECK(output_tokens >= 0),
    total_tokens INTEGER NOT NULL CHECK(total_tokens >= 0),
    latency_ms INTEGER NOT NULL CHECK(latency_ms >= 0),
    created_at TEXT NOT NULL
);"""


def _make_exact_v31_provider_table(connection) -> None:
    connection.execute("ALTER TABLE provider_requests RENAME TO provider_requests_v32")
    connection.execute(V31_PROVIDER_REQUESTS_SCHEMA)
    connection.execute("INSERT INTO provider_requests SELECT * FROM provider_requests_v32")
    connection.execute("DROP TABLE provider_requests_v32")


def _make_exact_v29_store(database) -> None:
    """Downgrade a freshly-created fixture into the frozen v29 shape.

    This intentionally uses the historical DDL verbatim.  It verifies that the
    production upgrade accepts only the exact old signature rather than merely
    trusting a value in ``schema_version``.
    """

    with sqlite3.connect(database) as connection:
        _drop_v34_identity_schema(connection)
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA legacy_alter_table=ON")
        connection.execute("DROP TABLE workflow_service_bindings")
        connection.execute("DROP TRIGGER sessions_owner_subject_immutable")
        connection.execute("DROP INDEX sessions_owner_archive_idx")
        connection.execute("DROP INDEX sessions_agent_start_idx")
        connection.execute("ALTER TABLE sessions RENAME TO sessions_v31")
        connection.execute(
            """CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    goal_revision INTEGER NOT NULL,
    agent_run_id TEXT,
    pending_agent_run_id TEXT,
    agent_start_state TEXT,
    owner_subject TEXT NOT NULL DEFAULT 'local-pilot-user',
    title TEXT,
    archived_at TEXT
)"""
        )
        connection.execute(
            """CREATE TRIGGER sessions_owner_subject_immutable
BEFORE UPDATE ON sessions
WHEN NEW.owner_subject <> OLD.owner_subject
BEGIN
    SELECT RAISE(ABORT, 'SESSION_OWNER_IMMUTABLE');
END"""
        )
        connection.execute("INSERT INTO sessions SELECT * FROM sessions_v31")
        connection.execute("DROP TABLE sessions_v31")
        connection.execute(
            "CREATE INDEX sessions_owner_archive_idx "
            "ON sessions(project_id, owner_subject, archived_at)"
        )
        connection.execute(
            "CREATE INDEX sessions_agent_start_idx ON sessions(agent_start_state) "
            "WHERE pending_agent_run_id IS NOT NULL"
        )
        connection.execute("DROP INDEX run_controls_claim_idx")
        connection.execute("ALTER TABLE run_controls RENAME TO run_controls_v30")
        connection.execute(
            """CREATE TABLE run_controls (
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    request_id TEXT NOT NULL,
    agent_run_id TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(session_id, request_id)
)"""
        )
        connection.execute(
            """INSERT INTO sessions(
                session_id,project_id,goal_revision,owner_subject
            ) VALUES ('session','project',0,'local-pilot-user')"""
        )
        connection.execute(
            """INSERT INTO run_controls(
                session_id,request_id,agent_run_id,requested_by,status,created_at
            ) VALUES ('session','request','run','operator','REQUESTED','2026-10-24T00:00:00Z')"""
        )
        connection.execute("DROP TABLE run_controls_v30")
        _make_exact_v31_provider_table(connection)
        connection.execute("UPDATE schema_version SET version=29")


def test_explicit_migrate_database_upgrades_only_the_exact_v29_contract(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    _make_exact_v29_store(database)

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT GLOB 'sqlite_*' ORDER BY type,name"
        ).fetchall()
    assert hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest() == V29_SCHEMA_DIGEST

    migrate_database(database)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 34
        row = connection.execute(
            "SELECT status,claim_owner,claim_generation,claim_until FROM run_controls"
        ).fetchone()
        assert row == ("REQUESTED", None, 0, None)
    backups = list(tmp_path.glob("kernel.sqlite3.v29-preupgrade-*.sqlite3"))
    assert len(backups) == 1
    assert backups[0].read_bytes()
    v30_backups = list(tmp_path.glob("kernel.sqlite3.v30-preupgrade-*.sqlite3"))
    assert len(v30_backups) == 1
    assert v30_backups[0].read_bytes()
    assert len(list(tmp_path.glob("kernel.sqlite3.v31-preupgrade-*.sqlite3"))) == 1
    assert len(list(tmp_path.glob("kernel.sqlite3.v32-preupgrade-*.sqlite3"))) == 1


def _make_exact_v30_store(database) -> None:
    """Downgrade a fixture to the frozen v30 shape without changing its data."""

    with sqlite3.connect(database) as connection:
        _drop_v34_identity_schema(connection)
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA legacy_alter_table=ON")
        connection.execute("DROP TABLE workflow_service_bindings")
        connection.execute("DROP TRIGGER sessions_owner_subject_immutable")
        connection.execute("DROP INDEX sessions_owner_archive_idx")
        connection.execute("DROP INDEX sessions_agent_start_idx")
        connection.execute("ALTER TABLE sessions RENAME TO sessions_v31")
        connection.execute(
            """CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    goal_revision INTEGER NOT NULL,
    agent_run_id TEXT,
    pending_agent_run_id TEXT,
    agent_start_state TEXT,
    owner_subject TEXT NOT NULL DEFAULT 'local-pilot-user',
    title TEXT,
    archived_at TEXT
)"""
        )
        connection.execute(
            """CREATE TRIGGER sessions_owner_subject_immutable
BEFORE UPDATE ON sessions
WHEN NEW.owner_subject <> OLD.owner_subject
BEGIN
    SELECT RAISE(ABORT, 'SESSION_OWNER_IMMUTABLE');
END"""
        )
        connection.execute("INSERT INTO sessions SELECT * FROM sessions_v31")
        connection.execute("DROP TABLE sessions_v31")
        connection.execute(
            "CREATE INDEX sessions_owner_archive_idx "
            "ON sessions(project_id, owner_subject, archived_at)"
        )
        connection.execute(
            "CREATE INDEX sessions_agent_start_idx ON sessions(agent_start_state) "
            "WHERE pending_agent_run_id IS NOT NULL"
        )
        _make_exact_v31_provider_table(connection)
        connection.execute("UPDATE schema_version SET version=30")
        rows = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT GLOB 'sqlite_*' ORDER BY type,name"
        ).fetchall()
    assert hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest() == V30_SCHEMA_DIGEST


def test_explicit_migrate_database_upgrades_exact_v30_without_rewriting_actor_history(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO sessions(session_id,project_id,goal_revision,owner_subject) "
            "VALUES ('session','project',0,'historical-subject')"
        )
    _make_exact_v30_store(database)

    migrate_database(database)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 34
        assert connection.execute(
            "SELECT owner_subject FROM sessions WHERE session_id='session'"
        ).fetchone()[0] == "historical-subject"
        session_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='sessions'"
        ).fetchone()[0]
    assert "DEFAULT 'local-pilot-user'" not in session_sql
    assert len(list(tmp_path.glob("kernel.sqlite3.v30-preupgrade-*.sqlite3"))) == 1


def test_v31_provider_attempt_upgrades_then_recovers_as_unknown(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        _drop_v34_identity_schema(connection)
        connection.execute("DROP TABLE workflow_service_bindings")
        _make_exact_v31_provider_table(connection)
        connection.execute("UPDATE schema_version SET version=31")
        connection.execute(
            """INSERT INTO provider_requests(
                   provider_request_id,stage,provider,model,protocol,attempt,
                   outcome,input_tokens,output_tokens,total_tokens,latency_ms,created_at
               ) VALUES('attempt','AGENT','provider','model','responses',1,
                        'STARTED',0,0,0,0,'2026-10-24T00:00:00Z')"""
        )
        rows = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT GLOB 'sqlite_*' ORDER BY type,name"
        ).fetchall()
    assert hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest() == V31_SCHEMA_DIGEST

    migrate_database(database)
    assert recover_provider_requests(database) == 1
    assert recover_provider_requests(database) == 0
    finish_provider_request(
        database, "attempt", outcome="SUCCEEDED", error_class=None,
        input_tokens=1, output_tokens=1, total_tokens=2, latency_ms=1,
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 34
        assert connection.execute(
            "SELECT outcome,error_class,total_tokens FROM provider_requests"
        ).fetchone() == ("UNKNOWN", "PROCESS_INTERRUPTED", 0)
    metrics = provider_unknown_metrics(database)
    assert metrics["count"] == 1
    assert metrics["oldest_age_seconds"] > 0
    assert len(list(tmp_path.glob("kernel.sqlite3.v31-preupgrade-*.sqlite3"))) == 1


def test_explicit_migrate_database_refuses_tampered_v31_before_backup(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        _drop_v34_identity_schema(connection)
        connection.execute("DROP TABLE workflow_service_bindings")
        _make_exact_v31_provider_table(connection)
        connection.execute("UPDATE schema_version SET version=31")
        connection.execute("CREATE TABLE unrecognized_v31_change(value TEXT)")
    before = database.read_bytes()
    with pytest.raises(ValueError, match="SCHEMA_VERSION_UNSUPPORTED"):
        migrate_database(database)
    assert database.read_bytes() == before
    assert not list(tmp_path.glob("kernel.sqlite3.v31-preupgrade-*.sqlite3"))


def test_explicit_migrate_database_refuses_a_tampered_v29_store_without_rewriting_it(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    _make_exact_v29_store(database)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unrecognized_v29_change(value TEXT)")
    before = database.read_bytes()

    with pytest.raises(ValueError, match="SCHEMA_VERSION_UNSUPPORTED"):
        migrate_database(database)

    assert database.read_bytes() == before
    assert not list(tmp_path.glob("kernel.sqlite3.v29-preupgrade-*.sqlite3"))
