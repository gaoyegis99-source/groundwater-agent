"""Exercise V6's MCP adapter through real MCP 2.x stdio and HTTP sessions."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
from threading import Thread
import time
from typing import Any

import anyio
import httpx2
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from platform_lightweight_agent import (
    McpToolDefinition,
    McpToolExecutor,
    ToolExecutionRequest,
    ToolExecutionUncertainError,
    ToolReference,
)


HERE = Path(__file__).resolve().parent
PACKAGE_ROOT = HERE.parents[2]
SERVER = HERE / "server.py"
TOOL = ToolReference("probe.real-mcp", "1", "sha256:" + "8" * 64)


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


class OfficialSessionBridge:
    """Narrow host bridge backed by the official MCP client core."""

    def __init__(
        self,
        transport: str,
        ledger: Path,
        *,
        url: str | None = None,
        timeout_seconds: float | None = None,
        stderr: Path | None = None,
    ) -> None:
        self.transport = transport
        self.ledger = ledger
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.stderr = stderr

    def call_tool(
        self,
        *,
        server_id: str,
        tool_name: str,
        arguments: Any,
        action_id: str,
    ) -> dict[str, object]:
        if server_id != f"real-{self.transport}":
            raise ValueError("unexpected frozen MCP server identity")
        return anyio.run(self._call, tool_name, dict(arguments), action_id)

    async def _call(
        self, tool_name: str, arguments: dict[str, object], action_id: str
    ) -> dict[str, object]:
        values = {**arguments, "action_id": action_id}
        if self.transport == "stdio":
            parameters = StdioServerParameters(
                command=sys.executable,
                args=[
                    str(SERVER),
                    "--transport",
                    "stdio",
                    "--ledger",
                    str(self.ledger),
                ],
                env=_environment(),
                cwd=PACKAGE_ROOT,
            )
            stderr_context: Any = (
                self.stderr.open("a", encoding="utf-8")
                if self.stderr is not None
                else nullcontext(sys.stderr)
            )
            with stderr_context as errlog:
                async with stdio_client(parameters, errlog=errlog) as streams:
                    return await self._session_call(streams, tool_name, values)
        if self.url is None:
            raise ValueError("HTTP bridge requires a URL")
        # This is a controlled loopback session.  Ignore ambient host proxy
        # settings so the probe neither depends on nor contacts a proxy.
        async with httpx2.AsyncClient(trust_env=False) as http_client:
            async with streamable_http_client(
                self.url, http_client=http_client
            ) as streams:
                return await self._session_call(streams, tool_name, values)

    async def _session_call(
        self, streams: Any, tool_name: str, values: dict[str, object]
    ) -> dict[str, object]:
        read_stream, write_stream = streams
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            if self.timeout_seconds is None:
                result = await session.call_tool(tool_name, values)
            else:
                with anyio.fail_after(self.timeout_seconds):
                    result = await session.call_tool(tool_name, values)
        return result.model_dump(by_alias=True, exclude_none=True)

    def reconcile_tool(self, **_: object) -> None:
        return None


def _execute(
    bridge: OfficialSessionBridge,
    tool_name: str,
    action_id: str,
    arguments: dict[str, object],
    *,
    semantics: str = "uncertain",
) -> Any:
    executor = McpToolExecutor(bridge)
    executor.register(
        McpToolDefinition(
            TOOL,
            f"real-{bridge.transport}",
            tool_name,
            server_version="2.3.0",
            tool_error_semantics=semantics,
        )
    )
    return executor.execute(ToolExecutionRequest(action_id, TOOL, arguments))


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_http_ready(process: subprocess.Popen[str], port: int) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise RuntimeError(
                f"MCP HTTP server exited early: {stdout[-1000:]!r} {stderr[-2000:]!r}"
            )
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.01)
    raise TimeoutError("MCP HTTP server did not accept connections")


def _kill_owned(process: subprocess.Popen[str]) -> int:
    cmdline = Path(f"/proc/{process.pid}/cmdline")
    if not cmdline.is_file() or str(SERVER).encode() not in cmdline.read_bytes():
        raise RuntimeError("refusing to kill a process not owned by the MCP probe")
    os.kill(process.pid, signal.SIGKILL)
    return process.wait(timeout=5)


def _wait_ledger(
    ledger: Path,
    transport: str,
    tool: str,
    phase: str,
    *,
    action_id: str | None = None,
    timeout: float = 10,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with sqlite3.connect(ledger) as connection:
            found = connection.execute(
                "SELECT 1 FROM calls WHERE transport=? AND tool=? AND phase=? "
                "AND (? IS NULL OR action_id=?) LIMIT 1",
                (transport, tool, phase, action_id, action_id),
            ).fetchone()
        if found is not None:
            return
        time.sleep(0.01)
    raise TimeoutError(f"MCP ledger barrier not reached: {transport}/{tool}/{phase}")


def _transport_matrix(
    transport: str,
    ledger: Path,
    *,
    url: str | None,
    stderr: Path,
    http_process: subprocess.Popen[str] | None,
) -> dict[str, object]:
    bridge = OfficialSessionBridge(
        transport, ledger, url=url, stderr=stderr
    )
    structured_id = f"{transport}-structured"
    structured = _execute(
        bridge, "structured", structured_id, {"value": 7}
    )
    plain_id = f"{transport}-plain"
    plain = _execute(bridge, "plain", plain_id, {"value": 8})
    known_id = f"{transport}-known-error"
    known = _execute(
        bridge, "fail", known_id, {}, semantics="known_failure"
    )
    uncertain_id = f"{transport}-uncertain-error"
    uncertain_error: str | None = None
    try:
        _execute(bridge, "fail", uncertain_id, {})
    except ToolExecutionUncertainError as error:
        uncertain_error = str(error)

    timeout_id = f"{transport}-cancelled"
    timeout_bridge = OfficialSessionBridge(
        transport,
        ledger,
        url=url,
        timeout_seconds=0.05,
        stderr=stderr,
    )
    timeout_error: str | None = None
    try:
        _execute(
            timeout_bridge,
            "slow",
            timeout_id,
            {"delay_seconds": 30.0},
        )
    except ToolExecutionUncertainError as error:
        timeout_error = str(error)
    _wait_ledger(
        ledger, transport, "slow", "started", action_id=timeout_id
    )

    disconnect_id = f"{transport}-disconnect"
    disconnect_error: str | None = None
    killed_exit: int | None = None
    if transport == "stdio":
        try:
            _execute(bridge, "disconnect", disconnect_id, {})
        except ToolExecutionUncertainError as error:
            disconnect_error = str(error)
    else:
        assert http_process is not None
        outcome: dict[str, object] = {}

        def invoke() -> None:
            try:
                outcome["result"] = _execute(
                    bridge,
                    "slow",
                    disconnect_id,
                    {"delay_seconds": 30.0},
                )
            except BaseException as error:  # noqa: BLE001 - cross-thread observation
                outcome["error"] = error

        thread = Thread(target=invoke)
        thread.start()
        _wait_ledger(
            ledger, transport, "slow", "started", action_id=disconnect_id
        )
        killed_exit = _kill_owned(http_process)
        thread.join(timeout=10)
        if thread.is_alive():
            raise TimeoutError("HTTP MCP disconnect call did not return")
        error = outcome.get("error")
        if isinstance(error, ToolExecutionUncertainError):
            disconnect_error = str(error)

    requirements = {
        "structured_content_preferred": structured.succeeded
        and structured.output == {"value": 7, "action_id": structured_id},
        "plain_content_preserved": plain.succeeded
        and isinstance(plain.output, dict)
        and isinstance(plain.output.get("content"), list)
        and plain_id in json.dumps(plain.output, sort_keys=True),
        "known_is_error_is_failure": known.succeeded is False
        and known.error == "MCP_TOOL_ERROR",
        "uncertain_is_error_fails_closed": uncertain_error is not None,
        "client_cancellation_is_unknown_not_success": timeout_error is not None,
        "disconnect_is_unknown_not_success": disconnect_error is not None,
        "owned_http_process_was_killed": transport != "http"
        or killed_exit == -signal.SIGKILL,
    }
    return {
        "case": "X02",
        "variant": f"real_mcp_{transport}_session",
        "contract_result": "PASS" if all(requirements.values()) else "FAIL",
        "requirements": requirements,
        "action_ids": {
            "structured": structured_id,
            "plain": plain_id,
            "known_error": known_id,
            "uncertain_error": uncertain_id,
            "cancelled": timeout_id,
            "disconnect": disconnect_id,
        },
        "structured_output": structured.output,
        "plain_output": plain.output,
        "known_error": known.error,
        "uncertain_error": uncertain_error,
        "timeout_error": timeout_error,
        "disconnect_error": disconnect_error,
        "http_killed_exit": killed_exit,
    }


def collect(root: Path) -> dict[str, object]:
    root.mkdir(parents=True, exist_ok=False)
    ledger = root / "mcp-ledger.sqlite"
    with sqlite3.connect(ledger) as connection:
        connection.execute(
            "CREATE TABLE calls(sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
            "transport TEXT NOT NULL, tool TEXT NOT NULL, phase TEXT NOT NULL, "
            "action_id TEXT NOT NULL)"
        )
    results = [
        _transport_matrix(
            "stdio",
            ledger,
            url=None,
            stderr=root / "stdio-server.stderr",
            http_process=None,
        )
    ]
    port = _free_port()
    command = [
        sys.executable,
        str(SERVER),
        "--transport",
        "http",
        "--ledger",
        str(ledger),
        "--port",
        str(port),
    ]
    process = subprocess.Popen(
        command,
        cwd=PACKAGE_ROOT,
        env=_environment(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_http_ready(process, port)
        results.append(
            _transport_matrix(
                "http",
                ledger,
                url=f"http://127.0.0.1:{port}/mcp",
                stderr=root / "http-client.stderr",
                http_process=process,
            )
        )
    finally:
        if process.poll() is None:
            _kill_owned(process)
        stdout, stderr = process.communicate()
        (root / "http-server.stdout").write_text(stdout, encoding="utf-8")
        (root / "http-server.stderr").write_text(stderr, encoding="utf-8")
    with sqlite3.connect(ledger) as connection:
        calls = [
            {
                "sequence": row[0],
                "transport": row[1],
                "tool": row[2],
                "phase": row[3],
                "action_id": row[4],
            }
            for row in connection.execute(
                "SELECT sequence, transport, tool, phase, action_id "
                "FROM calls ORDER BY sequence"
            )
        ]
    action_ids = {
        action_id
        for result in results
        for action_id in result["action_ids"].values()
    }
    recorded_ids = {str(item["action_id"]) for item in calls}
    identity_requirement = action_ids.issubset(recorded_ids)
    for result in results:
        result["requirements"]["action_id_reaches_real_server"] = (
            identity_requirement
        )
        if not identity_requirement:
            result["contract_result"] = "FAIL"
    return {
        "schema_version": "v6-real-mcp-session-evidence-v1",
        "result": (
            "FAIL"
            if any(item["contract_result"] == "FAIL" for item in results)
            else "PASS"
        ),
        "environment": {
            "python": sys.version,
            "mcp_distribution": "2.3.0",
            "network": "loopback only",
        },
        "results": results,
        "server_calls": calls,
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
            "schema_version": "v6-real-mcp-session-evidence-v1",
            "result": "ERROR",
            "results": [
                {
                    "case": "X02",
                    "variant": "real_mcp_stdio_and_http_sessions",
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
