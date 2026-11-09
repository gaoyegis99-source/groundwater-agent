"""Trusted local tool registry used by the generated runnable example.

``register`` accepts the common registry protocol, so changing ``lookup`` to
``async def`` (and optionally accepting ``ToolContext``) needs no assembly or
release-delivery rewrite.
"""

from platform_lightweight_agent.api import ToolReference
from platform_lightweight_agent.engine import ToolRegistrationExecutor

LOOKUP = ToolReference(
    "example.lookup",
    "1.0.0",
    "sha256:0000000000000000000000000000000000000000000000000000000000000000",
)


def lookup(arguments: dict[str, object]) -> dict[str, str]:
    """Development implementation; release targets bind a wheel instead."""

    return {"answer": f"example:{arguments['query']}"}


def register(executor: ToolRegistrationExecutor) -> None:
    executor.register(LOOKUP, lookup)
