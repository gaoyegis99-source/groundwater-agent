"""Confirmation, recovery, and persisted-state resume coordination."""

from __future__ import annotations

from platform_workflow_compiler.composition.execution import TOOL

from ..application.query_dtos import ConfirmedAction
from ..database import begin_immediate, connect_database
from ..errors import (
    AgentRunError,
    WorkflowDefinitionUnavailableError,
)
from .models import (
    AgentFinalV5,
    AgentRun,
    ReplyPresentationV1,
    ReplySectionV1,
)
from .persistence import (
    UsageDelta,
    claim_state,
    confirmation_is_current,
    fail_invalid_state,
    fail_workflow_definition_unavailable,
    finalize_recovered_run,
    finish_run,
    get_run,
    kernel_case_state,
    load_execution_context,
    mark_execution_queued,
)
from .sdk_execution import (
    SDKExecutionError,
    SDKStateError,
    execute_resumed,
)
from .run_dependencies import RunDependencies
from .run_finalization import (
    fail_run_result,
    finalize_case_execution,
    finalize_completed_execution,
    raise_execution_failure,
    record_result,
)


def confirm_run(
    deps: RunDependencies,
    agent_run_id: str,
    project_id: str,
    confirmation_id: str | None = None,
    confirmed_actions: tuple[ConfirmedAction, ...] | None = None,
) -> AgentRun:
    run = get_run(deps.database, agent_run_id, project_id)
    if run.status == "WAITING_EXECUTION":
        return run
    if run.status != "WAITING_CONFIRMATION":
        return run
    if any((action.status == "FAILED" for action in run.actions)):
        failed = next((action for action in run.actions if action.status == "FAILED"))
        fail_run_result(deps, agent_run_id, failed.error or "WORKFLOW_FAILED")
        return get_run(deps.database, agent_run_id, project_id)
    with connect_database(deps.database) as connection:
        begin_immediate(connection)
        if not confirmation_is_current(connection, agent_run_id):
            return get_run(deps.database, agent_run_id, project_id)
        pending_ids = tuple(
            (
                action.tool_call_id
                for action in run.actions
                if action.status == "PENDING"
            )
        )
        if confirmed_actions is None:
            confirmed_actions = tuple(
                (
                    ConfirmedAction(
                        tool_call_id=action.tool_call_id,
                        position=action.position,
                        tool=action.tool,
                        model_id=action.model_id,
                        model_version=action.model_version,
                        model_checksum=action.model_checksum,
                        workflow_id=action.workflow_id,
                        workflow_version=action.workflow_version,
                        workflow_checksum=action.workflow_checksum,
                        input_schema_checksum=action.schema_checksum,
                        arguments=action.arguments,
                        input_references=action.input_references,
                    )
                    for action in run.actions
                    if action.status == "PENDING"
                )
            )
        if pending_ids != tuple((action.tool_call_id for action in confirmed_actions)):
            raise ValueError("CONFIRMATION_ACTION_SET_INVALID")
        deps.agent_projection.require_current_confirmation(connection, confirmation_id)
        try:
            deps.workflow_execution.enqueue_confirmed_batch(
                connection,
                agent_run_id=run.agent_run_id,
                session_id=run.session_id,
                project_id=run.project_id,
                confirmation_id=confirmation_id,
                actions=confirmed_actions,
            )
        except WorkflowDefinitionUnavailableError:
            fail_workflow_definition_unavailable(
                connection, run, deps.agent_projection
            )
            unavailable = True
        else:
            mark_execution_queued(connection, run)
            unavailable = False
    if unavailable:
        raise AgentRunError("WORKFLOW_DEFINITION_UNAVAILABLE", agent_run_id)
    return get_run(deps.database, agent_run_id, project_id)


def reconcile_execution(deps: RunDependencies, agent_run_id: str) -> bool:
    run = get_run(deps.database, agent_run_id)
    if run.status != "WAITING_EXECUTION":
        return False
    return all(
        (
            action.status not in {"PENDING", "QUEUED", "RUNNING"}
            for action in run.actions
        )
    )


def finalize_recovery(deps: RunDependencies, agent_run_id: str) -> AgentRun | None:
    """Finalize a terminal recovery attempt using persisted execution facts."""
    return finalize_recovered_run(deps.database, agent_run_id, deps.agent_projection)


def confirm_recovery(
    deps: RunDependencies,
    agent_run_id: str,
    project_id: str,
    confirmation_id: str,
    confirmed_actions: tuple[ConfirmedAction, ...],
) -> AgentRun:
    run = get_run(deps.database, agent_run_id, project_id)
    with connect_database(deps.database) as connection:
        begin_immediate(connection)
        deps.agent_projection.require_current_confirmation(connection, confirmation_id)
        try:
            deps.workflow_execution.enqueue_confirmed_batch(
                connection,
                agent_run_id=agent_run_id,
                session_id=run.session_id,
                project_id=project_id,
                confirmation_id=confirmation_id,
                actions=confirmed_actions,
            )
        except WorkflowDefinitionUnavailableError:
            raise AgentRunError(
                "WORKFLOW_DEFINITION_UNAVAILABLE", agent_run_id
            ) from None
    return run


async def reject_run(
    deps: RunDependencies,
    agent_run_id: str,
    project_id: str,
    reason: str,
    *,
    tool_call_ids: tuple[str, ...],
) -> AgentRun:
    run = get_run(deps.database, agent_run_id, project_id)
    if run.status != "WAITING_CONFIRMATION":
        return run
    actions = {action.tool_call_id: action for action in run.actions}
    pending = {
        action.tool_call_id for action in run.actions if action.status == "PENDING"
    }
    targeted = set(tool_call_ids)
    if pending != targeted:
        if not targeted or any(
            (actions[item].status != "REJECTED" for item in targeted)
        ):
            raise ValueError("AGENT_REJECTION_ACTIONS_INVALID")
        if pending:
            return run
    if run.actions and all(action.tool == TOOL for action in run.actions):
        stored = claim_state(deps.database, run, expected_status="WAITING_CONFIRMATION", reject_pending=True)
        if stored is None:
            return get_run(deps.database, agent_run_id, project_id)
        return finalize_rejection(deps, get_run(deps.database, agent_run_id, project_id))
    return await resume_run(
        deps,
        agent_run_id,
        project_id,
        expected_status="WAITING_CONFIRMATION",
        approve=False,
        reason=reason,
    )


async def resume_execution(deps: RunDependencies, agent_run_id: str) -> AgentRun:
    run = get_run(deps.database, agent_run_id)
    return await resume_run(
        deps,
        agent_run_id,
        run.project_id,
        expected_status="WAITING_EXECUTION",
        approve=True,
        reason=None,
    )


async def resume_run(
    deps: RunDependencies,
    agent_run_id: str,
    project_id: str,
    *,
    expected_status: str,
    approve: bool,
    reason: str | None,
) -> AgentRun:
    run = get_run(deps.database, agent_run_id, project_id)
    if run.status != expected_status:
        return run
    if expected_status == "WAITING_EXECUTION" and run.actions and all(action.tool == TOOL for action in run.actions):
        return finalize_completed_execution(deps, run)
    case_state = kernel_case_state(deps.database, agent_run_id)
    if expected_status == "WAITING_EXECUTION" and case_state is not None:
        return finalize_case_execution(deps, run)
    if (
        expected_status == "WAITING_CONFIRMATION"
        and not approve
        and case_state is not None
    ):
        stored = claim_state(
            deps.database,
            run,
            expected_status=expected_status,
            reject_pending=True,
        )
        if stored is None:
            return get_run(deps.database, agent_run_id, project_id)
        return finalize_rejection(
            deps, get_run(deps.database, agent_run_id, project_id)
        )
    if (
        expected_status == "WAITING_EXECUTION"
        and deps.settings.agent_provider == "minimax"
        and run.actions
        and all(
            action.status not in {"PENDING", "QUEUED", "RUNNING"}
            for action in run.actions
        )
    ):
        # MiniMax's post-execution acknowledgement is presentation only.  The
        # frozen actions and WorkflowRun records already contain every fact
        # needed for the terminal reply, so a transient Provider failure here
        # must not turn a completed execution into a failed AgentRun.
        return finalize_completed_execution(deps, run)
    try:
        stored = claim_state(
            deps.database,
            run,
            expected_status=expected_status,
            reject_pending=not approve,
        )
        if stored is None:
            return get_run(deps.database, agent_run_id, project_id)
        execution = await execute_resumed(
            deps.sdk_execution,
            session_id=run.session_id,
            project_id=run.project_id,
            agent_run_id=run.agent_run_id,
            goal_revision=run.goal_revision,
            trace_id=run.trace_id,
            state_json=stored.state_json,
            action_statuses={
                action.tool_call_id: action.status for action in run.actions
            },
            approve=approve,
            reason=reason,
            execution_context=load_execution_context(
                deps.database, run.session_id, before_goal_revision=run.goal_revision
            ),
        )
    except SDKStateError as error:
        fail_invalid_state(
            deps.database, get_run(deps.database, agent_run_id), deps.agent_projection
        )
        raise AgentRunError("AGENT_RUN_STATE_INVALID", agent_run_id) from error
    except SDKExecutionError as error:
        raise_execution_failure(deps, agent_run_id, error)
    return record_result(deps, agent_run_id, execution)


def finalize_rejection(
    deps: RunDependencies, run: AgentRun,
) -> AgentRun:
    """Finish a rejected Kernel-owned proposal without restoring an SDK RunState."""
    rejected = [action for action in run.actions if action.status == "REJECTED"]
    if not rejected or len(rejected) != len(run.actions):
        raise ValueError("KERNEL_CASE_REJECTION_INVALID")
    conclusion = f"已拒绝 {len(rejected)} 项分析。"
    output = AgentFinalV5(
        message=conclusion,
        presentation=ReplyPresentationV1(
            conclusion=ReplySectionV1(markdown=conclusion, source_indexes=[]),
            details=[],
            execution_summary=None,
            unresolved_items=[],
            sources=[],
        ),
        workflow_runs=[],
        unresolved_items=[],
        interaction_options=[],
    )
    finish_run(
        deps.database,
        run,
        agent_projection=deps.agent_projection,
        status="COMPLETE",
        output=output,
        usage=UsageDelta(0, 0, 0),
        error=None,
        continuation_stop="COMPOSITION_REJECTED" if rejected[0].tool == TOOL else "KERNEL_CASE_REJECTED",
    )
    return get_run(deps.database, run.agent_run_id)
