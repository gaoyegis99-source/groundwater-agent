"""Local research identity, sessions, and personal API tokens."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from pathlib import Path
import re
import secrets
import sqlite3
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from ..database import connect_database, utc_now
from ..errors import (
    AuthenticationRequiredError,
    AuthorizationDeniedError,
    ResourceNotFoundError,
)
from .identity import RequestIdentity


RESEARCH_ROLES = frozenset({"admin", "researcher", "viewer"})
SESSION_COOKIE = "gw_research_session"
_USERNAME = re.compile(r"[a-z][a-z0-9_.-]{2,63}")
_PASSWORD_HASHER = PasswordHasher()
_IDLE = timedelta(hours=12)
_ABSOLUTE = timedelta(days=7)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _wire_time(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_username(value: str) -> str:
    normalized = value.strip().lower()
    if _USERNAME.fullmatch(normalized) is None:
        raise ValueError("USERNAME_INVALID")
    return normalized


def validate_password(value: str) -> str:
    # This profile is for a loopback/small-lab deployment. Argon2id, login
    # throttling at the Gateway boundary and revocable sessions provide the
    # durable controls; a twelve-character composition gate only made local
    # bootstrap needlessly awkward. Keep a small floor to reject accidental
    # empty/trivial values without imposing enterprise password policy.
    if len(value) < 8 or len(value.encode("utf-8")) > 1024:
        raise ValueError("PASSWORD_INVALID")
    return value


def bootstrap_admin(
    database: Path, project_id: str, username: str, display_name: str, password: str
) -> str:
    username = validate_username(username)
    validate_password(password)
    display_name = display_name.strip()
    if not display_name or len(display_name.encode("utf-8")) > 160:
        raise ValueError("DISPLAY_NAME_INVALID")
    user_id = str(uuid4())
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM research_users LIMIT 1").fetchone():
            raise ValueError("ADMIN_ALREADY_BOOTSTRAPPED")
        connection.execute(
            "INSERT INTO research_users VALUES (?,?,?,?,1,?)",
            (
                user_id,
                username,
                display_name,
                _PASSWORD_HASHER.hash(password),
                utc_now(),
            ),
        )
        connection.execute(
            "INSERT INTO research_project_memberships VALUES (?,?, 'admin')",
            (project_id, user_id),
        )
    return user_id


def create_user(
    database: Path, username: str, display_name: str, password: str
) -> dict[str, object]:
    username = validate_username(username)
    validate_password(password)
    display_name = display_name.strip()
    if not display_name or len(display_name.encode("utf-8")) > 160:
        raise ValueError("DISPLAY_NAME_INVALID")
    user_id = str(uuid4())
    with connect_database(database) as connection:
        try:
            connection.execute(
                "INSERT INTO research_users VALUES (?,?,?,?,1,?)",
                (
                    user_id,
                    username,
                    display_name,
                    _PASSWORD_HASHER.hash(password),
                    utc_now(),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError("USERNAME_CONFLICT") from error
    return {
        "userId": user_id,
        "username": username,
        "displayName": display_name,
        "enabled": True,
    }


def list_users(database: Path) -> list[dict[str, object]]:
    with connect_database(database) as connection:
        rows = connection.execute(
            "SELECT user_id,username,display_name,enabled,created_at FROM research_users ORDER BY username"
        ).fetchall()
    return [
        {
            "userId": row["user_id"],
            "username": row["username"],
            "displayName": row["display_name"],
            "enabled": bool(row["enabled"]),
            "createdAt": row["created_at"],
        }
        for row in rows
    ]


def patch_user(
    database: Path, user_id: str, *, display_name: str | None, enabled: bool | None
) -> bool:
    changes: list[str] = []
    values: list[object] = []
    if display_name is not None:
        display_name = display_name.strip()
        if not display_name or len(display_name.encode("utf-8")) > 160:
            raise ValueError("DISPLAY_NAME_INVALID")
        changes.append("display_name=?")
        values.append(display_name)
    if enabled is not None:
        changes.append("enabled=?")
        values.append(int(enabled))
    if not changes:
        raise ValueError("USER_CHANGE_REQUIRED")
    values.append(user_id)
    with connect_database(database) as connection:
        result = connection.execute(
            f"UPDATE research_users SET {','.join(changes)} WHERE user_id=?", values
        )
        if enabled is False:
            connection.execute(
                "UPDATE research_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                (utc_now(), user_id),
            )
            connection.execute(
                "UPDATE research_api_tokens SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                (utc_now(), user_id),
            )
    return result.rowcount == 1


def put_membership(database: Path, project_id: str, user_id: str, role: str) -> None:
    if role not in RESEARCH_ROLES:
        raise ValueError("ROLE_INVALID")
    with connect_database(database) as connection:
        try:
            connection.execute(
                """INSERT INTO research_project_memberships VALUES (?,?,?)
                   ON CONFLICT(project_id,user_id) DO UPDATE SET role=excluded.role""",
                (project_id, user_id, role),
            )
        except sqlite3.IntegrityError as error:
            raise LookupError("USER_NOT_FOUND") from error


def list_members(database: Path, project_id: str) -> list[dict[str, object]]:
    with connect_database(database) as connection:
        rows = connection.execute(
            """SELECT u.user_id,u.username,u.display_name,u.enabled,m.role
               FROM research_project_memberships m JOIN research_users u ON u.user_id=m.user_id
               WHERE m.project_id=? ORDER BY u.username""",
            (project_id,),
        ).fetchall()
    return [
        {
            "userId": row["user_id"],
            "username": row["username"],
            "displayName": row["display_name"],
            "enabled": bool(row["enabled"]),
            "role": row["role"],
        }
        for row in rows
    ]


def login(
    database: Path, project_id: str, username: str, password: str
) -> tuple[RequestIdentity, str, str]:
    username = validate_username(username)
    with connect_database(database) as connection:
        row = connection.execute(
            """SELECT u.*,m.role FROM research_users u
               LEFT JOIN research_project_memberships m
                 ON m.user_id=u.user_id AND m.project_id=?
               WHERE u.username=?""",
            (project_id, username),
        ).fetchone()
        if row is None or not row["enabled"]:
            raise AuthenticationRequiredError("AUTHENTICATION_REQUIRED")
        try:
            _PASSWORD_HASHER.verify(row["password_hash"], password)
        except (VerifyMismatchError, InvalidHashError):
            raise AuthenticationRequiredError("AUTHENTICATION_REQUIRED") from None
        if row["role"] is None:
            raise ResourceNotFoundError("PROJECT_NOT_FOUND")
        if _PASSWORD_HASHER.check_needs_rehash(row["password_hash"]):
            connection.execute(
                "UPDATE research_users SET password_hash=? WHERE user_id=?",
                (_PASSWORD_HASHER.hash(password), row["user_id"]),
            )
        session_id = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        now = _now()
        connection.execute(
            "INSERT INTO research_sessions VALUES (?,?,?,?,?,?,?,NULL)",
            (
                _digest(session_id),
                row["user_id"],
                _digest(csrf),
                _wire_time(now),
                _wire_time(now),
                _wire_time(now + _IDLE),
                _wire_time(now + _ABSOLUTE),
            ),
        )
    return _identity(row), session_id, csrf


def logout(database: Path, session_id: str) -> None:
    with connect_database(database) as connection:
        connection.execute(
            "UPDATE research_sessions SET revoked_at=? WHERE session_id=? AND revoked_at IS NULL",
            (utc_now(), _digest(session_id)),
        )


def authenticate(
    database: Path,
    project_id: str,
    *,
    authorization: str = "",
    session_id: str = "",
    csrf: str = "",
    require_csrf: bool = False,
) -> RequestIdentity:
    if authorization.startswith("Bearer "):
        return _authenticate_token(database, project_id, authorization[7:])
    if not session_id:
        raise AuthenticationRequiredError("AUTHENTICATION_REQUIRED")
    now = _now()
    with connect_database(database) as connection:
        row = connection.execute(
            """SELECT u.*,m.role,s.csrf_hash,s.idle_expires_at,s.absolute_expires_at
               FROM research_sessions s
               JOIN research_users u ON u.user_id=s.user_id
               LEFT JOIN research_project_memberships m
                 ON m.user_id=u.user_id AND m.project_id=?
               WHERE s.session_id=? AND s.revoked_at IS NULL""",
            (project_id, _digest(session_id)),
        ).fetchone()
        if (
            row is None
            or not row["enabled"]
            or _parse_time(row["idle_expires_at"]) <= now
            or _parse_time(row["absolute_expires_at"]) <= now
        ):
            raise AuthenticationRequiredError("AUTHENTICATION_REQUIRED")
        if row["role"] is None:
            raise ResourceNotFoundError("PROJECT_NOT_FOUND")
        if require_csrf and not hmac.compare_digest(row["csrf_hash"], _digest(csrf)):
            raise AuthorizationDeniedError("CSRF_REQUIRED")
        connection.execute(
            "UPDATE research_sessions SET last_seen_at=?,idle_expires_at=? WHERE session_id=?",
            (
                _wire_time(now),
                _wire_time(min(now + _IDLE, _parse_time(row["absolute_expires_at"]))),
                _digest(session_id),
            ),
        )
    return _identity(row)


def _authenticate_token(database: Path, project_id: str, token: str) -> RequestIdentity:
    now = _now()
    with connect_database(database) as connection:
        row = connection.execute(
            """SELECT u.*,m.role,t.token_id,t.expires_at FROM research_api_tokens t
               JOIN research_users u ON u.user_id=t.user_id
               LEFT JOIN research_project_memberships m
                 ON m.user_id=u.user_id AND m.project_id=?
               WHERE t.token_hash=? AND t.revoked_at IS NULL""",
            (project_id, _digest(token)),
        ).fetchone()
        if row is None or not row["enabled"] or _parse_time(row["expires_at"]) <= now:
            raise AuthenticationRequiredError("AUTHENTICATION_REQUIRED")
        if row["role"] is None:
            raise ResourceNotFoundError("PROJECT_NOT_FOUND")
        connection.execute(
            "UPDATE research_api_tokens SET last_used_at=? WHERE token_id=?",
            (_wire_time(now), row["token_id"]),
        )
    return _identity(row)


def _identity(row: sqlite3.Row) -> RequestIdentity:
    return RequestIdentity(
        row["user_id"], row["display_name"], frozenset({row["role"]})
    )


def create_token(
    database: Path, user_id: str, name: str, expires_days: int = 90
) -> tuple[dict[str, object], str]:
    name = name.strip()
    if not name or len(name) > 120 or not 1 <= expires_days <= 365:
        raise ValueError("TOKEN_INVALID")
    token_id = str(uuid4())
    token = "gwr_" + secrets.token_urlsafe(32)
    now = _now()
    expires = now + timedelta(days=expires_days)
    with connect_database(database) as connection:
        connection.execute(
            "INSERT INTO research_api_tokens VALUES (?,?,?,?,?,?,NULL,NULL)",
            (
                token_id,
                user_id,
                name,
                _digest(token),
                _wire_time(now),
                _wire_time(expires),
            ),
        )
    return {
        "tokenId": token_id,
        "name": name,
        "createdAt": _wire_time(now),
        "expiresAt": _wire_time(expires),
    }, token


def list_tokens(database: Path, user_id: str) -> list[dict[str, object]]:
    with connect_database(database) as connection:
        rows = connection.execute(
            """SELECT token_id,name,created_at,expires_at,last_used_at
               FROM research_api_tokens WHERE user_id=? AND revoked_at IS NULL
               ORDER BY created_at""",
            (user_id,),
        ).fetchall()
    return [
        {
            "tokenId": row["token_id"],
            "name": row["name"],
            "createdAt": row["created_at"],
            "expiresAt": row["expires_at"],
            "lastUsedAt": row["last_used_at"],
        }
        for row in rows
    ]


def revoke_token(database: Path, user_id: str, token_id: str) -> bool:
    with connect_database(database) as connection:
        result = connection.execute(
            """UPDATE research_api_tokens SET revoked_at=?
               WHERE token_id=? AND user_id=? AND revoked_at IS NULL""",
            (utc_now(), token_id, user_id),
        )
    return result.rowcount == 1
