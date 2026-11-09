"""Hold one SQLite execution lease while another process takes over."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

import anyio
from platform_lightweight_agent import (
    SQLiteRunJournal,
    WorkflowEngine,
)
from platform_lightweight_agent.journal import RunConflictError
import platform_lightweight_agent.workflow_v3 as workflow_v3


class _NeverExecutor:
    def execute(self, request: object) -> None:
        raise AssertionError(f"unexpected tool dispatch: {request!r}")

    def reconcile(self, request: object) -> None:
        raise AssertionError(f"unexpected tool reconciliation: {request!r}")


def _write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    os.replace(temporary, path)


async def _run(args: argparse.Namespace) -> None:
    # Only this isolated child changes its lease. The parent claims normally.
    workflow_v3._EXECUTION_LEASE_SECONDS = args.lease_seconds
    journal = SQLiteRunJournal(args.journal)
    engine = WorkflowEngine(journal, _NeverExecutor(), {})
    claimed, owner_id, generation = await workflow_v3._claim_execution(
        engine, journal.get(args.run_id)
    )
    _write_json(
        args.claimed,
        {"pid": os.getpid(), "generation": generation, "revision": claimed.revision},
    )
    deadline = time.monotonic() + 10
    while not args.release.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("lease fencing probe was not released")
        await anyio.sleep(0.01)
    coordinator = workflow_v3._Coordinator(
        engine, claimed, owner_id=owner_id, generation=generation
    )
    try:
        await coordinator.commit("STALE_OWNER_WRITE", {"generation": generation})
    except RunConflictError:
        outcome = "fenced"
    else:
        outcome = "accepted"
    _write_json(args.output, {"pid": os.getpid(), "outcome": outcome})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--claimed", type=Path, required=True)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lease-seconds", type=float, default=0.25)
    anyio.run(_run, parser.parse_args())


if __name__ == "__main__":
    main()
