"""Stable Session route facade.

HTTP paths and endpoint symbols remain here for callers that import the historic
module.  Query, command, workflow, and SSE registrations live in their own
responsibility modules so the facade carries no request-side implementation.
"""

from __future__ import annotations

from fastapi import APIRouter

from .session_commands import (
    confirm,
    confirm_control_proposal,
    create_message,
    create_session,
    dismiss_control_proposal,
    recover_workflow,
    reject,
    revise_confirmation,
    router as command_router,
    stop_run,
    update_session,
)
from .session_contracts import PROBLEM_RESPONSES
from .session_queries import (
    get_messages,
    get_session,
    get_session_preparation,
    get_session_results,
    list_sessions,
    router as query_router,
    task,
)
from .session_stream_routes import events, preview, router as stream_router
from .session_workflows import (
    export_workflow_draft,
    router as workflow_router,
    workflow_draft,
)


router = APIRouter(responses=PROBLEM_RESPONSES)
router.include_router(query_router)
router.include_router(command_router)
router.include_router(workflow_router)
router.include_router(stream_router)


__all__ = (
    "confirm",
    "confirm_control_proposal",
    "create_message",
    "create_session",
    "dismiss_control_proposal",
    "events",
    "export_workflow_draft",
    "get_messages",
    "get_session",
    "get_session_preparation",
    "get_session_results",
    "list_sessions",
    "preview",
    "recover_workflow",
    "reject",
    "revise_confirmation",
    "router",
    "stop_run",
    "task",
    "update_session",
    "workflow_draft",
)
