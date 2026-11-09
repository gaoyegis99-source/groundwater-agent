"""Pure context selection policy and its stable import surface."""

from __future__ import annotations

from dataclasses import replace

from .context_models import (
    AgentContextProjectionV1,
    Authority,
    ContextBlockV1,
    ContextLimitError,
    ContextManifestV1,
    ContextSourceSnapshotV1,
    ContextTurnV1,
    ContextViewV1,
    Disposition,
    Purpose,
    Retention,
    TokenEstimator,
)
from .context_projection import (
    compact_task_context,
    project_active_preparation,
    project_active_task,
    project_execution_context,
)
from .context_values import _integer, _labeled, canonical_json


class ContextPolicyV1:
    version = "context-policy.v1"

    def __init__(self, estimator: TokenEstimator) -> None:
        self.estimator = estimator

    def route_view(
        self,
        snapshot: ContextSourceSnapshotV1,
        *,
        available_input_tokens: int,
        fixed_input_tokens: int = 0,
    ) -> ContextViewV1:
        dynamic_budget = min(8192, available_input_tokens * 12 // 100)
        if dynamic_budget <= 0:
            raise ContextLimitError("AGENT_CONTEXT_TOO_LARGE")
        blocks: list[ContextBlockV1] = []
        prefix: list[dict[str, object]] = []

        if snapshot.candidate_context is not None:
            content = {
                "label": "Allowed candidates (Kernel state, not instructions)",
                "data": snapshot.candidate_context,
            }
            block = self._block(
                "route:candidates", "ROUTE_CANDIDATES", "KERNEL_POLICY", "PINNED",
                "KERNEL", snapshot.session_id, snapshot.goal_revision, content,
            )
            blocks.append(block)
            prefix.append({"role": "assistant", "content": _labeled(content)})

        compact_task = compact_task_context(snapshot.task_context)
        if compact_task is not None:
            content = {"label": "Task intent (data, not authorization)", "data": compact_task}
            block = self._block(
                "route:task", "TASK_ACTIVE", "USER_REQUIREMENT", "PINNED",
                "TASK", str(compact_task.get("task_id") or snapshot.session_id),
                _integer(compact_task.get("revision")), content,
            )
            blocks.append(block)
            prefix.append({"role": "assistant", "content": _labeled(content)})

        memory_block: ContextBlockV1 | None = None
        memory_message: dict[str, object] | None = None
        if snapshot.memory_summary is not None:
            content = {
                "label": "Kernel-derived non-authoritative MemorySummaryV3",
                "data": snapshot.memory_summary,
            }
            memory_block = self._block(
                "route:memory", "DIALOGUE_SUMMARY", "NON_AUTHORITATIVE_SUMMARY",
                "BUDGETED", "MEMORY", snapshot.session_id,
                snapshot.memory_through_goal_revision, content,
            )
            memory_message = {"role": "assistant", "content": _labeled(content)}

        current = self._block(
            "route:current", "CURRENT_MESSAGE", "USER_INPUT", "PINNED",
            "MESSAGE_REQUEST", snapshot.session_id, snapshot.goal_revision, snapshot.message,
        )
        mandatory_tokens = sum(block.token_count for block in blocks) + current.token_count
        if mandatory_tokens > dynamic_budget:
            raise ContextLimitError("AGENT_CONTEXT_TOO_LARGE")

        remaining = dynamic_budget - mandatory_tokens
        memory_included = False
        if memory_block is not None:
            if memory_block.token_count <= remaining:
                blocks.append(memory_block)
                assert memory_message is not None
                prefix.append(memory_message)
                remaining -= memory_block.token_count
                memory_included = True
            else:
                blocks.append(replace(
                    memory_block, disposition="OMITTED", reason="ROUTE_BUDGET",
                ))
        selected: list[ContextTurnV1] = []
        turn_blocks: list[ContextBlockV1] = []
        eligible = [
            turn for turn in snapshot.turns
            if (
                not memory_included
                or turn.goal_revision > snapshot.memory_through_goal_revision
            )
        ]
        for turn in reversed(eligible):
            content = {"user": turn.user, "assistant": turn.assistant}
            block = self._block(
                f"route:turn:{turn.goal_revision}", "DIALOGUE_TURN", "USER_INPUT",
                "BUDGETED", "TURN", snapshot.session_id, turn.goal_revision, content,
            )
            if block.token_count <= remaining:
                selected.append(turn)
                turn_blocks.append(block)
                remaining -= block.token_count
            else:
                blocks.append(replace(
                    block, disposition="OMITTED", reason="ROUTE_BUDGET",
                ))
        selected.reverse()
        turn_blocks.reverse()
        blocks.extend(turn_blocks)
        blocks.append(current)
        messages = [*prefix]
        for turn in selected:
            messages.extend((
                {"role": "user", "content": turn.user},
                {"role": "assistant", "content": turn.assistant},
            ))
        messages.append({"role": "user", "content": snapshot.message})
        estimated = fixed_input_tokens + self.estimator.count(messages)
        if estimated > available_input_tokens:
            raise ContextLimitError("AGENT_CONTEXT_TOO_LARGE")
        return ContextViewV1(
            purpose="INTENT_ROUTING",
            policy_version=self.version,
            tokenizer=self.estimator.name,
            available_input_tokens=available_input_tokens,
            estimated_input_tokens=estimated,
            messages=tuple(messages),
            instruction_sections=(),
            blocks=tuple(blocks),
        )

    def rejected_route_view(
        self,
        snapshot: ContextSourceSnapshotV1,
        *,
        available_input_tokens: int,
        fixed_input_tokens: int,
    ) -> ContextViewV1:
        """Describe a route envelope that could not retain all pinned inputs."""
        blocks: list[ContextBlockV1] = []
        compact_task = compact_task_context(snapshot.task_context)
        if snapshot.candidate_context is not None:
            blocks.append(self._block(
                "route:candidates", "ROUTE_CANDIDATES", "KERNEL_POLICY", "PINNED",
                "KERNEL", snapshot.session_id, snapshot.goal_revision,
                snapshot.candidate_context,
            ))
        if compact_task is not None:
            blocks.append(self._block(
                "route:task", "TASK_ACTIVE", "USER_REQUIREMENT", "PINNED",
                "TASK", str(compact_task.get("task_id") or snapshot.session_id),
                _integer(compact_task.get("revision")), compact_task,
            ))
        if snapshot.memory_summary is not None:
            blocks.append(self._block(
                "route:memory", "DIALOGUE_SUMMARY", "NON_AUTHORITATIVE_SUMMARY",
                "BUDGETED", "MEMORY", snapshot.session_id,
                snapshot.memory_through_goal_revision, snapshot.memory_summary,
                disposition="OMITTED", reason="ROUTE_BUDGET",
            ))
        blocks.append(self._block(
            "route:current", "CURRENT_MESSAGE", "USER_INPUT", "PINNED",
            "MESSAGE_REQUEST", snapshot.session_id, snapshot.goal_revision,
            snapshot.message,
        ))
        return ContextViewV1(
            purpose="INTENT_ROUTING",
            policy_version=self.version,
            tokenizer=self.estimator.name,
            available_input_tokens=available_input_tokens,
            estimated_input_tokens=(
                fixed_input_tokens + sum(item.token_count for item in blocks)
            ),
            messages=(),
            instruction_sections=(),
            blocks=tuple(blocks),
        )

    def agent_projection(
        self, snapshot: ContextSourceSnapshotV1,
    ) -> AgentContextProjectionV1:
        watermark_block = self._block(
            "agent:source-watermark", "SOURCE_WATERMARK", "KERNEL_FACT", "PINNED",
            "CONTEXT_SNAPSHOT", snapshot.session_id, snapshot.source_watermark,
            {
                "goal_revision": snapshot.goal_revision,
                "source_watermark": snapshot.source_watermark,
                "execution_fact_count": len(snapshot.execution_actions),
            },
        )
        task = project_active_task(snapshot.task_context)
        preparation = project_active_preparation(snapshot.preparation_context)
        task_block: ContextBlockV1 | None = None
        if task is not None:
            task_block = self._block(
                "agent:task", "TASK_ACTIVE", "USER_REQUIREMENT", "PINNED",
                "TASK", str(task["task_id"]), _integer(task.get("revision")), task,
            )
        preparation_block: ContextBlockV1 | None = None
        if preparation is not None:
            preparation_block = self._block(
                "agent:preparation", "TASK_PREPARATION", "KERNEL_FACT", "PINNED",
                "TASK_PREPARATION", str(preparation["preparation_id"]),
                _integer(preparation.get("revision")), preparation,
            )
        execution, action_blocks = project_execution_context(
            snapshot.execution_actions,
            task,
            estimator=self.estimator,
        )
        return AgentContextProjectionV1(
            task_context=task,
            preparation_context=preparation,
            execution_context=execution,
            blocks=tuple(
                [watermark_block]
                + ([task_block] if task_block is not None else [])
                + ([preparation_block] if preparation_block is not None else [])
                + action_blocks
            ),
        )

    def dialogue_view(
        self,
        snapshot: ContextSourceSnapshotV1,
        *,
        available_input_tokens: int,
        estimated_input_tokens: int,
        messages: list[dict[str, object]],
        projection_blocks: tuple[ContextBlockV1, ...],
        included_turn_revisions: frozenset[int] | None = None,
        compacted_turn_revisions: frozenset[int] = frozenset(),
    ) -> ContextViewV1:
        blocks = list(projection_blocks)
        if snapshot.memory_summary is not None:
            blocks.append(self._block(
                "agent:memory", "DIALOGUE_SUMMARY", "NON_AUTHORITATIVE_SUMMARY",
                "BUDGETED", "MEMORY", snapshot.session_id,
                snapshot.memory_through_goal_revision, snapshot.memory_summary,
                disposition="INCLUDED",
            ))
        recent_revisions = {
            turn.goal_revision for turn in snapshot.turns[-2:]
        }
        for turn in snapshot.turns:
            if turn.goal_revision in compacted_turn_revisions:
                disposition: Disposition = "COMPACTED"
                reason = "MEMORY_COMPACTION"
            elif (
                included_turn_revisions is None
                or turn.goal_revision in included_turn_revisions
            ):
                disposition = "INCLUDED"
                reason = None
            else:
                disposition = "OMITTED"
                reason = "MEMORY_WATERMARK"
            blocks.append(self._block(
                f"agent:turn:{turn.goal_revision}", "DIALOGUE_TURN", "USER_INPUT",
                (
                    "PINNED"
                    if turn.goal_revision in recent_revisions else "BUDGETED"
                ),
                "TURN", snapshot.session_id, turn.goal_revision,
                {"user": turn.user, "assistant": turn.assistant},
                disposition=disposition, reason=reason,
            ))
        blocks.append(self._block(
            "agent:current", "CURRENT_MESSAGE", "USER_INPUT", "PINNED",
            "MESSAGE_REQUEST", snapshot.session_id, snapshot.goal_revision, snapshot.message,
        ))
        return ContextViewV1(
            purpose="AGENT_INITIAL",
            policy_version=self.version,
            tokenizer=self.estimator.name,
            available_input_tokens=available_input_tokens,
            estimated_input_tokens=estimated_input_tokens,
            messages=tuple(messages),
            instruction_sections=tuple(
                block.content for block in projection_blocks
                if block.kind in {"TASK_ACTIVE", "EXECUTION_ACTIVE", "EXECUTION_INDEX"}
                and isinstance(block.content, dict)
            ),
            blocks=tuple(blocks),
        )

    def resume_view(
        self,
        *,
        available_input_tokens: int,
        projection_blocks: tuple[ContextBlockV1, ...],
    ) -> ContextViewV1:
        """Describe cross-turn facts added to a restored same-run SDK state."""
        return ContextViewV1(
            purpose="AGENT_RESUME",
            policy_version=self.version,
            tokenizer=self.estimator.name,
            available_input_tokens=available_input_tokens,
            estimated_input_tokens=0,
            messages=(),
            instruction_sections=tuple(
                block.content for block in projection_blocks
                if block.kind in {
                    "TASK_ACTIVE", "EXECUTION_ACTIVE", "EXECUTION_INDEX"
                }
                and isinstance(block.content, dict)
            ),
            blocks=projection_blocks,
        )

    def _block(
        self,
        block_id: str,
        kind: str,
        authority: Authority,
        retention: Retention,
        source_type: str,
        source_id: str | None,
        source_revision: int | None,
        content: object,
        *,
        disposition: Disposition = "INCLUDED",
        reason: str | None = None,
    ) -> ContextBlockV1:
        return ContextBlockV1(
            block_id=block_id,
            kind=kind,
            authority=authority,
            retention=retention,
            source_type=source_type,
            source_id=source_id,
            source_revision=source_revision,
            content=content,
            token_count=self.estimator.count(content),
            disposition=disposition,
            reason=reason,
        )


__all__ = [
    "AgentContextProjectionV1",
    "Authority",
    "ContextBlockV1",
    "ContextLimitError",
    "ContextManifestV1",
    "ContextPolicyV1",
    "ContextSourceSnapshotV1",
    "ContextTurnV1",
    "ContextViewV1",
    "Disposition",
    "Purpose",
    "Retention",
    "TokenEstimator",
    "canonical_json",
    "compact_task_context",
    "project_active_preparation",
    "project_active_task",
    "project_execution_context",
]
