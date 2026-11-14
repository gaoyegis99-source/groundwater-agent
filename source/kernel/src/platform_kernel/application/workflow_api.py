"""Workflow owner reads approved for cross-owner Kernel use."""

import sqlite3

from ..interaction.access import dispatch_confirmation_current
from ..interaction.workflow_drafts import frozen_composition

def session_execution_tasks(
    connection: sqlite3.Connection, session_id: str, project_id: str,
    agent_run_ids: tuple[str, ...],
) -> dict[str, list[dict]]:
    from ..workflow_execution.projection import session_execution_tasks as project

    return project(connection, session_id, project_id, agent_run_ids)


__all__ = ["dispatch_confirmation_current", "frozen_composition", "session_execution_tasks"]
