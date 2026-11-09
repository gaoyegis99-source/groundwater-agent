"""SSE response builders kept separate from the Session command routes."""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import Request
from fastapi.responses import StreamingResponse

from ...composition import PlatformApplication
from ..wire import to_wire


def preview_response(
    application: PlatformApplication, session_id: str, request: Request
) -> StreamingResponse:
    application.interaction.get(session_id)

    async def stream():
        yield "event: preview.reset\ndata: {}\n\n"
        async with application.previews.subscribe(session_id) as queue:
            while not await request.is_disconnected():
                try:
                    value = await asyncio.wait_for(queue.get(), timeout=15)
                    yield (
                        "event: preview.update\ndata: "
                        + json.dumps(to_wire(value), ensure_ascii=False)
                        + "\n\n"
                    )
                except TimeoutError:
                    yield ": keepalive\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def events_response(
    application: PlatformApplication,
    session_id: str,
    request: Request,
    *,
    last_event_id: int | None,
    after_event_id: int,
) -> StreamingResponse:
    application.interaction.get(session_id)
    application.record_sse_connection(
        reconnect=last_event_id is not None or after_event_id > 0
    )

    async def stream():
        cursor = after_event_id if last_event_id is None else last_event_id
        deadline = time.monotonic() + 25
        generation = application.commit_signal.generation
        while time.monotonic() < deadline and not await request.is_disconnected():
            items = application.interaction.events_after(session_id, cursor)
            if items:
                for item in items:
                    cursor = int(item["event_id"])
                    payload = json.dumps(
                        {
                            "schemaVersion": "session-event.v20",
                            "type": item["event_type"],
                            "data": to_wire(item["data"]),
                            "createdAt": item["created_at"],
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    yield f"id: {cursor}\nevent: {item['event_type']}\ndata: {payload}\n\n"
            else:
                yield ": heartbeat\n\n"
            generation = await application.commit_signal.wait(generation, 1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
