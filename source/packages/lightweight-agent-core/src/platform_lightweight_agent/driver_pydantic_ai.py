"""PydanticAI native-tool adapter with framework-neutral continuation."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from typing import Any

from ._driver_common import (
    AgentInvocationUsage,
    PYDANTIC_AI_CONTINUATION_SCHEMA_V1,
    _STABLE_ERROR_CODE,
    _SchemaValidatorBridge,
    _available_contracts,
    _compiled_contracts,
    _decision,
    _final_reply_type,
    _installed_version,
    _legacy_driver_prompt,
    _model_binding,
    _native_driver_prompt,
    _pydantic_usage_limits,
    _structured_output_type,
    _tool_description,
    _usage_observation,
    _validation_error,
)
from .runtime_contracts import AgentContext, AgentDriverFailure
from .spec import AgentDecision, AgentToolSpec, JsonValue, ToolCall, canonical_json


class PydanticAIDriver:
    """Primary native-tool adapter for the optional PydanticAI installation."""

    def __init__(
        self,
        agent: Any | None = None,
        *,
        model: Any | None = None,
        instructions: str = "Produce a safe workflow decision.",
        runner: Callable[[Any, str], Any] | None = None,
        driver_version: str | None = None,
        usage_observer: Callable[[AgentInvocationUsage], None] | None = None,
        model_name: str | None = None,
    ) -> None:
        if agent is None:
            try:
                from pydantic_ai import Agent
            except ImportError as error:
                raise RuntimeError(
                    "Install platform-lightweight-agent-core[pydantic-ai] to use PydanticAIDriver"
                ) from error
            agent = Agent(model, instructions=instructions)
        self._agent = agent
        self._runner = runner
        self._usage_observer = usage_observer
        self._model_name = model_name or (model if isinstance(model, str) else None)
        self._instructions = instructions
        self._model_binding = _model_binding(model, agent)
        self.driver_version = (
            driver_version
            or "pydantic-ai-native-v3@"
            + _installed_version("pydantic-ai", "pydantic-ai-slim")
        )

    def execution_binding(self) -> dict[str, JsonValue]:
        """Return the JSON-only model/prompt binding frozen into a run."""

        return {
            "driver_version": self.driver_version,
            "provider": "pydantic-ai",
            "model": self._model_name or self._model_binding,
            "instructions": self._instructions,
            "protocol": "native-tools-v3",
        }

    def decide(self, context: AgentContext) -> AgentDecision:
        if context.workflow_schema_version == "v6-light-workflow-v1":
            return self._decide_legacy(context)
        if context.workflow_schema_version != "v6-light-workflow-v2":
            raise AgentDriverFailure("AGENT_TOOL_CONTRACT_INVALID")
        if self._runner is not None:
            raise RuntimeError(
                "custom Agent runners are supported only for v1 workflows"
            )
        return self._decide_native(context)

    async def decide_async(self, context: AgentContext) -> AgentDecision:
        if context.workflow_schema_version not in {
            "v6-light-workflow-v3",
            "v6-light-workflow-v4",
        }:
            raise AgentDriverFailure("AGENT_TOOL_CONTRACT_INVALID")
        if self._runner is not None:
            raise RuntimeError(
                "custom Agent runners are supported only for v1 workflows"
            )
        (
            toolset,
            output_type,
            unexpected_error,
            provider_error,
            contracts,
            deferred_type,
            model_response_type,
            tool_call_type,
        ) = self._prepare_native(context)
        prompt, message_history, deferred_results = self._resume_inputs(
            context, contracts
        )
        usage_limits, usage_limit_error, request_limit = _pydantic_usage_limits(context)
        started = time.perf_counter()
        try:
            with self._agent.override(tools=[], toolsets=[toolset], native_tools=[]):
                result = await self._agent.run(
                    prompt,
                    output_type=output_type,
                    message_history=message_history,
                    deferred_tool_results=deferred_results,
                    retries=1,
                    usage_limits=usage_limits,
                )
        except usage_limit_error as error:
            # PydanticAI exposes one exception type for both fences. A request
            # fence is checked before the next call, so its count is exact. A
            # token fence is checked after a response; without a normal result
            # the adapter must not invent a request count.
            request_count = (
                request_limit if "request_limit" in str(error) else None
            )
            raise AgentDriverFailure(
                "AGENT_BUDGET_EXCEEDED", model_request_count=request_count
            ) from error
        except provider_error as error:
            raise AgentDriverFailure("AGENT_PROVIDER_FAILURE") from error
        except unexpected_error as error:
            raise AgentDriverFailure("AGENT_MODEL_OUTPUT_INVALID") from error
        usage = self._observe_usage(result, time.perf_counter() - started)
        decision = self._convert_native_result(
            result,
            contracts,
            deferred_type,
            model_response_type,
            tool_call_type,
            context,
        )
        return (
            replace(decision, usage=usage.to_mapping())
            if context.workflow_schema_version == "v6-light-workflow-v4"
            else decision
        )

    def _decide_legacy(self, context: AgentContext) -> AgentDecision:
        prompt = _legacy_driver_prompt(context)
        if self._runner is not None:
            raw = self._runner(self._agent, prompt)
        else:
            raw = self._agent.run_sync(prompt)
        return _decision(getattr(raw, "output", raw))

    def _decide_native(self, context: AgentContext) -> AgentDecision:
        (
            toolset,
            output_type,
            unexpected_error,
            provider_error,
            contracts,
            deferred_type,
            model_response_type,
            tool_call_type,
        ) = self._prepare_native(context)
        prompt, message_history, deferred_results = self._resume_inputs(
            context, contracts
        )
        usage_limits, usage_limit_error, request_limit = _pydantic_usage_limits(context)
        started = time.perf_counter()
        try:
            with self._agent.override(tools=[], toolsets=[toolset], native_tools=[]):
                result = self._agent.run_sync(
                    prompt,
                    output_type=output_type,
                    message_history=message_history,
                    deferred_tool_results=deferred_results,
                    retries=1,
                    usage_limits=usage_limits,
                )
        except usage_limit_error as error:
            request_count = (
                request_limit if "request_limit" in str(error) else None
            )
            raise AgentDriverFailure(
                "AGENT_BUDGET_EXCEEDED", model_request_count=request_count
            ) from error
        except provider_error as error:
            raise AgentDriverFailure("AGENT_PROVIDER_FAILURE") from error
        except unexpected_error as error:
            raise AgentDriverFailure("AGENT_MODEL_OUTPUT_INVALID") from error
        self._observe_usage(result, time.perf_counter() - started)
        return self._convert_native_result(
            result,
            contracts,
            deferred_type,
            model_response_type,
            tool_call_type,
            context,
        )

    def _prepare_native(self, context: AgentContext) -> tuple[Any, ...]:
        try:
            from pydantic_ai import DeferredToolRequests, ToolOutput
            from pydantic_ai.exceptions import (
                ModelAPIError,
                ModelRetry,
                UnexpectedModelBehavior,
            )
            from pydantic_ai.messages import ModelResponse, ToolCallPart
            from pydantic_ai.tools import ToolDefinition
            from pydantic_ai.toolsets import ExternalToolset
        except ImportError as error:
            raise RuntimeError(
                "Install platform-lightweight-agent-core[pydantic-ai] to use native tools"
            ) from error

        contracts = _compiled_contracts(context)
        available_contracts = _available_contracts(context, contracts)
        try:
            definitions = [
                ToolDefinition(
                    name=name,
                    description=_tool_description(spec),
                    parameters_json_schema=dict(spec.input_schema),
                    return_schema=dict(spec.output_schema),
                    include_return_schema=True,
                    strict=True,
                )
                for name, (spec, _, _) in available_contracts.items()
            ]
            validators = {
                name: _SchemaValidatorBridge(
                    input_validator,
                    lambda message, retry=ModelRetry: retry(message),
                )
                for name, (_, input_validator, _) in available_contracts.items()
            }
        except Exception as error:
            raise AgentDriverFailure("AGENT_TOOL_CONTRACT_INVALID") from error

        class ValidatedExternalToolset(ExternalToolset):
            async def get_tools(self, ctx: Any) -> dict[str, Any]:
                tools = await super().get_tools(ctx)
                return {
                    name: replace(
                        tool,
                        max_retries=1,
                        args_validator=validators[name],
                    )
                    for name, tool in tools.items()
                }

        toolset = ValidatedExternalToolset(
            definitions, id="workflow-tools"
        ).include_return_schemas()
        final_reply_name = "final_reply"
        suffix = 1
        while final_reply_name in contracts:
            final_reply_name = f"final_reply_{suffix}"
            suffix += 1
        output_type = [
            ToolOutput(
                (
                    _structured_output_type(canonical_json(context.output_schema))
                    if context.workflow_schema_version == "v6-light-workflow-v4"
                    and context.output_schema is not None
                    else _final_reply_type()
                ),
                name=final_reply_name,
                description=(
                    "Return the final structured workflow output."
                    if context.workflow_schema_version == "v6-light-workflow-v4"
                    else "Return the final workflow message."
                ),
                max_retries=1,
                strict=True,
            ),
            DeferredToolRequests,
        ]
        return (
            toolset,
            output_type,
            UnexpectedModelBehavior,
            (ModelAPIError, TimeoutError),
            contracts,
            DeferredToolRequests,
            ModelResponse,
            ToolCallPart,
        )

    def _resume_inputs(
        self,
        context: AgentContext,
        contracts: Mapping[str, tuple[AgentToolSpec, Any, Any]],
    ) -> tuple[str | None, list[Any] | None, Any | None]:
        """Restore one deferred-tool turn through PydanticAI's native protocol."""

        continuation = context.driver_continuation
        deferred = context.deferred_tool_results
        if continuation is None:
            if deferred or context.prior_tool_results:
                raise AgentDriverFailure("AGENT_CONTINUATION_INVALID")
            return _native_driver_prompt(context), None, None
        try:
            from pydantic_ai import DeferredToolRequests, ModelMessagesTypeAdapter
            from pydantic_ai.messages import ModelResponse, ToolCallPart

            if set(continuation) != {
                "schema_version",
                "driver_version",
                "messages",
            } or continuation.get("schema_version") != (
                PYDANTIC_AI_CONTINUATION_SCHEMA_V1
            ):
                raise ValueError("unsupported continuation schema")
            if continuation.get("driver_version") != self.driver_version:
                raise ValueError("continuation driver version changed")
            raw_messages = continuation.get("messages")
            if not isinstance(raw_messages, list):
                raise ValueError("continuation messages are malformed")
            messages = list(ModelMessagesTypeAdapter.validate_python(raw_messages))
            last_response = next(
                (
                    message
                    for message in reversed(messages)
                    if isinstance(message, ModelResponse)
                ),
                None,
            )
            if last_response is None:
                raise ValueError("continuation has no model response")
            pending_calls = [
                part
                for part in last_response.parts
                if isinstance(part, ToolCallPart) and part.tool_name in contracts
            ]
            pending = {call.tool_call_id: call for call in pending_calls}
            if not pending_calls or len(pending) != len(pending_calls):
                raise ValueError("continuation call IDs are missing or duplicated")
            records: dict[str, Mapping[str, JsonValue]] = {}
            action_ids: set[str] = set()
            for raw in deferred:
                if not isinstance(raw, Mapping) or set(raw) != {
                    "action_id",
                    "tool_call_id",
                    "name",
                    "arguments",
                    "succeeded",
                    "output",
                    "error",
                }:
                    raise ValueError("deferred tool result is malformed")
                action_id = raw.get("action_id")
                call_id = raw.get("tool_call_id")
                name = raw.get("name")
                arguments = raw.get("arguments")
                succeeded = raw.get("succeeded")
                if (
                    not isinstance(action_id, str)
                    or not action_id
                    or action_id in action_ids
                    or not isinstance(call_id, str)
                    or call_id in records
                    or call_id not in pending
                    or not isinstance(name, str)
                    or name not in contracts
                    or not isinstance(arguments, Mapping)
                    or not isinstance(succeeded, bool)
                ):
                    raise ValueError("deferred tool result identity is invalid")
                call = pending[call_id]
                if call.tool_name != name or call.args_as_dict() != dict(arguments):
                    raise ValueError("deferred tool result does not match its call")
                if _validation_error(contracts[name][1], arguments) is not None:
                    raise ValueError("deferred tool arguments are invalid")
                if succeeded:
                    output = raw.get("output")
                    if (
                        raw.get("error") is not None
                        or _validation_error(
                            contracts[name][2], output, label="Tool output"
                        )
                        is not None
                    ):
                        raise ValueError("deferred tool output is invalid")
                elif (
                    raw.get("output") is not None
                    or not isinstance(raw.get("error"), str)
                    or _STABLE_ERROR_CODE.fullmatch(raw["error"]) is None
                ):
                    raise ValueError("deferred tool failure is invalid")
                records[call_id] = raw
                action_ids.add(action_id)
            if set(records) != set(pending):
                raise ValueError("deferred tool results are incomplete")
            calls = {
                call_id: (
                    record["output"]
                    if record["succeeded"] is True
                    else {"error": record["error"]}
                )
                for call_id, record in records.items()
            }
            requests = DeferredToolRequests(calls=pending_calls)
            resolved = requests.build_results(
                calls=calls,
                metadata={
                    call_id: {"action_id": record["action_id"]}
                    for call_id, record in records.items()
                },
            )
            return None, messages, resolved
        except AgentDriverFailure:
            raise
        except Exception as error:
            raise AgentDriverFailure("AGENT_CONTINUATION_INVALID") from error

    def _continuation_for_calls(
        self,
        result: Any,
        context: AgentContext,
        raw_calls: Iterable[Any],
        model_response_type: type[Any],
        tool_call_type: type[Any],
    ) -> dict[str, JsonValue]:
        """Serialize only version-pinned SDK JSON, never a live SDK object."""

        try:
            from pydantic_ai import ModelMessagesTypeAdapter

            existing: list[Any] = []
            if context.driver_continuation is not None:
                raw_existing = context.driver_continuation.get("messages")
                if not isinstance(raw_existing, list):
                    raise ValueError("continuation messages are malformed")
                existing = list(
                    ModelMessagesTypeAdapter.validate_python(raw_existing)
                )
            selected_ids = {call.tool_call_id for call in raw_calls}
            if None in selected_ids or len(selected_ids) == 0:
                raise ValueError("native calls require opaque call IDs")
            new_messages = list(result.new_messages())
            response_index = next(
                (
                    index
                    for index in range(len(new_messages) - 1, -1, -1)
                    if isinstance(new_messages[index], model_response_type)
                ),
                None,
            )
            if response_index is None:
                raise ValueError("native result has no model response")
            response = new_messages[response_index]
            response_parts = [
                part
                for part in response.parts
                if not isinstance(part, tool_call_type)
                or part.tool_call_id in selected_ids
            ]
            retained_ids = {
                part.tool_call_id
                for part in response_parts
                if isinstance(part, tool_call_type)
            }
            if retained_ids != selected_ids:
                raise ValueError("native result call IDs do not match its response")
            # PydanticAI may append a request containing synthetic returns for
            # a simultaneously selected final-output tool or retry prompts for
            # invalid siblings. Those parts describe the SDK's abandoned
            # handling of this same response and conflict with the host's real
            # deferred results. A resumable continuation must therefore end at
            # the filtered response batch itself.
            new_messages = new_messages[: response_index + 1]
            new_messages[-1] = replace(response, parts=response_parts)
            if not isinstance(new_messages[-1], model_response_type):
                raise ValueError("native continuation must end with a model response")
            messages = ModelMessagesTypeAdapter.dump_python(
                [*existing, *new_messages], mode="json"
            )
            if not isinstance(messages, list):
                raise ValueError("serialized continuation is malformed")
            continuation: dict[str, JsonValue] = {
                "schema_version": PYDANTIC_AI_CONTINUATION_SCHEMA_V1,
                "driver_version": self.driver_version,
                "messages": messages,
            }
            # Enforce JSON detachment now rather than at journal commit time.
            return json.loads(canonical_json(continuation))
        except Exception as error:
            raise AgentDriverFailure("AGENT_CONTINUATION_INVALID") from error

    def _observe_usage(self, result: object, duration: float) -> AgentInvocationUsage:
        usage = _usage_observation(
            result,
            duration_seconds=duration,
            provider="pydantic-ai",
            model=self._model_name,
        )
        if self._usage_observer is not None:
            self._usage_observer(usage)
        return usage

    def _convert_native_result(
        self,
        result: Any,
        contracts: Mapping[str, tuple[AgentToolSpec, Any, Any]],
        deferred_type: type[Any],
        model_response_type: type[Any],
        tool_call_type: type[Any],
        context: AgentContext,
    ) -> AgentDecision:
        output = result.output
        available_contracts = _available_contracts(context, contracts)

        def convert_calls(raw_calls: Iterable[Any]) -> AgentDecision:
            raw_calls = tuple(raw_calls)
            calls: list[ToolCall] = []
            try:
                for call in raw_calls:
                    if call.tool_name not in available_contracts:
                        raise AgentDriverFailure("AGENT_MODEL_OUTPUT_INVALID")
                    arguments = call.args_as_dict()
                    message = _validation_error(contracts[call.tool_name][1], arguments)
                    if message is not None:
                        raise AgentDriverFailure("AGENT_MODEL_OUTPUT_INVALID")
                    calls.append(
                        ToolCall(
                            contracts[call.tool_name][0].tool,
                            arguments,
                            call_id=call.tool_call_id,
                        )
                    )
                if not calls:
                    raise AgentDriverFailure("AGENT_MODEL_OUTPUT_INVALID")
                decision = AgentDecision(
                    "",
                    tuple(calls),
                    continuation=self._continuation_for_calls(
                        result,
                        context,
                        raw_calls,
                        model_response_type,
                        tool_call_type,
                    ),
                )
            except AgentDriverFailure:
                raise
            except Exception as error:
                raise AgentDriverFailure("AGENT_MODEL_OUTPUT_INVALID") from error
            return decision

        def simultaneous_native_calls() -> list[Any]:
            for message in reversed(result.all_messages()):
                if isinstance(message, model_response_type):
                    # Tool requests outrank a final reply only while those
                    # tools were actually offered in this SDK invocation.
                    return [
                        part
                        for part in message.parts
                        if isinstance(part, tool_call_type)
                        and part.tool_name in available_contracts
                    ]
            return []

        if isinstance(output, deferred_type):
            return convert_calls(output.calls)
        if context.workflow_schema_version == "v6-light-workflow-v4" and isinstance(
            output, Mapping
        ):
            if native_calls := simultaneous_native_calls():
                return convert_calls(native_calls)
            return AgentDecision("", output=dict(output))
        if isinstance(output, _final_reply_type()):
            # PydanticAI handles output tools before external tools. If a model
            # emits both, recover the skipped external calls from that response
            # so the public protocol can consistently prioritize tool requests.
            if native_calls := simultaneous_native_calls():
                return convert_calls(native_calls)
            return AgentDecision(output.message)
        raise AgentDriverFailure("AGENT_MODEL_OUTPUT_INVALID")
