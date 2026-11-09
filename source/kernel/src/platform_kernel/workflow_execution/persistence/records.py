from __future__ import annotations

import json

from ...contracts import DataVersionRefV2
from ..models import WorkflowRun


def row_to_run(row) -> WorkflowRun:
    return WorkflowRun(
        run_id=row["run_id"],
        agent_run_id=row["agent_run_id"],
        session_id=row["session_id"],
        tool_call_id=row["tool_call_id"],
        confirmation_id=row["confirmation_id"],
        attempt=row["attempt"],
        retry_of_workflow_run_id=row["retry_of_workflow_run_id"],
        position=row["position"],
        project_id=row["project_id"],
        model_id=row["model_id"],
        model_version=row["model_version"],
        model_checksum=row["model_checksum"],
        workflow_id=row["workflow_id"],
        workflow_version=row["workflow_version"],
        workflow_checksum=row["workflow_checksum"],
        execution_service=row["execution_service"],
        executor_id=row["executor_id"],
        arguments=json.loads(row["arguments_json"]),
        input_references={
            name: DataVersionRefV2.from_storage_dict(reference)
            for name, reference in json.loads(row["input_references_json"]).items()
        },
        status=row["status"],
        remote_job_id=row["remote_job_id"],
        outputs=tuple(
            DataVersionRefV2.from_storage_dict(item)
            for item in json.loads(row["outputs_json"])
        ),
        error=row["error"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        duration_ms=row["duration_ms"],
    )
