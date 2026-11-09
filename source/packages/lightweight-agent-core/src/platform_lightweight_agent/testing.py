"""Small dependency-free helpers for third-party tool contract tests.

The helpers deliberately use normal ``assert``/``AssertionError`` semantics
instead of importing pytest.  Tool packages can parameterize their own tests
with pytest (or another runner) while exercising the exact action-id replay
contract used by the V6 engine.
"""

from __future__ import annotations

import inspect
from copy import deepcopy
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .runtime_contracts import (
    CancellationToken,
    ToolExecutionFailedError,
    ToolExecutionRejectedError,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutionStoppedError,
    ToolExecutionUncertainError,
)
from .spec import (
    ToolReference,
    WorkflowValidationError,
    canonical_json,
    contains_secret_name,
    json_copy,
)


@dataclass(frozen=True)
class ToolContractCase:
    """One named action replay case suitable for test parametrization."""

    name: str
    request: ToolExecutionRequest

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise WorkflowValidationError("tool contract case name is invalid")


def action_replay_cases(request: ToolExecutionRequest) -> tuple[ToolContractCase, ...]:
    """Return the first-call and same-action replay cases for a tool request.

    The same stable action identity is intentionally reused.  A tool author
    can feed these values to ``pytest.mark.parametrize`` and assert the
    expected result, while :func:`assert_sync_action_replay` or
    :func:`assert_async_action_replay` verifies the stronger no-repeat-effect
    invariant in one call.
    """

    if not isinstance(request, ToolExecutionRequest):
        raise WorkflowValidationError("tool contract request is invalid")
    return (
        ToolContractCase("first_dispatch", request),
        ToolContractCase("same_action_replay", request),
    )


def assert_sync_action_replay(
    executor: object,
    request: ToolExecutionRequest,
    *,
    effect_observer: Callable[[], object],
) -> ToolExecutionResult:
    """Prove stable identity, no replay effect, and conflict rejection.

    ``effect_observer`` should return an equality-comparable snapshot of the
    external effect (for example a row count, ledger contents, or request log).
    The helper copies each observation so mutable probes cannot hide a replay.
    """

    method = getattr(executor, "execute", None)
    if not callable(method):
        raise AssertionError("tool contract executor has no synchronous execute method")
    _observe(effect_observer)
    first = _result(method(request), request)
    after_first = _observe(effect_observer)
    replay = _result(method(request), request)
    _same_result(first, replay)
    if _observe(effect_observer) != after_first:
        raise AssertionError("same action replay repeated an external effect")
    conflict = _conflicting_request(request)
    try:
        method(conflict)
    except ToolExecutionRejectedError:
        pass
    else:
        raise AssertionError("same action_id with conflicting arguments was accepted")
    if _observe(effect_observer) != after_first:
        raise AssertionError("conflicting action request changed the external effect")
    return first


async def assert_async_action_replay(
    executor: object,
    request: ToolExecutionRequest,
    *,
    cancellation: CancellationToken | None = None,
    effect_observer: Callable[[], object],
) -> ToolExecutionResult:
    """Async equivalent of :func:`assert_sync_action_replay`."""

    token = cancellation or CancellationToken()
    _observe(effect_observer)
    first = await _execute_async(executor, request, token)
    _require_action_identity(first, request)
    after_first = _observe(effect_observer)
    replay = await _execute_async(executor, request, token)
    _require_action_identity(replay, request)
    _same_result(first, replay)
    if _observe(effect_observer) != after_first:
        raise AssertionError("same action replay repeated an external effect")
    conflict = _conflicting_request(request)
    try:
        await _execute_async(executor, conflict, token)
    except ToolExecutionRejectedError:
        pass
    else:
        raise AssertionError("same action_id with conflicting arguments was accepted")
    if _observe(effect_observer) != after_first:
        raise AssertionError("conflicting action request changed the external effect")
    return first


async def _execute_async(
    executor: object, request: ToolExecutionRequest, token: CancellationToken
) -> ToolExecutionResult:
    native = getattr(executor, "native_execute_async", None)
    if callable(native):
        value = native(request, token)
        if value is not None:
            if not inspect.isawaitable(value):
                raise AssertionError("native async tool execution did not return awaitable")
            return _result(await value)
    method = getattr(executor, "execute_async", None)
    if callable(method):
        value = method(request, token)
        if not inspect.isawaitable(value):
            raise AssertionError("async tool execution did not return awaitable")
        return _result(await value)
    try:
        import anyio
    except ImportError as error:
        raise RuntimeError(
            "Install platform-lightweight-agent-core[workflow] for async tool contract tests"
        ) from error
    sync = getattr(executor, "execute", None)
    if not callable(sync):
        raise AssertionError("tool contract executor cannot execute requests")
    return _result(await anyio.to_thread.run_sync(sync, request))


def _result(
    value: object, request: ToolExecutionRequest | None = None
) -> ToolExecutionResult:
    if not isinstance(value, ToolExecutionResult):
        raise AssertionError("tool executor returned an invalid contract result")
    # Reconstruct through the public wire shape so a custom executor cannot
    # hand a mutable mapping to a test and accidentally hide an invalid value.
    result = ToolExecutionResult.from_mapping(value.to_mapping())
    if request is not None:
        _require_action_identity(result, request)
    return result


def _require_action_identity(
    result: ToolExecutionResult, request: ToolExecutionRequest
) -> None:
    if result.action_id != request.action_id:
        raise AssertionError("tool result action_id does not match the request")


def _observe(observer: Callable[[], object]) -> object:
    if not callable(observer):
        raise AssertionError("effect_observer must be callable")
    return deepcopy(observer())


def _conflicting_request(request: ToolExecutionRequest) -> ToolExecutionRequest:
    arguments = dict(request.arguments)
    key = "_v6_contract_conflict"
    while key in arguments:
        key = f"_{key}"
    arguments[key] = True
    return ToolExecutionRequest(request.action_id, request.tool, arguments)


def _same_result(first: ToolExecutionResult, replay: ToolExecutionResult) -> None:
    if first.action_id != replay.action_id:
        raise AssertionError("same action replay returned another action_id")
    if first.to_mapping() != replay.to_mapping():
        raise AssertionError("same action replay returned another result")


def assert_sync_outcome_classification(
    operation: Callable[[], object],
    expected: str,
    *,
    error_code: str | None = None,
) -> ToolExecutionResult | None:
    """Assert one adapter preserves a known failure, stop, or unknown signal.

    The caller configures its real client/runner fixture for the scenario and
    passes the normal executor call as ``operation``.  This keeps the helper
    independent of HTTP, MCP, container, or SDK test doubles while ensuring
    all integrations use the same V6 outcome vocabulary.
    """

    if expected not in {"failed", "stopped", "uncertain"}:
        raise AssertionError("expected outcome must be failed, stopped, or uncertain")
    try:
        value = operation()
    except ToolExecutionFailedError as error:
        if expected != "failed":
            raise AssertionError(f"expected {expected}, got known failure") from error
        if error_code is not None and error.code != error_code:
            raise AssertionError("known failure returned another error code") from error
        return None
    except ToolExecutionStoppedError as error:
        if expected != "stopped":
            raise AssertionError(f"expected {expected}, got stopped") from error
        return None
    except ToolExecutionUncertainError as error:
        if expected != "uncertain":
            raise AssertionError(f"expected {expected}, got uncertain") from error
        return None
    if expected != "failed":
        raise AssertionError(f"expected {expected}, got a deterministic result")
    result = _result(value)
    if result.succeeded:
        raise AssertionError("known failure was reported as successful")
    if error_code is not None and result.error != error_code:
        raise AssertionError("known failure returned another error code")
    return result


def assert_sync_reconcile_query(
    executor: object,
    request: ToolExecutionRequest,
    *,
    effect_observer: Callable[[], object],
) -> ToolExecutionResult:
    """Prove lookup and same-action reuse do not recreate the external action."""

    method = getattr(executor, "reconcile", None)
    if not callable(method):
        raise AssertionError("tool contract executor has no reconcile method")
    before = _observe(effect_observer)
    value = method(request)
    if value is None:
        raise AssertionError("reconciliation could not query the original result")
    result = _result(value, request)
    if _observe(effect_observer) != before:
        raise AssertionError("reconciliation query recreated an external effect")
    execute = getattr(executor, "execute", None)
    if not callable(execute):
        raise AssertionError("tool contract executor has no execute method")
    replay = _result(execute(request), request)
    _same_result(result, replay)
    if _observe(effect_observer) != before:
        raise AssertionError("recovered action was recreated after reconciliation")
    return result


def assert_execution_bindings_distinct(
    first_executor: object,
    second_executor: object,
    tool: ToolReference,
) -> tuple[dict[str, object], dict[str, object]]:
    """Prove two execution targets cannot silently share one frozen binding."""

    bindings: list[dict[str, object]] = []
    for executor in (first_executor, second_executor):
        provider = getattr(executor, "execution_binding", None)
        if not callable(provider):
            raise AssertionError("tool executor has no execution_binding method")
        raw = provider(tool)
        if not isinstance(raw, Mapping):
            raise AssertionError("tool execution binding is not an object")
        binding = json_copy(dict(raw))
        if contains_secret_name(binding):
            raise AssertionError("tool execution binding contains credential fields")
        bindings.append(binding)
    if bindings[0] == bindings[1]:
        raise AssertionError("different execution targets have the same binding")
    return bindings[0], bindings[1]


def assert_credential_free_contract(
    *values: object,
    forbidden_values: tuple[str, ...] = (),
) -> None:
    """Assert portable bindings/results/checkpoints contain no credential data."""

    for value in values:
        if isinstance(value, Mapping) and contains_secret_name(value):
            raise AssertionError("portable contract contains credential fields")
        try:
            serialized = canonical_json(value)
        except WorkflowValidationError as error:
            raise AssertionError("portable contract is not strict JSON") from error
        if any(secret and secret in serialized for secret in forbidden_values):
            raise AssertionError("portable contract contains a forbidden credential value")
