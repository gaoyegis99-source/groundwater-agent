from __future__ import annotations

from typing import Any, Literal

from pydantic import Field
from platform_protocol_contracts import CamelModel, to_camel


class DataVersionReferenceV2(CamelModel):
    kind: str
    service: str
    dataset_id: str
    version_id: str
    semantic_id: str
    object_id: str
    media_type: str
    format: str
    size_bytes: int = Field(ge=0)
    checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    output_name: str | None = None


class JobRequestBodyV6(CamelModel):
    request_id: str
    project_id: str
    model_id: str
    model_version: str
    model_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    workflow_id: str
    workflow_version: str
    workflow_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    executor_id: str
    arguments: dict[str, Any]
    input_references: dict[str, DataVersionReferenceV2]
    deadline_at: str
    composition: dict[str, Any] | None = None


class JobStatusResponseV6(CamelModel):
    job_id: str
    request_id: str
    status: str
    stage: str | None
    error: str | None
    outputs: list[DataVersionReferenceV2]
    service_version: str | None
    created_at: str | None
    started_at: str | None
    finished_at: str | None


class JobPublicationResponse(CamelModel):
    job_id: str
    request_id: str
    publication_id: str | None = None
    state: Literal["OPEN", "PREPARING", "READY", "PUBLISHED", "FAILED", "ABORTED"] | None = None


class HealthResponse(CamelModel):
    status: str


class ReadinessResponse(HealthResponse):
    definition_set_digest: str
    components: dict[str, str]


class NodeDiagnosticsResponse(CamelModel):
    nodes: list[dict[str, Any]]


class JobGraphNode(CamelModel):
    node_id: str
    depends_on: list[str]


class JobGraphResponse(CamelModel):
    request_id: str
    workflow_id: str
    workflow_version: str
    workflow_checksum: str
    nodes: list[JobGraphNode]


class WorkerIdentityV3(CamelModel):
    worker_id: str = Field(min_length=1)
    runtime: str = Field(min_length=1)
    catalog_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    slots: int = Field(ge=1)
    memory_bytes: int = Field(ge=1)


class RemoteLeaseStateV3(CamelModel):
    remote_task_id: str | None = None
    request_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=128)
    remote_status: str = Field(min_length=1)
    last_polled_at: str | None = None
    remote_termination_confirmed: bool | None = None


class LeaseHeartbeatV3(CamelModel):
    lease_token: str = Field(min_length=1)
    remote_state: RemoteLeaseStateV3 | None = None


class WorkerOutputV3(CamelModel):
    port: str = Field(min_length=1)
    path: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    format: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class LeaseCompletionV3(LeaseHeartbeatV3):
    outputs: list[WorkerOutputV3]
    values: dict[str, Any]
    stdout: str
    stderr: str


class LeaseFailureV3(LeaseHeartbeatV3):
    error_code: str = Field(min_length=1)
    stdout: str
    stderr: str


class TaskLeaseV3(CamelModel):
    lease_id: str
    lease_token: str
    lease_expires_at: str
    job_id: str
    node_id: str
    attempt: int
    operator_id: str
    operator_version: str
    implementation_revision: str
    runtime: str
    timeout_seconds: int
    memory_limit_bytes: int
    parameters: dict[str, Any]
    inputs: dict[str, Any]
    workspace: str
    output_contracts: list[dict[str, Any]]
    traceparent: str = Field(
        pattern=r"^[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$"
    )
    remote_binding: dict[str, str]
    remote_state: dict[str, Any]


class LeaseClaimResponseV3(CamelModel):
    lease: TaskLeaseV3 | None


_NAMED_MAPS = {"inputs", "parameters", "values"}


def lease_to_wire(value: object, *, parent: str | None = None) -> object:
    if isinstance(value, list | tuple):
        return [lease_to_wire(item) for item in value]
    if not isinstance(value, dict):
        return value
    if parent in _NAMED_MAPS:
        return {str(key): lease_to_wire(item) for key, item in value.items()}
    return {
        to_camel(str(key)): lease_to_wire(item, parent=str(key))
        for key, item in value.items()
    }
