"""Kill after approval commit and recover without submitting it again."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

from platform_lightweight_agent import SQLiteRunJournal


HERE = Path(__file__).resolve().parent
PACKAGE_ROOT = HERE.parents[2]
WORKER = HERE / "worker.py"


def _environment() -> dict[str, str]:
    result = {
        name: os.environ[name]
        for name in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
        if name in os.environ
    }
    result.update(
        {
            "PYTHONPATH": str(PACKAGE_ROOT / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "OPENAI_AGENTS_DISABLE_TRACING": "1",
        }
    )
    return result


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
        str(root / "approval-committed.json"),
        "--output",
        str(root / f"{operation}.json"),
        "--run-id",
        run_id,
    ]


def _kill_owned(process: subprocess.Popen[str]) -> int:
    cmdline = Path(f"/proc/{process.pid}/cmdline")
    if not cmdline.is_file() or str(WORKER).encode() not in cmdline.read_bytes():
        raise RuntimeError("refusing to kill a process not owned by this probe")
    os.kill(process.pid, signal.SIGKILL)
    return process.wait(timeout=5)


def _wait_barrier(process: subprocess.Popen[str], path: Path) -> dict[str, object]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise RuntimeError(
                f"seed exited before approval barrier: {stdout!r} {stderr!r}"
            )
        time.sleep(0.01)
    raise TimeoutError("approval commit barrier was not reached")


def run(root: Path) -> dict[str, object]:
    root.mkdir(parents=True, exist_ok=False)
    with sqlite3.connect(root / "effects.sqlite") as connection:
        connection.execute(
            "CREATE TABLE calls(sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
            "action_id TEXT NOT NULL)"
        )
    run_id = "approval-process-recovery"
    seed_command = _command("seed", root, run_id)
    seed = subprocess.Popen(
        seed_command,
        cwd=PACKAGE_ROOT,
        env=_environment(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        barrier = _wait_barrier(seed, root / "approval-committed.json")
        killed_exit = _kill_owned(seed)
    finally:
        if seed.poll() is None:
            _kill_owned(seed)
    recover_command = _command("recover", root, run_id)
    recovered = subprocess.run(
        recover_command,
        cwd=PACKAGE_ROOT,
        env=_environment(),
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    journal = SQLiteRunJournal(root / "runs.sqlite")
    snapshot = journal.get(run_id)
    events = journal.events(run_id, limit=1_000)
    with sqlite3.connect(root / "effects.sqlite") as connection:
        calls = [
            row[0]
            for row in connection.execute(
                "SELECT action_id FROM calls ORDER BY sequence"
            )
        ]
    approval_id = str(barrier["approval_id"])
    action_id = str(barrier["action_id"])
    requirements = {
        "approval_was_committed_before_kill": barrier["decision"] is True,
        "worker_was_sigkilled": killed_exit == -signal.SIGKILL,
        "new_host_recovers_without_control_request": recovered.returncode == 0
        and snapshot.status == "SUCCEEDED",
        "approval_identity_is_preserved": snapshot.state["v3"][
            "approval_decisions"
        ][approval_id]
        is True,
        "action_identity_is_preserved": sorted(snapshot.state["actions"])
        == [action_id],
        "tool_executes_once": calls == [action_id],
        "approval_not_requested_twice": [event.type for event in events].count(
            "RUN_WAITING_APPROVAL"
        )
        == 1
        and [event.type for event in events].count("ACTION_PROPOSED") == 1,
    }
    return {
        "schema_version": "v6-approval-process-recovery-v1",
        "result": "PASS" if all(requirements.values()) else "FAIL",
        "results": [
            {
                "case": "A01",
                "variant": "sigkill_after_approval_commit",
                "contract_result": (
                    "PASS" if all(requirements.values()) else "FAIL"
                ),
                "requirements": requirements,
                "run_id": run_id,
                "execution_id": snapshot.execution_id,
                "approval_id": approval_id,
                "action_id": action_id,
                "tool_calls": calls,
                "event_sequences": [event.sequence for event in events],
                "event_types": [event.type for event in events],
                "worker": {
                    "seed_pid": seed.pid,
                    "killed_exit_code": killed_exit,
                    "recover_exit_code": recovered.returncode,
                    "recover_stderr": recovered.stderr[-2_000:],
                },
                "commands": {
                    "seed": seed_command,
                    "recover": recover_command,
                },
                "evidence_directory": str(root),
            }
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run(args.evidence_root)
        exit_code = 0
    except Exception as error:  # noqa: BLE001 - preserve infrastructure evidence
        report = {
            "schema_version": "v6-approval-process-recovery-v1",
            "result": "ERROR",
            "results": [
                {
                    "case": "A01",
                    "variant": "sigkill_after_approval_commit",
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
