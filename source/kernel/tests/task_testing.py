"""Measure protocol overhead so narrow-window tests retain comparable headroom."""
from copy import copy, deepcopy
from dataclasses import replace

from platform_kernel.agent_runtime.context_usage import context_tokens
from platform_kernel.agent_runtime.memory import ExecutionContextV1
from platform_kernel.agent_runtime.prompts import _PROGRESS_INTENT_INSTRUCTIONS
from platform_kernel.agent_runtime.sdk_execution.execution import _build_agent


def task_overhead(application):
    agent = _build_agent(application.agent_runtime.sdk_execution, ExecutionContextV1(()))
    old = replace(agent, tools=[tool for tool in agent.tools if tool.name != 'update_task'],
                  instructions=agent.instructions.split('\nFor an explicit deliverable, use update_task')[0])
    return context_tokens(agent, agent.instructions, []) - context_tokens(old, old.instructions, [])


def progress_protocol_overhead(application):
    """Return only the provider-schema cost of declared progress intents.

    Constrained-context tests model historical headroom rather than a production
    window. Keep that headroom stable when a required protocol field is added;
    the protocol still remains fully present in every model request.
    """
    agent = _build_agent(
        application.agent_runtime.sdk_execution, ExecutionContextV1(())
    )
    baseline_tools = []
    for tool in agent.tools:
        cloned = copy(tool)
        schema = deepcopy(tool.params_json_schema)
        properties = schema.get("properties", {})
        if "progress_intent" in properties:
            properties.pop("progress_intent")
            schema["required"] = [
                name for name in schema.get("required", [])
                if name != "progress_intent"
            ]
        cloned.params_json_schema = schema
        baseline_tools.append(cloned)
    baseline = replace(
        agent,
        tools=baseline_tools,
        instructions=agent.instructions.replace(_PROGRESS_INTENT_INSTRUCTIONS, ""),
    )
    measured = max(
        0,
        context_tokens(agent, agent.instructions, [])
        - context_tokens(baseline, baseline.instructions, []),
    )
    # Tokenization at the tools/input JSON boundary is not additive. Preserve a
    # small fixed envelope so test behavior cannot flip on a merged token.
    return measured + 16


def preserve_context_headroom(application):
    extra = task_overhead(application) + progress_protocol_overhead(application)
    settings = replace(application.settings, agent_context_window_tokens=application.settings.agent_context_window_tokens + extra)
    application.settings = settings
    application.agent_runtime.settings = settings
    application.agent_runtime.sdk_execution = replace(application.agent_runtime.sdk_execution, settings=settings)
