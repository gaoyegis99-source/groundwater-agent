from __future__ import annotations

from dataclasses import dataclass
import json
import re
import unicodedata
from typing import Annotated, Any, Literal

from ..contracts import DataVersionRefV2
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)


def _require_utf8(value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("UTF8_STRING_REQUIRED") from error
    return value


Utf8String = Annotated[StrictStr, AfterValidator(_require_utf8)]


def normalize_reply_json(value: str) -> str:
    """Apply the only pre-validation reply transforms allowed by v12."""
    parsed = json.loads(value)

    def normalize(item):
        if isinstance(item, str):
            return unicodedata.normalize("NFC", item).strip()
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items()}
        if isinstance(item, list):
            result = []
            fingerprints = set()
            for child in item:
                child = normalize(child)
                fingerprint = json.dumps(
                    child, ensure_ascii=False, allow_nan=False,
                    sort_keys=True, separators=(",", ":"),
                )
                if fingerprint not in fingerprints:
                    fingerprints.add(fingerprint)
                    result.append(child)
            return result
        return item

    return json.dumps(
        normalize(parsed), ensure_ascii=False, allow_nan=False,
        separators=(",", ":"),
    )


def reply_validation_diagnostics(error: ValidationError) -> dict[str, object]:
    stable_codes = {
        "WORKFLOW_DRAFT_DATA_BINDINGS_INVALID",
        "WORKFLOW_DRAFT_PARAMETER_SOURCES_INVALID",
    }
    issues = []
    for item in error.errors(
        include_url=False,
        include_context=True,
        include_input=False,
    ):
        path = "/" + "/".join(
            str(part).replace("~", "~0").replace("/", "~1")
            for part in item["loc"]
        )
        issue = {"path": path, "type": item["type"]}
        context = item.get("ctx")
        code = (
            str(context.get("error"))
            if isinstance(context, dict) and context.get("error") is not None
            else None
        )
        if code in stable_codes:
            issue["code"] = code
        issues.append(issue)
    return {"reason": "REPLY_SCHEMA_INVALID", "issues": issues}


class MemorySummaryV3(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goals: list[Utf8String]
    constraints: list[Utf8String]
    decisions: list[Utf8String]
    unresolved_items: list[Utf8String]


class InteractionOptionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Utf8String = Field(min_length=1, max_length=24)
    description: Utf8String = Field(min_length=1, max_length=80)
    message: Utf8String = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_trimmed_text(self):
        if any(
            value != value.strip()
            for value in (self.label, self.description, self.message)
        ):
            raise ValueError("INTERACTION_OPTION_TEXT_NOT_TRIMMED")
        _forbid_confirmation_bypass(" ".join((self.label, self.description, self.message)))
        return self


_CONFIRMATION_BYPASS = re.compile(
    r"(?:跳过|绕过|无需|不经|取消)(?:人工)?确认|直接执行|立即执行|"
    r"(?:请|立即|直接)(?:执行|运行|计算).{0,24}(?:分析|模型|工作流)|"
    r"skip\s+(?:the\s+)?confirmation|without\s+confirmation|execute\s+directly",
    re.IGNORECASE,
)
_CONFIRMATION_BYPASS_DENIAL = re.compile(
    r"(?:无法|不能|不可|不会|不要|不应|不得|尚未|未能|并未|没有|严禁)"
    r"(?:再|立刻|马上|擅自|自行|现在|当前|先)?"
    r"(?:直接执行|立即执行|绕过(?:人工)?确认|跳过(?:人工)?确认|"
    r"无需(?:人工)?确认|不经(?:人工)?确认|取消(?:人工)?确认)",
    re.IGNORECASE,
)


def _forbid_confirmation_bypass(value: str) -> None:
    # A blocked execution reply must be able to say that it cannot run or
    # bypass confirmation. Remove only compact, explicit denials before
    # checking the remaining text for an actionable bypass instruction.
    candidate = _CONFIRMATION_BYPASS_DENIAL.sub("", value)
    if _CONFIRMATION_BYPASS.search(candidate):
        raise ValueError("CONFIRMATION_BYPASS_LANGUAGE_FORBIDDEN")


class ReplySectionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    markdown: Utf8String = Field(min_length=1)
    source_indexes: list[StrictInt]

    @model_validator(mode="after")
    def require_unique_source_indexes(self):
        if len(self.source_indexes) != len(set(self.source_indexes)):
            raise ValueError("REPLY_SOURCE_INDEX_DUPLICATE")
        if any(index < 0 for index in self.source_indexes):
            raise ValueError("REPLY_SOURCE_INDEX_INVALID")
        return self


class ReplySourceV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Utf8String = Field(min_length=1)
    version_id: Utf8String = Field(min_length=1)
    unit_id: Utf8String = Field(min_length=1)
    checksum: Utf8String = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ReplyPresentationV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conclusion: ReplySectionV1 | None
    details: list[ReplySectionV1] = Field(max_length=3)
    execution_summary: ReplySectionV1 | None
    unresolved_items: list[ReplySectionV1]
    sources: list[ReplySourceV2]

    @model_validator(mode="after")
    def require_valid_sources(self):
        identities = [
            (source.version_id, source.unit_id, source.checksum)
            for source in self.sources
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("REPLY_SOURCE_DUPLICATE")
        sections = [
            *(() if self.conclusion is None else (self.conclusion,)),
            *self.details,
            *(() if self.execution_summary is None else (self.execution_summary,)),
            *self.unresolved_items,
        ]
        if any(
            index >= len(self.sources)
            for section in sections
            for index in section.source_indexes
        ):
            raise ValueError("REPLY_SOURCE_INDEX_INVALID")
        return self


class AgentFinalV5(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: Utf8String
    presentation: ReplyPresentationV1
    workflow_runs: list[Utf8String]
    unresolved_items: list[Utf8String]
    interaction_options: list[InteractionOptionV1] = Field(max_length=3)

    @model_validator(mode="after")
    def require_unique_interaction_options(self):
        _require_unique_interaction_options(self.interaction_options)
        return self


class ReplyCitationV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: Utf8String = Field(min_length=1)
    unit_id: Utf8String = Field(min_length=1)
    checksum: Utf8String = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ReplySegmentV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Utf8String = Field(min_length=1)
    citations: list[ReplyCitationV2]

    @model_validator(mode="after")
    def forbid_confirmation_bypass(self):
        _forbid_confirmation_bypass(self.text)
        return self


class _WorkflowGraphModel(BaseModel):
    """Python names stay snake_case while the Workflow v6 wire stays camelCase."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=False,
        serialize_by_alias=True,
    )


class WorkflowInputDraft(_WorkflowGraphModel):

    name: Utf8String = Field(min_length=1)
    cardinality: Literal["one"]
    media_types: list[Utf8String] = Field(alias="mediaTypes", min_length=1)
    formats: list[Utf8String] = Field(min_length=1)


class WorkflowNodeOutputDraft(_WorkflowGraphModel):

    node_id: Utf8String = Field(alias="nodeId", min_length=1)
    port: Utf8String = Field(min_length=1)


class WorkflowInputBindingDraft(_WorkflowGraphModel):

    workflow_input: Utf8String = Field(alias="workflowInput", min_length=1)


class WorkflowNodeBindingDraft(_WorkflowGraphModel):

    node_output: WorkflowNodeOutputDraft = Field(alias="nodeOutput")


class WorkflowParameterBindingDraft(_WorkflowGraphModel):

    workflow_parameter: Utf8String = Field(alias="workflowParameter", min_length=1)


class WorkflowNodeDraft(_WorkflowGraphModel):

    node_id: Utf8String = Field(alias="nodeId", min_length=1)
    operator_id: Utf8String = Field(alias="operatorId", min_length=1)
    operator_version: Utf8String = Field(alias="operatorVersion", min_length=1)
    inputs: dict[str, WorkflowInputBindingDraft | WorkflowNodeBindingDraft]
    parameters: dict[str, WorkflowParameterBindingDraft]


class WorkflowOutputDraft(_WorkflowGraphModel):

    name: Utf8String = Field(min_length=1)
    node_output: WorkflowNodeOutputDraft = Field(alias="nodeOutput")


class WorkflowGraphDraft(_WorkflowGraphModel):

    inputs: list[WorkflowInputDraft]
    parameters: list[Utf8String]
    nodes: list[WorkflowNodeDraft] = Field(min_length=1)
    outputs: list[WorkflowOutputDraft] = Field(min_length=1)


class WorkflowParameterSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Utf8String = Field(min_length=1)
    source: Literal["USER", "DATA", "KNOWLEDGE", "OPERATOR_DEFAULT"]
    explanation: Utf8String = Field(min_length=1)


class WorkflowDataReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["data_version"]
    service: Literal["data-primary"]
    dataset_id: Utf8String = Field(min_length=1)
    version_id: Utf8String = Field(min_length=1)
    semantic_id: Utf8String = Field(min_length=1)
    object_id: Utf8String = Field(min_length=1)
    checksum: Utf8String = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    media_type: Utf8String = Field(min_length=1)
    format: Utf8String = Field(min_length=1)
    size_bytes: StrictInt = Field(ge=0)
    output_name: Utf8String | None = None


class WorkflowDataBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_name: Utf8String = Field(min_length=1)
    data_ref: WorkflowDataReference


class WorkflowDraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Utf8String = Field(min_length=1)
    summary: Utf8String = Field(min_length=1)
    graph: WorkflowGraphDraft
    data_bindings: list[WorkflowDataBinding]
    parameter_values: dict[str, Any]
    parameter_sources: list[WorkflowParameterSource]
    knowledge_refs: list[ReplyCitationV2]
    unresolved_items: list[Utf8String]
    limitations: list[Utf8String]

    @model_validator(mode="after")
    def require_consistent_bindings(self):
        parameter_names = [item.name for item in self.parameter_sources]
        if (
            len(parameter_names) != len(set(parameter_names))
            or set(parameter_names) != set(self.parameter_values)
            or not set(self.parameter_values) <= set(self.graph.parameters)
        ):
            raise ValueError("WORKFLOW_DRAFT_PARAMETER_SOURCES_INVALID")
        input_names = [item.input_name for item in self.data_bindings]
        if len(input_names) != len(set(input_names)):
            raise ValueError("WORKFLOW_DRAFT_DATA_BINDINGS_INVALID")
        return self


class SubmittedReplyV6(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conclusion: ReplySegmentV1 | None
    details: list[ReplySegmentV1] = Field(max_length=3)
    unresolved_items: list[ReplySegmentV1]
    referenced_workflow_runs: list[Utf8String]
    include_execution_status: StrictBool
    interaction_options: list[InteractionOptionV1] = Field(max_length=3)
    workflow_draft: WorkflowDraftInput | None

    @model_validator(mode="after")
    def require_unique_interaction_options(self):
        _require_unique_interaction_options(self.interaction_options)
        return self


def _require_unique_interaction_options(
    options: list[InteractionOptionV1],
) -> None:
    for field in ("label", "description", "message"):
        values = [getattr(item, field) for item in options]
        if len(values) != len(set(values)):
            raise ValueError("INTERACTION_OPTIONS_DUPLICATE")


class AgentPlanStepV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: Utf8String = Field(min_length=1)
    tool: Utf8String = Field(min_length=1)
    purpose: Utf8String = Field(min_length=1)


@dataclass(frozen=True)
class AgentPlanV2:
    agent_run_id: str
    revision: int
    goal: str
    steps: tuple[AgentPlanStepV2, ...]
    reason: str


@dataclass(frozen=True)
class AgentAction:
    tool_call_id: str
    position: int
    tool: str
    model_id: str
    model_version: str
    model_checksum: str
    workflow_id: str
    workflow_version: str
    workflow_checksum: str
    plan_revision: int
    arguments: dict[str, object]
    input_references: dict[str, DataVersionRefV2]
    schema_checksum: str
    fingerprint: str
    status: str
    workflow_run_id: str | None
    remote_outputs: tuple[DataVersionRefV2, ...]
    error: str | None


@dataclass(frozen=True)
class AgentRun:
    agent_run_id: str
    session_id: str
    project_id: str
    goal_revision: int
    provider: str
    model: str
    status: str
    steps: tuple[str, ...]
    plans: tuple[AgentPlanV2, ...]
    actions: tuple[AgentAction, ...]
    output: AgentFinalV5 | None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    trace_id: str
    error: str | None
    created_at: str
    started_at: str
    finished_at: str | None
    duration_ms: int | None
