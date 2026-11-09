"""The explicit dependencies used by Agent-run coordinators."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..application.ports import AgentProjectionPort, WorkflowExecutionPort
from ..settings import ExecutableModelDefinition, KernelSettings
from .sdk_execution import SDKExecutionDependencies


@dataclass(frozen=True)
class RunDependencies:
    database: Path
    settings: KernelSettings
    workflow_execution: WorkflowExecutionPort
    sdk_execution: SDKExecutionDependencies
    agent_projection: AgentProjectionPort
    models_by_tool: dict[str, ExecutableModelDefinition]
