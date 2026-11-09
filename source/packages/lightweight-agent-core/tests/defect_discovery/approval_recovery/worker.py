"""Independent worker for approval-commit process recovery evidence."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

import anyio
from platform_lightweight_agent import (
    ApprovalDecision,
    HostRuntime,
    HostRuntimeLimits,
    NodeContract,
    RuntimeAssembly,
    SQLiteRunJournal,
    ToolExecutionResult,
    ToolRecoveryCapabilities,
    ToolReference,
    ValueBinding,
    WorkflowEngine,
    tool_step,
    workflow,
)


TOOL = ToolReference("probe.approval-recovery", "1", "sha256:" + "e" * 64)
EMPTY = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}
OUTPUT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"value": {"type": "integer"}},
    "required": ["value"],
    "additionalProperties": False,
}


def _spec():  # type: ignore[no-untyped-def]
    return workflow(
        "approval-process-recovery",
        "1",
        (
            tool_step(
                "effect",
                TOOL,
                approval_required=True,
                contract=NodeContract(EMPTY, OUTPUT, {}),
                capabilities=ToolRecoveryCapabilities(external_side_effects=True),
            ),
        ),
        schema_version="v6-light-workflow-v4",
        input_schema=EMPTY,
        output_schema=OUTPUT,
        output_bindings={
            "/value": ValueBinding("step_output", "/value", "effect")
        },
    )


class Executor:
    def __init__(self, ledger: Path) -> None:
        self.ledger = ledger

    def execute(self, request: Any) -> ToolExecutionResult:
        with sqlite3.connect(self.ledger) as connection:
            connection.execute(
                "INSERT INTO calls(action_id) VALUES(?)", (request.action_id,)
            )
            connection.commit()
        return ToolExecutionResult(request.action_id, True, {"value": 1})

    def reconcile(self, request: Any) -> None:
        del request
        return None


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


async def _seed(args: argparse.Namespace) -> None:
    journal = SQLiteRunJournal(args.journal)
    engine = WorkflowEngine(journal, Executor(args.ledger), {})
    waiting = await engine.run_async(_spec(), {}, run_id=args.run_id)
    approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))
    saved = await engine.resume_async(
        waiting.run_id,
        approvals=(ApprovalDecision(approval_id, True),),
        advance=False,
    )
    action_id = next(iter(saved.state["actions"]))
    _atomic_json(
        args.barrier,
        {
            "pid": os.getpid(),
            "run_id": saved.run_id,
            "execution_id": saved.execution_id,
            "approval_id": approval_id,
            "action_id": action_id,
            "revision": saved.revision,
            "decision": saved.state["v3"]["approval_decisions"][approval_id],
            "action_status": saved.state["actions"][action_id]["status"],
        },
    )
    while True:
        await anyio.sleep(1)


async def _recover(args: argparse.Namespace) -> None:
    journal = SQLiteRunJournal(args.journal)
    host = HostRuntime(
        RuntimeAssembly(journal, Executor(args.ledger)),
        limits=HostRuntimeLimits(recovery_scan_interval_seconds=0.01),
    )
    try:
        host.start()
        with anyio.fail_after(10):
            while True:
                snapshot = host.get(args.run_id)
                if snapshot.status in {"SUCCEEDED", "BLOCKED", "CANCELLED"}:
                    break
                await anyio.sleep(0.01)
        _atomic_json(args.output, snapshot.to_mapping())
    finally:
        host.close(mode="wait")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("seed", "recover"))
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--barrier", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if args.operation == "seed":
        anyio.run(_seed, args)
    else:
        anyio.run(_recover, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
