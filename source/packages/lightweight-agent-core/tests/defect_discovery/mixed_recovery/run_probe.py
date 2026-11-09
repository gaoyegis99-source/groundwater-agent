"""Mix controls and real process exits around one durable action."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import functools
import json
import os
from pathlib import Path
import random
import signal
import sqlite3
import subprocess
import sys
import time
from typing import Iterator

import anyio


HERE = Path(__file__).resolve().parent
PACKAGE_ROOT = HERE.parents[2]
WORKER = HERE / "worker.py"
sys.path.insert(0, str(HERE))
from fixture_tools import register  # noqa: E402 - stable fixture module identity

from platform_lightweight_agent import (  # noqa: E402 - after fixture path
    ApprovalDecision,
    FunctionToolExecutor,
    SQLiteRunJournal,
    ToolExecutionResult,
    WorkflowEngine,
    WorkflowRunError,
)


PATTERNS = (
    "unknown_cancel_success",
    "unknown_recover_cancel_failure",
    "cancel_before_dispatch",
    "approval_rejected",
    "unknown_success_no_cancel",
)


def _environment(root: Path) -> dict[str, str]:
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
    result = {name: os.environ[name] for name in keep if name in os.environ}
    result.update(
        {
            "PYTHONPATH": os.pathsep.join((str(HERE), str(PACKAGE_ROOT / "src"))),
            "PYTHONDONTWRITEBYTECODE": "1",
            "OPENAI_AGENTS_DISABLE_TRACING": "1",
            "V6_MIXED_LEDGER": str(root / "effects.sqlite"),
            "V6_MIXED_BARRIER": str(root / "effect-committed.json"),
        }
    )
    return result


@contextmanager
def _fixture_environment(root: Path) -> Iterator[None]:
    names = {
        "V6_MIXED_LEDGER": str(root / "effects.sqlite"),
        "V6_MIXED_BARRIER": str(root / "effect-committed.json"),
    }
    previous = {name: os.environ.get(name) for name in names}
    os.environ.update(names)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _engine(root: Path) -> WorkflowEngine:
    executor = FunctionToolExecutor()
    register(executor)
    return WorkflowEngine(SQLiteRunJournal(root / "runs.sqlite"), executor, {})


def _command(operation: str, root: Path, run_id: str) -> list[str]:
    return [
        sys.executable,
        str(WORKER),
        operation,
        "--journal",
        str(root / "runs.sqlite"),
        "--ledger",
        str(root / "effects.sqlite"),
        "--barrier",
        str(root / "effect-committed.json"),
        "--run-id",
        run_id,
        "--output",
        str(root / f"{operation}.json"),
    ]


def _run_worker(operation: str, root: Path, run_id: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _command(operation, root, run_id),
        cwd=PACKAGE_ROOT,
        env=_environment(root),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def _wait_barrier(process: subprocess.Popen[str], path: Path) -> dict[str, object]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise RuntimeError(
                f"worker exited before effect barrier: {stdout!r} {stderr!r}"
            )
        time.sleep(0.01)
    raise TimeoutError("effect barrier was not reached")


def _kill_owned(process: subprocess.Popen[str]) -> int:
    cmdline = Path(f"/proc/{process.pid}/cmdline")
    if not cmdline.is_file() or str(WORKER).encode() not in cmdline.read_bytes():
        raise RuntimeError("refusing to kill a process not owned by this probe")
    os.kill(process.pid, signal.SIGKILL)
    return process.wait(timeout=5)


def _drive_and_kill(root: Path, run_id: str) -> dict[str, object]:
    command = _command("resume", root, run_id)
    process = subprocess.Popen(
        command,
        cwd=PACKAGE_ROOT,
        env=_environment(root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        barrier = _wait_barrier(process, root / "effect-committed.json")
        returncode = _kill_owned(process)
    finally:
        if process.poll() is None:
            _kill_owned(process)
    return {
        "command": command,
        "pid": process.pid,
        "returncode": returncode,
        "barrier": barrier,
    }


def _facts(root: Path, run_id: str) -> dict[str, object]:
    journal = SQLiteRunJournal(root / "runs.sqlite")
    snapshot = journal.get(run_id)
    events = journal.events(run_id, limit=1_000)
    with sqlite3.connect(root / "effects.sqlite") as connection:
        effects = [
            {"action_id": row[0], "value": row[1]}
            for row in connection.execute(
                "SELECT action_id, value FROM effects ORDER BY sequence"
            )
        ]
    return {
        "status": snapshot.status,
        "execution_id": snapshot.execution_id,
        "revision": snapshot.revision,
        "cancel_requested": snapshot.state["v3"]["cancel_requested"],
        "approval_decisions": dict(snapshot.state["v3"]["approval_decisions"]),
        "action_ids": sorted(snapshot.state["actions"]),
        "actions": dict(snapshot.state["actions"]),
        "output": snapshot.state["v3"].get("output"),
        "event_sequences": [event.sequence for event in events],
        "event_types": [event.type for event in events],
        "effects": effects,
    }


def _persist_decision(
    engine: WorkflowEngine, run_id: str, approval_id: str, approved: bool
) -> object:
    return anyio.run(
        functools.partial(
            engine.resume_async,
            run_id,
            approvals=(ApprovalDecision(approval_id, approved),),
            advance=False,
        )
    )


def _late_result(
    engine: WorkflowEngine,
    run_id: str,
    action_id: str,
    result: ToolExecutionResult,
) -> dict[str, object]:
    before = engine.get(run_id).to_mapping()
    error: str | None = None
    try:
        engine.reconcile(run_id, action_id, result)
    except WorkflowRunError as caught:
        error = str(caught)
    after = engine.get(run_id).to_mapping()
    return {"error": error, "state_unchanged": after == before}


def run_trial(root: Path, seed: int, pattern: str) -> dict[str, object]:
    root.mkdir(parents=True, exist_ok=False)
    with sqlite3.connect(root / "effects.sqlite") as connection:
        connection.execute(
            "CREATE TABLE effects(sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
            "action_id TEXT NOT NULL, value INTEGER NOT NULL)"
        )
    run_id = f"mixed-{seed}"
    operations: list[str] = []
    seeded = _run_worker("seed", root, run_id)
    if seeded.returncode != 0:
        raise RuntimeError(f"seed failed: {seeded.stderr[-2000:]}")
    operations.append("seed_process_waiting_approval")
    with _fixture_environment(root):
        engine = _engine(root)
        waiting = engine.get(run_id)
        initial_execution = waiting.execution_id
        approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))
        action_id = next(iter(waiting.state["actions"]))
        killed: dict[str, object] | None = None
        recovery: subprocess.CompletedProcess[str] | None = None
        late = {"error": None, "state_unchanged": True}

        if pattern == "approval_rejected":
            _persist_decision(engine, run_id, approval_id, False)
            operations.append("reject_approval")
            recovery = _run_worker("resume", root, run_id)
            operations.append("restart_process")
        elif pattern == "cancel_before_dispatch":
            _persist_decision(engine, run_id, approval_id, True)
            operations.append("approve_without_advancing")
            engine.request_cancel(run_id, reason="seeded cancellation")
            operations.append("request_cancel_before_dispatch")
            recovery = _run_worker("resume", root, run_id)
            operations.append("restart_process")
        else:
            _persist_decision(engine, run_id, approval_id, True)
            operations.append("approve_without_advancing")
            killed = _drive_and_kill(root, run_id)
            operations.append("real_process_exit_after_effect")
            time.sleep(0.45)
            if pattern == "unknown_cancel_success":
                engine = _engine(root)
                engine.request_cancel(run_id, reason="seeded cancellation")
                operations.append("request_cancel")
            recovery = _run_worker("resume", root, run_id)
            operations.append("restart_process_to_unknown")
            if recovery.returncode != 0:
                raise RuntimeError(f"recovery failed: {recovery.stderr[-2000:]}")
            engine = _engine(root)
            if pattern == "unknown_recover_cancel_failure":
                engine.request_cancel(run_id, reason="seeded cancellation")
                operations.append("request_cancel")
                confirmed = ToolExecutionResult(
                    action_id, False, error="CONTROLLED_FAILURE"
                )
                conflicting = ToolExecutionResult(
                    action_id, True, {"value": 99}
                )
            else:
                confirmed = ToolExecutionResult(
                    action_id, True, {"value": 11}
                )
                conflicting = ToolExecutionResult(
                    action_id, False, error="LATE_CONFLICT"
                )
            engine.reconcile(run_id, action_id, confirmed)
            operations.append("manual_reconcile")
            late = _late_result(engine, run_id, action_id, conflicting)
            operations.append("late_conflicting_result")
            recovery = _run_worker("resume", root, run_id)
            operations.append("final_process_restart")

    if recovery is None or recovery.returncode != 0:
        raise RuntimeError(
            "restart failed: " + ("missing" if recovery is None else recovery.stderr[-2000:])
        )
    final = _facts(root, run_id)
    sequences = final["event_sequences"]
    action_ids = final["action_ids"]
    effects = final["effects"]
    expected_status = {
        "approval_rejected": "BLOCKED",
        "cancel_before_dispatch": "CANCELLED",
        "unknown_cancel_success": "CANCELLED",
        "unknown_recover_cancel_failure": "CANCELLED",
        "unknown_success_no_cancel": "SUCCEEDED",
    }[pattern]
    expected_effects = 0 if pattern in {
        "approval_rejected",
        "cancel_before_dispatch",
    } else 1
    requirements = {
        "expected_terminal_status": final["status"] == expected_status,
        "execution_identity_stable": final["execution_id"] == initial_execution,
        "single_action_identity": action_ids == [action_id],
        "approval_identity_stable": approval_id in final["approval_decisions"],
        "event_sequence_monotonic": sequences == sorted(set(sequences)),
        "external_effect_count": len(effects) == expected_effects,
        "effect_uses_action_identity": not effects
        or {item["action_id"] for item in effects} == {action_id},
        "real_exit_was_sigkill_when_selected": killed is None
        or killed["returncode"] == -signal.SIGKILL,
        "late_result_does_not_overwrite": late["state_unchanged"],
        "cancel_intent_is_monotonic": "request_cancel" not in operations
        and "request_cancel_before_dispatch" not in operations
        or final["cancel_requested"] is True,
    }
    return {
        "case": "R07",
        "variant": pattern,
        "seed": seed,
        "contract_result": "PASS" if all(requirements.values()) else "FAIL",
        "requirements": requirements,
        "operations": operations,
        "approval_id": approval_id,
        "action_id": action_id,
        "final": final,
        "late_result": late,
        "killed_worker": killed,
        "recovery": {
            "returncode": recovery.returncode,
            "stderr": recovery.stderr[-2_000:],
        },
        "evidence_directory": str(root),
    }


def collect(root: Path, repetitions: int, random_seed: int) -> dict[str, object]:
    root.mkdir(parents=True, exist_ok=False)
    chooser = random.Random(random_seed)
    selected = list(PATTERNS)
    selected.extend(chooser.choice(PATTERNS) for _ in range(max(0, repetitions - 5)))
    results: list[dict[str, object]] = []
    for index, pattern in enumerate(selected, start=1):
        seed = random_seed + index
        try:
            results.append(run_trial(root / f"trial-{index:02d}", seed, pattern))
        except Exception as error:  # noqa: BLE001 - continue the seeded matrix
            results.append(
                {
                    "case": "R07",
                    "variant": pattern,
                    "seed": seed,
                    "contract_result": "ERROR",
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "evidence_directory": str(root / f"trial-{index:02d}"),
                }
            )
    return {
        "schema_version": "v6-mixed-real-process-state-evidence-v1",
        "result": (
            "ERROR"
            if any(item["contract_result"] == "ERROR" for item in results)
            else "FAIL"
            if any(item["contract_result"] == "FAIL" for item in results)
            else "PASS"
        ),
        "random_seed": random_seed,
        "repetitions": len(selected),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=15)
    parser.add_argument("--seed", type=int, default=20261028)
    args = parser.parse_args()
    report = collect(args.evidence_root, args.repetitions, args.seed)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 1 if report["result"] == "ERROR" else 0


if __name__ == "__main__":
    raise SystemExit(main())
