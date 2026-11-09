from typing import Any

from .models import AgentRun


def agent_run_response(run: AgentRun) -> dict:
    return {
        "agent_run_id": run.agent_run_id,
        "session_id": run.session_id,
        "project_id": run.project_id,
        "goal_revision": run.goal_revision,
        "status": run.status,
        "steps": list(run.steps),
        "plans": [
            {
                "revision": plan.revision,
                "goal": plan.goal,
                "steps": [step.model_dump() for step in plan.steps],
                "reason": plan.reason,
            }
            for plan in run.plans
        ],
        "agent_output": None if run.output is None else run.output.model_dump(),
        "actions": [
            {
                "tool_call_id": action.tool_call_id,
                "tool": action.tool,
                "model_id": action.model_id,
                "model_version": action.model_version,
                "model_checksum": action.model_checksum,
                "workflow_id": action.workflow_id,
                "workflow_version": action.workflow_version,
                "workflow_checksum": action.workflow_checksum,
                "plan_revision": action.plan_revision,
                "arguments": dict(action.arguments),
                "schema_checksum": action.schema_checksum,
                "fingerprint": action.fingerprint,
                "status": action.status,
                "workflow_run_id": action.workflow_run_id,
                "remote_outputs": [
                    reference.as_dict() for reference in action.remote_outputs
                ],
                "error": action.error,
            }
            for action in run.actions
        ],
        "workflow_run_ids": list(
            dict.fromkeys(
                action.workflow_run_id
                for action in run.actions
                if action.workflow_run_id is not None
            )
        ),
        "remote_outputs": [
            reference.as_dict()
            for action in run.actions
            for reference in action.remote_outputs
        ],
        "provider": run.provider,
        "model": run.model,
        "token_usage": {
            "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens,
            "total_tokens": run.total_tokens,
        },
        "trace_id": run.trace_id,
        "error_code": run.error,
        "timing": {
            "created_at": run.created_at,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "duration_ms": run.duration_ms,
        },
    }


def agent_trace_response(
    run: AgentRun, items: tuple[dict[str, Any], ...]
) -> dict:
    trace = next((item for item in items if item["object"] == "trace"), None)
    spans = [item for item in items if item["object"] == "trace.span"]
    return {
        "agent_run_id": run.agent_run_id,
        "trace_id": run.trace_id,
        "trace": trace,
        "spans": spans,
    }
