from __future__ import annotations

from dataclasses import dataclass

from ..contracts import DataVersionRefV2


@dataclass(frozen=True)
class WorkflowRun:
    run_id: str
    agent_run_id: str
    session_id: str
    tool_call_id: str
    confirmation_id: str | None
    attempt: int
    retry_of_workflow_run_id: str | None
    position: int
    project_id: str
    model_id: str
    model_version: str
    model_checksum: str
    workflow_id: str
    workflow_version: str
    workflow_checksum: str
    execution_service: str
    executor_id: str
    arguments: dict[str, object]
    input_references: dict[str, DataVersionRefV2]
    status: str
    remote_job_id: str | None
    outputs: tuple[DataVersionRefV2, ...]
    error: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    duration_ms: int | None


@dataclass(frozen=True)
class DispatchWork:
    run: WorkflowRun
    state: str
    deadline_at: str


@dataclass(frozen=True)
class WorkflowTickResult:
    processed_count: int
    ready_agent_run_ids: tuple[str, ...]
