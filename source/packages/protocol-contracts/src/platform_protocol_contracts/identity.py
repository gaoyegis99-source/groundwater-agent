"""Validated, deployment-local identity shared by V6 capability services.

The value is deliberately a small protocol value object rather than a service
setting.  Local mode is a development/deployment convenience, but all services
must still present the same identity envelope to preserve audit attribution.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import time
from typing import Any


_SUBJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}")
_ROLES = frozenset({"admin", "researcher", "viewer"})
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}")
_JTI = re.compile(r"[A-Za-z0-9_-]{16,128}")
_SEGMENT = re.compile(r"[A-Za-z0-9_-]+")
_ASSERTION_TYPE = "platform-identity+jwt"
_REQUIRED_CLAIMS = frozenset(
    {"iss", "aud", "sub", "name", "roles", "project", "iat", "exp", "jti"}
)


class IdentityAssertionError(ValueError):
    """A signed identity assertion is malformed, untrusted, or outside its lifetime."""


@dataclass(frozen=True)
class VerifiedIdentity:
    subject: str
    display_name: str
    roles: tuple[str, ...]
    project: str
    issuer: str
    audience: str
    issued_at: int
    expires_at: int
    jti: str


def encode_display_name_header(value: str) -> str:
    """Encode a validated display name as an ASCII-only internal HTTP header."""

    LocalIdentity("header-subject", value, ("viewer",))
    return "b64:" + _encode_segment(value.encode("utf-8")).decode("ascii")


def decode_display_name_header(value: str) -> str:
    """Decode the current ASCII form and the legacy ASGI latin-1 mapping."""

    if value.startswith("b64:"):
        try:
            return _decode_segment(value[4:]).decode("utf-8").strip()
        except (UnicodeError, ValueError):
            return ""
    try:
        value = value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return value.strip()


def load_identity_assertion_key(path: str | Path) -> bytes:
    """Load a bounded HMAC key without accepting an empty or human-sized secret."""

    try:
        value = Path(path).read_bytes().rstrip(b"\r\n")
    except OSError as error:
        raise ValueError("PLATFORM_IDENTITY_ASSERTION_KEY_UNREADABLE") from error
    _validate_key(value)
    return value


def mint_identity_assertion(
    key: bytes,
    *,
    issuer: str,
    audience: str,
    key_id: str,
    subject: str,
    display_name: str,
    roles: tuple[str, ...] | list[str],
    project: str,
    now: int | None = None,
    ttl_seconds: int = 60,
    jti: str | None = None,
) -> str:
    """Create the compact assertion accepted by the V6 ingress verifier.

    This helper is intentionally dependency-free so the managed edge proxy can
    reproduce the exact canonical representation without importing service code.
    """

    _validate_key(key)
    _validate_identifier(issuer, "IDENTITY_ASSERTION_ISSUER_INVALID")
    _validate_identifier(audience, "IDENTITY_ASSERTION_AUDIENCE_INVALID")
    _validate_identifier(key_id, "IDENTITY_ASSERTION_KEY_ID_INVALID")
    identity = LocalIdentity(subject, display_name, tuple(sorted(roles)))
    _validate_identifier(project, "IDENTITY_ASSERTION_PROJECT_INVALID")
    if not 1 <= ttl_seconds <= 300:
        raise ValueError("IDENTITY_ASSERTION_TTL_INVALID")
    issued_at = int(time.time()) if now is None else now
    token_id = jti or secrets.token_urlsafe(18)
    if type(issued_at) is not int or _JTI.fullmatch(token_id) is None:
        raise ValueError("IDENTITY_ASSERTION_TIME_OR_JTI_INVALID")
    header = {"alg": "HS256", "kid": key_id, "typ": _ASSERTION_TYPE}
    payload = {
        "aud": audience,
        "exp": issued_at + ttl_seconds,
        "iat": issued_at,
        "iss": issuer,
        "jti": token_id,
        "name": identity.display_name,
        "project": project,
        "roles": list(identity.roles),
        "sub": identity.subject,
    }
    signing_input = _encode_json(header) + b"." + _encode_json(payload)
    signature = hmac.new(key, signing_input, hashlib.sha256).digest()
    return signing_input.decode("ascii") + "." + _encode_segment(signature).decode("ascii")


def verify_identity_assertion(
    token: str,
    key: bytes,
    *,
    issuer: str,
    audience: str,
    key_id: str,
    project: str,
    now: int | None = None,
    clock_skew_seconds: int = 30,
    max_ttl_seconds: int = 300,
) -> VerifiedIdentity:
    """Authenticate and strictly decode one short-lived ingress assertion."""

    _validate_key(key)
    if not token or len(token) > 8192 or token.count(".") != 2:
        raise IdentityAssertionError("IDENTITY_ASSERTION_INVALID")
    encoded_header, encoded_payload, encoded_signature = token.split(".")
    try:
        header = _decode_json(encoded_header)
        if header != {"alg": "HS256", "kid": key_id, "typ": _ASSERTION_TYPE}:
            raise IdentityAssertionError("IDENTITY_ASSERTION_INVALID")
        signature = _decode_segment(encoded_signature)
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise IdentityAssertionError("IDENTITY_ASSERTION_INVALID") from error
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    expected = hmac.new(key, signing_input, hashlib.sha256).digest()
    if len(signature) != hashlib.sha256().digest_size or not hmac.compare_digest(
        signature, expected
    ):
        raise IdentityAssertionError("IDENTITY_ASSERTION_INVALID")
    try:
        payload = _decode_json(encoded_payload)
        if set(payload) != _REQUIRED_CLAIMS:
            raise IdentityAssertionError("IDENTITY_ASSERTION_INVALID")
        issued_at, expires_at = payload["iat"], payload["exp"]
        if type(issued_at) is not int or type(expires_at) is not int:
            raise IdentityAssertionError("IDENTITY_ASSERTION_INVALID")
        current = int(time.time()) if now is None else now
        if (
            type(current) is not int
            or not 0 <= clock_skew_seconds <= 120
            or not 1 <= max_ttl_seconds <= 300
            or issued_at > current + clock_skew_seconds
            or expires_at <= current - clock_skew_seconds
            or expires_at <= issued_at
            or expires_at - issued_at > max_ttl_seconds
        ):
            raise IdentityAssertionError("IDENTITY_ASSERTION_EXPIRED")
        if (
            payload["iss"] != issuer
            or payload["aud"] != audience
            or payload["project"] != project
            or _JTI.fullmatch(payload["jti"]) is None
        ):
            raise IdentityAssertionError("IDENTITY_ASSERTION_INVALID")
        raw_roles = payload["roles"]
        if (
            not isinstance(raw_roles, list)
            or not raw_roles
            or any(not isinstance(role, str) for role in raw_roles)
            or len(raw_roles) != len(set(raw_roles))
        ):
            raise IdentityAssertionError("IDENTITY_ASSERTION_INVALID")
        identity = LocalIdentity(
            payload["sub"], payload["name"], tuple(sorted(raw_roles))
        )
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, IdentityAssertionError):
            raise
        raise IdentityAssertionError("IDENTITY_ASSERTION_INVALID") from error
    return VerifiedIdentity(
        subject=identity.subject,
        display_name=identity.display_name,
        roles=identity.roles,
        project=project,
        issuer=issuer,
        audience=audience,
        issued_at=issued_at,
        expires_at=expires_at,
        jti=payload["jti"],
    )


def _validate_key(value: bytes) -> None:
    if not isinstance(value, bytes) or not 32 <= len(value) <= 4096:
        raise ValueError("PLATFORM_IDENTITY_ASSERTION_KEY_INVALID")


def _validate_identifier(value: object, code: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(code)


def _encode_segment(value: bytes) -> bytes:
    return base64.urlsafe_b64encode(value).rstrip(b"=")


def _encode_json(value: dict[str, Any]) -> bytes:
    return _encode_segment(
        json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    )


def _decode_segment(value: str) -> bytes:
    if not value or _SEGMENT.fullmatch(value) is None:
        raise ValueError("IDENTITY_ASSERTION_SEGMENT_INVALID")
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def _decode_json(value: str) -> dict[str, Any]:
    def unique_object(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("IDENTITY_ASSERTION_DUPLICATE_KEY")
            result[key] = item
        return result

    decoded = json.loads(
        _decode_segment(value).decode("utf-8"), object_pairs_hook=unique_object
    )
    if not isinstance(decoded, dict):
        raise ValueError("IDENTITY_ASSERTION_OBJECT_REQUIRED")
    return decoded


@dataclass(frozen=True)
class LocalIdentity:
    """The one local-mode actor accepted by all V6 public capability surfaces."""

    subject: str = "local-research-admin"
    display_name: str = "Local Research Admin"
    roles: tuple[str, ...] = ("admin",)

    def __post_init__(self) -> None:
        if _SUBJECT.fullmatch(self.subject) is None:
            raise ValueError("PLATFORM_LOCAL_IDENTITY_SUBJECT_INVALID")
        if (
            not self.display_name
            or len(self.display_name.encode("utf-8")) > 160
            or any(ord(character) < 32 or ord(character) == 127 for character in self.display_name)
        ):
            raise ValueError("PLATFORM_LOCAL_IDENTITY_DISPLAY_NAME_INVALID")
        if (
            not self.roles
            or len(set(self.roles)) != len(self.roles)
            or set(self.roles) - _ROLES
        ):
            raise ValueError("PLATFORM_LOCAL_IDENTITY_ROLES_INVALID")

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "LocalIdentity":
        values = environ if environ is not None else os.environ
        raw_roles = values.get("PLATFORM_LOCAL_IDENTITY_ROLES", "admin")
        roles = tuple(sorted(item.strip() for item in raw_roles.split(",") if item.strip()))
        return cls(
            subject=values.get("PLATFORM_LOCAL_IDENTITY_SUBJECT", "local-research-admin"),
            display_name=values.get("PLATFORM_LOCAL_IDENTITY_DISPLAY_NAME", "Local Research Admin"),
            roles=roles,
        )

    @property
    def roles_header(self) -> str:
        return ",".join(self.roles)
