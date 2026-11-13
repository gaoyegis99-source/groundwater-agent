"""Persisted Agent-state resume orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from functools import partial
import json
from uuid import uuid4

from agents import (
    RunState,
    UserError,
    custom_span,
    trace,
)
from .usage_ports import ScopedActivityRecorder

from ...application.task_api import context_for_run
from ..case_resolver import load_explicit_model_tool_request
from ..context_policy import (
    ContextPolicyV1,
    ContextSourceSnapshotV1,
)
from ..governance import (
    resume_governance,
    validate_governance,
)
from ..memory import (
    ExecutionContextV1,
    O200kTokenEstimator,
)
from ..persistence import load_request_context
from .activity import (
    _RunUsageCapture,
    _ZERO_USAGE,
    _activity,
    _activity_status,
    _usage_data,
)
from .context import _available_input_tokens
from .types import (
    SDKExecutionDependencies,
    SDKExecutionError,
    SDKExecutionResult,
    SDKStateError,
)

from .execution_builders import (
    _build_agent,
    _build_run_config,
    _build_run_context,
    _capabilities,
)
from .segment_runner import _execute_agent


async def execute_resumed(
    dependencies: SDKExecutionDependencies,
    *,
    session_id: str,
    project_id: str,
    agent_run_id: str,
    goal_revision: int,
    trace_id: str,
    state_json: str,
    action_statuses: dict[str, str],
    approve: bool,
    reason: str | None,
    execution_context: ExecutionContextV1,
) -> SDKExecutionResult:
    run_config = _build_run_config(
        dependencies.settings,
        session_id=session_id,
        project_id=project_id,
        agent_run_id=agent_run_id,
        trace_id=trace_id,
        goal_revision=goal_revision,
    )
    segment = _activity({"agent_run_id": agent_run_id, "goal_revision": goal_revision,
                         "segment_id": str(uuid4())}, "SEGMENT",
                        "RESUME_EXECUTION" if approve else "RESUME_REJECTION", status="QUEUED")
    activity_recorder = ScopedActivityRecorder(dependencies.activity_log, session_id)
    activity_recorder.record(segment)
    hooks = _RunUsageCapture(
        _available_input_tokens(dependencies), segment=segment,
        activity_recorder=activity_recorder,
        observation_names=frozenset(tool.name for tool in dependencies.settings.observation_tools),
        observation_tools=dependencies.settings.observation_tools,
        purpose="PROCESSING",
        request_agent_run_id=agent_run_id,
        usage_ledger=dependencies.usage_ledger,
        context_purpose="AGENT_RESUME",
        provider=dependencies.settings.agent_provider,
        model=dependencies.settings.agent_model,
        protocol=dependencies.settings.agent_protocol,
    )
    async with dependencies.agent_slots:
        _activity_status(activity_recorder.record, segment, "RUNNING")
        deadline_at = (
            asyncio.get_running_loop().time()
            + dependencies.settings.agent_segment_timeout_seconds
        )
        diagnostics = {"phase": "resume", "status": "FAILED", "error_code": None}
        usage = _ZERO_USAGE
        with trace(
            run_config.workflow_name,
            trace_id=trace_id,
            group_id=session_id,
            metadata=run_config.trace_metadata,
        ), custom_span("agent.segment", data=diagnostics):
            try:
                full_task_context = context_for_run(dependencies.database, agent_run_id)
                # RunState and the persisted AgentAction already freeze the approved
                # proposal. Repeating its preparation snapshot here would duplicate
                # exact references and parameters in the resume request.
                full_preparation_context = None
                request_context = {
                    **load_request_context(dependencies.database, agent_run_id),
                    "task_context": full_task_context,
                    "preparation_context": full_preparation_context,
                    "explicit_model_tool_request": load_explicit_model_tool_request(
                        dependencies.database, dependencies.settings, agent_run_id,
                    ),
                }
                capabilities = _capabilities(dependencies, request_context)
                policy = ContextPolicyV1(O200kTokenEstimator())
                resume_snapshot = ContextSourceSnapshotV1(
                        session_id=session_id,
                        goal_revision=goal_revision,
                        message="",
                        turns=(),
                        task_context=full_task_context,
                        preparation_context=full_preparation_context,
                        execution_actions=tuple(
                            item.as_dict() for item in execution_context.actions
                        ),
                        mode=capabilities.mode,
                        source_watermark=goal_revision,
                    )
                projection = policy.agent_projection(resume_snapshot)
                request_context["task_context"] = projection.task_context
                request_context["preparation_context"] = projection.preparation_context
                effective_execution_context = projection.execution_context
                hooks.set_initial_context_manifest(policy.resume_view(
                    available_input_tokens=_available_input_tokens(dependencies),
                    projection_blocks=projection.blocks,
                ).manifest())
                agent = _build_agent(
                    dependencies, effective_execution_context,
                    request_context,
                )
                # This segment reports a frozen action result, not a new turn of
                # discovery. Make that boundary explicit to the Provider as well
                # as enforcing it through tool availability and reply validation.
                agent.instructions += (
                    "\nThis is the finalization of the confirmed or rejected action. "
                    "Its terminal result is supplied by Kernel. Call submit_reply alone "
                    "with conclusion=null and include_execution_status=true. "
                    "Do not inspect inputs or outputs or propose another execution."
                )
                run_config.model_settings = replace(
                    dependencies.model_settings,
                    tool_choice="submit_reply",
                    parallel_tool_calls=False,
                )
                restore_timeout = asyncio.timeout_at(deadline_at)
                try:
                    async with restore_timeout:
                        payload = json.loads(state_json)
                        responses = (
                            payload.get("model_responses")
                            if isinstance(payload, dict)
                            else None
                        )
                        if not isinstance(responses, list):
                            raise ValueError("AGENT_RUN_STATE_INVALID")
                        persisted_context = payload.get("context")
                        persisted_context = (
                            persisted_context.get("context")
                            if isinstance(persisted_context, dict)
                            else None
                        )
                        if not isinstance(persisted_context, dict):
                            raise ValueError("AGENT_RUN_STATE_INVALID")
                        governance_state = validate_governance(
                            persisted_context.get("governance"), capabilities
                        )
                        governance_state = resume_governance(governance_state)
                        remaining_requests = (
                            governance_state["request_limit"]
                            - governance_state["request_count"]
                        )
                        state = await RunState.from_json(
                            agent,
                            payload,
                            context_override=_build_run_context(
                                project_id=project_id,
                                agent_run_id=agent_run_id,
                                goal_revision=goal_revision,
                                trace_id=trace_id,
                                governance_state=governance_state,
                            ),
                            strict_context=True,
                        )
                        interruptions = state.get_interruptions()
                        if approve and any(
                            action_statuses.get(item.call_id) not in {"COMPLETE", "FAILED"}
                            for item in interruptions
                        ):
                            raise ValueError("AGENT_ACTION_REPLAY_INVALID")
                        for interruption in interruptions:
                            if approve:
                                state.approve(interruption, always_approve=False)
                            else:
                                state.reject(
                                    interruption,
                                    always_reject=False,
                                    rejection_message=reason,
                                )
                except TimeoutError as error:
                    if not restore_timeout.expired():
                        raise
                    raise SDKExecutionError(
                        "AGENT_DEADLINE_EXCEEDED", upstream=False, usage=_ZERO_USAGE
                    ) from error
                except (KeyError, TypeError, ValueError, UserError) as error:
                    raise SDKStateError("AGENT_RUN_STATE_INVALID") from error
                execution = await _execute_agent(
                    agent,
                    state,
                    run_config,
                    response_start=len(responses),
                    previous_usage=_ZERO_USAGE,
                    memory_update=None,
                    usage_capture=hooks,
                    deadline_at=deadline_at,
                    preview=None if dependencies.publish_preview is None else partial(dependencies.publish_preview, session_id, agent_run_id),
                    phase="resume",
                    max_turns=max(1, remaining_requests * 2),
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
            except SDKStateError:
                diagnostics["error_code"] = "AGENT_RUN_STATE_INVALID"
                raise
            finally:
                diagnostics["usage"] = _usage_data(usage)
