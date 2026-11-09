from __future__ import annotations

from dataclasses import dataclass

from ..contracts import DataVersionRefV2


@dataclass(frozen=True)
class ConfirmationAction:
    tool_call_id: str
    tool: str
    model_id: str
    model_version: str
    model_checksum: str
    workflow_id: str
    workflow_version: str
    workflow_checksum: str
    input_schema_checksum: str
    fingerprint: str
    arguments: dict[str, object]
    input_references: dict[str, DataVersionRefV2]


@dataclass(frozen=True)
class Confirmation:
    confirmation_id: str
    goal_revision: int
    status: str
    actions: tuple[ConfirmationAction, ...]
    agent_run_id: str
    reason: str | None = None
    revision: int = 1
    kind: str = "INITIAL"
    replaces_confirmation_id: str | None = None
    source_workflow_run_id: str | None = None
    request_id: str | None = None
    created_at: str | None = None


@dataclass(frozen=True)
class Session:
    session_id: str
    project_id: str
    goal_revision: int
    agent_run_id: str | None
    confirmation: Confirmation | None
    agent_output: object | None
    workflow_run_ids: tuple[str, ...]
    remote_outputs: tuple[DataVersionRefV2, ...]
    workflow_draft_id: str | None
    task: dict | None = None
    control_proposals: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True)
class SessionView:
    session: Session
    agent_steps: tuple[str, ...]
