from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from agents.testing import ScriptedModel
from fastapi.testclient import TestClient
import httpx
from platform_protocol_contracts import decode_display_name_header
import pytest

from platform_kernel.access.research_auth import bootstrap_admin, create_user, put_membership
from platform_kernel.access import research_auth
from platform_kernel.api import create_app
from platform_kernel.composition import PlatformApplication
from platform_kernel.errors import AuthenticationRequiredError


PASSWORD = "correct horse battery staple"


def _application(kernel_settings):
    settings = replace(kernel_settings(), research_auth_required=True)
    bootstrap_admin(settings.data_root / "kernel.sqlite3", "p", "alice", "张三", PASSWORD)
    return PlatformApplication(
        settings=settings,
        model=ScriptedModel([]),
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(503))
        ),
    )


def test_login_session_and_ingress_verifier_ignore_forged_headers(kernel_settings):
    with TestClient(create_app(application=_application(kernel_settings))) as client:
        login = client.post(
            "/api/v12/auth/login", json={"username": "alice", "password": PASSWORD}
        )
        response = client.get(
            "/internal/identity/verify",
            headers={"X-Platform-Subject": "forged", "X-Platform-Roles": "viewer"},
        )

    assert login.status_code == 200
    assert response.status_code == 204
    assert response.headers["x-project-id"] == "p"
    assert response.headers["x-platform-subject"] == login.json()["currentUser"]["subject"]
    assert decode_display_name_header(response.headers["x-platform-display-name"]) == "张三"
    assert response.headers["x-platform-roles"] == "admin"


def test_missing_or_revoked_session_is_rejected(kernel_settings):
    with TestClient(create_app(application=_application(kernel_settings))) as client:
        assert client.get("/internal/identity/verify").status_code == 401
        login = client.post(
            "/api/v12/auth/login", json={"username": "alice", "password": PASSWORD}
        )
        assert client.post(
            "/api/v12/auth/logout",
            headers={"X-CSRF-Token": login.headers["x-csrf-token"]},
        ).status_code == 204
        assert client.get("/internal/identity/verify").status_code == 401


def test_required_role_is_enforced_from_project_membership(kernel_settings):
    application = _application(kernel_settings)
    user = create_user(application.database, "viewer", "Viewer", PASSWORD)
    put_membership(application.database, "p", user["userId"], "viewer")
    with TestClient(create_app(application=application)) as client:
        assert client.post(
            "/api/v12/auth/login", json={"username": "viewer", "password": PASSWORD}
        ).status_code == 200
        denied = client.get(
            "/internal/identity/verify",
            headers={"X-Platform-Required-Role": "admin"},
        )
        allowed = client.get(
            "/internal/identity/verify",
            headers={"X-Platform-Required-Role": "viewer"},
        )

    assert denied.status_code == 403
    assert allowed.status_code == 204


def test_personal_api_token_authenticates_without_a_cookie(kernel_settings):
    with TestClient(create_app(application=_application(kernel_settings))) as client:
        login = client.post(
            "/api/v12/auth/login", json={"username": "alice", "password": PASSWORD}
        )
        created = client.post(
            "/api/v12/auth/tokens",
            headers={"X-CSRF-Token": login.headers["x-csrf-token"]},
            json={"name": "notebook", "expiresDays": 30},
        )
        listed = client.get("/api/v12/auth/tokens")
        token = created.json()["token"]
        client.cookies.clear()
        response = client.get(
            "/internal/identity/verify",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert created.status_code == 201
    assert listed.status_code == 200
    assert listed.json()["items"][0]["tokenId"] == created.json()["tokenId"]
    assert response.status_code == 204
    assert response.headers["x-platform-roles"] == "admin"


def test_cookie_writes_require_csrf_and_v11_is_gone(kernel_settings):
    with TestClient(create_app(application=_application(kernel_settings))) as client:
        login = client.post(
            "/api/v12/auth/login", json={"username": "alice", "password": PASSWORD}
        )
        denied = client.post(
            "/api/v12/auth/tokens", json={"name": "missing-csrf", "expiresDays": 30}
        )
        retired = client.post("/api/v11/sessions", json={})

    assert login.status_code == 200
    assert denied.status_code == 403
    assert retired.status_code == 410
    assert retired.json()["code"] == "API_VERSION_RETIRED"


def test_session_idle_timeout_and_token_revocation(kernel_settings, monkeypatch):
    application = _application(kernel_settings)
    start = datetime(2026, 11, 3, tzinfo=timezone.utc)
    monkeypatch.setattr(research_auth, "_now", lambda: start)
    _identity, session_id, csrf = research_auth.login(
        application.database, "p", "alice", PASSWORD
    )
    metadata, token = research_auth.create_token(
        application.database, _identity.subject, "automation", 30
    )

    monkeypatch.setattr(
        research_auth, "_now", lambda: start + timedelta(hours=12, seconds=1)
    )
    with pytest.raises(AuthenticationRequiredError):
        research_auth.authenticate(
            application.database,
            "p",
            session_id=session_id,
            csrf=csrf,
            require_csrf=True,
        )

    monkeypatch.setattr(research_auth, "_now", lambda: start + timedelta(hours=1))
    assert research_auth.authenticate(
        application.database, "p", authorization=f"Bearer {token}"
    ).subject == _identity.subject
    assert research_auth.revoke_token(
        application.database, _identity.subject, str(metadata["tokenId"])
    )
    with pytest.raises(AuthenticationRequiredError):
        research_auth.authenticate(
            application.database, "p", authorization=f"Bearer {token}"
        )


def test_admin_manages_users_and_members_with_403_and_404_boundaries(kernel_settings):
    with TestClient(create_app(application=_application(kernel_settings))) as client:
        login = client.post(
            "/api/v12/auth/login", json={"username": "alice", "password": PASSWORD}
        )
        csrf = {"X-CSRF-Token": login.headers["x-csrf-token"]}
        created = client.post(
            "/api/v12/admin/users",
            headers=csrf,
            json={"username": "viewer", "displayName": "Viewer", "password": PASSWORD},
        )
        membership = client.post(
            "/api/v12/projects/p/members",
            headers=csrf,
            json={"userId": created.json()["userId"], "role": "viewer"},
        )
        users = client.get("/api/v12/admin/users")
        members = client.get("/api/v12/projects/p/members")
        missing_project = client.get("/api/v12/projects/other/members")
        client.post("/api/v12/auth/logout", headers=csrf)
        client.post(
            "/api/v12/auth/login", json={"username": "viewer", "password": PASSWORD}
        )
        forbidden = client.get(
            "/api/v12/admin/users",
            headers={
                "X-Platform-Subject": "forged-admin",
                "X-Platform-Roles": "admin",
                "X-Project-ID": "p",
            },
        )

    assert created.status_code == 201
    assert membership.status_code == 201
    assert membership.json()["role"] == "viewer"
    assert users.status_code == 200
    assert {item["username"] for item in users.json()["items"]} == {
        "alice", "viewer"
    }
    assert members.status_code == 200
    assert {item["role"] for item in members.json()["items"]} == {
        "admin", "viewer"
    }
    assert missing_project.status_code == 404
    assert forbidden.status_code == 403


def test_valid_local_user_without_project_membership_gets_404(kernel_settings):
    application = _application(kernel_settings)
    create_user(application.database, "outside", "Outside", "simplepass")

    with TestClient(create_app(application=application)) as client:
        response = client.post(
            "/api/v12/auth/login",
            json={"username": "outside", "password": "simplepass"},
        )

    assert response.status_code == 404
    assert response.json()["code"] == "PROJECT_NOT_FOUND"
