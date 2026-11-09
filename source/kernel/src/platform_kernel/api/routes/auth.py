from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import Field

from platform_protocol_contracts import CamelModel

from ...access.identity import RequestIdentity, require_role
from ...access.research_auth import (
    SESSION_COOKIE,
    create_token,
    create_user,
    list_members,
    list_tokens,
    list_users,
    login,
    logout,
    patch_user,
    put_membership,
    revoke_token,
)
from ...errors import ResourceNotFoundError
from ..dependencies import current_identity, platform_application
from ...composition import PlatformApplication


router = APIRouter(prefix="/api/v12")


class LoginRequest(CamelModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=1024)


class TokenCreate(CamelModel):
    name: str = Field(min_length=1, max_length=120)
    expires_days: int = Field(default=90, alias="expiresDays", ge=1, le=365)


class UserCreate(CamelModel):
    username: str
    display_name: str = Field(alias="displayName")
    password: str


class UserPatch(CamelModel):
    display_name: str | None = Field(default=None, alias="displayName")
    enabled: bool | None = None


class MembershipPut(CamelModel):
    user_id: str = Field(alias="userId")
    role: Literal["admin", "researcher", "viewer"]


class MembershipPatch(CamelModel):
    role: Literal["admin", "researcher", "viewer"]


class IdentityResponse(CamelModel):
    subject: str
    display_name: str
    roles: list[str]


class LoginResponse(CamelModel):
    current_user: IdentityResponse
    csrf_token: str


class TokenResponse(CamelModel):
    token_id: str
    name: str
    created_at: str
    expires_at: str
    last_used_at: str | None = None
    token: str | None = None


class TokenPage(CamelModel):
    items: list[TokenResponse]


class UserResponse(CamelModel):
    user_id: str
    username: str
    display_name: str
    enabled: bool
    created_at: str | None = None
    role: Literal["admin", "researcher", "viewer"] | None = None


class UserPage(CamelModel):
    items: list[UserResponse]


class UpdatedUser(CamelModel):
    user_id: str
    updated: bool


class MembershipResponse(CamelModel):
    user_id: str
    role: Literal["admin", "researcher", "viewer"]


def _admin(
    identity: Annotated[RequestIdentity, Depends(current_identity)],
) -> RequestIdentity:
    require_role(identity, "admin")
    return identity


@router.post("/auth/login", response_model=LoginResponse)
def auth_login(
    body: LoginRequest,
    response: Response,
    application: PlatformApplication = Depends(platform_application),
):
    identity, session_id, csrf = login(
        application.database,
        application.settings.project_id,
        body.username,
        body.password,
    )
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        httponly=True,
        samesite="strict",
        max_age=7 * 86400,
        path="/",
    )
    response.headers["X-CSRF-Token"] = csrf
    return {"currentUser": identity.as_wire(), "csrfToken": csrf}


@router.post("/auth/logout", status_code=204)
def auth_logout(
    request: Request,
    response: Response,
    _identity: Annotated[RequestIdentity, Depends(current_identity)],
    application: PlatformApplication = Depends(platform_application),
):
    session_id = request.cookies.get(SESSION_COOKIE, "")
    if session_id:
        logout(application.database, session_id)
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/auth/me", response_model=IdentityResponse)
def auth_me(identity: Annotated[RequestIdentity, Depends(current_identity)]):
    return identity.as_wire()


@router.get("/auth/tokens", response_model=TokenPage)
def auth_tokens(
    identity: Annotated[RequestIdentity, Depends(current_identity)],
    application: PlatformApplication = Depends(platform_application),
):
    return {"items": list_tokens(application.database, identity.subject)}


@router.post("/auth/tokens", status_code=201, response_model=TokenResponse)
def auth_token_create(
    body: TokenCreate,
    identity: Annotated[RequestIdentity, Depends(current_identity)],
    application: PlatformApplication = Depends(platform_application),
):
    metadata, token = create_token(
        application.database, identity.subject, body.name, body.expires_days
    )
    return metadata | {"token": token}


@router.delete("/auth/tokens/{tokenId}", status_code=204)
def auth_token_delete(
    token_id: Annotated[str, Path(alias="tokenId")],
    identity: Annotated[RequestIdentity, Depends(current_identity)],
    application: PlatformApplication = Depends(platform_application),
):
    if not revoke_token(application.database, identity.subject, token_id):
        raise ResourceNotFoundError("TOKEN_NOT_FOUND")


@router.get("/admin/users", response_model=UserPage)
def admin_users(
    _identity: Annotated[RequestIdentity, Depends(_admin)],
    application: PlatformApplication = Depends(platform_application),
):
    return {"items": list_users(application.database)}


@router.post("/admin/users", status_code=201, response_model=UserResponse)
def admin_user_create(
    body: UserCreate,
    _identity: Annotated[RequestIdentity, Depends(_admin)],
    application: PlatformApplication = Depends(platform_application),
):
    return create_user(
        application.database, body.username, body.display_name, body.password
    )


@router.patch("/admin/users/{userId}", response_model=UpdatedUser)
def admin_user_patch(
    user_id: Annotated[str, Path(alias="userId")],
    body: UserPatch,
    _identity: Annotated[RequestIdentity, Depends(_admin)],
    application: PlatformApplication = Depends(platform_application),
):
    if not patch_user(
        application.database,
        user_id,
        display_name=body.display_name,
        enabled=body.enabled,
    ):
        raise ResourceNotFoundError("USER_NOT_FOUND")
    return {"userId": user_id, "updated": True}


@router.get("/projects/{projectId}/members", response_model=UserPage)
def project_members(
    project_id: Annotated[str, Path(alias="projectId")],
    _identity: Annotated[RequestIdentity, Depends(_admin)],
    application: PlatformApplication = Depends(platform_application),
):
    if project_id != application.settings.project_id:
        raise ResourceNotFoundError("PROJECT_NOT_FOUND")
    return {"items": list_members(application.database, project_id)}


@router.post(
    "/projects/{projectId}/members", status_code=201,
    response_model=MembershipResponse,
)
def project_member_create(
    project_id: Annotated[str, Path(alias="projectId")],
    body: MembershipPut,
    _identity: Annotated[RequestIdentity, Depends(_admin)],
    application: PlatformApplication = Depends(platform_application),
):
    if project_id != application.settings.project_id:
        raise ResourceNotFoundError("PROJECT_NOT_FOUND")
    try:
        put_membership(application.database, project_id, body.user_id, body.role)
    except LookupError as error:
        raise ResourceNotFoundError("USER_NOT_FOUND") from error
    return {"userId": body.user_id, "role": body.role}


@router.patch(
    "/projects/{projectId}/members/{userId}", response_model=MembershipResponse
)
def project_member_patch(
    project_id: Annotated[str, Path(alias="projectId")],
    user_id: Annotated[str, Path(alias="userId")],
    body: MembershipPatch,
    _identity: Annotated[RequestIdentity, Depends(_admin)],
    application: PlatformApplication = Depends(platform_application),
):
    if project_id != application.settings.project_id:
        raise ResourceNotFoundError("PROJECT_NOT_FOUND")
    try:
        put_membership(application.database, project_id, user_id, body.role)
    except LookupError as error:
        raise ResourceNotFoundError("USER_NOT_FOUND") from error
    return {"userId": user_id, "role": body.role}
