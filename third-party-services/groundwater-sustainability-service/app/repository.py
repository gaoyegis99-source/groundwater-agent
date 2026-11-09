from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schemas import TaskStatus


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SustainabilityTaskRepository:
    """SQLite persistence for comprehensive sustainability evaluation tasks."""

    def __init__(self, database_path: Path):
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sustainability_tasks (
                    task_id TEXT PRIMARY KEY,
                    client_request_id TEXT UNIQUE,
                    status TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    result_json TEXT,
                    result_file TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    error_details_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_sustainability_tasks_status "
                "ON sustainability_tasks(status)"
            )

    def recover_unfinished(self) -> int:
        now = utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE sustainability_tasks
                SET status = ?, error_code = ?, error_message = ?, error_details_json = ?,
                    updated_at = ?, finished_at = ?
                WHERE status IN (?, ?)
                """,
                (
                    TaskStatus.failed.value,
                    "SERVICE_RESTARTED",
                    "服务重启，未完成任务已终止；请重新提交。",
                    json.dumps({"recoverable": True}, ensure_ascii=False),
                    now,
                    now,
                    TaskStatus.pending.value,
                    TaskStatus.running.value,
                ),
            )
            return cursor.rowcount

    def create(self, task_id: str, client_request_id: str | None, input_data: dict[str, Any]) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sustainability_tasks (
                    task_id, client_request_id, status, input_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    client_request_id,
                    TaskStatus.pending.value,
                    json.dumps(input_data, ensure_ascii=False, separators=(",", ":")),
                    now,
                    now,
                ),
            )

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sustainability_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return self._decode(row) if row else None

    def get_by_client_request_id(self, client_request_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sustainability_tasks WHERE client_request_id = ?",
                (client_request_id,),
            ).fetchone()
        return self._decode(row) if row else None

    def claim_pending(self, task_id: str) -> bool:
        now = utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE sustainability_tasks SET status = ?, started_at = ?, updated_at = ? "
                "WHERE task_id = ? AND status = ?",
                (
                    TaskStatus.running.value,
                    now,
                    now,
                    task_id,
                    TaskStatus.pending.value,
                ),
            )
            return cursor.rowcount == 1

    def cancel_pending(self, task_id: str) -> bool:
        now = utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE sustainability_tasks
                SET status = ?, updated_at = ?, finished_at = ?
                WHERE task_id = ? AND status = ?
                """,
                (
                    TaskStatus.cancelled.value,
                    now,
                    now,
                    task_id,
                    TaskStatus.pending.value,
                ),
            )
            return cursor.rowcount == 1

    def mark_succeeded(self, task_id: str, result: dict[str, Any], result_file: str) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE sustainability_tasks
                SET status = ?, result_json = ?, result_file = ?, updated_at = ?, finished_at = ?
                WHERE task_id = ?
                """,
                (
                    TaskStatus.succeeded.value,
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    result_file,
                    now,
                    now,
                    task_id,
                ),
            )

    def mark_failed(self, task_id: str, code: str, message: str, details: Any | None = None) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE sustainability_tasks
                SET status = ?, error_code = ?, error_message = ?, error_details_json = ?,
                    updated_at = ?, finished_at = ? WHERE task_id = ?
                """,
                (
                    TaskStatus.failed.value,
                    code,
                    message,
                    json.dumps(details, ensure_ascii=False) if details is not None else None,
                    now,
                    now,
                    task_id,
                ),
            )

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["input"] = json.loads(data.pop("input_json"))
        data["result"] = json.loads(data.pop("result_json")) if data.get("result_json") else None
        data.pop("result_json", None)
        data["error_details"] = (
            json.loads(data.pop("error_details_json")) if data.get("error_details_json") else None
        )
        data.pop("error_details_json", None)
        return data
