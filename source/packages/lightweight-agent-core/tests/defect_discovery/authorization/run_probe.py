"""Targeted approval/revocation probes using the production Engine and Host."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from threading import Event, Lock
from typing import Any

import anyio
from platform_lightweight_agent import (
    ActionAuthorization,
    ApprovalDecision,
    HostRuntime,
    HostRuntimeLimits,
    InMemoryRunJournal,
    NodeContract,
    RuntimeAssembly,
    ToolExecutionResult,
    ToolRecoveryCapabilities,
    ToolReference,
    ValueBinding,
    WorkflowEngine,
    WorkflowRunError,
    tool_step,
    workflow,
)


TOOL = ToolReference("probe.authorization", "1", "sha256:" + "b" * 64)
INPUT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"hold": {"type": "boolean"}},
    "required": ["hold"],
    "additionalProperties": False,
}
OUTPUT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"value": {"type": "integer"}},
    "required": ["value"],
    "additionalProperties": False,
}


def _spec(name: str, *, hold: bool, approval: bool):  # type: ignore[no-untyped-def]
    return workflow(
        name,
        "1",
        (
            tool_step(
                "effect",
                TOOL,
                approval_required=approval,
                contract=NodeContract(
                    INPUT,
                    OUTPUT,
                    {"/hold": ValueBinding("literal", value=hold)},
                ),
                capabilities=ToolRecoveryCapabilities(external_side_effects=True),
            ),
        ),
        schema_version="v6-light-workflow-v4",
        input_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        output_schema=OUTPUT,
        output_bindings={
            "/value": ValueBinding("step_output", "/value", "effect")
        },
    )


class Policy:
    def __init__(self) -> None:
        self.allowed = True
        self.calls: list[dict[str, str]] = []
        self.lock = Lock()

    def authorize(
        self, request: Any, *, run_id: str, workflow: Any, step_id: str
    ) -> ActionAuthorization:
        del request, workflow
        with self.lock:
            self.calls.append({"run_id": run_id, "step_id": step_id})
            allowed = self.allowed
        return (
            ActionAuthorization(True)
            if allowed
            else ActionAuthorization(False, "POLICY_REVOKED")
        )


class Executor:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.calls: list[str] = []
        self.effects: list[str] = []
        self.lock = Lock()

    async def execute_async(self, request: Any, cancellation: Any) -> ToolExecutionResult:
        del cancellation
        with self.lock:
            self.calls.append(request.action_id)
            self.effects.append(request.action_id)
        if request.arguments["hold"]:
            self.started.set()
            await anyio.to_thread.run_sync(self.release.wait)
        return ToolExecutionResult(request.action_id, True, {"value": 1})

    async def reconcile_async(self, request: Any) -> None:
        del request
        return None


def _result(
    name: str,
    snapshot: Any,
    executor: Executor,
    policy: Policy,
    *,
    expected_calls: int,
    approval_id: str,
) -> dict[str, object]:
    action_ids = sorted(snapshot.state["actions"])
    requirements = {
        "blocked_after_revocation": snapshot.status == "BLOCKED",
        "stable_denial_code": snapshot.state.get("failure", {}).get("code")
        == "ACTION_DENIED",
        "tool_entry_count": len(executor.calls) == expected_calls,
        "effect_count": len(executor.effects) == expected_calls,
    }
    return {
        "case": "A03",
        "variant": name,
        "contract_result": "PASS" if all(requirements.values()) else "FAIL",
        "requirements": requirements,
        "approval_id": approval_id,
        "status": snapshot.status,
        "failure": snapshot.state.get("failure"),
        "action_ids": action_ids,
        "tool_calls": list(executor.calls),
        "effects": list(executor.effects),
        "authorization_checks": list(policy.calls),
    }


async def _direct_variants() -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for name, persist_first in (
        ("revoked_before_approval", False),
        ("revoked_after_approval_persisted", True),
    ):
        executor, policy = Executor(), Policy()
        engine = WorkflowEngine(InMemoryRunJournal(), executor, {}, policy=policy)
        waiting = await engine.run_async(
            _spec(name, hold=False, approval=True), {}, run_id=name
        )
        approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))
        if persist_first:
            saved = await engine.resume_async(
                name,
                approvals=(ApprovalDecision(approval_id, True),),
                advance=False,
            )
            assert saved.state["v3"]["approval_decisions"][approval_id] is True
        policy.allowed = False
        if persist_first:
            completed = await engine.resume_async(name)
        else:
            completed = await engine.resume_async(
                name, approvals=(ApprovalDecision(approval_id, True),)
            )
        results.append(
            _result(
                name,
                completed,
                executor,
                policy,
                expected_calls=0,
                approval_id=approval_id,
            )
        )
    return results


async def _queued_variant(*, revoke: bool) -> dict[str, object]:
    executor, policy = Executor(), Policy()
    journal = InMemoryRunJournal()
    host = HostRuntime(
        RuntimeAssembly(journal, executor, policy=policy),
        limits=HostRuntimeLimits(
            max_active_runs=2,
            max_queued_runs=2,
            max_tool_calls=1,
            max_model_requests=1,
        ),
    )
    outcome: dict[str, Any] = {}
    name = "revoked_after_approval_while_tool_queued" if revoke else "authorized_control"
    approval_id = ""
    try:
        async with anyio.create_task_group() as group:

            async def hold_capacity() -> None:
                outcome["holder"] = await host.run_async(
                    _spec("holder", hold=True, approval=False), {}, run_id="holder"
                )

            async def run_approved() -> None:
                outcome["candidate"] = await host.resume_async("candidate")

            group.start_soon(hold_capacity)
            assert await anyio.to_thread.run_sync(executor.started.wait, 5)
            waiting = await host.run_async(
                _spec("candidate", hold=False, approval=True), {}, run_id="candidate"
            )
            approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))
            # Record the control fact without driving execution.  A separate
            # Engine over the same real journal represents a control-plane
            # request; the Host remains the only execution owner.
            controller = WorkflowEngine(journal, executor, {}, policy=policy)
            saved = await controller.resume_async(
                "candidate",
                approvals=(ApprovalDecision(approval_id, True),),
                advance=False,
            )
            assert saved.state["v3"]["approval_decisions"][approval_id] is True
            group.start_soon(run_approved)
            with anyio.fail_after(5):
                while True:
                    current = host.get("candidate")
                    actions = current.state["actions"]
                    if actions and next(iter(actions.values()))["status"] == "READY":
                        break
                    await anyio.lowlevel.checkpoint()
            policy.allowed = not revoke
            executor.release.set()
    finally:
        executor.release.set()
        host.close(mode="wait")
    completed = outcome["candidate"]
    expected_calls = 1 if revoke else 2
    if revoke:
        return _result(
            name,
            completed,
            executor,
            policy,
            expected_calls=expected_calls,
            approval_id=approval_id,
        )
    requirements = {
        "positive_control_succeeded": completed.status == "SUCCEEDED",
        "both_tools_entered": len(executor.calls) == expected_calls,
        "both_effects_committed": len(executor.effects) == expected_calls,
    }
    return {
        "case": "A03",
        "variant": name,
        "contract_result": "PASS" if all(requirements.values()) else "FAIL",
        "requirements": requirements,
        "approval_id": approval_id,
        "status": completed.status,
        "action_ids": sorted(completed.state["actions"]),
        "tool_calls": list(executor.calls),
        "effects": list(executor.effects),
        "authorization_checks": list(policy.calls),
    }


async def _approval_identity_variant() -> dict[str, object]:
    """Exercise replay, conflicting decisions, and cross-run identities."""

    executor, policy = Executor(), Policy()
    engine = WorkflowEngine(InMemoryRunJournal(), executor, {}, policy=policy)
    first = await engine.run_async(
        _spec("approval-identity-a", hold=False, approval=True),
        {},
        run_id="approval-identity-a",
    )
    second = await engine.run_async(
        _spec("approval-identity-b", hold=False, approval=True),
        {},
        run_id="approval-identity-b",
    )
    first_id = next(iter(first.state["v3"]["pending_approvals"]))
    second_id = next(iter(second.state["v3"]["pending_approvals"]))
    accepted = await engine.resume_async(
        first.run_id,
        approvals=(ApprovalDecision(first_id, True),),
        advance=False,
    )
    replayed = await engine.resume_async(
        first.run_id,
        approvals=(ApprovalDecision(first_id, True),),
        advance=False,
    )
    conflicting_error: str | None = None
    try:
        await engine.resume_async(
            first.run_id,
            approvals=(ApprovalDecision(first_id, False),),
            advance=False,
        )
    except WorkflowRunError as error:
        conflicting_error = str(error)
    foreign_error: str | None = None
    try:
        await engine.resume_async(
            first.run_id,
            approvals=(ApprovalDecision(second_id, True),),
            advance=False,
        )
    except WorkflowRunError as error:
        foreign_error = str(error)
    before_drive = engine.get(first.run_id)
    completed = await engine.resume_async(first.run_id)
    untouched = engine.get(second.run_id)
    requirements = {
        "same_decision_is_idempotent": replayed.revision == accepted.revision,
        "opposite_decision_is_rejected": conflicting_error is not None,
        "cross_run_identity_is_rejected": foreign_error is not None,
        "first_decision_remains_authoritative": before_drive.state["v3"][
            "approval_decisions"
        ][first_id]
        is True,
        "approved_action_executes_once": completed.status == "SUCCEEDED"
        and len(executor.calls) == 1,
        "other_run_does_not_advance": untouched.status == "WAITING_APPROVAL",
    }
    return {
        "case": "A02",
        "variant": "idempotent_conflicting_and_cross_run_decisions",
        "contract_result": "PASS" if all(requirements.values()) else "FAIL",
        "requirements": requirements,
        "approval_ids": {"first": first_id, "second": second_id},
        "revisions": {
            "accepted": accepted.revision,
            "replayed": replayed.revision,
            "before_drive": before_drive.revision,
            "completed": completed.revision,
        },
        "errors": {
            "conflicting": conflicting_error,
            "foreign": foreign_error,
        },
        "tool_calls": list(executor.calls),
    }


async def run() -> dict[str, object]:
    results = await _direct_variants()
    results.append(await _queued_variant(revoke=True))
    results.append(await _queued_variant(revoke=False))
    results.append(await _approval_identity_variant())
    return {
        "schema_version": "v6-authorization-defect-evidence-v1",
        "result": (
            "FAIL"
            if any(item["contract_result"] == "FAIL" for item in results)
            else "PASS"
        ),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = anyio.run(run)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
