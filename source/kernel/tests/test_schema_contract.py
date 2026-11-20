from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from platform_kernel import database as kernel_database
from platform_kernel import schema as kernel_schema
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


KERNEL_V36_SCHEMA_DIGEST = (
    "db480e8d1df5b1c7b5b72bb62db24d6fdb4c79f7cfc50de659085f5ca943b4df"
)
KERNEL_V37_SCHEMA_DIGEST = (
    "d647e55c4092e2a7650fa6f5c96705f8adef18e22427c5bed372c2489c897dee"
)
KERNEL_V38_SCHEMA_DIGEST = (
    "558f8fe888941ed6e5c82d49663fbd33204e8010f6c0aefef3f6f5043684b5e3"
)


def _create_v36_with_history(database: Path) -> None:
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        _drop_v37_work_items(connection)
        connection.execute("UPDATE schema_version SET version=36")
        connection.execute(
            "INSERT INTO sessions(session_id,project_id,goal_revision,owner_subject,archived_at) "
            "VALUES ('historical-session','project',2,'historical-subject','archived')"
        )
        connection.execute(
            "INSERT INTO messages(session_id,goal_revision,role,content) "
            "VALUES ('historical-session',2,'user','historical message')"
        )
        assert _schema_digest(connection) == KERNEL_V36_SCHEMA_DIGEST


def _schema_digest(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT GLOB 'sqlite_*' ORDER BY type,name"
    ).fetchall()
    return hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def test_work_item_activity_tracks_commands_and_scoped_session_events(tmp_path: Path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        _insert_two_items(connection)
        connection.executemany(
            "INSERT INTO sessions(session_id,project_id,goal_revision,owner_subject) "
            "VALUES (?, 'project', 0, ?)",
            [("first-session", "alice"), ("second-session", "bob")],
        )
        connection.execute(
            "INSERT INTO work_item_sessions VALUES ('first','first-session','now')"
        )
        connection.executemany(
            "INSERT INTO session_events(session_id,event_type,payload_json,created_at) "
            "VALUES (?, ?, '{\"private\":\"body\"}', 'now')",
            [
                ("first-session", "agent.activity"),
                ("first-session", "agent.plan_recorded"),
                ("second-session", "task.created"),
                ("first-session", "task.created"),
            ],
        )
        connection.execute("UPDATE sessions SET project_id='moved' WHERE session_id='first-session'")
        connection.execute(
            "INSERT INTO session_events(session_id,event_type,payload_json,created_at) "
            "VALUES ('first-session','task.revised','{}','now')"
        )
        rows = connection.execute(
            "SELECT work_item_id,sequence,kind,command_sequence,session_id,event_type "
            "FROM work_item_activity ORDER BY work_item_id,sequence"
        ).fetchall()
        assert rows == [
            ("first", 1, "COMMAND", 1, None, None),
            ("first", 2, "SESSION", None, "first-session", "task.created"),
            ("second", 1, "COMMAND", 1, None, None),
        ]


def _insert_two_items(connection: sqlite3.Connection) -> None:
    connection.executemany(
        "INSERT INTO work_items(work_item_id,project_id,owner_subject,title,revision,created_at,updated_at) "
        "VALUES (?, 'project', ?, 'Work', 1, 'now', 'now')",
        [("first", "alice"), ("second", "bob")],
    )
    connection.executemany(
        "INSERT INTO work_item_commands(sequence,project_id,owner_subject,request_id,"
        "work_item_id,command,fingerprint,revision,changed,created_at) "
        "VALUES (1, 'project', ?, 'request', ?, 'CREATE', 'digest', 1, 1, 'now')",
        [("alice", "first"), ("bob", "second")],
    )


def _create_v37_with_commands(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.executescript(kernel_schema.V37_SCHEMA)
        connection.execute("INSERT INTO schema_version VALUES (37)")
        _insert_two_items(connection)
        assert _schema_digest(connection) == KERNEL_V37_SCHEMA_DIGEST


def test_explicit_v37_migration_keeps_old_commands_and_seeds_activity(tmp_path: Path) -> None:
    database = tmp_path / "kernel.sqlite3"
    _create_v37_with_commands(database)
    with sqlite3.connect(database) as connection:
        old_commands = connection.execute("SELECT * FROM work_item_commands ORDER BY work_item_id").fetchall()

    with pytest.raises(ValueError, match="SCHEMA_VERSION_UNSUPPORTED"):
        initialize_database(database)
    migrate_database(database)
    initialize_database(database)
    migrate_database(database)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone() == (38,)
        assert connection.execute("SELECT * FROM work_item_commands ORDER BY work_item_id").fetchall() == [
            row + (None, None) for row in old_commands
        ]
        assert connection.execute(
            "SELECT work_item_id,sequence,kind,command_sequence FROM work_item_activity ORDER BY work_item_id"
        ).fetchall() == [("first", 1, "COMMAND", 1), ("second", 1, "COMMAND", 1)]
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()

    (backup,) = tmp_path.glob("kernel.sqlite3.v37-preupgrade-*.sqlite3")
    assert backup.name.endswith(hashlib.sha256(backup.read_bytes()).hexdigest()[:16] + ".sqlite3")
    with sqlite3.connect(backup) as connection:
        assert _schema_digest(connection) == KERNEL_V37_SCHEMA_DIGEST
        assert connection.execute("SELECT * FROM work_item_commands ORDER BY work_item_id").fetchall() == old_commands


def test_explicit_v37_migration_rolls_back_and_can_be_repeated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "kernel.sqlite3"
    _create_v37_with_commands(database)
    with monkeypatch.context() as patch:
        patch.setattr(kernel_database, "_expected_schema_signature", lambda: ())
        with pytest.raises(ValueError, match="SCHEMA_VERSION_UNSUPPORTED"):
            migrate_database(database)

    with sqlite3.connect(database) as connection:
        assert _schema_digest(connection) == KERNEL_V37_SCHEMA_DIGEST
        assert connection.execute("SELECT version FROM schema_version").fetchone() == (37,)
        assert connection.execute("SELECT COUNT(*) FROM work_item_commands").fetchone() == (2,)

    migrate_database(database)
    initialize_database(database)
    assert len(list(tmp_path.glob("kernel.sqlite3.v37-preupgrade-*.sqlite3"))) == 1


def test_explicit_v37_migration_rejects_tampering_before_backup(tmp_path: Path) -> None:
    database = tmp_path / "kernel.sqlite3"
    _create_v37_with_commands(database)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unrecognized_v37_change(value TEXT)")
    before = database.read_bytes()

    with pytest.raises(ValueError, match="SCHEMA_VERSION_UNSUPPORTED"):
        migrate_database(database)

    assert database.read_bytes() == before
    assert not list(tmp_path.glob("kernel.sqlite3.v37-preupgrade-*.sqlite3"))


def test_work_item_review_requires_its_scope_and_token(tmp_path: Path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        _insert_two_items(connection)
        connection.execute(
            "INSERT INTO sessions(session_id,project_id,goal_revision,owner_subject) "
            "VALUES ('first-session','project',0,'alice')"
        )
        command = (
            "INSERT INTO work_item_commands(sequence,project_id,owner_subject,request_id,"
            "work_item_id,command,fingerprint,revision,changed,created_at,review_session_id,review_token) "
            "VALUES (2, 'project', 'alice', 'next', 'first', ?, 'digest', 2, 1, 'now', ?, ?)"
        )
        for row in [("UPDATE", "first-session", None), ("REVIEW", None, "token"),
                    ("REVIEW", "first-session", None)]:
            with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
                connection.execute(command, row)
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute(command, ("REVIEW", "missing-session", "token"))
        connection.execute(command, ("REVIEW", "first-session", "token"))
        assert connection.execute(
            "SELECT kind,command_sequence FROM work_item_activity WHERE work_item_id='first' AND sequence=2"
        ).fetchone() == ("COMMAND", 2)


def test_explicit_v36_migration_preserves_session_and_creates_verified_backup(
    tmp_path: Path,
) -> None:
    database = tmp_path / "kernel.sqlite3"
    _create_v36_with_history(database)
    with pytest.raises(ValueError, match="SCHEMA_VERSION_UNSUPPORTED"):
        initialize_database(database)

    migrate_database(database)
    initialize_database(database)
    migrate_database(database)

    backups = list(tmp_path.glob("kernel.sqlite3.v36-preupgrade-*.sqlite3"))
    assert len(backups) == 1
    backup_digest = hashlib.sha256(backups[0].read_bytes()).hexdigest()
    assert backups[0].name == f"kernel.sqlite3.v36-preupgrade-{backup_digest[:16]}.sqlite3"
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone() == (36,)
        assert _schema_digest(connection) == KERNEL_V36_SCHEMA_DIGEST
        original_session = connection.execute("SELECT * FROM sessions").fetchone()
        original_message = connection.execute("SELECT * FROM messages").fetchone()
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone() == (38,)
        assert connection.execute("SELECT * FROM sessions").fetchone() == original_session
        assert connection.execute("SELECT * FROM messages").fetchone() == original_message
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT * FROM work_items").fetchall() == []


def test_explicit_v36_migration_rejects_tampering_before_backup(tmp_path: Path) -> None:
    database = tmp_path / "kernel.sqlite3"
    _create_v36_with_history(database)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unrecognized_v36_change(value TEXT)")
    before = database.read_bytes()

    with pytest.raises(ValueError, match="SCHEMA_VERSION_UNSUPPORTED"):
        migrate_database(database)

    assert database.read_bytes() == before
    assert not list(tmp_path.glob("kernel.sqlite3.v36-preupgrade-*.sqlite3"))


def test_explicit_v36_migration_rolls_back_on_bad_final_signature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "kernel.sqlite3"
    _create_v36_with_history(database)
    with monkeypatch.context() as patch:
        patch.setattr(
            kernel_database,
            "WORK_ITEM_V37_STATEMENTS",
            (*kernel_database.WORK_ITEM_V37_STATEMENTS, "CREATE TABLE extra_v37(value TEXT)"),
        )
        with pytest.raises(ValueError, match="SCHEMA_VERSION_UNSUPPORTED"):
            migrate_database(database)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone() == (36,)
        assert _schema_digest(connection) == KERNEL_V36_SCHEMA_DIGEST
    assert len(list(tmp_path.glob("kernel.sqlite3.v36-preupgrade-*.sqlite3"))) == 1
    migrate_database(database)
    initialize_database(database)
    assert len(list(tmp_path.glob("kernel.sqlite3.v36-preupgrade-*.sqlite3"))) == 1


def test_work_item_scope_and_session_membership_are_enforced_by_schema(
    tmp_path: Path,
) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executemany(
            "INSERT INTO work_items(work_item_id,project_id,owner_subject,title,"
            "revision,created_at,updated_at) "
            "VALUES (?, 'project', 'owner', 'Work', 1, 'now', 'now')",
            [("work",), ("other-work",)],
        )
        connection.executemany(
            "INSERT INTO sessions(session_id,project_id,goal_revision,owner_subject) VALUES (?, ?, 0, ?)",
            [
                ("own", "project", "owner"),
                ("other-owner", "project", "another"),
                ("other-project", "different", "owner"),
            ],
        )
        link_session = (
            "INSERT INTO work_item_sessions(work_item_id,session_id,linked_at) "
            "VALUES (?, ?, 'now')"
        )
        connection.execute(link_session, ("work", "own"))
        for scope_violation in ("other-owner", "other-project"):
            with pytest.raises(sqlite3.IntegrityError, match="WORK_ITEM_SESSION_SCOPE_MISMATCH"):
                connection.execute(link_session, ("work", scope_violation))
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            connection.execute(link_session, ("other-work", "own"))
        with pytest.raises(sqlite3.IntegrityError, match="WORK_ITEM_SCOPE_IMMUTABLE"):
            connection.execute(
                "UPDATE work_items SET owner_subject='another' WHERE work_item_id='work'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute(
                "INSERT INTO work_item_commands(sequence,project_id,owner_subject,request_id,"
                "work_item_id,command,fingerprint,revision,changed,created_at) "
                "VALUES (1, 'project', 'another', 'request', 'work', 'ARCHIVE', 'digest', 1, 1, 'now')"
            )


def test_work_item_audit_sequence_is_positive_and_local_to_each_item(
    tmp_path: Path,
) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executemany(
            "INSERT INTO work_items(work_item_id,project_id,owner_subject,title,"
            "revision,created_at,updated_at) VALUES (?, 'project', ?, 'Work', 1, 'now', 'now')",
            [("first", "alice"), ("second", "bob")],
        )
        add_command = (
            "INSERT INTO work_item_commands(sequence,project_id,owner_subject,request_id,"
            "work_item_id,command,fingerprint,revision,changed,created_at) "
            "VALUES (?, 'project', ?, ?, ?, 'CREATE', 'digest', 1, 1, 'now')"
        )
        connection.execute(add_command, (1, "alice", "request", "first"))
        connection.execute(add_command, (1, "bob", "request", "second"))
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE.*work_item_id.*sequence"):
            connection.execute(add_command, (1, "alice", "another request", "first"))
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            connection.execute(add_command, (0, "alice", "another request", "first"))
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL.*sequence"):
            connection.execute(add_command, (None, "alice", "another request", "first"))


def test_kernel_v38_sqlite_schema_is_frozen(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)

    with sqlite3.connect(database) as connection:
        digest = _schema_digest(connection)
        schema_version = connection.execute(
            "SELECT version FROM schema_version"
        ).fetchone()[0]
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()

    assert digest == KERNEL_V38_SCHEMA_DIGEST
    assert schema_version == 38
    # Kernel versions its store with schema_version; changing the unused SQLite
    # pragma would still be a storage-contract change.
    assert user_version == 0
    assert foreign_key_errors == []


def test_explicit_migration_adds_workflow_selections_to_v34(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        _drop_v36_output_steps(connection)
        connection.execute("DROP TABLE message_workflow_selections")
        connection.execute("UPDATE schema_version SET version=34")
    with pytest.raises(ValueError, match="SCHEMA_VERSION_UNSUPPORTED"):
        initialize_database(database)
    migrate_database(database)
    initialize_database(database)
    assert len(list(tmp_path.glob("kernel.sqlite3.v34-preupgrade-*.sqlite3"))) == 1


def _drop_v36_output_steps(connection: sqlite3.Connection) -> None:
    _drop_v37_work_items(connection)
    connection.execute("DROP TABLE workflow_output_sources")
    connection.execute("DROP TABLE published_output_steps")


def _drop_v37_work_items(connection: sqlite3.Connection) -> None:
    connection.execute("DROP TRIGGER work_item_session_activity")
    connection.execute("DROP TRIGGER work_item_command_activity")
    connection.execute("DROP TABLE work_item_activity")
    connection.execute("DROP TABLE work_item_commands")
    connection.execute("DROP TABLE work_item_references")
    connection.execute("DROP TABLE work_item_sessions")
    connection.execute("DROP TABLE work_items")


def test_migrate_existing_v35_to_output_steps(tmp_path) -> None:
    database = tmp_path / "kernel.sqlite3"
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        _drop_v36_output_steps(connection)
        connection.execute("UPDATE schema_version SET version=35")
    migrate_database(database)
    initialize_database(database)
    assert len(list(tmp_path.glob("kernel.sqlite3.v35-preupgrade-*.sqlite3"))) == 1


def _drop_v34_identity_schema(connection: sqlite3.Connection) -> None:
    _drop_v36_output_steps(connection)
    connection.execute("DROP TABLE message_workflow_selections")
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
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 38
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
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 38
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
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 38
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
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 38
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
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 38
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
