from datetime import datetime
import re
from typing import Literal

from platform_protocol_contracts import CamelModel
from pydantic import Field, StrictInt


class WorkflowNodeDiagnostic(CamelModel):
    node_id: str = Field(min_length=1, max_length=256)
    operator_id: str = Field(min_length=1, max_length=256)
    runtime: Literal["native", "qgis", "remote_http"]
    status: Literal["WAITING", "READY", "LEASED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "SKIPPED"]
    attempt_count: StrictInt | None = Field(ge=0)
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None = Field(pattern=r"^[A-Z][A-Z0-9_]{0,95}$")

    @classmethod
    def from_executor(cls, row: dict) -> "WorkflowNodeDiagnostic":
        error = row.get("error")
        if error is not None and (
            not isinstance(error, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", error)
        ):
            error = "WORKFLOW_NODE_FAILED"
        return cls.model_validate({
            "nodeId": row.get("nodeId"),
            "operatorId": row.get("operatorId"),
            "runtime": row.get("runtime"),
            "status": row.get("status"),
            "attemptCount": row.get("attemptCount"),
            "startedAt": row.get("startedAt"),
            "finishedAt": row.get("finishedAt"),
            "errorCode": error,
        })


class WorkflowNodeDiagnosticsResponse(CamelModel):
    workflow_run_id: str
    availability: Literal["NOT_DISPATCHED", "UNAVAILABLE", "AVAILABLE"]
    nodes: list[WorkflowNodeDiagnostic]


class WorkflowGraphNode(CamelModel):
    node_id: str
    depends_on: list[str]


class WorkflowGraphResponse(CamelModel):
    workflow_run_id: str
    availability: Literal["NOT_DISPATCHED", "UNAVAILABLE", "AVAILABLE"]
    nodes: list[WorkflowGraphNode]
