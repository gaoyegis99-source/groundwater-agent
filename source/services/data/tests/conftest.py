import os
import subprocess

# Pin direct checks to the owning project's Docker launcher.
import sys as _maintenance_sys  # noqa: E402
import time
import uuid
from pathlib import Path
from pathlib import Path as _MaintenancePath  # noqa: E402

import psycopg
import pytest
from fastapi.testclient import TestClient
from platform_data_service.adapters.database import Database
from platform_data_service.api.app import create_app
from platform_data_service.operations.worker import Worker
from platform_data_service.settings import Settings
from psycopg.conninfo import make_conninfo

_maintenance_source = next(
    parent
    for parent in _MaintenancePath(__file__).resolve().parents
    if (parent / "scripts/maintenance/project_environment.py").is_file()
)
_maintenance_sys.path.insert(0, str(_maintenance_source))
from scripts.maintenance.project_environment import docker_executable  # noqa: E402


@pytest.fixture(scope="session")
def postgres():
    supplied = os.environ.get("DATA_TEST_DATABASE_URL")
    if supplied:
        yield supplied
        return
    name = "data-service-test-" + uuid.uuid4().hex[:12]
    subprocess.run(
        [
            docker_executable(),
            "run",
            "--rm",
            "-d",
            "--name",
            name,
            "-e",
            "POSTGRES_PASSWORD=data-test",
            "-p",
            "127.0.0.1::5432",
            "postgres:16.10-bookworm",
        ],
        check=True,
        capture_output=True,
    )
    try:
        port = (
            subprocess.check_output(
                [docker_executable(), "port", name, "5432"], text=True
            )
            .strip()
            .rsplit(":", 1)[1]
        )
        dsn = f"postgresql://postgres:data-test@127.0.0.1:{port}/postgres"
        for _ in range(120):
            try:
                with psycopg.connect(dsn):
                    break
            except psycopg.OperationalError:
                time.sleep(0.25)
        else:
            raise RuntimeError("test PostgreSQL did not start")
        yield dsn
    finally:
        subprocess.run(
            [docker_executable(), "stop", name], check=True, capture_output=True
        )


@pytest.fixture
def settings(postgres, tmp_path):
    name = "test_" + uuid.uuid4().hex
    with psycopg.connect(postgres, autocommit=True) as c:
        c.execute(
            psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name))
        )
    result = Settings(
        make_conninfo(postgres, dbname=name), Path(tmp_path / "store"), query_seconds=20
    )
    Database(result.dsn).migrate()
    yield result
    with psycopg.connect(postgres, autocommit=True) as c:
        c.execute(
            psycopg.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                psycopg.sql.Identifier(name)
            )
        )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def worker(settings):
    return Worker(settings)
