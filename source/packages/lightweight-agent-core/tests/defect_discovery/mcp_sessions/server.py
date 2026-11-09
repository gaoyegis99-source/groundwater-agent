"""Controlled MCP 2.x server for real stdio and Streamable HTTP probes."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3

import anyio
from mcp.server.mcpserver import MCPServer


def _record(
    ledger: Path, transport: str, tool: str, phase: str, action_id: str
) -> None:
    with sqlite3.connect(ledger, timeout=10) as connection:
        connection.execute(
            "INSERT INTO calls(transport, tool, phase, action_id) VALUES(?, ?, ?, ?)",
            (transport, tool, phase, action_id),
        )
        connection.commit()


def _server(ledger: Path, transport: str) -> MCPServer:
    server = MCPServer("v6-real-session-probe", version="1")

    @server.tool(name="structured", structured_output=True)
    def structured(value: int, action_id: str) -> dict[str, int | str]:
        _record(ledger, transport, "structured", "complete", action_id)
        return {"value": value, "action_id": action_id}

    @server.tool(name="plain", structured_output=False)
    def plain(value: int, action_id: str) -> str:
        _record(ledger, transport, "plain", "complete", action_id)
        return f"plain:{value}:{action_id}"

    @server.tool(name="fail", structured_output=False)
    def fail(action_id: str) -> str:
        _record(ledger, transport, "fail", "raised", action_id)
        raise ValueError("controlled MCP tool failure")

    @server.tool(name="slow", structured_output=True)
    async def slow(delay_seconds: float, action_id: str) -> dict[str, str]:
        _record(ledger, transport, "slow", "started", action_id)
        await anyio.sleep(delay_seconds)
        _record(ledger, transport, "slow", "complete", action_id)
        return {"action_id": action_id}

    @server.tool(name="disconnect", structured_output=False)
    def disconnect(action_id: str) -> str:
        _record(ledger, transport, "disconnect", "exiting", action_id)
        os._exit(23)

    return server


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=("stdio", "http"), required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    server = _server(args.ledger, args.transport)
    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        if args.port is None:
            raise ValueError("HTTP transport requires --port")
        server.run(
            transport="streamable-http",
            host="127.0.0.1",
            port=args.port,
            streamable_http_path="/mcp",
            stateless_http=True,
            json_response=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
