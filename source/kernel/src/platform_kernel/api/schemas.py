from __future__ import annotations

from typing import Annotated, Any, Literal

from platform_protocol_contracts import CamelModel, ProblemDetailsV11

from pydantic import (
    BeforeValidator,
    Field,
    field_validator,
    model_validator,
    StrictInt,
)


def _require_utf8(value: object) -> object:
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError("UTF8_STRING_REQUIRED") from error
    return value


_Utf8String = Annotated[str, BeforeValidator(_require_utf8)]


class SessionCreate(CamelModel):
    project_id: _Utf8String = Field(alias="projectId")


class SessionUpdate(CamelModel):
    title: _Utf8String | None = Field(default=None, min_length=1, max_length=120)
    archived: bool | None = None

    @field_validator("title")
    @classmethod
    def valid_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("SESSION_TITLE_INVALID")
        return value

    @model_validator(mode="after")
    def require_change(self):
        if self.title is None and self.archived is None:
            raise ValueError("at least one session property is required")
        return self


class MessageCreate(CamelModel):
    request_id: _Utf8String = Field(alias="requestId", min_length=1)
    message: _Utf8String = Field(min_length=1)
    requested_mode: Literal["AUTO", "DIALOGUE", "WORKFLOW_DRAFT"] = Field(
        alias="requestedMode"
    )
    corrects_agent_run_id: _Utf8String | None = Field(default=None, alias="correctsAgentRunId", min_length=1)
    corrects_request_id: _Utf8String | None = Field(
        default=None, alias="correctsRequestId", min_length=1
    )
    input_references: list["MessageInputReference"] = Field(
        default_factory=list, alias="inputReferences", max_length=20
    )

    @model_validator(mode="after")
    def validate_correction(self):
        if self.corrects_request_id is not None and self.requested_mode == "AUTO":
            raise ValueError(
                "correctsRequestId requires an explicit mode"
            )
        identities = [
            (item.version_id, item.object_id) for item in self.input_references
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate version_id/object_id input reference")
        return self


class MessageInputReference(CamelModel):
    kind: Literal["data_version"] = "data_version"
    service: Literal["data-primary"] = "data-primary"
    dataset_id: _Utf8String = Field(min_length=1)
    version_id: _Utf8String = Field(min_length=1)
    semantic_id: _Utf8String = Field(min_length=1)
    object_id: _Utf8String = Field(min_length=1)
    media_type: _Utf8String = Field(min_length=1)
    format: _Utf8String = Field(min_length=1)
    size_bytes: StrictInt = Field(ge=0)
    checksum: _Utf8String = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    output_name: _Utf8String | None = Field(default=None, min_length=1)


class StopRequest(CamelModel):
    request_id: _Utf8String = Field(alias="requestId", min_length=1)
    agent_run_id: _Utf8String = Field(alias="agentRunId", min_length=1)


class ControlProposalDecisionRequest(CamelModel):
    request_id: _Utf8String = Field(alias="requestId", min_length=1)


class ConfirmationRequest(CamelModel):
    confirmation_id: _Utf8String = Field(alias="confirmationId")


class RejectionRequest(CamelModel):
    confirmation_id: _Utf8String = Field(alias="confirmationId")
    reason: _Utf8String = Field(min_length=1)


class ConfirmationRevisionAction(CamelModel):
    tool_call_id: _Utf8String = Field(alias="toolCallId", min_length=1)
    arguments: dict[str, object]


class ConfirmationRevisionRequest(CamelModel):
    request_id: _Utf8String = Field(alias="requestId", min_length=1)
    expected_goal_revision: StrictInt = Field(alias="expectedGoalRevision", ge=0)
    actions: list[ConfirmationRevisionAction] = Field(min_length=1)


class WorkflowRecoveryRequest(CamelModel):
    request_id: _Utf8String = Field(alias="requestId", min_length=1)
    expected_goal_revision: StrictInt = Field(alias="expectedGoalRevision", ge=0)


class HealthResponse(CamelModel):
    status: str


class ReadinessResponse(HealthResponse):
    components: dict[str, Any] = Field(min_length=1)


class ObservationToolCapability(CamelModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


class ModelCapability(CamelModel):
    model_id: str
    model_version: str
    model_checksum: str
    workflow_id: str
    workflow_version: str
    workflow_checksum: str
    description: str
    tool: str
    input_schema: dict[str, Any]
    data_input_parameters: list[str]
    required_inputs: list[str]
    default_parameters: dict[str, Any]
    final_outputs: list[str]
    applicability_status: str
    deadline_seconds: int


class CurrentUser(CamelModel):
    subject: str
    display_name: str
    roles: list[str]


class CapabilitiesResponse(CamelModel):
    product_id: str
    project_id: str
    services: list[str] = Field(default_factory=list)
    orchestration_owner: Literal["AGENT"]
    observation_tools: list[ObservationToolCapability]
    models: list[ModelCapability]
    current_user: CurrentUser


class SessionSummary(CamelModel):
    session_id: str
    project_id: str
    title: str | None
    created_at: str | None
    goal_revision: int
    agent_run_id: str | None
    pending_agent_run_id: str | None
    workflow_draft_id: str | None
    archived: bool = False


class SessionMetadataResponse(CamelModel):
    session_id: str
    title: str | None
    archived: bool


class AuditEvent(CamelModel):
    event_id: int
    session_id: str
    owner_subject: str
    event_type: str
    actor_subject: str
    facts: dict[str, Any]
    created_at: str


class AuditEventPage(CamelModel):
    items: list[AuditEvent]
    next_cursor: int | None


class SessionPage(CamelModel):
    items: list[SessionSummary]
    next_cursor: int | None


class SessionResponse(CamelModel):
    session_id: str
    project_id: str
    goal_revision: int
    agent_run_id: str | None
    workflow_draft_id: str | None
    confirmation: dict[str, Any] | None
    agent_output: dict[str, Any] | None
    workflow_run_ids: list[str]
    remote_outputs: list[dict[str, Any]]
    agent_steps: list[str]
    control_proposals: list[dict[str, Any]]
    task: dict[str, Any] | None


class SessionDetailResponse(SessionResponse):
    preparation: dict[str, Any] | None


class PreparationResponse(CamelModel):
    schema_version: str
    preparation_id: str
    revision: int
    session_id: str
    agent_run_id: str
    goal_revision: int
    task_id: str | None
    base_task_revision: int | None
    status: str
    entries: list[dict[str, Any]]
    fresh_for_current_turn: bool
    disposition: str


class StopResponse(CamelModel):
    session_id: str
    request_id: str
    agent_run_id: str
    status: str


class TaskResponse(CamelModel):
    task: dict[str, Any] | None


class SessionResultsResponse(CamelModel):
    session_id: str
    project_id: str
    items: list[dict[str, Any]]
    next_cursor: int | None


class MessageReceiptResponse(CamelModel):
    schema_version: str
    request: dict[str, Any]
    request_id: str
    session_id: str
    goal_revision: int
    agent_run_id: str
    requested_mode: str
    routing_source: str
    routing_status: str
    intent_decision: str | None
    intent: dict[str, Any] | None
    semantic_intent: str | None
    task_action: str | None
    control_intent: str | None
    clarification_reason: str | None
    dialogue_policy: str | None
    resolved_mode: str | None
    clarification_required: bool
    workflow_draft_id: str | None
    corrects_request_id: str | None
    corrects_agent_run_id: str | None = None
    error_code: str | None


class MessageHistoryResponse(CamelModel):
    session_id: str
    goal_revision: int
    items: list[dict[str, Any]]
    turns: list[dict[str, Any]]
    next_cursor: int | None
    event_cursor: int
    snapshot_at: str
    task: dict[str, Any] | None
    preparation: dict[str, Any] | None
    control_requests: list[dict[str, Any]]
    control_proposals: list[dict[str, Any]]
    control_messages: list[dict[str, Any]]


class WorkflowDraftResponse(CamelModel):
    schema_version: str
    draft_id: str
    session_id: str
    agent_run_id: str
    goal_revision: int
    payload: dict[str, Any] | None
    status: str
    error_code: str | None
    diagnostics: dict[str, Any] | None
    checksum: str | None = None
    created_at: str | None = None


class AgentRunResponse(CamelModel):
    agent_run_id: str
    session_id: str
    project_id: str
    goal_revision: int
    status: str
    steps: list[str]
    plans: list[dict[str, Any]]
    agent_output: dict[str, Any] | None
    actions: list[dict[str, Any]]
    workflow_run_ids: list[str]
    remote_outputs: list[dict[str, Any]]
    provider: str
    model: str
    token_usage: dict[str, int]
    trace_id: str
    error_code: str | None
    timing: dict[str, Any]


class AgentTraceResponse(CamelModel):
    agent_run_id: str
    trace_id: str
    trace: dict[str, Any] | None
    spans: list[dict[str, Any]]


class ProviderRequestPage(CamelModel):
    agent_run_id: str
    items: list[dict[str, Any]]
    next_cursor: str | None = None


class WorkflowRunResponse(CamelModel):
    run_id: str
    agent_run_id: str
    tool_call_id: str
    position: int
    project_id: str
    model_id: str
    model_version: str
    model_checksum: str
    workflow_id: str
    workflow_version: str
    workflow_checksum: str
    execution_service: str
    executor_id: str
    arguments: dict[str, Any]
    status: str
    remote_job_id: str | None
    outputs: list[dict[str, Any]]
    error: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    duration_ms: int | None


PROBLEM_RESPONSE = {
    "model": ProblemDetailsV11,
    "content": {
        "application/problem+json": {
            "schema": {"$ref": "#/components/schemas/ProblemDetailsV11"}
        }
    },
}
