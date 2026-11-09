"""Explicit HTTP access policy for the product-neutral Kernel.

The access package owns only request identity and route-policy composition.  It
does not own sessions, Agent runs, or workflow state; their owners continue to
perform the exact persistence checks used by the public boundary.
"""

from .identity import RequestIdentity, require_role, resolve_identity
from .policies import (
    AGENT_RUN_POLICY,
    AUDIT_POLICY,
    SESSION_POLICY,
    WORKFLOW_RUN_POLICY,
    RouteAccessPolicy,
)

__all__ = (
    "AGENT_RUN_POLICY",
    "AUDIT_POLICY",
    "SESSION_POLICY",
    "WORKFLOW_RUN_POLICY",
    "RequestIdentity",
    "RouteAccessPolicy",
    "require_role",
    "resolve_identity",
)
