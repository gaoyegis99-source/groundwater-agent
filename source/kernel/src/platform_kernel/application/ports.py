from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
import sqlite3
from typing import Protocol

from .query_dtos import AgentRunView, ConfirmedAction


class AgentProjectionPort(Protocol):
    """Atomic bridge from Agent-owned transitions to Interaction projections."""

    def project_agent_run(self, connection: sqlite3.Connection, run: AgentRunView) -> bool: ...

    def upsert_assistant_message(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        goal_revision: int,
        content: str,
    ) -> None: ...

    def activity_counts(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        agent_run_id: str,
    ) -> tuple[int, int]: ...

    def require_current_confirmation(
        self, connection: sqlite3.Connection, confirmation_id: str | None
    ) -> None: ...


class AgentRuntimePort(Protocol):
    database: Path

    def get(self, agent_run_id: str, project_id: str | None = None) -> AgentRunView: ...
    def find(self, agent_run_id: str) -> AgentRunView | None: ...
    async def start(
        self, session_id: str, project_id: str, goal_revision: int,
        message: str, history: tuple[tuple[int, str, str], ...], *,
        agent_run_id: str, dialogue_policy: str = "READ_ONLY",
        actor_subject: str | None = None,
    ) -> AgentRunView: ...
    def confirm(
        self, agent_run_id: str, project_id: str,
        confirmation_id: str | None = None,
        confirmed_actions: tuple[ConfirmedAction, ...] | None = None,
    ) -> AgentRunView: ...
    async def reject(
        self,
        agent_run_id: str,
        project_id: str,
        reason: str,
        *,
        tool_call_ids: tuple[str, ...],
    ) -> AgentRunView: ...
    def confirm_recovery(
        self,
        agent_run_id: str,
        project_id: str,
        confirmation_id: str,
        confirmed_actions: tuple[ConfirmedAction, ...],
    ) -> AgentRunView: ...
    async def resume_execution(self, agent_run_id: str) -> AgentRunView: ...
    def supersede(self, agent_run_id: str) -> None: ...
    def session_results(
        self, session_id: str, project_id: str,
        current_revision: int, after_revision: int,
    ) -> dict[str, object]: ...


class WorkflowExecutionPort(Protocol):
    def enqueue_confirmed_batch(
        self, connection: sqlite3.Connection, *, agent_run_id: str,
        session_id: str, project_id: str,
        confirmation_id: str | None = None,
        actions: tuple[ConfirmedAction, ...],
    ) -> tuple[object, ...]: ...
    def recover_interrupted(self) -> None: ...
    def tick(self) -> AsyncIterator[object]: ...
    def recovery_source(
        self, project_id: str, session_id: str, workflow_run_id: str
    ) -> dict[str, object] | None: ...
