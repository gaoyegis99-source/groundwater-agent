from dataclasses import dataclass, field

import pytest

from platform_kernel.access import RequestIdentity, RouteAccessPolicy
from platform_kernel.errors import AuthorizationDeniedError


@dataclass
class _Settings:
    project_id: str = "project"


@dataclass
class _Application:
    database: object = object()
    settings: _Settings = field(default_factory=_Settings)


def test_route_policy_requires_role_before_resource_lookup() -> None:
    looked_up: list[str] = []

    def owned(_database, _project, resource, _subject):
        looked_up.append(resource)

    policy = RouteAccessPolicy("researcher", "sessionId", owned)
    identity = RequestIdentity("auditor", "Auditor", frozenset({"viewer"}))

    with pytest.raises(AuthorizationDeniedError, match="ROLE_FORBIDDEN"):
        policy.enforce(identity, _Application(), {"sessionId": "secret"})
    assert looked_up == []


def test_route_policy_passes_exact_project_subject_and_path_value() -> None:
    values: list[tuple[object, str, str, str]] = []

    def owned(*args):
        values.append(args)

    policy = RouteAccessPolicy("researcher", "sessionId", owned)
    application = _Application()
    identity = RequestIdentity("alice", "Alice", frozenset({"researcher"}))

    policy.enforce(identity, application, {"sessionId": "session-1"})

    assert values == [(application.database, "project", "session-1", "alice")]
