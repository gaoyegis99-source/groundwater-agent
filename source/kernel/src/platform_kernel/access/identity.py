"""Local request identity used only when research authentication is disabled."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..errors import AuthorizationDeniedError


RESEARCH_ROLES = frozenset({"admin", "researcher", "viewer"})


@dataclass(frozen=True)
class RequestIdentity:
    subject: str
    display_name: str
    roles: frozenset[str]

    def as_wire(self) -> dict[str, object]:
        return {
            "subject": self.subject,
            "displayName": self.display_name,
            "roles": sorted(self.roles),
        }


def resolve_identity(headers: Mapping[str, str], settings) -> RequestIdentity:
    """Return the fixed test identity; production uses local sessions/tokens."""
    del headers
    return RequestIdentity(
        settings.local_identity_subject,
        settings.local_identity_display_name,
        frozenset(settings.local_identity_roles),
    )


def require_role(identity: RequestIdentity, role: str) -> None:
    hierarchy = {
        "admin": {"admin"},
        "researcher": {"admin", "researcher"},
        "viewer": RESEARCH_ROLES,
    }
    if not identity.roles.intersection(hierarchy.get(role, {role})):
        raise AuthorizationDeniedError("ROLE_FORBIDDEN")
