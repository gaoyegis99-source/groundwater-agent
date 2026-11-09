"""Immutable, provider-neutral context data structures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from .context_values import _digest

Authority = Literal[
    "KERNEL_POLICY",
    "USER_INPUT",
    "USER_REQUIREMENT",
    "KERNEL_FACT",
    "NON_AUTHORITATIVE_SUMMARY",
]
Retention = Literal["PINNED", "BUDGETED", "RETRIEVABLE"]
Disposition = Literal[
    "INCLUDED", "COMPACTED", "RETRIEVABLE", "OMITTED", "WOULD_COMPACT"
]
Purpose = Literal["INTENT_ROUTING", "AGENT_INITIAL", "AGENT_RESUME", "COMPACTION"]


class TokenEstimator(Protocol):
    name: str

    def count(self, value: object) -> int: ...


@dataclass(frozen=True)
class ContextTurnV1:
    goal_revision: int
    user: str
    assistant: str


@dataclass(frozen=True)
class ContextSourceSnapshotV1:
    session_id: str
    goal_revision: int
    message: str
    turns: tuple[ContextTurnV1, ...]
    memory_summary: dict[str, object] | None = None
    memory_through_goal_revision: int = 0
    task_context: dict[str, object] | None = None
    preparation_context: dict[str, object] | None = None
    execution_actions: tuple[dict[str, object], ...] = ()
    mode: str = "DIALOGUE"
    candidate_context: dict[str, object] | None = None
    source_watermark: int = 0


@dataclass(frozen=True)
class ContextBlockV1:
    block_id: str
    kind: str
    authority: Authority
    retention: Retention
    source_type: str
    source_id: str | None
    source_revision: int | None
    content: object
    token_count: int
    disposition: Disposition = "INCLUDED"
    reason: str | None = None

    def manifest(self) -> dict[str, object]:
        return {
            "block_id": self.block_id,
            "kind": self.kind,
            "authority": self.authority,
            "retention": self.retention,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_revision": self.source_revision,
            "content_hash": _digest(self.content),
            "token_count": self.token_count,
            "disposition": self.disposition,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ContextManifestV1:
    purpose: Purpose
    policy_version: str
    tokenizer: str
    available_input_tokens: int
    estimated_input_tokens: int
    blocks: tuple[dict[str, object], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": "context-manifest.v1",
            "purpose": self.purpose,
            "policy_version": self.policy_version,
            "tokenizer": self.tokenizer,
            "available_input_tokens": self.available_input_tokens,
            "estimated_input_tokens": self.estimated_input_tokens,
            "blocks": list(self.blocks),
        }


@dataclass(frozen=True)
class ContextViewV1:
    purpose: Purpose
    policy_version: str
    tokenizer: str
    available_input_tokens: int
    estimated_input_tokens: int
    messages: tuple[dict[str, object], ...]
    instruction_sections: tuple[dict[str, object], ...]
    blocks: tuple[ContextBlockV1, ...]

    def manifest(self) -> dict[str, object]:
        return ContextManifestV1(
            purpose=self.purpose,
            policy_version=self.policy_version,
            tokenizer=self.tokenizer,
            available_input_tokens=self.available_input_tokens,
            estimated_input_tokens=self.estimated_input_tokens,
            blocks=tuple(block.manifest() for block in self.blocks),
        ).as_dict()

    @property
    def retrieval_index(self) -> tuple[dict[str, object], ...]:
        return tuple(
            block.manifest() for block in self.blocks
            if block.disposition == "RETRIEVABLE"
        )

    @property
    def omitted_items(self) -> tuple[dict[str, object], ...]:
        return tuple(
            block.manifest() for block in self.blocks
            if block.disposition in {"COMPACTED", "OMITTED", "WOULD_COMPACT"}
        )

    @property
    def content_hash(self) -> str:
        return _digest({
            "messages": self.messages,
            "instruction_sections": self.instruction_sections,
            "manifest": self.manifest(),
        })


@dataclass(frozen=True)
class AgentContextProjectionV1:
    task_context: dict[str, object] | None
    preparation_context: dict[str, object] | None
    execution_context: dict[str, object]
    blocks: tuple[ContextBlockV1, ...]


class ContextLimitError(ValueError):
    pass
