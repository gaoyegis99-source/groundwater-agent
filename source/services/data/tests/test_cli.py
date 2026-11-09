from contextlib import contextmanager

import pytest
import psycopg

from platform_data_service import cli
from platform_data_service.adapters.database import Database


class FakeDatabase:
    def __init__(self, dsn, events, check_error=None):
        self.events = events
        self.check_error = check_error

    def migrate(self):
        self.events.append("migrate")

    def check(self):
        self.events.append("check")
        if self.check_error:
            raise self.check_error


def test_migrate_validates_existing_schema_when_runtime_owns_root(
    settings, monkeypatch
):
    events = []

    @contextmanager
    def locked(*args, **kwargs):
        raise RuntimeError("DATA_ROOT_IN_USE")
        yield

    monkeypatch.setattr(cli, "runtime_lock", locked)
    monkeypatch.setattr(
        cli, "Database", lambda dsn: FakeDatabase(dsn, events)
    )

    assert cli.migrate(settings) == {"schema": 8}
    assert events == ["check"]


def test_migrate_does_not_hide_unsupported_schema(settings, monkeypatch):
    events = []

    @contextmanager
    def locked(*args, **kwargs):
        raise RuntimeError("DATA_ROOT_IN_USE")
        yield

    monkeypatch.setattr(cli, "runtime_lock", locked)
    monkeypatch.setattr(
        cli,
        "Database",
        lambda dsn: FakeDatabase(
            dsn, events, check_error=RuntimeError("DATA_SCHEMA_UNSUPPORTED")
        ),
    )

    with pytest.raises(RuntimeError, match="DATA_SCHEMA_UNSUPPORTED"):
        cli.migrate(settings)
    assert events == ["check"]


def test_migrate_keeps_exclusive_lock_for_schema_change(settings, monkeypatch):
    events = []

    @contextmanager
    def locked(*args, **kwargs):
        events.append(("lock", kwargs["exclusive"]))
        yield

    monkeypatch.setattr(cli, "runtime_lock", locked)
    monkeypatch.setattr(
        cli, "Database", lambda dsn: FakeDatabase(dsn, events)
    )

    assert cli.migrate(settings) == {"schema": 8}
    assert events == [("lock", True), "migrate"]


def test_migrate_retries_transient_database_startup(settings, monkeypatch):
    events = []

    class StartingDatabase(FakeDatabase):
        def migrate(self):
            self.events.append("migrate")
            if self.events.count("migrate") < 3:
                raise psycopg.OperationalError("the database system is starting up")

    monkeypatch.setattr(cli, "Database", lambda dsn: StartingDatabase(dsn, events))
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: events.append(("sleep", seconds)))

    assert cli.migrate(settings) == {"schema": 8}
    assert events == ["migrate", ("sleep", 1), "migrate", ("sleep", 2), "migrate"]


def test_migrate_does_not_retry_schema_failure(settings, monkeypatch):
    events = []

    class InvalidSchemaDatabase(FakeDatabase):
        def migrate(self):
            self.events.append("migrate")
            raise RuntimeError("SCHEMA_VERSION_UNSUPPORTED")

    monkeypatch.setattr(
        cli, "Database", lambda dsn: InvalidSchemaDatabase(dsn, events)
    )
    monkeypatch.setattr(
        cli.time,
        "sleep",
        lambda seconds: pytest.fail(f"unexpected retry after {seconds}s"),
    )

    with pytest.raises(RuntimeError, match="SCHEMA_VERSION_UNSUPPORTED"):
        cli.migrate(settings)
    assert events == ["migrate"]


def test_database_rejects_retired_schema(settings):
    database = Database(settings.dsn)
    with database.transaction() as connection:
        connection.execute("DROP TABLE previews")
        connection.execute(
            "ALTER TABLE operations DROP CONSTRAINT operations_kind_check"
        )
        connection.execute(
            "ALTER TABLE operations ADD CONSTRAINT operations_kind_check "
            "CHECK(kind IN ('prepare','query'))"
        )
        connection.execute("UPDATE schema_version SET version=1")

    with pytest.raises(RuntimeError, match="SCHEMA_VERSION_UNSUPPORTED"):
        database.migrate()
