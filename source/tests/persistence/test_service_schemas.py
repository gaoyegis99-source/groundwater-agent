from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from platform_executor_service.database import (
    initialize_database as initialize_executor,
)
from platform_kernel.database import initialize_database as initialize_kernel
from platform_kernel.schema import SCHEMA_VERSION as KERNEL_SCHEMA_VERSION


INITIALIZERS = {
    "kernel": (initialize_kernel, KERNEL_SCHEMA_VERSION),
    "executor": (initialize_executor, 8),
}
TABLES = {
    "kernel": (
        "messages",
        "role",
        "agent_runs_project_idx",
        "sessions",
        "project_id TEXT NOT NULL",
    ),
    "executor": (
        "executor_jobs",
        "stage",
        "executor_jobs_status_idx",
        "executor_jobs",
        "request_id TEXT NOT NULL",
    ),
}


@pytest.mark.parametrize("name", INITIALIZERS)
def test_each_current_sqlite_owner_has_an_independent_versioned_database(
    tmp_path: Path, name: str
) -> None:
    initialize, version = INITIALIZERS[name]
    database = tmp_path / f"{name}.sqlite3"
    initialize(database)
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute("SELECT version FROM schema_version").fetchone()[0]
            == version
        )
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    if name == "kernel":
        assert "messages" in tables
        assert "executor_jobs" not in tables
    else:
        assert "executor_jobs" in tables
        assert "messages" not in tables


@pytest.mark.parametrize("name", INITIALIZERS)
def test_unversioned_nonempty_database_is_rejected_without_modification(
    tmp_path: Path, name: str
) -> None:
    initialize, _version = INITIALIZERS[name]
    database = tmp_path / f"{name}.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE legacy(value TEXT)")
        connection.execute("INSERT INTO legacy VALUES ('preserve')")
    _assert_rejected_without_modification(initialize, database)


@pytest.mark.parametrize("name", INITIALIZERS)
def test_unknown_schema_version_is_rejected(tmp_path: Path, name: str) -> None:
    initialize, _version = INITIALIZERS[name]
    database = tmp_path / f"{name}.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE schema_version(version INTEGER NOT NULL)")
        connection.execute("INSERT INTO schema_version VALUES (999)")
    _assert_rejected_without_modification(initialize, database)


@pytest.mark.parametrize("name", INITIALIZERS)
def test_current_version_with_modified_schema_is_rejected_without_modification(
    tmp_path: Path, name: str
) -> None:
    initialize, _version = INITIALIZERS[name]
    table = TABLES[name][0]
    database = tmp_path / f"{name}.sqlite3"
    initialize(database)
    with sqlite3.connect(database) as connection:
        connection.execute(f"DROP TABLE {table}")
    _assert_rejected_without_modification(initialize, database)


@pytest.mark.parametrize("name", INITIALIZERS)
def test_current_version_with_missing_column_is_rejected(
    tmp_path: Path, name: str
) -> None:
    initialize, _version = INITIALIZERS[name]
    table, column = TABLES[name][:2]
    database = tmp_path / f"{name}.sqlite3"
    initialize(database)
    with sqlite3.connect(database) as connection:
        if name == "kernel":
            connection.execute("DROP INDEX messages_session_revision_role_idx")
        connection.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    _assert_rejected_without_modification(initialize, database)


@pytest.mark.parametrize("name", INITIALIZERS)
def test_current_version_with_wrong_index_is_rejected(
    tmp_path: Path, name: str
) -> None:
    initialize, _version = INITIALIZERS[name]
    index = TABLES[name][2]
    database = tmp_path / f"{name}.sqlite3"
    initialize(database)
    with sqlite3.connect(database) as connection:
        connection.execute(f"DROP INDEX {index}")
    _assert_rejected_without_modification(initialize, database)


@pytest.mark.parametrize("name", INITIALIZERS)
def test_current_version_with_extra_table_is_rejected(
    tmp_path: Path, name: str
) -> None:
    initialize, _version = INITIALIZERS[name]
    database = tmp_path / f"{name}.sqlite3"
    initialize(database)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unexpected(value TEXT)")
    _assert_rejected_without_modification(initialize, database)


@pytest.mark.parametrize("name", INITIALIZERS)
def test_current_version_with_changed_constraint_is_rejected(
    tmp_path: Path, name: str
) -> None:
    initialize, _version = INITIALIZERS[name]
    table, fragment = TABLES[name][3:]
    database = tmp_path / f"{name}.sqlite3"
    initialize(database)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA writable_schema = ON")
        connection.execute(
            "UPDATE sqlite_master SET sql = replace(sql, ?, ?) WHERE name = ?",
            (fragment, fragment.replace(" NOT NULL", ""), table),
        )
    _assert_rejected_without_modification(initialize, database)


def _assert_rejected_without_modification(initialize, database: Path) -> None:
    before = database.read_bytes()
    with pytest.raises((ValueError, RuntimeError), match="SCHEMA_VERSION_UNSUPPORTED"):
        initialize(database)
    assert database.read_bytes() == before
