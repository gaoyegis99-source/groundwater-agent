"""Pure projections of authoritative task and execution context."""

from __future__ import annotations

from .context_models import ContextBlockV1, TokenEstimator
from .context_values import _dicts, _integer, _mapping


def compact_task_context(value: dict[str, object] | None) -> dict[str, object] | None:
    if value is None:
        return None
    spec = _mapping(value.get("spec"))
    progress = _mapping(value.get("progress"))
    if not spec:
        return dict(value)
    return {
        "task_id": spec.get("task_id"),
        "revision": spec.get("revision"),
        "objective": spec.get("objective"),
        "constraints": spec.get("constraints", []),
        "unresolved_items": spec.get("unresolved_items", []),
        "state": progress.get("state"),
        "blocking_reasons": progress.get("blocking_reasons", []),
    }


def project_active_preparation(
    value: dict[str, object] | None,
) -> dict[str, object] | None:
    if value is None or value.get("fresh_for_current_turn") is not True:
        return None
    return {
        key: value.get(key)
        for key in (
            "schemaVersion", "preparation_id", "revision", "goal_revision",
            "task_id", "base_task_revision", "status", "entries",
            "fresh_for_current_turn", "disposition",
        )
    }


def project_active_task(value: dict[str, object] | None) -> dict[str, object] | None:
    if value is None:
        return None
    spec = _mapping(value.get("spec"))
    progress = _mapping(value.get("progress"))
    checks = {
        item.get("id"): item
        for item in _dicts(progress.get("criteria"))
        if isinstance(item.get("id"), str)
    }
    step_checks = {
        item.get("id"): item
        for item in _dicts(progress.get("steps"))
        if isinstance(item.get("id"), str)
    }
    criteria = _dicts(spec.get("criteria"))
    steps = _dicts(spec.get("steps"))
    active_criteria = [
        item for item in criteria
        if not bool(_mapping(checks.get(item.get("id"))).get("satisfied"))
    ]
    completed_criteria = [
        item.get("id") for item in criteria
        if bool(_mapping(checks.get(item.get("id"))).get("satisfied"))
    ]
    active_steps = [
        item for item in steps
        if not bool(_mapping(step_checks.get(item.get("id"))).get("complete"))
    ]
    completed_steps = [
        item.get("id") for item in steps
        if bool(_mapping(step_checks.get(item.get("id"))).get("complete"))
    ]
    evidence = [
        check.get("evidence")
        for check in checks.values()
        if isinstance(check.get("evidence"), dict)
    ]
    return {
        "schemaVersion": "task-active-context.v1",
        "task_id": spec.get("task_id"),
        "revision": spec.get("revision"),
        "objective": spec.get("objective"),
        "source_goal_revision": spec.get("source_goal_revision"),
        "constraints": spec.get("constraints", []),
        "criteria": active_criteria,
        "unresolved_items": spec.get("unresolved_items", []),
        "steps": active_steps,
        "state": progress.get("state"),
        "blocking_reasons": progress.get("blocking_reasons", []),
        "evidence": evidence,
        "results": progress.get("results", []),
        "completed": {
            "criteria": completed_criteria,
            "steps": completed_steps,
        },
    }


def project_execution_context(
    actions: tuple[dict[str, object], ...],
    task: dict[str, object] | None,
    *,
    estimator: TokenEstimator,
) -> tuple[dict[str, object], list[ContextBlockV1]]:
    referenced_runs: set[str] = set()
    referenced_versions: set[str] = set()
    if task is not None:
        for item in _dicts(task.get("evidence")):
            run_id = item.get("workflow_run_id")
            if isinstance(run_id, str):
                referenced_runs.add(run_id)
        for item in _dicts(task.get("results")):
            version_id = item.get("version_id")
            if isinstance(version_id, str):
                referenced_versions.add(version_id)

    pinned: list[dict[str, object]] = []
    retrievable: list[dict[str, object]] = []
    blocks: list[ContextBlockV1] = []
    for index, action in enumerate(actions):
        status = action.get("status")
        task_start = None if task is None else _integer(
            task.get("source_goal_revision")
        )
        action_revision = _integer(action.get("goal_revision"))
        outputs = _dicts(action.get("outputs"))
        output_versions = {
            value.get("version_id") for value in outputs
            if isinstance(value.get("version_id"), str)
        }
        must_pin = (
            status in {"PENDING", "QUEUED", "RUNNING"}
            or (
                status == "FAILED"
                and task_start is not None
                and action_revision is not None
                and action_revision >= task_start
            )
            or action.get("workflow_run_id") in referenced_runs
            or bool(output_versions & referenced_versions)
        )
        block = ContextBlockV1(
            block_id=f"agent:execution:{index}",
            kind="EXECUTION_ACTIVE" if must_pin else "EXECUTION_HISTORY",
            authority="KERNEL_FACT",
            retention="PINNED" if must_pin else "RETRIEVABLE",
            source_type="WORKFLOW_RUN",
            source_id=(
                action.get("workflow_run_id")
                if isinstance(action.get("workflow_run_id"), str) else None
            ),
            source_revision=_integer(action.get("goal_revision")),
            content=action,
            token_count=estimator.count(action),
            disposition="INCLUDED" if must_pin else "RETRIEVABLE",
            reason=None if must_pin else "HISTORICAL_SUCCESS",
        )
        blocks.append(block)
        (pinned if must_pin else retrievable).append(action)

    counts: dict[str, int] = {}
    revisions: list[int] = []
    recent_ids: list[str] = []
    for action in retrievable:
        status = str(action.get("status"))
        counts[status] = counts.get(status, 0) + 1
        revision = _integer(action.get("goal_revision"))
        if revision is not None:
            revisions.append(revision)
        run_id = action.get("workflow_run_id")
        if isinstance(run_id, str):
            recent_ids.append(run_id)
    index = {
        "count": len(retrievable),
        "status_counts": counts,
        "goal_revision_range": (
            [min(revisions), max(revisions)] if revisions else None
        ),
        "recent_workflow_run_ids": recent_ids[-20:],
        "retrieval_tools": ["list_execution_facts", "get_execution_fact"],
    }
    execution = {
        "schema": "ExecutionContextV1",
        "actions": pinned,
    }
    if retrievable:
        execution["retrievable"] = index
        blocks.append(ContextBlockV1(
            block_id="agent:execution:index",
            kind="EXECUTION_INDEX",
            authority="KERNEL_FACT",
            retention="PINNED",
            source_type="EXECUTION_INDEX",
            source_id=None,
            source_revision=max(revisions) if revisions else None,
            content=index,
            token_count=estimator.count(index),
        ))
    return execution, blocks
