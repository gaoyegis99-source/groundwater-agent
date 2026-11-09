"""Isolated SQLite state-layout experiment; not a second V6 runtime.

The same deterministic, tool-like transition stream is committed either as a
complete JSON state or as changed cells.  SQLite supplies the transaction and
CAS semantics in both cases.  This experiment deliberately does not claim to
measure the current workflow coordinator or to solve its change-set capture.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import platform
import sqlite3
import statistics
import sys
import tempfile
import time
from typing import Any, Iterable, Literal


Section = Literal["steps", "actions", "outputs"]


@dataclass(frozen=True)
class Mutation:
    section: Section
    key: str
    value: dict[str, object]


@dataclass(frozen=True)
class Transition:
    event_type: str
    changes: tuple[Mutation, ...]


class StaleRevisionError(RuntimeError):
    pass


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _transitions(count: int) -> Iterable[Transition]:
    for ordinal in range(count):
        step = f"tool-{ordinal:04d}"
        action = f"action-{ordinal:04d}"
        yield Transition("STEP_STARTED", (Mutation("steps", step, {"status": "RUNNING"}),))
        yield Transition(
            "ACTION_PROPOSED",
            (Mutation("actions", action, {"status": "PROPOSED", "step_id": step}),),
        )
        yield Transition(
            "ACTION_DISPATCHED",
            (Mutation("actions", action, {"status": "DISPATCHED", "step_id": step}),),
        )
        result = {"value": ordinal}
        yield Transition(
            "ACTION_SUCCEEDED",
            (
                Mutation(
                    "actions", action,
                    {"status": "SUCCEEDED", "step_id": step, "output": result},
                ),
            ),
        )
        yield Transition(
            "STEP_SUCCEEDED",
            (
                Mutation("steps", step, {"status": "SUCCEEDED"}),
                Mutation("outputs", step, result),
            ),
        )


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
        "state_json TEXT)"
    )
    connection.execute(
        "CREATE TABLE events (run_id TEXT NOT NULL, sequence INTEGER NOT NULL, "
        "event_type TEXT NOT NULL, PRIMARY KEY (run_id, sequence))"
    )
    connection.execute(
        "CREATE TABLE cells (run_id TEXT NOT NULL, section TEXT NOT NULL, "
        "cell_key TEXT NOT NULL, value_json TEXT NOT NULL, "
        "PRIMARY KEY (run_id, section, cell_key))"
    )
    return connection


class Store:
    def __init__(self, connection: sqlite3.Connection, *, normalized: bool) -> None:
        self.connection = connection
        self.normalized = normalized
        self.state: dict[str, dict[str, dict[str, object]]] = {
            "steps": {}, "actions": {}, "outputs": {},
        }
        self.revision = 1
        self.payload_bytes = 0
        self.connection.execute(
            "INSERT INTO runs(run_id, revision, state_json) VALUES ('run', 1, ?)",
            (None if normalized else _json(self.state),),
        )
        self.connection.execute(
            "INSERT INTO events(run_id, sequence, event_type) VALUES ('run', 1, 'CREATED')"
        )

    def commit(
        self, transition: Transition, *, expected_revision: int,
        fail_after_state_write: bool = False,
    ) -> None:
        # A fresh local copy ensures a fault never makes uncommitted state
        # visible to the next operation. It is needed only by the full-row
        # design; the normalized design reads cells from SQLite on demand.
        next_state = (
            json.loads(_json(self.state)) if not self.normalized else None
        )
        if next_state is not None:
            for change in transition.changes:
                next_state[change.section][change.key] = change.value
        connection = self.connection
        committed_payload_bytes = 0
        connection.execute("BEGIN IMMEDIATE")
        try:
            changed = connection.execute(
                "UPDATE runs SET revision=? WHERE run_id='run' AND revision=?",
                (expected_revision + 1, expected_revision),
            )
            if changed.rowcount != 1:
                raise StaleRevisionError("revision CAS rejected stale transition")
            if self.normalized:
                for change in transition.changes:
                    serialized = _json(change.value)
                    connection.execute(
                        "INSERT INTO cells(run_id, section, cell_key, value_json) "
                        "VALUES ('run', ?, ?, ?) ON CONFLICT(run_id, section, cell_key) "
                        "DO UPDATE SET value_json=excluded.value_json",
                        (change.section, change.key, serialized),
                    )
                    committed_payload_bytes += len(serialized.encode("utf-8"))
            else:
                serialized = _json(next_state)
                connection.execute(
                    "UPDATE runs SET state_json=? WHERE run_id='run'",
                    (serialized,),
                )
                committed_payload_bytes += len(serialized.encode("utf-8"))
            if fail_after_state_write:
                raise RuntimeError("injected fault before event insert")
            connection.execute(
                "INSERT INTO events(run_id, sequence, event_type) VALUES ('run', ?, ?)",
                (expected_revision + 1, transition.event_type),
            )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        self.revision = expected_revision + 1
        self.payload_bytes += committed_payload_bytes
        if next_state is not None:
            self.state = next_state

    def read(self) -> dict[str, dict[str, dict[str, object]]]:
        if not self.normalized:
            row = self.connection.execute(
                "SELECT state_json FROM runs WHERE run_id='run'"
            ).fetchone()
            return json.loads(row[0])
        state: dict[str, dict[str, dict[str, object]]] = {
            "steps": {}, "actions": {}, "outputs": {},
        }
        for section, key, serialized in self.connection.execute(
            "SELECT section, cell_key, value_json FROM cells WHERE run_id='run'"
        ):
            state[section][key] = json.loads(serialized)
        return state


def _database_bytes(path: Path) -> int:
    return sum(
        item.stat().st_size
        for item in (path, path.with_name(path.name + "-wal"))
        if item.exists()
    )


def _safety_checks() -> dict[str, bool]:
    with tempfile.TemporaryDirectory(prefix="v6-state-layout-safety-") as raw:
        path = Path(raw) / "runs.sqlite"
        connection = _connect(path)
        try:
            store = Store(connection, normalized=True)
            dispatched = Transition(
                "ACTION_DISPATCHED",
                (Mutation("actions", "a", {"status": "DISPATCHED"}),),
            )
            store.commit(dispatched, expected_revision=1)
            before = store.read()
            try:
                store.commit(
                    Transition(
                        "ACTION_SUCCEEDED",
                        (Mutation("actions", "a", {"status": "SUCCEEDED"}),),
                    ),
                    expected_revision=2,
                    fail_after_state_write=True,
                )
            except RuntimeError as error:
                assert str(error) == "injected fault before event insert"
            else:
                raise AssertionError("fault injection did not stop the commit")
            assert store.read() == before
            assert connection.execute("SELECT revision FROM runs").fetchone()[0] == 2
            assert connection.execute("SELECT count(*) FROM events").fetchone()[0] == 2
            try:
                store.commit(dispatched, expected_revision=1)
            except StaleRevisionError:
                pass
            else:
                raise AssertionError("stale CAS unexpectedly succeeded")
        finally:
            connection.close()
        reopened = sqlite3.connect(path)
        try:
            durable = reopened.execute(
                "SELECT value_json FROM cells WHERE section='actions' AND cell_key='a'"
            ).fetchone()
            assert json.loads(durable[0]) == {"status": "DISPATCHED"}
        finally:
            reopened.close()
    return {
        "state_and_event_rollback_atomic": True,
        "revision_cas_rejects_stale": True,
        "dispatched_survives_reopen_as_unknown_outcome": True,
    }


def benchmark(sizes: tuple[int, ...], samples: int) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for count in sizes:
        expected: dict[str, dict[str, dict[str, object]]] = {
            "steps": {}, "actions": {}, "outputs": {},
        }
        transitions = tuple(_transitions(count))
        for transition in transitions:
            for change in transition.changes:
                expected[change.section][change.key] = change.value
        for normalized in (False, True):
            elapsed: list[float] = []
            hydration: list[float] = []
            payloads: list[int] = []
            database_sizes: list[int] = []
            for _ in range(samples):
                with tempfile.TemporaryDirectory(prefix="v6-state-layout-") as raw:
                    path = Path(raw) / "runs.sqlite"
                    connection = _connect(path)
                    try:
                        store = Store(connection, normalized=normalized)
                        start = time.perf_counter_ns()
                        for transition in transitions:
                            store.commit(transition, expected_revision=store.revision)
                        elapsed.append((time.perf_counter_ns() - start) / 1_000_000)
                        start = time.perf_counter_ns()
                        actual = store.read()
                        hydration.append((time.perf_counter_ns() - start) / 1_000_000)
                        if actual != expected:
                            raise AssertionError("state layouts produced different results")
                        if store.revision != len(transitions) + 1:
                            raise AssertionError("revision count is incorrect")
                        events = connection.execute("SELECT count(*) FROM events").fetchone()[0]
                        if events != store.revision:
                            raise AssertionError("state and event counts diverged")
                        payloads.append(store.payload_bytes)
                        database_sizes.append(_database_bytes(path))
                    finally:
                        connection.close()
            rows.append({
                "steps": count,
                "layout": "normalized_cells" if normalized else "full_snapshot",
                "transitions": len(transitions),
                "commit_total_ms_p50": round(statistics.median(elapsed), 3),
                "commit_total_ms_observed_max": round(max(elapsed), 3),
                "hydrate_ms_p50": round(statistics.median(hydration), 3),
                "state_payload_bytes": payloads[0],
                "sqlite_plus_wal_bytes_p50": round(statistics.median(database_sizes)),
            })
    return {
        "schema_version": "v6-state-layout-experiment-v1",
        "environment": {
            "python": sys.version.split()[0],
            "sqlite": sqlite3.sqlite_version,
            "platform": platform.platform(),
            "journal_mode": "WAL",
            "synchronous": "FULL",
            "wal_autocheckpoint": 0,
        },
        "scope": "Synthetic tool transitions; SQLite FULL sync, WAL, one event per CAS. "
        "Measures storage layout only, not production coordinator mutation capture, "
        "validation, value envelopes, approvals, model ledger, or end-to-end latency.",
        "safety_checks": _safety_checks(),
        "samples": samples,
        "results": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default="10,40,100,500")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        sizes = tuple(int(raw) for raw in args.sizes.split(","))
    except ValueError as error:
        parser.error(f"invalid size: {error}")
    if not sizes or any(size < 1 or size > 500 for size in sizes) or args.samples < 1:
        parser.error("sizes must be 1..500 and samples must be positive")
    report = benchmark(sizes, args.samples)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
