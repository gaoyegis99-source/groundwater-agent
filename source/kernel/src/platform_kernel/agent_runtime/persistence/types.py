from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UsageDelta:
    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class StoredRunState:
    state_json: str


@dataclass(frozen=True)
class ReplayedAction:
    arguments: dict[str, object]
    executed_arguments: dict[str, object] | None
    status: str
    workflow_run_id: str | None
    outputs: list[object]
    error: str | None
