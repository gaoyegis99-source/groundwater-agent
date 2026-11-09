"""A killed v4 worker must not replay an externally dispatched action."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

from platform_lightweight_agent import (
    SQLiteRunJournal,
    ToolExecutionResult,
    WorkflowEngine,
)


_WORKER = """
import os
from pathlib import Path
import sqlite3
import sys

from platform_lightweight_agent import (
    NodeContract, SQLiteRunJournal, ToolExecutionResult,
    ToolRecoveryCapabilities, ToolReference, ValueBinding,
    WorkflowEngine, tool_step, workflow_v4,
)
import platform_lightweight_agent.workflow_v3 as workflow_runtime

database, ledger, window = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
workflow_runtime._EXECUTION_LEASE_SECONDS = 0.1
empty = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object", "properties": {}, "additionalProperties": False,
}
output = {
    **empty,
    "properties": {"value": {"type": "integer"}},
    "required": ["value"],
}
reference = ToolReference("cell-crash.effect", "1", "sha256:" + "d" * 64)
spec = workflow_v4(
    "cell-crash", "1",
    (
        tool_step(
            "effect", reference, approval_required=False,
            contract=NodeContract(empty, output),
            capabilities=ToolRecoveryCapabilities(
                external_side_effects=True, supports_result_query=False,
            ),
        ),
    ),
    input_schema=empty, output_schema=output,
    output_bindings={
        "/value": ValueBinding("step_output", "/value", "effect"),
    },
)

class Executor:
    async def execute_async(self, request, cancellation):
        del cancellation
        with sqlite3.connect(ledger) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS effects "
                "(id INTEGER PRIMARY KEY, action_id TEXT NOT NULL, value INTEGER NOT NULL)"
            )
            connection.execute(
                "INSERT INTO effects(action_id, value) VALUES (?, ?)",
                (request.action_id, 7),
            )
        return ToolExecutionResult(request.action_id, True, {"value": 7})

    async def reconcile_async(self, request):
        raise AssertionError("result query was not declared")

journal = SQLiteRunJournal(database)
original_commit = journal._replace_cell_writes

def fault(head, writes, *, status, event_type, payload):
    if window == "before_result_commit" and event_type == "ACTION_RESOLVED":
        os._exit(73)
    committed = original_commit(
        head, writes, status=status, event_type=event_type, payload=payload
    )
    if window == "after_dispatch" and event_type == "ACTION_DISPATCHED":
        os._exit(74)
    return committed

journal._replace_cell_writes = fault
WorkflowEngine(journal, Executor(), {}).run(spec, {}, run_id="cell-crash-run")
raise AssertionError("worker survived the injected crash window")
"""


class _NoReplayExecutor:
    async def execute_async(self, request: object, cancellation: object) -> object:
        del request, cancellation
        raise AssertionError("dispatched action was replayed")

    async def reconcile_async(self, request: object) -> object:
        del request
        raise AssertionError("undeclared result query was attempted")


@pytest.mark.parametrize(
    ("window", "expected_exit", "expected_effects"),
    (("after_dispatch", 74, 0), ("before_result_commit", 73, 1)),
)
@pytest.mark.parametrize("_attempt", range(3))
def test_v4_cell_commit_preserves_dispatch_barrier_across_process_exit(
    tmp_path: Path,
    window: str,
    expected_exit: int,
    expected_effects: int,
    _attempt: int,
) -> None:
    database = tmp_path / "runs.sqlite"
    ledger = tmp_path / "effects.sqlite"
    with sqlite3.connect(ledger) as connection:
        connection.execute(
            "CREATE TABLE effects "
            "(id INTEGER PRIMARY KEY, action_id TEXT NOT NULL, value INTEGER NOT NULL)"
        )
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    worker = subprocess.run(
        [sys.executable, "-c", _WORKER, str(database), str(ledger), window],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert worker.returncode == expected_exit, worker.stderr

    journal = SQLiteRunJournal(database)
    crashed = journal.get("cell-crash-run")
    action_id, action = next(iter(crashed.state["actions"].items()))
    assert action["status"] == "DISPATCHED"
    assert [event.type for event in journal.events(crashed.run_id)].count(
        "ACTION_DISPATCHED"
    ) == 1
    with sqlite3.connect(ledger) as connection:
        count = connection.execute("SELECT count(*) FROM effects").fetchone()[0]
    assert count == expected_effects

    # The former process's short persisted lease must expire before a new
    # owner can claim it; the deadline comes from the journal, not a sleep
    # chosen to guess when an event happened.
    lease = journal.control(crashed.run_id).execution_lease_expires
    assert lease is not None
    deadline = time.monotonic() + 3.0
    while time.time() < lease:
        assert time.monotonic() < deadline
        time.sleep(0.01)

    restarted = WorkflowEngine(journal, _NoReplayExecutor(), {})
    unknown = restarted.resume(crashed.handle)
    assert unknown.status == "RECONCILING"
    assert unknown.state["actions"][action_id]["status"] == "RECONCILING"
    with sqlite3.connect(ledger) as connection:
        assert connection.execute("SELECT count(*) FROM effects").fetchone()[0] == count

    if expected_effects:
        confirmed = restarted.reconcile(
            crashed.handle,
            action_id,
            ToolExecutionResult(action_id, True, {"value": 7}),
        )
        assert confirmed.status == "SUCCEEDED"
        assert confirmed.state["v3"]["output"] == {"value": 7}
        assert SQLiteRunJournal(database).get(crashed.run_id).to_mapping() == (
            confirmed.to_mapping()
        )
