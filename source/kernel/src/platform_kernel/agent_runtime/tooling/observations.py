"""Service-owned observation tool transport adapters."""

from __future__ import annotations

import json
from typing import Any

from agents import FunctionTool
from agents.tool_context import ToolContext
from jsonschema import ValidationError, validate

from ...errors import AgentRunError
from ...settings import (
    ObservationToolDefinition,
    ServiceBinding,
)
from ...tool_progress import (
    parse_progress_intent,
    provider_progress_intent_schema,
)
from ..observation_client import ObservationClient


MINIMAX_KNOWLEDGE_TOOLS = {
    "retrieve_evidence", "read_evidence", "lookup_knowledge",
}
MINIMAX_NORMALIZED_OBSERVATION_TOOLS = (
    MINIMAX_KNOWLEDGE_TOOLS | {"discover_datasets"}
)
MINIMAX_KNOWLEDGE_BUDGET = {"max_units": 8, "max_chars": 16000}


def _observation_description(
    definition: ObservationToolDefinition, provider: str,
) -> str:
    if provider not in {"deepseek", "minimax"}:
        return definition.description
    if provider == "deepseek":
        if definition.name == "inspect_data_table":
            return (
                "Inspect one queryable CSV or Parquet Data version. Pass only "
                "version_id; never pass object_id or dataset_id."
            )
        if definition.name == "retrieve_evidence":
            return "Search Knowledge. On STALE, retrieve again."
        if definition.name == "read_evidence":
            return (
                "Completed retrieval refs only: version_id, unit_id, checksum."
            )
        return definition.description
    if definition.name == "retrieve_evidence":
        return (
            "Search Knowledge with a natural-language query. For general questions "
            "pass only query. Use an exact Data binding only for bound evidence. "
            "Then call read_evidence with returned identities."
        )
    if definition.name == "read_evidence":
        return (
            "Read full Knowledge evidence. Copy only exact version_id, unit_id and "
            "checksum values returned by retrieve_evidence."
        )
    if definition.name == "lookup_knowledge":
        return (
            "Look up Knowledge only by an exact known key or exact Data binding. "
            "Use retrieve_evidence for natural-language questions."
        )
    return definition.description


def _observation_input_schema(
    definition: ObservationToolDefinition, provider: str,
) -> dict[str, object]:
    schema = json.loads(json.dumps(definition.input_schema))
    if definition.progress is None:
        return schema
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("OBSERVATION_INPUT_SCHEMA_INVALID")
    if provider == "minimax" and definition.name in MINIMAX_KNOWLEDGE_TOOLS:
        keep = {
            "retrieve_evidence": {"query", "scope", "binding"},
            "read_evidence": {"refs"},
            "lookup_knowledge": {"key", "binding"},
        }[definition.name]
        schema["properties"] = {
            key: value for key, value in properties.items() if key in keep
        }
        properties = schema["properties"]
        definitions = schema.get("$defs")
        if isinstance(definitions, dict):
            definitions.pop("Budget", None)
            if not definitions:
                schema.pop("$defs", None)
        # Conditional binding rules remain enforced by the service. Keeping the
        # provider schema small makes the valid general-question path unambiguous.
        schema.pop("allOf", None)
    properties["progress_intent"] = provider_progress_intent_schema(definition)
    required = schema.get("required")
    if not isinstance(required, list):
        required = []
        schema["required"] = required
    if "progress_intent" not in required:
        required.append("progress_intent")
    return schema


def _normalize_observation_arguments(
    arguments: object, *, provider: str, tool_name: str,
    accepts_budget: bool, input_schema: dict[str, object] | None = None,
) -> object:
    if (
        provider == "deepseek"
        and isinstance(arguments, dict)
        and isinstance(input_schema, dict)
        and input_schema.get("additionalProperties") is False
        and isinstance(input_schema.get("properties"), dict)
    ):
        properties = input_schema["properties"]
        required = input_schema.get("required", [])
        if (
            isinstance(required, list)
            and all(name in arguments for name in required)
        ):
            # DeepSeek occasionally adds an explanatory top-level field beside
            # an otherwise complete strict tool call. The advertised schema is
            # authoritative: discard only unknown siblings, while incomplete
            # or wrongly typed known fields still fail normal validation.
            arguments = {
                name: value for name, value in arguments.items()
                if name in properties
            }
    if (
        provider != "minimax"
        or tool_name not in MINIMAX_NORMALIZED_OBSERVATION_TOOLS
        or not isinstance(arguments, dict)
    ):
        return arguments
    normalized = {key: value for key, value in arguments.items() if key != "budget"}
    if tool_name == "discover_datasets":
        # The service treats omitted pagination values as its audited defaults.
        # MiniMax sometimes fills optional fields with null or a textual null
        # sentinel despite leaving them unset semantically. Omission is the
        # single unambiguous representation at this boundary. Preserve every
        # non-sentinel cursor so contract validation still rejects drift.
        if normalized.get("limit") is None:
            normalized.pop("limit", None)
        cursor = normalized.get("after")
        if cursor is None or (
            isinstance(cursor, str)
            and cursor.strip().casefold() in {
                "", "null", "none", "undefined", "unset", "not set", "n/a", "未设置",
            }
        ):
            normalized.pop("after", None)
    if tool_name == "retrieve_evidence" and normalized.get("scope") == "general":
        # MiniMax may populate the optional binding with a guessed partial object
        # or the string "null" even when it explicitly selected general scope.
        # General retrieval is unbound by contract, so discard that inapplicable
        # value. Bound and combined scopes still require an exact service binding.
        normalized["binding"] = None
    if accepts_budget:
        # MiniMax cannot tune retrieval size. Kernel supplies the audited
        # Knowledge defaults even if the model emits an invalid shorthand.
        normalized["budget"] = dict(MINIMAX_KNOWLEDGE_BUDGET)
    return normalized


def _observation_tool(
    definition: ObservationToolDefinition,
    service: ServiceBinding,
    observation_client: ObservationClient,
    *,
    provider: str,
) -> FunctionTool:
    async def invoke(context: ToolContext[dict[str, Any]], arguments_json: str):
        try:
            arguments = json.loads(arguments_json)
            if not isinstance(arguments, dict):
                raise AgentRunError(
                    "AGENT_MODEL_OUTPUT_INVALID",
                    diagnostics={"reason": "OBSERVATION_ARGUMENTS_INVALID"},
                )
            if definition.progress is None:
                service_arguments = arguments
            else:
                _intent, service_arguments = parse_progress_intent(definition, arguments)
            validate(service_arguments, definition.input_schema)
        except json.JSONDecodeError as error:
            raise AgentRunError(
                "AGENT_MODEL_OUTPUT_INVALID",
                diagnostics={"reason": "OBSERVATION_ARGUMENTS_JSON_INVALID"},
            ) from error
        except ValidationError as error:
            raise AgentRunError(
                "AGENT_MODEL_OUTPUT_INVALID",
                diagnostics={
                    "reason": "OBSERVATION_ARGUMENTS_INVALID",
                    "issues": [{
                        "path": "/" + "/".join(str(part) for part in error.path),
                        "validator": str(error.validator),
                        "observed_type": type(error.instance).__name__,
                    }],
                },
            ) from error
        return await observation_client.invoke(
            definition,
            service,
            call_id=context.tool_call_id,
            project_id=context.context["project_id"],
            trace_id=context.context["trace_id"],
            arguments=service_arguments,
            agent_run_id=context.context.get("agent_run_id"),
        )

    return FunctionTool(
        name=definition.name,
        description=_observation_description(definition, provider),
        params_json_schema=_observation_input_schema(definition, provider),
        on_invoke_tool=invoke,
        strict_json_schema=True,
        # ObservationClient validates service-owned JSON, including arbitrary RESULT
        # values. SDK strict output schemas cannot represent those open objects.
    )
