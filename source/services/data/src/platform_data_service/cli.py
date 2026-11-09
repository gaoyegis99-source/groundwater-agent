import argparse
import json
import logging
import os
from pathlib import Path
import time

import psycopg

from .adapters.database import Database
from .adapters.locking import runtime_lock
from . import maintenance
from .operations.worker import Worker
from .settings import Settings


LOGGER = logging.getLogger(__name__)
DATABASE_STARTUP_ATTEMPTS = 12


def _migrate_when_database_ready(database):
    """Retry only transient database operational failures during Compose startup.

    PostgreSQL's image can briefly accept ``pg_isready`` probes from its
    initialization server before the final server is accepting connections.
    Schema and contract failures use other exception types and still fail on
    their first attempt.
    """
    for attempt in range(1, DATABASE_STARTUP_ATTEMPTS + 1):
        try:
            database.migrate()
            return
        except psycopg.OperationalError:
            if attempt == DATABASE_STARTUP_ATTEMPTS:
                raise
            delay = min(attempt, 3)
            LOGGER.warning(
                "DATABASE_STARTUP_RETRY attempt=%d max_attempts=%d delay_seconds=%d",
                attempt,
                DATABASE_STARTUP_ATTEMPTS,
                delay,
            )
            time.sleep(delay)


def migrate(settings):
    """Apply the schema, or validate it when a live runtime owns the object root.

    Re-running Compose starts the completed migration dependency again.  A live
    service and worker intentionally retain shared object-root locks, so in that
    case migration may only prove that the database is already on the supported
    schema.  A missing or stale schema still fails through ``Database.check``.
    """
    database = Database(settings.dsn)
    try:
        with runtime_lock(settings.root, exclusive=True):
            _migrate_when_database_ready(database)
    except RuntimeError as error:
        if str(error) != "DATA_ROOT_IN_USE":
            raise
        database.check()
    return {"schema": 8}


def main():
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=["migrate", "worker", "audit", "gc", "backup", "restore"]
    )
    parser.add_argument("--path", type=Path)
    args = parser.parse_args()
    settings = Settings.env()
    if args.command == "worker":
        if profile := os.environ.get("DATA_SECCOMP_PROFILE"):
            from .adapters.sandbox import install_container_profile

            install_container_profile(profile)
        with runtime_lock(settings.root):
            Worker(settings).run()
        return
    if args.command == "migrate":
        result = migrate(settings)
    else:
        with runtime_lock(settings.root, exclusive=True):
            if args.command in {"backup", "restore"}:
                if args.path is None:
                    parser.error("--path is required")
                result = getattr(maintenance, args.command)(settings, args.path.resolve())
            else:
                result = getattr(maintenance, args.command)(settings)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
