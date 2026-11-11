"""Immutable definitions loaded from the compiled Kernel manifest."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceBinding:
    name: str
    internal_base_url: str


@dataclass(frozen=True)
class ToolOutcomeProgressDefinition:
    summary_template: str
    facts_schema: dict[str, object]


@dataclass(frozen=True)
class ToolProgressDefinition:
    capability: str
    semantic_role: str
    template_key: str
    title: str
    running_summary_template: str
    processing_title: str
    processing_summary_template: str
    outcomes: dict[str, ToolOutcomeProgressDefinition]


@dataclass(frozen=True)
class ObservationToolDefinition:
    name: str
    description: str
    service: str
    path: str
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    input_schema_checksum: str
    output_schema_checksum: str
    timeout_seconds: int
    contract: str | None = None
    progress: ToolProgressDefinition | None = None


@dataclass(frozen=True)
class ExecutableModelFieldRequirement:
    """One model argument that names a field in one exact Data input."""

    parameter: str
    input_parameter: str
    identifier: str
    allowed_types: tuple[str, ...]


@dataclass(frozen=True)
class ExecutableModelSemanticRequirement:
    """One declared unit, CRS, or temporal fact required by a model input."""

    kind: str
    input_parameter: str | None = None
    field_parameter: str | None = None
    expected_parameter: str | None = None
    start_parameter: str | None = None
    end_parameter: str | None = None
    parameter: str | None = None
    crs_requirement: str | None = None
    reference_field_parameter: str | None = None
    depth_field_parameter: str | None = None
    reference_meanings: tuple[str, ...] = ()
    depth_meanings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutableModelDefinition:
    """Kernel's callable model projection; the Workflow DAG belongs to Executor."""

    model_id: str
    model_version: str
    model_checksum: str
    workflow_id: str
    workflow_version: str
    tool_name: str
    tool_description: str
    input_schema: dict[str, object]
    default_parameters: dict[str, object]
    execution_service: str
    executor_id: str
    deadline_seconds: int
    workflow_checksum: str
    input_schema_checksum: str
    final_outputs: tuple[str, ...]
    data_input_parameters: tuple[str, ...]
    field_requirements: tuple[ExecutableModelFieldRequirement, ...] = ()
    semantic_requirements: tuple[ExecutableModelSemanticRequirement, ...] = ()


@dataclass(frozen=True)
class ExecutionCaseInputRole:
    role: str
    parameter: str


@dataclass(frozen=True)
class ExecutionCaseDefinition:
    case_id: str
    aliases: tuple[str, ...]
    model_id: str
    model_version: str
    dataset_tag: str
    input_roles: tuple[ExecutionCaseInputRole, ...]
    default_profile: str
    profiles: dict[str, dict[str, object]]
