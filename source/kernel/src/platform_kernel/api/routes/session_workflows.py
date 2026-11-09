"""Workflow-draft endpoints owned by the Session API family."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from fastapi.responses import Response

from ...composition import PlatformApplication
from ...errors import ConfirmationConflictError
from ..dependencies import platform_application
from ..schemas import WorkflowDraftResponse
from ..wire import to_wire
from .session_contracts import PROBLEM_RESPONSES


router = APIRouter(responses=PROBLEM_RESPONSES)


@router.get(
    "/api/v12/sessions/{sessionId}/workflow-drafts/{draftId}",
    response_model=WorkflowDraftResponse,
    operation_id="kernel_get_workflow_draft",
)
def workflow_draft(
    session_id: Annotated[str, Path(alias="sessionId")],
    draft_id: Annotated[str, Path(alias="draftId")],
    application: PlatformApplication = Depends(platform_application),
):
    return to_wire(application.interaction.workflow_draft(session_id, draft_id))


@router.get(
    "/api/v12/sessions/{sessionId}/workflow-drafts/{draftId}/export",
    response_model=WorkflowDraftResponse,
    operation_id="kernel_export_workflow_draft",
)
def export_workflow_draft(
    session_id: Annotated[str, Path(alias="sessionId")],
    draft_id: Annotated[str, Path(alias="draftId")],
    application: PlatformApplication = Depends(platform_application),
):
    draft = application.interaction.workflow_draft(session_id, draft_id)
    if draft["payload"] is None:
        raise ConfirmationConflictError("WORKFLOW_DRAFT_NOT_AVAILABLE")
    payload = to_wire(draft["payload"])
    return Response(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="workflow-draft-{draft["draft_id"]}.json"'
            )
        },
    )


__all__ = ("export_workflow_draft", "router", "workflow_draft")
