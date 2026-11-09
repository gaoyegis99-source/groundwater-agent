"""Build the executable-model input preparation tool."""

from __future__ import annotations

import json
import re
from pathlib import Path

from agents import FunctionTool
from agents.tool_context import ToolContext
from jsonschema import ValidationError, validate

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
from .provider_arguments import normalize_model_arguments


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
        descriptor = detail.get("descriptor") or {}
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
                    "status": "PASS" if descriptor.get("fields") else "UNKNOWN",
                },
                {
                    "kind": "FIELD_SEMANTICS",
                    "input": parameter,
                    "status": "PASS" if semantics.get("fields") else "UNKNOWN",
                },
            )
        )
    return references, checks, blocking_reasons, blocking_issues


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
        "parameters": supplied if isinstance(supplied, dict) else {},
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
