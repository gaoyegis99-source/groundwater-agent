"""Independent V6 worker for mixed approval/cancel/reconcile sequences."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import anyio
from fixture_tools import TOOL, register
from platform_lightweight_agent import (
    FunctionToolExecutor,
    NodeContract,
    SQLiteRunJournal,
    ToolRecoveryCapabilities,
    ValueBinding,
    WorkflowEngine,
    tool_step,
    workflow,
)
import platform_lightweight_agent.workflow_v3 as workflow_v3


EMPTY = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}
VALUE = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"value": {"type": "integer"}},
    "required": ["value"],
    "additionalProperties": False,
}


def _spec():  # type: ignore[no-untyped-def]
    return workflow(
        "mixed-process-recovery",
        "1",
        (
            tool_step(
                "effect",
                TOOL,
                approval_required=True,
                capabilities=ToolRecoveryCapabilities(external_side_effects=True),
                contract=NodeContract(
                    VALUE,
                    VALUE,
                    {"/value": ValueBinding("literal", value=11)},
                ),
            ),
        ),
        schema_version="v6-light-workflow-v4",
        input_schema=EMPTY,
        output_schema=VALUE,
        output_bindings={
            "/value": ValueBinding("step_output", "/value", "effect")
        },
    )


async def _run(args: argparse.Namespace) -> object:
    workflow_v3._EXECUTION_LEASE_SECONDS = 0.3
    executor = FunctionToolExecutor()
    register(executor)
    engine = WorkflowEngine(SQLiteRunJournal(args.journal), executor, {})
    if args.operation == "seed":
        return await engine.run_async(_spec(), {}, run_id=args.run_id)
    return await engine.resume_async(args.run_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("seed", "resume"))
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--barrier", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.environ["V6_MIXED_LEDGER"] = str(args.ledger)
    os.environ["V6_MIXED_BARRIER"] = str(args.barrier)
    snapshot = anyio.run(_run, args)
    temporary = args.output.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(snapshot.to_mapping(), sort_keys=True), encoding="utf-8"
    )
    os.replace(temporary, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
