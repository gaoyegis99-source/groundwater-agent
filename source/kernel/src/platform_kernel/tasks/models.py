"""Task declarations contain intent and evidence links, never execution state."""
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..contracts import DataVersionRefV2


class TaskModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        populate_by_name=False,
        serialize_by_alias=True,
    )


class TaskRequirement(TaskModel):
    id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=2000)
    source_quote: str = Field(min_length=1, max_length=2000)
    replaces: str | None = None


class TaskCriterion(TaskRequirement):
    kind: Literal["RESULT", "REPLY", "DRAFT", "USER_REVIEW"]
    model_id: str | None = None
    output_names: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def check_kind(self):
        if self.kind == "RESULT":
            if not self.model_id or not self.output_names:
                raise ValueError("TASK_RESULT_REQUIREMENTS_MISSING")
        elif self.model_id is not None or self.output_names:
            raise ValueError("TASK_CRITERION_SHAPE_INVALID")
        if len(set(self.output_names)) != len(self.output_names):
            raise ValueError("TASK_OUTPUT_DUPLICATE")
        return self


class TaskEvidence(TaskModel):
    criterion_id: str
    # null means the current AgentRun, supplied by Kernel at commit.
    agent_run_id: str | None = None
    workflow_run_id: str | None = None
    review_quote: str | None = None


class TaskStep(TaskModel):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=500)
    depends_on: list[str]
    criterion_ids: list[str] = Field(min_length=1)
    gaps: list[str]


class TaskUpdate(TaskModel):
    expected_revision: int = Field(ge=0)
    objective_quote: str | None = None
    constraints: list[TaskRequirement] = Field(default_factory=list, max_length=50)
    criteria: list[TaskCriterion] = Field(default_factory=list, max_length=50)
    suggestions: list[str] | None = Field(default=None, max_length=30)
    unresolved_items: list[str] | None = Field(default=None, max_length=30)
    steps: list[TaskStep] | None = Field(default=None, max_length=50)
    evidence: list[TaskEvidence] = Field(default_factory=list, max_length=50)


class TaskSpecV1(TaskModel):
    schema_version: Literal["task-spec.v1"] = Field(
        default="task-spec.v1", alias="schemaVersion"
    )
    task_id: str
    session_id: str
    revision: int
    objective: str
    source_goal_revision: int
    source_message_id: int
    constraints: list[dict]
    criteria: list[dict]
    suggestions: list[str]
    unresolved_items: list[str]
    steps: list[TaskStep]


class TaskProgressV1(TaskModel):
    schema_version: Literal["task-progress.v1"] = Field(
        default="task-progress.v1", alias="schemaVersion"
    )
    lifecycle: Literal["ACTIVE", "COMPLETE"]
    state: str
    criteria: list[dict]
    steps: list[dict]
    results: list[dict]
    metrics: dict
    blocking_reasons: list[str] = Field(default_factory=list)


class TaskContextV1(TaskModel):
    schema_version: Literal["task-context.v1"] = Field(
        default="task-context.v1", alias="schemaVersion"
    )
    spec: TaskSpecV1
    progress: TaskProgressV1


class TaskPreparationKnowledgeRefV1(TaskModel):
    version_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class TaskPreparationCheckV1(TaskModel):
    kind: str = Field(min_length=1)
    status: Literal["PASS", "UNKNOWN", "FAIL"]
    input: str | None = None
    version_id: str | None = None
    semantic_id: str | None = None
    current_version: str | None = None
    operator_catalog_digest: str | None = None
    count: int | None = Field(default=None, ge=0)
    parameter: str | None = Field(default=None, exclude_if=lambda value: value is None)
    identifier: Literal["fieldId", "name"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    value: str | None = Field(default=None, exclude_if=lambda value: value is None)
    field_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    field_name: str | None = Field(default=None, exclude_if=lambda value: value is None)
    actual_type: str | None = Field(default=None, exclude_if=lambda value: value is None)
    allowed_types: list[str] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )
    candidates: list[dict[str, str]] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )
    actual_unit: str | None = Field(default=None, exclude_if=lambda value: value is None)
    expected_unit: str | None = Field(default=None, exclude_if=lambda value: value is None)
    relationship: Literal[
        "DECLARED", "EQUIVALENT", "CONVERTIBLE", "NOT_COMPARABLE"
    ] | None = Field(default=None, exclude_if=lambda value: value is None)
    actual_crs: str | None = Field(default=None, exclude_if=lambda value: value is None)
    expected_crs: str | None = Field(default=None, exclude_if=lambda value: value is None)
    crs_requirement: Literal["valid", "projectedMetric"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    crs_unit: str | None = Field(default=None, exclude_if=lambda value: value is None)
    reference_field: str | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    depth_field: str | None = Field(default=None, exclude_if=lambda value: value is None)
    reference_meaning: str | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    depth_meaning: str | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    vertical_datum: str | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    timezone: str | None = Field(default=None, exclude_if=lambda value: value is None)
    time_range: str | None = Field(default=None, exclude_if=lambda value: value is None)
    requested_start: str | None = Field(default=None, exclude_if=lambda value: value is None)
    requested_end: str | None = Field(default=None, exclude_if=lambda value: value is None)


class TaskPreparationIssueV1(TaskModel):
    code: str = Field(min_length=1)
    kind: Literal[
        "MISSING_DATA",
        "MISSING_PARAMETER",
        "INVALID_PARAMETER",
        "UNRESOLVED_ITEM",
        "DATA_VERSION_NOT_CURRENT",
        "UNKNOWN",
    ]
    input: str | None = None
    parameter: str | None = None
    path: str | None = None
    validator: str | None = None
    identifier: Literal["fieldId", "name"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    value: str | None = Field(default=None, exclude_if=lambda value: value is None)
    actual_type: str | None = Field(default=None, exclude_if=lambda value: value is None)
    allowed_types: list[str] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )
    candidates: list[dict[str, str]] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )


class TaskPreparationEntryV1(TaskModel):
    target_kind: Literal["EXECUTABLE_MODEL", "WORKFLOW_DRAFT"]
    target_id: str = Field(min_length=1)
    target_version: str | None = None
    selection_policy: Literal["PINNED", "CURRENT_REQUIRED"] = "PINNED"
    status: Literal["READY", "BLOCKED"]
    inputs: dict[str, dict[str, object]] = Field(default_factory=dict)
    knowledge_refs: list[TaskPreparationKnowledgeRefV1] = Field(default_factory=list)
    parameters: dict[str, object] = Field(default_factory=dict)
    parameter_sources: dict[str, str] = Field(default_factory=dict)
    checks: list[TaskPreparationCheckV1] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    blocking_issues: list[TaskPreparationIssueV1] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_contract(self):
        for name, value in self.inputs.items():
            if not name:
                raise ValueError("TASK_PREPARATION_INPUT_NAME_INVALID")
            DataVersionRefV2.from_storage_dict(value)
        json.dumps(
            self.parameters, ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
        if self.status == "READY":
            if self.blocking_reasons or self.blocking_issues or any(
                check.status == "FAIL" for check in self.checks
            ):
                raise ValueError("TASK_PREPARATION_READY_INVALID")
            if set(self.parameter_sources) != set(self.parameters):
                raise ValueError("TASK_PREPARATION_PARAMETER_SOURCES_INVALID")
        elif not self.blocking_reasons and not self.blocking_issues:
            raise ValueError("TASK_PREPARATION_BLOCKER_REQUIRED")
        return self


class TaskPreparationV1(TaskModel):
    schema_version: Literal["task-preparation.v1"] = Field(
        default="task-preparation.v1", alias="schemaVersion"
    )
    preparation_id: str
    revision: int = Field(ge=1)
    session_id: str
    agent_run_id: str
    goal_revision: int = Field(ge=0)
    task_id: str | None = None
    base_task_revision: int | None = Field(default=None, ge=0)
    status: Literal["READY", "BLOCKED"]
    entries: list[TaskPreparationEntryV1] = Field(min_length=1)
    fresh_for_current_turn: bool = True
    disposition: Literal[
        "ACTIVE", "WAITING_CONFIRMATION", "EXECUTING", "CONSUMED",
        "REJECTED", "FAILED", "ABANDONED", "STALE",
    ] = "ACTIVE"

    @model_validator(mode="after")
    def validate_status(self):
        expected = (
            "BLOCKED"
            if any(entry.status == "BLOCKED" for entry in self.entries)
            else "READY"
        )
        if self.status != expected:
            raise ValueError("TASK_PREPARATION_STATUS_INVALID")
        return self
