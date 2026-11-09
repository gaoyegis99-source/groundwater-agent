"""OpenAI Agents SDK adapter; no SDK runtime state leaves this module."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from ._driver_common import (
    AgentInvocationUsage,
    _SchemaValidatorBridge,
    _available_contracts,
    _compiled_contracts,
    _decision,
    _final_reply_type,
    _installed_version,
    _legacy_driver_prompt,
    _model_binding,
    _native_driver_prompt,
    _remaining_model_requests,
    _tool_description,
    _usage_observation,
    _validation_error,
    _validator,
)
from .runtime_contracts import AgentContext, AgentDriverFailure
from .spec import AgentDecision, AgentToolSpec, JsonValue, ToolCall


class OpenAIAgentsDriver:
    """Native-tool adapter for ``openai-agents`` with a frozen v1 fallback."""

    def __init__(
        self,
        agent: Any | None = None,
        *,
        name: str = "workflow-agent",
        instructions: str = "Produce a safe workflow decision.",
        model: Any | None = None,
        runner: Callable[[Any, str], Any] | None = None,
        driver_version: str | None = None,
        usage_observer: Callable[[AgentInvocationUsage], None] | None = None,
        model_name: str | None = None,
    ) -> None:
        if agent is None:
            try:
                from agents import Agent
            except ImportError as error:
                raise RuntimeError(
                    "Install platform-lightweight-agent-core[openai-agents] to use OpenAIAgentsDriver"
                ) from error
            agent = Agent(name=name, instructions=instructions, model=model)
        self._agent = agent
        self._runner = runner
        self._usage_observer = usage_observer
        self._model_name = model_name or (model if isinstance(model, str) else None)
        agent_instructions = getattr(agent, "instructions", None)
        self._instructions = (
            agent_instructions if isinstance(agent_instructions, str) else instructions
        )
        self._model_binding = _model_binding(model, agent)
        self.driver_version = (
            driver_version
            or "openai-agents-native-v2@" + _installed_version("openai-agents")
        )

    def execution_binding(self) -> dict[str, JsonValue]:
        """Return the JSON-only model/prompt binding frozen into a run."""

        return {
            "driver_version": self.driver_version,
            "provider": "openai-agents",
            "model": self._model_name or self._model_binding,
            "instructions": self._instructions,
            "protocol": "native-tools-v2",
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
            runner_type,
            prepared_agent,
            prompt,
            run_config,
            errors,
            provider_errors,
            contracts,
        ) = self._prepare_native(context)
        remaining_requests = _remaining_model_requests(context)
        max_turns = 2 if remaining_requests is None else min(2, remaining_requests)
        request_budget_limited = (
            remaining_requests is not None and remaining_requests < 2
        )
        started = time.perf_counter()
        try:
            result = await runner_type.run(
                prepared_agent,
                prompt,
                max_turns=max_turns,
                run_config=run_config,
            )
        except provider_errors as error:
            raise AgentDriverFailure("AGENT_PROVIDER_FAILURE") from error
        except errors as error:
            if request_budget_limited and isinstance(error, errors[0]):
                raise AgentDriverFailure(
                    "AGENT_BUDGET_EXCEEDED",
                    model_request_count=max_turns,
                ) from error
            raise AgentDriverFailure("AGENT_MODEL_OUTPUT_INVALID") from error
        usage = self._observe_usage(result, time.perf_counter() - started)
        decision = self._convert_native_result(result, contracts, context)
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
            try:
                from agents import Runner
            except ImportError as error:
                raise RuntimeError(
                    "openai-agents is required to invoke this driver"
                ) from error
            raw = Runner.run_sync(self._agent, prompt)
        return _decision(getattr(raw, "final_output", raw))

    def _decide_native(self, context: AgentContext) -> AgentDecision:
        (
            runner_type,
            prepared_agent,
            prompt,
            run_config,
            errors,
            provider_errors,
            contracts,
        ) = self._prepare_native(context)
        started = time.perf_counter()
        try:
            result = runner_type.run_sync(
                prepared_agent,
                prompt,
                max_turns=2,
                run_config=run_config,
            )
        except provider_errors as error:
            raise AgentDriverFailure("AGENT_PROVIDER_FAILURE") from error
        except errors as error:
            raise AgentDriverFailure("AGENT_MODEL_OUTPUT_INVALID") from error
        self._observe_usage(result, time.perf_counter() - started)
        return self._convert_native_result(result, contracts, context)

    def _prepare_native(self, context: AgentContext) -> tuple[Any, ...]:
        try:
            from agents import AgentOutputSchemaBase, FunctionTool, RunConfig, Runner
            from agents.exceptions import (
                MaxTurnsExceeded,
                ModelBehaviorError,
                ModelTimeoutError,
            )
            from agents.run_config import ToolExecutionConfig
            from agents.tool_guardrails import (
                ToolGuardrailFunctionOutput,
                ToolInputGuardrail,
            )
            from openai import APIError
        except ImportError as error:
            raise RuntimeError(
                "Install platform-lightweight-agent-core[openai-agents] to use native tools"
            ) from error

        contracts = _compiled_contracts(context)
        final_output_schema = None
        if context.workflow_schema_version == "v6-light-workflow-v4":
            if context.output_schema is None:
                raise AgentDriverFailure("AGENT_TOOL_CONTRACT_INVALID")
            output_validator = _validator(context.output_schema)

            class WorkflowOutputSchema(AgentOutputSchemaBase):
                def is_plain_text(self) -> bool:
                    return False

                def name(self) -> str:
                    return "workflow_output"

                def json_schema(self) -> dict[str, Any]:
                    return dict(context.output_schema or {})

                def is_strict_json_schema(self) -> bool:
                    return True

                def validate_json(self, json_str: str) -> dict[str, Any]:
                    try:
                        parsed = json.loads(json_str)
                    except json.JSONDecodeError as error:
                        raise ModelBehaviorError(
                            "Workflow output was not valid JSON."
                        ) from error
                    message = _validation_error(
                        output_validator, parsed, label="Workflow output"
                    )
                    if message is not None or not isinstance(parsed, dict):
                        raise ModelBehaviorError(
                            message or "Workflow output must be an object."
                        )
                    return parsed

            final_output_schema = WorkflowOutputSchema()
        available_contracts = _available_contracts(context, contracts)
        function_tools = []
        for name, (spec, input_validator, _) in available_contracts.items():
            validator = _SchemaValidatorBridge(input_validator, ValueError)

            def validate_input(data: Any, *, current: Any = validator) -> Any:
                try:
                    current.validate_json(data.context.tool_arguments)
                except ValueError:
                    return ToolGuardrailFunctionOutput.reject_content(
                        "Tool arguments did not match the declared schema. Correct them and try once more."
                    )
                return ToolGuardrailFunctionOutput.allow()

            async def never_execute(_: Any, __: str, *, tool_name: str = name) -> Any:
                raise RuntimeError(
                    f"host-managed tool {tool_name!r} reached an SDK execution handler"
                )

            try:
                function_tools.append(
                    FunctionTool(
                        name=name,
                        description=_tool_description(spec),
                        params_json_schema=dict(spec.input_schema),
                        on_invoke_tool=never_execute,
                        strict_json_schema=True,
                        tool_input_guardrails=[
                            ToolInputGuardrail(
                                guardrail_function=validate_input,
                                name=f"validate_{name}",
                            )
                        ],
                        needs_approval=True,
                        output_json_schema=dict(spec.output_schema),
                    )
                )
            except Exception as error:
                raise AgentDriverFailure("AGENT_TOOL_CONTRACT_INVALID") from error

        prepared_agent = self._agent.clone(
            tools=function_tools,
            mcp_servers=[],
            handoffs=[],
            output_type=final_output_schema or _final_reply_type(),
        )
        return (
            Runner,
            prepared_agent,
            _native_driver_prompt(context),
            RunConfig(
                tracing_disabled=True,
                tool_execution=ToolExecutionConfig(
                    pre_approval_tool_input_guardrails=True
                ),
            ),
            (MaxTurnsExceeded, ModelBehaviorError),
            (ModelTimeoutError, APIError, TimeoutError),
            contracts,
        )

    def _observe_usage(self, result: object, duration: float) -> AgentInvocationUsage:
        usage = _usage_observation(
            result,
            duration_seconds=duration,
            provider="openai-agents",
            model=self._model_name,
        )
        if self._usage_observer is not None:
            self._usage_observer(usage)
        return usage

    def _convert_native_result(
        self,
        result: Any,
        contracts: Mapping[str, tuple[AgentToolSpec, Any, Any]],
        context: AgentContext,
    ) -> AgentDecision:
        available_contracts = _available_contracts(context, contracts)
        if result.interruptions:
            calls: list[ToolCall] = []
            try:
                for interruption in result.interruptions:
                    name = interruption.name
                    if name not in available_contracts:
                        raise AgentDriverFailure("AGENT_MODEL_OUTPUT_INVALID")
                    arguments = json.loads(interruption.arguments)
                    message = _validation_error(contracts[name][1], arguments)
                    if message is not None or not isinstance(arguments, dict):
                        raise AgentDriverFailure("AGENT_MODEL_OUTPUT_INVALID")
                    raw_item = getattr(interruption, "raw_item", None)
                    call_id = (
                        raw_item.get("call_id")
                        if isinstance(raw_item, Mapping)
                        else getattr(raw_item, "call_id", None)
                    )
                    calls.append(
                        ToolCall(
                            contracts[name][0].tool,
                            arguments,
                            call_id=call_id if isinstance(call_id, str) else None,
                        )
                    )
                decision = AgentDecision("", tuple(calls))
            except AgentDriverFailure:
                raise
            except Exception as error:
                raise AgentDriverFailure("AGENT_MODEL_OUTPUT_INVALID") from error
            return decision

        final_output = result.final_output
        if context.workflow_schema_version == "v6-light-workflow-v4":
            if isinstance(final_output, Mapping):
                return AgentDecision("", output=dict(final_output))
            raise AgentDriverFailure("AGENT_MODEL_OUTPUT_INVALID")
        if isinstance(final_output, _final_reply_type()):
            return AgentDecision(final_output.message)
        raise AgentDriverFailure("AGENT_MODEL_OUTPUT_INVALID")
