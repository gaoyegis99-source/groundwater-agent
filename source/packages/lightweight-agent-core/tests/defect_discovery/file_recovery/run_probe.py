"""Exercise published CSV artifacts across a killed-worker journal gap."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import stat
import subprocess
import sys
import time

from platform_lightweight_agent import FileReference, FileWorkspace, SQLiteRunJournal


HERE = Path(__file__).resolve().parent
PACKAGE_ROOT = HERE.parents[2]
WORKER = HERE / "worker.py"
LOGICAL_OUTPUT = "reports/csv-statistics.json"
VARIANTS = ("intact", "missing_blob", "tampered_blob", "corrupt_index")


def _environment() -> dict[str, str]:
    result = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
        if key in os.environ
    }
    result.update(
        {
            "PYTHONPATH": str(PACKAGE_ROOT / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "OPENAI_AGENTS_DISABLE_TRACING": "1",
        }
    )
    return result


def _command(
    operation: str, root: Path, run_id: str, *, action_id: str = ""
) -> list[str]:
    command = [
        sys.executable,
        str(WORKER),
        operation,
        "--root",
        str(root),
        "--run-id",
        run_id,
        "--output",
        str(root / f"{operation}.json"),
    ]
    if action_id:
        command.extend(("--action-id", action_id))
    return command


def _wait_barrier(process: subprocess.Popen[str], barrier: Path) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if barrier.is_file():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise RuntimeError(
                f"CSV worker exited before publication barrier: {stdout!r} {stderr!r}"
            )
        time.sleep(0.01)
    raise TimeoutError("CSV worker did not publish before the deadline")


def _kill(process: subprocess.Popen[str]) -> int:
    command = Path(f"/proc/{process.pid}/cmdline")
    if not command.is_file() or str(WORKER).encode() not in command.read_bytes():
        raise RuntimeError("refusing to kill a non-probe process")
    os.kill(process.pid, signal.SIGKILL)
    return process.wait(timeout=5)


def _calls(path: Path) -> list[dict[str, str]]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT tool_id, action_id FROM calls ORDER BY sequence"
        ).fetchall()
    return [{"tool_id": tool, "action_id": action} for tool, action in rows]


def _initialize(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=False)
    with sqlite3.connect(root / "calls.sqlite") as connection:
        connection.execute(
            "CREATE TABLE calls (sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
            "tool_id TEXT NOT NULL, action_id TEXT NOT NULL)"
        )
    workspace = FileWorkspace.under(root / "workspace")
    source = root / "input.csv"
    source.write_text("level\n1\n2\n3\n", encoding="utf-8")
    reference = workspace.stage_input(source, "inputs/values.csv")
    (root / "input.json").write_text(
        json.dumps({"file": reference.to_mapping(), "column": "level"}),
        encoding="utf-8",
    )


def _published_identity(root: Path, action_id: str) -> tuple[FileReference, Path, Path]:
    workspace = FileWorkspace.under(root / "workspace")
    reference = workspace.published_output(action_id, LOGICAL_OUTPUT)
    blob = workspace.resolve_published(reference)
    index = (
        workspace.artifact_root
        / ".actions"
        / action_id
        / f"{LOGICAL_OUTPUT}.json"
    )
    return reference, blob, index


def _mutate(variant: str, blob: Path, index: Path) -> None:
    if variant == "missing_blob":
        blob.unlink()
    elif variant == "tampered_blob":
        blob.chmod(stat.S_IRUSR | stat.S_IWUSR)
        blob.write_bytes(blob.read_bytes() + b"tampered")
    elif variant == "corrupt_index":
        index.chmod(stat.S_IRUSR | stat.S_IWUSR)
        index.write_text("{", encoding="utf-8")


def run_variant(root: Path, variant: str) -> dict[str, object]:
    trial = root / variant
    _initialize(trial)
    run_id = f"csv-publication-{variant}"
    start = _command("start", trial, run_id)
    process = subprocess.Popen(
        start,
        cwd=PACKAGE_ROOT,
        env=_environment(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_barrier(process, trial / "barrier.json")
        killed_exit = _kill(process)
    finally:
        if process.poll() is None:
            _kill(process)
    time.sleep(0.45)
    journal = SQLiteRunJournal(trial / "runs.sqlite")
    before = journal.get(run_id)
    calculate = [
        (action_id, action)
        for action_id, action in before.state["actions"].items()
        if str(action.get("step_key", "")).endswith("calculate")
    ]
    if len(calculate) != 1:
        raise RuntimeError("probe could not identify the calculate action")
    action_id = calculate[0][0]
    reference, blob, index = _published_identity(trial, action_id)
    original_digest = "sha256:" + hashlib.sha256(blob.read_bytes()).hexdigest()
    _mutate(variant, blob, index)

    resume = subprocess.run(
        _command("resume", trial, run_id),
        cwd=PACKAGE_ROOT,
        env=_environment(),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    held = journal.get(run_id)
    lookup_error: str | None = None
    lookup_ok = False
    try:
        recovered = FileWorkspace.under(trial / "workspace").published_output(
            action_id, LOGICAL_OUTPUT
        )
        lookup_ok = recovered == reference
    except Exception as error:  # noqa: BLE001 - exact public failure is evidence
        lookup_error = str(error)

    reconcile_exit: int | None = None
    if variant == "intact":
        reconciled = subprocess.run(
            _command("reconcile", trial, run_id, action_id=action_id),
            cwd=PACKAGE_ROOT,
            env=_environment(),
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        reconcile_exit = reconciled.returncode
    final = journal.get(run_id)
    calls = _calls(trial / "calls.sqlite")
    calculate_calls = [
        item for item in calls if item["tool_id"] == "csv-statistics.calculate"
    ]
    requirements = {
        "killed_in_exact_gap": killed_exit == -signal.SIGKILL,
        "publication_precedes_receipt": before.state["actions"][action_id]["status"]
        == "DISPATCHED",
        "resume_does_not_rerun_calculate": len(calculate_calls) == 1,
        "resume_holds_unknown_result": held.status == "RECONCILING",
        "published_digest_was_valid": original_digest == reference.sha256,
        "intact_lookup_or_corruption_rejected": (
            lookup_ok if variant == "intact" else lookup_error is not None
        ),
        "intact_reconciles_or_damage_stays_unknown": (
            final.status == "SUCCEEDED"
            if variant == "intact"
            else final.status == "RECONCILING"
        ),
        "intact_reconcile_process": (
            reconcile_exit == 0 if variant == "intact" else reconcile_exit is None
        ),
    }
    return {
        "case": "P04",
        "variant": variant,
        "contract_result": "PASS" if all(requirements.values()) else "FAIL",
        "requirements": requirements,
        "run_id": run_id,
        "execution_id": before.execution_id,
        "action_id": action_id,
        "publication": reference.to_mapping(),
        "lookup_error": lookup_error,
        "statuses": {
            "before_resume": before.status,
            "after_resume": held.status,
            "final": final.status,
        },
        "tool_calls": calls,
        "worker": {
            "pid": process.pid,
            "killed_exit_code": killed_exit,
            "resume_exit_code": resume.returncode,
            "resume_stderr": resume.stderr[-2000:],
            "reconcile_exit_code": reconcile_exit,
        },
        "evidence_directory": str(trial),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--variants", nargs="+", choices=VARIANTS, default=list(VARIANTS)
    )
    args = parser.parse_args()
    args.evidence_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    for variant in args.variants:
        try:
            results.append(run_variant(args.evidence_root, variant))
        except Exception as error:  # noqa: BLE001 - preserve the rest of the matrix
            results.append(
                {
                    "case": "P04",
                    "variant": variant,
                    "contract_result": "ERROR",
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            )
    report = {
        "schema_version": "v6-file-recovery-evidence-v1",
        "result": (
            "ERROR"
            if any(item["contract_result"] == "ERROR" for item in results)
            else "FAIL"
            if any(item["contract_result"] == "FAIL" for item in results)
            else "PASS"
        ),
        "results": results,
    }
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 1 if report["result"] == "ERROR" else 0


if __name__ == "__main__":
    raise SystemExit(main())
