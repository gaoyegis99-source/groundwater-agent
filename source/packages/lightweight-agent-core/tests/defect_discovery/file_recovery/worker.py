"""Real CSV capability worker for the artifact/journal crash boundary."""

from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any, Callable, Mapping

import anyio
from platform_lightweight_agent import (
    ApprovalDecision,
    FileWorkspace,
    LocalToolExecutor,
    SQLiteRunJournal,
    ToolContext,
    ToolExecutionResult,
    ToolReference,
    WorkflowEngine,
    WorkflowSpec,
    compile_domain_pack,
)
import platform_lightweight_agent.workflow_v3 as workflow_v3


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
PACK_ROOT = PACKAGE_ROOT / "examples" / "domain-packs" / "csv-statistics"
WHEEL = next((PACK_ROOT / "artifacts").glob("*.whl"))
LOGICAL_OUTPUT = "reports/csv-statistics.json"


class ResultBarrierJournal:
    """Block before the calculate result's ACTION_RESOLVED transaction."""

    def __init__(self, inner: SQLiteRunJournal, barrier: Path) -> None:
        self._inner = inner
        self._barrier = barrier
        self._resolved = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def replace(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if kwargs.get("event_type") == "ACTION_RESOLVED":
            self._resolved += 1
            if self._resolved == 2:
                temporary = self._barrier.with_suffix(".tmp")
                temporary.write_text(
                    json.dumps(
                        {
                            "pid": os.getpid(),
                            "event_type": "ACTION_RESOLVED",
                            "phase": "before",
                            "occurrence": self._resolved,
                        }
                    ),
                    encoding="utf-8",
                )
                os.replace(temporary, self._barrier)
                while True:
                    time.sleep(1)
        return self._inner.replace(*args, **kwargs)


def _record_call(database: Path, tool_id: str, action_id: str) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO calls(tool_id, action_id) VALUES(?, ?)",
            (tool_id, action_id),
        )
        connection.commit()


def _executor(
    workspace: FileWorkspace, calls: Path
) -> tuple[LocalToolExecutor, Mapping[str, ToolReference], Any]:
    if str(WHEEL) not in sys.path:
        sys.path.insert(0, str(WHEEL))
    capability = importlib.import_module("csv_statistics_capability")
    raw_tools = json.loads((PACK_ROOT / "tools.json").read_text(encoding="utf-8"))
    references = {
        item["tool_id"]: ToolReference.from_mapping(
            {key: item[key] for key in ("tool_id", "version", "package_digest")}
        )
        for item in raw_tools
    }
    executor = LocalToolExecutor(
        context_factory=lambda request, cancellation: ToolContext(
            request.action_id, cancellation, workspace
        )
    )

    def contextual(
        tool_id: str, handler: Callable[[Mapping[str, object], object], object]
    ) -> Callable[[Mapping[str, object], ToolContext], object]:
        def invoke(arguments: Mapping[str, object], context: ToolContext) -> object:
            _record_call(calls, tool_id, context.action_id)
            return handler(arguments, context)

        return invoke

    def plain(
        tool_id: str, handler: Callable[[Mapping[str, object]], object]
    ) -> Callable[[Mapping[str, object]], object]:
        def invoke(arguments: Mapping[str, object]) -> object:
            # The blocked path has no ToolContext by contract, but this probe
            # uses valid input and should never enter it.
            _record_call(calls, tool_id, "no-context")
            return handler(arguments)

        return invoke

    executor.register(
        references["csv-statistics.validate"],
        contextual("csv-statistics.validate", capability.validate),
    )
    executor.register(
        references["csv-statistics.calculate"],
        contextual("csv-statistics.calculate", capability.calculate),
    )
    executor.register(
        references["csv-statistics.blocked"],
        plain("csv-statistics.blocked", capability.blocked),
    )
    return executor, references, capability


def _workflow(root: Path) -> WorkflowSpec:
    compiled_root = root / "compiled"
    if not compiled_root.exists():
        compilation = compile_domain_pack(
            PACK_ROOT, compiled_root, parameter_profile="small"
        )
        project = compilation.projects["analysis"]
    else:
        from platform_lightweight_agent import load_project

        project = load_project(compiled_root / "entries" / "analysis")
    assert project.workflow_path is not None
    return WorkflowSpec.from_mapping(
        json.loads(project.workflow_path.read_text(encoding="utf-8"))
    )


def _input(root: Path) -> dict[str, object]:
    return json.loads((root / "input.json").read_text(encoding="utf-8"))


async def _operate(args: argparse.Namespace) -> object:
    workflow_v3._EXECUTION_LEASE_SECONDS = 0.3
    workspace = FileWorkspace.under(args.root / "workspace")
    executor, _references, _capability = _executor(workspace, args.root / "calls.sqlite")
    base = SQLiteRunJournal(args.root / "runs.sqlite")
    journal: object = base
    if args.operation == "start":
        journal = ResultBarrierJournal(base, args.root / "barrier.json")
    engine = WorkflowEngine(journal, executor, {})  # type: ignore[arg-type]
    spec = _workflow(args.root)
    if args.operation == "start":
        waiting = await engine.run_async(spec, _input(args.root), run_id=args.run_id)
        approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))
        return await engine.resume_async(
            args.run_id, approvals=(ApprovalDecision(approval_id, True),)
        )
    if args.operation == "resume":
        return await engine.resume_async(args.run_id)

    current = engine.get(args.run_id)
    action_id = args.action_id
    reference = workspace.published_output(action_id, LOGICAL_OUTPUT)
    summary = json.loads(workspace.resolve_published(reference).read_text(encoding="utf-8"))
    validation = current.state["v3"]["step_outputs"]["validate"]
    result = ToolExecutionResult(
        action_id,
        True,
        {
            "business_status": "APPROVED",
            "reason_code": None,
            "result_file": reference.to_mapping(),
            "summary": summary,
            "validation": validation,
        },
    )
    return await engine.reconcile_async(args.run_id, action_id, result)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("start", "resume", "reconcile"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--action-id", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    snapshot = anyio.run(_operate, args)
    temporary = args.output.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(snapshot.to_mapping(), sort_keys=True),  # type: ignore[union-attr]
        encoding="utf-8",
    )
    os.replace(temporary, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
