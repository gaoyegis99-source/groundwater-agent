"""Small framework-neutral integration ports used by the workflow kernel."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol

from .runtime_contracts import (
    ActionAuthorization,
    AgentContext,
    CancellationToken,
    ToolExecutionRequest,
    ToolExecutionResult,
)
from .spec import AgentDecision, JsonValue, ToolReference, WorkflowSpec


class AgentDriver(Protocol):
    """A model integration that returns only framework-neutral decisions."""

    driver_version: str

    def decide(self, context: AgentContext) -> AgentDecision: ...


class AsyncAgentDriver(Protocol):
    """A model adapter with a native asynchronous decision boundary."""

    driver_version: str

    async def decide_async(self, context: AgentContext) -> AgentDecision: ...


class ToolExecutor(Protocol):
    """Executes only references that an application explicitly registered."""

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult: ...

    def reconcile(
        self, request: ToolExecutionRequest
    ) -> ToolExecutionResult | None: ...

    def execution_binding(self, tool: ToolReference) -> Mapping[str, JsonValue]: ...


class AsyncToolExecutor(Protocol):
    """An executor that cooperates with workflow cancellation."""

    async def execute_async(
        self, request: ToolExecutionRequest, cancellation: CancellationToken
    ) -> ToolExecutionResult: ...

    async def reconcile_async(
        self, request: ToolExecutionRequest
    ) -> ToolExecutionResult | None: ...


class ToolRegistrationExecutor(Protocol):
    """The trusted registration surface shared by sync and async tools."""

    def register(
        self,
        tool: ToolReference,
        handler: Callable[..., object],
        *,
        implementation_version: str | None = None,
    ) -> None: ...

    def is_registered(self, tool: ToolReference) -> bool: ...


class ActionPolicy(Protocol):
    """Embedding hook for current permission, budget, and version checks."""

    def authorize(
        self,
        request: ToolExecutionRequest,
        *,
        run_id: str,
        workflow: WorkflowSpec,
        step_id: str,
    ) -> ActionAuthorization: ...
