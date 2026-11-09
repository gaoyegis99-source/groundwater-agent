"""Kernel HTTP transport package with cycle-safe lazy exports."""

from typing import Any

__all__ = [
    "ConfirmationRequest",
    "MessageCreate",
    "ProblemDetails",
    "RejectionRequest",
    "SessionCreate",
    "ValidationIssue",
    "create_app",
]


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from .app import create_app

        return create_app
    if name in {
        "ConfirmationRequest",
        "MessageCreate",
        "ProblemDetails",
        "RejectionRequest",
        "SessionCreate",
        "ValidationIssue",
    }:
        from . import schemas

        return getattr(schemas, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
