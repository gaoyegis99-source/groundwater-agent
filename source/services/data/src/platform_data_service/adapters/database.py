from contextlib import contextmanager
import hashlib
from importlib.resources import files
import json
import uuid

import psycopg
from psycopg_pool import ConnectionPool, PoolTimeout
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def identity():
    return uuid.uuid4().hex


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value).encode()).hexdigest()


class Database:
    def __init__(self, dsn, *, pool_min=0, pool_max=0, pool_timeout=3.0):
        self.dsn = dsn
        self.pool = (
            ConnectionPool(
                dsn, min_size=pool_min, max_size=pool_max, timeout=pool_timeout,
                kwargs={"row_factory": dict_row, "connect_timeout": 5}, open=False,
                name="data",
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

    def operation_metrics(self, project):
        with self.transaction() as connection:
            states = connection.execute(
                "SELECT state,count(*) AS count FROM operations "
                "WHERE project=%s GROUP BY state",
                (project,),
            ).fetchall()
            oldest = connection.execute(
                "SELECT COALESCE(EXTRACT(epoch FROM now()-min(created_at)),0) AS age "
                "FROM operations WHERE project=%s AND state='QUEUED'",
                (project,),
            ).fetchone()
            retries = connection.execute(
                "SELECT count(*) AS count FROM operations "
                "WHERE project=%s AND generation>1",
                (project,),
            ).fetchone()
            publications = connection.execute(
                "SELECT state,count(*) AS count FROM publications "
                "WHERE project=%s GROUP BY state",
                (project,),
            ).fetchall()
            outbox = connection.execute(
                """SELECT state,count(*) AS count FROM outbox_events
                   WHERE project=%s GROUP BY state""",
                (project,),
            ).fetchall()
        return (
            {row["state"]: row["count"] for row in states},
            float(oldest["age"]),
            int(retries["count"]),
            {row["state"]: row["count"] for row in publications},
            {row["state"]: row["count"] for row in outbox},
        )

    @contextmanager
    def transaction(self):
        try:
            manager = (
                self.pool.connection() if self.pool is not None and self._pool_open
                else psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5)
            )
            with manager as conn:
                conn.execute("SET LOCAL statement_timeout = '15s'")
                conn.execute("SET LOCAL lock_timeout = '5s'")
                yield conn
        except PoolTimeout as error:
            from ..errors import DataProblemError

            raise DataProblemError("DATABASE_POOL_EXHAUSTED", 503) from error

    def check(self):
        with self.transaction() as c:
            rows = c.execute("SELECT version FROM schema_version").fetchall()
            if rows != [{"version": 8}]:
                raise RuntimeError("SCHEMA_VERSION_UNSUPPORTED")

    def migrate(self):
        with self.transaction() as c:
            c.execute("SELECT pg_advisory_xact_lock(76541231)")
            if (
                c.execute("SELECT to_regclass('schema_version') AS name").fetchone()[
                    "name"
                ]
                is None
            ):
                c.execute(
                    files("platform_data_service")
                    .joinpath("migrations/001.sql")
                    .read_text()
                )
            version = c.execute("SELECT version FROM schema_version").fetchone()["version"]
            if version == 4:
                c.execute(
                    files("platform_data_service")
                    .joinpath("migrations/002.sql")
                    .read_text()
                )
                version = 5
            if version == 5:
                c.execute(
                    files("platform_data_service")
                    .joinpath("migrations/003.sql")
                    .read_text()
                )
                version = 6
            if version == 6:
                c.execute(
                    files("platform_data_service")
                    .joinpath("migrations/004.sql")
                    .read_text()
                )
                version = 7
            if version == 7:
                c.execute(
                    files("platform_data_service")
                    .joinpath("migrations/005.sql")
                    .read_text()
                )
                version = 8
            if version != 8:
                raise RuntimeError("SCHEMA_VERSION_UNSUPPORTED")
        self.check()


def event(c, op):
    c.execute(
        "INSERT INTO operation_events(operation_id,state,generation) VALUES (%s,%s,%s)",
        (op["id"], op["state"], op["generation"]),
    )


def enqueue(c, project, kind, target, payload, actor):
    op = c.execute(
        """INSERT INTO operations(id,project,kind,target,payload,state,actor)
        VALUES (%s,%s,%s,%s,%s,'QUEUED',%s) RETURNING *""",
        (identity(), project, kind, target, Jsonb(payload), Jsonb(actor)),
    ).fetchone()
    event(c, op)
    return op["id"]
