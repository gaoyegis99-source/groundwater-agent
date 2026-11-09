"""Host-owned runtime assembly and bounded in-process execution.

The workflow definition remains declarative.  A host provides the concrete
drivers, exact tool routes, authorization policy, optional credential resolver,
and resources it owns.  ``HostRuntime`` uses one AnyIO blocking portal for its
lifetime so synchronous callers, async callers, CLI wrappers, and a local Web
surface share the same process-wide admission and model/tool capacity limits.

No SDK, credential, task, portal, or limiter object is persisted in a run.
"""

from __future__ import annotations

import inspect
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from concurrent.futures import CancelledError as FutureCancelledError
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from functools import partial
from threading import Event as ThreadEvent
from threading import RLock
from typing import Any, Protocol, Self
from uuid import uuid4

from .engine import (
    ProcessResourceDiagnostics,
    WorkflowEngine,
    process_resource_diagnostics,
    _event_target,
    _journal_events,
    _journal_get,
    _journal_list_runs,
    _journal_status,
    _run_target,
)
from .journal import (
    EventCursor,
    RunConflictError,
    RunControl,
    RunHandle,
    RunEvent,
    RunJournal,
    RunListCursor,
    RunPage,
    RunQueryFilter,
    RunNotFoundError,
    RunSnapshot,
)
from .runtime_contracts import (
    AgentContext,
    CancellationToken,
    ToolExecutionRejectedError,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutionUncertainError,
    WorkflowRunError,
)
from .runtime_ports import ActionPolicy, AgentDriver, ToolExecutor
from .spec import (
    ASYNC_SCHEMA_VERSIONS,
    ApprovalDecision,
    JsonValue,
    ToolReference,
    WorkflowSpec,
    WorkflowValidationError,
    contains_secret_name,
    json_copy,
)


_BACKGROUND_SUBMISSION_PREFIX = "background-recovery-"
_RECOVERY_BLOCK_RETRY_SECONDS = 30.0


class HostRuntimeError(WorkflowRunError):
    """A stable host-runtime boundary error."""


@dataclass(frozen=True)
class HostCleanupFailure:
    """One credential-free close hook failure."""

    index: int
    resource_type: str
    code: str = "HOST_RESOURCE_CLEANUP_FAILED"

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "index": self.index,
            "resource_type": self.resource_type,
            "code": self.code,
        }


@dataclass(frozen=True)
class HostRuntimeCloseReport:
    """Inspectible separation of execution stop and resource cleanup."""

    execution_stopped: bool
    resources_complete: bool
    closed: bool
    unfinished_resources: tuple[int, ...] = ()
    failures: tuple[HostCleanupFailure, ...] = ()

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "execution_stopped": self.execution_stopped,
            "resources_complete": self.resources_complete,
            "closed": self.closed,
            "unfinished_resources": list(self.unfinished_resources),
            "failures": [item.to_mapping() for item in self.failures],
        }


@dataclass(frozen=True)
class HostRuntimeDiagnostics:
    """Small process/host lifecycle diagnostic for long-running embeddings."""

    state: str
    process_resources: ProcessResourceDiagnostics
    incomplete_cleanups: int
    cleanup_failures: tuple[HostCleanupFailure, ...] = ()

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "state": self.state,
            "process_resources": self.process_resources.to_mapping(),
            "incomplete_cleanups": self.incomplete_cleanups,
            "cleanup_failures": [item.to_mapping() for item in self.cleanup_failures],
        }


class HostRuntimeOverloadedError(HostRuntimeError):
    """The host queue is full; no run has been started or mutated."""


class HostRuntimeClosedError(HostRuntimeError):
    """The caller tried to use a host runtime that is closing or closed."""


class HostRuntimeCloseTimeoutError(HostRuntimeError):
    """Close requested a safe stop but active runs have not settled yet."""

    def __init__(self, code: str, report: HostRuntimeCloseReport) -> None:
        super().__init__(code)
        self.code = code
        self.report = report


class HostRuntimeCleanupError(HostRuntimeError):
    """Execution stopped, but one or more owned resources failed to close."""

    def __init__(self, report: HostRuntimeCloseReport) -> None:
        super().__init__("HOST_RUNTIME_CLEANUP_FAILED")
        self.code = "HOST_RUNTIME_CLEANUP_FAILED"
        self.report = report


@dataclass(frozen=True)
class RecoveryBlock:
    """A process-local reason a background recovery attempt is deferred.

    A bad runtime binding must not be written into the durable workflow state:
    an operator may install the missing release or restore the matching driver
    and retry the same run.  This compact diagnostic instead prevents one
    host from immediately retrying the same unchanged row while still making
    the reason inspectable to an embedding.
    """

    run_id: str
    revision: int
    code: str
    retry_at: float

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "run_id": self.run_id,
            "revision": self.revision,
            "code": self.code,
            "retry_at": self.retry_at,
        }


class CredentialResolver(Protocol):
    """Host-only credential lookup used at a routed tool boundary.

    Returned values are passed only to an opt-in credential-aware executor and
    never copied into a ``ToolExecutionRequest``, snapshot, event, or result.
    """

    def resolve(self, reference: str) -> object: ...


def _identity(reference: ToolReference) -> tuple[str, str, str]:
    return reference.tool_id, reference.version, reference.package_digest


def _credential_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 256
        or any(character.isspace() for character in value)
    ):
        raise WorkflowValidationError("credential reference is invalid")
    return value


@dataclass(frozen=True)
class ToolRoute:
    """One host-authorized executor for one immutable tool identity."""

    reference: ToolReference
    executor: ToolExecutor
    credential_refs: tuple[str, ...] = ()
    binding: Mapping[str, JsonValue] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.reference, ToolReference):
            raise WorkflowValidationError("tool route requires a ToolReference")
        if not (
            callable(getattr(self.executor, "execute", None))
            or callable(getattr(self.executor, "execute_async", None))
        ):
            raise WorkflowValidationError("tool route executor cannot execute requests")
        refs = tuple(_credential_name(item) for item in self.credential_refs)
        if len(set(refs)) != len(refs):
            raise WorkflowValidationError(
                "tool route credential references must be unique"
            )
        object.__setattr__(self, "credential_refs", refs)
        if self.binding is not None:
            binding = json_copy(dict(self.binding))
            if not isinstance(binding, dict) or contains_secret_name(binding):
                raise WorkflowValidationError(
                    "tool route binding must be credential-free JSON"
                )
            object.__setattr__(self, "binding", binding)


@dataclass(frozen=True)
class RuntimeAssembly:
    """Explicit host-owned components used to construct a workflow engine.

    Supply either a catch-all ``executor`` or one or more exact ``tool_routes``.
    Routes are intentionally *not* discovered from Python entry points: entry
    point discovery is a readiness diagnostic, whereas this object is the
    explicit authorization to execute a particular reference.
    """

    journal: RunJournal
    executor: ToolExecutor | None = None
    drivers: Mapping[str, AgentDriver] = field(default_factory=dict)
    workflows: tuple[WorkflowSpec, ...] = ()
    policy: ActionPolicy | None = None
    runtime_binding: Mapping[str, JsonValue] = field(default_factory=dict)
    tool_routes: tuple[ToolRoute, ...] = ()
    credential_resolver: CredentialResolver | Callable[[str], object] | None = None
    closeables: tuple[object, ...] = ()

    def __post_init__(self) -> None:
        if self.executor is None and not self.tool_routes:
            raise WorkflowValidationError(
                "runtime assembly requires an executor or exact tool routes"
            )
        if self.executor is not None and self.tool_routes:
            raise WorkflowValidationError(
                "runtime assembly cannot combine a default executor with routes"
            )
        routes = tuple(self.tool_routes)
        if any(not isinstance(route, ToolRoute) for route in routes):
            raise WorkflowValidationError("runtime assembly tool routes are invalid")
        if len({_identity(route.reference) for route in routes}) != len(routes):
            raise WorkflowValidationError("runtime assembly tool routes are duplicated")
        if (
            any(route.credential_refs for route in routes)
            and self.credential_resolver is None
        ):
            raise WorkflowValidationError(
                "credential-aware tool routes require a host credential resolver"
            )
        drivers = dict(self.drivers)
        if any(
            not isinstance(name, str)
            or not name
            or not isinstance(getattr(driver, "driver_version", None), str)
            or not driver.driver_version
            or not (
                callable(getattr(driver, "decide", None))
                or callable(getattr(driver, "decide_async", None))
            )
            for name, driver in drivers.items()
        ):
            raise WorkflowValidationError("runtime assembly drivers are invalid")
        workflows = tuple(self.workflows)
        if any(not isinstance(item, WorkflowSpec) for item in workflows):
            raise WorkflowValidationError("runtime assembly workflows are invalid")
        copied_binding = json_copy(dict(self.runtime_binding))
        if not isinstance(copied_binding, dict) or contains_secret_name(copied_binding):
            raise WorkflowValidationError(
                "runtime assembly binding must be credential-free JSON"
            )
        object.__setattr__(self, "tool_routes", routes)
        object.__setattr__(self, "drivers", drivers)
        object.__setattr__(self, "workflows", workflows)
        object.__setattr__(self, "runtime_binding", copied_binding)
        object.__setattr__(self, "closeables", tuple(self.closeables))

    def build_engine(
        self,
        *,
        executor: ToolExecutor | None = None,
        drivers: Mapping[str, AgentDriver] | None = None,
    ) -> WorkflowEngine:
        """Create an engine without taking ownership of supplied resources."""

        selected_executor = executor or self._executor()
        return WorkflowEngine(
            self.journal,
            selected_executor,
            self.drivers if drivers is None else drivers,
            workflows=self.workflows,
            policy=self.policy,
            runtime_binding=self.runtime_binding,
        )

    def _executor(self) -> ToolExecutor:
        if self.executor is not None:
            return self.executor
        return _RoutedToolExecutor(
            self.tool_routes,
            credential_resolver=self.credential_resolver,
        )


def assemble_runtime(assembly: RuntimeAssembly) -> WorkflowEngine:
    """Public common assembly point for CLI, Python, and local Web hosts."""

    if not isinstance(assembly, RuntimeAssembly):
        raise WorkflowValidationError("assemble_runtime requires RuntimeAssembly")
    return assembly.build_engine()


class _RoutedToolExecutor:
    """Exact-reference dispatcher; discovery and authorization never mix."""

    def __init__(
        self,
        routes: Sequence[ToolRoute],
        *,
        credential_resolver: CredentialResolver | Callable[[str], object] | None,
    ) -> None:
        self._routes = {_identity(route.reference): route for route in routes}
        self._credential_resolver = credential_resolver

    def _route(self, request: ToolExecutionRequest) -> ToolRoute:
        route = self._routes.get(_identity(request.tool))
        if route is None:
            raise ToolExecutionRejectedError("TOOL_ROUTE_NOT_REGISTERED")
        return route

    def execution_binding(self, tool: ToolReference) -> dict[str, JsonValue]:
        route = self._routes.get(_identity(tool))
        if route is None:
            raise ToolExecutionRejectedError("TOOL_ROUTE_NOT_REGISTERED")
        if route.binding is not None:
            return json_copy(dict(route.binding))
        provider = getattr(route.executor, "execution_binding", None)
        if callable(provider):
            raw = provider(tool)
            if not isinstance(raw, Mapping):
                raise WorkflowValidationError(
                    "routed tool execution binding must be an object"
                )
            binding = json_copy(dict(raw))
            if not isinstance(binding, dict) or contains_secret_name(binding):
                raise WorkflowValidationError(
                    "routed tool execution binding must be credential-free JSON"
                )
            return binding
        return {
            "adapter": "custom-python-v1",
            "implementation_version": tool.package_digest,
        }

    def _credentials(self, route: ToolRoute) -> Mapping[str, object]:
        if not route.credential_refs:
            return {}
        resolver = self._credential_resolver
        if resolver is None:  # safeguarded by RuntimeAssembly, defensive here
            raise ToolExecutionRejectedError("CREDENTIAL_REFERENCE_UNBOUND")
        result: dict[str, object] = {}
        for reference in route.credential_refs:
            try:
                value = (
                    resolver(reference)
                    if callable(resolver)
                    else resolver.resolve(reference)
                )
            except Exception as error:
                raise ToolExecutionRejectedError(
                    "CREDENTIAL_REFERENCE_UNBOUND"
                ) from error
            if value is None:
                raise ToolExecutionRejectedError("CREDENTIAL_REFERENCE_UNBOUND")
            result[reference] = value
        return result

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        route = self._route(request)
        credentials = self._credentials(route)
        if credentials:
            method = getattr(route.executor, "execute_with_credentials", None)
            if not callable(method):
                raise ToolExecutionRejectedError("CREDENTIAL_INJECTION_UNSUPPORTED")
            return method(request, credentials)
        method = getattr(route.executor, "execute", None)
        if not callable(method):
            raise ToolExecutionRejectedError("TOOL_ROUTE_NOT_REGISTERED")
        return method(request)

    def reconcile(self, request: ToolExecutionRequest) -> ToolExecutionResult | None:
        route = self._route(request)
        credentials = self._credentials(route)
        if credentials:
            method = getattr(route.executor, "reconcile_with_credentials", None)
            if not callable(method):
                raise ToolExecutionRejectedError("CREDENTIAL_INJECTION_UNSUPPORTED")
            return method(request, credentials)
        method = getattr(route.executor, "reconcile", None)
        if not callable(method):
            raise ToolExecutionRejectedError("TOOL_ROUTE_NOT_REGISTERED")
        return method(request)

    def acknowledge_durable_result(self, request: ToolExecutionRequest) -> None:
        """Forward the journal-durability acknowledgement to the exact route."""

        method = getattr(
            self._route(request).executor, "acknowledge_durable_result", None
        )
        if callable(method):
            method(request)

    def native_execute_async(
        self, request: ToolExecutionRequest, cancellation: CancellationToken
    ) -> object | None:
        """Return a route's native async operation, never a thread fallback.

        The host limiter uses this split to retain a capacity token for a
        synchronous route's actual worker thread after a caller abandons its
        wait.  ``execute_async`` retains the generic fallback for non-host
        assemblies.
        """

        route = self._route(request)
        credentials = self._credentials(route)
        if not credentials:
            native = getattr(route.executor, "native_execute_async", None)
            if callable(native):
                value = native(request, cancellation)
                if value is None:
                    return None
                if not inspect.isawaitable(value):
                    raise ToolExecutionUncertainError(
                        "native async routed executor returned without awaiting"
                    )
                return value
        method_name = (
            "execute_with_credentials_async" if credentials else "execute_async"
        )
        method = getattr(route.executor, method_name, None)
        if not callable(method):
            return None
        value = (
            method(request, credentials, cancellation)
            if credentials
            else method(request, cancellation)
        )
        if not inspect.isawaitable(value):
            raise ToolExecutionUncertainError(
                "async routed executor returned without awaiting"
            )
        return value

    async def execute_async(
        self, request: ToolExecutionRequest, cancellation: CancellationToken
    ) -> ToolExecutionResult:
        value = self.native_execute_async(request, cancellation)
        if value is not None:
            return await value
        anyio = _anyio()
        return await anyio.to_thread.run_sync(
            self.execute, request, abandon_on_cancel=True
        )

    async def reconcile_async(
        self, request: ToolExecutionRequest
    ) -> ToolExecutionResult | None:
        route = self._route(request)
        credentials = self._credentials(route)
        method_name = (
            "reconcile_with_credentials_async" if credentials else "reconcile_async"
        )
        method = getattr(route.executor, method_name, None)
        if callable(method):
            value = method(request, credentials) if credentials else method(request)
            if not inspect.isawaitable(value):
                raise ToolExecutionUncertainError(
                    "async routed reconciliation returned without awaiting"
                )
            return await value
        anyio = _anyio()
        return await anyio.to_thread.run_sync(
            self.reconcile, request, abandon_on_cancel=True
        )


@dataclass(frozen=True)
class HostRuntimeLimits:
    """Process-local admission and external-call bounds for one host runtime."""

    max_active_runs: int = 4
    max_queued_runs: int = 16
    max_tool_calls: int = 16
    max_model_requests: int = 4
    max_control_operations: int = 16
    recovery_scan_interval_seconds: float = 1.0
    shutdown_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        for name in (
            "max_active_runs",
            "max_queued_runs",
            "max_tool_calls",
            "max_model_requests",
            "max_control_operations",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise WorkflowValidationError(f"{name} must be a positive integer")
        if (
            not isinstance(self.recovery_scan_interval_seconds, (int, float))
            or isinstance(self.recovery_scan_interval_seconds, bool)
            or not 0.01 <= self.recovery_scan_interval_seconds <= 300.0
        ):
            raise WorkflowValidationError(
                "recovery_scan_interval_seconds must be between 0.01 and 300"
            )
        if (
            not isinstance(self.shutdown_timeout_seconds, (int, float))
            or isinstance(self.shutdown_timeout_seconds, bool)
            or self.shutdown_timeout_seconds <= 0
        ):
            raise WorkflowValidationError("shutdown_timeout_seconds must be positive")


@dataclass
class _HostLoopState:
    engine: WorkflowEngine
    executor: _LimitedExecutor
    admission: Any
    active_runs: Any
    tool_calls: Any
    model_requests: Any
    control_operations: Any
    background_send: Any
    background_receive: Any


@dataclass(frozen=True)
class _Submission:
    submission_id: str
    operation: str
    run_id: str
    future: Future[Any]


@dataclass
class _ToolLease:
    """One host token reserved before an Action is authorized."""

    request: ToolExecutionRequest
    borrower: object
    state: str = "reserved"


class _LimitedExecutor:
    """Bound host tool execution without mistaking abandonment for completion.

    The workflow reserves a token before it rechecks authorization and records
    ``DISPATCHED``.  When a synchronous handler runs in an AnyIO worker, the
    worker—not the cancelled caller—releases that token in its ``finally``.
    """

    def __init__(self, executor: ToolExecutor, limiter: Any) -> None:
        self._executor = executor
        self._limiter = limiter
        self._lock = RLock()
        self._leases: dict[str, _ToolLease] = {}
        self._active_sync_calls = 0
        self._sync_idle = ThreadEvent()
        self._sync_idle.set()

    def execution_binding(self, tool: ToolReference) -> dict[str, JsonValue]:
        provider = getattr(self._executor, "execution_binding", None)
        if callable(provider):
            raw = provider(tool)
            if not isinstance(raw, Mapping):
                raise WorkflowValidationError(
                    "limited tool execution binding must be an object"
                )
            return json_copy(dict(raw))
        return {
            "adapter": "custom-python-v1",
            "implementation_version": tool.package_digest,
        }

    async def _acquire(self, borrower: object) -> None:
        await self._limiter.acquire_on_behalf_of(borrower)

    async def _release(self, borrower: object) -> None:
        self._limiter.release_on_behalf_of(borrower)

    def _existing_lease(self, request: ToolExecutionRequest) -> _ToolLease | None:
        with self._lock:
            lease = self._leases.get(request.action_id)
            if lease is not None and lease.request.to_mapping() != request.to_mapping():
                raise ToolExecutionRejectedError(
                    "action id was reused with another request"
                )
            return lease

    async def reserve_for_dispatch_async(self, request: ToolExecutionRequest) -> None:
        """Acquire host capacity before final authorization and dispatch."""

        if self._existing_lease(request) is not None:
            return
        lease = _ToolLease(request, object())
        await self._acquire(lease.borrower)
        duplicate = False
        conflict = False
        with self._lock:
            existing = self._leases.get(request.action_id)
            if existing is None:
                self._leases[request.action_id] = lease
            else:
                duplicate = True
                conflict = existing.request.to_mapping() != request.to_mapping()
        if duplicate:
            await self._release(lease.borrower)
            if conflict:
                raise ToolExecutionRejectedError(
                    "action id was reused with another request"
                )

    def reserve_for_dispatch(self, request: ToolExecutionRequest) -> None:
        """Synchronous adapter used by legacy engine paths in an AnyIO worker."""

        if self._existing_lease(request) is not None:
            return
        try:
            from anyio import from_thread
        except ImportError as error:
            raise RuntimeError(
                "Install platform-lightweight-agent-core[workflow] for host runtime"
            ) from error
        lease = _ToolLease(request, object())
        try:
            from_thread.run(self._acquire, lease.borrower)
        except RuntimeError as error:
            raise HostRuntimeError(
                "host-managed tools must run through HostRuntime"
            ) from error
        duplicate = False
        conflict = False
        with self._lock:
            existing = self._leases.get(request.action_id)
            if existing is None:
                self._leases[request.action_id] = lease
            else:
                duplicate = True
                conflict = existing.request.to_mapping() != request.to_mapping()
        if duplicate:
            from_thread.run(self._release, lease.borrower)
            if conflict:
                raise ToolExecutionRejectedError(
                    "action id was reused with another request"
                )

    def _mark_started(self, lease: _ToolLease, state: str) -> bool:
        with self._lock:
            current = self._leases.get(lease.request.action_id)
            if current is not lease:
                return False
            if state == "sync" and lease.state == "sync_queued":
                # ``to_thread.run_sync(..., abandon_on_cancel=True)`` may
                # leave its worker queued after the caller stops waiting. The
                # queued state already owns the physical-task count; starting
                # it must not acquire a second count or capacity token.
                lease.state = state
                return True
            if lease.state != "reserved":
                return False
            lease.state = state
            if state == "sync":
                self._active_sync_calls += 1
                self._sync_idle.clear()
            return True

    def _mark_sync_queued(self, lease: _ToolLease) -> bool:
        """Count a worker submission before its thread has actually begun.

        AnyIO may abandon the caller's wait while a synchronous work item is
        still queued for a worker.  From the host's safety perspective that
        work item already owns its tool token and may still touch a closeable,
        so shutdown must wait for its eventual ``finally`` just as it would
        for a running thread.
        """

        with self._lock:
            current = self._leases.get(lease.request.action_id)
            if current is not lease or lease.state != "reserved":
                return False
            lease.state = "sync_queued"
            self._active_sync_calls += 1
            self._sync_idle.clear()
            return True

    async def _release_if_reserved_async(self, request: ToolExecutionRequest) -> None:
        lease: _ToolLease | None
        with self._lock:
            lease = self._leases.get(request.action_id)
            if lease is None or lease.state != "reserved":
                return
            lease.state = "released"
            self._leases.pop(request.action_id, None)
        await self._release(lease.borrower)

    def release_dispatch_reservation(self, request: ToolExecutionRequest) -> None:
        try:
            from anyio import from_thread
        except ImportError as error:
            raise RuntimeError(
                "Install platform-lightweight-agent-core[workflow] for host runtime"
            ) from error
        try:
            from_thread.run(self._release_if_reserved_async, request)
        except RuntimeError as error:
            raise HostRuntimeError(
                "host-managed tools must run through HostRuntime"
            ) from error

    async def release_dispatch_reservation_async(
        self, request: ToolExecutionRequest
    ) -> None:
        await self._release_if_reserved_async(request)

    def _finish_sync_on_loop(self, lease: _ToolLease) -> None:
        release = False
        with self._lock:
            was_sync = lease.state == "sync"
            if self._leases.get(lease.request.action_id) is lease:
                self._leases.pop(lease.request.action_id, None)
                if lease.state != "released":
                    lease.state = "released"
                    release = True
            if was_sync:
                self._active_sync_calls = max(0, self._active_sync_calls - 1)
                if self._active_sync_calls == 0:
                    self._sync_idle.set()
        if release:
            self._limiter.release_on_behalf_of(lease.borrower)

    async def _finish_async(self, lease: _ToolLease) -> None:
        release = False
        with self._lock:
            if self._leases.get(lease.request.action_id) is lease:
                self._leases.pop(lease.request.action_id, None)
                if lease.state != "released":
                    lease.state = "released"
                    release = True
        if release:
            await self._release(lease.borrower)

    def _run_sync_with_lease(
        self,
        request: ToolExecutionRequest,
        lease: _ToolLease,
        cancellation: CancellationToken | None = None,
    ) -> ToolExecutionResult:
        if not self._mark_started(lease, "sync"):
            raise HostRuntimeError("HOST_TOOL_RESERVATION_LOST")
        try:
            cancellable = getattr(self._executor, "execute_with_cancellation", None)
            result = (
                cancellable(request, cancellation)
                if cancellation is not None and callable(cancellable)
                else self._executor.execute(request)
            )
            if not isinstance(result, ToolExecutionResult):
                raise ToolExecutionUncertainError(
                    "tool executor returned an invalid result"
                )
            return result
        finally:
            try:
                from anyio import from_thread
            except ImportError as error:
                raise RuntimeError(
                    "Install platform-lightweight-agent-core[workflow] for host runtime"
                ) from error
            from_thread.run_sync(self._finish_sync_on_loop, lease)

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        self.reserve_for_dispatch(request)
        lease = self._existing_lease(request)
        if lease is None:
            raise HostRuntimeError("HOST_TOOL_RESERVATION_LOST")
        return self._run_sync_with_lease(request, lease)

    def reconcile(self, request: ToolExecutionRequest) -> ToolExecutionResult | None:
        # A reconciliation query is not a new side effect.  Keep legacy sync
        # queries bounded, while dispatch reservations remain Action-specific.
        try:
            from anyio import from_thread
        except ImportError as error:
            raise RuntimeError(
                "Install platform-lightweight-agent-core[workflow] for host runtime"
            ) from error
        borrower = object()
        try:
            from_thread.run(self._acquire, borrower)
            return self._executor.reconcile(request)
        finally:
            from_thread.run(self._release, borrower)

    def acknowledge_durable_result(self, request: ToolExecutionRequest) -> None:
        """Forward non-I/O cache acknowledgement without consuming a token."""

        method = getattr(self._executor, "acknowledge_durable_result", None)
        if callable(method):
            method(request)

    async def execute_async(
        self, request: ToolExecutionRequest, cancellation: CancellationToken
    ) -> ToolExecutionResult:
        await self.reserve_for_dispatch_async(request)
        lease = self._existing_lease(request)
        if lease is None:
            raise HostRuntimeError("HOST_TOOL_RESERVATION_LOST")
        try:
            native = getattr(self._executor, "native_execute_async", None)
            method = getattr(self._executor, "execute_async", None)
            result = (
                native(request, cancellation)
                if callable(native)
                else method(request, cancellation)
                if callable(method)
                else None
            )
            if result is not None:
                if not inspect.isawaitable(result):
                    raise ToolExecutionUncertainError(
                        "async tool executor returned without awaiting"
                    )
                if not self._mark_started(lease, "async"):
                    raise HostRuntimeError("HOST_TOOL_RESERVATION_LOST")
                try:
                    return await result
                finally:
                    anyio = _anyio()
                    with anyio.CancelScope(shield=True):
                        await self._finish_async(lease)
            anyio = _anyio()
            if not self._mark_sync_queued(lease):
                raise HostRuntimeError("HOST_TOOL_RESERVATION_LOST")
            return await anyio.to_thread.run_sync(
                self._run_sync_with_lease,
                request,
                lease,
                cancellation,
                abandon_on_cancel=True,
            )
        finally:
            # A started call owns release in its completion path.  This only
            # releases a reservation cancelled before its worker began.
            anyio = _anyio()
            with anyio.CancelScope(shield=True):
                await self._release_if_reserved_async(request)

    async def reconcile_async(
        self, request: ToolExecutionRequest
    ) -> ToolExecutionResult | None:
        borrower = object()
        await self._acquire(borrower)
        try:
            method = getattr(self._executor, "reconcile_async", None)
            if callable(method):
                result = method(request)
                if not inspect.isawaitable(result):
                    raise ToolExecutionUncertainError(
                        "async reconciliation returned without awaiting"
                    )
                return await result
            anyio = _anyio()
            return await anyio.to_thread.run_sync(
                self._executor.reconcile, request, abandon_on_cancel=True
            )
        finally:
            await self._release(borrower)

    def wait_for_physical_tasks(self, timeout_seconds: float) -> bool:
        """Wait for abandoned synchronous tool workers before shutdown."""

        return self._sync_idle.wait(timeout_seconds)


class _LimitedDriver:
    def __init__(self, driver: AgentDriver, limiter: Any) -> None:
        self._driver = driver
        self._limiter = limiter

    @property
    def driver_version(self) -> str:
        return self._driver.driver_version

    def __getattr__(self, name: str) -> object:
        return getattr(self._driver, name)

    async def _acquire(self, borrower: object) -> None:
        await self._limiter.acquire_on_behalf_of(borrower)

    async def _release(self, borrower: object) -> None:
        self._limiter.release_on_behalf_of(borrower)

    def decide(self, context: AgentContext):  # type: ignore[no-untyped-def]
        try:
            from anyio import from_thread
        except ImportError as error:
            raise RuntimeError(
                "Install platform-lightweight-agent-core[workflow] for host runtime"
            ) from error
        borrower = object()
        try:
            from_thread.run(self._acquire, borrower)
        except RuntimeError as error:
            raise HostRuntimeError(
                "host-managed drivers must run through HostRuntime"
            ) from error
        try:
            method = getattr(self._driver, "decide", None)
            if not callable(method):
                raise HostRuntimeError("v1/v2 host driver has no decide method")
            return method(context)
        finally:
            from_thread.run(self._release, borrower)

    async def decide_async(self, context: AgentContext):  # type: ignore[no-untyped-def]
        method = getattr(self._driver, "decide_async", None)
        if not callable(method):
            raise HostRuntimeError("v3/v4 host driver has no decide_async method")
        borrower = object()
        await self._acquire(borrower)
        try:
            decision = method(context)
            if not inspect.isawaitable(decision):
                raise HostRuntimeError("async host driver returned without awaiting")
            return await decision
        finally:
            anyio = _anyio()
            with anyio.CancelScope(shield=True):
                await self._release(borrower)


def _anyio():  # type: ignore[no-untyped-def]
    try:
        import anyio
    except ImportError as error:
        raise RuntimeError(
            "Install platform-lightweight-agent-core[workflow] for host runtime"
        ) from error
    return anyio


class HostRuntime:
    """One explicitly owned, bounded, process-local workflow runtime.

    ``start()`` creates one AnyIO ``BlockingPortal`` and all run/model/tool
    admission happens on that portal's event loop.  This gives synchronous and
    asynchronous embedding callers the same process-wide bounds without a
    network scheduler.  Only resources listed in ``RuntimeAssembly.closeables``
    are closed by this object.
    """

    def __init__(
        self, assembly: RuntimeAssembly, *, limits: HostRuntimeLimits | None = None
    ) -> None:
        if not isinstance(assembly, RuntimeAssembly):
            raise WorkflowValidationError("HostRuntime requires RuntimeAssembly")
        self._assembly = assembly
        self._limits = limits or HostRuntimeLimits()
        self._lock = RLock()
        self._portal_context: Any | None = None
        self._portal: Any | None = None
        self._state: _HostLoopState | None = None
        self._submissions: dict[Future[Any], _Submission] = {}
        # This small per-run counter is deliberately set *before* a portal
        # task can create a snapshot.  The recovery supervisor must never
        # race a foreground ``run``/``resume`` that this same host already
        # accepted and thereby claim its freshly persisted execution lease.
        self._foreground_submissions_by_run: dict[str, int] = {}
        self._cancelled_submissions: set[str] = set()
        # Background execution is deliberately represented by durable journal
        # state plus one coalesced wakeup, never one AnyIO task or in-memory
        # run-id queue per control.  A bounded worker set only consumes the
        # compact journal projection; it does not own continuation state.
        # A resolved reconciliation returns to RUNNING, so the journal remains
        # the only durable continuation backlog.
        self._background_wake_pending = False
        self._background_current_runs: set[str] = set()
        self._background_idle = ThreadEvent()
        self._background_idle.set()
        self._background_supervisor: Future[Any] | None = None
        self._recovery_cursor: tuple[int, str] | None = None
        self._recovery_blocks: dict[str, RecoveryBlock] = {}
        # This registry is ownership bookkeeping for close(), not a work
        # queue.  It holds only nonterminal runs observed by this host.
        self._owned_run_ids: set[str] = set()
        self._close_mode: str | None = None
        self._closing = False
        self._closed = False
        self._execution_stopped = False
        self._cleanup_completed: set[int] = set()
        self._cleanup_failures: dict[int, HostCleanupFailure] = {}
        self._cleanup_future: Future[tuple[HostCleanupFailure, ...]] | None = None

    @property
    def journal(self) -> RunJournal:
        return self._assembly.journal

    @property
    def limits(self) -> HostRuntimeLimits:
        return self._limits

    def recovery_blocks(self) -> tuple[RecoveryBlock, ...]:
        """Return current process-local background recovery deferrals.

        The durable journal remains authoritative for a run.  These entries
        only explain why this particular host is not repeatedly attempting an
        unchanged incompatible or failed recovery row before its retry window.
        A durable revision change clears the deferral immediately.
        """

        with self._lock:
            return tuple(
                sorted(
                    self._recovery_blocks.values(),
                    key=lambda item: (item.retry_at, item.run_id),
                )
            )

    def diagnostics(self) -> HostRuntimeDiagnostics:
        """Return bounded lifecycle and process-resource counters."""

        with self._lock:
            if self._closed:
                state = "closed"
            elif self._closing:
                state = "closing"
            elif self._portal is None:
                state = "new"
            else:
                state = "running"
            incomplete = (
                len(self._assembly.closeables) - len(self._cleanup_completed)
                if self._closing
                else 0
            )
            failures = tuple(
                self._cleanup_failures[index]
                for index in sorted(self._cleanup_failures)
            )
        return HostRuntimeDiagnostics(
            state=state,
            process_resources=process_resource_diagnostics(),
            incomplete_cleanups=incomplete,
            cleanup_failures=failures,
        )

    def start(self) -> HostRuntime:
        """Start the owned portal once; it remains live until ``close()``."""

        with self._lock:
            if self._closed or self._closing:
                raise HostRuntimeClosedError("HOST_RUNTIME_CLOSED")
            if self._portal is not None:
                return self
            try:
                from anyio.from_thread import start_blocking_portal
            except ImportError as error:
                raise RuntimeError(
                    "Install platform-lightweight-agent-core[workflow] for host runtime"
                ) from error
            context = start_blocking_portal()
            portal = context.__enter__()
            self._portal_context = context
            self._portal = portal
        try:
            portal.call(self._initialize)
            with self._lock:
                self._background_idle.clear()
            supervisor = portal.start_task_soon(self._background_supervisor_task)
            with self._lock:
                self._background_supervisor = supervisor
        except BaseException:
            with self._lock:
                self._portal = None
                self._portal_context = None
            context.__exit__(None, None, None)
            raise
        return self

    def _start_cleanup_portal(self) -> None:
        """Open only the event-loop owner needed by asynchronous close hooks.

        Closing a runtime that was never started must not start journal recovery
        merely to obtain an event loop for an explicitly-owned resource.  Apart
        from making ``close()`` unexpectedly execute durable work, that also
        made an empty recovery scan compete with a small cleanup timeout.  The
        cleanup portal deliberately has no engine state or supervisor.
        """

        with self._lock:
            if self._closed or self._closing:
                raise HostRuntimeClosedError("HOST_RUNTIME_CLOSED")
            if self._portal is not None:
                return
            try:
                from anyio.from_thread import start_blocking_portal
            except ImportError as error:
                raise RuntimeError(
                    "Install platform-lightweight-agent-core[workflow] for host runtime"
                ) from error
            context = start_blocking_portal()
            portal = context.__enter__()
            self._portal_context = context
            self._portal = portal

    async def _initialize(self) -> None:
        anyio = _anyio()
        base_executor = self._assembly._executor()
        tool_limiter = anyio.CapacityLimiter(self._limits.max_tool_calls)
        model_limiter = anyio.CapacityLimiter(self._limits.max_model_requests)
        limited_executor = _LimitedExecutor(base_executor, tool_limiter)
        # One coalesced wakeup asks the long-lived supervisor to scan the
        # journal.  The journal, rather than an unbounded collection of tasks
        # or queued run ids, is the backlog of durable continuations.
        background_send, background_receive = anyio.create_memory_object_stream(1)
        drivers = {
            name: _LimitedDriver(driver, model_limiter)
            for name, driver in self._assembly.drivers.items()
        }
        engine = self._assembly.build_engine(
            executor=limited_executor,
            drivers=drivers,
        )
        self._state = _HostLoopState(
            engine=engine,
            executor=limited_executor,
            admission=anyio.Semaphore(
                self._limits.max_active_runs + self._limits.max_queued_runs,
                max_value=(self._limits.max_active_runs + self._limits.max_queued_runs),
            ),
            active_runs=anyio.CapacityLimiter(self._limits.max_active_runs),
            tool_calls=tool_limiter,
            model_requests=model_limiter,
            control_operations=anyio.CapacityLimiter(
                self._limits.max_control_operations
            ),
            background_send=background_send,
            background_receive=background_receive,
        )

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.close(mode="wait")

    def _portal_or_raise(self) -> Any:
        self.start()
        with self._lock:
            if self._portal is None:
                raise HostRuntimeClosedError("HOST_RUNTIME_CLOSED")
            return self._portal

    def _submit(
        self,
        operation: str,
        run_id: str,
        *args: object,
        allow_closing: bool = False,
        expected_execution_id: str | None = None,
    ) -> Future[Any]:
        with self._lock:
            if self._closed or (self._closing and not allow_closing):
                raise HostRuntimeClosedError("HOST_RUNTIME_CLOSED")
        portal = self._portal_or_raise()
        submission_id = uuid4().hex
        with self._lock:
            self._foreground_submissions_by_run[run_id] = (
                self._foreground_submissions_by_run.get(run_id, 0) + 1
            )
        try:
            operation_kwargs: dict[str, str] = {}
            if expected_execution_id is not None:
                operation_kwargs["expected_execution_id"] = expected_execution_id
            future = portal.start_task_soon(
                partial(
                    self._operate,
                    operation,
                    run_id,
                    submission_id,
                    *args,
                    **operation_kwargs,
                )
            )
        except BaseException:
            with self._lock:
                self._foreground_submission_finished(run_id)
            raise
        submission = _Submission(submission_id, operation, run_id, future)
        with self._lock:
            self._submissions[future] = submission
            self._owned_run_ids.add(run_id)
        future.add_done_callback(self._completed)
        return future

    def _completed(self, future: Future[Any]) -> None:
        snapshot: RunSnapshot | None = None
        try:
            result = future.result()
            if isinstance(result, RunSnapshot):
                snapshot = result
        except BaseException:  # noqa: BLE001 - completion is observed below
            snapshot = None
        with self._lock:
            submission = self._submissions.pop(future, None)
            if submission is not None:
                self._cancelled_submissions.discard(submission.submission_id)
                self._foreground_submission_finished(submission.run_id)
            if snapshot is not None and snapshot.status in {
                "SUCCEEDED",
                "BLOCKED",
                "CANCELLED",
            }:
                self._owned_run_ids.discard(snapshot.run_id)

    def _foreground_submission_finished(self, run_id: str) -> None:
        """Drop one foreground-operation marker while holding ``_lock``."""

        remaining = self._foreground_submissions_by_run.get(run_id, 0) - 1
        if remaining > 0:
            self._foreground_submissions_by_run[run_id] = remaining
        else:
            self._foreground_submissions_by_run.pop(run_id, None)

    def _foreground_submission_active(self, run_id: str) -> bool:
        """Whether this host already has a foreground operation for ``run_id``."""

        with self._lock:
            return self._foreground_submissions_by_run.get(run_id, 0) > 0

    def _mark_submission_cancelled(self, future: Future[Any]) -> None:
        """Prevent an admitted-but-not-started operation from creating a run."""

        with self._lock:
            submission = self._submissions.get(future)
            if submission is not None:
                self._cancelled_submissions.add(submission.submission_id)

    def _submission_cancelled(self, submission_id: str) -> bool:
        with self._lock:
            return submission_id in self._cancelled_submissions

    def _closing_requests_cancellation(self) -> bool:
        with self._lock:
            return self._closing and self._close_mode == "request_cancel"

    async def _operate(
        self,
        operation: str,
        run_id: str,
        submission_id: str,
        *args: object,
        expected_execution_id: str | None = None,
    ) -> RunSnapshot:
        state = self._state
        if state is None:
            raise HostRuntimeError("HOST_RUNTIME_NOT_INITIALIZED")
        if self._submission_cancelled(submission_id):
            raise HostRuntimeError("HOST_RUNTIME_OPERATION_CANCELLED")

        # Durable controls must not occupy an execution slot.  In particular,
        # recording an approval or a manual receipt remains available while a
        # sibling branch owns the last active-run slot.  Legacy workflows do
        # not have a split control/drive protocol and retain their old path.
        if operation in {"request_cancel", "cancel"}:
            return await self._operate_control(
                state,
                operation,
                run_id,
                *args,
                expected_execution_id=expected_execution_id,
            )
        if operation in {"resume", "reconcile"}:
            existing = state.engine.get(
                run_id, expected_execution_id=expected_execution_id
            )
            workflow = WorkflowSpec.from_mapping(existing.workflow)
            if workflow.schema_version in ASYNC_SCHEMA_VERSIONS and (
                operation != "resume" or bool(args[1])
            ):
                # Submitting a concrete approval/receipt is control-plane work
                # and may acknowledge before execution capacity exists.  A
                # bare resume is intentionally different: it means "drive the
                # durable run now", including after a host restart.
                return await self._operate_control(
                    state,
                    operation,
                    run_id,
                    *args,
                    expected_execution_id=expected_execution_id,
                )

        if operation == "advance" and self._closing_requests_cancellation():
            # close(request_cancel) has a durable control phase.  Never turn a
            # leftover scheduler wakeup into a new business dispatch after it
            # begins; the close path records cancellation for this host's
            # owned work separately.
            return state.engine.get(
                run_id, expected_execution_id=expected_execution_id
            )

        uses_admission = operation in {"run", "resume", "advance"}
        try:
            if uses_admission:
                state.admission.acquire_nowait()
        except _anyio().WouldBlock as error:
            raise HostRuntimeOverloadedError("HOST_RUNTIME_OVERLOADED") from error
        try:
            async with state.active_runs:
                # The operation may have spent time in the bounded admission
                # queue.  Check again immediately before an engine call so a
                # caller cancellation cannot become a delayed new run.
                if self._submission_cancelled(submission_id):
                    raise HostRuntimeError("HOST_RUNTIME_OPERATION_CANCELLED")
                if operation == "run":
                    return await state.engine.run_async(
                        args[0],
                        args[1],
                        run_id=run_id,  # type: ignore[arg-type]
                    )
                if operation == "resume":
                    return await self._resume_existing(
                        state.engine,
                        run_id,
                        args[0],  # type: ignore[arg-type]
                        args[1],  # type: ignore[arg-type]
                        expected_execution_id=expected_execution_id,
                    )
                if operation == "reconcile":
                    return await state.engine.reconcile_async(
                        run_id,
                        args[0],  # type: ignore[arg-type]
                        args[1],  # type: ignore[arg-type]
                        expected_execution_id=expected_execution_id,
                    )
                if operation == "advance":
                    return await self._advance_existing(
                        state.engine,
                        run_id,
                        expected_execution_id=expected_execution_id,
                    )
                raise HostRuntimeError("HOST_RUNTIME_OPERATION_INVALID")
        finally:
            if uses_admission:
                state.admission.release()
                # The bounded recovery supervisor already continues its
                # current journal sweep.  Waking it again after a rejected
                # background attempt caused an incompatible row to trigger a
                # tight self-scan loop.  Foreground releases still wake the
                # supervisor so they share the same admission capacity.
                if not submission_id.startswith(_BACKGROUND_SUBMISSION_PREFIX):
                    self._wake_background()

    async def _operate_control(
        self,
        state: _HostLoopState,
        operation: str,
        run_id: str,
        *args: object,
        expected_execution_id: str | None = None,
    ) -> RunSnapshot:
        """Persist one v3/v4 control without waiting for execution capacity."""

        try:
            state.control_operations.acquire_nowait()
        except _anyio().WouldBlock as error:
            raise HostRuntimeOverloadedError(
                "HOST_RUNTIME_CONTROL_OVERLOADED"
            ) from error
        try:
            if operation == "request_cancel":
                snapshot = await self._request_cancel_existing(
                    state.engine,
                    run_id,
                    expected_execution_id=expected_execution_id,
                )
            elif operation == "cancel":
                snapshot = await self._cancel_existing(
                    state.engine,
                    run_id,
                    str(args[0]),
                    expected_execution_id=expected_execution_id,
                )
            elif operation == "resume":
                snapshot = await self._resume_existing(
                    state.engine,
                    run_id,
                    args[0],  # type: ignore[arg-type]
                    args[1],  # type: ignore[arg-type]
                    advance=False,
                    expected_execution_id=expected_execution_id,
                )
            elif operation == "reconcile":
                snapshot = await state.engine.reconcile_async(
                    run_id,
                    args[0],  # type: ignore[arg-type]
                    args[1],  # type: ignore[arg-type]
                    advance=False,
                    expected_execution_id=expected_execution_id,
                )
            else:  # defensive: the caller only sends known control operations
                raise HostRuntimeError("HOST_RUNTIME_OPERATION_INVALID")
            # A query-like v3 ``resume()`` with no decision remains a no-op.
            # Every other control either changed a durable fact or is an
            # idempotent replay that can safely prompt a stalled run to consume
            # its already-recorded fact.
            if operation != "resume" or bool(args[1]):
                self._schedule_advance_if_idle(snapshot)
            return snapshot
        finally:
            state.control_operations.release()

    @staticmethod
    def _snapshot_execution_is_active(snapshot: RunSnapshot) -> bool:
        v3 = snapshot.state.get("v3")
        execution = v3.get("execution") if isinstance(v3, Mapping) else None
        return bool(
            isinstance(execution, Mapping)
            and isinstance(execution.get("owner_id"), str)
            and isinstance(execution.get("lease_expires"), (int, float))
            and float(execution["lease_expires"]) > time.time()
        )

    def _schedule_advance_if_idle(self, snapshot: RunSnapshot) -> None:
        """Wake the bounded execution drain for a durable control transition.

        A live persisted owner observes the mutation itself.  Otherwise the
        run is already discoverable from the journal, so this method emits one
        coalesced wakeup instead of creating a task that can wait outside the
        host admission limit.
        """

        if snapshot.status not in {"RUNNING", "CANCEL_REQUESTED"} or (
            self._snapshot_execution_is_active(snapshot)
        ):
            return
        with self._lock:
            if self._closed or (self._closing and self._close_mode == "request_cancel"):
                return
            self._recovery_blocks.pop(snapshot.run_id, None)
            self._owned_run_ids.add(snapshot.run_id)
        self._wake_background()

    def _wake_background(self) -> None:
        """Coalesce a journal-drain wakeup without allocating a worker task."""

        state = self._state
        if state is None:
            return
        with self._lock:
            if self._closed or self._background_wake_pending:
                return
            self._background_wake_pending = True
            self._background_idle.clear()
        try:
            state.background_send.send_nowait(None)
        except _anyio().WouldBlock:
            # Another wakeup is already buffered.  It will observe the same
            # durable state, so retaining the coalesced marker is correct.
            return
        except (_anyio().ClosedResourceError, _anyio().BrokenResourceError):
            with self._lock:
                self._background_wake_pending = False
                if not self._background_current_runs:
                    self._background_idle.set()

    def _background_became_idle(self) -> None:
        with self._lock:
            if (
                not self._background_current_runs
                and not self._background_wake_pending
            ):
                self._background_idle.set()

    def _background_started(self, run_id: str) -> None:
        with self._lock:
            self._background_current_runs.add(run_id)
            self._background_idle.clear()

    def _background_finished(self, run_id: str) -> None:
        with self._lock:
            self._background_current_runs.discard(run_id)

    def _recovery_attempt_allowed(self, control: RunControl) -> bool:
        """Skip an unchanged local recovery failure until its bounded retry."""

        with self._lock:
            blocked = self._recovery_blocks.get(control.run_id)
            if blocked is None:
                return True
            if blocked.revision != control.revision or blocked.retry_at <= time.time():
                self._recovery_blocks.pop(control.run_id, None)
                return True
            return False

    def _defer_recovery(self, control: RunControl, error: BaseException) -> None:
        if isinstance(error, RunConflictError):
            code = "RECOVERY_RUNTIME_BINDING_MISMATCH"
        elif isinstance(error, WorkflowValidationError):
            code = "RECOVERY_WORKFLOW_INVALID"
        elif isinstance(error, WorkflowRunError):
            code = "RECOVERY_EXECUTION_REJECTED"
        else:
            code = "RECOVERY_EXECUTION_FAILED"
        block = RecoveryBlock(
            control.run_id,
            control.revision,
            code,
            time.time() + _RECOVERY_BLOCK_RETRY_SECONDS,
        )
        with self._lock:
            self._recovery_blocks[control.run_id] = block

    @staticmethod
    def _scan_accepts_cursor(scan: Callable[..., object]) -> bool:
        """Preserve compatibility with third-party pre-cursor journals."""

        try:
            parameters = inspect.signature(scan).parameters.values()
        except (TypeError, ValueError):
            return False
        return any(
            parameter.name == "after"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )

    async def _recovery_page(
        self,
        scan: Callable[..., object],
        *,
        limit: int,
        cursor: tuple[int, str] | None,
        supports_cursor: bool,
    ) -> tuple[RunControl, ...]:
        anyio = _anyio()

        def invoke() -> object:
            if supports_cursor:
                return scan(limit=limit, after=cursor)
            return scan(limit=limit)

        raw = await anyio.to_thread.run_sync(invoke)
        if not isinstance(raw, tuple) or not all(
            isinstance(item, RunControl) for item in raw
        ):
            raise HostRuntimeError("HOST_RUNTIME_RECOVERY_SCAN_INVALID")
        return raw

    async def _background_supervisor_task(self) -> None:
        """Periodically drain durable continuation work with fixed workers.

        The timer is deliberately low-frequency and bounded.  It catches a
        former process's lease expiring after this host has started without
        turning journal recovery into a separate scheduler service.
        """

        state = self._state
        if state is None:
            return
        anyio = _anyio()
        first_scan = True
        async with state.background_receive:
            while True:
                if not first_scan:
                    received_wakeup = False
                    try:
                        with anyio.move_on_after(
                            self._limits.recovery_scan_interval_seconds
                        ) as timeout:
                            await state.background_receive.receive()
                            received_wakeup = True
                    except anyio.EndOfStream:
                        return
                    if received_wakeup and not timeout.cancel_called:
                        with self._lock:
                            self._background_wake_pending = False
                first_scan = False
                await self._drain_background_work(state)

    async def _drain_background_work(self, state: _HostLoopState) -> None:
        """Fairly feed a fixed worker set from the durable journal projection.

        Workers call the normal host operation boundary and therefore share
        its admission, run, model, and tool limiters with foreground callers.
        They are fixed in number (at most ``max_active_runs``), so neither a
        long recovery list nor a bad binding creates an unbounded in-memory
        task queue.
        """

        anyio = _anyio()
        scan = getattr(self._assembly.journal, "recoverable_runs", None)
        if not callable(scan):
            self._background_became_idle()
            return
        supports_cursor = self._scan_accepts_cursor(scan)
        checked: set[str] = set()
        page_size = max(1, self._limits.max_active_runs + self._limits.max_queued_runs)
        worker_count = max(1, self._limits.max_active_runs)
        send, receive = anyio.create_memory_object_stream[RunControl](worker_count)
        stop = anyio.Event()

        async def worker(worker_receive: Any) -> None:
            async with worker_receive:
                async for control in worker_receive:
                    if stop.is_set() or self._closing_requests_cancellation():
                        continue
                    await self._advance_background_candidate(state, control, stop)

        cursor = self._recovery_cursor if supports_cursor else None
        try:
            async with anyio.create_task_group() as group:
                for _ in range(worker_count):
                    group.start_soon(worker, receive.clone())
                await receive.aclose()
                async with send:
                    while not stop.is_set() and not self._closing_requests_cancellation():
                        try:
                            controls = await self._recovery_page(
                                scan,
                                limit=page_size,
                                cursor=cursor,
                                supports_cursor=supports_cursor,
                            )
                        except Exception:  # noqa: BLE001 - a later scan may recover
                            break
                        if not controls:
                            # Keep the last cursor through this quiet scan.  A
                            # following timer resets it and starts the next
                            # round-robin sweep without immediately revisiting
                            # an incompatible first page.
                            if cursor is not None:
                                self._recovery_cursor = None
                            break
                        for control in controls:
                            cursor = (control.revision, control.run_id)
                            if supports_cursor:
                                self._recovery_cursor = cursor
                            if (
                                control.run_id in checked
                                or not self._recovery_attempt_allowed(control)
                            ):
                                continue
                            checked.add(control.run_id)
                            await send.send(control)
                            if stop.is_set() or self._closing_requests_cancellation():
                                break
                        if len(controls) < page_size:
                            break
        finally:
            self._background_became_idle()

    async def _advance_background_candidate(
        self, state: _HostLoopState, control: RunControl, stop: Any
    ) -> None:
        """Advance one row, recording only a local deferral on failure."""

        run_id = control.run_id
        if self._foreground_submission_active(run_id):
            return
        if not self._recovery_attempt_allowed(control):
            return
        try:
            snapshot = state.engine.get(
                run_id, expected_execution_id=control.execution_id
            )
            workflow = WorkflowSpec.from_mapping(snapshot.workflow)
        except RunNotFoundError:
            return
        except (RunConflictError, WorkflowRunError, WorkflowValidationError) as error:
            self._defer_recovery(control, error)
            return
        if workflow.schema_version not in ASYNC_SCHEMA_VERSIONS:
            return
        with self._lock:
            if self._closing and self._close_mode == "request_cancel":
                return
            self._owned_run_ids.add(run_id)
        self._background_started(run_id)
        try:
            # Re-check after the slower snapshot/schema work above.  A user
            # may have submitted a direct run/resume while this durable row
            # was waiting for a background worker; foreground ownership wins.
            if self._foreground_submission_active(run_id):
                return
            completed = await self._operate(
                "advance",
                run_id,
                f"{_BACKGROUND_SUBMISSION_PREFIX}{uuid4().hex}",
                expected_execution_id=control.execution_id,
            )
        except HostRuntimeOverloadedError:
            # Do not leave workers waiting outside the host's established
            # admission queue.  A foreground release emits the next wakeup.
            stop.set()
        except RunNotFoundError:
            return
        except (RunConflictError, WorkflowRunError, WorkflowValidationError) as error:
            self._defer_recovery(control, error)
        except Exception as error:  # noqa: BLE001 - record a bounded local retry
            self._defer_recovery(control, error)
        else:
            with self._lock:
                self._recovery_blocks.pop(run_id, None)
                if completed.status in {"SUCCEEDED", "BLOCKED", "CANCELLED"}:
                    self._owned_run_ids.discard(run_id)
        finally:
            self._background_finished(run_id)

    async def _stop_background_supervisor(self) -> None:
        state = self._state
        if state is not None:
            await state.background_send.aclose()

    async def _advance_existing(
        self,
        engine: WorkflowEngine,
        run_id: str,
        *,
        expected_execution_id: str | None = None,
    ) -> RunSnapshot:
        snapshot = engine.get(run_id, expected_execution_id=expected_execution_id)
        workflow = WorkflowSpec.from_mapping(snapshot.workflow)
        if workflow.schema_version not in ASYNC_SCHEMA_VERSIONS:
            raise HostRuntimeError("HOST_RUNTIME_ADVANCE_REQUIRES_ASYNC_WORKFLOW")
        return await engine.resume_async(
            run_id,
            approvals=(),
            advance=True,
            expected_execution_id=expected_execution_id,
        )

    async def _resume_existing(
        self,
        engine: WorkflowEngine,
        run_id: str,
        approved: bool | None,
        approvals: Sequence[ApprovalDecision],
        *,
        advance: bool = True,
        expected_execution_id: str | None = None,
    ) -> RunSnapshot:
        snapshot = engine.get(run_id, expected_execution_id=expected_execution_id)
        workflow = WorkflowSpec.from_mapping(snapshot.workflow)
        if workflow.schema_version in ASYNC_SCHEMA_VERSIONS:
            if approved is not None:
                raise WorkflowRunError(
                    "v3/v4 approvals require ApprovalDecision with an approval_id"
                )
            return await engine.resume_async(
                run_id,
                approvals=approvals,
                advance=advance,
                expected_execution_id=expected_execution_id,
            )
        if approvals:
            raise WorkflowRunError("v1/v2 workflows accept one boolean approval")
        anyio = _anyio()
        return await anyio.to_thread.run_sync(
            lambda: engine.resume(
                run_id,
                approved=approved,
                expected_execution_id=expected_execution_id,
            )
        )

    async def _request_cancel_existing(
        self,
        engine: WorkflowEngine,
        run_id: str,
        *,
        expected_execution_id: str | None = None,
    ) -> RunSnapshot:
        snapshot = engine.get(run_id, expected_execution_id=expected_execution_id)
        workflow = WorkflowSpec.from_mapping(snapshot.workflow)
        if workflow.schema_version in ASYNC_SCHEMA_VERSIONS:
            return await engine.request_cancel_async(
                run_id, expected_execution_id=expected_execution_id
            )
        anyio = _anyio()
        return await anyio.to_thread.run_sync(
            lambda: engine.cancel(
                run_id, expected_execution_id=expected_execution_id
            )
        )

    async def _cancel_existing(
        self,
        engine: WorkflowEngine,
        run_id: str,
        reason: str,
        *,
        expected_execution_id: str | None = None,
    ) -> RunSnapshot:
        snapshot = engine.get(run_id, expected_execution_id=expected_execution_id)
        workflow = WorkflowSpec.from_mapping(snapshot.workflow)
        if workflow.schema_version in ASYNC_SCHEMA_VERSIONS:
            return await engine.request_cancel_async(
                run_id,
                reason=reason,
                expected_execution_id=expected_execution_id,
            )
        anyio = _anyio()
        return await anyio.to_thread.run_sync(
            lambda: engine.cancel(
                run_id,
                reason=reason,
                expected_execution_id=expected_execution_id,
            )
        )

    @staticmethod
    def _wait_sync(future: Future[Any]) -> RunSnapshot:
        return future.result()

    async def _wait_async(self, future: Future[Any], run_id: str) -> RunSnapshot:
        anyio = _anyio()
        cancelled_exception = anyio.get_cancelled_exc_class()
        try:
            return await anyio.to_thread.run_sync(
                self._wait_sync, future, abandon_on_cancel=True
            )
        except cancelled_exception:
            self._mark_submission_cancelled(future)
            # Do not directly kill the portal task.  First persist cancellation
            # intent, allowing the core to classify a dispatched external action
            # as unknown rather than silently abandoning it.
            with anyio.CancelScope(shield=True):
                try:
                    requested = self._submit(
                        "request_cancel", run_id, allow_closing=True
                    )
                    await anyio.to_thread.run_sync(
                        self._wait_sync, requested, abandon_on_cancel=True
                    )
                except (
                    HostRuntimeClosedError,
                    HostRuntimeOverloadedError,
                    RunNotFoundError,
                ):
                    pass
            raise

    def run(
        self,
        workflow: WorkflowSpec,
        inputs: Mapping[str, JsonValue] | None = None,
        *,
        run_id: str | None = None,
    ) -> RunSnapshot:
        effective_run_id = run_id or f"host-{uuid4().hex}"
        return self._wait_sync(
            self._submit("run", effective_run_id, workflow, dict(inputs or {}))
        )

    async def run_async(
        self,
        workflow: WorkflowSpec,
        inputs: Mapping[str, JsonValue] | None = None,
        *,
        run_id: str | None = None,
    ) -> RunSnapshot:
        effective_run_id = run_id or f"host-{uuid4().hex}"
        return await self._wait_async(
            self._submit("run", effective_run_id, workflow, dict(inputs or {})),
            effective_run_id,
        )

    def resume(
        self,
        run_id: str | RunHandle,
        *,
        approved: bool | None = None,
        approvals: Sequence[ApprovalDecision] = (),
        expected_execution_id: str | None = None,
    ) -> RunSnapshot:
        run_id, expected_execution_id = _run_target(run_id, expected_execution_id)
        return self._wait_sync(
            self._submit(
                "resume",
                run_id,
                approved,
                tuple(approvals),
                expected_execution_id=expected_execution_id,
            )
        )

    async def resume_async(
        self,
        run_id: str | RunHandle,
        *,
        approvals: Sequence[ApprovalDecision] = (),
        expected_execution_id: str | None = None,
    ) -> RunSnapshot:
        run_id, expected_execution_id = _run_target(run_id, expected_execution_id)
        return await self._wait_async(
            self._submit(
                "resume",
                run_id,
                None,
                tuple(approvals),
                expected_execution_id=expected_execution_id,
            ),
            run_id,
        )

    def request_cancel(
        self,
        run_id: str | RunHandle,
        *,
        reason: str = "cancelled by caller",
        expected_execution_id: str | None = None,
    ) -> RunSnapshot:
        run_id, expected_execution_id = _run_target(run_id, expected_execution_id)
        return self._wait_sync(
            self._submit(
                "cancel",
                run_id,
                reason,
                expected_execution_id=expected_execution_id,
            )
        )

    async def request_cancel_async(
        self,
        run_id: str | RunHandle,
        *,
        reason: str = "cancelled by caller",
        expected_execution_id: str | None = None,
    ) -> RunSnapshot:
        run_id, expected_execution_id = _run_target(run_id, expected_execution_id)
        return await self._wait_async(
            self._submit(
                "cancel",
                run_id,
                reason,
                expected_execution_id=expected_execution_id,
            ),
            run_id,
        )

    def cancel(
        self,
        run_id: str | RunHandle,
        *,
        reason: str = "cancelled by caller",
        expected_execution_id: str | None = None,
    ) -> RunSnapshot:
        return self.request_cancel(
            run_id,
            reason=reason,
            expected_execution_id=expected_execution_id,
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
        return self._wait_sync(
            self._submit(
                "reconcile",
                run_id,
                action_id,
                result,
                expected_execution_id=expected_execution_id,
            )
        )

    async def reconcile_async(
        self,
        run_id: str | RunHandle,
        action_id: str,
        result: ToolExecutionResult,
        *,
        expected_execution_id: str | None = None,
    ) -> RunSnapshot:
        run_id, expected_execution_id = _run_target(run_id, expected_execution_id)
        return await self._wait_async(
            self._submit(
                "reconcile",
                run_id,
                action_id,
                result,
                expected_execution_id=expected_execution_id,
            ),
            run_id,
        )

    def get(
        self, run_id: str | RunHandle, *, expected_execution_id: str | None = None
    ) -> RunSnapshot:
        run_id, expected_execution_id = _run_target(run_id, expected_execution_id)
        return _journal_get(self._assembly.journal, run_id, expected_execution_id)

    def inspection(
        self, run_id: str | RunHandle, *, expected_execution_id: str | None = None
    ):  # type: ignore[no-untyped-def]
        run_id, expected_execution_id = _run_target(run_id, expected_execution_id)
        inspect = getattr(self._assembly.journal, "inspection", None)
        return (
            (
                inspect(run_id)
                if expected_execution_id is None
                else inspect(run_id, expected_execution_id=expected_execution_id)
            )
            if callable(inspect)
            else _journal_get(
                self._assembly.journal, run_id, expected_execution_id
            )
        )

    def view(
        self, run_id: str | RunHandle, *, expected_execution_id: str | None = None
    ):  # type: ignore[no-untyped-def]
        from .inspection import read_run_view

        run_id, expected_execution_id = _run_target(run_id, expected_execution_id)
        return read_run_view(self._assembly.journal, run_id, expected_execution_id)

    def explain(
        self, run_id: str | RunHandle, *, expected_execution_id: str | None = None
    ):  # type: ignore[no-untyped-def]
        from .inspection import explain_run

        return explain_run(
            self.inspection(run_id, expected_execution_id=expected_execution_id)
        )

    def preview_recovery(
        self, run_id: str | RunHandle, *, expected_execution_id: str | None = None
    ):  # type: ignore[no-untyped-def]
        from .inspection import preview_recovery

        return preview_recovery(
            self.inspection(run_id, expected_execution_id=expected_execution_id)
        )

    def status(
        self, run_id: str | RunHandle, *, expected_execution_id: str | None = None
    ):  # type: ignore[no-untyped-def]
        run_id, expected_execution_id = _run_target(run_id, expected_execution_id)
        return _journal_status(self._assembly.journal, run_id, expected_execution_id)

    def list_runs(
        self,
        filter: RunQueryFilter | None = None,
        cursor: RunListCursor | None = None,
        limit: int = 50,
    ) -> RunPage:
        """Read the optional compact journal projection without starting work."""

        return _journal_list_runs(self._assembly.journal, filter, cursor, limit)

    def events(
        self,
        run_id: str | RunHandle,
        *,
        after: int = 0,
        limit: int = 256,
        expected_execution_id: str | None = None,
        cursor: EventCursor | None = None,
    ):  # type: ignore[no-untyped-def]
        run_id, after, expected_execution_id = _event_target(
            run_id,
            after=after,
            expected_execution_id=expected_execution_id,
            cursor=cursor,
        )
        return _journal_events(
            self._assembly.journal,
            run_id,
            after=after,
            limit=limit,
            expected_execution_id=expected_execution_id,
        )

    async def stream_events(
        self,
        run_id: str | RunHandle,
        *,
        after: int = 0,
        expected_execution_id: str | None = None,
        cursor: EventCursor | None = None,
    ) -> AsyncIterator[RunEvent]:
        """Stream only persisted events; polling works across portal threads."""

        anyio = _anyio()
        run_id, after, expected_execution_id = _event_target(
            run_id,
            after=after,
            expected_execution_id=expected_execution_id,
            cursor=cursor,
        )
        sequence = after
        terminal = {"SUCCEEDED", "BLOCKED", "CANCELLED"}
        while True:
            page = await anyio.to_thread.run_sync(
                lambda sequence=sequence: _journal_events(
                    self._assembly.journal,
                    run_id,
                    after=sequence,
                    limit=256,
                    expected_execution_id=expected_execution_id,
                )
            )
            for event in page:
                sequence = event.sequence
                yield event
            status = await anyio.to_thread.run_sync(
                lambda: _journal_status(
                    self._assembly.journal, run_id, expected_execution_id
                )
            )
            if status.status in terminal and sequence >= status.last_sequence:
                return
            await anyio.sleep(0.05)

    def close(
        self,
        *,
        mode: str = "request_cancel",
        timeout_seconds: float | None = None,
    ) -> HostRuntimeCloseReport:
        """Stop accepting work and close explicitly-owned resources safely.

        ``wait`` drains existing operations.  ``request_cancel`` first records
        cancellation intent for known runs, then waits.  A timeout leaves the
        portal and owned resources intact so the host may inspect or retry; it
        never force-kills a task whose external outcome could be unknown.
        """

        if mode not in {"wait", "request_cancel"}:
            raise WorkflowValidationError(
                "host close mode must be wait or request_cancel"
            )
        timeout = (
            self._limits.shutdown_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or timeout <= 0
        ):
            raise WorkflowValidationError("host close timeout must be positive")
        # A host owns its closeables even when no caller started a run.  Open a
        # cleanup-only portal before entering the closing state so async close
        # hooks retain the same event-loop ownership convention without also
        # starting journal recovery.
        with self._lock:
            needs_start = not self._closed and self._portal is None
        if needs_start:
            self._start_cleanup_portal()
        with self._lock:
            if self._closed:
                return self._close_report()
            self._closing = True
            self._close_mode = mode
            portal = self._portal
            submissions = tuple(self._submissions.values())
            owned_run_ids = tuple(self._owned_run_ids)
            execution_stopped = self._execution_stopped
        if portal is None:  # defensive: start() above either supplied one or raised
            raise HostRuntimeClosedError("HOST_RUNTIME_CLOSED")
        deadline = time.monotonic() + float(timeout)
        if not execution_stopped:
            if mode == "request_cancel":
                # This includes durable continuation work, which has no public
                # Future in ``_submissions``.  The close request is recorded
                # before the fixed background drain can claim another action.
                for run_id in owned_run_ids:
                    try:
                        portal.call(self._request_cancel_if_started, run_id)
                    except RunNotFoundError:
                        # A front submission can have a caller-provided ID before
                        # it creates a durable run.  Prevent its later admission.
                        for submission in submissions:
                            if submission.run_id == run_id:
                                self._mark_submission_cancelled(submission.future)
                    except (RuntimeError, OSError, ValueError):
                        # A failed control request must not make shutdown pretend
                        # the action is stopped.  The wait below is authoritative.
                        pass
                portal.call(self._wake_background)
            for submission in submissions:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise self._close_timeout()
                try:
                    submission.future.exception(timeout=remaining)
                except FutureTimeoutError as error:
                    raise self._close_timeout() from error
                except FutureCancelledError:
                    # A cancelled queued submission never created a durable action.
                    pass
            # A durable control may have queued one internal continuation.
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self._background_idle.wait(remaining):
                raise self._close_timeout()
            # A synchronous worker cannot be killed safely.  Do not close
            # resources while it may still be using them.
            state = self._state
            if state is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not state.executor.wait_for_physical_tasks(
                    remaining
                ):
                    raise self._close_timeout()
            supervisor = self._background_supervisor
            if supervisor is not None:
                portal.call(self._stop_background_supervisor)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise self._close_timeout()
                try:
                    supervisor.exception(timeout=remaining)
                except FutureTimeoutError as error:
                    raise self._close_timeout() from error
            with self._lock:
                self._execution_stopped = True

        self._finish_resource_cleanup(portal, deadline)
        with self._lock:
            context = self._portal_context
        if context is not None:
            context.__exit__(None, None, None)
        with self._lock:
            self._portal = None
            self._portal_context = None
            self._closed = True
        return self._close_report()

    def _close_report(self) -> HostRuntimeCloseReport:
        with self._lock:
            unfinished = tuple(
                index
                for index in range(len(self._assembly.closeables))
                if index not in self._cleanup_completed
            )
            failures = tuple(
                self._cleanup_failures[index]
                for index in sorted(self._cleanup_failures)
            )
            return HostRuntimeCloseReport(
                execution_stopped=self._execution_stopped,
                resources_complete=not unfinished,
                closed=self._closed,
                unfinished_resources=unfinished,
                failures=failures,
            )

    def _close_timeout(self) -> HostRuntimeCloseTimeoutError:
        return HostRuntimeCloseTimeoutError(
            "HOST_RUNTIME_CLOSE_TIMEOUT", self._close_report()
        )

    def _finish_resource_cleanup(self, portal: Any, deadline: float) -> None:
        with self._lock:
            future = self._cleanup_future
            if future is None:
                pending = tuple(
                    index
                    for index in reversed(range(len(self._assembly.closeables)))
                    if index not in self._cleanup_completed
                )
                for index in pending:
                    self._cleanup_failures.pop(index, None)
                if pending:
                    future = portal.start_task_soon(
                        self._close_owned_resources, pending
                    )
                    self._cleanup_future = future
        if future is None:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise self._close_timeout()
        try:
            failures = future.result(timeout=remaining)
        except FutureTimeoutError as error:
            # The hook keeps ownership and continues on the portal.  A timeout
            # reports incomplete cleanup; it never claims the hook/thread stopped.
            raise self._close_timeout() from error
        with self._lock:
            if self._cleanup_future is future:
                self._cleanup_future = None
            self._cleanup_failures = {item.index: item for item in failures}
        if failures:
            raise HostRuntimeCleanupError(self._close_report())

    async def _request_cancel_if_started(self, run_id: str) -> bool:
        state = self._state
        if state is None:
            return False
        await self._request_cancel_existing(state.engine, run_id)
        return True

    async def _close_owned_resources(
        self, pending: tuple[int, ...]
    ) -> tuple[HostCleanupFailure, ...]:
        failures: list[HostCleanupFailure] = []
        anyio = _anyio()
        for index in pending:
            resource = self._assembly.closeables[index]
            close = getattr(resource, "aclose", None)
            if not callable(close):
                close = getattr(resource, "close", None)
            try:
                if callable(close):
                    if inspect.iscoroutinefunction(close):
                        value = close()
                    else:
                        value = await anyio.to_thread.run_sync(
                            close, abandon_on_cancel=False
                        )
                    if inspect.isawaitable(value):
                        await value
                with self._lock:
                    self._cleanup_completed.add(index)
                    self._cleanup_failures.pop(index, None)
            except BaseException as error:  # noqa: BLE001 - aggregate independent hooks
                if isinstance(error, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                    raise
                resource_type = (
                    f"{type(resource).__module__}.{type(resource).__qualname__}"
                )
                failure = HostCleanupFailure(index, resource_type)
                failures.append(failure)
                with self._lock:
                    self._cleanup_failures[index] = failure
        return tuple(failures)
