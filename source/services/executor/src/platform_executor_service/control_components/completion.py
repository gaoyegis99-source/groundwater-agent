from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from ..contracts import canonical_json
from ..database import connect_database, utc_now
from ..errors import ExecutionServiceError
from ..orchestration import TaskSucceeded
from ..task_execution import TaskOutcome, require_lease
from .shared import ControlComponent


class CompletionControl(ControlComponent):
    def complete(self, lease_id: str, body: dict) -> None:
        with connect_database(self.database) as connection:
            attempt = require_lease(connection, lease_id, body, utc_now())
            node = connection.execute(
                "SELECT * FROM job_nodes WHERE job_id=? AND node_id=?",
                (attempt["job_id"], attempt["node_id"]),
            ).fetchone()
        outcome = TaskOutcome.succeeded(
            body, json.loads(node["output_contracts_json"])
        )
        with TemporaryDirectory(
            dir=self.artifacts.object_root, prefix=".outputs-"
        ) as staging:
            try:
                staged = self.artifacts.stage_outputs(
                    node, attempt, body.get("outputs"), Path(staging)
                )
            except (FileNotFoundError, ExecutionServiceError):
                with connect_database(self.database) as connection:
                    require_lease(connection, lease_id, body, utc_now())
                raise
            with self.artifacts.object_publication():
                for item in staged:
                    self.artifacts.publish_object(item)
                with connect_database(self.database) as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    require_lease(connection, lease_id, body, utc_now())
                    outputs = []
                    for item in staged:
                        self.artifacts.record_object(connection, item)
                        connection.execute(
                            "INSERT OR IGNORE INTO job_object_pins VALUES (?, ?, ?)",
                            (
                                attempt["job_id"],
                                item["checksum"],
                                f"node-output:{attempt['node_id']}:{item['port']}",
                            ),
                        )
                        outputs.append(
                            {
                                key: item[key]
                                for key in (
                                    "port",
                                    "checksum",
                                    "size_bytes",
                                    "media_type",
                                    "format",
                                )
                            }
                        )
                    now = utc_now()
                    connection.execute(
                        """UPDATE node_attempts SET status = 'SUCCEEDED', stdout_tail = ?,
                               stderr_tail = ?, finished_at = ? WHERE lease_id = ?""",
                        (
                            outcome.stdout_tail,
                            outcome.stderr_tail,
                            now,
                            lease_id,
                        ),
                    )
                    outputs_json = canonical_json(outputs).decode("utf-8")
                    remote_state_json = canonical_json(
                        body.get("remote_state")
                        or json.loads(node["remote_state_json"])
                    ).decode("utf-8")
                    connection.execute(
                        """UPDATE job_nodes SET status = 'SUCCEEDED', outputs_json = ?,
                               values_json = ?, remote_state_json = ?, finished_at = ?
                               WHERE job_id = ? AND node_id = ?""",
                        (
                            outputs_json,
                            outcome.values_json,
                            remote_state_json,
                            now,
                            attempt["job_id"],
                            attempt["node_id"],
                        ),
                    )
                    self._advance(
                        connection,
                        attempt["job_id"],
                        TaskSucceeded(attempt["node_id"]),
                    )

    def fail(self, lease_id: str, body: dict) -> None:
        outcome = TaskOutcome.failed(body)
        with connect_database(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            attempt = require_lease(connection, lease_id, body, utc_now())
            now = utc_now()
            connection.execute(
                """UPDATE node_attempts SET status = 'FAILED', error_code = ?,
                       stdout_tail = ?, stderr_tail = ?, finished_at = ? WHERE lease_id = ?""",
                (
                    outcome.error_code,
                    outcome.stdout_tail,
                    outcome.stderr_tail,
                    now,
                    lease_id,
                ),
            )
            self._fail_node(
                connection,
                attempt["job_id"],
                attempt["node_id"],
                outcome.error_code,
            )
            if body.get("remote_state") is not None:
                connection.execute(
                    "UPDATE job_nodes SET remote_state_json=? WHERE job_id=? AND node_id=?",
                    (
                        canonical_json(body["remote_state"]).decode("utf-8"),
                        attempt["job_id"],
                        attempt["node_id"],
                    ),
                )
