"""Narrow orchestration services shared with the optional async scheduler.

The scheduler used to reach into dozens of ``WorkflowEngine`` private fields.
This immutable façade makes that coupling explicit without creating a second
engine, state machine, or persistence owner.  The engine remains the only
constructor and the journal remains the only durable authority.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from .journal import RunJournal, RunSnapshot
from .runtime_contracts import CancellationToken, ToolExecutionRequest
from .runtime_ports import (
    ActionPolicy,
    AgentDriver,
    AsyncAgentDriver,
    AsyncToolExecutor,
    ToolExecutor,
)
from .spec import JsonValue, WorkflowSpec


class EventWakeStream(Protocol):
    """Only the local wakeup operation is visible to orchestration."""

    def send_nowait(self, value: None) -> None: ...


class ValidateDrivers(Protocol):
    def __call__(
        self,
        workflow: WorkflowSpec,
        visiting: set[tuple[str, str]] | None = None,
        *,
        workflows: Mapping[tuple[str, str], WorkflowSpec] | None = None,
    ) -> None: ...


@dataclass(frozen=True)
class WorkflowRuntimeServices:
    """Process-local capabilities required by v3/v4 orchestration."""

    journal: RunJournal
    executor: ToolExecutor | AsyncToolExecutor
    drivers: Mapping[str, AgentDriver | AsyncAgentDriver]
    workflows: Mapping[tuple[str, str], WorkflowSpec]
    policy: ActionPolicy
    transition_lock: AbstractContextManager[object]
    active_lock: AbstractContextManager[object]
    active_tokens: MutableMapping[str, CancellationToken]
    event_watchers: MutableMapping[str, list[EventWakeStream]]
    notify_event: Callable[[str], None]
    acquire_resource: Callable[[str, str], Awaitable[None]]
    release_resource: Callable[[str, str], bool]
    reserve_tool_dispatch: Callable[[ToolExecutionRequest], Awaitable[None]]
    release_tool_dispatch: Callable[[ToolExecutionRequest], Awaitable[None]]
    manifest_workflows: Callable[[RunSnapshot], Mapping[tuple[str, str], WorkflowSpec]]
    verify_driver_bindings: Callable[[RunSnapshot], None]
    validate_drivers: ValidateDrivers
    driver_bindings: Callable[[WorkflowSpec], Mapping[str, str]]
    execution_manifest: Callable[[WorkflowSpec], Mapping[str, JsonValue]]
    driver_execution_binding: Callable[[str], Mapping[str, JsonValue]]


class WorkflowRuntimePort(Protocol):
    """The scheduler sees one service bundle, not engine private state."""

    workflow_services: WorkflowRuntimeServices
