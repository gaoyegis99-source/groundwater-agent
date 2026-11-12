"""Agent result, interruption, and terminal-state coordination."""

from __future__ import annotations

import json
from typing import Any, NoReturn

from ..errors import (
    AgentRunError,
    UpstreamServiceError,
)
from .case_resolver import load_explicit_model_tool_request
from .persistence import load_request_context
from .models import (
    DEVELOPMENT_CASE_LIMITATION,
    AgentFinalV5,
    AgentRun,
    ReplyPresentationV1,
    ReplySectionV1,
    SubmittedReplyV6,
    normalize_reply_json,
)
from .memory import MemoryUpdate
from .mode import resolve_mode_capabilities
from .persistence import (
    UsageDelta,
    fail_run,
    finish_run,
    get_run,
    execution_reply_facts,
    save_waiting_result,
)
from .run_dependencies import RunDependencies
from .result_processing import (
    ResultProcessingError,
    prepare_interruption,
)
from .reply import render_submitted_reply
from .workflow_draft import prepare_workflow_draft
from .sdk_execution import (
    ExecutionUsage,
    SDKExecutionError,
    SDKExecutionResult,
)


def usage_delta(usage: ExecutionUsage) -> UsageDelta:
    return UsageDelta(usage.input_tokens, usage.output_tokens, usage.total_tokens)


def finalize_completed_execution(
    deps: RunDependencies, run: AgentRun,
) -> AgentRun:
    """Finalize a normal execution using frozen actions and WorkflowRun facts."""
    return _finalize_persisted_execution(
        deps,
        run,
        include_case_disclaimer=False,
        continuation_stop="EXECUTION_RESULT",
    )


def finalize_case_execution(deps: RunDependencies, run: AgentRun) -> AgentRun:
    """Finalize a deterministic synthetic case with its required disclaimer."""
    return _finalize_persisted_execution(
        deps,
        run,
        include_case_disclaimer=True,
        continuation_stop="KERNEL_CASE_FINALIZED",
    )


def _finalize_persisted_execution(
    deps: RunDependencies,
    run: AgentRun,
    *,
    include_case_disclaimer: bool,
    continuation_stop: str,
) -> AgentRun:
    if any(
        action.status in {"PENDING", "QUEUED", "RUNNING"}
        for action in run.actions
    ):
        return run
    if not run.actions:
        raise ValueError("EXECUTION_ACTION_REQUIRED")

    plans_by_revision = {plan.revision: plan for plan in run.plans}
    plan_revisions = list(dict.fromkeys(
        action.plan_revision for action in run.actions
    ))
    if any(revision not in plans_by_revision for revision in plan_revisions):
        raise ValueError("EXECUTION_PLAN_REQUIRED")
    plans = [plans_by_revision[revision] for revision in plan_revisions]
    workflow_ids = list(dict.fromkeys(
        action.workflow_run_id
        for action in run.actions
        if action.workflow_run_id is not None
    ))
    outputs = [
        output for action in run.actions for output in action.remote_outputs
    ]
    input_identities = {
        (
            reference.service,
            reference.dataset_id,
            reference.version_id,
            reference.semantic_id,
            reference.object_id,
            reference.checksum,
        )
        for action in run.actions
        for reference in action.input_references.values()
    }
    succeeded = all(action.status == "COMPLETE" for action in run.actions)
    goals = "；".join(f"“{plan.goal}”" for plan in plans)
    case_disclaimer = ReplySectionV1(
        markdown=DEVELOPMENT_CASE_LIMITATION,
        source_indexes=[],
    )

    if succeeded:
        conclusion = (
            f"已完成“{plans[0].goal}”，发布了 {len(outputs)} 项可核验成果。"
            if len(plans) == 1
            else f"已完成 {len(plans)} 项执行计划，发布了 {len(outputs)} 项可核验成果。"
        )
        names = list(dict.fromkeys(
            output.output_name for output in outputs if output.output_name
        ))
        highlighted = "、".join(names[:6])
        output_text = "主要成果包括：" + (
            highlighted + ("等。" if len(names) > 6 else "。")
            if highlighted
            else "已发布结果。"
        )
        if len(plans) > 1:
            output_text = f"执行目标：{goals}。{output_text}"
        details = [
            ReplySectionV1(
                markdown=f"已使用 {len(input_identities)} 项冻结输入，参数采用本次确认时冻结的快照；执行前后均保持相同数据身份。",
                source_indexes=[],
            ),
            ReplySectionV1(markdown=output_text, source_indexes=[]),
        ]
        error = None
        status = "COMPLETE"
    else:
        codes = list(dict.fromkeys(
            action.error or "WORKFLOW_FAILED"
            for action in run.actions
            if action.status == "FAILED"
        ))
        if not codes:
            codes = ["WORKFLOW_FAILED"]
        conclusion = (
            f"“{plans[0].goal}”执行未完成。"
            if len(plans) == 1
            else f"{len(plans)} 项执行计划未全部完成。"
        )
        details = [ReplySectionV1(
            markdown="最新执行错误：" + "、".join(codes), source_indexes=[]
        )]
        if len(plans) > 1:
            details.append(ReplySectionV1(
                markdown=f"执行目标：{goals}。", source_indexes=[]
            ))
        error = codes[0]
        status = "FAILED"
    if include_case_disclaimer:
        details.append(case_disclaimer)

    execution_summary = ReplySectionV1(
        markdown=f"执行状态：{('成功' if succeeded else '失败')}；WorkflowRun {len(workflow_ids)} 项；成果 {len(outputs)} 项。",
        source_indexes=[],
    )
    output = AgentFinalV5(
        message="\n\n".join([
            conclusion,
            *(item.markdown for item in details),
            execution_summary.markdown,
        ]),
        presentation=ReplyPresentationV1(
            conclusion=ReplySectionV1(
                markdown=conclusion, source_indexes=[]
            ),
            details=details,
            execution_summary=execution_summary,
            unresolved_items=[],
            sources=[],
        ),
        workflow_runs=workflow_ids,
        unresolved_items=[],
        interaction_options=[],
    )
    finish_run(
        deps.database,
        run,
        agent_projection=deps.agent_projection,
        status=status,
        output=output,
        usage=UsageDelta(0, 0, 0),
        error=error,
        continuation_stop=continuation_stop,
    )
    return get_run(deps.database, run.agent_run_id)


def raise_execution_failure(
    deps: RunDependencies, agent_run_id: str, error: SDKExecutionError
) -> NoReturn:
    if get_run(deps.database, agent_run_id).status == "CANCELLED":
        raise AgentRunError("AGENT_STOPPED", agent_run_id) from error
    fail_with_usage(
        deps,
        get_run(deps.database, agent_run_id),
        error.code,
        None if error.usage is None else usage_delta(error.usage),
        error.memory_update,
        failed_activity_id=error.failed_activity_id,
        diagnostics=error.diagnostics,
    )
    if error.upstream:
        raise UpstreamServiceError(
            error.code, agent_run_id, diagnostics=error.diagnostics
        ) from error
    raise AgentRunError(
        error.code, agent_run_id, diagnostics=error.diagnostics
    ) from error


def record_result(deps: RunDependencies, agent_run_id: str, execution: SDKExecutionResult) -> AgentRun:
    run = get_run(deps.database, agent_run_id)
    if run.status == "CANCELLED":
        return run
    result = execution.result
    usage = usage_delta(execution.usage)
    if result.interruptions:
        failed = next(
            (action for action in run.actions if action.status == "FAILED"), None
        )
        if failed is not None:
            fail_with_usage(
                deps, run, failed.error or "WORKFLOW_FAILED", usage, execution.memory_update
            )
            return get_run(deps.database, run.agent_run_id)
        return record_interruption(deps, run, result, usage, execution.memory_update)
    return record_final(deps, run, result, usage, execution.memory_update)


def record_interruption(
    deps: RunDependencies,
    run: AgentRun,
    result: Any,
    usage: UsageDelta,
    memory_update: MemoryUpdate | None,
) -> AgentRun:
    try:
        context = load_request_context(deps.database, run.agent_run_id)
        capabilities = resolve_mode_capabilities(context, deps.settings.models)
        if capabilities.read_only:
            raise ResultProcessingError("AGENT_READ_ONLY_EXECUTION_FORBIDDEN")
        prepared = prepare_interruption(
            run,
            result,
            deps.models_by_tool,
            provider=deps.settings.agent_provider,
            explicit_model_tool_request=load_explicit_model_tool_request(
                deps.database, deps.settings, run.agent_run_id,
            ),
        )
    except (ResultProcessingError, AgentRunError) as error:
        diagnostics = error.diagnostics if isinstance(error, AgentRunError) else None
        fail_with_usage(
            deps, run, str(error), usage, memory_update, diagnostics=diagnostics,
        )
        raise AgentRunError(
            str(error), run.agent_run_id, diagnostics=diagnostics,
        ) from error
    preparation = (f"proposal-boundary:{run.agent_run_id}", prepared.preparations)
    save_waiting_result(
        deps.database,
        run,
        prepared.actions,
        prepared.state_payload,
        usage,
        memory_update,
        deps.agent_projection,
        task_updates=result.context_wrapper.context.get("task_updates", []),
        preparation=preparation,
    )
    return get_run(deps.database, run.agent_run_id)


def record_final(
    deps: RunDependencies,
    run: AgentRun,
    result: Any,
    usage: UsageDelta,
    memory_update: MemoryUpdate | None,
) -> AgentRun:
    workflow_preparation = None
    try:
        context = load_request_context(deps.database, run.agent_run_id)
        final_output = result.final_output
        facts = execution_reply_facts(deps.database, run.agent_run_id)
        if (
            deps.settings.agent_provider in {"minimax", "deepseek"}
            and context.get("dialogue_policy") == "EXECUTION_PROPOSAL"
            and facts["has_action"]
        ):
            # A provider acknowledgement is not an execution fact.  Re-project
            # the terminal reply at the persistence boundary as well as at the
            # tool boundary because some chat-completions adapters retain the
            # raw tool arguments as ``final_output``.  This makes the recorded
            # answer depend only on the frozen action and WorkflowRun records.
            final_output = json.dumps({
                "conclusion": None,
                "details": [],
                "unresolved_items": [],
                "referenced_workflow_runs": facts["workflow_run_ids"],
                "include_execution_status": True,
                "interaction_options": [],
                "workflow_draft": None,
            }, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        output = render_submitted_reply(
            deps.database,
            run.agent_run_id,
            result,
            final_output=final_output,
        )
        submitted = SubmittedReplyV6.model_validate_json(
            normalize_reply_json(final_output)
        )
        draft_input = submitted.workflow_draft
        workflow_draft = (
            prepare_workflow_draft(
                draft_input,
                result,
                deps.settings,
                context["workflow_draft_id"],
            )
            if draft_input is not None
            else None
        )
        if workflow_draft is not None and draft_input is not None:
            blockers = [
                f"WORKFLOW_DRAFT_MISSING_{item['kind']}:{item['name']}"
                for item in workflow_draft["missing_inputs"]
            ]
            if draft_input.unresolved_items:
                blockers.append("WORKFLOW_DRAFT_UNRESOLVED_ITEMS")
            blocking_issues = [
                {
                    "code": f"WORKFLOW_DRAFT_MISSING_{item['kind']}",
                    "kind": "MISSING_DATA"
                    if item["kind"] == "DATA"
                    else "MISSING_PARAMETER",
                    **(
                        {"input": item["name"]}
                        if item["kind"] == "DATA"
                        else {"parameter": item["name"]}
                    ),
                }
                for item in workflow_draft["missing_inputs"]
            ]
            if draft_input.unresolved_items:
                blocking_issues.append(
                    {
                        "code": "WORKFLOW_DRAFT_UNRESOLVED_ITEMS",
                        "kind": "UNRESOLVED_ITEM",
                    }
                )
            workflow_preparation = (
                f"workflow-draft:{context['workflow_draft_id']}",
                (
                    {
                        "target_kind": "WORKFLOW_DRAFT",
                        "target_id": workflow_draft["workflow"]["workflowId"],
                        "target_version": workflow_draft["workflow"]["version"],
                        "selection_policy": "PINNED",
                        "status": "BLOCKED" if blockers else "READY",
                        "inputs": {
                            item.input_name: item.data_ref.model_dump(exclude_none=True)
                            for item in draft_input.data_bindings
                        },
                        "knowledge_refs": [
                            item.model_dump()
                            for item in draft_input.knowledge_refs
                        ],
                        "parameters": draft_input.parameter_values,
                        "parameter_sources": {
                            item.name: item.source
                            for item in draft_input.parameter_sources
                        },
                        "checks": [
                            {
                                "kind": "WORKFLOW_COMPILE",
                                "status": "PASS",
                                "operator_catalog_digest": deps.settings.operator_catalog_digest,
                            },
                            {
                                "kind": "REQUIRED_INPUTS",
                                "status": "PASS"
                                if not workflow_draft["missing_inputs"]
                                else "FAIL",
                                "count": len(workflow_draft["missing_inputs"]),
                            },
                            {
                                "kind": "UNRESOLVED_ITEMS",
                                "status": "PASS" if not blockers else "FAIL",
                                "count": len(draft_input.unresolved_items),
                            },
                        ],
                        "blocking_reasons": blockers,
                        "blocking_issues": blocking_issues,
                    },
                ),
            )
    except AgentRunError as caught:
        fail_with_usage(
            deps,
            run,
            "AGENT_MODEL_OUTPUT_INVALID",
            usage,
            memory_update,
            diagnostics=caught.diagnostics,
        )
        raise AgentRunError(
            "AGENT_MODEL_OUTPUT_INVALID",
            run.agent_run_id,
            diagnostics=caught.diagnostics,
        ) from caught
    failed = next((action for action in run.actions if action.status == "FAILED"), None)
    status = "FAILED" if failed is not None else "COMPLETE"
    result_error = None if failed is None else failed.error
    finish_run(
        deps.database,
        run,
        agent_projection=deps.agent_projection,
        status=status,
        output=output,
        usage=usage,
        error=result_error,
        memory_update=memory_update,
        workflow_draft=workflow_draft,
        preparation=workflow_preparation,
        task_updates=result.context_wrapper.context.get("task_updates", [])
        if status == "COMPLETE"
        else [],
        continuation_stop=result.context_wrapper.context.get("governance", {}).get(
            "finalization_reason"
        ),
    )
    return get_run(deps.database, run.agent_run_id)


def fail_with_usage(
    deps: RunDependencies,
    run: AgentRun,
    code: str,
    usage: UsageDelta | None,
    memory_update: MemoryUpdate | None = None,
    *,
    failed_activity_id: str | None = None,
    diagnostics: dict | None = None,
) -> None:
    finish_run(
        deps.database,
        run,
        agent_projection=deps.agent_projection,
        status="FAILED",
        output=None,
        usage=usage or UsageDelta(0, 0, 0),
        error=code,
        memory_update=memory_update,
        failed_activity_id=failed_activity_id,
        diagnostics=diagnostics,
    )


def fail_run_result(deps: RunDependencies, agent_run_id: str, code: str) -> None:
    run = get_run(deps.database, agent_run_id)
    fail_run(deps.database, run, code, deps.agent_projection)
