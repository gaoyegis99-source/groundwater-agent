"""Initial Agent compute-segment orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial
from typing import Any
from uuid import uuid4

from agents import (
    custom_span,
    trace,
)

from ..context_policy import (
    ContextPolicyV1,
    ContextSourceSnapshotV1,
    ContextTurnV1,
)
from ..case_resolver import (
    parse_comparison_terms,
    parse_explicit_model_tool_request,
    parse_required_response_terms,
    requires_project_evidence,
)
from ..governance import (
    new_governance,
    run_limits,
)
from ..memory import (
    ExecutionContextV1,
    MemoryRecord,
    O200kTokenEstimator,
    completed_turns,
)
from ..persistence import load_initial_context_sources
from .activity import (
    _RunUsageCapture,
    _ZERO_USAGE,
    _activity,
    _activity_status,
    _usage_data,
)
from .context import _available_input_tokens, _prepare_initial_input
from .types import (
    SDKExecutionDependencies,
    SDKExecutionError,
    SDKExecutionResult,
)
from .usage_ports import ScopedActivityRecorder

from .execution_builders import (
    _build_agent,
    _build_run_config,
    _build_run_context,
    _capabilities,
)
from .segment_runner import _execute_agent


async def execute_initial(
    dependencies: SDKExecutionDependencies,
    *,
    session_id: str,
    project_id: str,
    agent_run_id: str,
    goal_revision: int,
    trace_id: str,
    history: tuple[tuple[int, str, str], ...],
    message: str,
    memory: MemoryRecord | None,
    execution_context: ExecutionContextV1,
    on_admitted: Callable[[], None],
    queued_activity: dict[str, Any] | None = None,
) -> SDKExecutionResult:
    run_config = _build_run_config(
        dependencies.settings,
        session_id=session_id,
        project_id=project_id,
        agent_run_id=agent_run_id,
        trace_id=trace_id,
        goal_revision=goal_revision,
    )
    activity_recorder = ScopedActivityRecorder(dependencies.activity_log, session_id)
    segment = queued_activity
    if segment is None:
        segment = _activity({"agent_run_id": agent_run_id, "goal_revision": goal_revision,
                             "segment_id": str(uuid4())}, "SEGMENT", "INITIAL", status="QUEUED")
        activity_recorder.record(segment)
    hooks = _RunUsageCapture(
        _available_input_tokens(dependencies), segment=segment,
        activity_recorder=activity_recorder,
        observation_names=frozenset(tool.name for tool in dependencies.settings.observation_tools),
        observation_tools=dependencies.settings.observation_tools,
        purpose="PROCESSING",
        request_agent_run_id=agent_run_id,
        usage_ledger=dependencies.usage_ledger,
        context_purpose="AGENT_INITIAL",
        provider=dependencies.settings.agent_provider,
        model=dependencies.settings.agent_model,
        protocol=dependencies.settings.agent_protocol,
    )
    async with dependencies.agent_slots:
        # A durable Session start stays pending until this single compute gate
        # admits it. Restart can then resume an unstarted request with its ID.
        on_admitted()
        _activity_status(activity_recorder.record, segment, "RUNNING")
        deadline_at = (
            asyncio.get_running_loop().time()
            + dependencies.settings.agent_segment_timeout_seconds
        )
        diagnostics = {"phase": "initial", "status": "FAILED", "error_code": None}
        usage = _ZERO_USAGE
        with trace(
            run_config.workflow_name,
            trace_id=trace_id,
            group_id=session_id,
            metadata=run_config.trace_metadata,
        ), custom_span("agent.segment", data=diagnostics):
            try:
                sources = load_initial_context_sources(
                    dependencies.database,
                    agent_run_id=agent_run_id,
                    session_id=session_id,
                    before_goal_revision=goal_revision,
                )
                if sources.durable_request:
                    history = sources.history
                    memory = sources.memory
                    execution_context = sources.execution_context
                else:
                    memory = memory or sources.memory
                    if not execution_context.actions:
                        execution_context = sources.execution_context
                full_task_context = sources.task_context
                full_preparation_context = sources.preparation_context
                request_context = {
                    **sources.request_context,
                    "task_context": full_task_context,
                    "preparation_context": full_preparation_context,
                    "current_message": message,
                }
                comparison_terms = parse_comparison_terms(message)
                if comparison_terms is not None:
                    request_context["comparison_terms"] = list(comparison_terms)
                required_response_terms = parse_required_response_terms(message)
                if required_response_terms:
                    request_context["required_response_terms"] = list(
                        required_response_terms
                    )
                if requires_project_evidence(message):
                    request_context["requires_project_evidence"] = True
                explicit_request = parse_explicit_model_tool_request(
                    dependencies.settings, message,
                )
                if explicit_request is not None:
                    request_context["explicit_model_tool_request"] = {
                        "tool": explicit_request.tool,
                        "arguments": explicit_request.arguments,
                    }
                capabilities = _capabilities(dependencies, request_context)
                context_policy = ContextPolicyV1(O200kTokenEstimator())
                all_turns = completed_turns(history, after_revision=0)
                snapshot = ContextSourceSnapshotV1(
                    session_id=session_id,
                    goal_revision=goal_revision,
                    message=message,
                    turns=tuple(
                        ContextTurnV1(item.goal_revision, item.user, item.assistant)
                        for item in all_turns
                    ),
                    memory_summary=(
                        None if memory is None else memory.summary.model_dump()
                    ),
                    memory_through_goal_revision=(
                        0 if memory is None else memory.through_goal_revision
                    ),
                    task_context=full_task_context,
                    preparation_context=full_preparation_context,
                    execution_actions=tuple(
                        item.as_dict() for item in execution_context.actions
                    ),
                    mode=capabilities.mode,
                    source_watermark=goal_revision,
                )
                projection = context_policy.agent_projection(snapshot)
                request_context["task_context"] = projection.task_context
                request_context["preparation_context"] = projection.preparation_context
                effective_execution_context = projection.execution_context
                agent = _build_agent(
                    dependencies, effective_execution_context,
                    request_context,
                )
                input_items, compact_usage, memory_update, context_view = await _prepare_initial_input(
                    dependencies,
                    agent,
                    run_config,
                    history=history,
                    message=message,
                    memory=memory,
                    deadline_at=deadline_at,
                    activity_hooks=hooks,
                    snapshot=snapshot,
                    projection=projection,
                    context_policy=context_policy,
                )
                hooks.set_initial_context_manifest(context_view.manifest())
                execution = await _execute_agent(
                    agent,
                    input_items,
                    run_config,
                    response_start=0,
                    context=_build_run_context(
                        project_id=project_id,
                        agent_run_id=agent_run_id,
                        goal_revision=goal_revision,
                        trace_id=trace_id,
                        governance_state=new_governance(capabilities),
                    ),
                    previous_usage=compact_usage,
                    memory_update=memory_update,
                    usage_capture=hooks,
                    deadline_at=deadline_at,
                    preview=None if dependencies.publish_preview is None else partial(dependencies.publish_preview, session_id, agent_run_id),
                    phase="initial",
                    max_turns=run_limits(capabilities).request_limit * 2,
                    request_timeout=(
                        dependencies.settings.agent_request_timeout_seconds
                    ),
                )
                usage = execution.usage
                diagnostics["status"] = "SUCCEEDED"
                return execution
            except SDKExecutionError as error:
                usage = error.usage or _ZERO_USAGE
                diagnostics["error_code"] = error.code
                raise
            finally:
                diagnostics["usage"] = _usage_data(usage)
