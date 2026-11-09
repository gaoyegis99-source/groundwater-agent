"""Kill independent V6 workers at durable action boundaries and collect facts."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
from threading import Event, Lock, Thread
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from platform_lightweight_agent import SQLiteRunJournal


HERE = Path(__file__).resolve().parent
PACKAGE_ROOT = HERE.parents[2]
WORKER = HERE / "worker.py"
JOURNAL_WINDOWS = {
    "before_action_ready",
    "after_action_ready",
    "before_action_dispatched",
    "after_action_dispatched",
    "before_action_resolved",
    "after_action_resolved",
    "after_step_completed",
    "after_run_succeeded",
}
ALL_WINDOWS = (
    "before_action_ready",
    "after_action_ready",
    "before_action_dispatched",
    "after_action_dispatched",
    "effect_committed",
    "before_action_resolved",
    "after_action_resolved",
    "after_step_completed",
    "after_run_succeeded",
)


@dataclass
class ServiceState:
    database: Path
    hang_after_effect: bool
    idempotent: bool
    effect_committed: Event = field(default_factory=Event)
    release_response: Event = field(default_factory=Event)
    lock: Lock = field(default_factory=Lock)

    def initialize(self) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.executescript(
                """
                CREATE TABLE requests (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    method TEXT NOT NULL,
                    action_id TEXT,
                    path TEXT NOT NULL
                );
                CREATE TABLE effects (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    action_id TEXT NOT NULL,
                    value INTEGER NOT NULL
                );
                """
            )

    def execute(self, action_id: str) -> None:
        with self.lock, sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO requests(method, action_id, path) VALUES('POST', ?, '/execute')",
                (action_id,),
            )
            existing = connection.execute(
                "SELECT 1 FROM effects WHERE action_id = ? LIMIT 1", (action_id,)
            ).fetchone()
            if not self.idempotent or existing is None:
                connection.execute(
                    "INSERT INTO effects(action_id, value) VALUES(?, 7)",
                    (action_id,),
                )
            connection.commit()
        self.effect_committed.set()

    def query(self, action_id: str) -> bool:
        with self.lock, sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO requests(method, action_id, path) VALUES('GET', ?, '/result')",
                (action_id,),
            )
            found = connection.execute(
                "SELECT 1 FROM effects WHERE action_id = ? LIMIT 1", (action_id,)
            ).fetchone()
            connection.commit()
        return found is not None

    def counts(self) -> dict[str, object]:
        with self.lock, sqlite3.connect(self.database) as connection:
            rows = connection.execute(
                "SELECT method, COUNT(*) FROM requests GROUP BY method"
            ).fetchall()
            effects = connection.execute("SELECT COUNT(*) FROM effects").fetchone()[0]
            actions = [
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT action_id FROM effects ORDER BY action_id"
                )
            ]
        counts = {method: count for method, count in rows}
        return {
            "execute_requests": counts.get("POST", 0),
            "query_requests": counts.get("GET", 0),
            "effects": effects,
            "effect_action_ids": actions,
        }


class EffectServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, state: ServiceState) -> None:
        self.state = state
        super().__init__(("127.0.0.1", 0), EffectHandler)


class EffectHandler(BaseHTTPRequestHandler):
    server: EffectServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, status: int, value: object) -> None:
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self) -> None:  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        payload = json.loads(raw)
        action_id = payload["action_id"]
        self.server.state.execute(action_id)
        if self.server.state.hang_after_effect:
            self.server.state.release_response.wait(30)
        self._json(200, {"output": {"value": 7}})

    def do_GET(self) -> None:  # noqa: N802
        action_id = parse_qs(urlparse(self.path).query).get("action_id", [""])[0]
        if self.server.state.query(action_id):
            self._json(200, {"status": "succeeded", "output": {"value": 7}})
        else:
            self._json(202, {"status": "pending"})


class RunningService:
    def __init__(self, state: ServiceState) -> None:
        self.server = EffectServer(state)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __exit__(self, *_args: object) -> None:
        self.server.state.release_response.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _environment() -> dict[str, str]:
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
    result = {name: os.environ[name] for name in keep if name in os.environ}
    result.update(
        {
            "PYTHONPATH": str(PACKAGE_ROOT / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "OPENAI_AGENTS_DISABLE_TRACING": "1",
            "NO_PROXY": "127.0.0.1,localhost",
        }
    )
    return result


def _command(
    operation: str,
    *,
    root: Path,
    origin: str,
    run_id: str,
    window: str,
    supports_query: bool,
    accepts_idempotency: bool,
) -> list[str]:
    command = [
        sys.executable,
        str(WORKER),
        operation,
        "--journal",
        str(root / "runs.sqlite"),
        "--barrier",
        str(root / "barrier.json"),
        "--window",
        window,
        "--origin",
        origin,
        "--run-id",
        run_id,
        "--output",
        str(root / f"{operation}.json"),
    ]
    if supports_query:
        command.append("--supports-query")
    if accepts_idempotency:
        command.append("--accepts-idempotency")
    return command


def _wait_for(predicate: Any, process: subprocess.Popen[str], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        code = process.poll()
        if code is not None:
            stdout, stderr = process.communicate()
            raise RuntimeError(
                f"worker exited before barrier ({code}); stdout={stdout!r}; stderr={stderr!r}"
            )
        time.sleep(0.01)
    raise TimeoutError("worker did not reach the crash barrier")


def _kill_owned(process: subprocess.Popen[str]) -> int:
    cmdline = Path(f"/proc/{process.pid}/cmdline")
    if not cmdline.is_file() or str(WORKER).encode() not in cmdline.read_bytes():
        raise RuntimeError("refusing to kill a process that is not the probe worker")
    os.kill(process.pid, signal.SIGKILL)
    return process.wait(timeout=5)


def _journal_facts(path: Path, run_id: str) -> dict[str, object]:
    journal = SQLiteRunJournal(path)
    snapshot = journal.get(run_id)
    events = journal.events(run_id, limit=1_000)
    actions = snapshot.state.get("actions", {})
    return {
        "status": snapshot.status,
        "execution_id": snapshot.execution_id,
        "revision": snapshot.revision,
        "action_ids": sorted(actions),
        "action_statuses": {
            action_id: action["status"] for action_id, action in actions.items()
        },
        "owner_generation": snapshot.state.get("v3", {})
        .get("execution", {})
        .get("generation"),
        "workflow_output": snapshot.state.get("v3", {}).get("output"),
        "event_sequences": [event.sequence for event in events],
        "event_types": [event.type for event in events],
    }


def _expected_status(window: str, supports_query: bool) -> str:
    # A transition blocked *before* ACTION_DISPATCHED leaves a durable READY
    # action, which is safe to execute.  Once DISPATCHED itself is committed,
    # the absence of an effect cannot prove the old worker never called out.
    if window == "after_action_dispatched":
        return "RECONCILING"
    if window in {"effect_committed", "before_action_resolved"}:
        return "SUCCEEDED" if supports_query else "RECONCILING"
    return "SUCCEEDED"


def run_trial(
    root: Path,
    *,
    window: str,
    supports_query: bool,
    accepts_idempotency: bool,
    repetition: int,
) -> dict[str, object]:
    variant = (
        f"{window}-query-{int(supports_query)}-idempotent-"
        f"{int(accepts_idempotency)}-{repetition}"
    )
    trial = root / variant
    trial.mkdir(parents=True, exist_ok=False)
    state = ServiceState(
        trial / "external-ledger.sqlite",
        hang_after_effect=window == "effect_committed",
        idempotent=accepts_idempotency,
    )
    state.initialize()
    run_id = "crash-" + variant
    with RunningService(state) as origin:
        start_command = _command(
            "start",
            root=trial,
            origin=origin,
            run_id=run_id,
            window=("none" if window == "effect_committed" else window),
            supports_query=supports_query,
            accepts_idempotency=accepts_idempotency,
        )
        process = subprocess.Popen(
            start_command,
            cwd=PACKAGE_ROOT,
            env=_environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            if window == "effect_committed":
                _wait_for(state.effect_committed.is_set, process, 15)
            else:
                barrier = trial / "barrier.json"
                _wait_for(barrier.is_file, process, 15)
            killed_exit = _kill_owned(process)
        finally:
            if process.poll() is None:
                _kill_owned(process)
            state.release_response.set()
        # The killed process left a real lease.  Its duration was shortened in
        # that process only; wait for actual persisted expiry, never edit rows.
        time.sleep(0.45)
        before = _journal_facts(trial / "runs.sqlite", run_id)
        resume_command = _command(
            "resume",
            root=trial,
            origin=origin,
            run_id=run_id,
            window="none",
            supports_query=supports_query,
            accepts_idempotency=accepts_idempotency,
        )
        resumed = subprocess.run(
            resume_command,
            cwd=PACKAGE_ROOT,
            env=_environment(),
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        after = _journal_facts(trial / "runs.sqlite", run_id)
        counts = state.counts()
    expected = _expected_status(window, supports_query)
    sequences = after["event_sequences"]
    action_ids = after["action_ids"]
    requirements = {
        "resume_exit_zero": resumed.returncode == 0,
        "expected_status": after["status"] == expected,
        "event_sequence_monotonic": sequences == sorted(set(sequences)),
        "single_action_identity": len(action_ids) == 1,
        "no_duplicate_effect": counts["effects"] <= 1,
        "no_duplicate_execute": counts["execute_requests"] <= 1,
        "effect_identity_matches_action": (
            not counts["effect_action_ids"]
            or counts["effect_action_ids"] == action_ids
        ),
    }
    return {
        "case": "R01/R02",
        "variant": variant,
        "window": window,
        "supports_result_query": supports_query,
        "accepts_idempotency_key": accepts_idempotency,
        "repetition": repetition,
        "contract_result": "PASS" if all(requirements.values()) else "FAIL",
        "requirements": requirements,
        "expected_final_status": expected,
        "before_resume": before,
        "after_resume": after,
        "external_ledger": counts,
        "worker": {
            "pid": process.pid,
            "killed_exit_code": killed_exit,
            "resume_exit_code": resumed.returncode,
            "resume_stderr": resumed.stderr[-2000:],
        },
        "commands": {"start": start_command, "resume": resume_command},
        "evidence_directory": str(trial),
        "lease_seconds": 0.3,
    }


def run_stale_owner_trial(root: Path) -> dict[str, object]:
    """Let a live old owner return after a new process has reconciled."""

    trial = root / "live-stale-owner"
    trial.mkdir(parents=True, exist_ok=False)
    state = ServiceState(
        trial / "external-ledger.sqlite",
        hang_after_effect=True,
        idempotent=False,
    )
    state.initialize()
    run_id = "live-stale-owner"
    with RunningService(state) as origin:
        start_command = _command(
            "start",
            root=trial,
            origin=origin,
            run_id=run_id,
            window="none",
            supports_query=True,
            accepts_idempotency=False,
        )
        old_owner = subprocess.Popen(
            start_command,
            cwd=PACKAGE_ROOT,
            env=_environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if not state.effect_committed.wait(15):
            raise TimeoutError("old owner did not commit its external effect")
        time.sleep(0.45)
        before = _journal_facts(trial / "runs.sqlite", run_id)
        takeover_command = _command(
            "resume",
            root=trial,
            origin=origin,
            run_id=run_id,
            window="none",
            supports_query=True,
            accepts_idempotency=False,
        )
        takeover = subprocess.run(
            takeover_command,
            cwd=PACKAGE_ROOT,
            env=_environment(),
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        after_takeover = _journal_facts(trial / "runs.sqlite", run_id)
        state.release_response.set()
        try:
            old_stdout, old_stderr = old_owner.communicate(timeout=10)
            old_forced_exit = False
        except subprocess.TimeoutExpired:
            _kill_owned(old_owner)
            old_stdout, old_stderr = old_owner.communicate()
            old_forced_exit = True
        final = _journal_facts(trial / "runs.sqlite", run_id)
        counts = state.counts()
    action_ids = final["action_ids"]
    requirements = {
        "old_owner_was_live_during_takeover": before["status"] == "RUNNING"
        and old_owner.pid > 0,
        "new_owner_reconciles": takeover.returncode == 0
        and after_takeover["status"] == "SUCCEEDED",
        "generation_increases": isinstance(before["owner_generation"], int)
        and isinstance(after_takeover["owner_generation"], int)
        and after_takeover["owner_generation"] > before["owner_generation"],
        "late_old_owner_cannot_overwrite": final["status"]
        == after_takeover["status"]
        and final["execution_id"] == after_takeover["execution_id"]
        and final["action_ids"] == after_takeover["action_ids"]
        and final["action_statuses"] == after_takeover["action_statuses"]
        and final["workflow_output"] == after_takeover["workflow_output"],
        "single_action_identity": len(action_ids) == 1,
        "single_execute_and_effect": counts["execute_requests"] == 1
        and counts["effects"] == 1,
        "takeover_queries_original_action": counts["query_requests"] == 1
        and counts["effect_action_ids"] == action_ids,
        "old_owner_did_not_need_forced_termination": not old_forced_exit,
    }
    return {
        "case": "R04",
        "variant": "live_old_owner_returns_after_new_generation",
        "contract_result": "PASS" if all(requirements.values()) else "FAIL",
        "requirements": requirements,
        "before_takeover": before,
        "after_takeover": after_takeover,
        "final": final,
        "external_ledger": counts,
        "old_owner": {
            "pid": old_owner.pid,
            "returncode": old_owner.returncode,
            "forced_exit": old_forced_exit,
            "stdout": old_stdout[-2_000:],
            "stderr": old_stderr[-2_000:],
        },
        "new_owner": {
            "returncode": takeover.returncode,
            "stdout": takeover.stdout[-2_000:],
            "stderr": takeover.stderr[-2_000:],
        },
        "commands": {"old_owner": start_command, "new_owner": takeover_command},
        "evidence_directory": str(trial),
        "lease_seconds": 0.3,
    }
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument(
        "--mode", choices=("crash-windows", "owner-generation"), default="crash-windows"
    )
    parser.add_argument(
        "--windows", nargs="+", choices=ALL_WINDOWS, default=list(ALL_WINDOWS)
    )
    args = parser.parse_args()
    args.evidence_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    if args.mode == "owner-generation":
        try:
            results.append(run_stale_owner_trial(args.evidence_root))
        except Exception as error:  # noqa: BLE001 - retain infrastructure evidence
            results.append(
                {
                    "case": "R04",
                    "variant": "live_old_owner_returns_after_new_generation",
                    "contract_result": "ERROR",
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            )
    else:
        for window in args.windows:
            variants = ((True, False), (False, False), (False, True))
            for supports_query, accepts_idempotency in variants:
                for repetition in range(1, args.repetitions + 1):
                    try:
                        results.append(
                            run_trial(
                                args.evidence_root,
                                window=window,
                                supports_query=supports_query,
                                accepts_idempotency=accepts_idempotency,
                                repetition=repetition,
                            )
                        )
                    except Exception as error:  # noqa: BLE001 - retain later cases
                        results.append(
                            {
                                "case": "R01/R02",
                                "variant": (
                                    f"{window}-query-{int(supports_query)}-"
                                    f"idempotent-{int(accepts_idempotency)}-{repetition}"
                                ),
                                "window": window,
                                "contract_result": "ERROR",
                                "error_type": type(error).__name__,
                                "error": str(error),
                            }
                        )
    report = {
        "schema_version": "v6-process-crash-evidence-v1",
        "result": (
            "ERROR"
            if any(item["contract_result"] == "ERROR" for item in results)
            else "FAIL"
            if any(item["contract_result"] == "FAIL" for item in results)
            else "PASS"
        ),
        "environment": {
            "python": sys.version,
            "platform": sys.platform,
            "source_root": str(PACKAGE_ROOT),
        },
        "results": results,
    }
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 1 if report["result"] == "ERROR" else 0


if __name__ == "__main__":
    raise SystemExit(main())
