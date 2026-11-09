"""State-changing Session HTTP routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from ...composition import PlatformApplication
from ...interaction.access import update_session_metadata
from ...interaction.control_persistence import request_stop
from ..dependencies import current_identity, platform_application, require_project
from ..identity import RequestIdentity
from ..schemas import (
    ConfirmationRequest,
    ConfirmationRevisionRequest,
    ControlProposalDecisionRequest,
    MessageCreate,
    MessageReceiptResponse,
    RejectionRequest,
    SessionMetadataResponse,
    SessionResponse,
    SessionCreate,
    SessionUpdate,
    StopRequest,
    StopResponse,
    WorkflowRecoveryRequest,
)
from ..wire import to_wire
from .session_contracts import PROBLEM_RESPONSES, session_wire


router = APIRouter(responses=PROBLEM_RESPONSES)


@router.post(
    "/api/v12/sessions",
    response_model=SessionResponse,
    operation_id="kernel_create_session",
)
def create_session(
    request: SessionCreate,
    identity: Annotated[RequestIdentity, Depends(current_identity)],
    application: PlatformApplication = Depends(platform_application),
):
    require_project(application, request.project_id)
    return session_wire(application.interaction.create_session(identity.subject))


@router.patch(
    "/api/v12/sessions/{sessionId}",
    response_model=SessionMetadataResponse,
    operation_id="kernel_update_session",
)
def update_session(
    session_id: Annotated[str, Path(alias="sessionId")],
    update: SessionUpdate,
    identity: Annotated[RequestIdentity, Depends(current_identity)],
    application: Annotated[PlatformApplication, Depends(platform_application)],
):
    return to_wire(
        update_session_metadata(
            application.database,
            application.settings.project_id,
            session_id,
            identity.subject,
            title=update.title,
            archived=update.archived,
        )
    )


@router.post(
    "/api/v12/sessions/{sessionId}/stop",
    status_code=202,
    response_model=StopResponse,
    operation_id="kernel_stop_run",
)
def stop_run(
    session_id: Annotated[str, Path(alias="sessionId")],
    request: StopRequest,
    identity: Annotated[RequestIdentity, Depends(current_identity)],
    application: PlatformApplication = Depends(platform_application),
):
    return to_wire(
        request_stop(
            application.database,
            session_id,
            application.settings.project_id,
            request.request_id,
            request.agent_run_id,
            identity.subject,
        )
    )


@router.post(
    "/api/v12/sessions/{sessionId}/control-proposals/{proposalId}/confirm",
    status_code=202,
    response_model=SessionResponse,
    operation_id="kernel_confirm_control_proposal",
)
async def confirm_control_proposal(
    session_id: Annotated[str, Path(alias="sessionId")],
    proposal_id: Annotated[str, Path(alias="proposalId")],
    request: ControlProposalDecisionRequest,
    identity: Annotated[RequestIdentity, Depends(current_identity)],
    application: PlatformApplication = Depends(platform_application),
):
    return session_wire(
        await application.interaction.confirm_control_proposal(
            session_id,
            proposal_id,
            request.request_id,
            identity.subject,
        )
    )


@router.post(
    "/api/v12/sessions/{sessionId}/control-proposals/{proposalId}/dismiss",
    response_model=SessionResponse,
    operation_id="kernel_dismiss_control_proposal",
)
def dismiss_control_proposal(
    session_id: Annotated[str, Path(alias="sessionId")],
    proposal_id: Annotated[str, Path(alias="proposalId")],
    request: ControlProposalDecisionRequest,
    identity: Annotated[RequestIdentity, Depends(current_identity)],
    application: PlatformApplication = Depends(platform_application),
):
    return session_wire(
        application.interaction.dismiss_control_proposal(
            session_id,
            proposal_id,
            request.request_id,
            identity.subject,
        )
    )


@router.post(
    "/api/v12/sessions/{sessionId}/messages",
    status_code=202,
    response_model=MessageReceiptResponse,
    operation_id="kernel_create_message",
)
def create_message(
    session_id: Annotated[str, Path(alias="sessionId")],
    request: MessageCreate,
    identity: Annotated[RequestIdentity, Depends(current_identity)],
    application: PlatformApplication = Depends(platform_application),
):
    return to_wire(
        application.interaction.message(
            session_id,
            request.request_id,
            request.message,
            request.requested_mode,
            input_references=[
                item.model_dump(exclude_none=True) for item in request.input_references
            ],
            corrects_request_id=request.corrects_request_id,
            corrects_agent_run_id=request.corrects_agent_run_id,
            owner_subject=identity.subject,
        )
    )


@router.post(
    "/api/v12/sessions/{sessionId}/confirm",
    status_code=202,
    response_model=SessionResponse,
    operation_id="kernel_confirm",
)
async def confirm(
    session_id: Annotated[str, Path(alias="sessionId")],
    request: ConfirmationRequest,
    identity: Annotated[RequestIdentity, Depends(current_identity)],
    application: PlatformApplication = Depends(platform_application),
):
    return session_wire(
        await application.interaction.confirm(
            session_id, request.confirmation_id, identity.subject
        )
    )


@router.post(
    "/api/v12/sessions/{sessionId}/confirmations/{confirmationId}/revisions",
    status_code=201,
    response_model=SessionResponse,
    operation_id="kernel_revise_confirmation",
)
async def revise_confirmation(
    session_id: Annotated[str, Path(alias="sessionId")],
    confirmation_id: Annotated[str, Path(alias="confirmationId")],
    request: ConfirmationRevisionRequest,
    identity: Annotated[RequestIdentity, Depends(current_identity)],
    application: PlatformApplication = Depends(platform_application),
):
    return session_wire(
        await application.interaction.revise_confirmation(
            session_id,
            confirmation_id,
            request.request_id,
            request.expected_goal_revision,
            tuple(item.model_dump() for item in request.actions),
            identity.subject,
        )
    )


@router.post(
    "/api/v12/sessions/{sessionId}/workflow-runs/{workflowRunId}/recoveries",
    status_code=201,
    response_model=SessionResponse,
    operation_id="kernel_recover_workflow",
)
def recover_workflow(
    session_id: Annotated[str, Path(alias="sessionId")],
    workflow_run_id: Annotated[str, Path(alias="workflowRunId")],
    request: WorkflowRecoveryRequest,
    identity: Annotated[RequestIdentity, Depends(current_identity)],
    application: PlatformApplication = Depends(platform_application),
):
    return session_wire(
        application.interaction.recover_workflow(
            session_id,
            workflow_run_id,
            request.request_id,
            request.expected_goal_revision,
            identity.subject,
        )
    )


@router.post(
    "/api/v12/sessions/{sessionId}/reject",
    response_model=SessionResponse,
    operation_id="kernel_reject",
)
async def reject(
    session_id: Annotated[str, Path(alias="sessionId")],
    request: RejectionRequest,
    identity: Annotated[RequestIdentity, Depends(current_identity)],
    application: PlatformApplication = Depends(platform_application),
):
    return session_wire(
        await application.interaction.reject(
            session_id, request.confirmation_id, request.reason, identity.subject
        )
    )


__all__ = (
    "confirm",
    "confirm_control_proposal",
    "create_message",
    "create_session",
    "dismiss_control_proposal",
    "recover_workflow",
    "reject",
    "revise_confirmation",
    "router",
    "stop_run",
    "update_session",
)
