import hashlib
import json
import sqlite3
from pathlib import Path

from platform_executor_service.database import (
    SCHEMA,
    SCHEMA_VERSION,
    _schema_signature,
    initialize_database,
)

EXECUTOR_SCHEMA_SIGNATURE = (
    "00ae661ddbe303be4dc320ceac89a4433b42bae8f0a404fdce3739a594a4f028"
)

def test_v12_keeps_executor_sqlite_v9_signature() -> None:
    assert SCHEMA_VERSION == 9
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(SCHEMA)
        encoded = json.dumps(
            _schema_signature(connection),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    assert hashlib.sha256(encoded).hexdigest() == EXECUTOR_SCHEMA_SIGNATURE


def test_stopped_v8_database_migrates_to_v9_without_losing_worker_rows(
    tmp_path: Path,
) -> None:
    legacy = SCHEMA.replace(
        "('native','qgis','remote_http')", "('native','qgis')"
    ).replace(
        "    remote_binding_json TEXT NOT NULL DEFAULT '{}',\n"
        "    remote_state_json TEXT NOT NULL DEFAULT '{}',\n",
        "",
    )
    database = tmp_path / "executor.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(legacy)
        connection.execute("INSERT INTO schema_version(version) VALUES (8)")
        connection.execute(
            "INSERT INTO worker_heartbeats VALUES (?,?,?,?,?,?)",
            ("native-1", "native", "sha256:" + "1" * 64, 1, 1024, "now"),
        )

    initialize_database(database)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone() == (9,)
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(job_nodes)")
        }
        assert {"remote_binding_json", "remote_state_json"} <= columns
        assert connection.execute(
            "SELECT worker_id,runtime FROM worker_heartbeats"
        ).fetchall() == [("native-1", "native")]
