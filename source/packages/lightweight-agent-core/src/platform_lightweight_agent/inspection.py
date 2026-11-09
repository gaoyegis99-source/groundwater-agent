"""Read-only, credential-safe explanations of durable workflow state.

The execution journal deliberately stores enough detail to resume a run, but
operators should not have to inspect a raw snapshot (which can be large) to
learn why it stopped.  This module derives compact views only; it never calls a
driver, executor, resolver, or journal mutation method.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .journal import BACKUP_RECOVERY_STATUS, RunInspection, RunSnapshot, RunViewSource, _snapshot_view_source
from .spec import JsonValue, ToolReference, WorkflowSpec, json_copy, workflow_semantics

RUN_EXPLANATION_SCHEMA_V1 = "v6-run-explanation-v1"
RECOVERY_PREVIEW_SCHEMA_V1 = "v6-recovery-preview-v1"

_UNKNOWN_ACTION_STATUSES = frozenset({"DISPATCHED", "RECONCILING"})
_TERMINAL_STATUSES = frozenset({"SUCCEEDED", "BLOCKED", "CANCELLED"})
_BINDING_FAILURE_CODES = frozenset(
    {
        "AGENT_DRIVER_UNAVAILABLE",
        "AGENT_DRIVER_BINDING_MISMATCH",
        "AGENT_DRIVER_ERROR",
        "TOOL_ROUTE_NOT_REGISTERED",
        "TOOL_REJECTED",
        "TOOL_RECONCILIATION_REJECTED",
    }
)


@dataclass(frozen=True)
class RunExplanation:
    """A compact explanation of a persisted run without business payloads."""

    run_id: str
    status: str
    workflow: Mapping[str, JsonValue]
    reason: Mapping[str, JsonValue]
    actions: tuple[Mapping[str, JsonValue], ...]
    pending_approvals: tuple[Mapping[str, JsonValue], ...]
    requirements: tuple[Mapping[str, JsonValue], ...]
    execution_id: str = ""
    revision: int = 1

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "schema_version": RUN_EXPLANATION_SCHEMA_V1,
            "effects": "not_executed",
            "run_id": self.run_id,
            "execution_id": self.execution_id,
            "revision": self.revision,
            "status": self.status,
            "workflow": json_copy(self.workflow),
            "reason": json_copy(self.reason),
            "actions": [json_copy(item) for item in self.actions],
            "pending_approvals": [json_copy(item) for item in self.pending_approvals],
            "requirements": [json_copy(item) for item in self.requirements],
        }


@dataclass(frozen=True)
class RecoveryPreview:
    """The next recovery operations inferred from state, never performed."""

    run_id: str
    status: str
    operations: tuple[Mapping[str, JsonValue], ...]
    blockers: tuple[Mapping[str, JsonValue], ...]
    execution_id: str = ""

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "schema_version": RECOVERY_PREVIEW_SCHEMA_V1,
            "effects": "not_executed",
            "run_id": self.run_id,
            "execution_id": self.execution_id,
            "status": self.status,
            "operations": [json_copy(item) for item in self.operations],
            "blockers": [json_copy(item) for item in self.blockers],
        }


def explain_run(snapshot: RunSnapshot | RunInspection) -> RunExplanation:
    """Explain a snapshot using only stable identifiers and protocol codes."""

    workflow = WorkflowSpec.from_mapping(snapshot.workflow)
    state = snapshot.state if isinstance(snapshot.state, Mapping) else {}
    actions = _action_summaries(state)
    pending_approvals = _pending_approval_summaries(state)
    failure = _failure_summary(state)
    reason = _reason(snapshot.status, state, failure, pending_approvals, actions)
    requirements = _requirements(snapshot, workflow, failure)
    return RunExplanation(
        snapshot.run_id,
        snapshot.status,
        {
            "workflow_id": workflow.workflow_id,
            "version": workflow.version,
            "schema_version": workflow.schema_version,
            "definition_digest": workflow.definition_digest,
            "semantics": workflow_semantics(workflow).to_mapping(),
        },
        reason,
        tuple(actions),
        tuple(pending_approvals),
        tuple(requirements),
        snapshot.execution_id,
        snapshot.revision,
    )


@dataclass(frozen=True)
class RunView:
    """Operator view from one revision; not an execution or recovery input."""

    source: RunViewSource

    @property
    def run_id(self) -> str:
        return self.source.inspection.run_id

    @property
    def execution_id(self) -> str:
        return self.source.inspection.execution_id

    def to_mapping(self) -> dict[str, JsonValue]:
        current = self.source.inspection
        explanation = explain_run(current).to_mapping()
        v3 = current.state.get("v3", {})
        states = dict(v3.get("step_states", {})) if isinstance(v3, Mapping) else {}
        for step in current.state.get("completed", []):
            states[step] = "COMPLETED"
        failure = current.state.get("failure", {})
        if isinstance(failure, Mapping) and isinstance(failure.get("step_id"), str):
            states[failure["step_id"]] = "FAILED"
        output = json_copy(self.source.output)
        return {
            "schema_version": "v6-run-view-v1",
            "run_id": current.run_id,
            "execution_id": current.execution_id,
            "revision": current.revision,
            "execution_status": current.status,
            "business_result": _business_result(current, output),
            "output": output,
            "explanation": explanation,
            "progress": [{"step_id": key, "status": value} for key, value in sorted(states.items())],
        }


def _business_result(current: RunInspection, output: JsonValue) -> JsonValue:
    """Display the declared output convention, never infer business success.

    This convention is opt-in via required, typed output fields. It does not
    change the workflow engine's statuses, permissions, or result validation.
    """
    schema = current.workflow.get("output_schema", {})
    if current.status != "SUCCEEDED" or not isinstance(output, Mapping) or not isinstance(schema, Mapping):
        return None
    required = schema.get("required", [])
    properties = schema.get("properties", {})
    if not isinstance(required, list) or not {"business_status", "reason_code"} <= set(required) or not isinstance(properties, Mapping):
        return None
    declared = properties.get("business_status", {})
    allowed = declared.get("enum", []) if isinstance(declared, Mapping) else []
    reason_schema = properties.get("reason_code", {})
    reason_types = reason_schema.get("type", []) if isinstance(reason_schema, Mapping) else []
    if isinstance(reason_types, str):
        reason_types = [reason_types]
    if (not isinstance(allowed, list) or not allowed or not all(isinstance(value, str) for value in allowed)
        or not isinstance(reason_types, list) or not reason_types
        or not all(value in {"string", "null"} for value in reason_types)):
        return None
    status, reason = output.get("business_status"), output.get("reason_code")
    reason_type = "null" if reason is None else "string" if isinstance(reason, str) else None
    if not isinstance(status, str) or status not in allowed or reason_type not in reason_types:
        return None
    return {"status": status, "reason_code": reason}


def read_run_view(journal: Any, run_id: str, expected_execution_id: str | None) -> RunView:
    reader = getattr(journal, "view_source", None)
    options = {} if expected_execution_id is None else {"expected_execution_id": expected_execution_id}
    if callable(reader):
        return RunView(reader(run_id, **options))
    # Third-party journals may implement only the original snapshot contract.
    return RunView(_snapshot_view_source(journal.get(run_id, **options)))


def preview_recovery(snapshot: RunSnapshot | RunInspection) -> RecoveryPreview:
    """Preview recovery without invoking a model, tool, or reconciliation API."""

    explanation = explain_run(snapshot)
    unknown = [
        action
        for action in explanation.actions
        if action.get("status") in _UNKNOWN_ACTION_STATUSES
    ]
    operations: list[Mapping[str, JsonValue]] = []
    blockers: list[Mapping[str, JsonValue]] = []
    if snapshot.status == BACKUP_RECOVERY_STATUS:
        at_risk_action_ids = [
            str(action["action_id"])
            for action in explanation.actions
            if action.get("status") in {"READY", "DISPATCHED", "RECONCILING"}
            and isinstance(action.get("action_id"), str)
        ]
        operations.append(
            {
                "kind": "release_backup_recovery",
                "execution": "requires_source_owner_stop_confirmation",
                "effects": "not_executed",
                "strategies": ["query", "idempotent-redispatch"],
                "risk_code": "DUPLICATE_EFFECT_POSSIBLE",
                "at_risk_action_ids": at_risk_action_ids,
            }
        )
        blockers.append(
            {
                "code": "BACKUP_RECOVERY_REQUIRED",
                "risk_code": "DUPLICATE_EFFECT_POSSIBLE",
            }
        )
        return RecoveryPreview(
            snapshot.run_id,
            snapshot.status,
            tuple(operations),
            tuple(blockers),
            snapshot.execution_id,
        )
    if unknown:
        operations.extend(
            {
                "kind": "query_action_result",
                "action_id": action["action_id"],
                "step_id": action["step_id"],
                "execution": "never_redispatch",
                "effects": "reconciliation_query_only",
            }
            for action in unknown
        )
        blockers.append(
            {
                "code": "EXTERNAL_RESULT_UNKNOWN",
                "action_ids": [action["action_id"] for action in unknown],
            }
        )
    if explanation.pending_approvals:
        operations.extend(
            _approval_operation(item) for item in explanation.pending_approvals
        )
        blockers.append(
            {
                "code": "APPROVAL_REQUIRED",
                "approval_ids": [
                    item["approval_id"] for item in explanation.pending_approvals
                ],
            }
        )
    failure_code = explanation.reason.get("code")
    if snapshot.status in _TERMINAL_STATUSES:
        operations.append(
            {
                "kind": "none",
                "execution": "terminal_run",
                "effects": "not_executed",
            }
        )
    elif snapshot.status == "RECONCILING":
        # The unknown-action operations above are the entire safe action set.
        pass
    elif snapshot.status == "WAITING_APPROVAL":
        # A decision is durable control input; no execution is previewed here.
        pass
    elif failure_code is not None:
        blockers.append({"code": str(failure_code)})
    else:
        operations.append(
            {
                "kind": "resume_workflow",
                "execution": "may_dispatch_new_actions",
                "effects": "not_executed",
            }
        )
    return RecoveryPreview(
        snapshot.run_id,
        snapshot.status,
        tuple(operations),
        tuple(blockers),
        snapshot.execution_id,
    )


def _approval_operation(item: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    """Describe an approval without inventing a v3 identifier for legacy runs."""

    approval_id = item.get("approval_id")
    if isinstance(approval_id, str):
        return {
            "kind": "await_approval",
            "approval_id": approval_id,
            "step_id": item["step_id"],
            "execution": "requires_human_decision",
            "effects": "not_executed",
        }
    return {
        "kind": "await_legacy_approval",
        "step_id": item["step_id"],
        "execution": "resume_with_boolean_approval",
        "effects": "not_executed",
    }


def _action_summaries(state: Mapping[str, Any]) -> list[Mapping[str, JsonValue]]:
    raw_actions = state.get("actions")
    if not isinstance(raw_actions, Mapping):
        return []
    result: list[Mapping[str, JsonValue]] = []
    for action_id, raw in sorted(raw_actions.items(), key=lambda item: str(item[0])):
        if not isinstance(action_id, str) or not isinstance(raw, Mapping):
            continue
        request = raw.get("request")
        tool: Mapping[str, JsonValue] | None = None
        if isinstance(request, Mapping) and isinstance(request.get("tool"), Mapping):
            try:
                tool = ToolReference.from_mapping(request["tool"]).to_mapping()
            except (TypeError, ValueError):
                tool = None
        status = raw.get("status")
        result.append(
            {
                "action_id": action_id,
                "step_id": str(raw.get("step_key", raw.get("step_id", ""))),
                "status": str(status) if status is not None else "UNKNOWN",
                "tool": dict(tool) if tool is not None else None,
                "result_known": bool(raw.get("result") is not None),
                "recovery": _recovery_summary(raw.get("recovery")),
            }
        )
    return result


def _recovery_summary(value: object) -> Mapping[str, JsonValue] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, JsonValue] = {}
    for key in (
        "query_count",
        "latest_result",
        "last_query_at",
        "next_query_at",
        "latest_error_code",
    ):
        item = value.get(key)
        if isinstance(item, (str, int, float)) and not isinstance(item, bool):
            result[key] = item
    return result or None


def _pending_approval_summaries(
    state: Mapping[str, Any],
) -> list[Mapping[str, JsonValue]]:
    v3 = state.get("v3")
    pending = v3.get("pending_approvals") if isinstance(v3, Mapping) else None
    if isinstance(pending, Mapping):
        result: list[Mapping[str, JsonValue]] = []
        for approval_id, item in sorted(
            pending.items(), key=lambda value: str(value[0])
        ):
            if not isinstance(approval_id, str) or not isinstance(item, Mapping):
                continue
            result.append(
                {
                    "approval_id": approval_id,
                    "kind": str(item.get("kind", "UNKNOWN")),
                    "step_id": str(item.get("step_key", "")),
                    "action_id": (
                        str(item["action_id"])
                        if isinstance(item.get("action_id"), str)
                        else None
                    ),
                }
            )
        return result
    legacy = state.get("pending")
    if not isinstance(legacy, Mapping):
        return []
    # v1/v2 have no persistent approval id. State that fact explicitly rather
    # than inventing an unstable value in a diagnostic response.
    return [
        {
            "approval_id": None,
            "decision_protocol": "legacy_boolean_resume",
            "kind": str(legacy.get("kind", "LEGACY")),
            "step_id": str(legacy.get("step_id", "")),
            "action_id": (
                str(legacy["action_id"])
                if isinstance(legacy.get("action_id"), str)
                else None
            ),
        }
    ]


def _failure_summary(state: Mapping[str, Any]) -> Mapping[str, JsonValue] | None:
    failure = state.get("failure")
    if not isinstance(failure, Mapping):
        return None
    result: dict[str, JsonValue] = {}
    for key in ("code", "step_id", "action_id", "approval_id", "path", "keyword"):
        value = failure.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            result[key] = value
    return result or None


def _reason(
    status: str,
    state: Mapping[str, Any],
    failure: Mapping[str, JsonValue] | None,
    pending_approvals: list[Mapping[str, JsonValue]],
    actions: list[Mapping[str, JsonValue]],
) -> Mapping[str, JsonValue]:
    if status == BACKUP_RECOVERY_STATUS:
        return {"code": "BACKUP_RECOVERY_REQUIRED"}
    if any(action.get("status") in _UNKNOWN_ACTION_STATUSES for action in actions):
        return {"code": "EXTERNAL_RESULT_UNKNOWN"}
    if pending_approvals:
        return {"code": "APPROVAL_REQUIRED"}
    if failure is not None:
        return dict(failure)
    v3 = state.get("v3")
    if isinstance(v3, Mapping) and v3.get("cancel_requested") is True:
        return {"code": "CANCEL_REQUESTED"}
    if status == "SUCCEEDED":
        return {"code": "COMPLETED"}
    return {"code": "RUNNING"}


def _requirements(
    snapshot: RunSnapshot | RunInspection,
    workflow: WorkflowSpec,
    failure: Mapping[str, JsonValue] | None,
) -> list[Mapping[str, JsonValue]]:
    requirements: list[Mapping[str, JsonValue]] = [
        {
            "kind": "workflow_definition",
            "workflow_id": workflow.workflow_id,
            "version": workflow.version,
            "definition_digest": workflow.definition_digest,
            "state": "frozen",
        }
    ]
    for driver, version in sorted(snapshot.driver_bindings.items()):
        requirements.append(
            {
                "kind": "driver_binding",
                "driver": str(driver),
                "version": str(version),
                "state": "required",
            }
        )
    code = failure.get("code") if failure is not None else None
    if isinstance(code, str) and code in _BINDING_FAILURE_CODES:
        requirements.append(
            {
                "kind": "runtime_binding",
                "code": code,
                "state": "needs_attention",
            }
        )
    return requirements
