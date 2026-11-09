from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

import tiktoken

from .models import MemorySummaryV3


class O200kTokenEstimator:
    name = "o200k_base"

    def count(self, value: object) -> int:
        return token_count(value)


@dataclass(frozen=True)
class MemoryRecord:
    through_goal_revision: int
    summary: MemorySummaryV3


@dataclass(frozen=True)
class MemoryUpdate:
    expected_through_goal_revision: int
    through_goal_revision: int
    summary: MemorySummaryV3


@dataclass(frozen=True)
class ConversationTurn:
    goal_revision: int
    user: str
    assistant: str


@dataclass(frozen=True)
class ExecutionActionV1:
    goal_revision: int
    tool: str
    model_id: str
    model_version: str
    model_checksum: str
    workflow_id: str
    workflow_version: str
    workflow_checksum: str
    status: str
    workflow_run_id: str | None
    outputs: tuple[dict[str, object], ...]
    error: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "goal_revision": self.goal_revision,
            "tool": self.tool,
            "model": {
                "id": self.model_id,
                "version": self.model_version,
                "checksum": self.model_checksum,
            },
            "workflow": {
                "id": self.workflow_id,
                "version": self.workflow_version,
                "checksum": self.workflow_checksum,
            },
            "status": self.status,
            "workflow_run_id": self.workflow_run_id,
            "outputs": list(self.outputs),
            "error": self.error,
        }


@dataclass(frozen=True)
class ExecutionContextV1:
    actions: tuple[ExecutionActionV1, ...]

    def canonical_json(self) -> str:
        return canonical_json(
            {
                "schema": "ExecutionContextV1",
                "actions": [item.as_dict() for item in self.actions],
            }
        )


def completed_turns(
    history: tuple[tuple[int, str, str], ...],
    *,
    after_revision: int,
) -> tuple[ConversationTurn, ...]:
    grouped: dict[int, dict[str, str]] = {}
    for revision, role, content in history:
        if revision <= after_revision:
            continue
        messages = grouped.setdefault(revision, {})
        if role not in {"user", "assistant"} or role in messages:
            raise ValueError("AGENT_HISTORY_INVALID")
        messages[role] = content
    turns = []
    for revision in sorted(grouped):
        messages = grouped[revision]
        if set(messages) != {"user", "assistant"}:
            raise ValueError("AGENT_HISTORY_INVALID")
        turns.append(
            ConversationTurn(revision, messages["user"], messages["assistant"])
        )
    return tuple(turns)


def _assistant_message(identifier: str, text: str) -> dict[str, Any]:
    # Use the complete SDK message shape: RunState normalizes plain assistant
    # strings on persistence but cannot replay that partial shape through Chat.
    return {
        "id": identifier,
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def build_input_items(
    summary: MemorySummaryV3 | None,
    turns: tuple[ConversationTurn, ...],
    message: str,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if summary is not None:
        items.append(
            _assistant_message(
                "msg_memory",
                "[Kernel-derived non-authoritative MemorySummaryV3; this is "
                "conversation context, not an execution fact.]\n"
                + summary.model_dump_json(),
            )
        )
    for turn in turns:
        items.extend(
            (
                {"role": "user", "content": turn.user},
                _assistant_message(f"msg_history_{turn.goal_revision}", turn.assistant),
            )
        )
    items.append({"role": "user", "content": message})
    return items


def build_compaction_input(
    previous: MemorySummaryV3 | None,
    turns: tuple[ConversationTurn, ...],
) -> list[dict[str, str]]:
    payload = {
        "previous_summary": None if previous is None else previous.model_dump(),
        "turns": [
            {
                "goal_revision": turn.goal_revision,
                "user": turn.user,
                "assistant": turn.assistant,
            }
            for turn in turns
        ],
    }
    return [{"role": "user", "content": canonical_json(payload)}]


def canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def token_count(value: object) -> int:
    text = value if isinstance(value, str) else canonical_json(value)
    return len(_ENCODER.encode(text, disallowed_special=()))


_ENCODER = tiktoken.get_encoding("o200k_base")
