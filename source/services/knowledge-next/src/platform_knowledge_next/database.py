from contextlib import contextmanager
from importlib.resources import files

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

from .config import MODEL_PROFILE
from .errors import KnowledgeProblemError


class Database:
    def __init__(self, settings, *, pool_min=0, pool_max=0, pool_timeout=3.0):
        self.settings = settings
        self.pool = (
            ConnectionPool(
                settings.database_url, min_size=pool_min, max_size=pool_max,
                timeout=pool_timeout,
                kwargs={"row_factory": dict_row, "connect_timeout": 5}, open=False,
                name="knowledge",
            )
            if pool_max
            else None
        )
        self._pool_open = False

    def open(self):
        if self.pool is not None:
            self.pool.open()
            self._pool_open = True

    def close(self):
        if self.pool is not None:
            self.pool.close()
            self._pool_open = False

    def pool_stats(self):
        return {} if self.pool is None or not self._pool_open else self.pool.get_stats()

    def import_metrics(self):
        with self.transaction() as connection:
            states = connection.execute(
                "SELECT state,count(*) AS count FROM imports GROUP BY state"
            ).fetchall()
            oldest = connection.execute(
                "SELECT COALESCE(EXTRACT(epoch FROM now()-min(created_at)),0) AS age "
                "FROM imports WHERE state IN ('QUEUED','RETRY_WAIT')"
            ).fetchone()
            retries = connection.execute(
                "SELECT count(*) AS count FROM imports WHERE attempt>1"
            ).fetchone()
        return (
            {row["state"]: row["count"] for row in states},
            float(oldest["age"]),
            int(retries["count"]),
        )

    @contextmanager
    def transaction(self, *, snapshot=False):
        try:
            manager = (
                self.pool.connection() if self.pool is not None and self._pool_open
                else psycopg.connect(
                    self.settings.database_url,
                    row_factory=dict_row,
                    connect_timeout=5,
                )
            )
            with manager as c:
                if snapshot:
                    c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                c.execute("SET LOCAL statement_timeout = '15s'")
                yield c
        except PoolTimeout as error:
            raise KnowledgeProblemError("DATABASE_POOL_EXHAUSTED", 503) from error

    def migrate(self):
        with self.transaction() as c:
            c.execute("SELECT pg_advisory_xact_lock(749301)")
            if (
                c.execute("SELECT to_regclass('public.meta') AS name").fetchone()[
                    "name"
                ]
                is None
            ):
                c.execute(files(__package__).joinpath("migrations/001.sql").read_text())
                c.execute(
                    "INSERT INTO meta(schema_version,project,profile) VALUES(4,%s,%s)",
                    (self.settings.project, MODEL_PROFILE),
                )
            row = c.execute("SELECT * FROM meta").fetchone()
            if (row["project"], row["profile"]) != (
                self.settings.project,
                MODEL_PROFILE,
            ):
                raise KnowledgeProblemError("DATABASE_CONTRACT_MISMATCH", 503)
            if row["schema_version"] == 4:
                c.execute(files(__package__).joinpath("migrations/002.sql").read_text())
                c.execute("UPDATE meta SET schema_version=5 WHERE singleton=true")
                row = c.execute("SELECT * FROM meta").fetchone()
            if row["schema_version"] == 5:
                c.execute(files(__package__).joinpath("migrations/003.sql").read_text())
                c.execute("UPDATE meta SET schema_version=6 WHERE singleton=true")
                row = c.execute("SELECT * FROM meta").fetchone()
            if row["schema_version"] == 6:
                c.execute(files(__package__).joinpath("migrations/004.sql").read_text())
                c.execute("UPDATE meta SET schema_version=7 WHERE singleton=true")
                row = c.execute("SELECT * FROM meta").fetchone()
            if row["schema_version"] != 7:
                raise KnowledgeProblemError("SCHEMA_VERSION_UNSUPPORTED", 503)
        self.check()

    def check(self):
        with self.transaction() as c:
            row = c.execute("SELECT * FROM meta").fetchone()
            if (row["schema_version"], row["project"], row["profile"]) != (
                7,
                self.settings.project,
                MODEL_PROFILE,
            ):
                raise KnowledgeProblemError("DATABASE_CONTRACT_MISMATCH", 503)


def required(row):
    if row is None:
        raise KnowledgeProblemError("NOT_FOUND", 404)
    return row
