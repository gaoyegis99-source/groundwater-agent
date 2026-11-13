"""Build the executable-model input preparation tool."""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, time, timezone
from difflib import get_close_matches
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agents import FunctionTool
from agents.tool_context import ToolContext
from jsonschema import ValidationError, validate
from pyproj import CRS
from pyproj.exceptions import CRSError

from ..application.task_api import save_preparation
from ..errors import AgentRunError, UpstreamServiceError
from ..settings import (
    ExecutableModelDefinition,
    ObservationToolDefinition,
    ServiceBinding,
)
from .evidence import _data_reference
from .governance import governance
from .observation_client import ObservationClient
from .provider_arguments import normalize_model_arguments, omit_unset_field_arguments


class _MissingDataInputError(ValueError):
    def __init__(self, parameter: str):
        super().__init__("PREPARATION_DATA_INPUT_MISSING")
        self.parameter = parameter


def build_preparation_tool(
    database: Path,
    *,
    observations: tuple[ObservationToolDefinition, ...],
    services_by_name: dict[str, ServiceBinding],
    models: tuple[ExecutableModelDefinition, ...],
    observation_client: ObservationClient,
    provider: str = "deepseek",
    explicit_model_tool_request: object = None,
) -> FunctionTool:
    """Create the tool that validates and persists one model proposal."""
    inspect_definition = next(
        (item for item in observations if item.name == "inspect_data_version"),
        None,
    )
    models_by_tool = {item.tool_name: item for item in models}

    async def prepare_task_inputs(
        context: ToolContext,
        arguments_json: str,
    ) -> dict[str, object]:
        request: object = {}
        try:
            request = _parse_request(arguments_json, models_by_tool)
            model = models_by_tool[request["target_tool"]]
            supplied_request = _explicit_request_arguments(
                request,
                explicit_model_tool_request,
            )
            supplied = normalize_model_arguments(
                supplied_request,
                model.input_schema,
                provider=provider,
            )
            supplied = omit_unset_field_arguments(supplied, model, explicit_model_tool_request)
            request = {**request, "arguments": supplied}
            selection_policy = request["selection_policy"]
            arguments = {**model.default_parameters, **supplied}
            validate(arguments, model.input_schema)
            if inspect_definition is None:
                raise ValueError("PREPARATION_INSPECTOR_UNAVAILABLE")
            inspect_service = services_by_name.get(inspect_definition.service)
            if inspect_service is None:
                raise ValueError("PREPARATION_INSPECTOR_UNAVAILABLE")
            (
                references,
                checks,
                blocking_reasons,
                blocking_issues,
            ) = await _inspect_data_inputs(
                context=context,
                model=model,
                arguments=arguments,
                selection_policy=selection_policy,
                inspect_definition=inspect_definition,
                inspect_service=inspect_service,
                observation_client=observation_client,
            )
            snapshot = _ready_snapshot(
                context=context,
                model=model,
                supplied=supplied,
                arguments=arguments,
                selection_policy=selection_policy,
                references=references,
                checks=checks,
                blocking_reasons=blocking_reasons,
                blocking_issues=blocking_issues,
            )
        except ValidationError as error:
            snapshot = _blocked_snapshot(
                request,
                models_by_tool,
                "MODEL_ARGUMENTS_INVALID",
                blocking_reasons=_schema_blocking_reasons(error),
                blocking_issues=_schema_blocking_issues(error, model),
            )
        except _MissingDataInputError as error:
            snapshot = _blocked_snapshot(
                request,
                models_by_tool,
                "PREPARATION_DATA_INPUT_MISSING",
                blocking_issues=[
                    {
                        "code": "PREPARATION_DATA_INPUT_MISSING",
                        "kind": "MISSING_DATA",
                        "input": error.parameter,
                    }
                ],
            )
        except (AgentRunError, UpstreamServiceError) as error:
            snapshot = _blocked_snapshot(
                request,
                models_by_tool,
                _error_code(error, "DATA_INPUT_UNAVAILABLE"),
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            UnicodeEncodeError,
            json.JSONDecodeError,
        ) as error:
            snapshot = _blocked_snapshot(
                request,
                models_by_tool,
                _error_code(error, "PREPARATION_ARGUMENTS_INVALID"),
            )
        prepared = save_preparation(
            database,
            agent_run_id=context.context["agent_run_id"],
            tool_call_id=context.tool_call_id,
            snapshot=snapshot,
        )
        return _public_result(prepared)

    return FunctionTool(
        name="prepare_task_inputs",
        description=(
            "Validate one installed executable model proposal against its full input "
            "schema and re-read every exact Data version. Call after discovery and "
            "before record_plan. READY means contractually ready for confirmation; "
            "UNKNOWN field checks still require domain judgment."
        ),
        params_json_schema={
            "type": "object",
            "properties": {
                "target_tool": {"type": "string", "enum": sorted(models_by_tool)},
                "arguments": {"type": "object", "additionalProperties": True},
                "selection_policy": {
                    "type": "string",
                    "enum": ["PINNED", "CURRENT_REQUIRED"],
                    "description": (
                        "Use CURRENT_REQUIRED only when the request requires the "
                        "dataset head; PINNED preserves an explicitly selected version."
                    ),
                },
            },
            "required": ["target_tool", "arguments"],
            "additionalProperties": False,
        },
        on_invoke_tool=prepare_task_inputs,
        strict_json_schema=False,
        _use_default_failure_error_function=False,
    )


def _explicit_request_arguments(
    request: dict[str, object],
    binding: object,
) -> dict[str, object]:
    """Use the exact structured user arguments for their named preparation."""
    if not isinstance(binding, dict) or set(binding) != {"tool", "arguments"}:
        return request["arguments"]
    arguments = binding.get("arguments")
    if (
        request.get("target_tool") != binding.get("tool")
        or not isinstance(arguments, dict)
        or not all(isinstance(name, str) for name in arguments)
    ):
        return request["arguments"]
    return dict(arguments)


def _parse_request(
    arguments_json: str,
    models_by_tool: dict[str, ExecutableModelDefinition],
) -> dict[str, object]:
    request = json.loads(
        arguments_json,
        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
    )
    if (
        not isinstance(request, dict)
        or not {"target_tool", "arguments"} <= set(request)
        or set(request) - {"target_tool", "arguments", "selection_policy"}
    ):
        raise ValueError("PREPARATION_ARGUMENTS_INVALID")
    target_tool = request["target_tool"]
    supplied = request["arguments"]
    selection_policy = request.get("selection_policy", "PINNED")
    if (
        not isinstance(target_tool, str)
        or target_tool not in models_by_tool
        or not isinstance(supplied, dict)
        or selection_policy not in {"PINNED", "CURRENT_REQUIRED"}
    ):
        raise ValueError("PREPARATION_TARGET_INVALID")
    return {
        "target_tool": target_tool,
        "arguments": supplied,
        "selection_policy": selection_policy,
    }


async def _inspect_data_inputs(
    *,
    context: ToolContext,
    model: ExecutableModelDefinition,
    arguments: dict[str, object],
    selection_policy: str,
    inspect_definition: ObservationToolDefinition,
    inspect_service: ServiceBinding,
    observation_client: ObservationClient,
) -> tuple[
    dict[str, dict[str, object]],
    list[dict[str, object]],
    list[str],
    list[dict[str, object]],
]:
    references = {}
    checks = []
    blocking_reasons = []
    blocking_issues = []
    requirements_by_input = {
        parameter: tuple(
            requirement
            for requirement in model.field_requirements
            if requirement.input_parameter == parameter
        )
        for parameter in model.data_input_parameters
    }
    semantic_requirements_by_input = {
        parameter: tuple(
            requirement
            for requirement in model.semantic_requirements
            if requirement.input_parameter == parameter
        )
        for parameter in model.data_input_parameters
    }
    for parameter in model.data_input_parameters:
        version_id = arguments.get(parameter)
        if not isinstance(version_id, str) or not version_id:
            raise _MissingDataInputError(parameter)
        response = await observation_client.invoke(
            inspect_definition,
            inspect_service,
            call_id=f"prepare:{context.tool_call_id}:{parameter}",
            project_id=context.context["project_id"],
            trace_id=context.context["trace_id"],
            arguments={"version_id": version_id},
            agent_run_id=context.context["agent_run_id"],
        )
        reference = _data_reference(response["data"])
        if reference.version_id != version_id:
            raise ValueError("PREPARATION_DATA_IDENTITY_MISMATCH")
        references[parameter] = reference.as_storage_dict()
        detail = response["data"]
        current_version = detail.get("currentVersion")
        version_is_current = not (
            selection_policy == "CURRENT_REQUIRED" and current_version != version_id
        )
        if not version_is_current:
            blocking_reasons.append("DATA_VERSION_NOT_CURRENT")
            blocking_issues.append(
                {
                    "code": "DATA_VERSION_NOT_CURRENT",
                    "kind": "DATA_VERSION_NOT_CURRENT",
                    "input": parameter,
                }
            )
        semantics = detail.get("semantics") or {}
        if not isinstance(semantics, dict):
            semantics = {}
        descriptor = detail.get("descriptor") or {}
        field_catalog = _field_catalog(descriptor)
        checks.extend(
            (
                {
                    "kind": "DATA_IDENTITY",
                    "input": parameter,
                    "status": "PASS",
                    "version_id": version_id,
                    "semantic_id": reference.semantic_id,
                },
                {
                    "kind": "SELECTION_POLICY",
                    "input": parameter,
                    "status": "PASS" if version_is_current else "FAIL",
                    "version_id": version_id,
                    "current_version": current_version,
                },
                {
                    "kind": "FIELD_METADATA",
                    "input": parameter,
                    "status": "PASS" if field_catalog is not None else "UNKNOWN",
                },
                {
                    "kind": "FIELD_SEMANTICS",
                    "input": parameter,
                    "status": "PASS" if semantics.get("fields") else "UNKNOWN",
                },
            )
        )
        for requirement in requirements_by_input[parameter]:
            _check_field_requirement(
                requirement=requirement,
                value=arguments.get(requirement.parameter),
                version_id=version_id,
                field_catalog=field_catalog,
                checks=checks,
                blocking_reasons=blocking_reasons,
                blocking_issues=blocking_issues,
            )
        for requirement in semantic_requirements_by_input[parameter]:
            _check_semantic_requirement(
                requirement=requirement,
                model=model,
                arguments=arguments,
                version_id=version_id,
                field_catalog=field_catalog,
                semantics=semantics,
                checks=checks,
                blocking_reasons=blocking_reasons,
                blocking_issues=blocking_issues,
            )
    for requirement in model.semantic_requirements:
        if requirement.kind == "crsParameter":
            _check_crs_parameter_requirement(
                requirement=requirement,
                arguments=arguments,
                checks=checks,
                blocking_reasons=blocking_reasons,
                blocking_issues=blocking_issues,
            )
    return references, checks, blocking_reasons, blocking_issues


def _field_catalog(descriptor: object) -> tuple[dict[str, str], ...] | None:
    """Return only the strict Data descriptor field contract, never aliases."""
    if not isinstance(descriptor, dict):
        return None
    fields = descriptor.get("fields")
    if not isinstance(fields, list) or not fields:
        return None
    catalog = []
    for field in fields:
        if not isinstance(field, dict):
            return None
        projected = {
            "field_id": field.get("fieldId"),
            "name": field.get("name"),
            "type": field.get("type"),
        }
        if any(not isinstance(value, str) or not value for value in projected.values()):
            return None
        catalog.append(projected)
    return tuple(catalog)


def _check_field_requirement(
    *,
    requirement,
    value: object,
    version_id: str,
    field_catalog: tuple[dict[str, str], ...] | None,
    checks: list[dict[str, object]],
    blocking_reasons: list[str],
    blocking_issues: list[dict[str, object]],
) -> None:
    # Optional field parameters such as render-map valueField have no contract
    # to check until the caller supplies a nonempty identifier.
    if value is None or value == "":
        return
    identifier_key = "field_id" if requirement.identifier == "fieldId" else "name"
    base = {
        "input": requirement.input_parameter,
        "parameter": requirement.parameter,
        "version_id": version_id,
        "identifier": requirement.identifier,
        "value": value,
    }
    issue_base = {
        "kind": "INVALID_PARAMETER",
        "input": requirement.input_parameter,
        "parameter": requirement.parameter,
        "path": _parameter_path(requirement.parameter),
        "identifier": requirement.identifier,
        "value": value,
    }
    if field_catalog is None:
        checks.append({"kind": "FIELD_EXISTS", "status": "UNKNOWN", **base})
        blocking_reasons.append("FIELD_METADATA_UNAVAILABLE")
        blocking_issues.append(
            {**issue_base, "code": "FIELD_METADATA_UNAVAILABLE", "kind": "UNKNOWN"}
        )
        return

    matches = [field for field in field_catalog if field[identifier_key] == value]
    if not matches:
        candidates = _similar_fields(str(value), identifier_key, field_catalog)
        checks.append(
            {
                "kind": "FIELD_EXISTS",
                "status": "FAIL",
                **base,
                "candidates": candidates,
            }
        )
        blocking_reasons.append("FIELD_NOT_FOUND")
        blocking_issues.append(
            {
                "code": "FIELD_NOT_FOUND",
                **issue_base,
                "candidates": candidates,
            }
        )
        return
    if len(matches) != 1:
        checks.append({"kind": "FIELD_EXISTS", "status": "FAIL", **base})
        blocking_reasons.append("FIELD_IDENTIFIER_AMBIGUOUS")
        blocking_issues.append(
            {"code": "FIELD_IDENTIFIER_AMBIGUOUS", **issue_base}
        )
        return

    field = matches[0]
    field_details = {
        "field_id": field["field_id"],
        "field_name": field["name"],
        "actual_type": field["type"],
    }
    checks.append(
        {"kind": "FIELD_EXISTS", "status": "PASS", **base, **field_details}
    )
    allowed_types = list(requirement.allowed_types)
    type_matches = _field_type_matches(field["type"], requirement.allowed_types)
    checks.append(
        {
            "kind": "FIELD_TYPE",
            "status": "PASS" if type_matches else "FAIL",
            **base,
            **field_details,
            "allowed_types": allowed_types,
        }
    )
    if not type_matches:
        blocking_reasons.append("FIELD_TYPE_MISMATCH")
        blocking_issues.append(
            {
                "code": "FIELD_TYPE_MISMATCH",
                **issue_base,
                "actual_type": field["type"],
                "allowed_types": allowed_types,
            }
        )


def _similar_fields(
    value: str,
    identifier_key: str,
    field_catalog: tuple[dict[str, str], ...],
) -> list[dict[str, str]]:
    identifiers = [field[identifier_key] for field in field_catalog]
    close = get_close_matches(value, identifiers, n=3, cutoff=0.5)
    return [
        field
        for identifier in close
        for field in field_catalog
        if field[identifier_key] == identifier
    ][:3]


def _field_type_matches(actual: str, allowed: tuple[str, ...]) -> bool:
    value = actual.strip().lower()
    if "any" in allowed:
        return True
    families = set()
    if value in {"bool", "boolean"}:
        families.add("boolean")
    if re.fullmatch(r"u?int(?:8|16|32|64)?", value):
        families.update(("integer", "number"))
    if (
        value in {"double", "float", "float16", "float32", "float64", "half_float"}
        or value.startswith("decimal")
    ):
        families.add("number")
    if value in {"string", "large_string", "utf8", "large_utf8"}:
        families.add("string")
    if value.startswith(("date", "timestamp", "time", "duration")):
        families.add("date-time")
    return bool(families.intersection(allowed))


_UNIT_IDENTITIES = {
    "m": ("m", "length"),
    "meter": ("m", "length"),
    "meters": ("m", "length"),
    "metre": ("m", "length"),
    "metres": ("m", "length"),
    "cm": ("cm", "length"),
    "centimeter": ("cm", "length"),
    "centimeters": ("cm", "length"),
    "centimetre": ("cm", "length"),
    "centimetres": ("cm", "length"),
    "mm": ("mm", "length"),
    "millimeter": ("mm", "length"),
    "millimeters": ("mm", "length"),
    "millimetre": ("mm", "length"),
    "millimetres": ("mm", "length"),
    "km": ("km", "length"),
    "ft": ("ft", "length"),
    "foot": ("ft", "length"),
    "feet": ("ft", "length"),
    "in": ("in", "length"),
    "inch": ("in", "length"),
    "inches": ("in", "length"),
    "s": ("s", "time"),
    "sec": ("s", "time"),
    "second": ("s", "time"),
    "seconds": ("s", "time"),
    "min": ("min", "time"),
    "minute": ("min", "time"),
    "minutes": ("min", "time"),
    "h": ("h", "time"),
    "hr": ("h", "time"),
    "hour": ("h", "time"),
    "hours": ("h", "time"),
    "d": ("d", "time"),
    "day": ("d", "time"),
    "days": ("d", "time"),
    "m/d": ("m/d", "length/time"),
    "m/day": ("m/d", "length/time"),
    "mm/d": ("mm/d", "length/time"),
    "mm/day": ("mm/d", "length/time"),
    "cm/d": ("cm/d", "length/time"),
    "cm/day": ("cm/d", "length/time"),
    "ft/d": ("ft/d", "length/time"),
    "ft/day": ("ft/d", "length/time"),
    "mg/l": ("mg/l", "concentration"),
    "ug/l": ("ug/l", "concentration"),
    "μg/l": ("ug/l", "concentration"),
}


def _normalized_unit(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold().replace("per", "/")


def _unit_relationship(actual: str, expected: str) -> str:
    actual_key = _normalized_unit(actual)
    expected_key = _normalized_unit(expected)
    if actual_key == expected_key:
        return "EQUIVALENT"
    actual_identity = _UNIT_IDENTITIES.get(actual_key)
    expected_identity = _UNIT_IDENTITIES.get(expected_key)
    if actual_identity is None or expected_identity is None:
        return "NOT_COMPARABLE"
    if actual_identity[0] == expected_identity[0]:
        return "EQUIVALENT"
    if actual_identity[1] == expected_identity[1]:
        return "CONVERTIBLE"
    return "NOT_COMPARABLE"


def _parse_crs(value: str) -> CRS | None:
    try:
        return CRS.from_user_input(value)
    except (CRSError, ValueError, TypeError):
        return None


def _crs_unit(value: CRS) -> str | None:
    names = {
        axis.unit_name
        for axis in value.axis_info[:2]
        if isinstance(axis.unit_name, str) and axis.unit_name
    }
    return ", ".join(sorted(names)) if names else None


def _is_projected_metric_crs(value: CRS) -> bool:
    axes = value.axis_info[:2]
    return bool(
        value.is_projected
        and len(axes) == 2
        and all(
            axis.unit_conversion_factor is not None
            and math.isclose(axis.unit_conversion_factor, 1.0)
            for axis in axes
        )
    )


def _resolved_requirement_field(
    *,
    requirement,
    model: ExecutableModelDefinition,
    arguments: dict[str, object],
    field_catalog: tuple[dict[str, str], ...] | None,
) -> dict[str, str] | None:
    return _resolved_model_field(
        input_parameter=requirement.input_parameter,
        field_parameter=requirement.field_parameter,
        model=model,
        arguments=arguments,
        field_catalog=field_catalog,
    )


def _resolved_model_field(
    *,
    input_parameter: str | None,
    field_parameter: str | None,
    model: ExecutableModelDefinition,
    arguments: dict[str, object],
    field_catalog: tuple[dict[str, str], ...] | None,
) -> dict[str, str] | None:
    if field_parameter is None or field_catalog is None:
        return None
    field_requirement = next(
        (
            item
            for item in model.field_requirements
            if item.parameter == field_parameter
            and item.input_parameter == input_parameter
        ),
        None,
    )
    if field_requirement is None:
        return None
    value = arguments.get(field_parameter)
    if not isinstance(value, str) or not value:
        return None
    identifier_key = (
        "field_id" if field_requirement.identifier == "fieldId" else "name"
    )
    matches = [field for field in field_catalog if field[identifier_key] == value]
    return matches[0] if len(matches) == 1 else None


def _semantic_check_base(requirement, version_id: str) -> dict[str, object]:
    return {
        "input": requirement.input_parameter,
        "version_id": version_id,
        **(
            {"parameter": requirement.field_parameter}
            if requirement.field_parameter is not None
            else {}
        ),
    }


def _semantic_failure(
    *,
    code: str,
    requirement,
    parameter: str | None,
    blocking_reasons: list[str],
    blocking_issues: list[dict[str, object]],
) -> None:
    blocking_reasons.append(code)
    blocking_issues.append(
        {
            "code": code,
            "kind": "INVALID_PARAMETER" if parameter else "UNKNOWN",
            "input": requirement.input_parameter,
            **({"parameter": parameter, "path": _parameter_path(parameter)} if parameter else {}),
        }
    )


def _semantic_field_details(field: dict[str, str] | None) -> dict[str, str]:
    if field is None:
        return {}
    return {
        "field_id": field["field_id"],
        "field_name": field["name"],
        "actual_type": field["type"],
    }


def _check_semantic_requirement(
    *,
    requirement,
    model: ExecutableModelDefinition,
    arguments: dict[str, object],
    version_id: str,
    field_catalog: tuple[dict[str, str], ...] | None,
    semantics: dict[str, object],
    checks: list[dict[str, object]],
    blocking_reasons: list[str],
    blocking_issues: list[dict[str, object]],
) -> None:
    if requirement.kind == "verticalBasis":
        _check_vertical_basis_requirement(
            requirement=requirement,
            model=model,
            arguments=arguments,
            version_id=version_id,
            field_catalog=field_catalog,
            semantics=semantics,
            checks=checks,
            blocking_reasons=blocking_reasons,
            blocking_issues=blocking_issues,
        )
        return
    field = _resolved_requirement_field(
        requirement=requirement,
        model=model,
        arguments=arguments,
        field_catalog=field_catalog,
    )
    if requirement.field_parameter is not None and field is None:
        # The corresponding FIELD_EXISTS/FIELD_TYPE checks already explain why
        # this semantic fact cannot yet be evaluated.
        return
    base = {
        **_semantic_check_base(requirement, version_id),
        **_semantic_field_details(field),
    }
    semantic_fields = semantics.get("fields")
    if not isinstance(semantic_fields, dict):
        semantic_fields = {}

    if requirement.kind == "fieldUnit":
        field_semantics = semantic_fields.get(field["field_id"]) if field else None
        actual = field_semantics.get("unit") if isinstance(field_semantics, dict) else None
        actual_unit = actual.strip() if isinstance(actual, str) and actual.strip() else None
        expected = (
            arguments.get(requirement.expected_parameter)
            if requirement.expected_parameter is not None
            else None
        )
        expected_unit = (
            expected.strip()
            if isinstance(expected, str) and expected.strip()
            else None
        )
        if actual_unit is None:
            checks.append(
                {
                    "kind": "FIELD_UNIT",
                    "status": "UNKNOWN",
                    **base,
                    **({"expected_unit": expected_unit} if expected_unit else {}),
                }
            )
            return
        relationship = (
            "DECLARED"
            if expected_unit is None
            else _unit_relationship(actual_unit, expected_unit)
        )
        matches = relationship in {"DECLARED", "EQUIVALENT"}
        checks.append(
            {
                "kind": "FIELD_UNIT",
                "status": "PASS" if matches else "FAIL",
                **base,
                "actual_unit": actual_unit,
                "relationship": relationship,
                **({"expected_unit": expected_unit} if expected_unit else {}),
            }
        )
        if not matches:
            _semantic_failure(
                code=(
                    "UNIT_CONVERSION_REQUIRED"
                    if relationship == "CONVERTIBLE"
                    else "UNIT_NOT_COMPARABLE"
                ),
                requirement=requirement,
                parameter=requirement.expected_parameter or requirement.field_parameter,
                blocking_reasons=blocking_reasons,
                blocking_issues=blocking_issues,
            )
        return

    if requirement.kind == "crs":
        actual = semantics.get("crs")
        actual_crs = actual.strip() if isinstance(actual, str) and actual.strip() else None
        expected = (
            arguments.get(requirement.expected_parameter)
            if requirement.expected_parameter is not None
            else None
        )
        expected_crs = (
            expected.strip()
            if isinstance(expected, str) and expected.strip()
            else None
        )
        if actual_crs is None:
            checks.append(
                {
                    "kind": "CRS",
                    "status": "UNKNOWN",
                    **base,
                    **({"expected_crs": expected_crs} if expected_crs else {}),
                }
            )
            return
        parsed_actual = _parse_crs(actual_crs)
        parsed_expected = _parse_crs(expected_crs) if expected_crs else None
        valid = parsed_actual is not None and (
            expected_crs is None or parsed_expected is not None
        )
        matches = bool(
            valid
            and (expected_crs is None or parsed_actual == parsed_expected)
        )
        checks.append(
            {
                "kind": "CRS",
                "status": "PASS" if matches else "FAIL",
                **base,
                "actual_crs": actual_crs,
                **(
                    {"crs_unit": unit}
                    if parsed_actual is not None
                    and (unit := _crs_unit(parsed_actual)) is not None
                    else {}
                ),
                **({"expected_crs": expected_crs} if expected_crs else {}),
            }
        )
        if not matches:
            code = "CRS_MISMATCH" if valid else "CRS_INVALID"
            _semantic_failure(
                code=code,
                requirement=requirement,
                parameter=(
                    requirement.expected_parameter
                    if parsed_actual is not None
                    else None
                ),
                blocking_reasons=blocking_reasons,
                blocking_issues=blocking_issues,
            )
        return

    if requirement.kind == "timeField":
        is_date_time = _field_type_matches(field["type"], ("date-time",))
        checks.append(
            {
                "kind": "TIME_FIELD_PARSEABILITY",
                "status": "PASS" if is_date_time else "UNKNOWN",
                **base,
            }
        )
        timezone_value = semantics.get("timezone")
        timezone_name = (
            timezone_value.strip()
            if isinstance(timezone_value, str) and timezone_value.strip()
            else None
        )
        timezone_status = "UNKNOWN"
        if timezone_name is not None:
            try:
                ZoneInfo(timezone_name)
                timezone_status = "PASS"
            except (ZoneInfoNotFoundError, ValueError):
                timezone_status = "FAIL"
        checks.append(
            {
                "kind": "TIMEZONE",
                "status": timezone_status,
                **base,
                **({"timezone": timezone_name} if timezone_name else {}),
            }
        )
        if timezone_status == "FAIL":
            _semantic_failure(
                code="TIMEZONE_INVALID",
                requirement=requirement,
                parameter=requirement.field_parameter,
                blocking_reasons=blocking_reasons,
                blocking_issues=blocking_issues,
            )
        return

    _check_time_range_requirement(
        requirement=requirement,
        arguments=arguments,
        semantics=semantics,
        base=base,
        checks=checks,
        blocking_reasons=blocking_reasons,
        blocking_issues=blocking_issues,
    )


def _check_vertical_basis_requirement(
    *,
    requirement,
    model: ExecutableModelDefinition,
    arguments: dict[str, object],
    version_id: str,
    field_catalog: tuple[dict[str, str], ...] | None,
    semantics: dict[str, object],
    checks: list[dict[str, object]],
    blocking_reasons: list[str],
    blocking_issues: list[dict[str, object]],
) -> None:
    reference_field = _resolved_model_field(
        input_parameter=requirement.input_parameter,
        field_parameter=requirement.reference_field_parameter,
        model=model,
        arguments=arguments,
        field_catalog=field_catalog,
    )
    depth_field = _resolved_model_field(
        input_parameter=requirement.input_parameter,
        field_parameter=requirement.depth_field_parameter,
        model=model,
        arguments=arguments,
        field_catalog=field_catalog,
    )
    if reference_field is None or depth_field is None:
        return
    semantic_fields = semantics.get("fields")
    if not isinstance(semantic_fields, dict):
        semantic_fields = {}

    def meaning(field: dict[str, str]) -> str | None:
        field_semantics = semantic_fields.get(field["field_id"])
        value = (
            field_semantics.get("meaning")
            if isinstance(field_semantics, dict)
            else None
        )
        return value.strip() if isinstance(value, str) and value.strip() else None

    reference_meaning = meaning(reference_field)
    depth_meaning = meaning(depth_field)
    vertical_value = semantics.get("verticalDatum")
    vertical_datum = (
        vertical_value.strip()
        if isinstance(vertical_value, str) and vertical_value.strip()
        else None
    )
    expected_reference = {value.casefold() for value in requirement.reference_meanings}
    expected_depth = {value.casefold() for value in requirement.depth_meanings}
    reference_key = reference_meaning.casefold() if reference_meaning else None
    depth_key = depth_meaning.casefold() if depth_meaning else None
    declared = (
        reference_key is not None
        and depth_key is not None
        and vertical_datum is not None
    )
    matches = bool(
        declared
        and reference_key in expected_reference
        and depth_key in expected_depth
    )
    conflicts = bool(reference_key in expected_depth or depth_key in expected_reference)
    checks.append(
        {
            "kind": "VERTICAL_BASIS",
            "status": "PASS" if matches else "FAIL" if conflicts else "UNKNOWN",
            "input": requirement.input_parameter,
            "version_id": version_id,
            "reference_field": reference_field["name"],
            "depth_field": depth_field["name"],
            **(
                {"reference_meaning": reference_meaning}
                if reference_meaning is not None
                else {}
            ),
            **(
                {"depth_meaning": depth_meaning}
                if depth_meaning is not None
                else {}
            ),
            **(
                {"vertical_datum": vertical_datum}
                if vertical_datum is not None
                else {}
            ),
        }
    )
    if conflicts:
        _semantic_failure(
            code="VERTICAL_BASIS_MISMATCH",
            requirement=requirement,
            parameter=None,
            blocking_reasons=blocking_reasons,
            blocking_issues=blocking_issues,
        )


def _check_crs_parameter_requirement(
    *,
    requirement,
    arguments: dict[str, object],
    checks: list[dict[str, object]],
    blocking_reasons: list[str],
    blocking_issues: list[dict[str, object]],
) -> None:
    value = arguments.get(requirement.parameter)
    actual_crs = value.strip() if isinstance(value, str) and value.strip() else None
    parsed = _parse_crs(actual_crs) if actual_crs else None
    valid = parsed is not None
    projected_metric = bool(parsed and _is_projected_metric_crs(parsed))
    passed = valid and (
        requirement.crs_requirement == "valid" or projected_metric
    )
    checks.append(
        {
            "kind": "CRS_PARAMETER",
            "status": "PASS" if passed else "FAIL",
            "parameter": requirement.parameter,
            "crs_requirement": requirement.crs_requirement,
            **({"actual_crs": actual_crs} if actual_crs else {}),
            **(
                {"crs_unit": unit}
                if parsed is not None
                and (unit := _crs_unit(parsed)) is not None
                else {}
            ),
        }
    )
    if passed:
        return
    _semantic_failure(
        code=(
            "CRS_INVALID"
            if not valid
            else "PROJECTED_METRIC_CRS_REQUIRED"
        ),
        requirement=requirement,
        parameter=requirement.parameter,
        blocking_reasons=blocking_reasons,
        blocking_issues=blocking_issues,
    )


def _parse_iso_value(value: object, timezone_name: str | None) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            parsed = datetime.combine(date.fromisoformat(raw), time.min)
        else:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        try:
            parsed = parsed.replace(
                tzinfo=ZoneInfo(timezone_name) if timezone_name else timezone.utc
            )
        except (ZoneInfoNotFoundError, ValueError):
            return None
    return parsed.astimezone(timezone.utc)


def _check_time_range_requirement(
    *,
    requirement,
    arguments: dict[str, object],
    semantics: dict[str, object],
    base: dict[str, object],
    checks: list[dict[str, object]],
    blocking_reasons: list[str],
    blocking_issues: list[dict[str, object]],
) -> None:
    time_range_value = semantics.get("timeRange")
    time_range = (
        time_range_value.strip()
        if isinstance(time_range_value, str) and time_range_value.strip()
        else None
    )
    requested_start_value = arguments.get(requirement.start_parameter)
    requested_end_value = arguments.get(requirement.end_parameter)
    requested_start = (
        requested_start_value.strip()
        if isinstance(requested_start_value, str)
        else str(requested_start_value or "")
    )
    requested_end = (
        requested_end_value.strip()
        if isinstance(requested_end_value, str)
        else str(requested_end_value or "")
    )
    details = {
        **base,
        "requested_start": requested_start,
        "requested_end": requested_end,
        **({"time_range": time_range} if time_range else {}),
    }
    if time_range is None:
        checks.append({"kind": "TIME_RANGE", "status": "UNKNOWN", **details})
        return
    parts = time_range.split("/")
    timezone_value = semantics.get("timezone")
    timezone_name = timezone_value if isinstance(timezone_value, str) else None
    available_start = _parse_iso_value(parts[0], timezone_name) if len(parts) == 2 else None
    available_end = _parse_iso_value(parts[1], timezone_name) if len(parts) == 2 else None
    if (
        available_start is None
        or available_end is None
        or available_start > available_end
    ):
        checks.append({"kind": "TIME_RANGE", "status": "UNKNOWN", **details})
        return
    desired_start = _parse_iso_value(requested_start, timezone_name)
    desired_end = _parse_iso_value(requested_end, timezone_name)
    if desired_start is None or desired_end is None or desired_start > desired_end:
        checks.append({"kind": "TIME_RANGE", "status": "FAIL", **details})
        _semantic_failure(
            code="TIME_PARAMETER_INVALID",
            requirement=requirement,
            parameter=requirement.start_parameter,
            blocking_reasons=blocking_reasons,
            blocking_issues=blocking_issues,
        )
        return
    covered = available_start <= desired_start and desired_end <= available_end
    checks.append(
        {"kind": "TIME_RANGE", "status": "PASS" if covered else "FAIL", **details}
    )
    if not covered:
        _semantic_failure(
            code="TIME_RANGE_NOT_COVERED",
            requirement=requirement,
            parameter=requirement.start_parameter,
            blocking_reasons=blocking_reasons,
            blocking_issues=blocking_issues,
        )


def _parameter_path(parameter: str) -> str:
    return "/" + parameter.replace("~", "~0").replace("/", "~1")


def _ready_snapshot(
    *,
    context: ToolContext,
    model: ExecutableModelDefinition,
    supplied: dict[str, object],
    arguments: dict[str, object],
    selection_policy: str,
    references: dict[str, dict[str, object]],
    checks: list[dict[str, object]],
    blocking_reasons: list[str],
    blocking_issues: list[dict[str, object]],
) -> dict[str, object]:
    parameters = {
        name: value
        for name, value in arguments.items()
        if name not in model.data_input_parameters
    }
    return {
        "target_kind": "EXECUTABLE_MODEL",
        "target_id": model.model_id,
        "target_version": model.model_version,
        "selection_policy": selection_policy,
        "status": "BLOCKED" if blocking_reasons else "READY",
        "inputs": references,
        "knowledge_refs": [
            {"version_id": version_id, "unit_id": unit_id, "checksum": checksum}
            for version_id, unit_id, checksum in governance(context.context)[
                "knowledge_evidence"
            ]
        ],
        "parameters": parameters,
        "parameter_sources": {
            name: "PROPOSED" if name in supplied else "MODEL_DEFAULT"
            for name in parameters
        },
        "checks": checks,
        "blocking_reasons": list(dict.fromkeys(blocking_reasons)),
        "blocking_issues": blocking_issues,
    }


def _blocked_snapshot(
    request: object,
    models_by_tool: dict[str, ExecutableModelDefinition],
    reason: str,
    *,
    path: str = "",
    blocking_reasons: list[str] | None = None,
    blocking_issues: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    target_tool = request.get("target_tool") if isinstance(request, dict) else None
    model = models_by_tool.get(target_tool) if isinstance(target_tool, str) else None
    supplied = request.get("arguments") if isinstance(request, dict) else None
    selection_policy = (
        request.get("selection_policy", "PINNED")
        if isinstance(request, dict)
        else "PINNED"
    )
    if selection_policy not in {"PINNED", "CURRENT_REQUIRED"}:
        selection_policy = "PINNED"
    return {
        "target_kind": "EXECUTABLE_MODEL",
        "target_id": model.model_id
        if model is not None
        else str(target_tool or "unknown"),
        "target_version": None if model is None else model.model_version,
        "selection_policy": selection_policy,
        "status": "BLOCKED",
        "inputs": {},
        "knowledge_refs": [],
        "parameters": {
            **(model.default_parameters if model is not None else {}),
            **(supplied if isinstance(supplied, dict) else {}),
        },
        "parameter_sources": {},
        "checks": [],
        "blocking_reasons": blocking_reasons or [reason + (f":{path}" if path else "")],
        "blocking_issues": blocking_issues
        or [
            {
                "code": reason,
                "kind": "UNKNOWN",
                **({"path": path} if path else {}),
            }
        ],
    }


def _schema_blocking_reasons(error: ValidationError) -> list[str]:
    """Project schema failures as paths and validators without argument values."""
    prefix = "/" + "/".join(
        str(item).replace("~", "~0").replace("/", "~1") for item in error.absolute_path
    )
    instance = error.instance
    schema = error.schema
    paths: list[str] = []
    if (
        error.validator == "additionalProperties"
        and isinstance(instance, dict)
        and isinstance(schema, dict)
        and isinstance(schema.get("properties"), dict)
    ):
        paths = [
            prefix.rstrip("/") + "/" + str(name).replace("~", "~0").replace("/", "~1")
            for name in sorted(set(instance) - set(schema["properties"]))
        ]
    elif (
        error.validator == "required"
        and isinstance(instance, dict)
        and isinstance(error.validator_value, list)
    ):
        paths = [
            prefix.rstrip("/") + "/" + str(name).replace("~", "~0").replace("/", "~1")
            for name in error.validator_value
            if name not in instance
        ]
    if not paths:
        paths = [prefix]
    validator = str(error.validator or "schema")
    return [f"MODEL_ARGUMENTS_INVALID:{path or '/'}:{validator}" for path in paths[:32]]


def _schema_blocking_issues(
    error: ValidationError, model: ExecutableModelDefinition
) -> list[dict[str, object]]:
    reasons = _schema_blocking_reasons(error)
    issues = []
    for reason in reasons:
        path, validator = reason.removeprefix("MODEL_ARGUMENTS_INVALID:").rsplit(":", 1)
        token = path.lstrip("/").split("/", 1)[0]
        name = token.replace("~1", "/").replace("~0", "~") if token else None
        if validator == "required" and name in model.data_input_parameters:
            kind = "MISSING_DATA"
        elif validator == "required":
            kind = "MISSING_PARAMETER"
        else:
            kind = "INVALID_PARAMETER"
        issues.append(
            {
                "code": "MODEL_ARGUMENTS_INVALID",
                "kind": kind,
                **({"input": name} if kind == "MISSING_DATA" and name else {}),
                **({"parameter": name} if kind != "MISSING_DATA" and name else {}),
                "path": path,
                "validator": validator,
            }
        )
    return issues


def _error_code(error: Exception, fallback: str) -> str:
    reason = str(error)
    return reason if re.fullmatch(r"[A-Z][A-Z0-9_]*", reason) else fallback


def _public_result(prepared: dict[str, object]) -> dict[str, object]:
    return {
        "status": prepared["status"],
        "preparation_id": prepared["preparation_id"],
        "revision": prepared["revision"],
        "target_id": prepared["target_id"],
        "target_version": prepared["target_version"],
        "selection_policy": prepared["selection_policy"],
        "data_references": prepared["inputs"],
        "knowledge_references": prepared["knowledge_refs"],
        "parameters": prepared["parameters"],
        "parameter_sources": prepared["parameter_sources"],
        "checks": prepared["checks"],
        "blocking_reasons": prepared["blocking_reasons"],
        "blocking_issues": prepared["blocking_issues"],
    }
