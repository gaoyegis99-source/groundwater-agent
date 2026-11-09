from typing import Annotated, cast

from fastapi import Depends, Request

from ..composition import PlatformApplication
from ..errors import ResourceNotFoundError
from ..access.research_auth import SESSION_COOKIE, authenticate
from ..access import (
    AGENT_RUN_POLICY,
    SESSION_POLICY,
    WORKFLOW_RUN_POLICY,
    RequestIdentity,
    resolve_identity,
)


def platform_application(request: Request) -> PlatformApplication:
    return cast(PlatformApplication, request.app.state.platform)


def require_project(application: PlatformApplication, project_id: str) -> None:
    if project_id != application.settings.project_id:
        # Project existence is intentionally hidden from authenticated users
        # outside the configured project. Role failures inside the project are
        # handled separately as 403.
        raise ResourceNotFoundError("PROJECT_NOT_FOUND")


def require_supplied_project(request: Request) -> None:
    if not request.url.path.startswith("/api/v12/"):
        return
    application = platform_application(request)
    values = (
        request.query_params.getlist("projectId")
        + request.query_params.getlist("project_id")
        + request.headers.getlist("X-Project-ID")
    )
    for project_id in values:
        require_project(application, project_id)


def current_identity(request: Request) -> RequestIdentity:
    identity = getattr(request.state, "identity", None)
    if identity is None:
        application = platform_application(request)
        if application.settings.research_auth_required:
            identity = authenticate(
                application.database,
                application.settings.project_id,
                authorization=request.headers.get("Authorization", ""),
                session_id=request.cookies.get(SESSION_COOKIE, ""),
                csrf=request.headers.get("X-CSRF-Token", ""),
                require_csrf=request.method not in {"GET", "HEAD", "OPTIONS"},
            )
        else:
            identity = resolve_identity(request.headers, application.settings)
        request.state.identity = identity
    return identity


def require_session_boundary(
    request: Request,
    identity: Annotated[RequestIdentity, Depends(current_identity)],
    application: Annotated[PlatformApplication, Depends(platform_application)],
) -> None:
    SESSION_POLICY.enforce(identity, application, request.path_params)


def require_agent_boundary(
    request: Request,
    identity: Annotated[RequestIdentity, Depends(current_identity)],
    application: Annotated[PlatformApplication, Depends(platform_application)],
) -> None:
    AGENT_RUN_POLICY.enforce(identity, application, request.path_params)


def require_workflow_boundary(
    request: Request,
    identity: Annotated[RequestIdentity, Depends(current_identity)],
    application: Annotated[PlatformApplication, Depends(platform_application)],
) -> None:
    WORKFLOW_RUN_POLICY.enforce(identity, application, request.path_params)
