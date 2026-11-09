"""Exercise the HTTP adapter through real loopback transport failures."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Lock, Thread
from typing import Any

from platform_lightweight_agent import (
    InMemoryRunJournal,
    RemoteHttpTool,
    RemoteHttpToolExecutor,
    ToolReference,
    WorkflowEngine,
    tool_step,
    workflow,
)


TOOL = ToolReference("probe.http-fault", "1", "sha256:" + "3" * 64)
MODES = (
    "success",
    "client_error",
    "server_error",
    "malformed",
    "nan",
    "wrong_reconcile_action",
)


@dataclass
class State:
    mode: str
    posts: list[str] = field(default_factory=list)
    gets: list[str] = field(default_factory=list)
    lock: Lock = field(default_factory=Lock)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, state: State) -> None:
        self.state = state
        super().__init__(("127.0.0.1", 0), Handler)


class Handler(BaseHTTPRequestHandler):
    server: Server

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        payload = json.loads(
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
        )
        action_id = payload["action_id"]
        with self.server.state.lock:
            self.server.state.posts.append(action_id)
        mode = self.server.state.mode
        if mode == "success":
            self._send(200, b'{"output":{"value":7}}')
        elif mode == "client_error":
            self._send(400, b'{"error":"invalid"}')
        elif mode == "server_error":
            self._send(500, b'{"error":"unknown"}')
        elif mode == "malformed":
            self._send(200, b"{")
        elif mode == "nan":
            self._send(200, b'{"output":{"value":NaN}}')
        elif mode == "wrong_reconcile_action":
            self._send(202, b'{"status":"accepted"}')
        else:  # pragma: no cover - fixed mode table
            raise AssertionError(mode)

    def do_GET(self) -> None:  # noqa: N802
        with self.server.state.lock:
            self.server.state.gets.append(self.path)
        self._send(
            200,
            b'{"status":"succeeded","action_id":"wrong-action","output":{"value":7}}',
        )


def _spec():  # type: ignore[no-untyped-def]
    return workflow(
        "http-fault-probe",
        "1",
        [tool_step("call", TOOL, {}, approval_required=False)],
        schema_version="v6-light-workflow-v1",
    )


def _trial(mode: str) -> dict[str, Any]:
    state = State(mode)
    server = Server(state)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        executor = RemoteHttpToolExecutor()
        executor.register(
            RemoteHttpTool(
                TOOL,
                origin + "/execute",
                reconciliation_endpoint=(
                    origin + "/result" if mode == "wrong_reconcile_action" else None
                ),
            )
        )
        engine = WorkflowEngine(InMemoryRunJournal(), executor, {})
        initial = engine.run(_spec(), run_id=f"http-{mode}")
        final = (
            engine.resume(initial.run_id)
            if mode == "wrong_reconcile_action"
            else initial
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    expected = {
        "success": "SUCCEEDED",
        "client_error": "BLOCKED",
        "server_error": "RECONCILING",
        "malformed": "RECONCILING",
        "nan": "RECONCILING",
        "wrong_reconcile_action": "RECONCILING",
    }[mode]
    action_id = next(iter(final.state["actions"]))
    requirements = {
        "stable_final_status": final.status == expected,
        "one_execute_request": state.posts == [action_id],
        "no_blind_redispatch": len(state.posts) == 1,
        "query_only_when_declared": len(state.gets)
        == (1 if mode == "wrong_reconcile_action" else 0),
        "wrong_action_receipt_not_accepted": mode != "wrong_reconcile_action"
        or final.status == "RECONCILING",
    }
    return {
        "case": "X01",
        "variant": mode,
        "contract_result": "PASS" if all(requirements.values()) else "FAIL",
        "requirements": requirements,
        "expected_status": expected,
        "actual_status": final.status,
        "action_id": action_id,
        "posts": list(state.posts),
        "gets": list(state.gets),
    }


def _connection_failure() -> dict[str, Any]:
    state = State("success")
    server = Server(state)
    port = server.server_port
    server.server_close()
    executor = RemoteHttpToolExecutor()
    executor.register(
        RemoteHttpTool(TOOL, f"http://127.0.0.1:{port}/execute", timeout_seconds=1)
    )
    snapshot = WorkflowEngine(InMemoryRunJournal(), executor, {}).run(
        _spec(), run_id="http-connection-failure"
    )
    action_id = next(iter(snapshot.state["actions"]))
    requirements = {
        "result_is_unknown": snapshot.status == "RECONCILING",
        "one_stable_action": list(snapshot.state["actions"]) == [action_id],
    }
    return {
        "case": "X01",
        "variant": "connection_failure",
        "contract_result": "PASS" if all(requirements.values()) else "FAIL",
        "requirements": requirements,
        "actual_status": snapshot.status,
        "action_id": action_id,
    }


def collect() -> dict[str, object]:
    results = [_trial(mode) for mode in MODES]
    results.append(_connection_failure())
    return {
        "schema_version": "v6-loopback-http-fault-evidence-v1",
        "result": (
            "FAIL"
            if any(item["contract_result"] == "FAIL" for item in results)
            else "PASS"
        ),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = collect()
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
