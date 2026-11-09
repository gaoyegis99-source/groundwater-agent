"""Optional model-framework adapters behind the small :class:`AgentDriver` API."""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from .runtime_contracts import AgentContext, AgentDriverFailure
from .spec import (
    AgentDecision,
    AgentToolSpec,
    JsonValue,
    WorkflowValidationError,
    canonical_json,
)

_STABLE_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
SCRIPTED_DECISIONS_SCHEMA_V1 = "v6-scripted-decisions-v1"
PYDANTIC_AI_CONTINUATION_SCHEMA_V1 = "v6-pydantic-ai-continuation-v1"


@dataclass(frozen=True)
class AgentInvocationUsage:
    """Framework-neutral observations from one independent model invocation."""

    request_count: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    # SDK request count and transport retry count are deliberately separate.
    # Neither current supported SDK guarantees transport-attempt reporting, so
    # ``None`` means unknown rather than "one network request".
    transport_attempts: int | None = None
    duration_seconds: float | None = None
    provider: str | None = None
    model: str | None = None

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "request_count": self.request_count,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cached_tokens": self.cached_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "transport_attempts": self.transport_attempts,
            "duration_seconds": self.duration_seconds,
            "provider": self.provider,
            "model": self.model,
        }


def _nonnegative_int(value: object) -> int | None:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else None
    )


def _attribute(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _usage_observation(
    result: object,
    *,
    duration_seconds: float,
    provider: str,
    model: str | None,
) -> AgentInvocationUsage:
    usage: object | None = None
    candidate = getattr(result, "usage", None)
    if inspect.ismethod(candidate) or inspect.isfunction(candidate):
        try:
            usage = candidate()
        except TypeError:
            usage = None
    elif candidate is not None:
        usage = candidate
    if usage is None:
        wrapper = getattr(result, "context_wrapper", None)
        usage = getattr(wrapper, "usage", None)
    input_details = _attribute(usage, "input_tokens_details") if usage else None
    output_details = _attribute(usage, "output_tokens_details") if usage else None
    details = _attribute(usage, "details") if usage else None
    cached = _nonnegative_int(_attribute(input_details, "cached_tokens"))
    if cached is None:
        cached = _nonnegative_int(_attribute(details, "cache_read_tokens"))
    reasoning = _nonnegative_int(_attribute(output_details, "reasoning_tokens"))
    if reasoning is None:
        reasoning = _nonnegative_int(_attribute(details, "reasoning_tokens"))
    transport_attempts = _nonnegative_int(_attribute(usage, "transport_attempts"))
    if transport_attempts is None:
        transport_attempts = _nonnegative_int(_attribute(details, "transport_attempts"))
    return AgentInvocationUsage(
        request_count=_nonnegative_int(_attribute(usage, "requests")),
        input_tokens=_nonnegative_int(_attribute(usage, "input_tokens")),
        output_tokens=_nonnegative_int(_attribute(usage, "output_tokens")),
        total_tokens=_nonnegative_int(_attribute(usage, "total_tokens")),
        cached_tokens=cached,
        reasoning_tokens=reasoning,
        transport_attempts=transport_attempts,
        duration_seconds=max(duration_seconds, 0.0),
        provider=provider,
        model=model,
    )


def _pydantic_usage_limits(
    context: AgentContext,
) -> tuple[Any | None, type[Exception], int | None]:
    """Build PydanticAI's native request and token fences from durable usage.

    A driver call may issue more than one model request when PydanticAI repairs
    invalid structured output.  Its ``UsageLimits`` checks the request count
    before every model request, which is the only layer that can stop that
    internal retry before it crosses the workflow budget.
    """

    try:
        from pydantic_ai.exceptions import UsageLimitExceeded
        from pydantic_ai.usage import UsageLimits
    except ImportError as error:
        raise RuntimeError(
            "Install platform-lightweight-agent-core[pydantic-ai] to use native tools"
        ) from error
    if context.budget is None:
        return None, UsageLimitExceeded, None
    used = context.model_requests_used
    tokens_used = context.total_tokens_used
    if (
        not isinstance(used, int)
        or isinstance(used, bool)
        or used < 0
        or not isinstance(tokens_used, int)
        or isinstance(tokens_used, bool)
        or tokens_used < 0
    ):
        raise AgentDriverFailure("AGENT_USAGE_INVALID")
    remaining = context.budget.max_model_requests - used
    if remaining < 1:
        raise AgentDriverFailure("AGENT_BUDGET_EXCEEDED")
    remaining_tokens = None
    if context.budget.max_total_tokens is not None:
        remaining_tokens = context.budget.max_total_tokens - tokens_used
        if remaining_tokens < 1:
            raise AgentDriverFailure("AGENT_BUDGET_EXCEEDED")
    return (
        UsageLimits(
            request_limit=remaining,
            total_tokens_limit=remaining_tokens,
        ),
        UsageLimitExceeded,
        remaining,
    )


def _remaining_model_requests(context: AgentContext) -> int | None:
    """Return the durable request allowance for one native SDK run."""

    if context.budget is None:
        return None
    used = context.model_requests_used
    if not isinstance(used, int) or isinstance(used, bool) or used < 0:
        raise AgentDriverFailure("AGENT_USAGE_INVALID")
    remaining = context.budget.max_model_requests - used
    if remaining < 1:
        raise AgentDriverFailure("AGENT_BUDGET_EXCEEDED")
    return remaining


def _legacy_driver_prompt(context: AgentContext) -> str:
    """The frozen v1 JSON-envelope contract."""

    payload = {
        "run_id": context.run_id,
        "workflow_id": context.workflow_id,
        "workflow_version": context.workflow_version,
        "step_id": context.step_id,
        "turn": context.turn,
        "input": context.step_input,
        "values": context.values,
        "prior_tool_results": context.prior_tool_results,
        "allowed_tools": [item.to_mapping() for item in context.allowed_tools],
    }
    return (
        "Return exactly one JSON object matching "
        '{"message": string, "tool_calls": [{"tool": {"tool_id": string, '
        '"version": string, "package_digest": string}, "arguments": object}]}. '
        "Use an empty tool_calls array when replying. Do not include credentials.\n\n"
        + canonical_json(payload)
    )


def _native_driver_prompt(context: AgentContext) -> str:
    """Build v2/v3 context without package identities or a JSON envelope."""

    payload = {
        "run_id": context.run_id,
        "workflow_id": context.workflow_id,
        "step_id": context.step_id,
        "turn": context.turn,
        "input": context.step_input,
        "values": context.values,
        "history": context.prior_tool_results,
    }
    final_kind = (
        "a final structured result matching the provided output schema"
        if context.workflow_schema_version == "v6-light-workflow-v4"
        else "a final message"
    )
    return (
        f"Complete this workflow turn. Reply with {final_kind} or request one or "
        "more available tools. Never include credentials.\n\n" + canonical_json(payload)
    )


def _decision(value: object) -> AgentDecision:
    if isinstance(value, AgentDecision):
        return value
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise WorkflowValidationError(
                "agent driver did not return a JSON decision"
            ) from error
    if not isinstance(value, Mapping):
        raise WorkflowValidationError("agent driver returned an unsupported decision")
    return AgentDecision.from_mapping(value)


def _installed_version(*names: str) -> str:
    for name in names:
        try:
            return version(name)
        except PackageNotFoundError:
            continue
    return "injected"


def _model_binding(model: object, agent: object) -> str:
    """Describe a configured model without retaining an SDK object."""

    candidate = model
    if candidate is None:
        try:
            candidate = getattr(agent, "model", None)
        except Exception:  # noqa: BLE001 - provider properties may be deferred
            candidate = None
    if isinstance(candidate, str) and candidate:
        return candidate
    if candidate is None:
        return "unspecified"
    model_name = getattr(candidate, "model_name", None)
    # Persist a provider-neutral identifier, never an SDK class/module path.
    implementation = type(candidate).__qualname__
    if isinstance(model_name, str) and model_name:
        return f"{implementation}:{model_name}"
    return implementation


@lru_cache(maxsize=1)
def _final_reply_type() -> type[Any]:
    try:
        from pydantic import BaseModel, ConfigDict, Field
    except ImportError as error:
        raise RuntimeError(
            "A native Agent adapter requires the selected framework extra"
        ) from error

    class FinalReply(BaseModel):
        model_config = ConfigDict(extra="forbid")

        message: str = Field(max_length=64_000)

    FinalReply.__name__ = "AgentFinalReply"
    return FinalReply


@lru_cache(maxsize=128)
def _structured_output_type(serialized_schema: str) -> Any:
    """Expose and enforce the exact frozen output schema in PydanticAI.

    ``ToolOutput`` needs both halves: its JSON-schema hook tells the model the
    desired shape, while the core-schema hook must reject an invalid response
    so PydanticAI can spend its configured native repair retry.  The shared
    jsonschema validator keeps this boundary identical to the kernel's later
    durable validation rather than maintaining a second hand-written type
    mapper.
    """

    try:
        from pydantic_core import core_schema
    except ImportError as error:
        raise RuntimeError(
            "A native Agent adapter requires the selected framework extra"
        ) from error
    schema = json.loads(serialized_schema)
    validator = _compile_schema(serialized_schema)

    class WorkflowStructuredOutput(dict[str, Any]):
        __is_model_like__ = True

        @classmethod
        def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> Any:
            del source, handler

            def validate(value: object) -> WorkflowStructuredOutput:
                issue = _validation_error(validator, value, label="Final output")
                if issue is not None:
                    raise ValueError(issue)
                if not isinstance(value, Mapping):
                    # jsonschema should already reject this, but keep the
                    # return type explicit if a nonstandard validator appears.
                    raise TypeError("Final output must be an object")
                return cls(dict(value))

            return core_schema.no_info_after_validator_function(
                validate,
                core_schema.dict_schema(
                    keys_schema=core_schema.str_schema(),
                    values_schema=core_schema.any_schema(),
                ),
            )

        @classmethod
        def __get_pydantic_json_schema__(cls, source: Any, handler: Any) -> Any:
            del source, handler
            return dict(schema)

    WorkflowStructuredOutput.__name__ = "WorkflowStructuredOutput"
    return WorkflowStructuredOutput


@lru_cache(maxsize=256)
def _compile_schema(serialized_schema: str) -> Any:
    try:
        from jsonschema.validators import validator_for
        from referencing import Registry, Resource
    except ImportError as error:
        raise RuntimeError(
            "Install the selected Agent framework extra to validate tool schemas"
        ) from error
    schema = json.loads(serialized_schema)
    validator_type = validator_for(schema)
    validator_type.check_schema(schema)
    resolver = (
        Registry()
        .with_resource("urn:platform-agent:schema", Resource.from_contents(schema))
        .resolver("urn:platform-agent:schema")
    )

    schema_maps = {
        "$defs",
        "definitions",
        "dependentSchemas",
        "patternProperties",
        "properties",
    }
    schema_sequences = {"allOf", "anyOf", "oneOf", "prefixItems"}
    schema_values = {
        "additionalProperties",
        "contains",
        "contentSchema",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    }

    def validate_references(node: object) -> None:
        if not isinstance(node, Mapping):
            return
        for key, child in node.items():
            if key in {"$ref", "$dynamicRef"} and isinstance(child, str):
                resolver.lookup(child)
            elif key in schema_maps and isinstance(child, Mapping):
                for nested_schema in child.values():
                    validate_references(nested_schema)
            elif key in schema_sequences and isinstance(child, list):
                for nested_schema in child:
                    validate_references(nested_schema)
            elif key in schema_values and isinstance(child, Mapping):
                validate_references(child)
            elif key in schema_values and isinstance(child, list):
                for nested_schema in child:
                    validate_references(nested_schema)

    validate_references(schema)
    return validator_type(schema)


def _validator(schema: Mapping[str, JsonValue]) -> Any:
    try:
        return _compile_schema(canonical_json(schema))
    except Exception as error:
        if isinstance(error, AgentDriverFailure):
            raise
        raise AgentDriverFailure("AGENT_TOOL_CONTRACT_INVALID") from error


def _validation_error(
    validator: Any, value: object, *, label: str = "Tool arguments"
) -> str | None:
    errors = sorted(
        validator.iter_errors(value),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    if not errors:
        return None
    error = errors[0]
    path = "/" + "/".join(str(part) for part in error.absolute_path)
    location = path if path != "/" else "the document root"
    return f"{label} failed schema validation at {location} ({error.validator})."


def _compiled_contracts(
    context: AgentContext,
) -> dict[str, tuple[AgentToolSpec, Any, Any]]:
    contracts: dict[str, tuple[AgentToolSpec, Any, Any]] = {}
    try:
        for spec in context.tool_specs:
            input_validator = _validator(spec.input_schema)
            output_validator = _validator(spec.output_schema)
            for example in spec.examples:
                if _validation_error(input_validator, example.arguments) is not None:
                    raise AgentDriverFailure("AGENT_TOOL_CONTRACT_INVALID")
                if (
                    example.output is not None
                    and _validation_error(output_validator, example.output) is not None
                ):
                    raise AgentDriverFailure("AGENT_TOOL_CONTRACT_INVALID")
            contracts[spec.name] = (spec, input_validator, output_validator)
    except AgentDriverFailure:
        raise
    except Exception as error:
        raise AgentDriverFailure("AGENT_TOOL_CONTRACT_INVALID") from error
    _validate_prior_outputs(context, contracts)
    return contracts


def _available_contracts(
    context: AgentContext,
    contracts: Mapping[str, tuple[AgentToolSpec, Any, Any]],
) -> Mapping[str, tuple[AgentToolSpec, Any, Any]]:
    """Apply the frozen host tool budget to this SDK request only."""

    remaining = context.tool_calls_remaining
    if remaining is not None and (
        not isinstance(remaining, int)
        or isinstance(remaining, bool)
        or remaining < 0
    ):
        raise AgentDriverFailure("AGENT_CONTINUATION_INVALID")
    return contracts if remaining is None or remaining > 0 else {}


def _validate_prior_outputs(
    context: AgentContext,
    contracts: Mapping[str, tuple[AgentToolSpec, Any, Any]],
) -> None:
    for history_item in context.prior_tool_results:
        raw_results = history_item.get("tool_results")
        if raw_results is None:
            continue
        if not isinstance(raw_results, list):
            raise AgentDriverFailure("AGENT_TOOL_OUTPUT_INVALID")
        for raw_result in raw_results:
            if not isinstance(raw_result, Mapping):
                raise AgentDriverFailure("AGENT_TOOL_OUTPUT_INVALID")
            if set(raw_result) != {
                "name",
                "arguments",
                "succeeded",
                "output",
                "error",
            }:
                raise AgentDriverFailure("AGENT_TOOL_OUTPUT_INVALID")
            name = raw_result.get("name")
            succeeded = raw_result.get("succeeded")
            arguments = raw_result.get("arguments")
            if (
                not isinstance(name, str)
                or name not in contracts
                or not isinstance(succeeded, bool)
                or not isinstance(arguments, Mapping)
                or _validation_error(contracts[name][1], arguments) is not None
            ):
                raise AgentDriverFailure("AGENT_TOOL_OUTPUT_INVALID")
            if succeeded is True:
                output = raw_result.get("output")
                if (
                    raw_result.get("error") is not None
                    or _validation_error(
                        contracts[name][2], output, label="Tool output"
                    )
                    is not None
                ):
                    raise AgentDriverFailure("AGENT_TOOL_OUTPUT_INVALID")
            elif (
                not isinstance(raw_result.get("error"), str)
                or _STABLE_ERROR_CODE.fullmatch(raw_result["error"]) is None
            ):
                raise AgentDriverFailure("AGENT_TOOL_OUTPUT_INVALID")


def _tool_description(spec: AgentToolSpec) -> str:
    return spec.model_description


class _SchemaValidatorBridge:
    """Adapt ``jsonschema`` to PydanticAI's SchemaValidator protocol."""

    def __init__(
        self, validator: Any, error_factory: Callable[[str], Exception]
    ) -> None:
        self._validator = validator
        self._error_factory = error_factory

    def validate_json(self, value: str | bytes | bytearray, **_: Any) -> dict[str, Any]:
        try:
            parsed = json.loads(value)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise self._error_factory("Tool arguments must be valid JSON.") from error
        return self.validate_python(parsed)

    def validate_python(self, value: Any, **_: Any) -> dict[str, Any]:
        message = _validation_error(self._validator, value)
        if message is not None:
            raise self._error_factory(message)
        if not isinstance(value, dict):
            raise self._error_factory("Tool arguments must be a JSON object.")
        return value


class ScriptedAgentDriver:
    """Deterministic driver for embeddings, tests, demos, and CLI examples."""

    driver_version = "scripted-v2"

    def __init__(
        self,
        decisions: (
            Iterable[AgentDecision | Mapping[str, Any] | str]
            | Mapping[
                str, Iterable[AgentDecision | Mapping[str, Any] | str]
            ]
        ),
    ) -> None:
        if isinstance(decisions, Mapping):
            scripts: dict[str, tuple[AgentDecision, ...]] = {}
            for address, items in decisions.items():
                if not isinstance(address, str) or not address:
                    raise WorkflowValidationError(
                        "scripted driver addresses must be non-empty strings"
                    )
                if isinstance(items, (str, bytes, Mapping)):
                    raise WorkflowValidationError(
                        "scripted driver address values must be decision lists"
                    )
                scripts[address] = tuple(_scripted_decision(item) for item in items)
            self._scripts = scripts
            self._default: tuple[AgentDecision, ...] = ()
        else:
            self._scripts = {}
            self._default = tuple(_scripted_decision(item) for item in decisions)

    def execution_binding(self) -> dict[str, JsonValue]:
        return {
            "driver_version": self.driver_version,
            "provider": "scripted",
            "model": "offline-script",
            "protocol": "scripted-v2",
            "simulated": True,
        }

    def decide(self, context: AgentContext) -> AgentDecision:
        decisions = self._decisions_for(context)
        index = context.turn - 1
        if index < 0 or index >= len(decisions):
            raise WorkflowValidationError(
                "scripted driver has no decision for this Agent turn"
            )
        return decisions[index]

    async def decide_async(self, context: AgentContext) -> AgentDecision:
        try:
            import anyio
        except ImportError as error:
            raise RuntimeError(
                "Install platform-lightweight-agent-core[workflow] for async scripts"
            ) from error
        await anyio.lowlevel.checkpoint()
        return self.decide(context)

    def _decisions_for(self, context: AgentContext) -> tuple[AgentDecision, ...]:
        workflow_id = context.definition_workflow_id or context.workflow_id
        version = context.definition_workflow_version or context.workflow_version
        step_id = context.definition_step_id or context.step_id
        address = f"{workflow_id}@{version}:{step_id}"
        return self._scripts.get(address, self._default)


def _scripted_decision(
    value: AgentDecision | Mapping[str, Any] | str,
) -> AgentDecision:
    decision = _decision(value)
    if decision.usage is not None:
        return decision
    return replace(
        decision,
        usage={
            "request_count": 1,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "transport_attempts": 0,
            "duration_seconds": 0.0,
            "provider": "scripted",
            "model": "offline-script",
        },
    )
