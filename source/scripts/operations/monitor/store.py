"""Durable Docker log history, independent of the product databases."""

from contextlib import contextmanager
import sqlite3
from threading import Lock


class LogStore:
    def __init__(self, path):
        self.lock = Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;
            CREATE TABLE IF NOT EXISTS logs(
                id INTEGER PRIMARY KEY AUTOINCREMENT, container TEXT NOT NULL,
                service TEXT NOT NULL, stamp TEXT NOT NULL, ordinal INTEGER NOT NULL,
                epoch REAL NOT NULL, line TEXT NOT NULL, kind TEXT NOT NULL,
                UNIQUE(container,stamp,ordinal,kind)
            );
            CREATE INDEX IF NOT EXISTS logs_time ON logs(epoch);
            CREATE INDEX IF NOT EXISTS logs_service ON logs(service,id);
            CREATE TABLE IF NOT EXISTS checkpoints(
                container TEXT PRIMARY KEY, stamp TEXT NOT NULL, ordinal INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS service_containers(
                service TEXT PRIMARY KEY, container TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS retention(floor INTEGER NOT NULL);
            INSERT INTO retention SELECT 0 WHERE NOT EXISTS(SELECT 1 FROM retention);
        """)

    @contextmanager
    def transaction(self):
        with self.lock, self.db:
            yield self.db

    def checkpoint(self, container):
        with self.transaction() as db:
            row = db.execute("SELECT stamp,ordinal FROM checkpoints WHERE container=?", (container,)).fetchone()
            return tuple(row) if row else None

    def remember_service(self, service, container):
        with self.transaction() as db:
            row = db.execute("SELECT container FROM service_containers WHERE service=?", (service,)).fetchone()
            db.execute(
                """INSERT INTO service_containers VALUES(?,?) ON CONFLICT(service) DO UPDATE
                   SET container=excluded.container""",
                (service, container),
            )
            return row[0] if row else None

    def append(self, container, service, stamp, ordinal, epoch, line, *, kind="log"):
        self.append_batch(container, service, [(stamp, ordinal, epoch, line)], kind=kind)

    def append_batch(self, container, service, records, *, kind="log"):
        if not records:
            return
        with self.transaction() as db:
            db.executemany(
                """INSERT OR IGNORE INTO logs(container,service,stamp,ordinal,epoch,line,kind)
                   VALUES(?,?,?,?,?,?,?)""",
                ((container, service, stamp, ordinal, epoch, line, kind)
                 for stamp, ordinal, epoch, line in records),
            )
            if kind == "log":
                stamp, ordinal, _, _ = max(records)
                db.execute(
                    """INSERT INTO checkpoints VALUES(?,?,?) ON CONFLICT(container) DO UPDATE
                       SET stamp=excluded.stamp, ordinal=excluded.ordinal
                       WHERE (excluded.stamp,excluded.ordinal) > (checkpoints.stamp,checkpoints.ordinal)""",
                    (container, stamp, ordinal),
                )

    def read(self, *, after=None, before=None, service=None, needle=None, start=None, end=None, limit=300):
        with self.transaction() as db:
            order = "DESC" if after is None else "ASC"
            rows = db.execute(
                f"""SELECT id,service,stamp,epoch,line,kind FROM logs
                    WHERE id > ? AND (? IS NULL OR id < ?) AND (? IS NULL OR service=?)
                    AND (? IS NULL OR instr(lower(line),lower(?))>0)
                    AND (? IS NULL OR epoch>=?) AND (? IS NULL OR epoch<=?)
                    ORDER BY id {order} LIMIT ?""",
                (after or 0, before, before, service, service, needle, needle, start, start, end, end, limit),
            ).fetchall()
        return [dict(row) for row in (reversed(rows) if after is None else rows)]

    def latest(self):
        with self.transaction() as db:
            row = db.execute("SELECT seq FROM sqlite_sequence WHERE name='logs'").fetchone()
            return row[0] if row else 0

    def expired(self, cursor):
        with self.transaction() as db:
            floor = db.execute("SELECT floor FROM retention").fetchone()[0]
            return 0 < floor and cursor <= floor

    def prune(self, now):
        with self.transaction() as db:
            cutoff = now - 86400
            db.execute(
                "UPDATE retention SET floor=MAX(floor,COALESCE((SELECT MAX(id) FROM logs WHERE epoch<?),0))",
                (cutoff,),
            )
            db.execute("DELETE FROM logs WHERE epoch<?", (cutoff,))
