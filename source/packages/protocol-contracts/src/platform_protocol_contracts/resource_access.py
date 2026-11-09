"""Shared, fail-closed resource authorization semantics for V6 assets.
Storage remains service-owned. This module is the single project-role decision
point used by Data and Knowledge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ResourceOperation = Literal["view", "modify", "manage"]
ResourceVisibility = Literal["PRIVATE", "PROJECT"]

_OPERATIONS = frozenset({"view", "modify", "manage"})
_PROJECT_READ_ROLES = frozenset({"admin", "researcher", "viewer"})


@dataclass(frozen=True)
class ResourcePolicy:
    """One resource's effective owner and project visibility."""

    owner_subject: str
    visibility: ResourceVisibility

    def __post_init__(self) -> None:
        if not isinstance(self.owner_subject, str) or not self.owner_subject:
            raise ValueError("RESOURCE_OWNER_INVALID")
        if self.visibility not in {"PRIVATE", "PROJECT"}:
            raise ValueError("RESOURCE_VISIBILITY_INVALID")

    def allows(
        self,
        *,
        subject: str,
        roles: tuple[str, ...] | list[str] | set[str],
        operation: ResourceOperation,
    ) -> bool:
        """Return a decision without a permissive fallback.

        Admins and owners retain administration. PROJECT visibility grants
        project members read-only access.
        """

        if operation not in _OPERATIONS or not subject:
            return False
        role_set = frozenset(roles)
        if "admin" in role_set or subject == self.owner_subject:
            return True
        if (
            operation == "view"
            and self.visibility == "PROJECT"
            and role_set.intersection(_PROJECT_READ_ROLES)
        ):
            return True
        return False
