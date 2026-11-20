"""Replay WorkItem activity and periodically invite an authority refresh."""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import Request
from fastapi.responses import StreamingResponse

from ...access import RequestIdentity
from ...composition import PlatformApplication
from ...errors import ResourceNotFoundError
from ..wire import to_wire


_BATCH_SIZE = 100
_STREAM_SECONDS = 25
_REFRESH_SECONDS = 10

WORK_ITEM_EVENT_RESPONSES: dict[int | str, dict] = {
    200: {
        "content": {
            "text/event-stream": {
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "event": {"enum": ["work_item.activity", "work_item.refresh", "work_item.reset"]},
                        "id": {"type": ["integer", "null"]},
                        "data": {"oneOf": [
                            {"$ref": "#/components/schemas/WorkItemCommandActivity"},
                            {"$ref": "#/components/schemas/WorkItemSessionActivity"},
                            {"type": "object", "maxProperties": 0},
                        ]},
                    },
                    "required": ["event", "data"],
                }
            }
        }
    }
}


async def events_response(
    application: PlatformApplication, work_item_id: str, actor: RequestIdentity,
    request: Request, after: int,
) -> StreamingResponse:
    generation = application.commit_signal.generation
    first = await asyncio.to_thread(application.work_items.activity_batch, work_item_id, actor, after, _BATCH_SIZE)
    application.record_sse_connection(reconnect=after > 0)

    async def stream():
        deadline = time.monotonic() + _STREAM_SECONDS
        refresh_at = time.monotonic() + _REFRESH_SECONDS
        cursor = after
        head, entries = first
        signal = generation
        while time.monotonic() < deadline and not await request.is_disconnected():
            if cursor > head:
                yield "event: work_item.reset\ndata: {}\n\n"
                return
            for entry in entries:
                cursor = entry["sequence"]
                payload = json.dumps(to_wire(entry), ensure_ascii=False, separators=(",", ":"))
                yield f"id: {cursor}\nevent: work_item.activity\ndata: {payload}\n\n"
            if time.monotonic() >= refresh_at:
                yield "event: work_item.refresh\ndata: {}\n\n"
                refresh_at = time.monotonic() + _REFRESH_SECONDS
            elif not entries:
                yield ": heartbeat\n\n"
            if cursor >= head:
                signal = await application.commit_signal.wait(signal, 1)
            try:
                head, entries = await asyncio.to_thread(
                    application.work_items.activity_batch, work_item_id, actor, cursor, _BATCH_SIZE,
                )
            except ResourceNotFoundError:
                # A restored copy may not have this WorkItem anymore.
                yield "event: work_item.reset\ndata: {}\n\n"
                return

    return StreamingResponse(
        stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
