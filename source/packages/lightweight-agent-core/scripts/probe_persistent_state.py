"""Compare persistent in-memory cells with real v4 editor transitions.

This is a development-only feasibility probe, not another run journal. The
workflow still executes through its normal InMemoryRunJournal; capture and
full-state verification are excluded from the replay timing. Run with::

    uv run --locked --extra workflow --group development \
      python scripts/probe_persistent_state.py \
      --sizes 10,40,100,500 --iterations 50

The pinned open-source ``immutables.Map`` supplies structural sharing. Values
are canonical JSON strings, so a retained Map cannot be changed by mutating a
captured Python object. No SDK, external tool, or network service is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from platform_lightweight_agent import (
    InMemoryRunJournal,
    LocalToolExecutor,
    WorkflowEngine,
)
from platform_lightweight_agent.journal import RunSnapshot, _state_cells
from platform_lightweight_agent.persistent_state import PersistentCellState
from platform_lightweight_agent.spec import canonical_json
from platform_lightweight_agent.state_edits import StateWriteSet

from measure_v3_v4_overhead import _TOOL, _definition


def _digest(cells: Mapping[str, str]) -> str:
    return hashlib.sha256(canonical_json(cells).encode("utf-8")).hexdigest()


class _CaptureJournal(InMemoryRunJournal):
    def __init__(self) -> None:
        super().__init__()
        self.initial_state: dict[str, Any] | None = None
        self.transitions: list[
            tuple[StateWriteSet, tuple[tuple[str, str | None], ...], str]
        ] = []

    def create(self, snapshot, event_type, payload):  # type: ignore[no-untyped-def]
        result = super().create(snapshot, event_type, payload)
        self.initial_state = result._state_copy()
        return result

    def replace(
        self, snapshot: RunSnapshot, *, expected_revision, event_type, payload, **kwargs
    ):  # type: ignore[no-untyped-def]
        before = self.get(snapshot.run_id)
        write_set = snapshot._state_write_set
        if write_set is None or not write_set._from_editor:
            raise AssertionError("persistent-state probe needs real editor writes")
        after_state = snapshot._state_copy()
        before_cells = _state_cells(before._state_copy())
        after_cells = _state_cells(after_state)
        updates = tuple(
            (path, after_cells.get(path))
            for path in sorted(before_cells.keys() | after_cells.keys())
            if before_cells.get(path) != after_cells.get(path)
        )
        expected = _digest(after_cells)
        result = super().replace(
            snapshot,
            expected_revision=expected_revision,
            event_type=event_type,
            payload=payload,
            **kwargs,
        )
        self.transitions.append((write_set, updates, expected))
        return result


def _apply_persistent(
    initial: Mapping[str, Any],
    transitions: list[tuple[StateWriteSet, tuple[tuple[str, str | None], ...], str]],
    *,
    verify: bool,
) -> PersistentCellState:
    cells = PersistentCellState.from_state(initial)
    retained = [(cells, _digest(dict(cells._cells)))] if verify else []
    for write_set, _, expected in transitions:
        projected = cells.apply_write_set(write_set)
        if projected is None:
            raise AssertionError("real editor produced a nonlocal state write")
        cells = projected
        if verify and _digest(dict(cells._cells)) != expected:
            raise AssertionError("persistent cells diverged from a durable revision")
        if verify:
            retained.append((cells, expected))
    for old_cells, expected in retained:
        if _digest(dict(old_cells._cells)) != expected:
            raise AssertionError("a prior immutable cell version was modified")
    return cells


def _apply_dict_copies(
    initial: Mapping[str, str],
    transitions: list[tuple[StateWriteSet, tuple[tuple[str, str | None], ...], str]],
) -> dict[str, str]:
    cells = dict(initial)
    for _, updates, _ in transitions:
        cells = cells.copy()
        for path, serialized in updates:
            if serialized is None:
                cells.pop(path, None)
            else:
                cells[path] = serialized
    return cells


def _timed_replay(operation, iterations: int) -> float:  # type: ignore[no-untyped-def]
    start = time.perf_counter_ns()
    for _ in range(iterations):
        operation()
    return round((time.perf_counter_ns() - start) / 1_000_000 / iterations, 3)


def measure(steps: int, *, iterations: int = 50) -> dict[str, Any]:
    if steps < 1 or steps > 500 or iterations < 1 or iterations > 500:
        raise ValueError("probe size or iteration count is outside its bound")
    journal = _CaptureJournal()
    executor = LocalToolExecutor()
    executor.register(_TOOL, lambda arguments: {"value": arguments["value"]})
    definition = _definition(steps, f"persistent-probe-{steps}")
    capture_start = time.perf_counter_ns()
    result = WorkflowEngine(journal, executor, {}).run(
        definition, {"value": steps}, run_id=f"persistent-probe-{steps}"
    )
    capture_ms = round((time.perf_counter_ns() - capture_start) / 1_000_000, 3)
    if result.status != "SUCCEEDED" or journal.initial_state is None:
        raise AssertionError("real workflow capture was incomplete")
    initial = journal.initial_state
    initial_cells = _state_cells(initial)
    transitions = journal.transitions
    verified = _apply_persistent(initial, transitions, verify=True)
    plain = _apply_dict_copies(initial_cells, transitions)
    if dict(verified._cells) != plain:
        raise AssertionError("persistent and plain cells differ")
    return {
        "steps": steps,
        "revisions_verified": len(transitions),
        "final_cell_count": len(verified._cells),
        "cell_operations": sum(len(updates) for _, updates, _ in transitions),
        "capture_ms": capture_ms,
        "persistent_replay_ms": _timed_replay(
            lambda: _apply_persistent(initial, transitions, verify=False), iterations
        ),
        "dict_copy_replay_ms": _timed_replay(
            lambda: _apply_dict_copies(initial_cells, transitions), iterations
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default="10,40,100,500")
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    sizes = tuple(int(item) for item in arguments.sizes.split(","))
    report = {
        "schema_version": "v6-persistent-state-probe-v2",
        "dependency": "immutables==0.21 ([workflow] extra)",
        "scope": "real v4 editor write sets; replay timing excludes capture and digest verification",
        "iterations": arguments.iterations,
        "results": [measure(size, iterations=arguments.iterations) for size in sizes],
    }
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    if arguments.output is not None:
        arguments.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
