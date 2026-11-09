"""Declarative route policies for identity, role, and owned-resource checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from .identity import RequestIdentity, require_role


class KernelApplication(Protocol):
    database: object
    settings: object


OwnershipCheck = Callable[[object, str, str, str], None]


@dataclass(frozen=True)
class RouteAccessPolicy:
    """One route family policy without giving the HTTP layer ownership of state."""

    required_role: str
    path_parameter: str | None = None
    ownership_check: OwnershipCheck | None = None

    def enforce(
        self,
        identity: RequestIdentity,
        application: KernelApplication,
        path_parameters: Mapping[str, str],
    ) -> None:
        require_role(identity, self.required_role)
        if self.path_parameter is None or self.ownership_check is None:
            return
        resource_id = path_parameters.get(self.path_parameter)
        if resource_id:
            self.ownership_check(
                application.database,
                application.settings.project_id,
                resource_id,
                identity.subject,
            )


# Imports remain here rather than in API dependencies so the complete public
# access matrix is visible in one place.
from ..interaction.access import (  # noqa: E402
    require_owned_agent,
    require_owned_session,
    require_owned_workflow,
)


SESSION_POLICY = RouteAccessPolicy("researcher", "sessionId", require_owned_session)
AGENT_RUN_POLICY = RouteAccessPolicy("researcher", "agentRunId", require_owned_agent)
WORKFLOW_RUN_POLICY = RouteAccessPolicy("researcher", "runId", require_owned_workflow)
AUDIT_POLICY = RouteAccessPolicy("admin")
