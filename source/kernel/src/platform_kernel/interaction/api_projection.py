from .models import Confirmation, SessionView


def session_response(view: SessionView) -> dict[str, object]:
    session = view.session
    confirmation = session.confirmation
    return {
        "session_id": session.session_id,
        "project_id": session.project_id,
        "goal_revision": session.goal_revision,
        "agent_run_id": session.agent_run_id,
        "workflow_draft_id": session.workflow_draft_id,
        "confirmation": confirmation_response(confirmation),
        "agent_output": (
            None if session.agent_output is None else session.agent_output.model_dump()
        ),
        "workflow_run_ids": list(session.workflow_run_ids),
        "remote_outputs": [item.as_dict() for item in session.remote_outputs],
        "agent_steps": list(view.agent_steps),
        "control_proposals": list(session.control_proposals),
        "task": None if session.task is None else {
            **{key: session.task["spec"][key] for key in ("task_id", "revision", "objective")},
            **{key: session.task["progress"][key] for key in ("lifecycle", "state")},
        },
    }


def confirmation_response(confirmation: Confirmation | None) -> dict | None:
    if confirmation is None:
        return None
    return {
        "confirmation_id": confirmation.confirmation_id,
        "goal_revision": confirmation.goal_revision,
        "status": confirmation.status,
        "agent_run_id": confirmation.agent_run_id,
        "reason": confirmation.reason,
        "revision": confirmation.revision,
        "kind": confirmation.kind,
        "replaces_confirmation_id": confirmation.replaces_confirmation_id,
        "source_workflow_run_id": confirmation.source_workflow_run_id,
        "request_id": confirmation.request_id,
        "created_at": confirmation.created_at,
        "actions": [{
            "tool_call_id": action.tool_call_id,
            "tool": action.tool,
            "model_id": action.model_id,
            "model_version": action.model_version,
            "model_checksum": action.model_checksum,
            "workflow_id": action.workflow_id,
            "workflow_version": action.workflow_version,
            "workflow_checksum": action.workflow_checksum,
            "input_schema_checksum": action.input_schema_checksum,
            "fingerprint": action.fingerprint,
            "arguments": dict(action.arguments),
            "input_references": {
                name: reference.as_dict()
                for name, reference in action.input_references.items()
            },
        } for action in confirmation.actions],
    }
