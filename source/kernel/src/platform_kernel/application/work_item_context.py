"""The read-only WorkItem projection consumed by Agent model requests."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class WorkItemContextRequest:
    project_id: str
    session_id: str
    agent_run_id: str
    goal_revision: int
    request_id: str | None
    intent: str | None
    task_action: str | None
    permissions: dict[str, object]
    include_resources: bool
    include_history: bool
    selection: dict | None = None


@dataclass(frozen=True)
class WorkItemContextBlock:
    layer: Literal["L0", "L1", "L2", "L3"]
    source_type: str
    source_id: str
    revision: int | None
    value: dict[str, object]
