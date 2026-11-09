from __future__ import annotations

import json
import re
import sqlite3

from ...contracts import DataVersionRefV2
from ..models import AgentAction, AgentFinalV5, AgentPlanStepV2, AgentPlanV2, AgentRun


def stable_error(value: str | None) -> str | None:
    if value is None:
        return None
    if re.fullmatch(r"[A-Z][A-Z0-9_]*", value):
        return value
    return "WORKFLOW_FAILED"


def action_status(decision_status: str, workflow_status: str | None) -> str:
    if decision_status == "APPROVED":
        if workflow_status is None:
            raise ValueError("AGENT_ACTION_WORKFLOW_MISSING")
        if workflow_status == "SUCCEEDED":
            return "COMPLETE"
        if workflow_status in {"FAILED", "TIMED_OUT", "CANCELLED"}:
            return "FAILED"
        return workflow_status
    if workflow_status is not None:
        raise ValueError("AGENT_ACTION_WORKFLOW_UNEXPECTED")
    return decision_status


def project_run(connection: sqlite3.Connection, row) -> AgentRun:
    plan_rows = connection.execute(
        "SELECT * FROM agent_plans WHERE agent_run_id = ? ORDER BY revision",
        (row["agent_run_id"],),
    ).fetchall()
    action_rows = connection.execute(
        """SELECT aa.*, wr.run_id AS workflow_run_id,
                  wr.status AS workflow_status, wr.outputs_json,
                  wr.error AS workflow_error
           FROM agent_actions AS aa
           LEFT JOIN workflow_runs AS wr ON wr.agent_run_id = aa.agent_run_id
             AND wr.tool_call_id = aa.tool_call_id
             AND wr.attempt = (SELECT MAX(w2.attempt) FROM workflow_runs w2
                               WHERE w2.agent_run_id=aa.agent_run_id
                                 AND w2.tool_call_id=aa.tool_call_id)
           WHERE aa.agent_run_id = ? ORDER BY aa.position""",
        (row["agent_run_id"],),
    ).fetchall()
    plans = tuple(
        AgentPlanV2(
            agent_run_id=item["agent_run_id"],
            revision=item["revision"],
            goal=item["goal"],
            steps=tuple(
                AgentPlanStepV2.model_validate(step)
                for step in json.loads(item["steps_json"])
            ),
            reason=item["reason"],
        )
        for item in plan_rows
    )
    actions = tuple(
        AgentAction(
            tool_call_id=item["tool_call_id"],
            position=item["position"],
            tool=item["tool"],
            model_id=item["model_id"],
            model_version=item["model_version"],
            model_checksum=item["model_checksum"],
            workflow_id=item["workflow_id"],
            workflow_version=item["workflow_version"],
            workflow_checksum=item["workflow_checksum"],
            plan_revision=item["plan_revision"],
            arguments=json.loads(item["arguments_json"]),
            input_references={
                name: DataVersionRefV2.from_storage_dict(reference)
                for name, reference in json.loads(item["input_references_json"]).items()
            },
            schema_checksum=item["schema_checksum"],
            fingerprint=item["fingerprint"],
            status=action_status(item["status"], item["workflow_status"]),
            workflow_run_id=item["workflow_run_id"],
            remote_outputs=tuple(
                DataVersionRefV2.from_storage_dict(value)
                for value in json.loads(item["outputs_json"] or "[]")
            ),
            error=item["workflow_error"] if item["workflow_status"] else item["error"],
        )
        for item in action_rows
    )
    return AgentRun(
        agent_run_id=row["agent_run_id"],
        session_id=row["session_id"],
        project_id=row["project_id"],
        goal_revision=row["goal_revision"],
        provider=row["provider"],
        model=row["model"],
        status=row["status"],
        steps=tuple(json.loads(row["steps_json"])),
        plans=plans,
        actions=actions,
        output=(
            None
            if row["output_json"] is None
            else AgentFinalV5.model_validate_json(row["output_json"])
        ),
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        total_tokens=row["total_tokens"],
        trace_id=row["trace_id"],
        error=row["error"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        duration_ms=row["duration_ms"],
    )
