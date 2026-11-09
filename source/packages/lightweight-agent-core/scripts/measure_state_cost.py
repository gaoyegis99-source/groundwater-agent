"""Count internal definition/state work; timings are observations, not gates."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import runpy
import sqlite3
import tempfile
import time
from typing import Any, Callable

from platform_lightweight_agent import (
    ApprovalDecision,
    InMemoryRunJournal,
    LocalToolExecutor,
    NodeContract,
    SQLiteRunJournal,
    ValueBinding,
    WorkflowEngine,
    approval_step,
    workflow_v4,
)
import platform_lightweight_agent.journal as journal_module


def _sizes(value: str) -> tuple[int, ...]:
    try:
        sizes = tuple(int(item) for item in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "sizes must be comma-separated integers"
        ) from error
    if (
        not sizes
        or any(size < 1 or size > 500 for size in sizes)
        or tuple(sorted(set(sizes))) != sizes
    ):
        raise argparse.ArgumentTypeError(
            "sizes must be increasing, distinct integers from 1 to 500"
        )
    return sizes


def _timed(method: Callable, durations: list[float]) -> Callable:
    def call(*args, **kwargs):
        started = time.perf_counter_ns()
        try:
            return method(*args, **kwargs)
        finally:
            durations.append((time.perf_counter_ns() - started) / 1_000_000)

    return call


def _resumable_definition(fixture: dict[str, Any], count: int):
    base = fixture["_definition"](count, "state-cost-resume")
    empty = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    approved = {
        **empty,
        "properties": {"approved": {"type": "boolean"}},
        "required": ["approved"],
    }
    combined = {
        **empty,
        "properties": {"value": {"type": "integer"}, "approved": {"type": "boolean"}},
        "required": ["value", "approved"],
    }
    return workflow_v4(
        base.workflow_id,
        base.version,
        (
            *base.steps,
            approval_step(
                "review",
                "Approve the completed local tools?",
                contract=NodeContract(empty, approved, {}),
            ),
        ),
        input_schema=base.input_schema,
        output_schema=combined,
        output_bindings={
            "/value": ValueBinding("step_output", "/value", base.steps[-1].step_id),
            "/approved": ValueBinding("step_output", "/approved", "review"),
        },
    )


def _instrument_sqlite(journal: SQLiteRunJournal, counters: Counter[str]) -> None:
    """Observe the actual SQLite write statements without changing the journal."""

    class TimedConnection(sqlite3.Connection):
        transaction_started_ns: int | None = None

        def execute(self, sql, parameters=(), /):  # type: ignore[no-untyped-def]
            command = sql.strip().upper()
            started = time.perf_counter_ns()
            result = super().execute(sql, parameters)
            elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
            if command == "BEGIN IMMEDIATE":
                self.transaction_started_ns = started
            elif command == "COMMIT" and self.transaction_started_ns is not None:
                counters["sqlite_transaction_ms"] += (
                    time.perf_counter_ns() - self.transaction_started_ns
                ) / 1_000_000
                counters["sqlite_commit_ms"] += elapsed_ms
                counters["sqlite_transactions"] += 1
                self.transaction_started_ns = None
            elif command.startswith("INSERT INTO LIGHTWEIGHT_AGENT_RUNS"):
                counters["sqlite_snapshot_write_bytes"] += len(
                    parameters[-2].encode("utf-8")
                )
            elif command.startswith("UPDATE LIGHTWEIGHT_AGENT_RUNS SET"):
                counters["sqlite_snapshot_write_bytes"] += len(
                    parameters[-4].encode("utf-8")
                )
            elif command.startswith("INSERT INTO LIGHTWEIGHT_AGENT_STATE_CELLS"):
                counters["sqlite_cell_write_count"] += 1
                counters["sqlite_cell_write_bytes"] += len(
                    parameters[1].encode("utf-8")
                ) + len((parameters[2] or parameters[3]).encode("utf-8"))
            elif command.startswith("INSERT OR IGNORE INTO LIGHTWEIGHT_AGENT_VALUES"):
                counters["sqlite_value_write_attempt_bytes"] += len(
                    parameters[1].encode("utf-8")
                )
            elif command.startswith("DELETE FROM LIGHTWEIGHT_AGENT_STATE_CELLS"):
                if "AND PATH_JSON" not in command:
                    counters["sqlite_cell_full_rewrites"] += 1
            return result

        def executemany(self, sql, parameters, /):  # type: ignore[no-untyped-def]
            if sql.startswith("INSERT OR IGNORE INTO lightweight_agent_values"):
                parameters = tuple(parameters)
                counters["sqlite_value_write_attempt_bytes"] += sum(
                    len(item[1].encode("utf-8")) for item in parameters
                )
            return super().executemany(sql, parameters)

    def connect() -> sqlite3.Connection:
        connection = sqlite3.connect(
            journal.path, isolation_level=None, factory=TimedConnection
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    journal._connect = connect


def measure(
    *,
    sizes: tuple[int, ...] = (1, 10, 40, 100),
    storages: tuple[str, ...] = ("memory", "sqlite"),
):
    fixture = runpy.run_path(str(Path(__file__).with_name("measure_v3_v4_overhead.py")))
    rows = []
    original_decode = journal_module._definition_object
    original_validate = journal_module._validate_checkpoint_object
    for storage in storages:
        previous: tuple[int, float] | None = None
        for count in sizes:
            counters = Counter(definition_decodes=0, definition_decoded_bytes=0)

            def decode(serialized, name):
                counters["definition_decodes"] += 1
                counters["definition_decoded_bytes"] += len(serialized.encode("utf-8"))
                return original_decode(serialized, name)

            def validate(value, name):
                decoded, serialized = original_validate(value, name)
                counters[name + "_validations"] += 1
                counters[name + "_validated_bytes"] += len(serialized.encode("utf-8"))
                return decoded, serialized

            journal_module._definition_object = decode
            journal_module._validate_checkpoint_object = validate
            try:
                with tempfile.TemporaryDirectory(prefix="v6-state-cost-") as raw:
                    journal = (
                        InMemoryRunJournal()
                        if storage == "memory"
                        else SQLiteRunJournal(Path(raw) / "runs.sqlite")
                    )
                    if isinstance(journal, SQLiteRunJournal):
                        _instrument_sqlite(journal, counters)
                    create_ms: list[float] = []
                    commit_ms: list[float] = []
                    cell_commit_ms: list[float] = []
                    journal.create = _timed(journal.create, create_ms)
                    journal.replace = _timed(journal.replace, commit_ms)
                    journal._replace_cell_writes = _timed(
                        journal._replace_cell_writes, cell_commit_ms
                    )
                    tool_calls: list[int] = []

                    def local_tool(arguments):  # type: ignore[no-untyped-def]
                        tool_calls.append(1)
                        return {"value": arguments["value"]}

                    def executor() -> LocalToolExecutor:
                        local = LocalToolExecutor()
                        local.register(
                            fixture["_TOOL"],
                            local_tool,
                            implementation_version="state-cost-tool-v1",
                        )
                        return local

                    engine = WorkflowEngine(journal, executor(), {})
                    started = time.perf_counter()
                    waiting = engine.run(
                        _resumable_definition(fixture, count), {"value": count}
                    )
                    elapsed = round((time.perf_counter() - started) * 1000, 3)
                    if waiting.status != "WAITING_APPROVAL" or len(tool_calls) != count:
                        raise AssertionError(
                            "resumable benchmark did not reach approval"
                        )
                    run_commits = len(commit_ms) + len(cell_commit_ms)
                    run_state_bytes = counters["state_validated_bytes"]
                    run_snapshot_bytes = counters["sqlite_snapshot_write_bytes"]
                    run_cell_bytes = counters["sqlite_cell_write_bytes"]
                    run_transaction_ms = counters["sqlite_transaction_ms"]
                    started = time.perf_counter_ns()
                    hydrated = journal.get(waiting.run_id)
                    hydrate_ms = (time.perf_counter_ns() - started) / 1_000_000
                    if hydrated.revision != waiting.revision:
                        raise AssertionError("journal hydration changed the revision")
                    started = time.perf_counter_ns()
                    restarted_journal = (
                        journal
                        if storage == "memory"
                        else SQLiteRunJournal(journal.path)
                    )
                    reopen_ms = (time.perf_counter_ns() - started) / 1_000_000
                    if (
                        isinstance(restarted_journal, SQLiteRunJournal)
                        and restarted_journal is not journal
                    ):
                        _instrument_sqlite(restarted_journal, counters)
                        restarted_journal.replace = _timed(
                            restarted_journal.replace, commit_ms
                        )
                        restarted_journal._replace_cell_writes = _timed(
                            restarted_journal._replace_cell_writes, cell_commit_ms
                        )
                    approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))
                    started = time.perf_counter_ns()
                    result = WorkflowEngine(restarted_journal, executor(), {}).resume(
                        waiting.handle,
                        approvals=(ApprovalDecision(approval_id, True),),
                    )
                    resume_ms = (time.perf_counter_ns() - started) / 1_000_000
                    if (
                        result.status != "SUCCEEDED"
                        or result.state["v3"]["output"]
                        != {"value": count, "approved": True}
                        or len(tool_calls) != count
                    ):
                        raise AssertionError("completed local tools replayed on resume")
                    database_bytes = None
                    if storage == "sqlite":
                        database_bytes = sum(
                            path.stat().st_size
                            for path in (
                                journal.path,
                                journal.path.with_name(journal.path.name + "-wal"),
                            )
                            if path.exists()
                        )
                    row = {
                        "storage": storage,
                        "steps": count,
                        "revision": result.revision,
                        "status": result.status,
                        **dict(counters),
                        "instrumented_elapsed_ms": elapsed,
                        "journal_create_ms": round(create_ms[0], 3),
                        "journal_commit_count": len(commit_ms) + len(cell_commit_ms),
                        "cell_delta_commit_count": len(cell_commit_ms),
                        "run_commit_count": run_commits,
                        "resume_commit_count": (
                            len(commit_ms) + len(cell_commit_ms) - run_commits
                        ),
                        "journal_commit_total_ms": round(
                            sum(commit_ms) + sum(cell_commit_ms), 3
                        ),
                        "journal_commit_max_ms": round(
                            max((*commit_ms, *cell_commit_ms), default=0), 3
                        ),
                        "snapshot_hydrate_ms": round(hydrate_ms, 3),
                        "journal_reopen_ms": (
                            round(reopen_ms, 3) if storage == "sqlite" else None
                        ),
                        "approval_resume_ms": round(resume_ms, 3),
                        "completed_tool_replays": len(tool_calls) - count,
                        "run_state_validated_bytes": run_state_bytes,
                        "resume_state_validated_bytes": (
                            counters["state_validated_bytes"] - run_state_bytes
                        ),
                        "sqlite_run_snapshot_write_bytes": run_snapshot_bytes,
                        "sqlite_resume_snapshot_write_bytes": (
                            counters["sqlite_snapshot_write_bytes"] - run_snapshot_bytes
                        ),
                        "sqlite_run_cell_write_bytes": run_cell_bytes,
                        "sqlite_resume_cell_write_bytes": (
                            counters["sqlite_cell_write_bytes"] - run_cell_bytes
                        ),
                        "sqlite_cell_write_count": counters["sqlite_cell_write_count"],
                        "sqlite_cell_full_rewrites": counters[
                            "sqlite_cell_full_rewrites"
                        ],
                        "sqlite_run_transaction_ms": round(run_transaction_ms, 3),
                        "sqlite_resume_transaction_ms": round(
                            counters["sqlite_transaction_ms"] - run_transaction_ms,
                            3,
                        ),
                        "sqlite_commit_ms": round(counters["sqlite_commit_ms"], 3),
                        "sqlite_value_write_attempt_bytes": counters[
                            "sqlite_value_write_attempt_bytes"
                        ],
                        "sqlite_database_and_wal_bytes": database_bytes,
                    }
                    if previous is not None:
                        previous_count, previous_elapsed = previous
                        row["incremental_run_ms_per_step"] = round(
                            (elapsed - previous_elapsed) / (count - previous_count),
                            3,
                        )
                    rows.append(row)
                    previous = (count, elapsed)
            finally:
                journal_module._definition_object = original_decode
                journal_module._validate_checkpoint_object = original_validate
    return {
        "schema_version": "v6-state-cost-v4",
        "scope": (
            "one instrumented run per size stops at approval, then a fresh engine "
            "continues it; SQLite also reopens its journal. Commit timing includes "
            "serialization and transaction; snapshot and cell write bytes count "
            "attempted row payloads, not physical WAL bytes. Incremental run cost is "
            "the difference from the preceding step count; not a latency release gate"
        ),
        "results": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sizes", type=_sizes, default=(1, 10, 40, 100))
    parser.add_argument(
        "--storage", choices=("memory", "sqlite", "both"), default="both"
    )
    args = parser.parse_args()
    report = measure(
        sizes=args.sizes,
        storages=("memory", "sqlite") if args.storage == "both" else (args.storage,),
    )
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
