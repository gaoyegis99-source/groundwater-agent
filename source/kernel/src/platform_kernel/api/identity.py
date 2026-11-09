"""Compatibility imports for the Kernel public HTTP boundary.

Access policy now lives in :mod:`platform_kernel.access`; preserving this import
surface avoids a breaking change for installed API adapters.
"""

from ..access.identity import (
    RESEARCH_ROLES,
    RequestIdentity,
    require_role,
    resolve_identity,
)

__all__ = ("RESEARCH_ROLES", "RequestIdentity", "require_role", "resolve_identity")
