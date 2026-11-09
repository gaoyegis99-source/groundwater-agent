"""Stable imported tool identity used by mixed real-process recovery probes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3

import anyio
from platform_lightweight_agent import ToolReference


TOOL = ToolReference("probe.mixed-recovery", "1", "sha256:" + "5" * 64)


async def effect(arguments, context):  # type: ignore[no-untyped-def]
    """Commit one external effect, expose a durable barrier, then stay live."""

    ledger = Path(os.environ["V6_MIXED_LEDGER"])
    barrier = Path(os.environ["V6_MIXED_BARRIER"])
    with sqlite3.connect(ledger, timeout=10) as connection:
        connection.execute(
            "INSERT INTO effects(action_id, value) VALUES(?, ?)",
            (context.action_id, int(arguments["value"])),
        )
        connection.commit()
    temporary = barrier.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "action_id": context.action_id,
                "value": int(arguments["value"]),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    os.replace(temporary, barrier)
    while True:
        await anyio.sleep(1)


def register(executor) -> None:  # type: ignore[no-untyped-def]
    executor.register(TOOL, effect)
