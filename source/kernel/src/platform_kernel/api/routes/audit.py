"""Read-only, content-free audit feed for project administrators."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ...composition import PlatformApplication
from ...interaction.audit import list_audit_events as audit_events
from ...access import AUDIT_POLICY, RequestIdentity
from ..dependencies import current_identity, platform_application
from ..schemas import AuditEventPage, PROBLEM_RESPONSE
from ..wire import to_wire


router = APIRouter(responses={401: PROBLEM_RESPONSE, 403: PROBLEM_RESPONSE})


@router.get(
    "/api/v12/audit/events",
    response_model=AuditEventPage,
    operation_id="kernel_list_audit_events",
)
def list_audit_events(
    identity: Annotated[RequestIdentity, Depends(current_identity)],
    application: Annotated[PlatformApplication, Depends(platform_application)],
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    AUDIT_POLICY.enforce(identity, application, {})
    return to_wire(
        audit_events(
            application.database,
            application.settings.project_id,
            cursor,
            limit,
        )
    )
