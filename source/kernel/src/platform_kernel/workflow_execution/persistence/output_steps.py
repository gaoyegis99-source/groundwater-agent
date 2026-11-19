"""Dispatch an approved step from an exact, committed WorkflowRun output."""

from __future__ import annotations

import json
import sqlite3

from ...application.query_dtos import ConfirmedAction
from ...database import append_event
from ...errors import WorkflowDefinitionUnavailableError
from ..models import WorkflowRun
from .queue import enqueue_batch


def enqueue_output_step(connection: sqlite3.Connection, source: WorkflowRun, outputs, *, models, services) -> None:
    step = connection.execute(
        """SELECT * FROM published_output_steps WHERE confirmation_id=? AND source_tool_call_id=?""",
        (source.confirmation_id, source.tool_call_id),
    ).fetchone()
    if step is None or connection.execute(
        "SELECT 1 FROM workflow_runs WHERE agent_run_id=? AND tool_call_id=?",
        (source.agent_run_id, step["tool_call_id"]),
    ).fetchone():
        return
    plan = json.loads(step["plan_json"])
    matches = [ref for ref in outputs if ref.output_name == plan["output"]]
    if len(matches) != 1:
        _blocked(connection, source, step, "WORKFLOW_PUBLISHED_OUTPUT_UNAVAILABLE")
        return
    reference = matches[0]
    position = connection.execute(
        "SELECT MAX(position)+1 FROM workflow_runs WHERE agent_run_id=?", (source.agent_run_id,),
    ).fetchone()[0]
    action = ConfirmedAction(
        tool_call_id=step["tool_call_id"],
        position=position,
        **{key: plan[key] for key in (
            "tool", "model_id", "model_version", "model_checksum", "workflow_id",
            "workflow_version", "workflow_checksum", "input_schema_checksum",
        )},
        arguments={**plan["arguments"], plan["input"]: reference.version_id},
        input_references={plan["input"]: reference},
    )
    try:
        child, = enqueue_batch(
            connection, agent_run_id=source.agent_run_id, session_id=source.session_id,
            project_id=source.project_id, confirmation_id=source.confirmation_id,
            actions=(action,), models=models, services=services,
        )
    except WorkflowDefinitionUnavailableError:
        _blocked(connection, source, step, "WORKFLOW_DEFINITION_UNAVAILABLE")
        return
    connection.execute(
        "INSERT INTO workflow_output_sources VALUES (?, ?)", (child.run_id, source.run_id),
    )


def _blocked(connection, source, step, code: str) -> None:
    append_event(connection, source.session_id, "workflow.output_step_blocked", {
        "source_workflow_run_id": source.run_id,
        "confirmation_id": source.confirmation_id,
        "tool_call_id": step["tool_call_id"],
        "error_code": code,
    })
