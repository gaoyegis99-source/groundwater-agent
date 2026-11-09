import pytest

from platform_protocol_contracts import ResourcePolicy


def policy(visibility: str = "PRIVATE") -> ResourcePolicy:
    return ResourcePolicy("owner@example.test", visibility)


def test_owner_and_admin_are_resource_administrators() -> None:
    private = policy()
    for operation in ("view", "modify", "manage"):
        assert private.allows(
            subject="owner@example.test",
            roles=("researcher",),
            operation=operation,
        )
        assert private.allows(
            subject="operator@example.test",
            roles=("admin",),
            operation=operation,
        )


@pytest.mark.parametrize("role", ("admin", "researcher", "viewer"))
def test_project_visibility_is_readable_for_every_project_role(role: str) -> None:
    shared = policy(visibility="PROJECT")
    assert shared.allows(subject="reader", roles=(role,), operation="view")
    if role == "admin":
        assert shared.allows(subject="reader", roles=(role,), operation="modify")
    else:
        assert not shared.allows(
            subject="reader", roles=(role,), operation="modify"
        )
    assert not shared.allows(
        subject="reader", roles=("unknown",), operation="view"
    )


@pytest.mark.parametrize("role", ("researcher", "viewer"))
def test_private_visibility_is_only_owner_or_admin(role: str) -> None:
    private = policy()
    assert not private.allows(subject="other", roles=(role,), operation="view")
    assert not private.allows(subject="other", roles=(role,), operation="modify")
