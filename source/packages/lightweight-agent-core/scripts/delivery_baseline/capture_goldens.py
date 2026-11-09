"""Produce current-wheel artifacts as a future compatibility baseline.

These are real installed-wheel outputs, explicitly NOT prior-release evidence.
"""

from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import sys

import platform_lightweight_agent as agent


def _completed_tool(_: object) -> dict[str, int]:
    return {"value": 7}


def capture(root: Path) -> dict[str, object]:
    root.mkdir(parents=True, exist_ok=False)
    empty = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }
    approved = {
        **empty,
        "properties": {"approved": {"type": "boolean"}},
        "required": ["approved"],
    }
    approval_definition = agent.workflow_v4(
        "golden-approval",
        "1",
        [
            agent.approval_step(
                "review",
                "Approve the synthetic compatibility fixture?",
                contract=agent.NodeContract(empty, approved, {}),
            )
        ],
        input_schema=empty,
        output_schema=approved,
        output_bindings={
            "/approved": agent.ValueBinding("step_output", "/approved", "review")
        },
    )
    journal = agent.SQLiteRunJournal(root / "runs.sqlite")
    waiting = agent.WorkflowEngine(journal, agent.LocalToolExecutor(), {}).run(
        approval_definition, {}, run_id="golden-v4"
    )
    assert waiting.status == "WAITING_APPROVAL"
    for name, value in {
        "workflow.json": approval_definition.to_mapping(),
        "waiting.json": waiting.to_mapping(),
        "execution-manifest.json": waiting.execution_manifest,
    }.items():
        (root / name).write_text(
            json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
    workspace = agent.FileWorkspace.under(root / "files")
    package = agent.create_local_recovery_package(
        journal, workspace, root / "local-recovery.zip"
    )
    restored, _ = agent.restore_local_recovery_package(
        package, root / "restored-control"
    )
    released = restored.release_backup_recovery("golden-v4", source_owner_stopped=True)
    approval = next(iter(released.state["v3"]["pending_approvals"]))
    complete = agent.WorkflowEngine(restored, agent.LocalToolExecutor(), {}).resume(
        released.handle, approvals=(agent.ApprovalDecision(approval, True),)
    )
    assert complete.status == "SUCCEEDED" and complete.state["v3"]["output"] == {
        "approved": True
    }

    completed_output = {
        **empty,
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
    }
    completed_tool = agent.ToolReference(
        "golden.completed",
        "1",
        "sha256:" + "7" * 64,
    )
    completed_definition = agent.workflow_v4(
        "golden-tool-completed",
        "1",
        [
            agent.tool_step(
                "compute",
                completed_tool,
                approval_required=False,
                contract=agent.NodeContract(empty, completed_output, {}),
                capabilities=agent.ToolRecoveryCapabilities(
                    external_side_effects=False
                ),
            )
        ],
        input_schema=empty,
        output_schema=completed_output,
        output_bindings={
            "/value": agent.ValueBinding("step_output", "/value", "compute")
        },
    )
    completed_executor = agent.LocalToolExecutor()
    completed_executor.register(
        completed_tool,
        _completed_tool,
        implementation_version="golden-completed-tool-v1",
    )
    completed_journal = agent.SQLiteRunJournal(root / "tool-completed.sqlite")
    completed = agent.WorkflowEngine(
        completed_journal, completed_executor, {}
    ).run(completed_definition, {}, run_id="golden-tool-completed")
    assert completed.status == "SUCCEEDED"
    assert completed.state["v3"]["output"] == {"value": 7}
    for name, value in {
        "tool-completed-workflow.json": completed_definition.to_mapping(),
        "tool-completed-snapshot.json": completed.to_mapping(),
        "tool-completed-execution-manifest.json": completed.execution_manifest,
    }.items():
        (root / name).write_text(
            json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )

    report = {
        "schema_version": "v6-current-wheel-goldens-v2",
        "result": "PASS",
        "classification": "current_producer_seed_not_historical_compatibility",
        "producer_version": version("platform-lightweight-agent-core"),
        "python": sys.version,
        "producer_module": agent.__file__,
        "fixtures": {
            "approval_waiting": {
                "run_id": waiting.run_id,
                "execution_id": waiting.execution_id,
                "status": waiting.status,
                "workflow": "workflow.json",
                "snapshot": "waiting.json",
                "journal": "runs.sqlite",
                "recovery_package": "local-recovery.zip",
            },
            "tool_completed": {
                "run_id": completed.run_id,
                "execution_id": completed.execution_id,
                "status": completed.status,
                "workflow": "tool-completed-workflow.json",
                "snapshot": "tool-completed-snapshot.json",
                "journal": "tool-completed.sqlite",
            },
        },
        "expectation": (
            "resume the original approval identity and do not replay the "
            "already completed tool action"
        ),
        "uncovered": [
            "prior releases",
            "domain lock and context policy evolution",
            "cross-driver or cross-driver-version continuation",
        ],
        "files": [
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in sorted(root.rglob("*"))
            if path.is_file()
        ],
    }
    (root / "goldens.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(capture(args.output)))
