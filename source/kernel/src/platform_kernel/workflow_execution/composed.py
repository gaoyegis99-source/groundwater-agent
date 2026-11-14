"""Resolve a composed action through its immutable Session draft."""

from __future__ import annotations

from platform_workflow_compiler import WorkflowValidationError
from platform_workflow_compiler.composition.execution import ArtifactComposition, execution_arguments

from ..contracts import DataVersionRefV2
from ..application.workflow_api import frozen_composition
from ..errors import WorkflowDefinitionUnavailableError
from ..settings import ExecutableModelDefinition


def resolve_composed(connection, profile: ArtifactComposition | None, *, agent_run_id: str, session_id: str,
                     project_id: str, action) -> tuple[ExecutableModelDefinition, dict]:
    if profile is None:
        raise WorkflowDefinitionUnavailableError("WORKFLOW_DEFINITION_UNAVAILABLE")
    definition = frozen_composition(connection, agent_run_id, session_id, project_id)
    if not isinstance(definition, dict):
        raise WorkflowDefinitionUnavailableError("WORKFLOW_DEFINITION_UNAVAILABLE")
    try:
        model, _ = profile.compile(definition, project_id)
    except WorkflowValidationError as error:
        raise WorkflowDefinitionUnavailableError("WORKFLOW_DEFINITION_UNAVAILABLE") from error
    arguments, references = execution_arguments(definition)
    if (
        arguments != action.arguments
        or {name: DataVersionRefV2.from_dict(ref) for name, ref in references.items()} != action.input_references
    ):
        raise WorkflowDefinitionUnavailableError("WORKFLOW_DEFINITION_UNAVAILABLE")
    reference = model["workflowRef"]
    return ExecutableModelDefinition(
        model_id=model["modelId"], model_version=model["version"], model_checksum=model["modelChecksum"],
        workflow_id=reference["workflowId"], workflow_version=reference["version"], workflow_checksum=reference["checksum"],
        tool_name=model["tool"]["name"], tool_description=model["tool"]["description"],
        input_schema=model["tool"]["inputSchema"], input_schema_checksum=model["inputSchemaChecksum"],
        default_parameters={}, execution_service=model["executionService"], executor_id=model["executorId"],
        deadline_seconds=model["deadlineSeconds"], final_outputs=tuple(model["finalOutputs"]),
        data_input_parameters=tuple(model["inputBindings"]),
    ), definition
