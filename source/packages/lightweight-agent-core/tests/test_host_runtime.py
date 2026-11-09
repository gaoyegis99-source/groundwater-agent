from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Lock
import time
from typing import Any

import anyio
import pytest
from platform_lightweight_agent import (
    ActionAuthorization,
    AgentDecision,
    ApprovalDecision,
    FunctionToolExecutor,
    HostRuntimeCleanupError,
    HostRuntime,
    HostRuntimeCloseTimeoutError,
    HostRuntimeLimits,
    HostRuntimeOverloadedError,
    InMemoryRunJournal,
    LocalToolExecutor,
    NodeContract,
    ParallelBranch,
    RuntimeAssembly,
    RunExecutionMismatchError,
    RunRetentionPolicy,
    SQLiteRunJournal,
    ToolExecutionResult,
    ToolExecutionStoppedError,
    ToolExecutionUncertainError,
    ToolRecoveryCapabilities,
    ToolReference,
    ToolRoute,
    ValueBinding,
    WorkflowEngine,
    WorkflowRunError,
    WorkflowValidationError,
    agent_step,
    approval_step,
    parallel_step,
    process_resource_diagnostics,
    tool_step,
    workflow,
)


def _schema(
    properties: Mapping[str, Any], required: tuple[str, ...] = ()
) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


EMPTY = _schema({})
VALUE = _schema({"value": {"type": "integer"}}, ("value",))
REPLY = _schema({"message": {"type": "string"}}, ("message",))
APPROVED = _schema({"approved": {"type": "boolean"}}, ("approved",))


def _tool_workflow(reference: ToolReference):
    return workflow(
        "host-tool",
        "1",
        (
            tool_step(
                "write",
                reference,
                approval_required=False,
                contract=NodeContract(EMPTY, VALUE, {}),
            ),
        ),
        schema_version="v6-light-workflow-v3",
        input_schema=EMPTY,
        output_schema=VALUE,
        output_bindings={"/value": ValueBinding("step_output", "/value", "write")},
    )


def _approval_workflow():
    return workflow(
        "host-approval",
        "1",
        (
            approval_step(
                "review",
                "approve?",
                contract=NodeContract(EMPTY, APPROVED, {}),
            ),
        ),
        schema_version="v6-light-workflow-v3",
        input_schema=EMPTY,
        output_schema=APPROVED,
        output_bindings={
            "/approved": ValueBinding("step_output", "/approved", "review")
        },
    )


def _timed_tool_workflow(reference: ToolReference):
    return workflow(
        "host-timed-tool",
        "1",
        (
            tool_step(
                "write",
                reference,
                approval_required=False,
                contract=NodeContract(EMPTY, VALUE, {}, timeout_seconds=0.02),
            ),
        ),
        schema_version="v6-light-workflow-v3",
        input_schema=EMPTY,
        output_schema=VALUE,
        output_bindings={"/value": ValueBinding("step_output", "/value", "write")},
    )


def _mutex_host_workflow(reference: ToolReference, resource: str):
    return workflow(
        "host-mutex",
        "1",
        (
            tool_step(
                "write",
                reference,
                approval_required=False,
                contract=NodeContract(EMPTY, VALUE, {}),
                capabilities=ToolRecoveryCapabilities(
                    external_side_effects=True,
                    supports_result_query=True,
                    mutex_resource=resource,
                ),
            ),
        ),
        schema_version="v6-light-workflow-v4",
        input_schema=EMPTY,
        output_schema=VALUE,
        output_bindings={"/value": ValueBinding("step_output", "/value", "write")},
    )


class _CredentialRoute:
    def __init__(self) -> None:
        self.credentials: list[Mapping[str, object]] = []

    def execute(self, request):  # type: ignore[no-untyped-def]
        raise AssertionError("v3 should use the async route boundary")

    def reconcile(self, request):  # type: ignore[no-untyped-def]
        del request

    async def execute_with_credentials_async(self, request, credentials, cancellation):  # type: ignore[no-untyped-def]
        del cancellation
        self.credentials.append(dict(credentials))
        return ToolExecutionResult(request.action_id, True, {"value": 7})

    async def reconcile_with_credentials_async(self, request, credentials):  # type: ignore[no-untyped-def]
        del request, credentials


def test_host_runtime_routes_exact_tools_and_keeps_credentials_out_of_journal() -> None:
    reference = ToolReference("host.route", "1", "sha256:" + "a" * 64)
    route = _CredentialRoute()
    host = HostRuntime(
        RuntimeAssembly(
            InMemoryRunJournal(),
            tool_routes=(ToolRoute(reference, route, ("service-token",)),),
            credential_resolver=lambda name: (
                "top-secret" if name == "service-token" else None
            ),
        )
    )
    try:
        completed = host.run(_tool_workflow(reference), {})
    finally:
        host.close()

    assert completed.status == "SUCCEEDED"
    assert route.credentials == [{"service-token": "top-secret"}]
    serialized = str(completed.to_mapping())
    assert "top-secret" not in serialized
    assert "service-token" not in serialized


def test_host_handle_rejects_a_stale_control_after_run_name_reuse(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Host controls retain the same instance precondition as the engine."""

    journal = SQLiteRunJournal(tmp_path / "host-execution-identity.sqlite")
    host = HostRuntime(RuntimeAssembly(journal, LocalToolExecutor()))
    try:
        original = host.run(_approval_workflow(), {}, run_id="reused-host-run")
        approval_id = next(iter(original.state["v3"]["pending_approvals"]))
        acknowledged = host.resume(
            original.handle,
            approvals=(ApprovalDecision(approval_id, True),),
        )
        assert acknowledged.status == "RUNNING"
        assert host.resume(original.handle).status == "SUCCEEDED"
        journal.apply_retention(
            RunRetentionPolicy(archive_after_seconds=0, purge_after_seconds=0),
            now=time.time() + 60,
        )
        replacement = host.run(_approval_workflow(), {}, run_id="reused-host-run")

        with pytest.raises(RunExecutionMismatchError):
            host.request_cancel(original.handle)
        assert host.get(replacement.handle).status == "WAITING_APPROVAL"
    finally:
        host.close(mode="wait")


def test_host_runtime_closes_only_explicitly_owned_resources() -> None:
    class Journal(InMemoryRunJournal):
        def __init__(self) -> None:
            super().__init__()
            self.recovery_scans = 0

        def recoverable_runs(  # type: ignore[no-untyped-def]
            self, *, limit=256, now=None, after=None
        ):
            self.recovery_scans += 1
            return super().recoverable_runs(limit=limit, now=now, after=after)

    class Owned:
        def __init__(self) -> None:
            self.closed = 0

        def close(self) -> None:
            self.closed += 1

    reference = ToolReference("host.owned", "1", "sha256:" + "e" * 64)
    owned = Owned()
    journal = Journal()
    host = HostRuntime(
        RuntimeAssembly(
            journal,
            tool_routes=(ToolRoute(reference, _CredentialRoute()),),
            closeables=(owned,),
        )
    )

    host.close(mode="wait")

    assert owned.closed == 1
    assert journal.recovery_scans == 0


class _HeldToolExecutor:
    def __init__(self) -> None:
        # HostRuntime runs injected async adapters on its owned portal thread.
        # Standard-library events make this deliberate cross-thread test
        # barrier independent of either AnyIO event loop.
        self.started = Event()
        self.release = Event()
        self.active = 0
        self.peak = 0
        self.calls = 0

    def execute(self, request):  # type: ignore[no-untyped-def]
        raise AssertionError("v3 should use execute_async")

    def reconcile(self, request):  # type: ignore[no-untyped-def]
        del request

    async def execute_async(self, request, cancellation):  # type: ignore[no-untyped-def]
        del cancellation
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.started.set()
        await anyio.to_thread.run_sync(self.release.wait)
        self.active -= 1
        return ToolExecutionResult(request.action_id, True, {"value": 1})

    async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
        del request


class _HeldSyncToolExecutor:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.active = 0
        self.peak = 0
        self.calls = 0

    def execute(self, request):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.started.set()
        self.release.wait()
        self.active -= 1
        return ToolExecutionResult(request.action_id, True, {"value": 1})

    def reconcile(self, request):  # type: ignore[no-untyped-def]
        del request


class _UnknownOrHeldExecutor:
    def __init__(self, unknown_tool_id: str) -> None:
        self.unknown_tool_id = unknown_tool_id
        self.started = Event()
        self.release = Event()

    def execute(self, request):  # type: ignore[no-untyped-def]
        raise AssertionError("v3 should use execute_async")

    def reconcile(self, request):  # type: ignore[no-untyped-def]
        del request

    async def execute_async(self, request, cancellation):  # type: ignore[no-untyped-def]
        del cancellation
        if request.tool.tool_id == self.unknown_tool_id:
            raise ToolExecutionUncertainError("lost receipt")
        self.started.set()
        await anyio.to_thread.run_sync(self.release.wait)
        return ToolExecutionResult(request.action_id, True, {"value": 1})

    async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
        del request


class _SwitchablePolicy:
    def __init__(self) -> None:
        self.allowed = True
        self.calls = 0

    def authorize(self, request, *, run_id, workflow, step_id):  # type: ignore[no-untyped-def]
        del request, run_id, workflow, step_id
        self.calls += 1
        return (
            ActionAuthorization(True)
            if self.allowed
            else ActionAuthorization(False, "POLICY_REVOKED")
        )


@pytest.mark.anyio
async def test_host_runtime_caps_tool_calls_across_independent_runs() -> None:
    reference = ToolReference("host.limit", "1", "sha256:" + "b" * 64)
    executor = _HeldToolExecutor()
    host = HostRuntime(
        RuntimeAssembly(InMemoryRunJournal(), executor),
        limits=HostRuntimeLimits(
            max_active_runs=2,
            max_queued_runs=2,
            max_tool_calls=1,
            max_model_requests=2,
        ),
    )
    snapshots: list[object] = []
    try:
        async with anyio.create_task_group() as group:

            async def start(run_id: str) -> None:
                snapshots.append(
                    await host.run_async(_tool_workflow(reference), {}, run_id=run_id)
                )

            group.start_soon(start, "host-tool-one")
            await anyio.to_thread.run_sync(executor.started.wait)
            group.start_soon(start, "host-tool-two")
            await anyio.sleep(0.05)
            assert executor.peak == 1
            executor.release.set()
    finally:
        host.close()

    assert len(snapshots) == 2
    assert executor.peak == 1


@pytest.mark.anyio
async def test_host_cancellation_of_a_queued_run_prevents_late_start() -> None:
    reference = ToolReference("host.queued-cancel", "1", "sha256:" + "f" * 64)
    executor = _HeldToolExecutor()
    host = HostRuntime(
        RuntimeAssembly(InMemoryRunJournal(), executor),
        limits=HostRuntimeLimits(
            max_active_runs=1,
            max_queued_runs=2,
            max_tool_calls=1,
            max_model_requests=1,
        ),
    )
    completed: list[object] = []
    try:
        async with anyio.create_task_group() as group:

            async def first() -> None:
                completed.append(
                    await host.run_async(
                        _tool_workflow(reference), {}, run_id="host-first"
                    )
                )

            group.start_soon(first)
            await anyio.to_thread.run_sync(executor.started.wait)
            with anyio.move_on_after(0.02) as cancelled:
                await host.run_async(
                    _tool_workflow(reference), {}, run_id="host-queued"
                )
            assert cancelled.cancel_called
            executor.release.set()
        # Let the portal consume the cancelled queued submission after its
        # active-run slot is released.
        await anyio.sleep(0.05)
    finally:
        host.close(mode="wait")

    assert len(completed) == 1
    assert executor.calls == 1


class _HeldDriver:
    driver_version = "held-driver-v1"

    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.active = 0
        self.peak = 0

    def decide(self, context):  # type: ignore[no-untyped-def]
        del context
        raise AssertionError("v3 should use decide_async")

    async def decide_async(self, context):  # type: ignore[no-untyped-def]
        del context
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.started.set()
        await anyio.to_thread.run_sync(self.release.wait)
        self.active -= 1
        return AgentDecision("done")


class _AsyncOnlyDriver:
    driver_version = "async-only-v1"

    async def decide_async(self, context):  # type: ignore[no-untyped-def]
        del context
        return AgentDecision("done")


def _agent_workflow():
    return workflow(
        "host-agent",
        "1",
        (
            agent_step(
                "answer",
                "model",
                tools=(),
                contract=NodeContract(EMPTY, REPLY, {}),
            ),
        ),
        schema_version="v6-light-workflow-v3",
        input_schema=EMPTY,
        output_schema=REPLY,
        output_bindings={"/message": ValueBinding("step_output", "/message", "answer")},
    )


def _approval_workflow():
    approved = _schema({"approved": {"type": "boolean"}}, ("approved",))
    return workflow(
        "host-approval",
        "1",
        (
            approval_step(
                "review", "Approve?", contract=NodeContract(EMPTY, approved, {})
            ),
        ),
        schema_version="v6-light-workflow-v3",
        input_schema=EMPTY,
        output_schema=approved,
        output_bindings={
            "/approved": ValueBinding("step_output", "/approved", "review")
        },
    )


def _approval_then_tool_workflow(reference: ToolReference):
    approved = _schema({"approved": {"type": "boolean"}}, ("approved",))
    return workflow(
        "host-restart-approval",
        "1",
        (
            approval_step(
                "review", "Approve?", contract=NodeContract(EMPTY, approved, {})
            ),
            tool_step(
                "write",
                reference,
                approval_required=False,
                contract=NodeContract(EMPTY, VALUE, {}),
            ),
        ),
        schema_version="v6-light-workflow-v3",
        input_schema=EMPTY,
        output_schema=VALUE,
        output_bindings={"/value": ValueBinding("step_output", "/value", "write")},
    )


def _approval_while_tool_runs(reference: ToolReference):
    approved = _schema({"approved": {"type": "boolean"}}, ("approved",))
    joined = _schema({"review": approved, "work": VALUE}, ("review", "work"))
    fanout = parallel_step(
        "fanout",
        (
            ParallelBranch(
                "review",
                (
                    approval_step(
                        "review-step",
                        "Continue?",
                        contract=NodeContract(EMPTY, approved, {}),
                    ),
                ),
            ),
            ParallelBranch(
                "work",
                (
                    tool_step(
                        "work-step",
                        reference,
                        approval_required=False,
                        contract=NodeContract(EMPTY, VALUE, {}),
                    ),
                ),
            ),
        ),
        contract=NodeContract(EMPTY, joined, {}),
        failure_policy="wait_all",
    )
    return workflow(
        "host-approval-race",
        "1",
        (fanout,),
        schema_version="v6-light-workflow-v3",
        input_schema=EMPTY,
        output_schema=EMPTY,
        output_bindings={},
    )


@pytest.mark.anyio
async def test_host_runtime_caps_model_requests_and_rejects_full_queue() -> None:
    driver = _HeldDriver()
    executor = _HeldToolExecutor()
    host = HostRuntime(
        RuntimeAssembly(InMemoryRunJournal(), executor, {"model": driver}),
        limits=HostRuntimeLimits(
            max_active_runs=2,
            max_queued_runs=1,
            max_tool_calls=1,
            max_model_requests=1,
        ),
    )
    completed: list[object] = []
    try:
        async with anyio.create_task_group() as group:

            async def start(run_id: str) -> None:
                completed.append(
                    await host.run_async(_agent_workflow(), {}, run_id=run_id)
                )

            group.start_soon(start, "host-model-one")
            await anyio.to_thread.run_sync(driver.started.wait)
            group.start_soon(start, "host-model-two")
            group.start_soon(start, "host-model-three")
            await anyio.sleep(0.05)
            assert driver.peak == 1
            with pytest.raises(HostRuntimeOverloadedError, match="OVERLOADED"):
                await host.run_async(_agent_workflow(), {}, run_id="host-model-four")
            driver.release.set()
    finally:
        host.close()

    assert len(completed) == 3
    assert driver.peak == 1


@pytest.mark.anyio
async def test_host_runtime_accepts_an_async_only_driver_for_v3() -> None:
    host = HostRuntime(
        RuntimeAssembly(
            InMemoryRunJournal(),
            _HeldToolExecutor(),
            {"model": _AsyncOnlyDriver()},
        )
    )
    try:
        snapshot = await host.run_async(_agent_workflow(), {}, run_id="async-only")
    finally:
        host.close(mode="wait")

    assert snapshot.status == "SUCCEEDED"


@pytest.mark.anyio
async def test_host_invalid_approval_does_not_turn_into_cancellation() -> None:
    host = HostRuntime(RuntimeAssembly(InMemoryRunJournal(), _HeldToolExecutor()))
    try:
        waiting = await host.run_async(_approval_workflow(), {}, run_id="bad-approval")
        assert waiting.status == "WAITING_APPROVAL"
        with pytest.raises(WorkflowRunError, match="does not identify"):
            await host.resume_async(
                waiting.run_id,
                approvals=(ApprovalDecision("approval-does-not-exist", True),),
            )
        persisted = host.get(waiting.run_id)
    finally:
        host.close(mode="wait")

    assert persisted.status == "WAITING_APPROVAL"
    assert persisted.revision == waiting.revision
    assert persisted.state["v3"]["cancel_requested"] is False
    assert len(persisted.state["v3"]["pending_approvals"]) == 1


@pytest.mark.anyio
async def test_host_persists_parallel_approval_while_execution_slot_is_busy() -> None:
    reference = ToolReference("host.control", "1", "sha256:" + "c" * 64)
    executor = _HeldToolExecutor()
    host = HostRuntime(
        RuntimeAssembly(InMemoryRunJournal(), executor),
        limits=HostRuntimeLimits(
            max_active_runs=1,
            max_queued_runs=1,
            max_tool_calls=1,
            max_model_requests=1,
            max_control_operations=1,
        ),
    )
    completed: dict[str, object] = {}
    try:
        async with anyio.create_task_group() as group:

            async def run() -> None:
                completed["snapshot"] = await host.run_async(
                    _approval_while_tool_runs(reference), {}, run_id="control-race"
                )

            group.start_soon(run)
            await anyio.to_thread.run_sync(executor.started.wait)
            while True:
                current = host.get("control-race")
                pending = current.state["v3"]["pending_approvals"]
                if pending:
                    break
                await anyio.lowlevel.checkpoint()
            approval_id = next(iter(pending))
            # This returns after the control fact is journaled; it does not
            # wait for the held sibling branch's active-run slot.
            with anyio.fail_after(0.5):
                accepted = await host.resume_async(
                    "control-race", approvals=(ApprovalDecision(approval_id, True),)
                )
            assert accepted.state["v3"]["approval_decisions"][approval_id] is True
            assert approval_id not in accepted.state["v3"]["pending_approvals"]
            executor.release.set()
        final = completed["snapshot"]
    finally:
        host.close(mode="wait")

    assert final.status == "SUCCEEDED"  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_host_start_recovers_a_saved_approval_without_resubmission(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    reference = ToolReference("host.restart", "1", "sha256:" + "9" * 64)
    calls: list[str] = []
    journal_path = tmp_path / "host-restart.sqlite"

    class Executor:
        def execute(self, request):  # type: ignore[no-untyped-def]
            calls.append(request.action_id)
            return ToolExecutionResult(request.action_id, True, {"value": 1})

        def reconcile(self, request):  # type: ignore[no-untyped-def]
            del request

    first = WorkflowEngine(SQLiteRunJournal(journal_path), Executor(), {})
    waiting = await first.run_async(
        _approval_then_tool_workflow(reference), {}, run_id="saved-approval"
    )
    approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))
    # This represents a process exit after the approval fact committed but
    # before its in-memory continuation can claim a new executor lease.
    saved = await first.resume_async(
        waiting.run_id,
        approvals=(ApprovalDecision(approval_id, True),),
        advance=False,
    )
    assert saved.status == "RUNNING"

    host = HostRuntime(RuntimeAssembly(SQLiteRunJournal(journal_path), Executor()))
    try:
        host.start()
        for _ in range(100):
            if host.get(saved.run_id).status == "SUCCEEDED":
                break
            await anyio.sleep(0.01)
        else:
            raise AssertionError("host did not recover saved approval")
    finally:
        host.close(mode="wait")

    assert calls and len(calls) == 1


async def _saved_running_after_approval(journal, reference: ToolReference, run_id: str):  # type: ignore[no-untyped-def]
    """Create a durable v3 continuation without driving its tool yet."""

    class Executor:
        def execute(self, request):  # type: ignore[no-untyped-def]
            return ToolExecutionResult(request.action_id, True, {"value": 1})

        def reconcile(self, request):  # type: ignore[no-untyped-def]
            del request

    engine = WorkflowEngine(journal, Executor(), {})
    waiting = await engine.run_async(
        _approval_then_tool_workflow(reference), {}, run_id=run_id
    )
    approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))
    return await engine.resume_async(
        run_id,
        approvals=(ApprovalDecision(approval_id, True),),
        advance=False,
    )


@pytest.mark.anyio
async def test_host_periodically_recovers_after_a_previous_lease_expires(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    """Starting during another owner's lease does not require a manual resume."""

    reference = ToolReference("host.lease-wakeup", "1", "sha256:" + "6" * 64)
    journal = SQLiteRunJournal(tmp_path / "lease-wakeup.sqlite")
    saved = await _saved_running_after_approval(journal, reference, "leased-run")
    state = deepcopy(saved.state)
    state["v3"]["execution"] = {
        "generation": 1,
        "owner_id": "executor-from-exited-process",
        "lease_expires": time.time() + 0.05,
    }
    journal.replace(
        saved.next_revision(state=state),
        expected_revision=saved.revision,
        event_type="TEST_EXECUTOR_LEASE",
        payload={},
    )
    calls: list[str] = []

    class Executor:
        def execute(self, request):  # type: ignore[no-untyped-def]
            calls.append(request.action_id)
            return ToolExecutionResult(request.action_id, True, {"value": 1})

        def reconcile(self, request):  # type: ignore[no-untyped-def]
            del request

    host = HostRuntime(
        RuntimeAssembly(journal, Executor()),
        limits=HostRuntimeLimits(recovery_scan_interval_seconds=0.01),
    )
    try:
        host.start()
        with anyio.fail_after(1):
            while host.get("leased-run").status != "SUCCEEDED":
                await anyio.sleep(0.01)
    finally:
        host.close(mode="wait")

    assert len(calls) == 1


@pytest.mark.anyio
async def test_expired_execution_lease_fences_a_stale_other_process(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A prior OS process cannot commit after a new owner claims its run."""

    from platform_lightweight_agent.workflow_v3 import _claim_execution

    reference = ToolReference("host.cross-process-lease", "1", "sha256:" + "3" * 64)
    journal_path = tmp_path / "cross-process-lease.sqlite"
    journal = SQLiteRunJournal(journal_path)
    saved = await _saved_running_after_approval(journal, reference, "fenced-run")
    claimed_path = tmp_path / "claimed.json"
    release_path = tmp_path / "release"
    result_path = tmp_path / "stale-result.json"
    package_root = Path(__file__).resolve().parents[1]
    worker = (
        Path(__file__).resolve().parent
        / "defect_discovery"
        / "lease_fencing"
        / "worker.py"
    )
    environment = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
        if key in os.environ
    }
    environment["PYTHONPATH"] = str(package_root / "src")
    process = subprocess.Popen(
        [
            sys.executable,
            str(worker),
            "--journal",
            str(journal_path),
            "--run-id",
            saved.run_id,
            "--claimed",
            str(claimed_path),
            "--release",
            str(release_path),
            "--output",
            str(result_path),
        ],
        cwd=package_root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        with anyio.fail_after(5):
            while not claimed_path.exists():
                if process.poll() is not None:
                    _, stderr = process.communicate()
                    raise AssertionError(f"lease owner exited early: {stderr[-2_000:]}")
                await anyio.sleep(0.01)
        first_claim = json.loads(claimed_path.read_text(encoding="utf-8"))
        assert first_claim["pid"] == process.pid
        assert first_claim["generation"] > 0
        with anyio.fail_after(5):
            while True:
                control = journal.control(saved.run_id)
                if (
                    control.execution_lease_expires is not None
                    and control.execution_lease_expires <= time.time()
                ):
                    break
                await anyio.sleep(0.01)

        class NeverExecutor:
            def execute(self, request):  # type: ignore[no-untyped-def]
                raise AssertionError(f"unexpected tool dispatch: {request!r}")

            def reconcile(self, request):  # type: ignore[no-untyped-def]
                raise AssertionError(f"unexpected reconciliation: {request!r}")

        new_owner = WorkflowEngine(journal, NeverExecutor(), {})
        claimed, _, generation = await _claim_execution(
            new_owner, journal.get(saved.run_id)
        )
        assert generation == first_claim["generation"] + 1
        release_path.touch()
        _, stderr = await anyio.to_thread.run_sync(
            lambda: process.communicate(timeout=5)
        )
        assert process.returncode == 0, stderr[-2_000:]
        result = json.loads(result_path.read_text(encoding="utf-8"))
        assert result == {"pid": process.pid, "outcome": "fenced"}
        final = journal.get(saved.run_id)
        assert final.revision == claimed.revision
        assert final.state["v3"]["execution"]["generation"] == generation
        assert "STALE_OWNER_WRITE" not in {
            event.type for event in journal.events(saved.run_id, limit=100)
        }
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.anyio
async def test_host_recovers_after_previous_process_dies_with_live_lease(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    """A startup scan must not be the only chance to resume a crashed owner."""

    reference = ToolReference("host.crashed-owner", "1", "sha256:" + "8" * 64)
    journal_path = tmp_path / "crashed-owner.sqlite"
    journal = SQLiteRunJournal(journal_path)
    saved = await _saved_running_after_approval(journal, reference, "crashed-run")
    claimed_path = tmp_path / "claimed.json"
    worker = (
        Path(__file__).resolve().parent
        / "defect_discovery"
        / "lease_fencing"
        / "worker.py"
    )
    package_root = Path(__file__).resolve().parents[1]
    environment = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
        if key in os.environ
    }
    environment["PYTHONPATH"] = str(package_root / "src")
    process = subprocess.Popen(
        [
            sys.executable,
            str(worker),
            "--journal",
            str(journal_path),
            "--run-id",
            saved.run_id,
            "--claimed",
            str(claimed_path),
            "--release",
            str(tmp_path / "never-release"),
            "--output",
            str(tmp_path / "never-written.json"),
            "--lease-seconds",
            "60",
        ],
        cwd=package_root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    host: HostRuntime | None = None
    try:
        with anyio.fail_after(5):
            while not claimed_path.exists():
                if process.poll() is not None:
                    _, stderr = process.communicate()
                    raise AssertionError(f"lease owner exited early: {stderr[-2_000:]}")
                await anyio.sleep(0.01)
        claim = json.loads(claimed_path.read_text(encoding="utf-8"))
        assert claim["pid"] == process.pid
        process.kill()
        process.wait(timeout=5)

        calls: list[str] = []

        class Executor:
            def execute(self, request):  # type: ignore[no-untyped-def]
                calls.append(request.action_id)
                return ToolExecutionResult(request.action_id, True, {"value": 1})

            def reconcile(self, request):  # type: ignore[no-untyped-def]
                del request

        host = HostRuntime(
            RuntimeAssembly(SQLiteRunJournal(journal_path), Executor()),
            limits=HostRuntimeLimits(recovery_scan_interval_seconds=0.02),
        )
        host.start()
        original_lease = host.journal.control(saved.run_id).execution_lease_expires
        assert original_lease is not None and original_lease > time.time()
        assert host.get(saved.run_id).status == "RUNNING"
        assert calls == []

        # The killed owner claimed a long lease so startup must observe it as
        # live even under suite load. Shorten that one durable test fact only
        # after the host starts; its periodic scan must notice expiry without
        # an incoming user request or a new execution owner.
        current = journal.get(saved.run_id)
        state = json.loads(json.dumps(current.state))
        state["v3"]["execution"]["lease_expires"] = time.time() + 1
        journal.replace(
            current.next_revision(state=state),
            expected_revision=current.revision,
            event_type="TEST_LEASE_SHORTENED",
            payload={},
        )
        lease = journal.control(saved.run_id).execution_lease_expires
        assert lease is not None and lease > time.time()
        with anyio.fail_after(max(0, lease - time.time()) + 10):
            while host.get(saved.run_id).status != "SUCCEEDED":
                await anyio.sleep(0.02)
        assert len(calls) == 1
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if host is not None:
            host.close(mode="wait")


@pytest.mark.anyio
async def test_host_recovery_cursor_skips_incompatible_rows_without_spin(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    """An unchanged bad binding cannot hide a later compatible continuation."""

    reference = ToolReference("host.fair-recovery", "1", "sha256:" + "5" * 64)
    attempts: dict[str, int] = {}

    class BindingMismatchJournal(SQLiteRunJournal):
        blocked: set[str] = set()

        def get(self, run_id: str):  # type: ignore[no-untyped-def]
            if run_id in self.blocked:
                attempts[run_id] = attempts.get(run_id, 0) + 1
                raise WorkflowValidationError("target binding is unavailable")
            return super().get(run_id)

    journal = BindingMismatchJournal(tmp_path / "fair-recovery.sqlite")
    blocked_ids = tuple(f"blocked-{index}" for index in range(3))
    for run_id in (*blocked_ids, "compatible"):
        await _saved_running_after_approval(journal, reference, run_id)
    journal.blocked = set(blocked_ids)
    calls: list[str] = []

    class Executor:
        def execute(self, request):  # type: ignore[no-untyped-def]
            calls.append(request.action_id)
            return ToolExecutionResult(request.action_id, True, {"value": 1})

        def reconcile(self, request):  # type: ignore[no-untyped-def]
            del request

    host = HostRuntime(
        RuntimeAssembly(journal, Executor()),
        limits=HostRuntimeLimits(
            max_active_runs=1,
            max_queued_runs=1,
            recovery_scan_interval_seconds=0.01,
        ),
    )
    try:
        host.start()
        with anyio.fail_after(1):
            while host.get("compatible").status != "SUCCEEDED":
                await anyio.sleep(0.01)
        await anyio.sleep(0.06)
        blocks = {item.run_id: item for item in host.recovery_blocks()}
    finally:
        host.close(mode="wait")

    assert len(calls) == 1
    assert set(attempts) == set(blocked_ids)
    assert all(attempts[run_id] == 1 for run_id in blocked_ids)
    assert {item.code for item in blocks.values()} == {"RECOVERY_WORKFLOW_INVALID"}


@pytest.mark.anyio
async def test_host_recovery_uses_available_run_and_tool_capacity_in_parallel() -> None:
    reference = ToolReference("host.parallel-recovery", "1", "sha256:" + "4" * 64)
    journal = InMemoryRunJournal()
    await _saved_running_after_approval(journal, reference, "recover-one")
    await _saved_running_after_approval(journal, reference, "recover-two")
    executor = _HeldToolExecutor()
    host = HostRuntime(
        RuntimeAssembly(journal, executor),
        limits=HostRuntimeLimits(
            max_active_runs=2,
            max_queued_runs=2,
            max_tool_calls=2,
            max_model_requests=1,
        ),
    )
    try:
        host.start()
        with anyio.fail_after(1):
            while executor.calls < 2:
                await anyio.sleep(0.01)
        assert executor.peak == 2
        executor.release.set()
        with anyio.fail_after(1):
            while any(
                host.get(run_id).status != "SUCCEEDED"
                for run_id in ("recover-one", "recover-two")
            ):
                await anyio.sleep(0.01)
    finally:
        executor.release.set()
        host.close(mode="wait")


@pytest.mark.anyio
async def test_host_bare_resume_drives_a_saved_approval_after_restart(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    """``resume`` remains an execution command even without a new control."""

    reference = ToolReference("host.manual-restart", "1", "sha256:" + "7" * 64)
    journal_path = tmp_path / "host-manual-restart.sqlite"
    calls: list[str] = []

    class Executor:
        def execute(self, request):  # type: ignore[no-untyped-def]
            calls.append(request.action_id)
            return ToolExecutionResult(request.action_id, True, {"value": 1})

        def reconcile(self, request):  # type: ignore[no-untyped-def]
            del request

    source = WorkflowEngine(SQLiteRunJournal(journal_path), Executor(), {})
    waiting = await source.run_async(
        _approval_then_tool_workflow(reference), {}, run_id="manual-saved-approval"
    )
    approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))
    saved = await source.resume_async(
        waiting.run_id,
        approvals=(ApprovalDecision(approval_id, True),),
        advance=False,
    )

    class NoAutomaticRecoveryJournal(SQLiteRunJournal):
        """Keep startup idle so this test exercises explicit Host.resume."""

        def recoverable_runs(self, *, limit=256, now=None):  # type: ignore[no-untyped-def]
            del limit, now
            return ()

    host = HostRuntime(
        RuntimeAssembly(NoAutomaticRecoveryJournal(journal_path), Executor())
    )
    try:
        completed = await host.resume_async(saved.run_id)
    finally:
        host.close(mode="wait")

    assert completed.status == "SUCCEEDED"
    assert calls == [next(iter(completed.state["actions"]))]


@pytest.mark.anyio
async def test_host_close_cancels_durable_background_continuations_before_dispatch() -> (
    None
):
    """A bounded drain never turns queued approvals into late side effects."""

    reference = ToolReference("host.drain", "1", "sha256:" + "3" * 64)
    executor = _HeldToolExecutor()
    journal = InMemoryRunJournal()
    creator = WorkflowEngine(journal, executor, {})
    waiting = [
        await creator.run_async(
            _approval_then_tool_workflow(reference), {}, run_id=f"queued-{index}"
        )
        for index in range(12)
    ]
    host = HostRuntime(
        RuntimeAssembly(journal, executor),
        limits=HostRuntimeLimits(
            max_active_runs=1,
            max_queued_runs=1,
            max_tool_calls=1,
            max_model_requests=1,
        ),
    )
    advance_started = Event()
    allow_advance = Event()
    advance_lock = Lock()
    active_advances = 0
    peak_advances = 0
    original_operate = host._operate
    original_request_cancel = host._request_cancel_if_started
    holder_cancelled = Event()

    async def observe_background_advance(
        operation: str,
        run_id: str,
        submission_id: str,
        *args: object,
        **kwargs: object,
    ) -> object:
        """Count tasks before admission; old fan-out leaked waiters here."""

        nonlocal active_advances, peak_advances
        if operation != "advance":
            return await original_operate(
                operation, run_id, submission_id, *args, **kwargs
            )
        with advance_lock:
            active_advances += 1
            peak_advances = max(peak_advances, active_advances)
        advance_started.set()
        try:
            await anyio.to_thread.run_sync(allow_advance.wait)
            return await original_operate(
                operation, run_id, submission_id, *args, **kwargs
            )
        finally:
            with advance_lock:
                active_advances -= 1

    host._operate = observe_background_advance  # type: ignore[method-assign]

    async def observe_cancellation(run_id: str) -> bool:
        accepted = await original_request_cancel(run_id)
        if run_id == "holder":
            holder_cancelled.set()
        return accepted

    host._request_cancel_if_started = observe_cancellation  # type: ignore[method-assign]
    holder: dict[str, object] = {}
    try:
        async with anyio.create_task_group() as group:

            async def occupy_execution_slot() -> None:
                holder["snapshot"] = await host.run_async(
                    _tool_workflow(reference), {}, run_id="holder"
                )

            async def close_host() -> None:
                await anyio.to_thread.run_sync(
                    lambda: host.close(mode="request_cancel", timeout_seconds=2)
                )

            group.start_soon(occupy_execution_slot)
            await anyio.to_thread.run_sync(executor.started.wait)
            for snapshot in waiting:
                approval_id = next(iter(snapshot.state["v3"]["pending_approvals"]))
                accepted = await host.resume_async(
                    snapshot.run_id, approvals=(ApprovalDecision(approval_id, True),)
                )
                assert accepted.status == "RUNNING"
            assert await anyio.to_thread.run_sync(advance_started.wait, 1)
            # The fixed supervisor is the only task allowed to reach the
            # admission boundary. The twelve remaining continuations exist
            # only as RUNNING rows in the journal.
            await anyio.sleep(0.05)
            with advance_lock:
                assert peak_advances == 1
                assert executor.calls == 1
            assert "holder" in host._owned_run_ids
            allow_advance.set()
            group.start_soon(close_host)
            with anyio.fail_after(0.5):
                while not host._closing:
                    await anyio.lowlevel.checkpoint()
            assert await anyio.to_thread.run_sync(holder_cancelled.wait, 1)
            assert host.get("holder").status == "CANCEL_REQUESTED"
            executor.release.set()
    finally:
        allow_advance.set()
        if not executor.release.is_set():
            executor.release.set()
        if not host._closed:
            host.close(mode="wait", timeout_seconds=2)

    assert holder["snapshot"].status in {"CANCELLED", "RECONCILING"}  # type: ignore[index,union-attr]
    assert executor.calls == 1
    assert all(
        journal.get(snapshot.run_id).status != "SUCCEEDED" for snapshot in waiting
    )


@pytest.mark.anyio
async def test_host_persists_cancellation_intent_without_waiting_for_a_tool() -> None:
    reference = ToolReference("host.cancel-control", "1", "sha256:" + "6" * 64)
    executor = _HeldToolExecutor()
    host = HostRuntime(
        RuntimeAssembly(InMemoryRunJournal(), executor),
        limits=HostRuntimeLimits(
            max_active_runs=1,
            max_queued_runs=1,
            max_tool_calls=1,
            max_model_requests=1,
            max_control_operations=1,
        ),
    )
    result: dict[str, object] = {}
    try:
        async with anyio.create_task_group() as group:

            async def run() -> None:
                result["snapshot"] = await host.run_async(
                    _tool_workflow(reference), {}, run_id="cancel-control"
                )

            group.start_soon(run)
            await anyio.to_thread.run_sync(executor.started.wait)
            with anyio.fail_after(0.5):
                requested = await host.request_cancel_async("cancel-control")
            assert requested.status == "CANCEL_REQUESTED"
            assert executor.active == 1
            executor.release.set()
    finally:
        host.close(mode="wait")

    assert result["snapshot"].status in {"CANCELLED", "RECONCILING"}  # type: ignore[index,union-attr]


@pytest.mark.anyio
async def test_host_rechecks_authorization_after_its_tool_queue() -> None:
    reference = ToolReference("host.revoked", "1", "sha256:" + "7" * 64)
    executor = _HeldToolExecutor()
    policy = _SwitchablePolicy()
    host = HostRuntime(
        RuntimeAssembly(InMemoryRunJournal(), executor, policy=policy),
        limits=HostRuntimeLimits(
            max_active_runs=2,
            max_queued_runs=2,
            max_tool_calls=1,
            max_model_requests=1,
        ),
    )
    results: dict[str, object] = {}
    try:
        async with anyio.create_task_group() as group:

            async def invoke(name: str) -> None:
                results[name] = await host.run_async(
                    _tool_workflow(reference), {}, run_id=name
                )

            group.start_soon(invoke, "granted-first")
            await anyio.to_thread.run_sync(executor.started.wait)
            group.start_soon(invoke, "revoked-while-queued")
            await anyio.sleep(0.05)
            # The second action waits on the host's final tool gate, so it has
            # not yet observed authorization.  Revoke before capacity opens.
            assert policy.calls == 1
            policy.allowed = False
            executor.release.set()
    finally:
        host.close(mode="wait")

    assert results["granted-first"].status == "SUCCEEDED"  # type: ignore[index,union-attr]
    denied = results["revoked-while-queued"]
    assert denied.status == "BLOCKED"  # type: ignore[union-attr]
    assert denied.state["failure"]["code"] == "ACTION_DENIED"  # type: ignore[union-attr,index]
    assert executor.calls == 1


@pytest.mark.anyio
async def test_host_records_manual_receipt_while_an_execution_slot_is_busy() -> None:
    unknown_reference = ToolReference("host.unknown", "1", "sha256:" + "4" * 64)
    held_reference = ToolReference("host.receipt-held", "1", "sha256:" + "5" * 64)
    executor = _UnknownOrHeldExecutor(unknown_reference.tool_id)
    host = HostRuntime(
        RuntimeAssembly(InMemoryRunJournal(), executor),
        limits=HostRuntimeLimits(
            max_active_runs=1,
            max_queued_runs=1,
            max_tool_calls=1,
            max_model_requests=1,
        ),
    )
    held_result: dict[str, object] = {}
    try:
        unknown = await host.run_async(
            _tool_workflow(unknown_reference), {}, run_id="unknown-receipt"
        )
        assert unknown.status == "RECONCILING"
        action_id = next(iter(unknown.state["actions"]))
        async with anyio.create_task_group() as group:

            async def hold_slot() -> None:
                held_result["snapshot"] = await host.run_async(
                    _tool_workflow(held_reference), {}, run_id="receipt-held"
                )

            group.start_soon(hold_slot)
            await anyio.to_thread.run_sync(executor.started.wait)
            with anyio.fail_after(0.5):
                accepted = await host.reconcile_async(
                    "unknown-receipt",
                    action_id,
                    ToolExecutionResult(action_id, True, {"value": 1}),
                )
            assert accepted.state["actions"][action_id]["status"] == "RESOLVED"
            executor.release.set()
        for _ in range(50):
            settled = host.get("unknown-receipt")
            if settled.status == "SUCCEEDED":
                break
            await anyio.sleep(0.01)
        else:
            raise AssertionError("queued execution continuation did not settle")
    finally:
        host.close(mode="wait")

    assert held_result["snapshot"].status == "SUCCEEDED"  # type: ignore[index,union-attr]


@pytest.mark.anyio
async def test_host_keeps_sync_tool_capacity_and_resources_until_worker_stops() -> None:
    reference = ToolReference("host.sync", "1", "sha256:" + "8" * 64)
    executor = _HeldSyncToolExecutor()

    class Owned:
        closed = False

        def close(self) -> None:
            self.closed = True

    owned = Owned()
    host = HostRuntime(
        RuntimeAssembly(InMemoryRunJournal(), executor, closeables=(owned,)),
        limits=HostRuntimeLimits(
            max_active_runs=2,
            max_queued_runs=2,
            max_tool_calls=1,
            max_model_requests=1,
            shutdown_timeout_seconds=0.02,
        ),
    )
    results: dict[str, object] = {}
    try:
        async with anyio.create_task_group() as group:

            async def invoke(name: str) -> None:
                results[name] = await host.run_async(
                    _timed_tool_workflow(reference), {}, run_id=name
                )

            group.start_soon(invoke, "sync-first")
            await anyio.to_thread.run_sync(executor.started.wait)
            # The v3 timeout abandons the caller's wait but cannot prove the
            # synchronous operation stopped.  A second request must remain
            # queued at the host limiter rather than overlap physically.
            group.start_soon(invoke, "sync-second")
            await anyio.sleep(0.08)
            assert executor.peak == 1
            with pytest.raises(HostRuntimeCloseTimeoutError, match="CLOSE_TIMEOUT"):
                host.close(mode="wait", timeout_seconds=0.01)
            assert owned.closed is False
            executor.release.set()
        await anyio.sleep(0.05)
    finally:
        host.close(mode="wait")

    assert executor.peak == 1
    assert owned.closed is True


@pytest.mark.anyio
async def test_host_close_requests_the_live_sync_tool_context_token() -> None:
    reference = ToolReference("host.sync.context", "1", "sha256:" + "9" * 64)
    started = Event()
    stopped = Event()
    executor = FunctionToolExecutor()

    def cooperative(arguments, context):  # type: ignore[no-untyped-def]
        del arguments
        started.set()
        while not context.cancellation.cancelled:
            time.sleep(0.002)
        stopped.set()
        raise ToolExecutionStoppedError("host cancellation observed")

    executor.register(reference, cooperative)

    class Owned:
        closed = False

        def close(self) -> None:
            self.closed = True

    owned = Owned()
    host = HostRuntime(
        RuntimeAssembly(InMemoryRunJournal(), executor, closeables=(owned,)),
        limits=HostRuntimeLimits(shutdown_timeout_seconds=1),
    )
    result: dict[str, object] = {}

    async with anyio.create_task_group() as group:

        async def invoke() -> None:
            result["snapshot"] = await host.run_async(
                _tool_workflow(reference), {}, run_id="host-sync-context-cancel"
            )

        group.start_soon(invoke)
        assert await anyio.to_thread.run_sync(started.wait, 1)
        await anyio.to_thread.run_sync(
            lambda: host.close(mode="request_cancel", timeout_seconds=1)
        )

    assert stopped.is_set()
    assert owned.closed is True
    assert result["snapshot"].status in {"CANCELLED", "RECONCILING"}  # type: ignore[union-attr]


@pytest.mark.anyio
async def test_host_close_timeout_keeps_runtime_available_until_work_settles() -> None:
    reference = ToolReference("host.close", "1", "sha256:" + "d" * 64)
    executor = _HeldToolExecutor()
    host = HostRuntime(
        RuntimeAssembly(InMemoryRunJournal(), executor),
        limits=HostRuntimeLimits(shutdown_timeout_seconds=0.01),
    )
    result: dict[str, object] = {}
    async with anyio.create_task_group() as group:

        async def start() -> None:
            result["snapshot"] = await host.run_async(
                _tool_workflow(reference), {}, run_id="host-close-run"
            )

        group.start_soon(start)
        await anyio.to_thread.run_sync(executor.started.wait)
        with pytest.raises(HostRuntimeCloseTimeoutError, match="CLOSE_TIMEOUT"):
            host.close(mode="wait", timeout_seconds=0.01)
        executor.release.set()
    host.close(mode="wait")

    assert result["snapshot"].status == "SUCCEEDED"  # type: ignore[index,union-attr]


@pytest.mark.parametrize("asyncio_debug", [False, True])
@pytest.mark.anyio
async def test_two_host_event_loops_share_mutex_and_close_independently(
    monkeypatch: pytest.MonkeyPatch, asyncio_debug: bool
) -> None:
    if asyncio_debug:
        monkeypatch.setenv("PYTHONASYNCIODEBUG", "1")
    else:
        monkeypatch.delenv("PYTHONASYNCIODEBUG", raising=False)
    reference = ToolReference("host.mutex", "1", "sha256:" + "a" * 64)
    first_started = Event()
    first_release = Event()
    second_started = Event()
    second_release = Event()

    class SharedExecutor:
        def __init__(self) -> None:
            self.lock = Lock()
            self.calls = 0
            self.active = 0
            self.peak = 0

        async def execute_async(self, request, cancellation):  # type: ignore[no-untyped-def]
            del cancellation
            with self.lock:
                self.calls += 1
                ordinal = self.calls
                self.active += 1
                self.peak = max(self.peak, self.active)
            if ordinal == 1:
                first_started.set()
                await anyio.to_thread.run_sync(first_release.wait)
            else:
                second_started.set()
                await anyio.to_thread.run_sync(second_release.wait)
            with self.lock:
                self.active -= 1
            return ToolExecutionResult(request.action_id, True, {"value": ordinal})

        async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
            del request

    executor = SharedExecutor()
    resource = f"host-cross-loop-{asyncio_debug}"
    baseline = process_resource_diagnostics().registered_resources
    first = HostRuntime(RuntimeAssembly(InMemoryRunJournal(), executor))
    second = HostRuntime(RuntimeAssembly(InMemoryRunJournal(), executor))
    results: dict[str, object] = {}
    first_finished = Event()
    try:
        async with anyio.create_task_group() as group:

            async def run_first() -> None:
                results["first"] = await first.run_async(
                    _mutex_host_workflow(reference, resource), {}, run_id="host-first"
                )
                first_finished.set()

            async def run_second() -> None:
                results["second"] = await second.run_async(
                    _mutex_host_workflow(reference, resource), {}, run_id="host-second"
                )

            group.start_soon(run_first)
            assert await anyio.to_thread.run_sync(first_started.wait, 1)
            group.start_soon(run_second)
            with anyio.fail_after(3):
                while True:
                    queued = process_resource_diagnostics()
                    if queued.waiters == 1:
                        break
                    await anyio.sleep(0.005)
            assert executor.calls == 1
            assert queued.registered_resources == baseline + 1
            assert queued.holders == 1
            assert queued.waiters == 1
            first_release.set()
            assert await anyio.to_thread.run_sync(second_started.wait, 1)
            assert await anyio.to_thread.run_sync(first_finished.wait, 1)
            report = await anyio.to_thread.run_sync(
                lambda: first.close(mode="wait", timeout_seconds=1)
            )
            assert report.closed is True
            assert executor.active == 1
            second_release.set()
    finally:
        first_release.set()
        second_release.set()
        if first.diagnostics().state != "closed":
            first.close(mode="wait")
        second.close(mode="wait")

    assert executor.calls == 2
    assert executor.peak == 1
    assert results["first"].status == "SUCCEEDED"  # type: ignore[index,union-attr]
    assert results["second"].status == "SUCCEEDED"  # type: ignore[index,union-attr]
    diagnostics = process_resource_diagnostics()
    assert diagnostics.registered_resources == baseline
    assert diagnostics.holders == 0
    assert diagnostics.waiters == 0


def test_close_aggregates_cleanup_failures_and_retry_finishes() -> None:
    calls: list[str] = []

    class Good:
        def close(self) -> None:
            calls.append("good")

    class Flaky:
        attempts = 0

        def close(self) -> None:
            self.attempts += 1
            calls.append("flaky")
            if self.attempts == 1:
                raise RuntimeError("injected cleanup failure")

    flaky = Flaky()
    host = HostRuntime(
        RuntimeAssembly(
            InMemoryRunJournal(),
            _HeldToolExecutor(),
            closeables=(Good(), flaky),
        )
    )

    with pytest.raises(HostRuntimeCleanupError) as captured:
        host.close(mode="wait", timeout_seconds=1)
    report = captured.value.report
    assert report.execution_stopped is True
    assert report.closed is False
    assert report.resources_complete is False
    assert report.unfinished_resources == (1,)
    assert tuple(item.index for item in report.failures) == (1,)
    assert calls == ["flaky", "good"]
    diagnostics = host.diagnostics()
    assert diagnostics.state == "closing"
    assert diagnostics.incomplete_cleanups == 1

    completed = host.close(mode="wait", timeout_seconds=1)
    assert completed.closed is True
    assert completed.resources_complete is True
    assert completed.failures == ()
    assert calls == ["flaky", "good", "flaky"]
    assert host.close(mode="wait") == completed


def test_close_timeout_covers_async_cleanup_without_claiming_stop() -> None:
    class Slow:
        finished = Event()

        async def aclose(self) -> None:
            await anyio.sleep(0.2)
            self.finished.set()

    slow = Slow()
    host = HostRuntime(
        RuntimeAssembly(InMemoryRunJournal(), _HeldToolExecutor(), closeables=(slow,))
    )
    started = time.monotonic()
    with pytest.raises(HostRuntimeCloseTimeoutError) as captured:
        host.close(mode="wait", timeout_seconds=0.02)
    elapsed = time.monotonic() - started
    assert elapsed < 0.15
    report = captured.value.report
    assert report.execution_stopped is True
    assert report.resources_complete is False
    assert report.closed is False
    assert report.unfinished_resources == (0,)
    assert host.diagnostics().incomplete_cleanups == 1

    assert slow.finished.wait(timeout=1)
    completed = host.close(mode="wait", timeout_seconds=1)
    assert completed.closed is True
    assert completed.resources_complete is True


def test_close_timeout_does_not_duplicate_or_abandon_sync_cleanup() -> None:
    class SlowSync:
        started = Event()
        release = Event()
        finished = Event()
        calls = 0

        def close(self) -> None:
            self.calls += 1
            self.started.set()
            assert self.release.wait(timeout=2)
            self.finished.set()

    slow = SlowSync()
    host = HostRuntime(
        RuntimeAssembly(InMemoryRunJournal(), _HeldToolExecutor(), closeables=(slow,))
    )
    with pytest.raises(HostRuntimeCloseTimeoutError) as first_timeout:
        host.close(mode="wait", timeout_seconds=0.02)
    assert slow.started.wait(timeout=1)
    assert first_timeout.value.report.execution_stopped is True
    assert first_timeout.value.report.resources_complete is False

    with pytest.raises(HostRuntimeCloseTimeoutError):
        host.close(mode="wait", timeout_seconds=0.02)
    assert slow.calls == 1
    assert slow.finished.is_set() is False
    assert host.diagnostics().incomplete_cleanups == 1

    slow.release.set()
    assert slow.finished.wait(timeout=1)
    completed = host.close(mode="wait", timeout_seconds=1)
    assert completed.closed is True
    assert completed.resources_complete is True
    assert slow.calls == 1


def test_repeated_hosts_reclaim_idle_process_resource_entries() -> None:
    reference = ToolReference("host.reclaim", "1", "sha256:" + "d" * 64)
    baseline = process_resource_diagnostics().registered_resources

    class Immediate:
        async def execute_async(self, request, cancellation):  # type: ignore[no-untyped-def]
            del cancellation
            return ToolExecutionResult(request.action_id, True, {"value": 1})

        async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
            del request

    for index in range(25):
        host = HostRuntime(RuntimeAssembly(InMemoryRunJournal(), Immediate()))
        result = host.run(
            _mutex_host_workflow(reference, f"host-reclaim-{index}"),
            {},
            run_id=f"host-reclaim-run-{index}",
        )
        assert result.status == "SUCCEEDED"
        report = host.close(mode="wait")
        assert report.closed is True
        diagnostics = process_resource_diagnostics()
        assert diagnostics.registered_resources == baseline
        assert diagnostics.holders == 0
        assert diagnostics.waiters == 0
