from __future__ import annotations

import sqlite3

from ..interaction.message_request_common import materialize_workflow_draft
from ..interaction.state_projection import (
    activity_counts,
    project_agent_run,
    upsert_assistant_message,
)
from ..tasks.persistence import require_current_confirmation
from .query_dtos import AgentRunView


class AgentProjectionAdapter:
    """Apply Agent transitions to Interaction and Task projections atomically."""

    def __init__(self, models=(), output_steps=()) -> None:
        self.models = models
        self.output_steps = output_steps

    def project_agent_run(self, connection: sqlite3.Connection, run: AgentRunView) -> bool:
        return project_agent_run(connection, run, models=self.models, output_steps=self.output_steps)

    def upsert_assistant_message(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        goal_revision: int,
        content: str,
    ) -> None:
        upsert_assistant_message(connection, session_id, goal_revision, content)

    def materialize_workflow_draft(
        self,
        connection: sqlite3.Connection,
        *,
        session_id: str,
        request_id: str,
        message: str,
        goal_revision: int,
        agent_run_id: str,
        workflow_draft_id: str,
        created_at: str,
    ) -> None:
        materialize_workflow_draft(
            connection, session_id=session_id, request_id=request_id,
            message=message, goal_revision=goal_revision, agent_run_id=agent_run_id,
            workflow_draft_id=workflow_draft_id, created_at=created_at,
        )

    def activity_counts(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        agent_run_id: str,
    ) -> tuple[int, int]:
        return activity_counts(connection, session_id, agent_run_id)

    def require_current_confirmation(
        self, connection: sqlite3.Connection, confirmation_id: str | None
    ) -> None:
        require_current_confirmation(connection, confirmation_id)
