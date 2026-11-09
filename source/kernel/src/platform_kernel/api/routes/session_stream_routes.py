"""SSE route registrations for the Session API family."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request
from fastapi.responses import StreamingResponse

from ...composition import PlatformApplication
from ..dependencies import platform_application
from .session_contracts import PROBLEM_RESPONSES, SSE_RESPONSE
from .session_streams import events_response, preview_response


router = APIRouter(responses=PROBLEM_RESPONSES)


@router.get(
    "/api/v12/sessions/{sessionId}/preview",
    response_class=StreamingResponse,
    responses=SSE_RESPONSE,
    operation_id="kernel_preview",
)
async def preview(
    session_id: Annotated[str, Path(alias="sessionId")],
    request: Request,
    application: PlatformApplication = Depends(platform_application),
):
    return preview_response(application, session_id, request)


@router.get(
    "/api/v12/sessions/{sessionId}/events",
    response_class=StreamingResponse,
    responses=SSE_RESPONSE,
    operation_id="kernel_events",
)
async def events(
    session_id: Annotated[str, Path(alias="sessionId")],
    request: Request,
    last_event_id: Annotated[int | None, Header(alias="Last-Event-ID", ge=0)] = None,
    after_event_id: Annotated[int, Query(alias="afterEventId", ge=0)] = 0,
    application: PlatformApplication = Depends(platform_application),
):
    return events_response(
        application,
        session_id,
        request,
        last_event_id=last_event_id,
        after_event_id=after_event_id,
    )


__all__ = ("events", "preview", "router")
