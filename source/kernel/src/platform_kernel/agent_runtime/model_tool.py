from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from agents import FunctionTool
from agents.tool_context import ToolContext
from jsonschema import ValidationError, validate

from ..errors import AgentRunError
from ..settings import ExecutableModelDefinition
from .persistence import load_replayed_action
from .provider_arguments import normalize_model_tool_arguments


def provider_model_input_schema(
    model: ExecutableModelDefinition,
) -> dict[str, object]:
    """Expose the complete contract, including user-overridable defaults.

    Omitting defaulted properties makes explicit user values impossible to send
    through a strict Provider tool. Kernel still merges omitted defaults before
    validating and freezing the confirmation arguments.
    """
    schema = deepcopy(model.input_schema)
    properties = schema.get("properties")
    if (
        not isinstance(properties, dict)
        or len(model.default_parameters) > 16
    ):
        # Very large models already consume most of the Provider tool budget.
        # Their product description and Agent instruction carry the general
        # default policy without repeating it across hundreds of properties.
        return schema
    for name, value in model.default_parameters.items():
        field = properties.get(name)
        if not isinstance(field, dict):
            continue
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        guidance = (
            f"Kernel default: {encoded}. Omit this property unless the user or "
            "current evidence explicitly overrides that default."
        )
        description = field.get("description")
        field["description"] = (
            guidance
            if not isinstance(description, str) or not description.strip()
            else f"{description.rstrip()} {guidance}"
        )
    return schema


def build_model_tool(
    database: Path,
    model: ExecutableModelDefinition,
    *,
    provider: str = "deepseek",
) -> FunctionTool:
    async def invoke(context: ToolContext[dict[str, Any]], arguments_json: str):
        try:
            supplied = json.loads(arguments_json)
            if not isinstance(supplied, dict):
                raise TypeError("model arguments must be an object")
            supplied = normalize_model_tool_arguments(
                supplied, model.input_schema, provider=provider,
            )
            arguments = {**model.default_parameters, **supplied}
            validate(arguments, model.input_schema)
        except (json.JSONDecodeError, TypeError, ValidationError) as error:
            raise AgentRunError("AGENT_MODEL_OUTPUT_INVALID") from error
        action = load_replayed_action(
            database, context.context["agent_run_id"], context.tool_call_id
        )
        if action is None or action.arguments != arguments:
            raise AgentRunError("AGENT_ACTION_REPLAY_INVALID")
        if action.status not in {"COMPLETE", "FAILED"}:
            raise AgentRunError("AGENT_ACTION_NOT_TERMINAL")
        executed_arguments = action.executed_arguments
        return json.dumps(
            {
                "status": action.status,
                "workflow_run_id": action.workflow_run_id,
                "outputs": action.outputs,
                "error": action.error,
                "executed_arguments": executed_arguments,
                "arguments_adjusted_by_user": (
                    executed_arguments != action.arguments
                ),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    return FunctionTool(
        name=model.tool_name,
        description=model.tool_description,
        params_json_schema=provider_model_input_schema(model),
        on_invoke_tool=invoke,
        strict_json_schema=True,
        needs_approval=True,
    )
