"""Fresh-process metric probe for the V6 delivery baseline."""

from __future__ import annotations

import argparse
import json
import resource
import tempfile
import time
from pathlib import Path


def _rss_bytes() -> int:
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024


def _approval_then_tool_workflow(tool: object) -> object:
    import platform_lightweight_agent as agent

    return agent.workflow(
        "delivery-recovery-probe",
        "1",
        [
            agent.approval_step("review", "continue?"),
            agent.tool_step(
                "read", tool, {"file": {"$ref": "/file"}}, approval_required=False
            ),
        ],
        schema_version="v6-light-workflow-v1",
    )


def _execution_workflow(tool: object) -> object:
    import platform_lightweight_agent as agent

    return agent.workflow(
        "delivery-execution-probe",
        "1",
        [agent.tool_step("echo", tool, {"value": "installed"}, approval_required=False)],
        schema_version="v6-light-workflow-v1",
    )


def _run_execution_probe(agent: object, root: Path) -> bool:
    tool = agent.ToolReference("delivery.echo", "1", "sha256:" + "d" * 64)
    executor = agent.FunctionToolExecutor()
    executor.register(tool, lambda arguments: {"value": arguments["value"]})
    journal = agent.SQLiteRunJournal(root / "execution.sqlite")
    snapshot = agent.WorkflowEngine(journal, executor, {}).run(
        _execution_workflow(tool), {}, run_id="delivery-execution"
    )
    return snapshot.status == "SUCCEEDED" and any(
        item.get("result", {}).get("output") == {"value": "installed"}
        for item in snapshot.state.get("actions", {}).values()
        if isinstance(item, dict)
    )


def _run_recovery_probe(agent: object, root: Path) -> bool:
    """Actually resume an installed workflow after a local recovery handoff."""

    workspace = agent.FileWorkspace.under(root / "files")
    source = root / "input.txt"
    source.write_text("delivery", encoding="utf-8")
    reference = workspace.stage_input(source, "input.txt")
    tool = agent.ToolReference("delivery.read", "1", "sha256:" + "e" * 64)
    effects: list[str] = []

    def read(arguments, context):  # type: ignore[no-untyped-def]
        effects.append(context.action_id)
        return {
            "text": context.files.resolve_input(arguments["file"]).read_text(
                encoding="utf-8"
            )
        }

    original_tools = agent.FunctionToolExecutor(
        context_factory=lambda request, cancellation: agent.ToolContext(
            request.action_id, cancellation, workspace
        )
    )
    original_tools.register(tool, read)
    original_journal = agent.SQLiteRunJournal(root / "runs.sqlite")
    waiting = agent.WorkflowEngine(original_journal, original_tools, {}).run(
        _approval_then_tool_workflow(tool),
        {"file": reference.to_mapping()},
        run_id="delivery-recovery",
    )
    if waiting.status != "WAITING_APPROVAL":
        return False
    package = agent.create_local_recovery_package(
        original_journal, workspace, root / "recovery.zip"
    )
    restored_journal, restored_workspace = agent.restore_local_recovery_package(
        package, root / "restored"
    )
    released = restored_journal.release_backup_recovery(
        "delivery-recovery", source_owner_stopped=True
    )
    if released.status != "WAITING_APPROVAL":
        return False
    restored_tools = agent.FunctionToolExecutor(
        context_factory=lambda request, cancellation: agent.ToolContext(
            request.action_id, cancellation, restored_workspace
        )
    )
    restored_tools.register(tool, read)
    completed = agent.WorkflowEngine(restored_journal, restored_tools, {}).resume(
        "delivery-recovery", approved=True
    )
    return (
        completed.status == "SUCCEEDED"
        and effects
        and len(effects) == 1
        and restored_workspace.resolve_input(reference).read_text(encoding="utf-8")
        == "delivery"
    )


def _run_query_probe(agent: object, root: Path) -> tuple[bool, float]:
    journal = agent.SQLiteRunJournal(root / "query.sqlite")
    definition = _legacy_approval_workflow()
    for index in range(100):
        journal.create(
            agent.RunSnapshot(
                run_id=f"query-{index:03d}",
                workflow=definition,
                inputs={},
                state={"values": {}},
                status="SUCCEEDED",
                driver_bindings={},
            ),
            "RUN_STARTED",
            {},
        )
    started = time.perf_counter()
    page = journal.list_runs(limit=50)
    elapsed = (time.perf_counter() - started) * 1000
    return len(page.items) == 50 and page.next_cursor is not None, elapsed


def _run_cleanup_probe(agent: object) -> tuple[bool, float]:
    host = agent.HostRuntime(
        agent.RuntimeAssembly(agent.InMemoryRunJournal(), agent.LocalToolExecutor())
    )
    started = time.perf_counter()
    try:
        host.start()
    finally:
        report = host.close(mode="wait")
    elapsed = (time.perf_counter() - started) * 1000
    return report.closed and report.execution_stopped, elapsed


def _legacy_approval_workflow() -> dict[str, object]:
    return {
        "schema_version": "v6-light-workflow-v1",
        "workflow_id": "delivery-probe",
        "version": "1",
        "steps": [
            {
                "id": "review",
                "kind": "approval",
                "config": {"prompt": "continue?"},
            }
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("import", "execution", "recovery", "query", "cleanup"),
        required=True,
    )
    args = parser.parse_args()
    started = time.perf_counter()
    import platform_lightweight_agent as agent

    import_ms = (time.perf_counter() - started) * 1000
    result: dict[str, object] = {
        "import_ms": import_ms,
        "rss_bytes": _rss_bytes(),
    }
    if args.mode == "execution":
        with tempfile.TemporaryDirectory(prefix="v6-delivery-execution-") as raw:
            execution_started = time.perf_counter()
            result["accepted"] = _run_execution_probe(agent, Path(raw))
            result["execution_ms"] = (time.perf_counter() - execution_started) * 1000
            result["rss_bytes"] = _rss_bytes()
    elif args.mode == "query":
        with tempfile.TemporaryDirectory(prefix="v6-delivery-query-") as raw:
            accepted, query_ms = _run_query_probe(agent, Path(raw))
            result["accepted"] = accepted
            result["query_ms"] = query_ms
            result["rss_bytes"] = _rss_bytes()
    elif args.mode == "cleanup":
        accepted, cleanup_ms = _run_cleanup_probe(agent)
        result["accepted"] = accepted
        result["cleanup_ms"] = cleanup_ms
        result["rss_bytes"] = _rss_bytes()
    elif args.mode == "recovery":
        with tempfile.TemporaryDirectory(prefix="v6-delivery-probe-") as raw:
            root = Path(raw)
            recovery_started = time.perf_counter()
            result["accepted"] = _run_recovery_probe(agent, root)
            result["recovery_ms"] = (time.perf_counter() - recovery_started) * 1000
            result["rss_bytes"] = _rss_bytes()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
