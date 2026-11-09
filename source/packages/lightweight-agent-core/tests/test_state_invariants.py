from __future__ import annotations

# Hypothesis is intentionally optional and must be skipped before importing its
# symbols, so these imports cannot appear in the ordinary top-level group.
import copy
import tempfile
from pathlib import Path

import pytest
from platform_lightweight_agent import (
    ApprovalDecision,
    FileWorkspace,
    NodeContract,
    RunSnapshot,
    SQLiteRunJournal,
    ToolExecutionResult,
    ToolExecutionUncertainError,
    ToolReference,
    ValueBinding,
    WorkflowEngine,
    WorkflowValidationError,
    approval_step,
    create_local_recovery_package,
    preview_local_artifact_retention,
    restore_local_recovery_package,
    tool_step,
    workflow,
)
from platform_lightweight_agent.workflow_transitions import (
    classify_action_settlement,
    merge_cancel_intent,
)

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import settings  # noqa: E402 - optional dependency guard above
from hypothesis import strategies as st  # noqa: E402 - optional dependency guard above
from hypothesis.stateful import (  # noqa: E402 - optional dependency guard above
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
)


class RecoveryStateMachine(RuleBasedStateMachine):
    """Generate restart/control/result interleavings around one action."""

    def __init__(self) -> None:
        super().__init__()
        self.status = "DISPATCHED"
        self.result = None
        self.first_confirmed = None
        self.cancel_requested = False
        self.terminal = False

    @rule(value=st.integers(min_value=-10, max_value=10))
    def executor_or_reconcile_result(self, value: int) -> None:
        candidate = {
            "action_id": "action-1",
            "succeeded": True,
            "output": {"value": value},
            "error": None,
        }
        if self.terminal:
            before = copy.deepcopy(self.result)
            assert self.result == before
            return
        transition = classify_action_settlement(self.status, self.result, candidate)
        if transition.disposition == "accept":
            self.status = "RESOLVED"
            self.result = dict(transition.result or {})
            self.first_confirmed = copy.deepcopy(self.result)
        elif transition.disposition == "idempotent":
            assert transition.result == self.result
        elif transition.disposition == "conflict":
            assert transition.result == self.first_confirmed
        else:  # pragma: no cover - generated states stay open or resolved
            assert False, transition

    @rule()
    def request_cancel(self) -> None:
        self.cancel_requested = merge_cancel_intent(self.cancel_requested)

    @rule()
    def restart(self) -> None:
        self.result = copy.deepcopy(self.result)
        self.cancel_requested = bool(self.cancel_requested)

    @rule()
    def finish(self) -> None:
        self.terminal = True

    @invariant()
    def confirmed_result_is_immutable(self) -> None:
        if self.first_confirmed is not None:
            assert self.result == self.first_confirmed

    @invariant()
    def cancellation_is_monotonic(self) -> None:
        if self.cancel_requested:
            assert merge_cancel_intent(self.cancel_requested, False) is True


TestRecoveryStateMachine = RecoveryStateMachine.TestCase


def _schema(properties, required=()):  # type: ignore[no-untyped-def]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_REAL_TOOL = ToolReference("stateful.write", "1", "sha256:" + "e" * 64)


class _ExternalLedger:
    def __init__(self) -> None:
        self.execute_calls = 0
        self.reconcile_calls = 0
        self.results: dict[str, ToolExecutionResult] = {}


class _UncertainExecutor:
    def __init__(self, ledger: _ExternalLedger) -> None:
        self.ledger = ledger

    async def execute_async(self, request, cancellation):  # type: ignore[no-untyped-def]
        del cancellation
        self.ledger.execute_calls += 1
        self.ledger.results[request.action_id] = ToolExecutionResult(
            request.action_id, True, {"value": request.arguments["value"]}
        )
        raise ToolExecutionUncertainError("persisted side effect, missing receipt")

    async def reconcile_async(self, request):  # type: ignore[no-untyped-def]
        self.ledger.reconcile_calls += 1
        return self.ledger.results.get(request.action_id)


def _real_workflow():  # type: ignore[no-untyped-def]
    empty = _schema({})
    approved = _schema({"approved": {"type": "boolean"}}, ("approved",))
    value = _schema({"value": {"type": "integer"}}, ("value",))
    return workflow(
        "stateful-real-engine",
        "1",
        (
            approval_step(
                "review",
                "Continue?",
                contract=NodeContract(empty, approved, {}),
            ),
            tool_step(
                "write",
                _REAL_TOOL,
                approval_required=False,
                contract=NodeContract(
                    value,
                    value,
                    {"/value": ValueBinding("literal", value=3)},
                ),
            ),
        ),
        schema_version="v6-light-workflow-v3",
        input_schema=empty,
        output_schema=value,
        output_bindings={"/value": ValueBinding("step_output", "/value", "write")},
    )


class RealSQLiteRecoveryStateMachine(RuleBasedStateMachine):
    """Compare a small reference model with real Engine/SQLite restarts."""

    def __init__(self) -> None:
        super().__init__()
        self.temporary = tempfile.TemporaryDirectory(prefix="v6-stateful-")
        self.path = Path(self.temporary.name) / "runs.sqlite"
        self.ledger = _ExternalLedger()
        self.run_id = "stateful-run"
        self.started = False
        self.cancel_seen = False
        self.terminal_status: str | None = None
        self.confirmed_results: dict[str, dict] = {}
        self._restart_engine()

    def teardown(self) -> None:
        self.temporary.cleanup()

    def _restart_engine(self) -> None:
        self.journal = SQLiteRunJournal(self.path)
        self.engine = WorkflowEngine(self.journal, _UncertainExecutor(self.ledger), {})

    def _current(self):  # type: ignore[no-untyped-def]
        return self.journal.get(self.run_id)

    def _is_terminal(self) -> bool:
        return self.started and self._current().status in {
            "SUCCEEDED",
            "BLOCKED",
            "CANCELLED",
        }

    def _pending(self) -> bool:
        if not self.started or self._is_terminal():
            return False
        return bool(self._current().state["v3"]["pending_approvals"])

    def _unknown_action(self) -> str | None:
        if not self.started or self._is_terminal():
            return None
        for action_id, action in self._current().state["actions"].items():
            if action["status"] in {"DISPATCHED", "RECONCILING"}:
                return action_id
        return None

    @precondition(lambda self: not self.started)
    @rule()
    def start(self) -> None:
        snapshot = self.engine.run(_real_workflow(), {}, run_id=self.run_id)
        assert snapshot.status == "WAITING_APPROVAL"
        self.started = True

    @precondition(lambda self: self.started)
    @rule()
    def restart(self) -> None:
        before = self._current().to_mapping()
        self._restart_engine()
        assert self._current().to_mapping() == before

    @precondition(lambda self: self._pending())
    @rule(approved=st.booleans())
    def decide_approval(self, approved: bool) -> None:
        current = self._current()
        approval_id = next(iter(current.state["v3"]["pending_approvals"]))
        self.engine.resume(
            self.run_id,
            approvals=(ApprovalDecision(approval_id, approved),),
        )

    @precondition(lambda self: self.started and not self._is_terminal())
    @rule()
    def request_cancel(self) -> None:
        snapshot = self.engine.request_cancel(self.run_id, reason="stateful request")
        self.cancel_seen = True
        assert snapshot.state["v3"]["cancel_requested"] is True

    @precondition(lambda self: self.started and not self._is_terminal())
    @rule()
    def resume(self) -> None:
        self.engine.resume(self.run_id)

    @precondition(lambda self: self._unknown_action() is not None)
    @rule()
    def reconcile(self) -> None:
        action_id = self._unknown_action()
        assert action_id is not None
        result = self.ledger.results[action_id]
        self.engine.reconcile(self.run_id, action_id, result)

    @invariant()
    def persisted_state_matches_reference_invariants(self) -> None:
        if not self.started:
            return
        snapshot = self._current()
        if self.cancel_seen:
            assert snapshot.state["v3"]["cancel_requested"] is True
        if self.terminal_status is None and snapshot.status in {
            "SUCCEEDED",
            "BLOCKED",
            "CANCELLED",
        }:
            self.terminal_status = snapshot.status
        if self.terminal_status is not None:
            assert snapshot.status == self.terminal_status
        for action_id, action in snapshot.state["actions"].items():
            if action["status"] == "RESOLVED":
                result = copy.deepcopy(action["result"])
                self.confirmed_results.setdefault(action_id, result)
                assert result == self.confirmed_results[action_id]
        assert self.ledger.execute_calls <= 1


TestRealSQLiteRecoveryStateMachine = RealSQLiteRecoveryStateMachine.TestCase
TestRealSQLiteRecoveryStateMachine.settings = settings(
    max_examples=25,
    stateful_step_count=20,
    deadline=None,
)


class LocalRecoveryFileStateMachine(RuleBasedStateMachine):
    """Compose input retention, package creation, restore and overwrite refusal."""

    def __init__(self) -> None:
        super().__init__()
        self.temporary = tempfile.TemporaryDirectory(prefix="v6-file-stateful-")
        self.root = Path(self.temporary.name)
        self.workspace = FileWorkspace.under(self.root / "files")
        source = self.root / "source.txt"
        source.write_text("stateful input", encoding="utf-8")
        self.reference = self.workspace.stage_input(source, "input.txt")
        self.journal = SQLiteRunJournal(self.root / "runs.sqlite")
        self.journal.create(
            RunSnapshot(
                run_id="file-stateful",
                workflow={
                    "schema_version": "v6-light-workflow-v1",
                    "workflow_id": "file-stateful",
                    "version": "1",
                    "steps": [],
                },
                inputs={"file": self.reference.to_mapping()},
                state={"values": {}},
                status="WAITING_APPROVAL",
                driver_bindings={},
            ),
            "RUN_STARTED",
            {},
        )
        self.package: Path | None = None
        self.restored: list[tuple[SQLiteRunJournal, FileWorkspace, Path]] = []

    def teardown(self) -> None:
        self.temporary.cleanup()

    @rule()
    def preview_retention(self) -> None:
        preview = preview_local_artifact_retention(self.journal, self.workspace)
        assert self.reference in preview.protected

    @rule()
    def create_or_validate_backup(self) -> None:
        if self.package is None:
            self.package = create_local_recovery_package(
                self.journal, self.workspace, self.root / "recovery.zip"
            )
        assert self.package.is_file()

    @precondition(lambda self: self.package is not None)
    @rule()
    def restore_to_new_directory(self) -> None:
        assert self.package is not None
        destination = self.root / f"restored-{len(self.restored)}"
        journal, workspace = restore_local_recovery_package(
            self.package, destination
        )
        assert workspace.resolve_input(self.reference).read_text(encoding="utf-8") == (
            "stateful input"
        )
        self.restored.append((journal, workspace, destination))

    @precondition(lambda self: bool(self.restored) and self.package is not None)
    @rule()
    def refuse_overwrite(self) -> None:
        assert self.package is not None
        _journal, workspace, destination = self.restored[-1]
        before = workspace.resolve_input(self.reference).read_bytes()
        with pytest.raises(WorkflowValidationError, match="OVERWRITE_UNSAFE"):
            restore_local_recovery_package(
                self.package, destination, overwrite=True
            )
        assert workspace.resolve_input(self.reference).read_bytes() == before

    @invariant()
    def original_input_is_always_available(self) -> None:
        assert self.workspace.resolve_input(self.reference).is_file()


TestLocalRecoveryFileStateMachine = LocalRecoveryFileStateMachine.TestCase
TestLocalRecoveryFileStateMachine.settings = settings(
    max_examples=15,
    stateful_step_count=12,
    deadline=None,
)


def test_restart_lost_receipt_cancel_and_late_result_share_one_action(
    tmp_path: Path,
) -> None:
    """Exercise the highest-risk controls as one persisted interleaving."""

    path = tmp_path / "combined-recovery.sqlite"
    ledger = _ExternalLedger()
    first = WorkflowEngine(SQLiteRunJournal(path), _UncertainExecutor(ledger), {})
    waiting = first.run(_real_workflow(), {}, run_id="combined-run")
    approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))

    unknown = first.resume(
        waiting.run_id, approvals=(ApprovalDecision(approval_id, True),)
    )
    assert unknown.status == "RECONCILING"
    action_id = next(iter(unknown.state["actions"]))
    assert ledger.execute_calls == 1

    # Recreate all process-local state, persist a cancellation intent while the
    # external receipt is missing, then deliver that receipt late.  The action
    # is never dispatched again and unknown-result priority is retained until
    # the exact result is settled.
    restarted = WorkflowEngine(
        SQLiteRunJournal(path), _UncertainExecutor(ledger), {}
    )
    cancelled = restarted.request_cancel(unknown.run_id, reason="operator stop")
    assert cancelled.state["v3"]["cancel_requested"] is True
    assert ledger.execute_calls == 1

    settled = restarted.reconcile(
        unknown.run_id, action_id, ledger.results[action_id]
    )
    assert settled.status == "CANCELLED"
    assert settled.state["actions"][action_id]["status"] == "RESOLVED"
    assert ledger.execute_calls == 1

    final_restart = WorkflowEngine(
        SQLiteRunJournal(path), _UncertainExecutor(ledger), {}
    )
    assert final_restart.resume(unknown.run_id).to_mapping() == settled.to_mapping()
    assert ledger.execute_calls == 1
