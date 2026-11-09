"""Framework-neutral workflow execution with explicit approval and recovery.

The engine is deliberately conservative at the side-effect boundary.  It
creates a stable action identity before invoking a tool.  If a process dies
after that point and the executor cannot prove an outcome, the run becomes
``RECONCILING`` instead of replaying a potentially mutating request.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import inspect
import json
import sys
from collections import OrderedDict, deque
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field as dataclass_field
from threading import Condition, RLock
from typing import Any
from uuid import uuid4

from .journal import (
    BACKUP_RECOVERY_STATUS,
    EventCursor,
    RunConflictError,
    RunExecutionMismatchError,
    RunEvent,
    RunHandle,
    RunJournal,
    RunListCursor,
    RunPage,
    RunQueryFilter,
    RunQueryUnsupportedError,
    RunSnapshot,
)
from .spec import (
    ASYNC_SCHEMA_VERSIONS,
    AgentDecision,
    AgentToolSpec,
    ApprovalDecision,
    JsonValue,
    StepKind,
    StepSpec,
    ToolReference,
    WorkflowSpec,
    WorkflowValidationError,
    canonical_json,
    contains_secret_name,
    json_copy,
)
from .runtime_services import EventWakeStream, WorkflowRuntimeServices
from .runtime_contracts import (
    ActionAuthorization,
    AgentContext,
    AgentDriverFailure,
    CancellationToken,
    ToolContext,
    ToolContextFactory,
    ToolExecutionFailedError,
    ToolExecutionRejectedError,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutionStoppedError,
    ToolExecutionUncertainError,
    WorkflowRunError,
)
from .runtime_ports import (
    ActionPolicy,
    AgentDriver,
    AsyncAgentDriver as AsyncAgentDriver,
    AsyncToolExecutor as AsyncToolExecutor,
    ToolExecutor,
    ToolRegistrationExecutor as ToolRegistrationExecutor,
)

_EXECUTION_MANIFEST_V2 = "v6-execution-manifest-v2"
_EXECUTION_MANIFEST_V3 = "v6-execution-manifest-v3"
_SUPPORTED_EXECUTION_MANIFESTS = frozenset(
    {_EXECUTION_MANIFEST_V2, _EXECUTION_MANIFEST_V3}
)
_RUNTIME_PROTOCOL_VERSION = "v6-light-runtime-v1"


@dataclass(frozen=True)
class ProcessResourceDiagnostics:
    """Bounded process-local mutex registry diagnostics.

    The registry is intentionally independent of an asyncio/AnyIO event loop,
    so separate embedded ``HostRuntime`` instances can coordinate safely.
    """

    registered_resources: int
    holders: int
    waiters: int
    unmatched_releases: int

    def to_mapping(self) -> dict[str, int]:
        return {
            "registered_resources": self.registered_resources,
            "holders": self.holders,
            "waiters": self.waiters,
            "unmatched_releases": self.unmatched_releases,
        }


@dataclass(eq=False)
class _ProcessResourceWaiter:
    resource: str
    borrower: object
    granted: bool = False
    cancelled: bool = False


@dataclass
class _ProcessResourceEntry:
    owner: object | None = None
    waiters: deque[_ProcessResourceWaiter] = dataclass_field(default_factory=deque)


class _ProcessResourceRegistry:
    """Thread-safe, event-loop-neutral process mutex ownership."""

    def __init__(self) -> None:
        self._condition = Condition(RLock())
        self._entries: dict[str, _ProcessResourceEntry] = {}
        self._unmatched_releases = 0

    def register(self, resource: str, borrower: object) -> _ProcessResourceWaiter:
        ticket = _ProcessResourceWaiter(resource, borrower)
        with self._condition:
            entry = self._entries.setdefault(resource, _ProcessResourceEntry())
            if entry.owner == borrower or any(
                item.borrower == borrower for item in entry.waiters
            ):
                raise RuntimeError("PROCESS_RESOURCE_ALREADY_REGISTERED")
            entry.waiters.append(ticket)
            self._grant_next(entry)
            return ticket

    def wait(self, ticket: _ProcessResourceWaiter) -> bool:
        with self._condition:
            while not ticket.granted and not ticket.cancelled:
                self._condition.wait()
            return ticket.granted and not ticket.cancelled

    def cancel(self, ticket: _ProcessResourceWaiter) -> None:
        with self._condition:
            entry = self._entries.get(ticket.resource)
            if entry is None:
                ticket.cancelled = True
                return
            if entry.owner == ticket.borrower:
                entry.owner = None
            else:
                try:
                    entry.waiters.remove(ticket)
                except ValueError:
                    pass
            ticket.cancelled = True
            ticket.granted = False
            self._grant_next(entry)
            self._discard_idle(ticket.resource, entry)
            self._condition.notify_all()

    def release(self, resource: str, borrower: object) -> bool:
        with self._condition:
            entry = self._entries.get(resource)
            if entry is None or entry.owner != borrower:
                # Idempotent result races may release the same action twice.
                # Record that disposition explicitly; unlike the old broad
                # RuntimeError handler, thread/loop misuse is never hidden.
                self._unmatched_releases += 1
                return False
            entry.owner = None
            self._grant_next(entry)
            self._discard_idle(resource, entry)
            self._condition.notify_all()
            return True

    def diagnostics(self) -> ProcessResourceDiagnostics:
        with self._condition:
            return ProcessResourceDiagnostics(
                registered_resources=len(self._entries),
                holders=sum(
                    entry.owner is not None for entry in self._entries.values()
                ),
                waiters=sum(len(entry.waiters) for entry in self._entries.values()),
                unmatched_releases=self._unmatched_releases,
            )

    def _grant_next(self, entry: _ProcessResourceEntry) -> None:
        if entry.owner is not None:
            return
        while entry.waiters:
            ticket = entry.waiters.popleft()
            if ticket.cancelled:
                continue
            ticket.granted = True
            entry.owner = ticket.borrower
            self._condition.notify_all()
            return

    def _discard_idle(self, resource: str, entry: _ProcessResourceEntry) -> None:
        if entry.owner is None and not entry.waiters:
            self._entries.pop(resource, None)


_PROCESS_RESOURCE_REGISTRY = _ProcessResourceRegistry()


def process_resource_diagnostics() -> ProcessResourceDiagnostics:
    """Return a credential-free snapshot of process mutex usage."""

    return _PROCESS_RESOURCE_REGISTRY.diagnostics()


def _run_target(
    run_id: str | RunHandle, expected_execution_id: str | None
) -> tuple[str, str | None]:
    """Normalize the additive instance-precondition API at the core edge."""

    if isinstance(run_id, RunHandle):
        if (
            expected_execution_id is not None
            and expected_execution_id != run_id.execution_id
        ):
            raise RunExecutionMismatchError()
        return run_id.run_id, run_id.execution_id
    if not isinstance(run_id, str) or not run_id:
        raise WorkflowValidationError("run_id is required")
    if expected_execution_id is not None:
        # Reuse the public handle validation rather than accepting an arbitrary
        # opaque value at a control boundary.
        RunHandle(run_id, expected_execution_id)
    return run_id, expected_execution_id


def _event_target(
    run_id: str | RunHandle,
    *,
    after: int,
    expected_execution_id: str | None,
    cursor: EventCursor | None,
) -> tuple[str, int, str | None]:
    normalized_run_id, expected = _run_target(run_id, expected_execution_id)
    if cursor is None:
        if not isinstance(after, int) or isinstance(after, bool) or after < 0:
            raise WorkflowValidationError("event cursor must be a non-negative integer")
        return normalized_run_id, after, expected
    if not isinstance(cursor, EventCursor):
        raise WorkflowValidationError("event cursor is invalid")
    if after != 0:
        raise WorkflowValidationError("event cursor cannot be combined with after")
    if expected is not None and expected != cursor.execution_id:
        raise RunExecutionMismatchError()
    return normalized_run_id, cursor.sequence, cursor.execution_id


def _journal_get(
    journal: RunJournal, run_id: str, expected_execution_id: str | None
) -> RunSnapshot:
    """Keep old third-party Journal overrides working for legacy calls."""

    if expected_execution_id is None or not _accepts_execution_precondition(
        journal.get
    ):
        snapshot = journal.get(run_id)
        if (
            expected_execution_id is not None
            and snapshot.execution_id != expected_execution_id
        ):
            raise RunExecutionMismatchError()
        return snapshot
    return journal.get(run_id, expected_execution_id=expected_execution_id)


def _journal_status(
    journal: RunJournal, run_id: str, expected_execution_id: str | None
) -> Any:
    if expected_execution_id is None or not _accepts_execution_precondition(
        journal.status
    ):
        status = journal.status(run_id)
        if (
            expected_execution_id is not None
            and status.execution_id != expected_execution_id
        ):
            raise RunExecutionMismatchError()
        return status
    return journal.status(run_id, expected_execution_id=expected_execution_id)


def _journal_events(
    journal: RunJournal,
    run_id: str,
    *,
    after: int,
    limit: int,
    expected_execution_id: str | None,
) -> tuple[RunEvent, ...]:
    if expected_execution_id is None or not _accepts_execution_precondition(
        journal.events
    ):
        events = journal.events(run_id, after=after, limit=limit)
        if expected_execution_id is not None:
            status = _journal_status(journal, run_id, expected_execution_id)
            if any(event.execution_id != status.execution_id for event in events):
                raise RunExecutionMismatchError()
        return events
    return journal.events(
        run_id,
        after=after,
        limit=limit,
        expected_execution_id=expected_execution_id,
    )


def _journal_list_runs(
    journal: RunJournal,
    filter: RunQueryFilter | None,
    cursor: RunListCursor | None,
    limit: int,
) -> RunPage:
    """Use the additive list capability without widening ``RunJournal``."""

    list_runs = getattr(journal, "list_runs", None)
    if not callable(list_runs):
        raise RunQueryUnsupportedError()
    page = list_runs(filter, cursor, limit)
    if not isinstance(page, RunPage):
        raise WorkflowRunError("RUN_QUERY_RESULT_INVALID")
    return page


def _accepts_execution_precondition(method: Any) -> bool:
    try:
        parameters = inspect.signature(method).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "expected_execution_id"
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _tool_context(
    factory: ToolContextFactory | None,
    request: ToolExecutionRequest,
    cancellation: CancellationToken,
) -> ToolContext:
    """Construct a validated context before the handler's side-effect edge."""

    try:
        context = (
            factory(request, cancellation)
            if factory is not None
            else ToolContext(request.action_id, cancellation)
        )
    except Exception as error:
        raise ToolExecutionRejectedError("TOOL_CONTEXT_UNAVAILABLE") from error
    if (
        not isinstance(context, ToolContext)
        or context.action_id != request.action_id
        or context.cancellation is not cancellation
    ):
        raise ToolExecutionRejectedError("TOOL_CONTEXT_UNAVAILABLE")
    return context


def _handler_wants_context(handler: Callable[..., object]) -> bool:
    """Make context opt-in without breaking async cancellation handlers."""

    try:
        parameters = tuple(inspect.signature(handler).parameters.values())
    except (TypeError, ValueError):
        return False
    positional = tuple(
        parameter
        for parameter in parameters
        if parameter.kind
        in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
    )
    if len(positional) < 2:
        return False
    parameter = positional[1]
    if parameter.name in {"context", "ctx", "tool_context"}:
        return True
    annotation = parameter.annotation
    return annotation is ToolContext or annotation in ("ToolContext", "tool_context")


def _invoke_sync_handler(
    handler: Callable[..., object],
    arguments: Mapping[str, JsonValue],
    context: ToolContext,
) -> object:
    """Pass context only to an explicitly opted-in synchronous handler.

    Before ``ToolContext`` existed, a synchronous handler was always invoked
    as ``handler(arguments)``.  Merely accepting an optional second argument
    must therefore not silently change its meaning when an embedding enables
    a context factory.  Match the documented name/annotation opt-in used by
    native async handlers instead.
    """

    if not _handler_wants_context(handler):
        return handler(arguments)
    try:
        inspect.signature(handler).bind(arguments, context)
    except (TypeError, ValueError):
        return handler(arguments)
    return handler(arguments, context)


def _invoke_async_handler(
    handler: Callable[..., object],
    arguments: Mapping[str, JsonValue],
    context: ToolContext,
) -> object:
    """Preserve ``(arguments, cancellation)`` while adding opt-in context."""

    try:
        inspect.signature(handler).bind(arguments, context)
    except (TypeError, ValueError):
        return handler(arguments)
    return handler(
        arguments, context if _handler_wants_context(handler) else context.cancellation
    )


def _python_tool_execution_binding(
    tool: ToolReference,
    handler: Callable[..., object],
    *,
    mode: str,
    implementation_version: str | None,
) -> dict[str, JsonValue]:
    """Describe Python code identity without serializing a closure or globals."""

    version = implementation_version or tool.package_digest
    if (
        not isinstance(version, str)
        or not version
        or len(version.encode("utf-8")) > 256
        or any(ord(character) < 32 for character in version)
    ):
        raise WorkflowValidationError("tool implementation_version is invalid")
    module = getattr(handler, "__module__", type(handler).__module__)
    qualname = getattr(handler, "__qualname__", type(handler).__qualname__)
    return {
        "adapter": "python-function-v1",
        "implementation": f"{module}.{qualname}",
        "implementation_version": version,
        "mode": mode,
    }


@dataclass(frozen=True)
class AllowTrustedActions:
    """Default policy: exact registered tools are trusted local capabilities."""

    def authorize(
        self,
        request: ToolExecutionRequest,
        *,
        run_id: str,
        workflow: WorkflowSpec,
        step_id: str,
    ) -> ActionAuthorization:
        del request, run_id, workflow, step_id
        return ActionAuthorization(True)


class LocalToolExecutor:
    """Trusted local-function executor with action-id idempotency.

    Applications register functions in code.  The model cannot install code,
    widen paths, or create a new registration.

    Completed results stay in memory until the workflow journal has committed
    their terminal action fact.  :meth:`acknowledge_durable_result` is called
    by :class:`WorkflowEngine` after that commit and bounds only those safe
    entries; dispatched and outcome-unknown actions are never evicted.
    """

    def __init__(
        self,
        *,
        max_durable_results: int = 1_024,
        context_factory: ToolContextFactory | None = None,
    ) -> None:
        if (
            not isinstance(max_durable_results, int)
            or isinstance(max_durable_results, bool)
            or max_durable_results < 0
        ):
            raise ValueError("max_durable_results must be a non-negative integer")
        self._lock = RLock()
        self._finished = Condition(self._lock)
        self._handlers: dict[
            tuple[str, str, str],
            Callable[..., object],
        ] = {}
        self._bindings: dict[tuple[str, str, str], dict[str, JsonValue]] = {}
        self._requests: dict[str, ToolExecutionRequest] = {}
        self._results: dict[str, ToolExecutionResult] = {}
        self._in_flight: set[str] = set()
        self._uncertain: set[str] = set()
        self._max_durable_results = max_durable_results
        self._durable_results: OrderedDict[str, None] = OrderedDict()
        self._context_factory = context_factory

    def register(
        self,
        tool: ToolReference,
        handler: Callable[..., object],
        *,
        implementation_version: str | None = None,
    ) -> None:
        if not callable(handler):
            raise WorkflowValidationError("tool handler must be callable")
        # Calling an ``async def`` like a regular local handler only creates a
        # coroutine object.  It used to be serialized as a successful tool
        # value and emitted an unawaited-coroutine warning.  v3's native async
        # executor protocol is the intentional boundary for such handlers.
        if inspect.iscoroutinefunction(handler):
            raise WorkflowValidationError(
                "async tool handlers require an AsyncToolExecutor adapter"
            )
        key = (tool.tool_id, tool.version, tool.package_digest)
        binding = _python_tool_execution_binding(
            tool,
            handler,
            mode="sync",
            implementation_version=implementation_version,
        )
        with self._lock:
            if key in self._handlers:
                raise ToolExecutionRejectedError("tool reference is already registered")
            self._handlers[key] = handler
            self._bindings[key] = binding

    def execution_binding(self, tool: ToolReference) -> dict[str, JsonValue]:
        key = (tool.tool_id, tool.version, tool.package_digest)
        with self._lock:
            binding = self._bindings.get(key)
        if binding is None:
            raise ToolExecutionRejectedError(
                "tool is not registered in the trusted catalogue"
            )
        return dict(binding)

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        """Execute through the legacy synchronous boundary.

        V3 callers use :meth:`execute_with_cancellation` so a context-aware
        synchronous handler observes the same durable run/step cancellation
        signal as a native asynchronous handler.
        """

        return self.execute_with_cancellation(request, CancellationToken())

    def execute_with_cancellation(
        self, request: ToolExecutionRequest, cancellation: CancellationToken
    ) -> ToolExecutionResult:
        if not isinstance(cancellation, CancellationToken):
            raise WorkflowValidationError("tool cancellation token is invalid")
        key = (request.tool.tool_id, request.tool.version, request.tool.package_digest)
        with self._finished:
            while True:
                original = self._requests.get(request.action_id)
                if original is not None:
                    if original.to_mapping() != request.to_mapping():
                        raise ToolExecutionRejectedError(
                            "action id was reused with another request"
                        )
                    known = self._results.get(request.action_id)
                    if known is not None:
                        return known
                    if request.action_id in self._uncertain:
                        raise ToolExecutionUncertainError(
                            "the original tool outcome is still uncertain"
                        )
                    if request.action_id in self._in_flight:
                        self._finished.wait()
                        continue
                handler = self._handlers.get(key)
                if handler is None:
                    raise ToolExecutionRejectedError(
                        "tool is not registered in the trusted catalogue"
                    )
                self._requests[request.action_id] = request
                self._in_flight.add(request.action_id)
                break
        handler_returned = False
        try:
            context = _tool_context(self._context_factory, request, cancellation)
            raw = _invoke_sync_handler(handler, dict(request.arguments), context)
            handler_returned = True
            if inspect.isawaitable(raw):
                # A decorator can conceal an async function from
                # ``iscoroutinefunction``.  Close a bare coroutine so this
                # fail-closed path does not itself leak a runtime warning.
                close = getattr(raw, "close", None)
                if callable(close):
                    close()
                raise ToolExecutionUncertainError(
                    "synchronous tool handler returned an awaitable"
                )
            if isinstance(raw, ToolExecutionResult):
                if raw.action_id != request.action_id:
                    raise ToolExecutionUncertainError(
                        "tool handler returned a result for another action"
                    )
                result = raw
            elif isinstance(raw, Mapping):
                output = json_copy(raw)
                result = ToolExecutionResult(request.action_id, True, output=output)
            else:
                output = {"value": json_copy(raw)}
                result = ToolExecutionResult(request.action_id, True, output=output)
        except ToolExecutionRejectedError:
            with self._finished:
                self._in_flight.discard(request.action_id)
                self._finished.notify_all()
            raise
        except ToolExecutionStoppedError:
            with self._finished:
                self._in_flight.discard(request.action_id)
                self._finished.notify_all()
            raise
        except ToolExecutionFailedError as error:
            result = ToolExecutionResult(request.action_id, False, error=error.code)
        except WorkflowValidationError as error:
            # The handler completed normally, but its returned value cannot
            # enter a credential-free JSON result.  This is a known rejected
            # output, not an assertion about the external side effect.  It is
            # safe to expose only a stable code; never persist the value.
            if handler_returned:
                result = ToolExecutionResult(
                    request.action_id, False, error="TOOL_OUTPUT_INVALID"
                )
            else:
                with self._finished:
                    self._in_flight.discard(request.action_id)
                    self._uncertain.add(request.action_id)
                    self._finished.notify_all()
                raise ToolExecutionUncertainError(
                    _exception_code("TOOL_PROGRAMMER", error)
                ) from error
        except ToolExecutionUncertainError:
            with self._finished:
                self._in_flight.discard(request.action_id)
                self._uncertain.add(request.action_id)
                self._finished.notify_all()
            raise
        # A handler is an external-side-effect boundary.  A programming error
        # after entering it is not evidence that no side effect occurred, so
        # never turn it into a definite failed ToolExecutionResult.  The
        # workflow will instead preserve the action identity for reconciliation.
        except Exception as error:
            with self._finished:
                self._in_flight.discard(request.action_id)
                self._uncertain.add(request.action_id)
                self._finished.notify_all()
            raise ToolExecutionUncertainError(
                _exception_code("TOOL_PROGRAMMER", error)
            ) from error
        with self._finished:
            previous = self._results.get(request.action_id)
            self._in_flight.discard(request.action_id)
            if previous is None:
                self._results[request.action_id] = result
            self._finished.notify_all()
            return previous or result

    def reconcile(self, request: ToolExecutionRequest) -> ToolExecutionResult | None:
        with self._finished:
            original = self._requests.get(request.action_id)
            if original is not None and original.to_mapping() != request.to_mapping():
                raise ToolExecutionRejectedError(
                    "action id was reused with another request"
                )
            return self._results.get(request.action_id)

    @property
    def cached_result_count(self) -> int:
        """Number of completed action results currently retained in memory."""

        with self._lock:
            return len(self._results)

    def is_registered(self, tool: ToolReference) -> bool:
        """Return whether this exact immutable reference has a local handler."""

        key = (tool.tool_id, tool.version, tool.package_digest)
        with self._lock:
            return key in self._handlers

    def acknowledge_durable_result(self, request: ToolExecutionRequest) -> None:
        """Bound a result cache only after its action fact is journaled.

        This is deliberately an engine-to-executor acknowledgement, rather
        than an eviction timer.  The durable journal remains authoritative for
        future workflow recovery; a duplicate engine dispatch cannot result
        from cache eviction because a resolved action is never dispatched.
        """

        with self._finished:
            action_id = request.action_id
            original = self._requests.get(action_id)
            if original is not None and original.to_mapping() != request.to_mapping():
                raise ToolExecutionRejectedError(
                    "action id was reused with another request"
                )
            if (
                action_id not in self._results
                or action_id in self._in_flight
                or action_id in self._uncertain
            ):
                return
            self._durable_results[action_id] = None
            self._durable_results.move_to_end(action_id)
            while len(self._durable_results) > self._max_durable_results:
                expired_action_id, _ = self._durable_results.popitem(last=False)
                # Only entries placed in _durable_results above may be
                # removed.  In-flight and unknown outcomes never enter it.
                self._results.pop(expired_action_id, None)
                self._requests.pop(expired_action_id, None)


class AsyncFunctionToolExecutor:
    """Small native adapter for explicitly registered ``async def`` handlers.

    The adapter intentionally owns no workflow scheduling policy: v3's AnyIO
    task group supplies the run/branch capacity limit and passes a
    :class:`CancellationToken` to the handler.  This class only provides the
    same exact-reference and action-id guarantees as :class:`LocalToolExecutor`
    without pretending that an asynchronous function is synchronous.

    A handler may accept ``(arguments)``, ``(arguments, cancellation)``, or
    opt in to ``(arguments, context)`` (using a ``context``/``ctx`` parameter
    name or :class:`ToolContext` annotation).  It must return a mapping, JSON value, or
    :class:`ToolExecutionResult`.  Explicit ``ToolExecutionFailedError``,
    ``ToolExecutionRejectedError``, ``ToolExecutionStoppedError`` and
    ``ToolExecutionUncertainError`` retain their normal boundary meanings.
    Any other exception becomes an uncertain result after dispatch.
    """

    def __init__(
        self,
        *,
        max_durable_results: int = 1_024,
        context_factory: ToolContextFactory | None = None,
    ) -> None:
        if (
            not isinstance(max_durable_results, int)
            or isinstance(max_durable_results, bool)
            or max_durable_results < 0
        ):
            raise ValueError("max_durable_results must be a non-negative integer")
        self._lock = RLock()
        self._finished = Condition(self._lock)
        self._handlers: dict[tuple[str, str, str], Callable[..., Any]] = {}
        self._bindings: dict[tuple[str, str, str], dict[str, JsonValue]] = {}
        self._requests: dict[str, ToolExecutionRequest] = {}
        self._results: dict[str, ToolExecutionResult] = {}
        self._in_flight: set[str] = set()
        self._uncertain: set[str] = set()
        self._max_durable_results = max_durable_results
        self._durable_results: OrderedDict[str, None] = OrderedDict()
        self._context_factory = context_factory

    def register(
        self,
        tool: ToolReference,
        handler: Callable[..., Any],
        *,
        implementation_version: str | None = None,
    ) -> None:
        """Register one native coroutine handler for an immutable reference."""

        if not callable(handler) or not inspect.iscoroutinefunction(handler):
            raise WorkflowValidationError(
                "AsyncFunctionToolExecutor requires an async tool handler"
            )
        key = (tool.tool_id, tool.version, tool.package_digest)
        binding = _python_tool_execution_binding(
            tool,
            handler,
            mode="async",
            implementation_version=implementation_version,
        )
        with self._lock:
            if key in self._handlers:
                raise ToolExecutionRejectedError("tool reference is already registered")
            self._handlers[key] = handler
            self._bindings[key] = binding

    def execution_binding(self, tool: ToolReference) -> dict[str, JsonValue]:
        key = (tool.tool_id, tool.version, tool.package_digest)
        with self._lock:
            binding = self._bindings.get(key)
        if binding is None:
            raise ToolExecutionRejectedError(
                "tool is not registered in the trusted catalogue"
            )
        return dict(binding)

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        del request
        raise ToolExecutionRejectedError(
            "async tool handlers require execute_async in a v3/v4 workflow"
        )

    async def execute_async(
        self, request: ToolExecutionRequest, cancellation: CancellationToken
    ) -> ToolExecutionResult:
        """Await one handler while preserving a durable action identity."""

        try:
            import anyio
        except ImportError as error:  # pragma: no cover - guarded by v3 caller
            raise RuntimeError(
                "Install platform-lightweight-agent-core[workflow] for async tools"
            ) from error
        key = (request.tool.tool_id, request.tool.version, request.tool.package_digest)
        handler: Callable[..., Any]
        while True:
            with self._finished:
                original = self._requests.get(request.action_id)
                if original is not None:
                    if original.to_mapping() != request.to_mapping():
                        raise ToolExecutionRejectedError(
                            "action id was reused with another request"
                        )
                    known = self._results.get(request.action_id)
                    if known is not None:
                        return known
                    if request.action_id in self._uncertain:
                        raise ToolExecutionUncertainError(
                            "the original tool outcome is still uncertain"
                        )
                    if request.action_id in self._in_flight:
                        wait_for_existing = True
                    else:  # pragma: no cover - defensive malformed internal state
                        wait_for_existing = False
                else:
                    wait_for_existing = False
                if not wait_for_existing:
                    handler = self._handlers.get(key)  # type: ignore[assignment]
                    if handler is None:
                        raise ToolExecutionRejectedError(
                            "tool is not registered in the trusted catalogue"
                        )
                    self._requests[request.action_id] = request
                    self._in_flight.add(request.action_id)
                    break
            # Do not block an AnyIO scheduler while another caller settles the
            # same action identity.  A cancellation merely abandons this wait;
            # it never removes the original in-flight fact.
            await anyio.to_thread.run_sync(
                self._wait_for_action, request.action_id, abandon_on_cancel=True
            )

        if cancellation.cancelled:
            self._finish_closed(request.action_id)
            raise ToolExecutionStoppedError("cancelled before async handler invocation")
        try:
            context = _tool_context(self._context_factory, request, cancellation)
            value = _invoke_async_handler(handler, dict(request.arguments), context)
            if not inspect.isawaitable(value):
                raise ToolExecutionUncertainError(
                    "async tool handler returned without awaiting"
                )
            raw = await value
            result = self._result_from_raw(request, raw)
        except ToolExecutionRejectedError:
            self._finish_closed(request.action_id)
            raise
        except ToolExecutionStoppedError:
            self._finish_closed(request.action_id)
            raise
        except ToolExecutionFailedError as error:
            result = ToolExecutionResult(request.action_id, False, error=error.code)
        except ToolExecutionUncertainError:
            self._finish_unknown(request.action_id)
            raise
        except anyio.get_cancelled_exc_class():
            # The async task was cancelled, but a remote operation may have
            # started.  The v3 scheduler will enter RECONCILING unless the
            # handler explicitly proved ``ToolExecutionStoppedError``.
            self._finish_unknown(request.action_id)
            raise
        except Exception as error:
            self._finish_unknown(request.action_id)
            raise ToolExecutionUncertainError(
                _exception_code("TOOL_PROGRAMMER", error)
            ) from error
        return self._finish_result(request.action_id, result)

    def reconcile(self, request: ToolExecutionRequest) -> ToolExecutionResult | None:
        with self._finished:
            original = self._requests.get(request.action_id)
            if original is not None and original.to_mapping() != request.to_mapping():
                raise ToolExecutionRejectedError(
                    "action id was reused with another request"
                )
            return self._results.get(request.action_id)

    async def reconcile_async(
        self, request: ToolExecutionRequest
    ) -> ToolExecutionResult | None:
        return self.reconcile(request)

    @property
    def cached_result_count(self) -> int:
        with self._lock:
            return len(self._results)

    def acknowledge_durable_result(self, request: ToolExecutionRequest) -> None:
        with self._finished:
            action_id = request.action_id
            original = self._requests.get(action_id)
            if original is not None and original.to_mapping() != request.to_mapping():
                raise ToolExecutionRejectedError(
                    "action id was reused with another request"
                )
            if (
                action_id not in self._results
                or action_id in self._in_flight
                or action_id in self._uncertain
            ):
                return
            self._durable_results[action_id] = None
            self._durable_results.move_to_end(action_id)
            while len(self._durable_results) > self._max_durable_results:
                expired, _ = self._durable_results.popitem(last=False)
                self._results.pop(expired, None)
                self._requests.pop(expired, None)

    def _wait_for_action(self, action_id: str) -> None:
        with self._finished:
            while action_id in self._in_flight:
                self._finished.wait()

    @staticmethod
    def _result_from_raw(
        request: ToolExecutionRequest, raw: object
    ) -> ToolExecutionResult:
        if isinstance(raw, ToolExecutionResult):
            if raw.action_id != request.action_id:
                raise ToolExecutionUncertainError(
                    "tool handler returned a result for another action"
                )
            return raw
        try:
            output = (
                json_copy(raw)
                if isinstance(raw, Mapping)
                else {"value": json_copy(raw)}
            )
            return ToolExecutionResult(request.action_id, True, output=output)
        except (TypeError, ValueError, WorkflowValidationError):
            return ToolExecutionResult(
                request.action_id, False, error="TOOL_OUTPUT_INVALID"
            )

    def _finish_result(
        self, action_id: str, result: ToolExecutionResult
    ) -> ToolExecutionResult:
        with self._finished:
            previous = self._results.get(action_id)
            self._in_flight.discard(action_id)
            self._uncertain.discard(action_id)
            if previous is None:
                self._results[action_id] = result
            self._finished.notify_all()
            return previous or result

    def _finish_closed(self, action_id: str) -> None:
        with self._finished:
            self._in_flight.discard(action_id)
            self._finished.notify_all()

    def _finish_unknown(self, action_id: str) -> None:
        with self._finished:
            self._in_flight.discard(action_id)
            self._uncertain.add(action_id)
            self._finished.notify_all()


class FunctionToolExecutor:
    """One trusted registry for ordinary and native-async Python tools.

    This is intentionally a composition of the existing
    :class:`LocalToolExecutor` and :class:`AsyncFunctionToolExecutor`, rather
    than a third hand-written action-state machine.  Registration chooses a
    delivery mode once from the callable; each child keeps its established
    exact-reference, idempotency, uncertainty, and durable-result behaviour.

    Native async dispatch is exposed through ``native_execute_async`` instead
    of a blanket ``execute_async`` method.  Hosts can then retain a capacity
    token for real synchronous worker threads while still awaiting an async
    tool natively.
    """

    def __init__(
        self,
        *,
        max_durable_results: int = 1_024,
        context_factory: ToolContextFactory | None = None,
    ) -> None:
        self._sync = LocalToolExecutor(
            max_durable_results=max_durable_results,
            context_factory=context_factory,
        )
        self._async = AsyncFunctionToolExecutor(
            max_durable_results=max_durable_results,
            context_factory=context_factory,
        )
        self._lock = RLock()
        self._modes: dict[tuple[str, str, str], str] = {}

    @staticmethod
    def _key(tool: ToolReference) -> tuple[str, str, str]:
        return tool.tool_id, tool.version, tool.package_digest

    def register(
        self,
        tool: ToolReference,
        handler: Callable[..., object],
        *,
        implementation_version: str | None = None,
    ) -> None:
        if not callable(handler):
            raise WorkflowValidationError("tool handler must be callable")
        key = self._key(tool)
        mode = "async" if inspect.iscoroutinefunction(handler) else "sync"
        with self._lock:
            if key in self._modes:
                raise ToolExecutionRejectedError("tool reference is already registered")
            if mode == "async":
                self._async.register(
                    tool,
                    handler,
                    implementation_version=implementation_version,
                )
            else:
                self._sync.register(
                    tool,
                    handler,
                    implementation_version=implementation_version,
                )
            self._modes[key] = mode

    def execution_binding(self, tool: ToolReference) -> dict[str, JsonValue]:
        if self._mode(tool) == "async":
            return self._async.execution_binding(tool)
        return self._sync.execution_binding(tool)

    def is_registered(self, tool: ToolReference) -> bool:
        with self._lock:
            return self._key(tool) in self._modes

    def _mode(self, tool: ToolReference) -> str | None:
        with self._lock:
            return self._modes.get(self._key(tool))

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        if self._mode(request.tool) == "async":
            return self._async.execute(request)
        return self._sync.execute(request)

    def execute_with_cancellation(
        self, request: ToolExecutionRequest, cancellation: CancellationToken
    ) -> ToolExecutionResult:
        """Run a registered sync handler with the host's live v3 token."""

        if self._mode(request.tool) == "async":
            return self._async.execute(request)
        return self._sync.execute_with_cancellation(request, cancellation)

    def reconcile(self, request: ToolExecutionRequest) -> ToolExecutionResult | None:
        if self._mode(request.tool) == "async":
            return self._async.reconcile(request)
        return self._sync.reconcile(request)

    def native_execute_async(
        self, request: ToolExecutionRequest, cancellation: CancellationToken
    ) -> object | None:
        """Return an awaitable only for a registered native async handler."""

        if self._mode(request.tool) != "async":
            return None
        return self._async.execute_async(request, cancellation)

    async def reconcile_async(
        self, request: ToolExecutionRequest
    ) -> ToolExecutionResult | None:
        if self._mode(request.tool) == "async":
            return await self._async.reconcile_async(request)
        return self._sync.reconcile(request)

    def acknowledge_durable_result(self, request: ToolExecutionRequest) -> None:
        if self._mode(request.tool) == "async":
            self._async.acknowledge_durable_result(request)
        else:
            self._sync.acknowledge_durable_result(request)

    @property
    def cached_result_count(self) -> int:
        return self._sync.cached_result_count + self._async.cached_result_count


@dataclass(frozen=True)
class _Next:
    kind: str
    step: StepSpec
    key: str
    definition: WorkflowSpec | None = None
    children: Sequence[StepSpec] = ()
    choice: int | None = None


class WorkflowEngine:
    """Run / resume / cancel / events API for an embeddable V6 workflow."""

    def __init__(
        self,
        journal: RunJournal,
        executor: ToolExecutor,
        drivers: Mapping[str, AgentDriver],
        *,
        workflows: Sequence[WorkflowSpec] = (),
        policy: ActionPolicy | None = None,
        runtime_binding: Mapping[str, JsonValue] | None = None,
    ) -> None:
        self._journal = journal
        self._executor = executor
        self._drivers = dict(drivers)
        self._workflows = {(item.workflow_id, item.version): item for item in workflows}
        self._policy = policy or AllowTrustedActions()
        copied_runtime_binding = json_copy(dict(runtime_binding or {}))
        if not isinstance(copied_runtime_binding, dict) or contains_secret_name(
            copied_runtime_binding
        ):
            raise WorkflowValidationError(
                "runtime binding must be credential-free JSON"
            )
        if "domain_lock" in copied_runtime_binding:
            # A domain lock is a generic frozen runtime fact from the engine's
            # perspective.  Its domain-specific source validation lives in
            # the authoring layer; this lightweight decoder prevents a direct
            # embedding caller from manufacturing a malformed v3 manifest.
            from .domain import DomainLock

            DomainLock.from_mapping(copied_runtime_binding["domain_lock"])
        self._runtime_binding = copied_runtime_binding
        journal_path = getattr(journal, "path", None)
        self._v3_resource_namespace: object = (
            ("sqlite", str(journal_path.resolve()))
            if journal_path is not None
            and callable(getattr(journal_path, "resolve", None))
            else ("journal", id(journal))
        )
        self._v3_transition_lock = RLock()
        self._v3_active_lock = RLock()
        self._v3_active_tokens: dict[str, CancellationToken] = {}
        self._v3_event_watchers: dict[str, list[EventWakeStream]] = {}
        if len(self._workflows) != len(tuple(workflows)):
            raise WorkflowValidationError(
                "subworkflow catalog contains duplicate versions"
            )
        self.workflow_services = WorkflowRuntimeServices(
            journal=self._journal,
            executor=self._executor,
            drivers=self._drivers,
            workflows=self._workflows,
            policy=self._policy,
            transition_lock=self._v3_transition_lock,
            active_lock=self._v3_active_lock,
            active_tokens=self._v3_active_tokens,
            event_watchers=self._v3_event_watchers,
            notify_event=self._notify_v3_event,
            acquire_resource=self._v3_acquire_resource,
            release_resource=self._v3_release_resource,
            reserve_tool_dispatch=self._reserve_tool_dispatch_async,
            release_tool_dispatch=self._release_tool_dispatch_async,
            manifest_workflows=self._manifest_workflows,
            verify_driver_bindings=self._verify_driver_bindings,
            validate_drivers=self._validate_drivers,
            driver_bindings=self._driver_bindings,
            execution_manifest=self._execution_manifest,
            driver_execution_binding=self._driver_execution_binding,
        )

    def _v3_resource_borrower(self, action_id: str) -> tuple[object, str]:
        """Disambiguate identical run IDs owned by independent journals."""

        return self._v3_resource_namespace, action_id

    async def _v3_acquire_resource(self, resource: str, action_id: str) -> None:
        """Acquire a process mutex without sharing an event-loop primitive."""

        try:
            import anyio
        except ImportError as error:
            raise RuntimeError(
                "Install platform-lightweight-agent-core[workflow]"
            ) from error
        ticket = _PROCESS_RESOURCE_REGISTRY.register(
            resource, self._v3_resource_borrower(action_id)
        )
        try:
            acquired = await anyio.to_thread.run_sync(
                _PROCESS_RESOURCE_REGISTRY.wait,
                ticket,
                abandon_on_cancel=True,
            )
        except BaseException:
            # ``ticket`` is registered before the worker starts.  Cancellation
            # therefore cannot leave a later-starting abandoned worker holding
            # the resource; cancel() resolves both sides of the race.
            _PROCESS_RESOURCE_REGISTRY.cancel(ticket)
            raise
        if not acquired:
            raise WorkflowRunError("PROCESS_RESOURCE_ACQUIRE_CANCELLED")

    def _v3_release_resource(self, resource: str, action_id: str) -> bool:
        """Release one exact action lease; return False for an idempotent race."""

        return _PROCESS_RESOURCE_REGISTRY.release(
            resource, self._v3_resource_borrower(action_id)
        )

    def run(
        self,
        workflow: WorkflowSpec,
        inputs: Mapping[str, JsonValue] | None = None,
        *,
        run_id: str | None = None,
    ) -> RunSnapshot:
        if workflow.schema_version in ASYNC_SCHEMA_VERSIONS:
            return self._run_v3_sync(self.run_async, workflow, inputs, run_id=run_id)
        values = json_copy(dict(inputs or {}))
        if contains_secret_name(values):
            raise WorkflowValidationError(
                "workflow inputs must use credential references, not credentials"
            )
        self._validate_drivers(workflow)
        run_id = run_id or f"run-{uuid4().hex}"
        bindings = self._driver_bindings(workflow)
        state: dict[str, JsonValue] = {
            "values": values,
            "completed": [],
            "branches": {},
            "actions": {},
            "agents": {},
            "approved_steps": [],
            "pending": None,
            "failure": None,
        }
        snapshot = RunSnapshot(
            run_id=run_id,
            workflow=workflow.to_mapping(),
            inputs=values,
            state=state,
            status="RUNNING",
            driver_bindings=bindings,
            execution_id=f"execution-{uuid4().hex}",
            execution_manifest=self._execution_manifest(workflow),
        )
        snapshot = self._journal.create(
            snapshot,
            "RUN_STARTED",
            {
                "workflow_id": workflow.workflow_id,
                "version": workflow.version,
                "definition_digest": workflow.definition_digest,
            },
        )
        return self._drive(snapshot)

    def resume(
        self,
        run_id: str | RunHandle,
        *,
        approved: bool | None = None,
        approvals: Sequence[ApprovalDecision] = (),
        expected_execution_id: str | None = None,
    ) -> RunSnapshot:
        run_id, expected_execution_id = _run_target(run_id, expected_execution_id)
        snapshot = _journal_get(self._journal, run_id, expected_execution_id)
        if snapshot.status == BACKUP_RECOVERY_STATUS:
            raise WorkflowRunError("BACKUP_RECOVERY_REQUIRED")
        workflow = WorkflowSpec.from_mapping(snapshot.workflow)
        if workflow.schema_version in ASYNC_SCHEMA_VERSIONS:
            if approved is not None:
                raise WorkflowRunError(
                    "v3/v4 approvals require ApprovalDecision with an approval_id"
                )
            return self._run_v3_sync(
                self.resume_async,
                run_id,
                approvals=approvals,
                expected_execution_id=expected_execution_id,
            )
        if approvals:
            raise WorkflowRunError("v1/v2 workflows accept one boolean approval")
        self._verify_driver_bindings(snapshot)
        if snapshot.status in {"SUCCEEDED", "BLOCKED", "CANCELLED"}:
            return snapshot
        if snapshot.status == "RECONCILING":
            state = _state(snapshot)
            reconciling = [
                action_id
                for action_id, action in state["actions"].items()
                if isinstance(action, Mapping) and action.get("status") == "RECONCILING"
            ]
            if not reconciling:
                raise WorkflowRunError("reconciling run has no unresolved action")
            action = _action(state, reconciling[0])
            request = ToolExecutionRequest.from_mapping(action["request"])
            try:
                result = self._executor.reconcile(request)
            except ToolExecutionRejectedError:
                state["failure"] = {
                    "code": "TOOL_RECONCILIATION_REJECTED",
                    "action_id": request.action_id,
                }
                return self._commit(
                    snapshot,
                    state,
                    "BLOCKED",
                    "TOOL_RECONCILIATION_REJECTED",
                    {
                        "action_id": request.action_id,
                        "error_code": "TOOL_RECONCILIATION_REJECTED",
                    },
                )
            if result is None:
                return snapshot
            return self._drive(self._record_action_result(snapshot, result))
        state = _state(snapshot)
        pending = state.get("pending")
        if snapshot.status == "WAITING_APPROVAL":
            if approved is None:
                raise WorkflowRunError("approval decision is required")
            if not isinstance(pending, Mapping):
                raise WorkflowRunError("approval state is missing")
            if not approved:
                state["pending"] = None
                state["failure"] = {
                    "code": "APPROVAL_REJECTED",
                    "step_id": pending.get("step_id"),
                }
                return self._commit(
                    snapshot,
                    state,
                    "BLOCKED",
                    "APPROVAL_REJECTED",
                    {"step_id": pending.get("step_id")},
                )
            if pending.get("kind") == "STEP_APPROVAL":
                approved_steps = set(_strings(state.get("approved_steps")))
                approved_steps.add(str(pending["step_id"]))
                state["approved_steps"] = sorted(approved_steps)
            elif pending.get("kind") == "ACTION_APPROVAL":
                action = _action(state, str(pending["action_id"]))
                if action.get("status") != "PENDING_APPROVAL":
                    raise WorkflowRunError("approval does not match a pending action")
                action["status"] = "READY"
            else:
                raise WorkflowRunError("unknown approval type")
            state["pending"] = None
            snapshot = self._commit(
                snapshot,
                state,
                "RUNNING",
                "APPROVAL_GRANTED",
                {
                    "step_id": pending.get("step_id"),
                    "action_id": pending.get("action_id"),
                },
            )
        return self._drive(snapshot)

    def cancel(
        self,
        run_id: str | RunHandle,
        *,
        reason: str = "cancelled by caller",
        expected_execution_id: str | None = None,
    ) -> RunSnapshot:
        run_id, expected_execution_id = _run_target(run_id, expected_execution_id)
        snapshot = _journal_get(self._journal, run_id, expected_execution_id)
        if snapshot.status == BACKUP_RECOVERY_STATUS:
            raise WorkflowRunError("BACKUP_RECOVERY_REQUIRED")
        if (
            WorkflowSpec.from_mapping(snapshot.workflow).schema_version
            in ASYNC_SCHEMA_VERSIONS
        ):
            raise WorkflowRunError(
                "cancel() is not valid for v3/v4; use request_cancel()"
            )
        if snapshot.status in {"SUCCEEDED", "BLOCKED", "CANCELLED"}:
            return snapshot
        if not isinstance(reason, str) or not reason:
            raise WorkflowRunError("cancellation reason is required")
        state = _state(snapshot)
        state["pending"] = None
        # This journal can be exported. Keep a fixed code here; applications
        # that need a human explanation store it in their protected audit log.
        state["failure"] = {"code": "CANCELLED", "reason_code": "CANCELLED_BY_CALLER"}
        return self._commit(
            snapshot,
            state,
            "CANCELLED",
            "CANCELLATION_REQUESTED",
            {"reason_code": "CANCELLED_BY_CALLER"},
        )

    def reconcile(
        self,
        run_id: str | RunHandle,
        action_id: str,
        result: ToolExecutionResult,
        *,
        expected_execution_id: str | None = None,
    ) -> RunSnapshot:
        run_id, expected_execution_id = _run_target(run_id, expected_execution_id)
        snapshot = _journal_get(self._journal, run_id, expected_execution_id)
        if snapshot.status == BACKUP_RECOVERY_STATUS:
            raise WorkflowRunError("BACKUP_RECOVERY_REQUIRED")
        if (
            WorkflowSpec.from_mapping(snapshot.workflow).schema_version
            in ASYNC_SCHEMA_VERSIONS
        ):
            return self._run_v3_sync(
                self.reconcile_async,
                run_id,
                action_id,
                result,
                expected_execution_id=expected_execution_id,
            )
        self._verify_driver_bindings(snapshot)
        if snapshot.status != "RECONCILING":
            raise WorkflowRunError(
                "only a reconciling run can accept an external result"
            )
        if result.action_id != action_id:
            raise WorkflowRunError("reconciled result refers to another action")
        state = _state(snapshot)
        action = _action(state, action_id)
        if action.get("status") != "RECONCILING":
            raise WorkflowRunError(
                "only a reconciling action can accept an external result"
            )
        request = ToolExecutionRequest.from_mapping(action["request"])
        if request.action_id != action_id:
            raise WorkflowRunError("journal action binding is invalid")
        state["actions"][action_id]["status"] = "RESOLVED"
        state["actions"][action_id]["result"] = result.to_mapping()
        snapshot = self._commit(
            snapshot,
            state,
            "RUNNING",
            "ACTION_RECONCILED",
            {"action_id": action_id, "succeeded": result.succeeded},
        )
        return self._drive(snapshot)

    def list_runs(
        self,
        filter: RunQueryFilter | None = None,
        cursor: RunListCursor | None = None,
        limit: int = 50,
    ) -> RunPage:
        """List compact persisted run metadata when the journal supports it."""

        return _journal_list_runs(self._journal, filter, cursor, limit)

    def events(
        self,
        run_id: str | RunHandle,
        *,
        after: int = 0,
        limit: int = 256,
        expected_execution_id: str | None = None,
        cursor: EventCursor | None = None,
    ):
        run_id, after, expected_execution_id = _event_target(
            run_id,
            after=after,
            expected_execution_id=expected_execution_id,
            cursor=cursor,
        )
        return _journal_events(
            self._journal,
            run_id,
            after=after,
            limit=limit,
            expected_execution_id=expected_execution_id,
        )

    def status(
        self,
        run_id: str | RunHandle,
        *,
        expected_execution_id: str | None = None,
    ):  # type: ignore[no-untyped-def]
        """Return the journal's lightweight status projection."""

        run_id, expected_execution_id = _run_target(run_id, expected_execution_id)
        return _journal_status(self._journal, run_id, expected_execution_id)

    def get(
        self,
        run_id: str | RunHandle,
        *,
        expected_execution_id: str | None = None,
    ) -> RunSnapshot:
        run_id, expected_execution_id = _run_target(run_id, expected_execution_id)
        return _journal_get(self._journal, run_id, expected_execution_id)

    def inspection(
        self,
        run_id: str | RunHandle,
        *,
        expected_execution_id: str | None = None,
    ):  # type: ignore[no-untyped-def]
        """Return the smallest journal view needed by operator read paths."""

        run_id, expected_execution_id = _run_target(run_id, expected_execution_id)
        inspect = getattr(self._journal, "inspection", None)
        if callable(inspect):
            if expected_execution_id is None:
                return inspect(run_id)
            return inspect(run_id, expected_execution_id=expected_execution_id)
        return _journal_get(self._journal, run_id, expected_execution_id)

    def view(
        self, run_id: str | RunHandle, *, expected_execution_id: str | None = None
    ):  # type: ignore[no-untyped-def]
        from .inspection import read_run_view

        run_id, expected_execution_id = _run_target(run_id, expected_execution_id)
        return read_run_view(self._journal, run_id, expected_execution_id)

    def explain(
        self,
        run_id: str | RunHandle,
        *,
        expected_execution_id: str | None = None,
    ):  # type: ignore[no-untyped-def]
        """Return a credential-safe, read-only explanation of a run."""

        from .inspection import explain_run

        return explain_run(
            self.inspection(run_id, expected_execution_id=expected_execution_id)
        )

    def preview_recovery(
        self,
        run_id: str | RunHandle,
        *,
        expected_execution_id: str | None = None,
    ):  # type: ignore[no-untyped-def]
        """Show recovery's possible next operations without performing them."""

        from .inspection import preview_recovery

        return preview_recovery(
            self.inspection(run_id, expected_execution_id=expected_execution_id)
        )

    def release_backup_recovery(
        self,
        run_id: str | RunHandle,
        *,
        source_owner_stopped: bool,
        strategy: str = "query",
        expected_execution_id: str | None = None,
    ) -> RunSnapshot:
        """Acknowledge ownership transfer for a safely restored SQLite run.

        Restoring a backup never authorizes new dispatches automatically.  The
        SQLite journal owns the recovery gate because it is the component that
        created it; this engine wrapper keeps embedding code on the same
        public surface as ordinary resume/reconcile operations.
        """

        release = getattr(self._journal, "release_backup_recovery", None)
        if not callable(release):
            raise WorkflowRunError("BACKUP_RECOVERY_UNSUPPORTED")
        run_id, expected_execution_id = _run_target(run_id, expected_execution_id)
        if expected_execution_id is None:
            return release(
                run_id,
                source_owner_stopped=source_owner_stopped,
                strategy=strategy,
            )
        return release(
            run_id,
            source_owner_stopped=source_owner_stopped,
            strategy=strategy,
            expected_execution_id=expected_execution_id,
        )

    async def run_async(
        self,
        workflow: WorkflowSpec,
        inputs: Mapping[str, JsonValue] | None = None,
        *,
        run_id: str | None = None,
    ) -> RunSnapshot:
        """Start v3/v4 natively, or adapt a legacy run through a worker thread."""

        if workflow.schema_version not in ASYNC_SCHEMA_VERSIONS:
            try:
                import anyio
            except ImportError as error:
                raise RuntimeError(
                    "Install platform-lightweight-agent-core[workflow] for async APIs"
                ) from error
            return await anyio.to_thread.run_sync(
                functools.partial(self.run, workflow, inputs, run_id=run_id)
            )
        from .workflow_v3 import run_v3

        return await run_v3(self, workflow, inputs or {}, run_id=run_id)

    async def resume_async(
        self,
        run_id: str | RunHandle,
        *,
        approvals: Sequence[ApprovalDecision] = (),
        advance: bool = True,
        expected_execution_id: str | None = None,
    ) -> RunSnapshot:
        run_id, expected_execution_id = _run_target(run_id, expected_execution_id)
        snapshot = _journal_get(self._journal, run_id, expected_execution_id)
        if snapshot.status == BACKUP_RECOVERY_STATUS:
            raise WorkflowRunError("BACKUP_RECOVERY_REQUIRED")
        workflow = WorkflowSpec.from_mapping(snapshot.workflow)
        if workflow.schema_version not in ASYNC_SCHEMA_VERSIONS:
            if approvals:
                raise WorkflowRunError("legacy workflows accept one boolean approval")
            try:
                import anyio
            except ImportError as error:
                raise RuntimeError(
                    "Install platform-lightweight-agent-core[workflow] for async APIs"
                ) from error
            return await anyio.to_thread.run_sync(
                functools.partial(
                    self.resume,
                    run_id,
                    expected_execution_id=expected_execution_id,
                )
            )
        from .workflow_v3 import resume_v3

        return await resume_v3(self, snapshot, approvals, advance=advance)

    def request_cancel(
        self,
        run_id: str | RunHandle,
        *,
        reason: str = "cancelled by caller",
        expected_execution_id: str | None = None,
    ) -> RunSnapshot:
        return self._run_v3_sync(
            self.request_cancel_async,
            run_id,
            reason=reason,
            expected_execution_id=expected_execution_id,
        )

    async def request_cancel_async(
        self,
        run_id: str | RunHandle,
        *,
        reason: str = "cancelled by caller",
        expected_execution_id: str | None = None,
    ) -> RunSnapshot:
        from .workflow_v3 import request_cancel_v3

        run_id, expected_execution_id = _run_target(run_id, expected_execution_id)
        return await request_cancel_v3(
            self,
            run_id,
            reason=reason,
            expected_execution_id=expected_execution_id,
        )

    async def reconcile_async(
        self,
        run_id: str | RunHandle,
        action_id: str,
        result: ToolExecutionResult,
        *,
        advance: bool = True,
        expected_execution_id: str | None = None,
    ) -> RunSnapshot:
        run_id, expected_execution_id = _run_target(run_id, expected_execution_id)
        snapshot = _journal_get(self._journal, run_id, expected_execution_id)
        if snapshot.status == BACKUP_RECOVERY_STATUS:
            raise WorkflowRunError("BACKUP_RECOVERY_REQUIRED")
        workflow = WorkflowSpec.from_mapping(snapshot.workflow)
        if workflow.schema_version not in ASYNC_SCHEMA_VERSIONS:
            try:
                import anyio
            except ImportError as error:
                raise RuntimeError(
                    "Install platform-lightweight-agent-core[workflow] for async APIs"
                ) from error
            return await anyio.to_thread.run_sync(
                functools.partial(
                    self.reconcile,
                    run_id,
                    action_id,
                    result,
                    expected_execution_id=expected_execution_id,
                )
            )
        from .workflow_v3 import reconcile_v3

        return await reconcile_v3(self, snapshot, action_id, result, advance=advance)

    async def stream_events(
        self,
        run_id: str | RunHandle,
        *,
        after: int = 0,
        expected_execution_id: str | None = None,
        cursor: EventCursor | None = None,
    ) -> AsyncIterator[RunEvent]:
        from .workflow_v3 import stream_v3_events

        run_id, after, expected_execution_id = _event_target(
            run_id,
            after=after,
            expected_execution_id=expected_execution_id,
            cursor=cursor,
        )
        async for event in stream_v3_events(
            self,
            run_id,
            after=after,
            expected_execution_id=expected_execution_id,
        ):
            yield event

    def _run_v3_sync(self, function: Callable[..., Any], *args: Any, **kwargs: Any):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise WorkflowRunError(
                "v3/v4 synchronous APIs cannot run inside an event loop; use the async API"
            )
        try:
            import anyio
        except ImportError as error:
            raise RuntimeError(
                "Install platform-lightweight-agent-core[workflow] for v3/v4 workflows"
            ) from error
        return anyio.run(functools.partial(function, *args, **kwargs))

    def _notify_v3_event(self, run_id: str) -> None:
        """Best-effort local wakeup; persisted events remain authoritative."""

        try:
            import anyio
        except ImportError:
            return

        with self._v3_active_lock:
            watchers = tuple(self._v3_event_watchers.get(run_id, ()))
        for watcher in watchers:
            try:
                watcher.send_nowait(None)
            except (
                anyio.BrokenResourceError,
                anyio.ClosedResourceError,
                anyio.WouldBlock,
            ):
                continue

    def _drive(self, snapshot: RunSnapshot) -> RunSnapshot:
        # Every iteration either saves one event or returns.  This keeps restart
        # points small and avoids re-running already observed operations.
        frozen_workflows = self._manifest_workflows(snapshot)
        for _ in range(10_000):
            if snapshot.status != "RUNNING":
                return snapshot
            workflow = WorkflowSpec.from_mapping(snapshot.workflow)
            state = _state(snapshot)
            next_item = self._next(
                workflow.steps,
                "",
                state,
                workflows=frozen_workflows,
                definition=workflow,
            )
            if next_item is None:
                return self._commit(snapshot, state, "SUCCEEDED", "RUN_SUCCEEDED", {})
            snapshot = self._advance(snapshot, workflow, state, next_item)
        state = _state(snapshot)
        state["failure"] = {"code": "ENGINE_STEP_LIMIT"}
        return self._commit(snapshot, state, "BLOCKED", "ENGINE_STEP_LIMIT", {})

    def _next(
        self,
        steps: Sequence[StepSpec],
        prefix: str,
        state: Mapping[str, Any],
        *,
        workflows: Mapping[tuple[str, str], WorkflowSpec] | None = None,
        definition: WorkflowSpec | None = None,
    ) -> _Next | None:
        catalog = self._workflows if workflows is None else workflows
        if definition is None:
            raise WorkflowRunError("workflow definition context is required")
        completed = set(_strings(state.get("completed")))
        branches = state.get("branches", {})
        for step in steps:
            key = f"{prefix}{step.step_id}"
            if key in completed:
                continue
            if step.kind is StepKind.CONDITION:
                choice = branches.get(key) if isinstance(branches, Mapping) else None
                if choice is None:
                    return _Next("CHOOSE", step, key, definition)
                if choice not in {0, 1}:
                    return _Next("INVALID", step, key, definition)
                item = self._next(
                    step.children[int(choice)],
                    key + "/",
                    state,
                    workflows=catalog,
                    definition=definition,
                )
                if item is not None:
                    return item
                return _Next("COMPLETE", step, key, definition)
            if step.kind is StepKind.PARALLEL:
                for branch in step.children:
                    item = self._next(
                        branch,
                        key + "/",
                        state,
                        workflows=catalog,
                        definition=definition,
                    )
                    if item is not None:
                        return item
                return _Next("COMPLETE", step, key, definition)
            if step.kind is StepKind.SUBWORKFLOW:
                child = catalog.get(
                    (str(step.config["workflow_id"]), str(step.config["version"]))
                )
                if child is None:
                    return _Next("MISSING_SUBWORKFLOW", step, key, definition)
                item = self._next(
                    child.steps,
                    key + "/",
                    state,
                    workflows=catalog,
                    definition=child,
                )
                if item is not None:
                    return item
                return _Next("COMPLETE", step, key, definition)
            return _Next("LEAF", step, key, definition)
        return None

    def _advance(
        self,
        snapshot: RunSnapshot,
        workflow: WorkflowSpec,
        state: dict[str, Any],
        item: _Next,
    ) -> RunSnapshot:
        if item.kind == "CHOOSE":
            branch = (
                _pointer(state["values"], str(item.step.config["path"]))
                == item.step.config["equals"]
            )
            state["branches"][item.key] = 0 if branch else 1
            return self._commit(
                snapshot,
                state,
                "RUNNING",
                "CONDITION_SELECTED",
                {"step_id": item.key, "branch": "then" if branch else "otherwise"},
            )
        if item.kind == "COMPLETE":
            return self._complete_step(snapshot, state, item.key)
        if item.kind == "MISSING_SUBWORKFLOW":
            state["failure"] = {
                "code": "SUBWORKFLOW_NOT_REGISTERED",
                "step_id": item.key,
            }
            return self._commit(
                snapshot,
                state,
                "BLOCKED",
                "SUBWORKFLOW_NOT_REGISTERED",
                {"step_id": item.key},
            )
        if item.kind == "INVALID":
            state["failure"] = {"code": "INVALID_BRANCH_STATE", "step_id": item.key}
            return self._commit(
                snapshot,
                state,
                "BLOCKED",
                "INVALID_BRANCH_STATE",
                {"step_id": item.key},
            )
        if item.step.kind is StepKind.TOOL:
            return self._advance_tool_step(snapshot, state, item.step, item.key)
        if item.step.kind is StepKind.APPROVAL:
            return self._advance_approval_step(snapshot, state, item.step, item.key)
        if item.step.kind is StepKind.AGENT:
            return self._advance_agent_step(
                snapshot, item.definition or workflow, state, item.step, item.key
            )
        state["failure"] = {"code": "INVALID_LEAF", "step_id": item.key}
        return self._commit(
            snapshot, state, "BLOCKED", "INVALID_LEAF", {"step_id": item.key}
        )

    def _advance_tool_step(
        self, snapshot: RunSnapshot, state: dict[str, Any], step: StepSpec, key: str
    ) -> RunSnapshot:
        action_id = _action_id(snapshot.execution_id, key, 0)
        actions = state["actions"]
        if action_id not in actions:
            request = ToolExecutionRequest(
                action_id,
                ToolReference.from_mapping(step.config["tool"]),
                _resolve_templates(step.config.get("arguments", {}), state["values"]),
            )
            approval = bool(step.config.get("approval_required", True))
            actions[action_id] = {
                "request": request.to_mapping(),
                "status": "PENDING_APPROVAL" if approval else "READY",
                "step_id": key,
            }
            if approval:
                state["pending"] = {
                    "kind": "ACTION_APPROVAL",
                    "step_id": key,
                    "action_id": action_id,
                }
                return self._commit(
                    snapshot,
                    state,
                    "WAITING_APPROVAL",
                    "ACTION_PROPOSED",
                    {
                        "step_id": key,
                        "action_id": action_id,
                        "tool_id": request.tool.tool_id,
                    },
                )
            return self._commit(
                snapshot,
                state,
                "RUNNING",
                "ACTION_READY",
                {
                    "step_id": key,
                    "action_id": action_id,
                    "tool_id": request.tool.tool_id,
                },
            )
        updated = self._advance_action(snapshot, state, action_id)
        if updated.status != "RUNNING":
            return updated
        current = _state(updated)
        action = _action(current, action_id)
        if action.get("status") == "RESOLVED":
            result = ToolExecutionResult.from_mapping(action["result"])
            if not result.succeeded:
                current["failure"] = {
                    "code": "TOOL_FAILED",
                    "step_id": key,
                    "action_id": action_id,
                }
                return self._commit(
                    updated,
                    current,
                    "BLOCKED",
                    "TOOL_FAILED",
                    {"step_id": key, "action_id": action_id},
                )
            current["values"][key] = result.output or {}
            return self._complete_step(updated, current, key)
        return updated

    def _advance_approval_step(
        self, snapshot: RunSnapshot, state: dict[str, Any], step: StepSpec, key: str
    ) -> RunSnapshot:
        if key in set(_strings(state.get("approved_steps"))):
            state["values"][key] = {"approved": True}
            return self._complete_step(snapshot, state, key)
        pending = state.get("pending")
        if pending is not None:
            return snapshot
        state["pending"] = {
            "kind": "STEP_APPROVAL",
            "step_id": key,
            "prompt": step.config["prompt"],
        }
        return self._commit(
            snapshot,
            state,
            "WAITING_APPROVAL",
            "APPROVAL_REQUESTED",
            {"step_id": key, "prompt": step.config["prompt"]},
        )

    def _advance_agent_step(
        self,
        snapshot: RunSnapshot,
        workflow: WorkflowSpec,
        state: dict[str, Any],
        step: StepSpec,
        key: str,
    ) -> RunSnapshot:
        agents = state["agents"]
        new_agent = key not in agents
        agent_state = agents.setdefault(
            key,
            {
                "turns": 0,
                "actions": [],
                "messages": [],
                "resolved_action_batches": [],
                "driver_continuation": None,
            },
        )
        action_ids = _strings(agent_state.get("actions"))
        if action_ids:
            for action_id in action_ids:
                action = _action(state, action_id)
                if action.get("status") != "RESOLVED":
                    return self._advance_action(snapshot, state, action_id)
            if workflow.schema_version == "v6-light-workflow-v2":
                aliases = {
                    (
                        contract.tool.tool_id,
                        contract.tool.version,
                        contract.tool.package_digest,
                    ): contract.name
                    for contract in (
                        AgentToolSpec.from_mapping(item)
                        for item in step.config.get("tools", [])
                    )
                }
                results: list[dict[str, JsonValue]] = []
                for action_id in action_ids:
                    action = _action(state, action_id)
                    request = ToolExecutionRequest.from_mapping(action["request"])
                    result = ToolExecutionResult.from_mapping(action["result"])
                    identity = (
                        request.tool.tool_id,
                        request.tool.version,
                        request.tool.package_digest,
                    )
                    results.append(
                        {
                            "name": aliases[identity],
                            "arguments": dict(request.arguments),
                            "succeeded": result.succeeded,
                            "output": (
                                dict(result.output)
                                if result.output is not None
                                else None
                            ),
                            "error": result.error,
                        }
                    )
            else:
                results = [
                    dict(_action(state, action_id)["result"])
                    for action_id in action_ids
                ]
            agent_state["messages"] = [
                *agent_state.get("messages", []),
                {"tool_results": results},
            ]
            agent_state["resolved_action_batches"] = [
                *agent_state.get("resolved_action_batches", []),
                list(action_ids),
            ]
            agent_state["actions"] = []
            return self._commit(
                snapshot,
                state,
                "RUNNING",
                "AGENT_TOOL_RESULTS_OBSERVED",
                {"step_id": key, "action_ids": action_ids},
            )
        turns = agent_state.get("turns")
        if not isinstance(turns, int) or turns >= int(step.config.get("max_turns", 1)):
            state["failure"] = {"code": "AGENT_TURN_LIMIT", "step_id": key}
            return self._commit(
                snapshot, state, "BLOCKED", "AGENT_TURN_LIMIT", {"step_id": key}
            )
        driver_name = str(step.config["driver"])
        driver = self._drivers.get(driver_name)
        if driver is None:
            state["failure"] = {"code": "AGENT_DRIVER_UNAVAILABLE", "step_id": key}
            return self._commit(
                snapshot, state, "BLOCKED", "AGENT_DRIVER_UNAVAILABLE", {"step_id": key}
            )
        if workflow.schema_version == "v6-light-workflow-v2":
            tool_specs = tuple(
                AgentToolSpec.from_mapping(item)
                for item in step.config.get("tools", [])
            )
            allowed_tools = tuple(item.tool for item in tool_specs)
        else:
            tool_specs = ()
            allowed_tools = tuple(
                ToolReference.from_mapping(item)
                for item in step.config.get("tools", [])
            )
        context = AgentContext(
            run_id=snapshot.run_id,
            workflow_id=workflow.workflow_id,
            workflow_version=workflow.version,
            step_id=key,
            turn=turns + 1,
            inputs=snapshot.inputs,
            step_input=_resolve_templates(
                step.config.get("input", {}), state["values"]
            ),
            values=state["values"],
            prior_tool_results=tuple(agent_state.get("messages", [])),
            allowed_tools=allowed_tools,
            workflow_schema_version=workflow.schema_version,
            tool_specs=tool_specs,
            driver_continuation=_v2_driver_continuation(agent_state),
            deferred_tool_results=_v2_deferred_tool_results(
                state, agent_state, tool_specs
            ),
            definition_workflow_id=workflow.workflow_id,
            definition_workflow_version=workflow.version,
            definition_step_id=step.step_id,
        )
        invocations = agent_state.get("model_invocations")
        if invocations is None:
            if not new_agent:
                # Pre-ledger snapshots cannot prove whether a model call was
                # in flight at the crash boundary. Do not silently replay it.
                state["failure"] = {
                    "code": "AGENT_REQUEST_STATE_UNKNOWN",
                    "step_id": key,
                }
                return self._commit(
                    snapshot,
                    state,
                    "BLOCKED",
                    "AGENT_REQUEST_STATE_UNKNOWN",
                    {"step_id": key},
                )
            invocations = agent_state["model_invocations"] = {}
            agent_state["usage"] = {
                "driver_invocations": 0,
                "model_requests": 0,
                "model_requests_unknown": False,
                "total_tokens": 0,
                "tokens_unknown": False,
            }
        if not isinstance(invocations, dict):
            state["failure"] = {"code": "AGENT_REQUEST_STATE_INVALID", "step_id": key}
            return self._commit(
                snapshot,
                state,
                "BLOCKED",
                "AGENT_REQUEST_STATE_INVALID",
                {"step_id": key},
            )
        if any(
            isinstance(item, Mapping) and item.get("status") == "DISPATCHED"
            for item in invocations.values()
        ):
            for item in invocations.values():
                if isinstance(item, dict) and item.get("status") == "DISPATCHED":
                    item["status"] = "USAGE_UNKNOWN"
            agent_state["usage"]["model_requests_unknown"] = True
            agent_state["usage"]["tokens_unknown"] = True
            state["failure"] = {"code": "AGENT_USAGE_UNKNOWN", "step_id": key}
            return self._commit(
                snapshot,
                state,
                "BLOCKED",
                "AGENT_USAGE_UNKNOWN",
                {"step_id": key},
            )
        reserved = [
            (invocation_id, item)
            for invocation_id, item in invocations.items()
            if isinstance(item, dict) and item.get("status") == "RESERVED"
        ]
        if len(reserved) > 1 or (reserved and reserved[0][1].get("turn") != turns + 1):
            state["failure"] = {"code": "AGENT_REQUEST_STATE_INVALID", "step_id": key}
            return self._commit(
                snapshot,
                state,
                "BLOCKED",
                "AGENT_REQUEST_STATE_INVALID",
                {"step_id": key},
            )
        if not reserved:
            raw_id = f"{snapshot.execution_id}:{key}:model:{turns + 1}".encode()
            invocation_id = "model-" + hashlib.sha256(raw_id).hexdigest()[:40]
            if invocation_id in invocations:
                state["failure"] = {
                    "code": "AGENT_REQUEST_STATE_INVALID",
                    "step_id": key,
                }
                return self._commit(
                    snapshot,
                    state,
                    "BLOCKED",
                    "AGENT_REQUEST_STATE_INVALID",
                    {"step_id": key},
                )
            invocations[invocation_id] = {"turn": turns + 1, "status": "RESERVED"}
            return self._commit(
                snapshot,
                state,
                "RUNNING",
                "AGENT_REQUEST_RESERVED",
                {"step_id": key, "invocation_id": invocation_id},
            )
        invocation_id = reserved[0][0]
        reserved[0][1]["status"] = "DISPATCHED"
        snapshot = self._commit(
            snapshot,
            state,
            "RUNNING",
            "AGENT_REQUEST_DISPATCHED",
            {"step_id": key, "invocation_id": invocation_id},
        )
        state = _state(snapshot)
        agent_state = state["agents"][key]
        invocation = agent_state["model_invocations"][invocation_id]
        usage_state = agent_state["usage"]

        def record_invocation(
            *,
            request_count: int | None = None,
            total_tokens: int | None = None,
            failed: bool = False,
        ) -> None:
            if request_count is not None and request_count < 1:
                request_count = None
            usage_state["driver_invocations"] += 1
            if request_count is None:
                usage_state["model_requests_unknown"] = True
            usage_state["model_requests"] += max(1, request_count or 0)
            if total_tokens is None:
                usage_state["tokens_unknown"] = True
            else:
                usage_state["total_tokens"] += total_tokens
            invocation["status"] = (
                "REQUEST_LIMIT_REACHED"
                if failed and request_count is not None
                else "USAGE_UNKNOWN"
                if failed
                else "USAGE_CONFIRMED"
                if request_count is not None and total_tokens is not None
                else "USAGE_ESTIMATED"
            )
            invocation["request_count"] = max(1, request_count or 0)
            if total_tokens is not None:
                invocation["total_tokens"] = total_tokens

        try:
            decision = driver.decide(context)
        except AgentDriverFailure as error:
            record_invocation(request_count=error.model_request_count, failed=True)
            state["failure"] = {"code": error.code, "step_id": key}
            return self._commit(
                snapshot,
                state,
                "BLOCKED",
                error.code,
                {"step_id": key},
            )
        # Drivers are optional framework adapters; their implementation errors
        # must become a stable, resumable run state rather than escape the core.
        except Exception as error:  # noqa: BLE001
            record_invocation(failed=True)
            state["failure"] = {
                "code": "AGENT_DRIVER_ERROR",
                "step_id": key,
                "error_code": _exception_code("AGENT_DRIVER", error),
            }
            return self._commit(
                snapshot,
                state,
                "BLOCKED",
                "AGENT_DRIVER_ERROR",
                {"step_id": key, "error_code": _exception_code("AGENT_DRIVER", error)},
            )
        if not isinstance(decision, AgentDecision):
            record_invocation(failed=True)
            state["failure"] = {"code": "AGENT_DECISION_INVALID", "step_id": key}
            return self._commit(
                snapshot, state, "BLOCKED", "AGENT_DECISION_INVALID", {"step_id": key}
            )
        record_invocation(
            request_count=(
                decision.usage.get("request_count")
                if decision.usage is not None
                else None
            ),
            total_tokens=(
                decision.usage.get("total_tokens")
                if decision.usage is not None
                else None
            ),
        )
        agent_state["turns"] = turns + 1
        if decision.tool_calls:
            agent_state["driver_continuation"] = (
                json_copy(decision.continuation)
                if decision.continuation is not None
                else None
            )
        else:
            # The opaque provider correlation is needed only while a native
            # tool batch is outstanding.  Terminal state keeps neutral action
            # facts/call IDs and drops the now-dead serialized conversation.
            agent_state["driver_continuation"] = None
        if decision.message or workflow.schema_version == "v6-light-workflow-v1":
            agent_state["messages"] = [
                *agent_state.get("messages", []),
                {"assistant": decision.message},
            ]
        if not decision.tool_calls:
            state["values"][key] = {"message": decision.message}
            return self._complete_step(
                snapshot,
                state,
                key,
                event_type="AGENT_REPLY_ACCEPTED",
                payload={"step_id": key, "turn": turns + 1},
            )
        created: list[str] = []
        allowed_tools = {
            (tool.tool_id, tool.version, tool.package_digest)
            for tool in context.allowed_tools
        }
        for index, call in enumerate(decision.tool_calls):
            tool_identity = (
                call.tool.tool_id,
                call.tool.version,
                call.tool.package_digest,
            )
            if tool_identity not in allowed_tools:
                state["failure"] = {
                    "code": "AGENT_TOOL_SCOPE_DENIED",
                    "step_id": key,
                }
                return self._commit(
                    snapshot,
                    state,
                    "BLOCKED",
                    "AGENT_TOOL_SCOPE_DENIED",
                    {"step_id": key, "tool_id": call.tool.tool_id},
                )
            action_id = _action_id(snapshot.execution_id, key, turns * 100 + index)
            if action_id in state["actions"]:
                state["failure"] = {"code": "ACTION_ID_CONFLICT", "step_id": key}
                return self._commit(
                    snapshot, state, "BLOCKED", "ACTION_ID_CONFLICT", {"step_id": key}
                )
            request = ToolExecutionRequest(
                action_id,
                call.tool,
                (
                    dict(call.arguments)
                    if workflow.schema_version == "v6-light-workflow-v2"
                    else _resolve_templates(call.arguments, state["values"])
                ),
            )
            state["actions"][action_id] = {
                "request": request.to_mapping(),
                "status": "PENDING_APPROVAL",
                "step_id": key,
                "source": "agent",
                "model_call_id": call.call_id,
            }
            created.append(action_id)
        agent_state["actions"] = created
        state["pending"] = {
            "kind": "ACTION_APPROVAL",
            "step_id": key,
            "action_id": created[0],
        }
        return self._commit(
            snapshot,
            state,
            "WAITING_APPROVAL",
            "AGENT_ACTIONS_PROPOSED",
            {"step_id": key, "turn": turns + 1, "action_ids": created},
        )

    def _advance_action(
        self, snapshot: RunSnapshot, state: dict[str, Any], action_id: str
    ) -> RunSnapshot:
        action = _action(state, action_id)
        request = ToolExecutionRequest.from_mapping(action["request"])
        status = action.get("status")
        if status == "PENDING_APPROVAL":
            pending = state.get("pending")
            if pending is None:
                state["pending"] = {
                    "kind": "ACTION_APPROVAL",
                    "step_id": action.get("step_id"),
                    "action_id": action_id,
                }
                return self._commit(
                    snapshot,
                    state,
                    "WAITING_APPROVAL",
                    "ACTION_APPROVAL_REQUIRED",
                    {"step_id": action.get("step_id"), "action_id": action_id},
                )
            return snapshot
        if status == "READY":
            workflow = WorkflowSpec.from_mapping(snapshot.workflow)
            try:
                self._reserve_tool_dispatch(request)
            except Exception as error:  # noqa: BLE001 - host admission is fail-closed
                state["failure"] = {
                    "code": "ACTION_ADMISSION_ERROR",
                    "action_id": action_id,
                }
                return self._commit(
                    snapshot,
                    state,
                    "BLOCKED",
                    "ACTION_ADMISSION_ERROR",
                    {
                        "action_id": action_id,
                        "error_code": _exception_code("ACTION_ADMISSION", error),
                    },
                )
            entered_executor = False
            try:
                try:
                    authorization = self._policy.authorize(
                        request,
                        run_id=snapshot.run_id,
                        workflow=workflow,
                        step_id=str(action.get("step_id", "")),
                    )
                # Policy is an integration boundary. A failed recheck is fail-closed.
                except Exception as error:  # noqa: BLE001
                    state["failure"] = {
                        "code": "ACTION_AUTHORIZATION_ERROR",
                        "action_id": action_id,
                    }
                    return self._commit(
                        snapshot,
                        state,
                        "BLOCKED",
                        "ACTION_AUTHORIZATION_ERROR",
                        {
                            "action_id": action_id,
                            "error_code": _exception_code(
                                "ACTION_AUTHORIZATION", error
                            ),
                        },
                    )
                if (
                    not isinstance(authorization, ActionAuthorization)
                    or not authorization.allowed
                ):
                    reason = (
                        authorization.reason
                        if isinstance(authorization, ActionAuthorization)
                        else "INVALID_POLICY_RESULT"
                    )
                    state["failure"] = {
                        "code": "ACTION_DENIED",
                        "action_id": action_id,
                    }
                    return self._commit(
                        snapshot,
                        state,
                        "BLOCKED",
                        "ACTION_DENIED",
                        {"action_id": action_id, "reason_code": reason},
                    )
                action["status"] = "DISPATCHED"
                dispatched = self._commit(
                    snapshot,
                    state,
                    "RUNNING",
                    "ACTION_DISPATCHED",
                    {"step_id": action.get("step_id"), "action_id": action_id},
                )
                entered_executor = True
                try:
                    result = self._executor.execute(request)
                except ToolExecutionRejectedError as error:
                    state = _state(dispatched)
                    _action(state, action_id)["status"] = "REJECTED"
                    state["failure"] = {
                        "code": error.code,
                        "action_id": action_id,
                        "error_code": error.code,
                    }
                    return self._commit(
                        dispatched,
                        state,
                        "BLOCKED",
                        "TOOL_REJECTED",
                        {"action_id": action_id, "error_code": error.code},
                    )
                except ToolExecutionFailedError as error:
                    return self._record_action_result(
                        dispatched,
                        ToolExecutionResult(action_id, False, error=error.code),
                    )
                except ToolExecutionStoppedError:
                    return self._record_action_result(
                        dispatched,
                        ToolExecutionResult(
                            action_id, False, error="TOOL_EXECUTION_STOPPED"
                        ),
                    )
                # A transport failure after dispatch has an unknown external result.
                # Preserve the action identity and reconcile rather than retrying it.
                except Exception as error:  # noqa: BLE001
                    state = _state(dispatched)
                    _action(state, action_id)["status"] = "RECONCILING"
                    return self._commit(
                        dispatched,
                        state,
                        "RECONCILING",
                        "ACTION_OUTCOME_UNKNOWN",
                        {
                            "action_id": action_id,
                            "error_code": _exception_code("TOOL", error),
                        },
                    )
                return self._record_action_result(dispatched, result)
            finally:
                if not entered_executor:
                    self._release_tool_dispatch(request)
        if status in {"DISPATCHED", "RECONCILING"}:
            try:
                result = self._executor.reconcile(request)
            except ToolExecutionRejectedError:
                state["failure"] = {
                    "code": "TOOL_RECONCILIATION_REJECTED",
                    "action_id": action_id,
                }
                return self._commit(
                    snapshot,
                    state,
                    "BLOCKED",
                    "TOOL_RECONCILIATION_REJECTED",
                    {
                        "action_id": action_id,
                        "error_code": "TOOL_RECONCILIATION_REJECTED",
                    },
                )
            if result is not None:
                return self._record_action_result(snapshot, result)
            if status == "RECONCILING":
                return snapshot
            action["status"] = "RECONCILING"
            return self._commit(
                snapshot,
                state,
                "RECONCILING",
                "ACTION_OUTCOME_UNKNOWN",
                {"action_id": action_id},
            )
        if status == "RESOLVED":
            return snapshot
        raise WorkflowRunError("unknown action status")

    def _record_action_result(
        self, snapshot: RunSnapshot, result: ToolExecutionResult
    ) -> RunSnapshot:
        state = _state(snapshot)
        action = _action(state, result.action_id)
        request = ToolExecutionRequest.from_mapping(action["request"])
        if request.action_id != result.action_id:
            raise WorkflowRunError("executor returned a result for another action")
        action["status"] = "RESOLVED"
        action["result"] = result.to_mapping()
        committed = self._commit(
            snapshot,
            state,
            "RUNNING",
            "ACTION_RESOLVED",
            {"action_id": result.action_id, "succeeded": result.succeeded},
        )
        self._acknowledge_durable_result(request)
        return committed

    def _acknowledge_durable_result(self, request: ToolExecutionRequest) -> None:
        """Tell an opt-in executor that a completed action is durable.

        ``ToolExecutor`` deliberately stays a minimal two-method protocol.
        Executors that keep an in-process completion cache may implement this
        optional hook to free only entries protected by the journal.  It is
        called after, never before, the successful journal replacement.
        """

        acknowledge = getattr(self._executor, "acknowledge_durable_result", None)
        if callable(acknowledge):
            acknowledge(request)

    def _reserve_tool_dispatch(self, request: ToolExecutionRequest) -> None:
        """Enter an optional host admission point before authorization.

        The core remains framework-neutral: only a host wrapper that exposes
        this hook obtains a token.  A normal executor has no additional queue.
        """

        reserve = getattr(self._executor, "reserve_for_dispatch", None)
        if callable(reserve):
            reserve(request)

    def _release_tool_dispatch(self, request: ToolExecutionRequest) -> None:
        release = getattr(self._executor, "release_dispatch_reservation", None)
        if callable(release):
            release(request)

    async def _reserve_tool_dispatch_async(self, request: ToolExecutionRequest) -> None:
        reserve = getattr(self._executor, "reserve_for_dispatch_async", None)
        if callable(reserve):
            value = reserve(request)
            if not hasattr(value, "__await__"):
                raise WorkflowRunError("async tool dispatch reservation is invalid")
            await value

    async def _release_tool_dispatch_async(self, request: ToolExecutionRequest) -> None:
        release = getattr(self._executor, "release_dispatch_reservation_async", None)
        if callable(release):
            value = release(request)
            if not hasattr(value, "__await__"):
                raise WorkflowRunError("async tool dispatch release is invalid")
            await value

    def _complete_step(
        self,
        snapshot: RunSnapshot,
        state: dict[str, Any],
        key: str,
        *,
        event_type: str = "STEP_COMPLETED",
        payload: Mapping[str, JsonValue] | None = None,
    ) -> RunSnapshot:
        completed = set(_strings(state.get("completed")))
        completed.add(key)
        state["completed"] = sorted(completed)
        return self._commit(
            snapshot, state, "RUNNING", event_type, payload or {"step_id": key}
        )

    def _commit(
        self,
        snapshot: RunSnapshot,
        state: Mapping[str, JsonValue],
        status: str,
        event_type: str,
        payload: Mapping[str, JsonValue],
    ) -> RunSnapshot:
        next_snapshot = snapshot.next_revision(state=state, status=status)
        return self._journal.replace(
            next_snapshot,
            expected_revision=snapshot.revision,
            event_type=event_type,
            payload=payload,
        )

    def _validate_drivers(
        self,
        workflow: WorkflowSpec,
        visiting: set[tuple[str, str]] | None = None,
        *,
        workflows: Mapping[tuple[str, str], WorkflowSpec] | None = None,
    ) -> None:
        catalog = self._workflows if workflows is None else workflows
        visiting = set() if visiting is None else visiting
        identity = (workflow.workflow_id, workflow.version)
        if identity in visiting:
            raise WorkflowValidationError(
                "subworkflow references must not form a cycle"
            )
        visiting.add(identity)
        for step in _walk(workflow.steps):
            if (
                step.kind is StepKind.AGENT
                and str(step.config["driver"]) not in self._drivers
            ):
                raise WorkflowValidationError(
                    f"agent driver {step.config['driver']} is not registered"
                )
        for step in _walk(workflow.steps):
            if step.kind is StepKind.SUBWORKFLOW:
                reference = (
                    str(step.config["workflow_id"]),
                    str(step.config["version"]),
                )
                child = catalog.get(reference)
                if child is None:
                    raise WorkflowValidationError("subworkflow is not registered")
                self._validate_drivers(child, visiting, workflows=catalog)
        visiting.remove(identity)

    def _driver_bindings(self, workflow: WorkflowSpec) -> dict[str, str]:
        bindings: dict[str, str] = {}
        visiting: set[tuple[str, str]] = set()

        def collect(current: WorkflowSpec) -> None:
            identity = (current.workflow_id, current.version)
            if identity in visiting:
                return
            visiting.add(identity)
            for step in _walk(current.steps):
                if step.kind is StepKind.AGENT:
                    name = str(step.config["driver"])
                    driver = self._drivers[name]
                    version = getattr(driver, "driver_version", type(driver).__name__)
                    if not isinstance(version, str) or not version:
                        raise WorkflowValidationError(
                            "agent driver version must be explicit"
                        )
                    bindings[name] = version
                elif step.kind is StepKind.SUBWORKFLOW:
                    child = self._workflows.get(
                        (
                            str(step.config["workflow_id"]),
                            str(step.config["version"]),
                        )
                    )
                    if child is not None:
                        collect(child)
            visiting.remove(identity)

        collect(workflow)
        return bindings

    def _driver_execution_binding(self, name: str) -> dict[str, JsonValue]:
        driver = self._drivers[name]
        version = getattr(driver, "driver_version", type(driver).__name__)
        if not isinstance(version, str) or not version:
            raise WorkflowValidationError("agent driver version must be explicit")
        provider = getattr(driver, "execution_binding", None)
        if callable(provider):
            raw = provider()
            if not isinstance(raw, Mapping):
                raise WorkflowValidationError(
                    "agent driver execution binding must be an object"
                )
            binding = json_copy(dict(raw))
        else:
            binding = {
                "driver_version": version,
                "implementation": (
                    f"{type(driver).__module__}.{type(driver).__qualname__}"
                ),
            }
        if binding.get("driver_version") != version or contains_secret_name(binding):
            raise WorkflowValidationError("agent driver execution binding is invalid")
        return binding

    def _tool_execution_binding(self, reference: ToolReference) -> dict[str, JsonValue]:
        """Describe one exact execution target without serializing runtime state."""

        provider = getattr(self._executor, "execution_binding", None)
        if callable(provider):
            raw = provider(reference)
            if not isinstance(raw, Mapping):
                raise WorkflowValidationError(
                    "tool execution binding must be an object"
                )
            binding = json_copy(dict(raw))
        else:
            # Compatibility boundary for application executors that predate
            # target descriptors.  The immutable package digest remains the
            # host-declared implementation version.  Custom adapters needing
            # a finer identity expose execution_binding(tool), rather than
            # relying on unstable Python object or closure serialization.
            binding = {
                "adapter": "custom-python-v1",
                "implementation_version": reference.package_digest,
            }
        if not isinstance(binding, dict) or contains_secret_name(binding):
            raise WorkflowValidationError(
                "tool execution binding must be credential-free JSON"
            )
        return binding

    def _execution_manifest_version(self) -> str:
        """Select an immutable protocol without changing legacy runs.

        Only a compiled domain project carries ``domain_lock``.  Existing
        projects therefore keep emitting and resuming v2 manifests, while a
        new domain run has a visibly distinct protocol/version boundary.
        """

        return (
            _EXECUTION_MANIFEST_V3
            if "domain_lock" in self._runtime_binding
            else _EXECUTION_MANIFEST_V2
        )

    def _execution_manifest(self, workflow: WorkflowSpec) -> dict[str, JsonValue]:
        """Freeze every definition and runtime binding needed to resume a run."""

        catalog: dict[tuple[str, str], WorkflowSpec] = {}
        drivers: set[str] = set()
        tools: dict[tuple[str, str, str], ToolReference] = {}
        visiting: set[tuple[str, str]] = set()

        def collect(current: WorkflowSpec, *, root: bool = False) -> None:
            identity = (current.workflow_id, current.version)
            if identity in visiting:
                raise WorkflowValidationError(
                    "subworkflow references must not form a cycle"
                )
            if not root and identity in catalog:
                return
            visiting.add(identity)
            if not root:
                catalog[identity] = current
            for step in _walk(current.steps):
                if step.kind is StepKind.TOOL:
                    reference = ToolReference.from_mapping(step.config["tool"])
                    tools[
                        (
                            reference.tool_id,
                            reference.version,
                            reference.package_digest,
                        )
                    ] = reference
                elif step.kind is StepKind.AGENT:
                    drivers.add(str(step.config["driver"]))
                    for raw in step.config.get("tools", []):
                        if isinstance(raw, Mapping) and "tool" in raw:
                            reference = ToolReference.from_mapping(raw["tool"])
                        else:
                            reference = ToolReference.from_mapping(raw)
                        tools[
                            (
                                reference.tool_id,
                                reference.version,
                                reference.package_digest,
                            )
                        ] = reference
                elif step.kind is StepKind.SUBWORKFLOW:
                    child_identity = (
                        str(step.config["workflow_id"]),
                        str(step.config["version"]),
                    )
                    child = self._workflows.get(child_identity)
                    if child is None:
                        raise WorkflowValidationError("subworkflow is not registered")
                    collect(child)
            visiting.remove(identity)

        collect(workflow, root=True)
        policy_version = getattr(
            self._policy, "policy_version", type(self._policy).__name__
        )
        if not isinstance(policy_version, str) or not policy_version:
            raise WorkflowValidationError("action policy version must be explicit")
        frozen_tools = [reference for _, reference in sorted(tools.items())]
        return {
            "schema_version": self._execution_manifest_version(),
            "runtime_protocol": _RUNTIME_PROTOCOL_VERSION,
            "root": {
                "workflow_id": workflow.workflow_id,
                "version": workflow.version,
                "definition_digest": workflow.definition_digest,
            },
            "subworkflows": [
                {
                    "workflow": child.to_mapping(),
                    "definition_digest": child.definition_digest,
                }
                for _, child in sorted(catalog.items())
            ],
            "tools": [reference.to_mapping() for reference in frozen_tools],
            "tool_bindings": [
                {
                    "tool": reference.to_mapping(),
                    "binding": self._tool_execution_binding(reference),
                }
                for reference in frozen_tools
            ],
            "drivers": {
                name: self._driver_execution_binding(name) for name in sorted(drivers)
            },
            "policy": {
                "implementation": (
                    f"{type(self._policy).__module__}.{type(self._policy).__qualname__}"
                ),
                "policy_version": policy_version,
            },
            "environment": {
                "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            },
            "runtime_binding": dict(self._runtime_binding),
        }

    def _manifest_workflows(
        self, snapshot: RunSnapshot
    ) -> dict[tuple[str, str], WorkflowSpec]:
        manifest = snapshot.execution_manifest
        if manifest.get("schema_version") not in _SUPPORTED_EXECUTION_MANIFESTS:
            raise RunConflictError("run has no compatible frozen execution manifest")
        raw_children = manifest.get("subworkflows")
        if not isinstance(raw_children, list):
            raise RunConflictError("run execution manifest is malformed")
        result: dict[tuple[str, str], WorkflowSpec] = {}
        for raw in raw_children:
            if not isinstance(raw, Mapping) or set(raw) != {
                "workflow",
                "definition_digest",
            }:
                raise RunConflictError("run execution manifest is malformed")
            child = WorkflowSpec.from_mapping(raw["workflow"])
            if raw["definition_digest"] != child.definition_digest:
                raise RunConflictError("frozen subworkflow digest does not match")
            identity = (child.workflow_id, child.version)
            if identity in result:
                raise RunConflictError("frozen subworkflow is duplicated")
            result[identity] = child
        return result

    def _verify_execution_manifest(self, snapshot: RunSnapshot) -> None:
        manifest = snapshot.execution_manifest
        if (
            manifest.get("schema_version") != self._execution_manifest_version()
            or manifest.get("runtime_protocol") != _RUNTIME_PROTOCOL_VERSION
        ):
            raise RunConflictError("run has no compatible frozen execution manifest")
        workflow = WorkflowSpec.from_mapping(snapshot.workflow)
        if manifest.get("root") != {
            "workflow_id": workflow.workflow_id,
            "version": workflow.version,
            "definition_digest": workflow.definition_digest,
        }:
            raise RunConflictError("frozen root workflow does not match the snapshot")
        environment = manifest.get("environment")
        if not isinstance(environment, Mapping) or environment.get("python") != (
            f"{sys.version_info.major}.{sys.version_info.minor}"
        ):
            raise RunConflictError("run is bound to another Python runtime")
        if manifest.get("runtime_binding", {}) != self._runtime_binding:
            raise RunConflictError("run is bound to a different host runtime assembly")
        self._manifest_workflows(snapshot)
        raw_tools = manifest.get("tools")
        raw_tool_bindings = manifest.get("tool_bindings")
        if not isinstance(raw_tools, list) or not isinstance(raw_tool_bindings, list):
            raise RunConflictError("run execution manifest has no tool bindings")
        try:
            references = [ToolReference.from_mapping(item) for item in raw_tools]
            actual_tool_bindings = [
                {
                    "tool": reference.to_mapping(),
                    "binding": self._tool_execution_binding(reference),
                }
                for reference in references
            ]
        except (ToolExecutionRejectedError, WorkflowValidationError) as error:
            raise RunConflictError(
                "run is bound to an unavailable tool execution target"
            ) from error
        if raw_tool_bindings != actual_tool_bindings:
            raise RunConflictError("run is bound to a different tool execution target")
        expected_drivers = manifest.get("drivers")
        if not isinstance(expected_drivers, Mapping):
            raise RunConflictError("run execution manifest has no driver bindings")
        for name, expected in expected_drivers.items():
            if not isinstance(name, str) or name not in self._drivers:
                raise RunConflictError("run is bound to an unavailable agent driver")
            if self._driver_execution_binding(name) != expected:
                raise RunConflictError(
                    "run is bound to a different driver version, model, prompt, "
                    "or driver configuration"
                )
        expected_policy = manifest.get("policy")
        actual_policy = {
            "implementation": (
                f"{type(self._policy).__module__}.{type(self._policy).__qualname__}"
            ),
            "policy_version": getattr(
                self._policy, "policy_version", type(self._policy).__name__
            ),
        }
        if expected_policy != actual_policy:
            raise RunConflictError("run is bound to a different action policy")

    def _verify_driver_bindings(self, snapshot: RunSnapshot) -> None:
        self._verify_execution_manifest(snapshot)
        for name, expected in snapshot.driver_bindings.items():
            driver = self._drivers.get(name)
            actual = (
                getattr(driver, "driver_version", None) if driver is not None else None
            )
            if actual != expected:
                raise RunConflictError(
                    "run is bound to an unavailable or different driver version"
                )


def _walk(steps: Sequence[StepSpec]):
    for step in steps:
        yield step
        for branch in step.children:
            yield from _walk(branch)


def _state(snapshot: RunSnapshot) -> dict[str, Any]:
    state = json.loads(canonical_json(snapshot.state))
    required = {
        "values",
        "completed",
        "branches",
        "actions",
        "agents",
        "approved_steps",
        "pending",
        "failure",
    }
    if not isinstance(state, dict) or not required <= set(state):
        raise WorkflowRunError("run state is incompatible with this kernel")
    return state


def _strings(value: object) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise WorkflowRunError("run state contains invalid identifiers")
    return value


def _action(state: Mapping[str, Any], action_id: str) -> dict[str, Any]:
    actions = state.get("actions")
    if (
        not isinstance(actions, Mapping)
        or action_id not in actions
        or not isinstance(actions[action_id], Mapping)
    ):
        raise WorkflowRunError("run action is missing")
    return actions[action_id]  # type: ignore[return-value]


def _v2_driver_continuation(
    agent_state: Mapping[str, Any],
) -> Mapping[str, JsonValue] | None:
    raw = agent_state.get("driver_continuation")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise WorkflowRunError("Agent driver continuation is malformed")
    copied = json_copy(raw)
    if not isinstance(copied, dict):
        raise WorkflowRunError("Agent driver continuation is malformed")
    return copied


def _v2_deferred_tool_results(
    state: Mapping[str, Any],
    agent_state: Mapping[str, Any],
    tool_specs: tuple[AgentToolSpec, ...],
) -> tuple[Mapping[str, JsonValue], ...]:
    if agent_state.get("driver_continuation") is None:
        return ()
    batches = agent_state.get("resolved_action_batches", [])
    if not isinstance(batches, list) or not batches:
        raise WorkflowRunError("Agent continuation has no resolved tool history")
    latest = batches[-1]
    if not isinstance(latest, list) or any(
        not isinstance(action_id, str) for action_id in latest
    ):
        raise WorkflowRunError("Agent continuation action references are malformed")
    by_identity = {
        (spec.tool.tool_id, spec.tool.version, spec.tool.package_digest): spec
        for spec in tool_specs
    }
    records: list[Mapping[str, JsonValue]] = []
    call_ids: set[str] = set()
    for action_id in latest:
        action = _action(state, action_id)
        if action.get("status") != "RESOLVED":
            raise WorkflowRunError("Agent continuation action is not resolved")
        call_id = action.get("model_call_id")
        if not isinstance(call_id, str) or not call_id or call_id in call_ids:
            raise WorkflowRunError("Agent continuation call identity is malformed")
        request = ToolExecutionRequest.from_mapping(action["request"])
        result = ToolExecutionResult.from_mapping(action["result"])
        identity = (
            request.tool.tool_id,
            request.tool.version,
            request.tool.package_digest,
        )
        contract = by_identity.get(identity)
        if contract is None:
            raise WorkflowRunError("Agent continuation tool is outside frozen scope")
        records.append(
            {
                "action_id": action_id,
                "tool_call_id": call_id,
                "name": contract.name,
                "arguments": dict(request.arguments),
                "succeeded": result.succeeded,
                "output": dict(result.output) if result.output is not None else None,
                "error": result.error,
            }
        )
        call_ids.add(call_id)
    return tuple(records)


def _action_id(execution_id: str, step_key: str, ordinal: int) -> str:
    # Keep the ordinal prefix so canonical JSON's lexical key ordering remains
    # execution order (useful to humans and old read-only consumers), while a
    # digest prevents nested paths and the immutable execution id leaking into
    # downstream idempotency keys.
    raw = f"{execution_id}:{step_key}:{ordinal}".encode()
    return f"action-{ordinal:08x}-" + hashlib.sha256(raw).hexdigest()[:40]


def _exception_code(prefix: str, error: Exception) -> str:
    """Return a bounded, credential-free diagnostic code for a local failure."""

    name = "".join(
        character if character.isalnum() else "_"
        for character in type(error).__name__.upper()
    )
    return (prefix + "_" + name)[:128]


def _pointer(value: object, pointer: str) -> JsonValue | object:
    current = value
    for token in pointer.lstrip("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping) and token in current:
            current = current[token]
        elif (
            isinstance(current, list) and token.isdigit() and int(token) < len(current)
        ):
            current = current[int(token)]
        else:
            return _MISSING
    return current


_MISSING = object()


def _resolve_templates(
    value: object, values: Mapping[str, JsonValue]
) -> Mapping[str, JsonValue]:
    """Resolve explicit ``{'$ref': '/pointer'}`` values without expression eval."""

    def resolve(item: object) -> Any:
        if isinstance(item, Mapping):
            if set(item) == {"$ref"}:
                pointer = item["$ref"]
                if not isinstance(pointer, str) or not pointer.startswith("/"):
                    raise WorkflowValidationError("$ref must be a JSON pointer")
                result = _pointer(values, pointer)
                if result is _MISSING:
                    raise WorkflowRunError("referenced workflow value is unavailable")
                return result
            return {str(key): resolve(child) for key, child in item.items()}
        if isinstance(item, list):
            return [resolve(child) for child in item]
        return item

    resolved = resolve(value)
    if not isinstance(resolved, Mapping):
        raise WorkflowValidationError("tool arguments must resolve to an object")
    if contains_secret_name(resolved):
        raise WorkflowValidationError("tool arguments must not contain credentials")
    return json_copy(resolved)
