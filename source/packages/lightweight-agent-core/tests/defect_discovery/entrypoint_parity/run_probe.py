"""Compare one frozen workflow across Python, CLI, and Web entrypoints."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from fastapi.testclient import TestClient
from platform_lightweight_agent import (
    ApprovalDecision,
    FunctionToolExecutor,
    HostRuntime,
    HostRuntimeLimits,
    LocalWebSecurity,
    NodeContract,
    RuntimeAssembly,
    SQLiteRunJournal,
    ToolRecoveryCapabilities,
    ToolReference,
    ValueBinding,
    WorkflowEngine,
    WorkflowSpec,
    create_web_app,
    tool_step,
    workflow,
)


HERE = Path(__file__).resolve().parent
PACKAGE_ROOT = HERE.parents[2]
TOOL = ToolReference("probe.entrypoint-parity", "1", "sha256:" + "6" * 64)
SESSION = "entrypoint-parity-session"
VALUE = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"value": {"type": "integer"}},
    "required": ["value"],
    "additionalProperties": False,
}


def _spec() -> WorkflowSpec:
    return workflow(
        "entrypoint-parity",
        "1",
        (
            tool_step(
                "double",
                TOOL,
                approval_required=True,
                capabilities=ToolRecoveryCapabilities(external_side_effects=False),
                contract=NodeContract(
                    VALUE,
                    VALUE,
                    {"/value": ValueBinding("workflow_input", "/value")},
                ),
            ),
        ),
        schema_version="v6-light-workflow-v4",
        input_schema=VALUE,
        output_schema=VALUE,
        output_bindings={
            "/value": ValueBinding("step_output", "/value", "double")
        },
    )


def _append_call(calls: list[dict[str, object]], arguments: Any, context: Any) -> None:
    calls.append(
        {
            "arguments": dict(arguments),
            "action_id": context.action_id,
            "cancelled": context.cancellation.cancelled,
        }
    )


def _executor(mode: str, calls: list[dict[str, object]]) -> FunctionToolExecutor:
    executor = FunctionToolExecutor()
    if mode == "sync":

        def handler(arguments: Any, context: Any) -> dict[str, int]:
            _append_call(calls, arguments, context)
            return {"value": int(arguments["value"]) * 2}

    else:

        async def handler(arguments: Any, context: Any) -> dict[str, int]:
            _append_call(calls, arguments, context)
            return {"value": int(arguments["value"]) * 2}

    executor.register(TOOL, handler)
    return executor


def _events(journal: SQLiteRunJournal, run_id: str) -> list[str]:
    return [event.type for event in journal.events(run_id, limit=1_000)]


def _semantic_result(
    *,
    channel: str,
    mode: str,
    waiting: Any,
    completed: Any,
    cancelled: Any,
    calls: list[dict[str, object]],
    success_events: list[str],
    cancel_events: list[str],
) -> dict[str, object]:
    approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))
    action_id = next(iter(completed.state["actions"]))
    required_success_events = {
        "ACTION_PROPOSED",
        "RUN_WAITING_APPROVAL",
        "APPROVALS_RECORDED",
        "ACTION_DISPATCHED",
        "ACTION_RESOLVED",
        "STEP_COMPLETED",
        "RUN_SUCCEEDED",
    }
    requirements = {
        "initially_waits_for_approval": waiting.status == "WAITING_APPROVAL",
        "approved_run_succeeds": completed.status == "SUCCEEDED",
        "output_matches": completed.state["v3"]["output"] == {"value": 14},
        "tool_called_once": len(calls) == 1,
        "tool_context_uses_action_identity": len(calls) == 1
        and calls[0]["action_id"] == action_id,
        "tool_receives_same_arguments": len(calls) == 1
        and calls[0]["arguments"] == {"value": 7},
        "approval_is_bound_to_action": waiting.state["v3"][
            "pending_approvals"
        ][approval_id]["action_id"]
        == action_id,
        "success_events_cover_same_semantics": required_success_events.issubset(
            success_events
        ),
        "cancelled_run_is_terminal": cancelled.status == "CANCELLED",
        "cancelled_run_never_calls_tool": len(cancelled.state["actions"]) == 1
        and next(iter(cancelled.state["actions"].values()))["status"]
        == "PENDING_APPROVAL"
        and len(calls) == 1,
        "cancel_events_are_persisted": "CANCELLATION_REQUESTED" in cancel_events
        and "RUN_CANCELLED" in cancel_events,
    }
    return {
        "channel": channel,
        "mode": mode,
        "contract_result": "PASS" if all(requirements.values()) else "FAIL",
        "requirements": requirements,
        "workflow_digest": WorkflowSpec.from_mapping(
            completed.workflow
        ).definition_digest,
        "statuses": {
            "waiting": waiting.status,
            "completed": completed.status,
            "cancelled": cancelled.status,
        },
        "output": completed.state["v3"].get("output"),
        "approval_id": approval_id,
        "action_id": action_id,
        "calls": calls,
        "success_events": success_events,
        "cancel_events": cancel_events,
    }


def _python_result(root: Path, mode: str) -> dict[str, object]:
    calls: list[dict[str, object]] = []
    journal = SQLiteRunJournal(root / f"python-{mode}.sqlite")
    engine = WorkflowEngine(journal, _executor(mode, calls), {})
    spec = _spec()
    success_id = f"python-{mode}-success"
    waiting = engine.run(spec, {"value": 7}, run_id=success_id)
    approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))
    completed = engine.resume(
        success_id, approvals=(ApprovalDecision(approval_id, True),)
    )
    cancel_id = f"python-{mode}-cancel"
    cancel_waiting = engine.run(spec, {"value": 7}, run_id=cancel_id)
    requested = engine.request_cancel(cancel_id, reason="parity probe")
    cancelled = engine.resume(cancel_id)
    if cancel_waiting.status != "WAITING_APPROVAL" or requested.status not in {
        "CANCEL_REQUESTED",
        "CANCELLED",
    }:
        raise AssertionError("Python cancellation did not pass through public states")
    return _semantic_result(
        channel="python",
        mode=mode,
        waiting=waiting,
        completed=completed,
        cancelled=cancelled,
        calls=calls,
        success_events=_events(journal, success_id),
        cancel_events=_events(journal, cancel_id),
    )


def _environment(root: Path) -> dict[str, str]:
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
    environment = {name: os.environ[name] for name in keep if name in os.environ}
    environment.update(
        {
            "PYTHONPATH": os.pathsep.join((str(root), str(PACKAGE_ROOT / "src"))),
            "PYTHONDONTWRITEBYTECODE": "1",
            "OPENAI_AGENTS_DISABLE_TRACING": "1",
        }
    )
    return environment


def _run_cli(command: list[str], root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    completed = subprocess.run(
        [sys.executable, "-m", "platform_lightweight_agent.cli", *command],
        cwd=root,
        env=_environment(root),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"CLI failed ({completed.returncode}): {completed.stderr[-2000:]}"
        )
    return json.loads(completed.stdout), {
        "command": command,
        "returncode": completed.returncode,
        "stderr": completed.stderr[-2_000:],
    }


def _registry_source(mode: str, ledger: Path) -> str:
    async_prefix = "async " if mode == "async" else ""
    return "\n".join(
        (
            "import json",
            "from pathlib import Path",
            "from platform_lightweight_agent import ToolReference",
            f"TOOL = ToolReference({TOOL.tool_id!r}, {TOOL.version!r}, "
            f"{TOOL.package_digest!r})",
            f"LEDGER = Path({str(ledger)!r})",
            f"{async_prefix}def handler(arguments, context):",
            "    with LEDGER.open('a', encoding='utf-8') as stream:",
            "        stream.write(json.dumps({'arguments': dict(arguments), "
            "'action_id': context.action_id, 'cancelled': "
            "context.cancellation.cancelled}) + '\\n')",
            "    return {'value': int(arguments['value']) * 2}",
            "def register(executor):",
            "    executor.register(TOOL, handler)",
            "",
        )
    )


def _cli_result(root: Path, mode: str) -> dict[str, object]:
    mode_root = root / f"cli-{mode}"
    mode_root.mkdir()
    ledger = mode_root / "calls.jsonl"
    (mode_root / "registry.py").write_text(
        _registry_source(mode, ledger), encoding="utf-8"
    )
    workflow_path = mode_root / "workflow.json"
    workflow_path.write_text(
        json.dumps(_spec().to_mapping(), sort_keys=True), encoding="utf-8"
    )
    journal_path = mode_root / "runs.sqlite"
    registry = "registry:register"
    success_id = f"cli-{mode}-success"
    waiting_map, start_observation = _run_cli(
        [
            "run",
            str(workflow_path),
            "--journal",
            str(journal_path),
            "--tool-registry",
            registry,
            "--input",
            '{"value":7}',
            "--run-id",
            success_id,
        ],
        mode_root,
    )
    waiting = SQLiteRunJournal(journal_path).get(success_id)
    approval_id = next(iter(waiting_map["state"]["v3"]["pending_approvals"]))
    _completed_map, approve_observation = _run_cli(
        [
            "resume",
            success_id,
            "--journal",
            str(journal_path),
            "--tool-registry",
            registry,
            "--approval-id",
            approval_id,
            "--approve",
        ],
        mode_root,
    )
    journal = SQLiteRunJournal(journal_path)
    completed = journal.get(success_id)
    cancel_id = f"cli-{mode}-cancel"
    _cancel_waiting_map, cancel_start_observation = _run_cli(
        [
            "run",
            str(workflow_path),
            "--journal",
            str(journal_path),
            "--tool-registry",
            registry,
            "--input",
            '{"value":7}',
            "--run-id",
            cancel_id,
        ],
        mode_root,
    )
    requested_map, cancel_observation = _run_cli(
        [
            "cancel",
            cancel_id,
            "--journal",
            str(journal_path),
            "--reason",
            "parity probe",
        ],
        mode_root,
    )
    _cancelled_map, settle_observation = _run_cli(
        [
            "resume",
            cancel_id,
            "--journal",
            str(journal_path),
            "--tool-registry",
            registry,
        ],
        mode_root,
    )
    cancelled = journal.get(cancel_id)
    calls = [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
    ]
    result = _semantic_result(
        channel="cli",
        mode=mode,
        waiting=waiting,
        completed=completed,
        cancelled=cancelled,
        calls=calls,
        success_events=_events(journal, success_id),
        cancel_events=_events(journal, cancel_id),
    )
    result["transport"] = {
        "start": start_observation,
        "approve": approve_observation,
        "cancel_start": cancel_start_observation,
        "cancel": cancel_observation,
        "settle": settle_observation,
        "cancel_ack_status": requested_map["status"],
    }
    return result


def _wait_terminal(host: HostRuntime, run_id: str) -> Any:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        snapshot = host.get(run_id)
        if snapshot.status in {"SUCCEEDED", "BLOCKED", "CANCELLED"}:
            return snapshot
        time.sleep(0.01)
    raise TimeoutError(f"Web-backed run {run_id} did not settle")


def _web_result(root: Path, mode: str) -> dict[str, object]:
    calls: list[dict[str, object]] = []
    journal = SQLiteRunJournal(root / f"web-{mode}.sqlite")
    host = HostRuntime(
        RuntimeAssembly(journal, _executor(mode, calls)),
        limits=HostRuntimeLimits(recovery_scan_interval_seconds=0.02),
    )
    app = create_web_app(
        host,
        workflow=_spec(),
        security=LocalWebSecurity(
            allowed_hosts=frozenset({"testserver"}), session_token=SESSION
        ),
    )
    try:
        with TestClient(app, headers={"X-V6-Local-Session": SESSION}) as client:
            success_id = f"web-{mode}-success"
            started = client.post(
                "/v2/runs", json={"run_id": success_id, "inputs": {"value": 7}}
            )
            if started.status_code != 201:
                raise RuntimeError(f"Web start failed: {started.text}")
            waiting = host.get(success_id)
            approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))
            approved = client.post(
                f"/v2/runs/{success_id}/approval",
                json={"approval_id": approval_id, "approved": True},
            )
            if approved.status_code != 200:
                raise RuntimeError(f"Web approval failed: {approved.text}")
            completed = _wait_terminal(host, success_id)
            cancel_id = f"web-{mode}-cancel"
            cancel_started = client.post(
                "/v2/runs", json={"run_id": cancel_id, "inputs": {"value": 7}}
            )
            if cancel_started.status_code != 201:
                raise RuntimeError(f"Web cancel start failed: {cancel_started.text}")
            cancelled_ack = client.post(
                f"/v2/runs/{cancel_id}/cancel", json={"reason": "parity probe"}
            )
            if cancelled_ack.status_code != 202:
                raise RuntimeError(f"Web cancel failed: {cancelled_ack.text}")
            cancelled = _wait_terminal(host, cancel_id)
        result = _semantic_result(
            channel="web",
            mode=mode,
            waiting=waiting,
            completed=completed,
            cancelled=cancelled,
            calls=calls,
            success_events=_events(journal, success_id),
            cancel_events=_events(journal, cancel_id),
        )
        result["transport"] = {
            "start_status": started.status_code,
            "approval_status": approved.status_code,
            "approval_ack_status": approved.json()["status"],
            "cancel_status": cancelled_ack.status_code,
            "cancel_ack_status": cancelled_ack.json()["status"],
        }
        return result
    finally:
        host.close(mode="wait")


def collect(root: Path) -> dict[str, object]:
    root.mkdir(parents=True, exist_ok=False)
    observations: list[dict[str, object]] = []
    for mode in ("sync", "async"):
        observations.extend(
            (
                _python_result(root, mode),
                _cli_result(root, mode),
                _web_result(root, mode),
            )
        )
    outputs = {json.dumps(item["output"], sort_keys=True) for item in observations}
    statuses = {
        json.dumps(item["statuses"], sort_keys=True) for item in observations
    }
    digests = {str(item["workflow_digest"]) for item in observations}
    aggregate = {
        "all_entrypoints_pass": all(
            item["contract_result"] == "PASS" for item in observations
        ),
        "same_frozen_workflow_digest": len(digests) == 1,
        "same_output": len(outputs) == 1,
        "same_terminal_semantics": len(statuses) == 1,
        "sync_and_async_covered": {
            str(item["mode"]) for item in observations
        }
        == {"sync", "async"},
        "python_cli_web_covered": {
            str(item["channel"]) for item in observations
        }
        == {"python", "cli", "web"},
    }
    return {
        "schema_version": "v6-entrypoint-parity-evidence-v1",
        "result": "PASS" if all(aggregate.values()) else "FAIL",
        "results": [
            {
                "case": "B04",
                "variant": "python_cli_web_sync_async_semantic_parity",
                "contract_result": (
                    "PASS" if all(aggregate.values()) else "FAIL"
                ),
                "requirements": aggregate,
                "observations": observations,
            }
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = collect(args.evidence_root)
        exit_code = 0
    except Exception as error:  # noqa: BLE001 - preserve infrastructure evidence
        report = {
            "schema_version": "v6-entrypoint-parity-evidence-v1",
            "result": "ERROR",
            "results": [
                {
                    "case": "B04",
                    "variant": "python_cli_web_sync_async_semantic_parity",
                    "contract_result": "ERROR",
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            ],
        }
        exit_code = 1
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
