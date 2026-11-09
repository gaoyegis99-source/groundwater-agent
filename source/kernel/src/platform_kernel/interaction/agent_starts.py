"""Durable pending-Agent start and projection lifecycle."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..application.ports import AgentRuntimePort
from ..application.query_dtos import AgentRunView, MemoryView
from ..errors import AgentRunError, UpstreamServiceError
from .persistence import claim_agent_start, history, pending_agent_start
from .state_projection import apply_agent_run


class MemoryReader(Protocol):
    def __call__(self, database: Path, session_id: str, /) -> MemoryView | None: ...


class PendingAgentStartExecutor:
    def __init__(
        self,
        database: Path,
        agent_runtime: AgentRuntimePort,
        memory_reader: MemoryReader,
    ) -> None:
        self.database = database
        self.agent_runtime = agent_runtime
        self.memory_reader = memory_reader

    async def resume(
        self, session_id: str, *, propagate_error: bool = True
    ) -> None:
        start = pending_agent_start(self.database, session_id)
        if start is None:
            return
        run = self.agent_runtime.find(start.agent_run_id)
        if run is not None:
            self.project(run)
            return
        message = claim_agent_start(self.database, start)
        if message is None:
            return
        memory = self.memory_reader(self.database, session_id)
        try:
            run = await self.agent_runtime.start(
                session_id,
                start.project_id,
                start.goal_revision,
                message,
                history(
                    self.database,
                    session_id,
                    after_revision=0 if memory is None else memory.through_goal_revision,
                ),
                agent_run_id=start.agent_run_id,
                actor_subject=start.owner_subject,
            )
        except (AgentRunError, UpstreamServiceError):
            run = self.agent_runtime.get(start.agent_run_id, start.project_id)
            self.project(run)
            if propagate_error:
                raise
            return
        self.project(run)

    def project(self, run: AgentRunView) -> None:
        current = apply_agent_run(self.database, run)
        if not current and run.status == "WAITING_CONFIRMATION":
            self.agent_runtime.supersede(run.agent_run_id)


__all__ = ("MemoryReader", "PendingAgentStartExecutor")
