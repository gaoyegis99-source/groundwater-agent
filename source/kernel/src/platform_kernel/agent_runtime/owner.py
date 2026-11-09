from __future__ import annotations

from pathlib import Path
from typing import Any

from ..application.ports import AgentProjectionPort, WorkflowExecutionPort
from ..application.query_dtos import ConfirmedAction
from ..settings import KernelSettings
from .models import (
    AgentRun,
)
from .memory import MemoryUpdate
from .persistence import (
    UsageDelta,
    find_run,
    get_run,
    list_session_results,
    list_provider_requests,
    recover_running_run,
    recover_provider_requests,
    runs_with_status,
    supersede_waiting_run,
)
from .sdk_execution import (
    SDKExecutionDependencies,
)
from .tracing import load_trace, prune_traces
from .run_dependencies import RunDependencies

from .run_finalization import record_final
from .run_resume import (
    confirm_recovery,
    confirm_run,
    finalize_recovery,
    reconcile_execution,
    reject_run,
    resume_execution,
)
from .run_start import start_run


class AgentRuntimeOwner:
    def __init__(
        self,
        database: Path,
        settings: KernelSettings,
        workflow_execution: WorkflowExecutionPort,
        sdk_execution: SDKExecutionDependencies,
        agent_projection: AgentProjectionPort,
    ) -> None:
        self.database = database
        self.settings = settings
        self.workflow_execution = workflow_execution
        self.sdk_execution = sdk_execution
        self.agent_projection = agent_projection
        self.models_by_tool = {item.tool_name: item for item in settings.models}

    @property
    def run_dependencies(self) -> RunDependencies:
        return RunDependencies(
            self.database, self.settings, self.workflow_execution,
            self.sdk_execution, self.agent_projection, self.models_by_tool,
        )

    async def start(
        self,
        session_id: str,
        project_id: str,
        goal_revision: int,
        message: str,
        history: tuple[tuple[int, str, str], ...],
        *,
        agent_run_id: str,
        dialogue_policy: str = "READ_ONLY",
        actor_subject: str | None = None,
    ) -> AgentRun:
        return await start_run(
            self.run_dependencies,
            session_id,
            project_id,
            goal_revision,
            message,
            history,
            agent_run_id=agent_run_id,
            dialogue_policy=dialogue_policy,
            actor_subject=actor_subject or self.settings.local_identity_subject,
        )

    def confirm(
        self,
        agent_run_id: str,
        project_id: str,
        confirmation_id: str | None = None,
        confirmed_actions: tuple[ConfirmedAction, ...] | None = None,
    ) -> AgentRun:
        return confirm_run(
            self.run_dependencies, agent_run_id, project_id,
            confirmation_id, confirmed_actions,
        )

    def reconcile_execution(self, agent_run_id: str) -> bool:
        return reconcile_execution(self.run_dependencies, agent_run_id)

    def finalize_recovery(self, agent_run_id: str) -> AgentRun | None:
        return finalize_recovery(self.run_dependencies, agent_run_id)

    def confirm_recovery(
        self,
        agent_run_id: str,
        project_id: str,
        confirmation_id: str,
        confirmed_actions: tuple[ConfirmedAction, ...],
    ) -> AgentRun:
        return confirm_recovery(
            self.run_dependencies, agent_run_id, project_id,
            confirmation_id, confirmed_actions,
        )

    async def reject(
        self,
        agent_run_id: str,
        project_id: str,
        reason: str,
        *,
        tool_call_ids: tuple[str, ...],
    ) -> AgentRun:
        return await reject_run(
            self.run_dependencies, agent_run_id, project_id, reason,
            tool_call_ids=tool_call_ids,
        )

    async def resume_execution(self, agent_run_id: str) -> AgentRun:
        return await resume_execution(self.run_dependencies, agent_run_id)

    def get(self, agent_run_id: str, project_id: str | None = None) -> AgentRun:
        return get_run(self.database, agent_run_id, project_id)

    def provider_requests(
        self,
        agent_run_id: str,
        project_id: str,
    ) -> tuple[dict[str, object], ...]:
        return list_provider_requests(self.database, agent_run_id, project_id)

    def find(self, agent_run_id: str) -> AgentRun | None:
        return find_run(self.database, agent_run_id)

    def session_results(
        self,
        session_id: str,
        project_id: str,
        current_revision: int,
        after_revision: int,
    ) -> dict[str, object]:
        return list_session_results(
            self.database, session_id, project_id, current_revision, after_revision
        )

    def supersede(self, agent_run_id: str) -> None:
        run = self.get(agent_run_id)
        supersede_waiting_run(self.database, run, self.agent_projection)

    def recover_interrupted(self) -> None:
        recover_provider_requests(self.database)
        runs = runs_with_status(self.database, "RUNNING")
        for run in runs:
            recover_running_run(self.database, run, self.agent_projection)

    def execution_waiting_runs(self) -> tuple[AgentRun, ...]:
        return runs_with_status(self.database, "WAITING_EXECUTION")

    def trace(self, agent_run_id: str, project_id: str) -> tuple[dict[str, Any], ...]:
        run = self.get(agent_run_id, project_id)
        return load_trace(self.database, run.trace_id)

    def prune_traces(self) -> int:
        return prune_traces(self.database)

    def _record_final(
        self,
        run: AgentRun,
        result: Any,
        usage: UsageDelta,
        memory_update: MemoryUpdate | None,
    ) -> AgentRun:
        return record_final(self.run_dependencies, run, result, usage, memory_update)
