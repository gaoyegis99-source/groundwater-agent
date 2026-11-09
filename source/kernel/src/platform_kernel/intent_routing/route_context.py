"""Pure-ish context compilation for intent routing.

The router owns admission, retries, and failure mapping.  This module owns the
deterministic conversion from durable interaction facts to a compiled provider
view, so that policy changes cannot become entangled with transport retries.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents import Agent

from ..agent_runtime.context_policy import (
    ContextPolicyV1,
    ContextSourceSnapshotV1,
    ContextTurnV1,
)
from ..agent_runtime.context_usage import context_tokens
from ..agent_runtime.memory import MemorySummaryV3, completed_turns


@dataclass(frozen=True)
class RouteContextRequest:
    """The complete, durable input to one intent-routing context compilation."""

    message: str
    memory: MemorySummaryV3 | None
    history: tuple[tuple[int, str, str], ...]
    session_id: str
    goal_revision: int
    memory_through_goal_revision: int
    task_context: dict | None
    candidate_context: dict | None


@dataclass(frozen=True)
class CompiledRouteContext:
    """A body-free policy manifest and the exact provider input it describes."""

    messages: tuple[object, ...]
    manifest: dict[str, object]


def compile_route_context(
    compiler: ContextPolicyV1,
    agent: Agent,
    request: RouteContextRequest,
    *,
    available_input_tokens: int,
) -> CompiledRouteContext:
    view = compiler.route_view(
        _snapshot(request),
        available_input_tokens=available_input_tokens,
        fixed_input_tokens=context_tokens(agent, agent.instructions, []),
    )
    return CompiledRouteContext(tuple(view.messages), view.manifest())


def rejected_route_manifest(
    compiler: ContextPolicyV1,
    agent: Agent,
    request: RouteContextRequest,
    *,
    available_input_tokens: int,
) -> dict[str, object]:
    """Return the auditable rejection view for an oversized request."""

    return compiler.rejected_route_view(
        _snapshot(request),
        available_input_tokens=available_input_tokens,
        fixed_input_tokens=context_tokens(agent, agent.instructions, []),
    ).manifest()


def _snapshot(request: RouteContextRequest) -> ContextSourceSnapshotV1:
    turns = completed_turns(request.history, after_revision=0)
    return ContextSourceSnapshotV1(
        session_id=request.session_id,
        goal_revision=request.goal_revision,
        message=request.message,
        turns=tuple(
            ContextTurnV1(turn.goal_revision, turn.user, turn.assistant) for turn in turns
        ),
        memory_summary=None if request.memory is None else request.memory.model_dump(),
        memory_through_goal_revision=request.memory_through_goal_revision,
        task_context=request.task_context,
        candidate_context=request.candidate_context,
        mode="INTENT_ROUTING",
    )


__all__ = (
    "CompiledRouteContext",
    "RouteContextRequest",
    "compile_route_context",
    "rejected_route_manifest",
)
