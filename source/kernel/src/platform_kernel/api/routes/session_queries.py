"""Read-only Session HTTP routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from ...composition import PlatformApplication
from ...tasks import get_preparation, get_task
from ...interaction.api_projection import session_response
from ..dependencies import current_identity, platform_application
from ..identity import RequestIdentity
from ..schemas import (
    MessageHistoryResponse,
    PreparationResponse,
    SessionDetailResponse,
    SessionPage,
    SessionResultsResponse,
    TaskResponse,
)
from ..wire import to_wire
from .session_contracts import PROBLEM_RESPONSES


router = APIRouter(responses=PROBLEM_RESPONSES)


@router.get(
    "/api/v12/sessions", response_model=SessionPage, operation_id="kernel_list_sessions"
)
def list_sessions(
    application: Annotated[PlatformApplication, Depends(platform_application)],
    identity: Annotated[RequestIdentity, Depends(current_identity)],
    cursor: Annotated[int | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    query: Annotated[str, Query(max_length=120)] = "",
    archived: bool = False,
):
    return to_wire(
        application.interaction.list_sessions(
            cursor,
            limit,
            owner_subject=identity.subject,
            query=query.strip(),
            archived=archived,
        )
    )


@router.get(
    "/api/v12/sessions/{sessionId}/messages",
    response_model=MessageHistoryResponse,
    operation_id="kernel_get_messages",
)
def get_messages(
    session_id: Annotated[str, Path(alias="sessionId")],
    application: Annotated[PlatformApplication, Depends(platform_application)],
    cursor: Annotated[int | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return to_wire(application.interaction.messages(session_id, cursor, limit))


@router.get(
    "/api/v12/sessions/{sessionId}",
    response_model=SessionDetailResponse,
    operation_id="kernel_get_session",
)
def get_session(
    session_id: Annotated[str, Path(alias="sessionId")],
    application: PlatformApplication = Depends(platform_application),
):
    response = session_response(application.interaction.get(session_id))
    response["preparation"] = get_preparation(
        application.database, session_id, application.settings.project_id
    )
    return to_wire(response)


@router.get(
    "/api/v12/sessions/{sessionId}/preparation",
    response_model=PreparationResponse,
    operation_id="kernel_get_session_preparation",
)
def get_session_preparation(
    session_id: Annotated[str, Path(alias="sessionId")],
    application: PlatformApplication = Depends(platform_application),
):
    return to_wire(
        get_preparation(
            application.database, session_id, application.settings.project_id
        )
    )


@router.get(
    "/api/v12/sessions/{sessionId}/task",
    response_model=TaskResponse,
    operation_id="kernel_get_task",
)
def task(
    session_id: Annotated[str, Path(alias="sessionId")],
    application: PlatformApplication = Depends(platform_application),
):
    return to_wire(
        {
            "task": get_task(
                application.database, session_id, application.settings.project_id
            )
        }
    )


@router.get(
    "/api/v12/sessions/{sessionId}/results",
    response_model=SessionResultsResponse,
    operation_id="kernel_get_session_results",
)
def get_session_results(
    session_id: Annotated[str, Path(alias="sessionId")],
    application: Annotated[PlatformApplication, Depends(platform_application)],
    cursor: Annotated[int, Query(ge=0)] = 0,
):
    return to_wire(application.interaction.results(session_id, cursor))


__all__ = (
    "get_messages",
    "get_session",
    "get_session_preparation",
    "get_session_results",
    "list_sessions",
    "router",
    "task",
)
