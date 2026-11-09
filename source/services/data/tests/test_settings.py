from pathlib import Path

import pytest
from psycopg_pool import PoolTimeout

from platform_data_service.adapters.database import Database
from platform_data_service.errors import DataProblemError
from platform_data_service.settings import Settings


def test_database_password_is_read_from_a_secret_file(monkeypatch, tmp_path) -> None:
    secret = tmp_path / "database-password"
    secret.write_text("p@ss:word\n")
    monkeypatch.setenv("DATA_DATABASE_URL", "postgresql://data@data-db:5432/data")
    monkeypatch.setenv("DATA_DATABASE_PASSWORD_FILE", str(secret))
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))

    assert Settings.env().dsn == "postgresql://data:p%40ss%3Aword@data-db:5432/data"


def test_secret_file_mode_rejects_an_inline_database_password(
    monkeypatch, tmp_path
) -> None:
    secret = tmp_path / "database-password"
    secret.write_text("safe\n")
    monkeypatch.setenv(
        "DATA_DATABASE_URL", "postgresql://data:inline@data-db:5432/data"
    )
    monkeypatch.setenv("DATA_DATABASE_PASSWORD_FILE", str(secret))
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))

    with pytest.raises(ValueError, match="URL_WITH_SECRET_FILE_INVALID"):
        Settings.env()


def test_worker_slots_are_bounded_and_read_from_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DATA_DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("DATA_ROOT", str(Path(tmp_path).resolve()))
    assert Settings.env().worker_slots == 2
    monkeypatch.setenv("DATA_WORKER_SLOTS", "4")
    assert Settings.env().worker_slots == 4
    monkeypatch.setenv("DATA_WORKER_SLOTS", "9")
    with pytest.raises(ValueError, match="worker_slots must not exceed 8"):
        Settings.env()


def test_local_identity_uses_the_shared_validated_profile(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DATA_DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("DATA_ROOT", str(Path(tmp_path).resolve()))
    monkeypatch.setenv("PLATFORM_LOCAL_IDENTITY_SUBJECT", "local:data")
    monkeypatch.setenv("PLATFORM_LOCAL_IDENTITY_DISPLAY_NAME", "Data User")
    monkeypatch.setenv("PLATFORM_LOCAL_IDENTITY_ROLES", "researcher,admin")

    assert Settings.env().local_identity.subject == "local:data"
    assert Settings.env().local_identity.roles == ("admin", "researcher")


def test_database_pool_exhaustion_is_a_stable_service_error() -> None:
    class ExhaustedPool:
        def connection(self):
            raise PoolTimeout()

    database = Database("postgresql://unused")
    database.pool = ExhaustedPool()
    database._pool_open = True
    with pytest.raises(DataProblemError) as failure:
        with database.transaction():
            pass
    assert (failure.value.code, failure.value.status) == (
        "DATABASE_POOL_EXHAUSTED",
        503,
    )


@pytest.mark.parametrize("pooled", [False, True])
def test_database_transactions_bound_statement_and_lock_waits(settings, pooled) -> None:
    database = Database(settings.dsn, pool_min=1, pool_max=2) if pooled else Database(settings.dsn)
    if pooled:
        database.open()
    try:
        with database.transaction() as connection:
            row = connection.execute(
                "SELECT current_setting('statement_timeout') AS statement, "
                "current_setting('lock_timeout') AS lock"
            ).fetchone()
        assert row == {"statement": "15s", "lock": "5s"}
    finally:
        database.close()
