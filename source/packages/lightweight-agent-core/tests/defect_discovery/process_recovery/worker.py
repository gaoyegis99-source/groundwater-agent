"""Single V6 worker used by the real-process crash-window probe."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
from typing import Any

import anyio
from platform_lightweight_agent import (
    NodeContract,
    RemoteHttpTool,
    RemoteHttpToolExecutor,
    SQLiteRunJournal,
    ToolRecoveryCapabilities,
    ToolReference,
    ValueBinding,
    WorkflowEngine,
    tool_step,
    workflow,
)
import platform_lightweight_agent.workflow_v3 as workflow_v3


TOOL = ToolReference("probe.external-effect", "1", "sha256:" + "a" * 64)
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


WINDOWS: dict[str, tuple[str, str]] = {
    "before_action_ready": ("ACTION_READY", "before"),
    "after_action_ready": ("ACTION_READY", "after"),
    "before_action_dispatched": ("ACTION_DISPATCHED", "before"),
    "after_action_dispatched": ("ACTION_DISPATCHED", "after"),
    "before_action_resolved": ("ACTION_RESOLVED", "before"),
    "after_action_resolved": ("ACTION_RESOLVED", "after"),
    "after_step_completed": ("STEP_COMPLETED", "after"),
    "after_run_succeeded": ("RUN_SUCCEEDED", "after"),
}


class BarrierJournal:
    """Pause exactly around one real SQLite transition without changing it."""

    def __init__(self, inner: SQLiteRunJournal, barrier: Path, window: str) -> None:
        self._inner = inner
        self._barrier = barrier
        self._event, self._phase = WINDOWS[window]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def replace(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        event_type = kwargs.get("event_type")
        if event_type == self._event and self._phase == "before":
            self._wait(event_type)
        result = self._inner.replace(*args, **kwargs)
        if event_type == self._event and self._phase == "after":
            self._wait(event_type)
        return result

    def _wait(self, event_type: object) -> None:
        payload = {
            "pid": os.getpid(),
            "event_type": event_type,
            "phase": self._phase,
            "monotonic_ns": time.monotonic_ns(),
        }
        temporary = self._barrier.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(temporary, self._barrier)
        while True:
            time.sleep(1)


def _spec(*, supports_query: bool, accepts_idempotency: bool):  # type: ignore[no-untyped-def]
    return workflow(
        "process-crash-probe",
        "1",
        (
            tool_step(
                "effect",
                TOOL,
                approval_required=False,
                contract=NodeContract(EMPTY, OUTPUT, {}),
                capabilities=ToolRecoveryCapabilities(
                    external_side_effects=True,
                    accepts_idempotency_key=accepts_idempotency,
                    supports_result_query=supports_query,
                ),
            ),
        ),
        schema_version="v6-light-workflow-v4",
        input_schema=EMPTY,
        output_schema=OUTPUT,
        output_bindings={
            "/value": ValueBinding("step_output", "/value", "effect")
        },
    )


async def _run(args: argparse.Namespace) -> object:
    # Only shorten the process-local lease used by this test.  The persisted
    # owner/generation protocol and every journal transition remain unchanged.
    workflow_v3._EXECUTION_LEASE_SECONDS = args.lease_seconds
    base_journal = SQLiteRunJournal(args.journal)
    journal: object = base_journal
    if args.window in WINDOWS:
        journal = BarrierJournal(base_journal, args.barrier, args.window)
    executor = RemoteHttpToolExecutor()
    executor.register(
        RemoteHttpTool(
            TOOL,
            args.origin + "/execute",
            reconciliation_endpoint=(
                args.origin + "/result" if args.supports_query else None
            ),
            timeout_seconds=20,
        )
    )
    engine = WorkflowEngine(journal, executor, {})  # type: ignore[arg-type]
    spec = _spec(
        supports_query=args.supports_query,
        accepts_idempotency=args.accepts_idempotency,
    )
    if args.operation == "start":
        return await engine.run_async(spec, {}, run_id=args.run_id)
    return await engine.resume_async(args.run_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("start", "resume"))
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--barrier", type=Path, required=True)
    parser.add_argument("--window", default="none")
    parser.add_argument("--origin", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--supports-query", action="store_true")
    parser.add_argument("--accepts-idempotency", action="store_true")
    parser.add_argument("--lease-seconds", type=float, default=0.3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    snapshot = anyio.run(_run, args)
    payload = snapshot.to_mapping()  # type: ignore[union-attr]
    temporary = args.output.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.replace(temporary, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
