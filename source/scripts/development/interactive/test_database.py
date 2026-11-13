"""Provide a fresh, local Knowledge database for one validation run."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import re
import secrets
import subprocess
import time

from scripts.maintenance.project_environment import docker_executable


@contextmanager
def knowledge_test_database(image: str) -> Iterator[str]:
    docker = docker_executable()
    name = f"gw-interactive-knowledge-{secrets.token_hex(8)}"
    password = secrets.token_hex(16)
    started = subprocess.run(
        [
            docker, "run", "--rm", "--detach", "--pull", "never", "--name", name,
            "--label", "platform.dev.purpose=knowledge-validation",
            "--env", f"POSTGRES_PASSWORD={password}", "--env", "POSTGRES_DB=knowledge_test",
            "--publish", "127.0.0.1::5432", image,
        ],
        capture_output=True, text=True, timeout=30,
    )
    if started.returncode:
        raise RuntimeError("DEV_KNOWLEDGE_TEST_CONTAINER_START_FAILED")
    try:
        binding = subprocess.check_output(
            [docker, "port", name, "5432/tcp"], text=True, timeout=10
        ).strip()
        if not re.fullmatch(r"127\.0\.0\.1:[0-9]{1,5}", binding):
            raise RuntimeError("DEV_KNOWLEDGE_TEST_PORT_INVALID")
        from psycopg import OperationalError, connect

        dsn = f"postgresql://postgres:{password}@{binding}/knowledge_test"
        deadline = time.monotonic() + 30
        while True:
            try:
                with connect(dsn, connect_timeout=2) as connection:
                    connection.execute("SELECT 1")
                break
            except OperationalError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("DEV_KNOWLEDGE_TEST_DATABASE_NOT_READY") from None
                time.sleep(0.25)
        yield dsn
    finally:
        removed = subprocess.run(
            [docker, "rm", "--force", "--volumes", name],
            capture_output=True, text=True, timeout=30,
        )
        if removed.returncode and "No such container" not in removed.stderr:
            raise RuntimeError("DEV_KNOWLEDGE_TEST_CONTAINER_CLEANUP_FAILED")
