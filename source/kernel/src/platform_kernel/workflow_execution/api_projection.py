from .models import WorkflowRun
from .node_diagnostics import WorkflowNodeDiagnosticsResponse as WorkflowNodeDiagnosticsResponse


def workflow_run_response(run: WorkflowRun) -> dict[str, object]:
    return {
        "run_id": run.run_id,
        "agent_run_id": run.agent_run_id,
        "tool_call_id": run.tool_call_id,
        "position": run.position,
        "project_id": run.project_id,
        "model_id": run.model_id,
        "model_version": run.model_version,
        "model_checksum": run.model_checksum,
        "workflow_id": run.workflow_id,
        "workflow_version": run.workflow_version,
        "workflow_checksum": run.workflow_checksum,
        "execution_service": run.execution_service,
        "executor_id": run.executor_id,
        "arguments": dict(run.arguments),
        "status": run.status,
        "remote_job_id": run.remote_job_id,
        "outputs": [item.as_dict() for item in run.outputs],
        "error": run.error,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "duration_ms": run.duration_ms,
    }
