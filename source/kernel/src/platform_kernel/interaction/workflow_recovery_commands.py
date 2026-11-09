"""Durable workflow-recovery command behind the InteractionOwner facade."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..application.ports import WorkflowExecutionPort
from .confirmation_commands import SessionProjectionPort
from .models import SessionView
from .recovery import create_recovery_confirmation


@dataclass(frozen=True)
class WorkflowRecoveryExecutor:
    database: Path
    project_id: str
    workflow_execution: WorkflowExecutionPort
    sessions: SessionProjectionPort

    def recover(
        self,
        session_id: str,
        workflow_run_id: str,
        request_id: str,
        expected_goal_revision: int,
        *,
        decision_subject: str,
    ) -> SessionView:
        source = self.workflow_execution.recovery_source(
            self.project_id, session_id, workflow_run_id
        )
        create_recovery_confirmation(
            self.database,
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            project_id=self.project_id,
            request_id=request_id,
            expected_goal_revision=expected_goal_revision,
            source=source,
            decision_subject=decision_subject,
        )
        return self.sessions.get(session_id)


__all__ = ("WorkflowRecoveryExecutor",)
