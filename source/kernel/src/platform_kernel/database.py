from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock


from .schema import SCHEMA, SCHEMA_VERSION
from .schema.interaction import (
    RUN_CONTROLS_INDEX_SQL,
    RUN_CONTROLS_SCHEMA,
    SESSIONS_OWNER_SUBJECT_TRIGGER,
    SESSIONS_SCHEMA,
)
from .schema.agent import PROVIDER_REQUESTS_SCHEMA
from .schema.workflow import WORKFLOW_SERVICE_BINDINGS_SCHEMA
from .schema.common import RESEARCH_IDENTITY_SCHEMA


V29_SCHEMA_DIGEST = "b52e947cc231e822f3c3536ad6ae8d8e6a20c688330e38bb60053229ed872fcc"
V30_SCHEMA_DIGEST = "20748d39effe13c30dc3c3f602170d021ea24ea507b339b4bf68943a1205c3ed"
V31_SCHEMA_DIGEST = "1547c6e45d08f63b6fb7e1e602ae730ac1d13fb933c38478011a52d76b71e008"
V32_SCHEMA_DIGEST = "540f693c1e743bc8aae7c1b4d0309e11bac0024f268fdd480ca73c0482239364"
V33_SCHEMA_DIGEST = "805b0f2272c4da80f615a78d8006d5507446f00175f6348ac75ece82bf239e75"


_listener_lock = Lock()
_commit_listeners: dict[Path, set[Callable[[], None]]] = {}
_sqlite_busy_lock = Lock()
_sqlite_busy_total = 0


class _KernelConnection(sqlite3.Connection):
    event_appended = False


def register_commit_listener(path: Path, listener: Callable[[], None]) -> None:
    with _listener_lock:
        _commit_listeners.setdefault(path.resolve(), set()).add(listener)


def unregister_commit_listener(path: Path, listener: Callable[[], None]) -> None:
    with _listener_lock:
        listeners = _commit_listeners.get(path.resolve())
        if listeners is None:
            return
        listeners.discard(listener)
        if not listeners:
            del _commit_listeners[path.resolve()]


def _notify_commit(path: Path) -> None:
    with _listener_lock:
        listeners = tuple(_commit_listeners.get(path.resolve(), ()))
    for listener in listeners:
        listener()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def duration_ms(started_at: str, finished_at: str) -> int:
    started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    finished = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    return int((finished - started).total_seconds() * 1000)


@contextmanager
def connect_database(path: Path) -> Iterator[sqlite3.Connection]:
    global _sqlite_busy_total
    connection = sqlite3.connect(path, timeout=30, factory=_KernelConnection)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            yield connection
        if connection.event_appended:
            _notify_commit(path)
    except sqlite3.OperationalError as error:
        if "locked" in str(error).lower() or "busy" in str(error).lower():
            with _sqlite_busy_lock:
                _sqlite_busy_total += 1
        raise
    finally:
        connection.close()


def sqlite_busy_total() -> int:
    with _sqlite_busy_lock:
        return _sqlite_busy_total


def begin_immediate(connection: sqlite3.Connection, *, wait: bool = True) -> bool:
    """Return False only when a nonblocking BEGIN meets another writer."""
    global _sqlite_busy_total
    if wait:
        connection.execute("BEGIN IMMEDIATE")
        return True
    previous_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
    connection.execute("PRAGMA busy_timeout=0")
    try:
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            code = getattr(error, "sqlite_errorcode", None)
            if code is not None and code & 0xFF == sqlite3.SQLITE_BUSY:
                with _sqlite_busy_lock:
                    _sqlite_busy_total += 1
                return False
            raise
        return True
    finally:
        connection.execute(f"PRAGMA busy_timeout={previous_timeout}")


def initialize_database(path: Path) -> None:
    """Create v34 or open an exact v34 store without implicit upgrades."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with connect_database(path) as connection:
        version_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        if version_table is None:
            user_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name NOT GLOB 'sqlite_*' LIMIT 1"
            ).fetchone()
            if user_table is not None:
                raise ValueError("SCHEMA_VERSION_UNSUPPORTED")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,)
            )
            return
        rows = connection.execute("SELECT version FROM schema_version").fetchall()
        if (
            len(rows) != 1
            or rows[0]["version"] != SCHEMA_VERSION
            or _schema_signature(connection) != _expected_schema_signature()
        ):
            raise ValueError("SCHEMA_VERSION_UNSUPPORTED")
        connection.execute("PRAGMA journal_mode = WAL")


def migrate_database(path: Path) -> None:
    """Explicitly migrate one frozen v29-v33 store through every version."""
    path.parent.mkdir(parents=True, exist_ok=True)
    upgrade_from: int | None = None
    with connect_database(path) as connection:
        version_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'schema_version'"
        ).fetchone()
        if version_table is None:
            user_table = connection.execute(
                """SELECT 1 FROM sqlite_master
                   WHERE type = 'table' AND name NOT GLOB 'sqlite_*'
                   LIMIT 1"""
            ).fetchone()
            if user_table is not None:
                raise ValueError("SCHEMA_VERSION_UNSUPPORTED")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,)
            )
        else:
            rows = connection.execute("SELECT version FROM schema_version").fetchall()
            if len(rows) != 1:
                raise ValueError("SCHEMA_VERSION_UNSUPPORTED")
            if rows[0]["version"] == 29:
                if _schema_digest(_schema_signature(connection)) != V29_SCHEMA_DIGEST:
                    raise ValueError("SCHEMA_VERSION_UNSUPPORTED")
                upgrade_from = 29
            elif rows[0]["version"] == 30:
                if _schema_digest(_schema_signature(connection)) != V30_SCHEMA_DIGEST:
                    raise ValueError("SCHEMA_VERSION_UNSUPPORTED")
                upgrade_from = 30
            elif rows[0]["version"] == 31:
                if _schema_digest(_schema_signature(connection)) != V31_SCHEMA_DIGEST:
                    raise ValueError("SCHEMA_VERSION_UNSUPPORTED")
                upgrade_from = 31
            elif rows[0]["version"] == 32:
                if _schema_digest(_schema_signature(connection)) != V32_SCHEMA_DIGEST:
                    raise ValueError("SCHEMA_VERSION_UNSUPPORTED")
                upgrade_from = 32
            elif rows[0]["version"] == 33:
                if _schema_digest(_schema_signature(connection)) != V33_SCHEMA_DIGEST:
                    raise ValueError("SCHEMA_VERSION_UNSUPPORTED")
                upgrade_from = 33
            elif rows[0]["version"] != SCHEMA_VERSION:
                raise ValueError("SCHEMA_VERSION_UNSUPPORTED")
            elif _schema_signature(connection) != _expected_schema_signature():
                raise ValueError("SCHEMA_VERSION_UNSUPPORTED")
        connection.execute("PRAGMA journal_mode = WAL")
    if upgrade_from == 29:
        _upgrade_v29_database(path)
        upgrade_from = 30
    if upgrade_from == 30:
        _upgrade_v30_database(path)
        upgrade_from = 31
    if upgrade_from == 31:
        _upgrade_v31_database(path)
        upgrade_from = 32
    if upgrade_from == 32:
        _upgrade_v32_database(path)
        upgrade_from = 33
    if upgrade_from == 33:
        _upgrade_v33_database(path)
    with connect_database(path) as connection:
        rows = connection.execute("SELECT version FROM schema_version").fetchall()
        if len(rows) != 1 or rows[0]["version"] != SCHEMA_VERSION:
            raise ValueError("SCHEMA_VERSION_UNSUPPORTED")
        if _schema_signature(connection) != _expected_schema_signature():
            raise ValueError("SCHEMA_VERSION_UNSUPPORTED")
        connection.execute("PRAGMA journal_mode = WAL")


def _upgrade_v33_database(path: Path) -> None:
    """Add research-local identity without rewriting scientific records."""

    _backup_database(path, 33)
    with connect_database(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute("SELECT version FROM schema_version").fetchall()
        if (
            len(rows) != 1
            or rows[0]["version"] != 33
            or _schema_digest(_schema_signature(connection)) != V33_SCHEMA_DIGEST
        ):
            raise ValueError("SCHEMA_VERSION_UNSUPPORTED")
        connection.executescript(RESEARCH_IDENTITY_SCHEMA)
        connection.execute("UPDATE schema_version SET version=34")


def _backup_database(path: Path, version: int) -> Path:
    """Create an immutable content-addressed backup for a schema step."""

    temporary = path.with_name(f".{path.name}.v{version}-preupgrade.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with sqlite3.connect(path) as source, sqlite3.connect(temporary) as target:
            source.backup(target)
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        backup = path.with_name(
            f"{path.name}.v{version}-preupgrade-{digest[:16]}.sqlite3"
        )
        if backup.exists():
            if hashlib.sha256(backup.read_bytes()).hexdigest() != digest:
                raise ValueError("SCHEMA_UPGRADE_BACKUP_CONFLICT")
            temporary.unlink()
        else:
            os.replace(temporary, backup)
        return backup
    finally:
        temporary.unlink(missing_ok=True)


def _schema_digest(signature: tuple[tuple[object, ...], ...]) -> str:
    encoded = json.dumps(
        signature, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _backup_v29_database(path: Path) -> Path:
    """Create a content-addressed pre-upgrade SQLite backup without overwrite."""

    temporary = path.with_name(f".{path.name}.v29-preupgrade.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with sqlite3.connect(path) as source, sqlite3.connect(temporary) as target:
            source.backup(target)
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        backup = path.with_name(f"{path.name}.v29-preupgrade-{digest[:16]}.sqlite3")
        if backup.exists():
            if hashlib.sha256(backup.read_bytes()).hexdigest() != digest:
                raise ValueError("SCHEMA_UPGRADE_BACKUP_CONFLICT")
            temporary.unlink()
        else:
            os.replace(temporary, backup)
        return backup
    finally:
        temporary.unlink(missing_ok=True)


def _upgrade_v29_database(path: Path) -> None:
    """Upgrade exactly the frozen v29 layout to v30 in one transaction.

    The pre-upgrade backup is made before acquiring the write transaction.  The
    migration itself rebuilds ``run_controls`` instead of using ALTER TABLE so
    upgraded and freshly-created stores share one verifiable schema signature.
    """

    _backup_v29_database(path)
    with connect_database(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute("SELECT version FROM schema_version").fetchall()
        if (
            len(rows) != 1
            or rows[0]["version"] != 29
            or _schema_digest(_schema_signature(connection)) != V29_SCHEMA_DIGEST
        ):
            raise ValueError("SCHEMA_VERSION_UNSUPPORTED")
        connection.execute("ALTER TABLE run_controls RENAME TO run_controls_v29_upgrade")
        connection.execute(RUN_CONTROLS_SCHEMA)
        connection.execute(
            """INSERT INTO run_controls(
                   session_id,request_id,agent_run_id,requested_by,status,created_at,
                   claim_owner,claim_generation,claim_until
               )
               SELECT session_id,request_id,agent_run_id,requested_by,status,created_at,
                      NULL,0,NULL
               FROM run_controls_v29_upgrade"""
        )
        connection.execute("DROP TABLE run_controls_v29_upgrade")
        connection.execute(RUN_CONTROLS_INDEX_SQL)
        connection.execute("UPDATE schema_version SET version=30")


def _backup_v30_database(path: Path) -> Path:
    """Create a content-addressed pre-upgrade backup without replacing evidence."""

    temporary = path.with_name(f".{path.name}.v30-preupgrade.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with sqlite3.connect(path) as source, sqlite3.connect(temporary) as target:
            source.backup(target)
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        backup = path.with_name(f"{path.name}.v30-preupgrade-{digest[:16]}.sqlite3")
        if backup.exists():
            if hashlib.sha256(backup.read_bytes()).hexdigest() != digest:
                raise ValueError("SCHEMA_UPGRADE_BACKUP_CONFLICT")
            temporary.unlink()
        else:
            os.replace(temporary, backup)
        return backup
    finally:
        temporary.unlink(missing_ok=True)


def _upgrade_v30_database(path: Path) -> None:
    """Rebuild only ``sessions`` to remove its implicit local actor default.

    SQLite cannot drop a column default.  ``legacy_alter_table`` keeps existing
    child foreign-key declarations pointed at the recreated ``sessions`` table;
    the transaction is checked before commit and the v30 backup remains the
    recovery source if anything fails.
    """

    _backup_v30_database(path)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA legacy_alter_table=ON")
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute("SELECT version FROM schema_version").fetchall()
        if (
            len(rows) != 1
            or rows[0][0] != 30
            or _schema_digest(_schema_signature(connection)) != V30_SCHEMA_DIGEST
        ):
            raise ValueError("SCHEMA_VERSION_UNSUPPORTED")
        connection.execute("DROP TRIGGER sessions_owner_subject_immutable")
        connection.execute("DROP INDEX sessions_owner_archive_idx")
        connection.execute("DROP INDEX sessions_agent_start_idx")
        connection.execute("ALTER TABLE sessions RENAME TO sessions_v30_upgrade")
        connection.execute(SESSIONS_SCHEMA)
        connection.execute(SESSIONS_OWNER_SUBJECT_TRIGGER)
        connection.execute(
            """INSERT INTO sessions(
                   session_id,project_id,goal_revision,agent_run_id,pending_agent_run_id,
                   agent_start_state,owner_subject,title,archived_at
               ) SELECT session_id,project_id,goal_revision,agent_run_id,pending_agent_run_id,
                   agent_start_state,owner_subject,title,archived_at
               FROM sessions_v30_upgrade"""
        )
        connection.execute("DROP TABLE sessions_v30_upgrade")
        connection.execute(
            "CREATE INDEX sessions_owner_archive_idx "
            "ON sessions(project_id, owner_subject, archived_at)"
        )
        connection.execute(
            "CREATE INDEX sessions_agent_start_idx ON sessions(agent_start_state) "
            "WHERE pending_agent_run_id IS NOT NULL"
        )
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("SCHEMA_VERSION_UNSUPPORTED")
        connection.execute("UPDATE schema_version SET version=31")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA legacy_alter_table=OFF")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.close()


def _upgrade_v31_database(path: Path) -> None:
    """Widen the provider attempt outcome without changing recorded attempts."""

    temporary = path.with_name(f".{path.name}.v31-preupgrade.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with sqlite3.connect(path) as source, sqlite3.connect(temporary) as target:
            source.backup(target)
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        backup = path.with_name(f"{path.name}.v31-preupgrade-{digest[:16]}.sqlite3")
        if backup.exists():
            if hashlib.sha256(backup.read_bytes()).hexdigest() != digest:
                raise ValueError("SCHEMA_UPGRADE_BACKUP_CONFLICT")
            temporary.unlink()
        else:
            os.replace(temporary, backup)
    finally:
        temporary.unlink(missing_ok=True)

    with connect_database(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute("SELECT version FROM schema_version").fetchall()
        if (
            len(rows) != 1
            or rows[0]["version"] != 31
            or _schema_digest(_schema_signature(connection)) != V31_SCHEMA_DIGEST
        ):
            raise ValueError("SCHEMA_VERSION_UNSUPPORTED")
        connection.execute(
            "ALTER TABLE provider_requests RENAME TO provider_requests_v31_upgrade"
        )
        connection.execute(PROVIDER_REQUESTS_SCHEMA)
        connection.execute(
            "INSERT INTO provider_requests SELECT * FROM provider_requests_v31_upgrade"
        )
        connection.execute("DROP TABLE provider_requests_v31_upgrade")
        connection.execute("UPDATE schema_version SET version=32")


def _upgrade_v32_database(path: Path) -> None:
    """Add frozen execution targets; historical runs remain explicitly unbound."""

    temporary = path.with_name(f".{path.name}.v32-preupgrade.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with sqlite3.connect(path) as source, sqlite3.connect(temporary) as target:
            source.backup(target)
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        backup = path.with_name(f"{path.name}.v32-preupgrade-{digest[:16]}.sqlite3")
        if backup.exists():
            if hashlib.sha256(backup.read_bytes()).hexdigest() != digest:
                raise ValueError("SCHEMA_UPGRADE_BACKUP_CONFLICT")
            temporary.unlink()
        else:
            os.replace(temporary, backup)
    finally:
        temporary.unlink(missing_ok=True)

    with connect_database(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute("SELECT version FROM schema_version").fetchall()
        if (
            len(rows) != 1
            or rows[0]["version"] != 32
            or _schema_digest(_schema_signature(connection)) != V32_SCHEMA_DIGEST
        ):
            raise ValueError("SCHEMA_VERSION_UNSUPPORTED")
        connection.execute(WORKFLOW_SERVICE_BINDINGS_SCHEMA)
        connection.execute(
            "INSERT INTO workflow_service_bindings(workflow_run_id,target_digest) "
            "SELECT run_id,'legacy-unbound' FROM workflow_runs"
        )
        connection.execute("UPDATE schema_version SET version=33")


def _schema_signature(connection: sqlite3.Connection) -> tuple[tuple[object, ...], ...]:
    return tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT GLOB 'sqlite_*' ORDER BY type, name"
        )
    )


def _expected_schema_signature() -> tuple[tuple[object, ...], ...]:
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(SCHEMA)
        return _schema_signature(connection)


def append_event(
    connection: sqlite3.Connection,
    session_id: str,
    event_type: str,
    payload: dict[str, object],
) -> int:
    cursor = connection.execute(
        """INSERT INTO session_events(session_id, event_type, payload_json, created_at)
           VALUES (?, ?, ?, ?)""",
        (
            session_id,
            event_type,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            utc_now(),
        ),
    )
    if isinstance(connection, _KernelConnection):
        connection.event_appended = True
    return int(cursor.lastrowid)


def read_session_events(
    connection: sqlite3.Connection, session_id: str, event_type: str,
    *, agent_run_ids: tuple[str, ...] | None = None,
) -> list[dict[str, object]]:
    """Read one domain event stream inside the caller's state transaction."""
    query = (
        "SELECT event_id,event_type,payload_json,created_at FROM session_events "
        "WHERE session_id=? AND event_type=?"
    )
    parameters = (session_id, event_type)
    if agent_run_ids is not None:
        if not agent_run_ids:
            return []
        placeholders = ",".join("?" for _ in agent_run_ids)
        query += f" AND json_extract(payload_json,'$.agent_run_id') IN ({placeholders})"
        parameters += agent_run_ids
    rows = connection.execute(
        query + " ORDER BY event_id", parameters,
    ).fetchall()
    return [{
        "event_id": row["event_id"], "event_type": row["event_type"],
        "data": json.loads(row["payload_json"]), "created_at": row["created_at"],
    } for row in rows]
