from __future__ import annotations

import pytest
from platform_lightweight_agent import (
    AsyncFunctionToolExecutor,
    FunctionToolExecutor,
    ToolExecutionFailedError,
    ToolExecutionRequest,
    ToolExecutionRejectedError,
    ToolExecutionResult,
    ToolExecutionStoppedError,
    ToolExecutionUncertainError,
    ToolReference,
    action_replay_cases,
    assert_async_action_replay,
    assert_credential_free_contract,
    assert_execution_bindings_distinct,
    assert_sync_action_replay,
    assert_sync_outcome_classification,
    assert_sync_reconcile_query,
)


REFERENCE = ToolReference("contract.echo", "1", "sha256:" + "e" * 64)


def test_sync_tool_contract_helpers_exercise_same_action_replay() -> None:
    calls = 0

    def handler(arguments):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return {"value": arguments["value"]}

    executor = FunctionToolExecutor()
    executor.register(REFERENCE, handler)
    request = ToolExecutionRequest("action-contract-sync", REFERENCE, {"value": 3})

    result = assert_sync_action_replay(
        executor, request, effect_observer=lambda: calls
    )

    assert result.output == {"value": 3}
    assert calls == 1
    assert [case.name for case in action_replay_cases(request)] == [
        "first_dispatch",
        "same_action_replay",
    ]


@pytest.mark.anyio
async def test_async_tool_contract_helpers_use_native_execution() -> None:
    calls = 0

    async def handler(arguments):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return {"value": arguments["value"]}

    executor = AsyncFunctionToolExecutor()
    executor.register(REFERENCE, handler)
    request = ToolExecutionRequest("action-contract-async", REFERENCE, {"value": 5})

    result = await assert_async_action_replay(
        executor, request, effect_observer=lambda: calls
    )

    assert result.output == {"value": 5}
    assert calls == 1


def test_sync_contract_rejects_a_stable_but_wrong_action_identity() -> None:
    class WrongIdentity:
        def execute(self, request):  # type: ignore[no-untyped-def]
            return ToolExecutionResult("another-action", True, {"value": 1})

    request = ToolExecutionRequest("expected-action", REFERENCE, {"value": 1})
    with pytest.raises(AssertionError, match="does not match"):
        assert_sync_action_replay(
            WrongIdentity(), request, effect_observer=lambda: 0
        )


def test_sync_contract_rejects_replayed_external_effect() -> None:
    class RepeatingEffect:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, request):  # type: ignore[no-untyped-def]
            self.calls += 1
            return ToolExecutionResult(request.action_id, True, {"value": 1})

    executor = RepeatingEffect()
    request = ToolExecutionRequest("repeat-action", REFERENCE, {"value": 1})
    with pytest.raises(AssertionError, match="repeated an external effect"):
        assert_sync_action_replay(
            executor, request, effect_observer=lambda: executor.calls
        )


def test_sync_contract_requires_conflicting_arguments_to_be_rejected() -> None:
    class AcceptsConflict:
        def execute(self, request):  # type: ignore[no-untyped-def]
            return ToolExecutionResult(request.action_id, True, {"value": 1})

    request = ToolExecutionRequest("conflict-action", REFERENCE, {"value": 1})
    with pytest.raises(AssertionError, match="conflicting arguments was accepted"):
        assert_sync_action_replay(
            AcceptsConflict(), request, effect_observer=lambda: 0
        )


def test_contract_accepts_explicit_conflict_rejection() -> None:
    calls = 0
    original = ToolExecutionRequest("manual-action", REFERENCE, {"value": 1})

    class ManualExecutor:
        def execute(self, request):  # type: ignore[no-untyped-def]
            nonlocal calls
            if request.to_mapping() != original.to_mapping():
                raise ToolExecutionRejectedError("conflict")
            if calls == 0:
                calls += 1
            return ToolExecutionResult(request.action_id, True, {"value": 1})

    result = assert_sync_action_replay(
        ManualExecutor(), original, effect_observer=lambda: calls
    )
    assert result.action_id == original.action_id


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mode", "message"),
    (
        ("wrong_identity", "does not match"),
        ("repeat_effect", "repeated an external effect"),
        ("accept_conflict", "conflicting arguments was accepted"),
    ),
)
async def test_async_contract_rejects_the_same_three_bad_executor_classes(
    mode: str, message: str
) -> None:
    class BadAsyncExecutor:
        def __init__(self) -> None:
            self.calls = 0

        async def execute_async(self, request, cancellation):  # type: ignore[no-untyped-def]
            del cancellation
            if mode == "repeat_effect":
                self.calls += 1
            action_id = (
                "another-action" if mode == "wrong_identity" else request.action_id
            )
            return ToolExecutionResult(action_id, True, {"value": 1})

    executor = BadAsyncExecutor()
    request = ToolExecutionRequest("async-bad-action", REFERENCE, {"value": 1})
    with pytest.raises(AssertionError, match=message):
        await assert_async_action_replay(
            executor,
            request,
            effect_observer=lambda: executor.calls,
        )


@pytest.mark.parametrize(
    ("expected", "operation", "code"),
    (
        (
            "failed",
            lambda: ToolExecutionResult("outcome-action", False, error="DECLINED"),
            "DECLINED",
        ),
        (
            "failed",
            lambda: (_ for _ in ()).throw(ToolExecutionFailedError("DECLINED")),
            "DECLINED",
        ),
        (
            "stopped",
            lambda: (_ for _ in ()).throw(ToolExecutionStoppedError("stopped")),
            None,
        ),
        (
            "uncertain",
            lambda: (_ for _ in ()).throw(
                ToolExecutionUncertainError("receipt unavailable")
            ),
            None,
        ),
    ),
)
def test_outcome_contract_helper_preserves_execution_facts(
    expected: str, operation, code: str | None  # type: ignore[no-untyped-def]
) -> None:
    assert_sync_outcome_classification(operation, expected, error_code=code)


def test_reconcile_contract_queries_without_redispatch() -> None:
    effects = ["committed"]
    request = ToolExecutionRequest("queried-action", REFERENCE, {})

    class QueryOnly:
        def reconcile(self, candidate):  # type: ignore[no-untyped-def]
            assert candidate == request
            return ToolExecutionResult(candidate.action_id, True, {"value": 1})

        def execute(self, candidate):  # type: ignore[no-untyped-def]
            assert candidate == request
            return ToolExecutionResult(candidate.action_id, True, {"value": 1})

    result = assert_sync_reconcile_query(
        QueryOnly(), request, effect_observer=lambda: effects
    )
    assert result.output == {"value": 1}

    class Recreates(QueryOnly):
        def execute(self, candidate):  # type: ignore[no-untyped-def]
            effects.append("recreated")
            return super().execute(candidate)

    with pytest.raises(AssertionError, match="recreated after reconciliation"):
        assert_sync_reconcile_query(
            Recreates(), request, effect_observer=lambda: effects
        )


def test_binding_and_credential_contract_helpers_are_reusable() -> None:
    class Bound:
        def __init__(self, target: str) -> None:
            self.target = target

        def execution_binding(self, tool):  # type: ignore[no-untyped-def]
            assert tool == REFERENCE
            return {"adapter": "probe-v1", "target": self.target}

    first, second = assert_execution_bindings_distinct(
        Bound("service-a"), Bound("service-b"), REFERENCE
    )
    assert first["target"] == "service-a"
    assert second["target"] == "service-b"
    assert_credential_free_contract(first, second, forbidden_values=("top-secret",))
    with pytest.raises(AssertionError, match="credential fields"):
        assert_credential_free_contract({"api_token": "top-secret"})


def test_python_tool_binding_uses_explicit_host_implementation_version() -> None:
    executor = FunctionToolExecutor()

    def handler(arguments):  # type: ignore[no-untyped-def]
        return arguments

    executor.register(REFERENCE, handler, implementation_version="wheel-2026.10")
    binding = executor.execution_binding(REFERENCE)
    assert binding == {
        "adapter": "python-function-v1",
        "implementation": f"{handler.__module__}.{handler.__qualname__}",
        "implementation_version": "wheel-2026.10",
        "mode": "sync",
    }
