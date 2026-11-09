"""Process-exit proof for the legacy model dispatch ledger."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from platform_lightweight_agent import (
    LocalToolExecutor,
    SQLiteRunJournal,
    WorkflowEngine,
)


_WORKER = """
import os
from pathlib import Path
import sqlite3
import sys

from platform_lightweight_agent import (
    AgentDecision, LocalToolExecutor, SQLiteRunJournal, WorkflowEngine,
    agent_step, workflow,
)

database, calls, schema = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]

class ExitBeforeReplyJournal(SQLiteRunJournal):
    def replace(self, snapshot, *, expected_revision, event_type, payload,
                expected_execution_id=None):
        if event_type == "AGENT_REPLY_ACCEPTED":
            os._exit(73)
        return super().replace(
            snapshot, expected_revision=expected_revision,
            event_type=event_type, payload=payload,
            expected_execution_id=expected_execution_id,
        )

class Driver:
    driver_version = "kill-window-driver-v1"

    def execution_binding(self):
        return {"driver_version": self.driver_version, "model": "offline-kill-window"}

    def decide(self, context):
        with sqlite3.connect(calls) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS calls (id INTEGER PRIMARY KEY)")
            connection.execute("INSERT INTO calls DEFAULT VALUES")
        return AgentDecision("answer")

engine = WorkflowEngine(
    ExitBeforeReplyJournal(database), LocalToolExecutor(), {"planner": Driver()}
)
spec = workflow(
    "model-kill-window", "1", [agent_step("answer", "planner", tools=())],
    schema_version=schema,
)
engine.run(spec, run_id="model-kill-window")
raise AssertionError("worker unexpectedly survived reply acceptance")
"""


class _NoSecondModelCall:
    driver_version = "kill-window-driver-v1"

    def execution_binding(self) -> dict[str, str]:
        return {"driver_version": self.driver_version, "model": "offline-kill-window"}

    def decide(self, context: object) -> object:
        del context
        raise AssertionError("dispatched model request was replayed")


@pytest.mark.parametrize(
    "schema_version", ("v6-light-workflow-v1", "v6-light-workflow-v2")
)
def test_model_dispatch_survives_process_exit_without_replay(
    tmp_path: Path, schema_version: str
) -> None:
    database = tmp_path / "runs.sqlite"
    calls = tmp_path / "calls.sqlite"
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    worker = subprocess.run(
        [sys.executable, "-c", _WORKER, str(database), str(calls), schema_version],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert worker.returncode == 73, worker.stderr
    with sqlite3.connect(calls) as connection:
        assert connection.execute("SELECT count(*) FROM calls").fetchone()[0] == 1

    journal = SQLiteRunJournal(database)
    crashed = journal.get("model-kill-window")
    invocation = next(
        iter(crashed.state["agents"]["answer"]["model_invocations"].values())
    )
    assert invocation["status"] == "DISPATCHED"
    blocked = WorkflowEngine(
        journal, LocalToolExecutor(), {"planner": _NoSecondModelCall()}
    ).resume(crashed.handle)

    assert blocked.status == "BLOCKED"
    assert blocked.state["failure"]["code"] == "AGENT_USAGE_UNKNOWN"
    assert blocked.state["agents"]["answer"]["usage"]["model_requests_unknown"] is True
    with sqlite3.connect(calls) as connection:
        assert connection.execute("SELECT count(*) FROM calls").fetchone()[0] == 1
