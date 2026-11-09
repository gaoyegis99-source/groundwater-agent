import sqlite3

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from platform_protocol_contracts import encode_display_name_header

from ...composition import PlatformApplication
from ...errors import (
    AuthenticationRequiredError,
    AuthorizationDeniedError,
    KernelUnavailableError,
)
from ..dependencies import current_identity, platform_application
from ..identity import RequestIdentity
from ...access.research_auth import SESSION_COOKIE, authenticate
from ..schemas import (
    CapabilitiesResponse,
    HealthResponse,
    PROBLEM_RESPONSE,
    ReadinessResponse,
)
from ..wire import to_wire


router = APIRouter()


@router.api_route(
    "/internal/identity/verify",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
def verify_ingress_identity(
    request: Request,
    application: PlatformApplication = Depends(platform_application),
):
    """Authenticate one edge assertion for Nginx ``auth_request``.

    Gateway-supplied identity headers are derived exclusively from this response;
    untrusted request headers are never copied into a backend request.
    """

    settings = application.settings
    identity = authenticate(
        application.database,
        settings.project_id,
        authorization=request.headers.get("Authorization", ""),
        session_id=request.cookies.get(SESSION_COOKIE, ""),
        csrf=request.headers.get("X-CSRF-Token", ""),
        require_csrf=(
            request.headers.get("X-Original-Method", request.method)
            not in {"GET", "HEAD", "OPTIONS"}
        ),
    )
    subject = identity.subject
    display_name = identity.display_name
    roles = tuple(sorted(identity.roles))
    required_role = request.headers.get("X-Platform-Required-Role", "")
    if required_role:
        if required_role not in {"admin", "researcher", "viewer"}:
            raise AuthenticationRequiredError("AUTHENTICATION_REQUIRED")
        if required_role not in roles:
            raise AuthorizationDeniedError("ROLE_FORBIDDEN")
    return Response(
        status_code=204,
        headers={
            "X-Project-ID": settings.project_id,
            "X-Platform-Subject": subject,
            "X-Platform-Display-Name": encode_display_name_header(display_name),
            "X-Platform-Roles": ",".join(roles),
        },
    )


@router.get(
    "/internal/health/live",
    response_model=HealthResponse,
    operation_id="kernel_internal_liveness",
)
def liveness(application: PlatformApplication = Depends(platform_application)):
    try:
        application.check_local_store()
    except sqlite3.Error as error:
        raise KernelUnavailableError("KERNEL_NOT_READY") from error
    return {"status": "UP"}


@router.get(
    "/internal/health/ready",
    response_model=HealthResponse,
    operation_id="kernel_internal_readiness",
)
def readiness(
    application: PlatformApplication = Depends(platform_application),
):
    liveness(application)
    if not application.is_ready():
        raise KernelUnavailableError("KERNEL_NOT_READY")
    return {"status": "UP"}


@router.get(
    "/internal/health",
    response_model=HealthResponse,
    operation_id="kernel_internal_health",
)
def health(
    application: PlatformApplication = Depends(platform_application),
):
    return readiness(application)


@router.get(
    "/api/v12/health",
    response_model=HealthResponse,
    operation_id="kernel_public_liveness",
)
def public_liveness(
    application: PlatformApplication = Depends(platform_application),
):
    return liveness(application)


@router.get(
    "/api/v12/ready",
    response_model=ReadinessResponse,
    responses={
        401: PROBLEM_RESPONSE,
        403: PROBLEM_RESPONSE,
        503: {
            "model": ReadinessResponse,
            "description": "One or more required components are not ready",
        },
    },
    operation_id="kernel_public_readiness",
)
async def public_readiness(
    _identity: Annotated[RequestIdentity, Depends(current_identity)],
    application: PlatformApplication = Depends(platform_application),
):
    try:
        application.check_local_store()
    except sqlite3.Error as error:
        raise KernelUnavailableError("KERNEL_NOT_READY") from error
    report = await application.readiness_report()
    return JSONResponse(
        to_wire(report.payload), status_code=200 if report.ready else 503
    )


@router.get(
    "/api/v12/capabilities",
    response_model=CapabilitiesResponse,
    responses={401: PROBLEM_RESPONSE, 403: PROBLEM_RESPONSE},
    operation_id="kernel_capabilities",
)
def capabilities(
    identity: Annotated[RequestIdentity, Depends(current_identity)],
    application: PlatformApplication = Depends(platform_application),
):
    value = json.loads(application.capabilities_json)
    value["currentUser"] = identity.as_wire()
    return Response(
        content=json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        media_type="application/json",
    )
