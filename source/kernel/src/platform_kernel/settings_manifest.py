"""Strict parsers for compiled Kernel manifest sections."""

from __future__ import annotations

from .settings_types import (
    ExecutableModelDefinition,
    ExecutableModelFieldRequirement,
    ExecutableModelSemanticRequirement,
    ExecutionCaseDefinition,
    ExecutionCaseInputRole,
    ObservationToolDefinition,
    ServiceBinding,
    ToolOutcomeProgressDefinition,
    ToolProgressDefinition,
)
from .settings_values import _checksum, _digest, _mapping, _text
from .tool_progress import validate_progress_definition


def _parse_tool_progress(value: object) -> ToolProgressDefinition:
    if not isinstance(value, dict) or set(value) != {
        "capability", "semanticRole", "templateKey", "title",
        "runningSummaryTemplate", "processingTitle",
        "processingSummaryTemplate", "outcomes",
    }:
        raise ValueError("KERNEL_CONFIG_INVALID")
    outcomes = value["outcomes"]
    if not isinstance(outcomes, dict) or not outcomes:
        raise ValueError("KERNEL_CONFIG_INVALID")
    parsed_outcomes: dict[str, ToolOutcomeProgressDefinition] = {}
    for code, outcome in outcomes.items():
        if (
            not isinstance(code, str)
            or not isinstance(outcome, dict)
            or set(outcome) != {"summary", "factsSchema"}
        ):
            raise ValueError("KERNEL_CONFIG_INVALID")
        parsed_outcomes[code] = ToolOutcomeProgressDefinition(
            summary_template=_text(outcome["summary"], "KERNEL_CONFIG_INVALID"),
            facts_schema=_mapping(outcome["factsSchema"]),
        )
    progress = ToolProgressDefinition(
        capability=_text(value["capability"], "KERNEL_CONFIG_INVALID"),
        semantic_role=_text(value["semanticRole"], "KERNEL_CONFIG_INVALID"),
        template_key=_text(value["templateKey"], "KERNEL_CONFIG_INVALID"),
        title=_text(value["title"], "KERNEL_CONFIG_INVALID"),
        running_summary_template=_text(
            value["runningSummaryTemplate"], "KERNEL_CONFIG_INVALID"
        ),
        processing_title=_text(value["processingTitle"], "KERNEL_CONFIG_INVALID"),
        processing_summary_template=_text(
            value["processingSummaryTemplate"], "KERNEL_CONFIG_INVALID"
        ),
        outcomes=parsed_outcomes,
    )
    try:
        validate_progress_definition(progress)
    except (TypeError, ValueError) as error:
        raise ValueError("KERNEL_CONFIG_INVALID") from error
    return progress


def _parse_services(value: object) -> tuple[ServiceBinding, ...]:
    if not isinstance(value, dict):
        raise ValueError("KERNEL_CONFIG_INVALID")
    services = []
    for name, config in value.items():
        if (
            not isinstance(name, str)
            or not isinstance(config, dict)
            or set(config) != {"internalBaseUrl"}
        ):
            raise ValueError("KERNEL_CONFIG_INVALID")
        services.append(
            ServiceBinding(
                _text(name, "KERNEL_CONFIG_INVALID"),
                _text(config["internalBaseUrl"], "KERNEL_CONFIG_INVALID").rstrip("/"),
            )
        )
    return tuple(services)


def _parse_execution_cases(
    value: object,
    models: tuple[ExecutableModelDefinition, ...],
) -> tuple[ExecutionCaseDefinition, ...]:
    if not isinstance(value, list):
        raise ValueError("KERNEL_CONFIG_INVALID")
    model_index = {(item.model_id, item.model_version): item for item in models}
    cases: list[ExecutionCaseDefinition] = []
    case_ids: set[str] = set()
    for item in value:
        required = {
            "schemaVersion", "caseId", "aliases", "modelRef", "datasetTag",
            "inputRoles", "defaultProfile", "profiles",
        }
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError("KERNEL_CONFIG_INVALID")
        model_ref = item["modelRef"]
        if not isinstance(model_ref, dict) or set(model_ref) != {"modelId", "version"}:
            raise ValueError("KERNEL_CONFIG_INVALID")
        model = model_index.get((model_ref["modelId"], model_ref["version"]))
        aliases = item["aliases"]
        roles = item["inputRoles"]
        profiles = item["profiles"]
        if (
            item["schemaVersion"] != "execution-case.v1"
            or not isinstance(item["caseId"], str) or not item["caseId"]
            or item["caseId"] in case_ids
            or model is None
            or not isinstance(item["datasetTag"], str) or not item["datasetTag"]
            or not isinstance(aliases, list) or not aliases
            or any(not isinstance(alias, str) or not alias for alias in aliases)
            or len(set(aliases)) != len(aliases)
            or not isinstance(roles, list) or not roles
            or not isinstance(profiles, dict) or not profiles
            or item["defaultProfile"] not in profiles
        ):
            raise ValueError("KERNEL_CONFIG_INVALID")
        parsed_roles: list[ExecutionCaseInputRole] = []
        seen_roles: set[str] = set()
        seen_parameters: set[str] = set()
        for role in roles:
            if not isinstance(role, dict) or set(role) != {"role", "parameter"}:
                raise ValueError("KERNEL_CONFIG_INVALID")
            role_name, parameter = role["role"], role["parameter"]
            if (
                not isinstance(role_name, str) or not role_name
                or not isinstance(parameter, str) or not parameter
                or role_name in seen_roles or parameter in seen_parameters
                or parameter not in model.data_input_parameters
            ):
                raise ValueError("KERNEL_CONFIG_INVALID")
            seen_roles.add(role_name)
            seen_parameters.add(parameter)
            parsed_roles.append(ExecutionCaseInputRole(role_name, parameter))
        if seen_parameters != set(model.data_input_parameters):
            raise ValueError("KERNEL_CONFIG_INVALID")
        parsed_profiles: dict[str, dict[str, object]] = {}
        for name, profile in profiles.items():
            if not isinstance(name, str) or not name or not isinstance(profile, dict):
                raise ValueError("KERNEL_CONFIG_INVALID")
            parsed_profiles[name] = dict(profile)
        case_ids.add(item["caseId"])
        cases.append(ExecutionCaseDefinition(
            case_id=item["caseId"], aliases=tuple(aliases),
            model_id=model.model_id, model_version=model.model_version,
            dataset_tag=item["datasetTag"], input_roles=tuple(parsed_roles),
            default_profile=item["defaultProfile"], profiles=parsed_profiles,
        ))
    return tuple(cases)


def _parse_tools(
    value: object, services: tuple[ServiceBinding, ...]
) -> tuple[ObservationToolDefinition, ...]:
    if not isinstance(value, list):
        raise ValueError("KERNEL_CONFIG_INVALID")
    service_names = {item.name for item in services}
    fields = {
        "name",
        "description",
        "contract",
        "service",
        "path",
        "inputSchema",
        "outputSchema",
        "timeoutSeconds",
        "inputSchemaChecksum",
        "outputSchemaChecksum",
        "progress",
    }
    tools = []
    for item in value:
        if not isinstance(item, dict) or set(item) != fields:
            raise ValueError("KERNEL_CONFIG_INVALID")
        if _text(item["service"], "KERNEL_CONFIG_INVALID") not in service_names:
            raise ValueError("KERNEL_CONFIG_INVALID")
        if _checksum(item["inputSchema"]) != item["inputSchemaChecksum"]:
            raise ValueError("KERNEL_CONFIG_INVALID")
        if _checksum(item["outputSchema"]) != item["outputSchemaChecksum"]:
            raise ValueError("KERNEL_CONFIG_INVALID")
        timeout = item["timeoutSeconds"]
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise ValueError("KERNEL_CONFIG_INVALID")
        tools.append(
            ObservationToolDefinition(
                name=_text(item["name"], "KERNEL_CONFIG_INVALID"),
                description=_text(item["description"], "KERNEL_CONFIG_INVALID"),
                service=item["service"],
                path=_text(item["path"], "KERNEL_CONFIG_INVALID"),
                input_schema=_mapping(item["inputSchema"]),
                output_schema=_mapping(item["outputSchema"]),
                input_schema_checksum=item["inputSchemaChecksum"],
                output_schema_checksum=item["outputSchemaChecksum"],
                timeout_seconds=timeout,
                contract=_text(item["contract"], "KERNEL_CONFIG_INVALID"),
                progress=_parse_tool_progress(item["progress"]),
            )
        )
    if len({item.name for item in tools}) != len(tools):
        raise ValueError("KERNEL_CONFIG_INVALID")
    return tuple(tools)


def _parse_models(
    value: object, services: tuple[ServiceBinding, ...]
) -> tuple[ExecutableModelDefinition, ...]:
    if not isinstance(value, list):
        raise ValueError("KERNEL_CONFIG_INVALID")
    service_names = {item.name for item in services}
    fields = {
        "modelId",
        "version",
        "workflowRef",
        "tool",
        "defaultParameters",
        "executionService",
        "executorId",
        "deadlineSeconds",
        "finalOutputs",
        "dataInputParameters",
        "fieldRequirements",
        "semanticRequirements",
        "modelChecksum",
        "inputSchemaChecksum",
    }
    models = []
    for item in value:
        if (
            not isinstance(item, dict)
            or set(item) != fields
            or not isinstance(item["executionService"], str)
            or item["executionService"] not in service_names
        ):
            raise ValueError("KERNEL_CONFIG_INVALID")
        _digest(item["modelChecksum"])
        workflow = item["workflowRef"]
        if not isinstance(workflow, dict) or set(workflow) != {"workflowId", "version", "checksum"}:
            raise ValueError("KERNEL_CONFIG_INVALID")
        tool = item["tool"]
        if not isinstance(tool, dict) or set(tool) != {
            "name",
            "description",
            "inputSchema",
        }:
            raise ValueError("KERNEL_CONFIG_INVALID")
        if _checksum(tool["inputSchema"]) != item["inputSchemaChecksum"]:
            raise ValueError("KERNEL_CONFIG_INVALID")
        _digest(workflow["checksum"])
        deadline = item["deadlineSeconds"]
        if isinstance(deadline, bool) or not isinstance(deadline, int) or deadline <= 0:
            raise ValueError("KERNEL_CONFIG_INVALID")
        final_outputs = item["finalOutputs"]
        if not isinstance(final_outputs, list):
            raise ValueError("KERNEL_CONFIG_INVALID")
        data_parameters = item["dataInputParameters"]
        if (not isinstance(data_parameters, list)
                or any(not isinstance(name, str) or name not in tool["inputSchema"]["properties"]
                       for name in data_parameters)
                or len(set(data_parameters)) != len(data_parameters)):
            raise ValueError("KERNEL_CONFIG_INVALID")
        field_requirements = item["fieldRequirements"]
        allowed_field_types = {
            "any", "boolean", "date-time", "integer", "number", "string"
        }
        if not isinstance(field_requirements, list):
            raise ValueError("KERNEL_CONFIG_INVALID")
        parsed_field_requirements = []
        seen_field_parameters = set()
        for requirement in field_requirements:
            if (
                not isinstance(requirement, dict)
                or set(requirement) != {
                    "parameter", "inputParameter", "identifier", "allowedTypes"
                }
                or not isinstance(requirement["parameter"], str)
                or requirement["parameter"] not in tool["inputSchema"]["properties"]
                or requirement["parameter"] in seen_field_parameters
                or not isinstance(requirement["inputParameter"], str)
                or requirement["inputParameter"] not in data_parameters
                or not isinstance(requirement["identifier"], str)
                or requirement["identifier"] not in {"fieldId", "name"}
                or not isinstance(requirement["allowedTypes"], list)
                or not requirement["allowedTypes"]
                or any(
                    not isinstance(field_type, str)
                    for field_type in requirement["allowedTypes"]
                )
                or len(set(requirement["allowedTypes"]))
                != len(requirement["allowedTypes"])
                or not set(requirement["allowedTypes"]) <= allowed_field_types
            ):
                raise ValueError("KERNEL_CONFIG_INVALID")
            seen_field_parameters.add(requirement["parameter"])
            parsed_field_requirements.append(
                ExecutableModelFieldRequirement(
                    parameter=requirement["parameter"],
                    input_parameter=requirement["inputParameter"],
                    identifier=requirement["identifier"],
                    allowed_types=tuple(requirement["allowedTypes"]),
                )
            )
        semantic_requirements = item["semanticRequirements"]
        if not isinstance(semantic_requirements, list):
            raise ValueError("KERNEL_CONFIG_INVALID")
        parsed_semantic_requirements = []
        seen_semantic_requirements = set()
        field_inputs = {
            (requirement.parameter, requirement.input_parameter)
            for requirement in parsed_field_requirements
        }
        available_arguments = set(tool["inputSchema"].get("required", [])) | set(
            item["defaultParameters"]
        )
        for requirement in semantic_requirements:
            if not isinstance(requirement, dict):
                raise ValueError("KERNEL_CONFIG_INVALID")
            kind = requirement.get("kind")
            input_parameter = requirement.get("inputParameter")
            if (
                kind not in {
                    "fieldUnit", "crs", "crsParameter", "timeField", "timeRange",
                    "verticalBasis",
                }
                or (
                    kind != "crsParameter"
                    and (
                        not isinstance(input_parameter, str)
                        or input_parameter not in data_parameters
                    )
                )
            ):
                raise ValueError("KERNEL_CONFIG_INVALID")
            field_parameter = None
            expected_parameter = None
            start_parameter = None
            end_parameter = None
            parameter = None
            crs_requirement = None
            reference_field_parameter = None
            depth_field_parameter = None
            reference_meanings = None
            depth_meanings = None
            if kind == "fieldUnit":
                if set(requirement) not in (
                    {"kind", "inputParameter", "fieldParameter"},
                    {"kind", "inputParameter", "fieldParameter", "unitParameter"},
                ):
                    raise ValueError("KERNEL_CONFIG_INVALID")
                field_parameter = requirement.get("fieldParameter")
                expected_parameter = requirement.get("unitParameter")
            elif kind == "crs":
                if set(requirement) not in (
                    {"kind", "inputParameter"},
                    {"kind", "inputParameter", "crsParameter"},
                ):
                    raise ValueError("KERNEL_CONFIG_INVALID")
                expected_parameter = requirement.get("crsParameter")
            elif kind == "crsParameter":
                if set(requirement) != {"kind", "parameter", "requirement"}:
                    raise ValueError("KERNEL_CONFIG_INVALID")
                input_parameter = None
                parameter = requirement.get("parameter")
                crs_requirement = requirement.get("requirement")
            elif kind == "verticalBasis":
                if set(requirement) != {
                    "kind", "inputParameter", "referenceFieldParameter",
                    "depthFieldParameter", "referenceMeanings", "depthMeanings",
                }:
                    raise ValueError("KERNEL_CONFIG_INVALID")
                reference_field_parameter = requirement.get(
                    "referenceFieldParameter"
                )
                depth_field_parameter = requirement.get("depthFieldParameter")
                reference_meanings = requirement.get("referenceMeanings")
                depth_meanings = requirement.get("depthMeanings")
            elif kind == "timeField":
                if set(requirement) != {
                    "kind", "inputParameter", "fieldParameter"
                }:
                    raise ValueError("KERNEL_CONFIG_INVALID")
                field_parameter = requirement.get("fieldParameter")
            else:
                if set(requirement) != {
                    "kind", "inputParameter", "fieldParameter",
                    "startParameter", "endParameter",
                }:
                    raise ValueError("KERNEL_CONFIG_INVALID")
                field_parameter = requirement.get("fieldParameter")
                start_parameter = requirement.get("startParameter")
                end_parameter = requirement.get("endParameter")
            needs_field = kind in {"fieldUnit", "timeField", "timeRange"}
            if (
                (needs_field and (
                    not isinstance(field_parameter, str)
                    or (field_parameter, input_parameter) not in field_inputs
                ))
                or (expected_parameter is not None and (
                    not isinstance(expected_parameter, str)
                    or expected_parameter not in available_arguments
                ))
                or (kind == "timeRange" and (
                    not isinstance(start_parameter, str)
                    or start_parameter not in available_arguments
                    or not isinstance(end_parameter, str)
                    or end_parameter not in available_arguments
                ))
                or (kind == "crsParameter" and (
                    not isinstance(parameter, str)
                    or parameter not in available_arguments
                    or crs_requirement not in {"valid", "projectedMetric"}
                ))
                or (kind == "verticalBasis" and (
                    not isinstance(reference_field_parameter, str)
                    or (reference_field_parameter, input_parameter)
                    not in field_inputs
                    or not isinstance(depth_field_parameter, str)
                    or (depth_field_parameter, input_parameter) not in field_inputs
                    or reference_field_parameter == depth_field_parameter
                    or not isinstance(reference_meanings, list)
                    or not reference_meanings
                    or any(
                        not isinstance(value, str) or not value
                        for value in reference_meanings
                    )
                    or len(reference_meanings) != len(set(reference_meanings))
                    or len(reference_meanings)
                    != len({value.casefold() for value in reference_meanings})
                    or not isinstance(depth_meanings, list)
                    or not depth_meanings
                    or any(
                        not isinstance(value, str) or not value
                        for value in depth_meanings
                    )
                    or len(depth_meanings) != len(set(depth_meanings))
                    or len(depth_meanings)
                    != len({value.casefold() for value in depth_meanings})
                    or not {
                        value.casefold() for value in reference_meanings
                    }.isdisjoint({value.casefold() for value in depth_meanings})
                ))
            ):
                raise ValueError("KERNEL_CONFIG_INVALID")
            identity = (
                kind, input_parameter, field_parameter, expected_parameter,
                start_parameter, end_parameter, parameter, crs_requirement,
                reference_field_parameter, depth_field_parameter,
                tuple(reference_meanings or ()), tuple(depth_meanings or ()),
            )
            if identity in seen_semantic_requirements:
                raise ValueError("KERNEL_CONFIG_INVALID")
            seen_semantic_requirements.add(identity)
            parsed_semantic_requirements.append(
                ExecutableModelSemanticRequirement(
                    kind=kind,
                    input_parameter=input_parameter,
                    field_parameter=field_parameter,
                    expected_parameter=expected_parameter,
                    start_parameter=start_parameter,
                    end_parameter=end_parameter,
                    parameter=parameter,
                    crs_requirement=crs_requirement,
                    reference_field_parameter=reference_field_parameter,
                    depth_field_parameter=depth_field_parameter,
                    reference_meanings=tuple(reference_meanings or ()),
                    depth_meanings=tuple(depth_meanings or ()),
                )
            )
        models.append(
            ExecutableModelDefinition(
                model_id=_text(item["modelId"], "KERNEL_CONFIG_INVALID"),
                model_version=_text(item["version"], "KERNEL_CONFIG_INVALID"),
                model_checksum=_text(item["modelChecksum"], "KERNEL_CONFIG_INVALID"),
                workflow_id=_text(workflow["workflowId"], "KERNEL_CONFIG_INVALID"),
                workflow_version=_text(workflow["version"], "KERNEL_CONFIG_INVALID"),
                tool_name=_text(tool["name"], "KERNEL_CONFIG_INVALID"),
                tool_description=_text(tool["description"], "KERNEL_CONFIG_INVALID"),
                input_schema=_mapping(tool["inputSchema"]),
                default_parameters=_mapping(item["defaultParameters"]),
                execution_service=item["executionService"],
                executor_id=_text(item["executorId"], "KERNEL_CONFIG_INVALID"),
                deadline_seconds=deadline,
                workflow_checksum=_text(workflow["checksum"], "KERNEL_CONFIG_INVALID"),
                input_schema_checksum=_text(item["inputSchemaChecksum"], "KERNEL_CONFIG_INVALID"),
                final_outputs=tuple(_text(name, "KERNEL_CONFIG_INVALID") for name in final_outputs),
                data_input_parameters=tuple(data_parameters),
                field_requirements=tuple(parsed_field_requirements),
                semantic_requirements=tuple(parsed_semantic_requirements),
            )
        )
    if len({item.model_id for item in models}) != len(models) or len(
        {item.tool_name for item in models}
    ) != len(models):
        raise ValueError("KERNEL_CONFIG_INVALID")
    return tuple(models)
