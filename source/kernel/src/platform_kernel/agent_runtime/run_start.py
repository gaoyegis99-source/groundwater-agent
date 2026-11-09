"""Initial Agent-run coordination."""

from __future__ import annotations

import json

from ..database import utc_now
from .persistence import load_request_context
from .models import (
    AgentPlanStepV2,
    AgentRun,
)
from .memory import ExecutionContextV1
from .persistence import (
    UsageDelta,
    create_run,
    ensure_dialogue_request,
    get_run,
    load_initial_activity,
    save_waiting_result,
    save_plan,
)
from .sdk_execution import (
    SDKExecutionError,
    execute_initial,
    new_trace_id,
)
from .case_resolver import resolve_execution_case
from .run_dependencies import RunDependencies
from .run_finalization import raise_execution_failure, record_result


async def start_run(
    deps: RunDependencies,
    session_id: str,
    project_id: str,
    goal_revision: int,
    message: str,
    history: tuple[tuple[int, str, str], ...],
    *,
    agent_run_id: str,
    dialogue_policy: str = "READ_ONLY",
    actor_subject: str,
) -> AgentRun:
    ensure_dialogue_request(deps.database, agent_run_id, dialogue_policy)
    request_context = load_request_context(deps.database, agent_run_id)
    dialogue_policy = str(request_context.get("dialogue_policy") or dialogue_policy)
    trace_id = new_trace_id()
    def on_admitted() -> None:
        create_run(
            deps.database,
            agent_run_id=agent_run_id,
            session_id=session_id,
            project_id=project_id,
            goal_revision=goal_revision,
            provider=deps.settings.agent_provider,
            model=deps.settings.agent_model,
            trace_id=trace_id,
            started_at=utc_now(),
        )

    if dialogue_policy == "EXECUTION_PROPOSAL":
        resolved_case = await resolve_execution_case(
            deps.settings,
            deps.sdk_execution.observation_client,
            message=message,
            project_id=project_id,
            agent_run_id=agent_run_id,
            trace_id=trace_id,
            actor_subject=actor_subject,
        )
        if resolved_case is not None:
            on_admitted()
            revision = save_plan(
                deps.database,
                agent_run_id,
                resolved_case.goal,
                [
                    AgentPlanStepV2(
                        step_id="run-case",
                        tool=resolved_case.model.tool_name,
                        purpose="执行固定案例并发布全部命名成果",
                    )
                ],
                resolved_case.reason,
            )
            if revision != resolved_case.action.plan_revision:
                raise ValueError("KERNEL_CASE_PLAN_REVISION_INVALID")
            action_arguments = json.loads(resolved_case.action.arguments_json)
            input_references = json.loads(resolved_case.action.input_references_json)
            preparation = (
                f"preparation:{resolved_case.case.case_id}",
                {
                    "target_kind": "EXECUTABLE_MODEL",
                    "target_id": resolved_case.model.model_id,
                    "target_version": resolved_case.model.model_version,
                    "selection_policy": "PINNED",
                    "status": "READY",
                    "inputs": input_references,
                    "knowledge_refs": [],
                    "parameters": {
                        key: value
                        for key, value in action_arguments.items()
                        if key not in resolved_case.model.data_input_parameters
                    },
                    "parameter_sources": {
                        key: "CASE_PROFILE"
                        if key in resolved_case.case.profiles[resolved_case.profile]
                        else "MODEL_DEFAULT"
                        for key in action_arguments
                        if key not in resolved_case.model.data_input_parameters
                    },
                    "checks": [
                        {
                            "kind": "DATA_IDENTITY",
                            "input": name,
                            "status": "PASS",
                            "version_id": reference["version_id"],
                            "semantic_id": reference["semantic_id"],
                        }
                        for name, reference in input_references.items()
                    ],
                    "blocking_reasons": [],
                },
            )
            run = get_run(deps.database, agent_run_id)
            save_waiting_result(
                deps.database,
                run,
                (resolved_case.action,),
                {
                    "schema_version": "kernel-case-state.v1",
                    "case_id": resolved_case.case.case_id,
                    "profile": resolved_case.profile,
                },
                UsageDelta(0, 0, 0),
                None,
                deps.agent_projection,
                preparation=(preparation[0], (preparation[1],)),
            )
            return get_run(deps.database, agent_run_id)
    try:
        execution = await execute_initial(
            deps.sdk_execution,
            session_id=session_id,
            project_id=project_id,
            agent_run_id=agent_run_id,
            goal_revision=goal_revision,
            trace_id=trace_id,
            history=history,
            message=message,
            memory=None,
            execution_context=ExecutionContextV1(()),
            on_admitted=on_admitted,
            queued_activity=load_initial_activity(
                deps.database, session_id, agent_run_id
            ),
        )
    except SDKExecutionError as error:
        raise_execution_failure(deps, agent_run_id, error)
    return record_result(deps, agent_run_id, execution)
