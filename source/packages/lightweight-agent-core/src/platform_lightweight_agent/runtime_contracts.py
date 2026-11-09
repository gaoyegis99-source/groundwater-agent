"""Framework-neutral invocation and tool execution contracts.

These values are deliberately independent of ``WorkflowEngine`` and of any
model SDK.  The engine retains re-exports for existing embedders, while new
adapters can depend on this small module without importing the scheduler.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import Event
from typing import TYPE_CHECKING

from .spec import (
    AgentBudget,
    AgentToolSpec,
    JsonValue,
    ToolReference,
    WorkflowValidationError,
    contains_secret_name,
    json_copy,
)

if TYPE_CHECKING:
    from .files import FileWorkspace


_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


class WorkflowRunError(RuntimeError):
    """A local workflow cannot continue under its current durable facts."""


class AgentDriverFailure(WorkflowRunError):  # noqa: N818 - public protocol name
    """A stable, credential-free failure reported by an Agent adapter."""

    def __init__(self, code: str, *, model_request_count: int | None = None) -> None:
        if not isinstance(code, str) or _ERROR_CODE.fullmatch(code) is None:
            raise ValueError("agent driver failure code must be stable")
        if model_request_count is not None and (
            not isinstance(model_request_count, int)
            or isinstance(model_request_count, bool)
            or model_request_count < 0
        ):
            raise ValueError("agent driver model request count is invalid")
        super().__init__(code)
        self.code = code
        # A provider may prove a request-limit boundary without returning a
        # normal decision. Keep that separate from unknown provider failures.
        self.model_request_count = model_request_count


class ToolExecutionRejectedError(WorkflowRunError):
    """The request is known invalid or outside the trusted local catalogue.

    Only a stable ``code`` crosses the trust boundary into durable diagnostics.
    Arbitrary exception prose is never journaled.
    """

    def __init__(self, message: str, *, code: str | None = None) -> None:
        if not isinstance(message, str) or not message:
            raise ValueError("tool execution rejection message is invalid")
        stable_code = code
        if stable_code is None:
            stable_code = message if _ERROR_CODE.fullmatch(message) else "TOOL_REJECTED"
        if (
            not isinstance(stable_code, str)
            or _ERROR_CODE.fullmatch(stable_code) is None
        ):
            raise ValueError("tool execution rejection code must be stable")
        super().__init__(message)
        self.code = stable_code


class ToolExecutionUncertainError(WorkflowRunError):
    """The caller cannot prove whether an external side effect happened."""


class ToolExecutionFailedError(WorkflowRunError):
    """A handler can prove a business failure without an unknown side effect.

    An ordinary Python exception is deliberately not this signal: it cannot
    prove that a dispatched external operation did not happen.
    """

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or _ERROR_CODE.fullmatch(code) is None:
            raise ValueError("tool execution failure code must be stable")
        super().__init__(code)
        self.code = code


class ToolExecutionStoppedError(WorkflowRunError):
    """The executor proved a dispatched operation stopped without a result."""


class CancellationToken:
    """Thread-safe cooperative cancellation signal for async integrations."""

    def __init__(self, parent: CancellationToken | None = None) -> None:
        self._event = Event()
        self._parent = parent

    @property
    def cancelled(self) -> bool:
        return self._event.is_set() or (
            self._parent is not None and self._parent.cancelled
        )

    def request(self) -> None:
        self._event.set()

    def child(self) -> CancellationToken:
        """Return a step-local signal which also observes its run parent."""

        return CancellationToken(self)

    async def wait(self) -> None:
        try:
            import anyio
        except ImportError as error:
            raise RuntimeError(
                "Install platform-lightweight-agent-core[workflow] for async workflows"
            ) from error
        while not self.cancelled:
            await anyio.sleep(0.02)

    async def checkpoint(self) -> None:
        if not self.cancelled:
            return
        try:
            import anyio
        except ImportError as error:
            raise RuntimeError(
                "Install platform-lightweight-agent-core[workflow] for async workflows"
            ) from error
        await anyio.lowlevel.checkpoint_if_cancelled()
        raise anyio.get_cancelled_exc_class()()


@dataclass(frozen=True)
class ToolContext:
    """Minimal host-provided context available to an opt-in tool handler.

    It contains only the action identity, cooperative cancellation signal,
    and optional host-owned file workspace: never a journal or credentials.
    """

    action_id: str
    cancellation: CancellationToken
    files: FileWorkspace | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action_id, str) or not self.action_id:
            raise WorkflowValidationError("tool context action_id is invalid")
        if not isinstance(self.cancellation, CancellationToken):
            raise WorkflowValidationError("tool context cancellation is invalid")
        if self.files is not None:
            from .files import FileWorkspace

            if not isinstance(self.files, FileWorkspace):
                raise WorkflowValidationError("tool context files are invalid")


@dataclass(frozen=True)
class AgentContext:
    run_id: str
    workflow_id: str
    workflow_version: str
    step_id: str
    turn: int
    inputs: Mapping[str, JsonValue]
    step_input: Mapping[str, JsonValue]
    values: Mapping[str, JsonValue]
    prior_tool_results: tuple[Mapping[str, JsonValue], ...]
    allowed_tools: tuple[ToolReference, ...] = ()
    workflow_schema_version: str = "v6-light-workflow-v1"
    tool_specs: tuple[AgentToolSpec, ...] = ()
    output_schema: Mapping[str, JsonValue] | None = None
    budget: AgentBudget | None = None
    model_requests_used: int = 0
    driver_continuation: Mapping[str, JsonValue] | None = None
    deferred_tool_results: tuple[Mapping[str, JsonValue], ...] = ()
    tool_calls_remaining: int | None = None
    definition_workflow_id: str | None = None
    definition_workflow_version: str | None = None
    definition_step_id: str | None = None
    total_tokens_used: int = 0


@dataclass(frozen=True)
class ToolExecutionRequest:
    action_id: str
    tool: ToolReference
    arguments: Mapping[str, JsonValue]

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "action_id": self.action_id,
            "tool": self.tool.to_mapping(),
            "arguments": dict(self.arguments),
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> ToolExecutionRequest:
        return cls(
            action_id=str(raw["action_id"]),
            tool=ToolReference.from_mapping(raw["tool"]),
            arguments=json_copy(raw["arguments"]),  # type: ignore[arg-type]
        )


ToolContextFactory = Callable[[ToolExecutionRequest, CancellationToken], ToolContext]


@dataclass(frozen=True)
class ToolExecutionResult:
    action_id: str
    succeeded: bool
    output: Mapping[str, JsonValue] | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.succeeded, bool):
            raise WorkflowValidationError("tool result succeeded must be boolean")
        if self.succeeded and self.error is not None:
            raise WorkflowValidationError(
                "successful tool results cannot contain an error"
            )
        if not self.succeeded and (
            not isinstance(self.error, str) or _ERROR_CODE.fullmatch(self.error) is None
        ):
            raise WorkflowValidationError(
                "failed tool results require a stable error code"
            )
        if self.output is not None:
            output = json_copy(self.output)
            if contains_secret_name(output):
                raise WorkflowValidationError(
                    "tool results must not contain credentials"
                )
            object.__setattr__(self, "output", output)

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "action_id": self.action_id,
            "succeeded": self.succeeded,
            "output": dict(self.output) if self.output is not None else None,
            "error": self.error,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> ToolExecutionResult:
        output = raw.get("output")
        succeeded = raw.get("succeeded")
        if not isinstance(succeeded, bool):
            raise WorkflowValidationError("tool result succeeded must be boolean")
        if output is not None and not isinstance(output, Mapping):
            raise WorkflowValidationError(
                "tool result output must be an object or null"
            )
        return cls(
            action_id=str(raw["action_id"]),
            succeeded=succeeded,
            output=json_copy(output) if isinstance(output, Mapping) else None,
            error=raw.get("error") if isinstance(raw.get("error"), str) else None,
        )


@dataclass(frozen=True)
class ActionAuthorization:
    """A fail-closed permission and budget decision for one external action."""

    allowed: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise WorkflowValidationError(
                "action authorization allowed must be boolean"
            )
        if self.allowed and self.reason is not None:
            raise WorkflowValidationError(
                "allowed action authorization cannot contain a reason"
            )
        if not self.allowed and (
            not isinstance(self.reason, str)
            or _ERROR_CODE.fullmatch(self.reason) is None
        ):
            raise WorkflowValidationError(
                "a denied action authorization requires a stable reason code"
            )
