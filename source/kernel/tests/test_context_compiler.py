from __future__ import annotations

from dataclasses import replace
import json

import pytest

from platform_kernel.agent_runtime.context_policy import (
    ContextLimitError,
    ContextPolicyV1,
    ContextSourceSnapshotV1,
    ContextTurnV1,
)
from platform_kernel.agent_runtime.memory import O200kTokenEstimator
from platform_kernel.contracts import DataVersionRefV2


def _task(revision: int = 3) -> dict:
    return {
        "spec": {
            "task_id": "task-1",
            "revision": revision,
            "objective": "提交分析说明",
            "source_goal_revision": 1,
            "constraints": [{"id": "scope", "text": "公开资料"}],
            "criteria": [
                {"id": "reply", "text": "提交说明", "kind": "REPLY"},
                {"id": "review", "text": "业务可用", "kind": "USER_REVIEW"},
            ],
            "unresolved_items": ["缺少时间范围"],
            "steps": [{
                "id": "write", "title": "撰写", "depends_on": [],
                "criterion_ids": ["reply"], "gaps": ["时间范围"],
            }],
        },
        "progress": {
            "state": "WAITING_INPUT",
            "criteria": [
                {"id": "reply", "satisfied": True, "evidence": {"agent_run_id": "a1"}},
                {"id": "review", "satisfied": False, "evidence": None},
            ],
            "steps": [{"id": "write", "complete": False}],
            "results": [],
            "blocking_reasons": [],
        },
    }


def _snapshot(*, task_revision: int = 3) -> ContextSourceSnapshotV1:
    return ContextSourceSnapshotV1(
        session_id="session",
        goal_revision=12,
        message="继续完成说明",
        turns=tuple(
            ContextTurnV1(index, f"用户 {index}", f"助手 {index}")
            for index in range(1, 12)
        ),
        memory_summary={
            "goals": ["完成说明"], "constraints": [],
            "decisions": [], "unresolved_items": [],
        },
        memory_through_goal_revision=8,
        task_context=_task(task_revision),
        execution_actions=(
            {
                "goal_revision": 5, "tool": "old", "status": "COMPLETE",
                "workflow_run_id": "workflow-old", "outputs": [], "error": None,
            },
            {
                "goal_revision": 11, "tool": "active", "status": "RUNNING",
                "workflow_run_id": "workflow-active", "outputs": [], "error": None,
            },
        ),
        mode="DIALOGUE",
        source_watermark=12,
    )


def test_context_policy_is_deterministic_and_revision_changes_hash():
    policy = ContextPolicyV1(O200kTokenEstimator())
    snapshot = _snapshot()
    first = policy.route_view(
        snapshot, available_input_tokens=100_000, fixed_input_tokens=100,
    )
    second = policy.route_view(
        snapshot, available_input_tokens=100_000, fixed_input_tokens=100,
    )
    changed = policy.route_view(
        _snapshot(task_revision=4),
        available_input_tokens=100_000,
        fixed_input_tokens=100,
    )

    assert first == second
    assert json.dumps(first.manifest(), sort_keys=True) == json.dumps(
        second.manifest(), sort_keys=True
    )
    assert first.content_hash != changed.content_hash


def test_execution_watermark_changes_agent_manifest_hash():
    policy = ContextPolicyV1(O200kTokenEstimator())
    original = _snapshot()
    changed = ContextSourceSnapshotV1(
        **{**original.__dict__, "source_watermark": original.source_watermark + 1}
    )

    def compile_hash(snapshot: ContextSourceSnapshotV1) -> str:
        projection = policy.agent_projection(snapshot)
        return policy.dialogue_view(
            snapshot,
            available_input_tokens=100_000,
            estimated_input_tokens=1_000,
            messages=[{"role": "user", "content": snapshot.message}],
            projection_blocks=projection.blocks,
        ).content_hash

    assert compile_hash(original) != compile_hash(changed)


def test_agent_context_pins_the_exact_data_version_in_active_task_results():
    published = DataVersionRefV2.from_dict({
        "kind": "data_version", "service": "data-primary", "datasetId": "dataset",
        "versionId": "active-version", "semanticId": "semantic", "objectId": "object",
        "mediaType": "text/csv", "format": "csv", "sizeBytes": 8,
        "checksum": "sha256:" + "a" * 64,
    }).as_storage_dict()
    older = {**published, "version_id": "older-version", "object_id": "older-object"}
    active_task = _task()
    active_task["progress"]["results"] = [published]
    snapshot = replace(
        _snapshot(),
        task_context=active_task,
        execution_actions=(
            {"status": "COMPLETE", "workflow_run_id": "older-run", "outputs": [older]},
            {"status": "COMPLETE", "workflow_run_id": "active-run", "outputs": [published]},
        ),
    )

    projection = ContextPolicyV1(O200kTokenEstimator()).agent_projection(snapshot)

    assert projection.execution_context["actions"] == [snapshot.execution_actions[1]]
    assert projection.execution_context["retrievable"]["recent_workflow_run_ids"] == ["older-run"]


def test_router_uses_summary_watermark_and_restores_selected_turn_order():
    policy = ContextPolicyV1(O200kTokenEstimator())
    view = policy.route_view(
        _snapshot(), available_input_tokens=100_000, fixed_input_tokens=100,
    )
    encoded = json.dumps(view.messages, ensure_ascii=False)

    assert "用户 8" not in encoded
    assert all(f"用户 {revision}" in encoded for revision in (9, 10, 11))
    assert encoded.index("用户 9") < encoded.index("用户 10") < encoded.index("用户 11")
    assert encoded.endswith('继续完成说明"}]')


def test_agent_projection_pins_active_facts_and_indexes_old_successes():
    projection = ContextPolicyV1(O200kTokenEstimator()).agent_projection(
        _snapshot()
    )

    assert [item["workflow_run_id"] for item in projection.execution_context["actions"]] == [
        "workflow-active"
    ]
    assert projection.execution_context["retrievable"]["count"] == 1
    assert projection.execution_context["retrievable"]["recent_workflow_run_ids"] == [
        "workflow-old"
    ]
    assert projection.task_context["criteria"][0]["id"] == "review"
    assert projection.task_context["completed"]["criteria"] == ["reply"]
    dispositions = {block.source_id: block.disposition for block in projection.blocks}
    assert dispositions["workflow-active"] == "INCLUDED"
    assert dispositions["workflow-old"] == "RETRIEVABLE"


def test_agent_projection_includes_only_current_task_preparation():
    policy = ContextPolicyV1(O200kTokenEstimator())
    base = _snapshot()
    preparation = {
        "schemaVersion": "task-preparation.v1",
        "preparation_id": "preparation-1",
        "revision": 2,
        "goal_revision": 12,
        "task_id": "task-1",
        "base_task_revision": 3,
        "status": "READY",
        "entries": [{
            "target_kind": "EXECUTABLE_MODEL",
            "target_id": "groundwater.summary",
            "target_version": "1.1.0",
            "status": "READY",
            "inputs": {"resource_id": {"version_id": "version-1"}},
            "knowledge_refs": [{
                "version_id": "knowledge-1", "unit_id": "unit-1",
                "checksum": "sha256:" + "a" * 64,
            }],
            "parameters": {"axis": "columns"},
            "parameter_sources": {"axis": "MODEL_DEFAULT"},
            "checks": [],
            "blocking_reasons": [],
        }],
        "fresh_for_current_turn": True,
        "disposition": "ACTIVE",
    }
    current = policy.agent_projection(ContextSourceSnapshotV1(
        **{**base.__dict__, "preparation_context": preparation}
    ))
    stale = policy.agent_projection(ContextSourceSnapshotV1(
        **{
            **base.__dict__,
            "preparation_context": {
                **preparation, "fresh_for_current_turn": False
            },
        }
    ))

    assert current.preparation_context["entries"][0]["knowledge_refs"][0]["unit_id"] == "unit-1"
    assert "TASK_PREPARATION" in {block.kind for block in current.blocks}
    assert stale.preparation_context is None
    assert "TASK_PREPARATION" not in {block.kind for block in stale.blocks}


def test_router_rejects_oversized_pinned_input_without_dropping_it():
    policy = ContextPolicyV1(O200kTokenEstimator())
    snapshot = _snapshot()
    snapshot = ContextSourceSnapshotV1(
        **{**snapshot.__dict__, "message": "固定输入" * 10_000}
    )
    with pytest.raises(ContextLimitError, match="AGENT_CONTEXT_TOO_LARGE"):
        policy.route_view(
            snapshot, available_input_tokens=2_000, fixed_input_tokens=500,
        )
    rejected = policy.rejected_route_view(
        snapshot, available_input_tokens=2_000, fixed_input_tokens=500,
    )
    current = next(
        block for block in rejected.blocks if block.kind == "CURRENT_MESSAGE"
    )
    assert current.retention == "PINNED"
    assert current.disposition == "INCLUDED"


def test_one_hundred_turns_and_executions_keep_provider_input_bounded():
    estimator = O200kTokenEstimator()
    policy = ContextPolicyV1(estimator)
    actions = tuple(
        {
            "goal_revision": revision,
            "tool": "historical-model",
            "status": "COMPLETE",
            "workflow_run_id": f"workflow-{revision:03d}",
            "outputs": [{"version_id": f"version-{revision:03d}"}],
            "error": None,
        }
        for revision in range(1, 101)
    )
    snapshot = ContextSourceSnapshotV1(
        session_id="long-session",
        goal_revision=101,
        message="继续",
        turns=tuple(
            ContextTurnV1(
                revision,
                f"用户第 {revision} 轮 " + "输入 " * 200,
                f"助手第 {revision} 轮 " + "答复 " * 200,
            )
            for revision in range(1, 101)
        ),
        memory_summary=None,
        execution_actions=actions,
        mode="DIALOGUE",
        source_watermark=100,
    )

    route = policy.route_view(
        snapshot, available_input_tokens=16_000, fixed_input_tokens=1_000,
    )
    projection = policy.agent_projection(snapshot)
    full_execution = {"schema": "ExecutionContextV1", "actions": list(actions)}

    assert route.estimated_input_tokens <= 16_000
    assert sum(block.disposition == "OMITTED" for block in route.blocks) > 0
    assert projection.execution_context["actions"] == []
    assert projection.execution_context["retrievable"]["count"] == 100
    assert len(
        projection.execution_context["retrievable"]["recent_workflow_run_ids"]
    ) == 20
    # This fixture is deliberately repetitive, but the projection still removes well
    # over the required 30% of the historical execution payload from provider input.
    assert estimator.count(projection.execution_context) <= int(
        estimator.count(full_execution) * 0.7
    )
