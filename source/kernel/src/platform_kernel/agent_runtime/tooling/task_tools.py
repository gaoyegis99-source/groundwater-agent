"""Plan staging, task metadata and authoritative execution-fact tools."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents import FunctionTool, RunContextWrapper, function_tool
from agents.tool_context import ToolContext
from pydantic import BaseModel, ConfigDict, StrictInt
from pydantic import ValidationError as PydanticValidationError

from ...application.task_api import TaskUpdate, stage_update
from ...errors import AgentRunError
from ...settings import ExecutableModelDefinition
from ..models import AgentPlanStepV2, Utf8String
from platform_protocol_contracts import AgentProgressIntent
from ...tool_progress import (
    builtin_progress_intent_schema,
    builtin_tool_outcome,
    validate_builtin_progress_intent,
)
from ..persistence import (
    get_execution_fact as read_execution_fact,
)
from ..persistence import (
    list_execution_facts as read_execution_facts,
)
from ..persistence import (
    save_plan,
)
from ..protocol_repair import stage_protocol_retry
from ..provider_arguments import normalize_model_tool_arguments
from .replies import _PlanStepInput


class _PlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    goal: Utf8String
    steps: list[_PlanStepInput]
    reason: Utf8String


def build_plan_tool(
    database: Path, all_tool_names: set[str], *, provider: str = "deepseek",
) -> FunctionTool:
    def persist(context, value: _PlanInput) -> dict[str, object]:
        steps = value.steps
        if len({step.step_id for step in steps}) != len(steps) or any(
            step.tool not in all_tool_names for step in steps
        ):
            raise AgentRunError("AGENT_PLAN_INVALID")
        revision = save_plan(
            database,
            context.context["agent_run_id"],
            value.goal,
            [AgentPlanStepV2.model_construct(
                step_id=step.step_id, tool=step.tool, purpose=step.purpose,
            ) for step in steps],
            value.reason,
        )
        return {"revision": revision, "status": "RECORDED"}

    @function_tool(failure_error_function=None)
    def record_plan(
        context: RunContextWrapper[dict[str, Any]],
        goal: Utf8String,
        steps: list[_PlanStepInput],
        reason: Utf8String,
    ) -> dict[str, object]:
        """Persist AgentPlanV2 before proposing an executable model call."""
        return persist(
            context,
            _PlanInput(goal=goal, steps=steps, reason=reason),
        )

    if provider not in {"minimax", "deepseek"}:
        return record_plan

    # DeepSeek receives the decorator-generated strict wire schema, but invocation
    # stays Kernel-owned so malformed arguments can use the same bounded repair
    # protocol as semantic plan errors.  MiniMax keeps its permissive schema and
    # alias normalization.
    schema = (
        record_plan.params_json_schema
        if provider == "deepseek"
        else _PlanInput.model_json_schema()
    )

    async def invoke(context: ToolContext, arguments_json: str):
        try:
            supplied = json.loads(arguments_json)
            if provider == "minimax" and isinstance(supplied, dict):
                supplied = normalize_model_tool_arguments(
                    supplied, schema, provider=provider,
                )
            value = _PlanInput.model_validate(supplied)
        except (json.JSONDecodeError, PydanticValidationError) as error:
            issues = (
                [] if isinstance(error, json.JSONDecodeError) else [
                    {
                        "path": "/" + "/".join(
                            str(part) for part in issue["loc"]
                        ),
                        "type": issue["type"],
                    }
                    for issue in error.errors(include_url=False)
                ]
            )
            if stage_protocol_retry(database, context):
                return {
                    "status": "RETRY_REQUIRED",
                    "reason": "PLAN_SCHEMA_INVALID",
                    "issues": issues,
                    "allowed_tools": sorted(all_tool_names),
                    "instruction": (
                        "Call record_plan once more with values matching its exact "
                        "schema and exact tool names listed in allowed_tools."
                    ),
                }
            raise AgentRunError(
                "AGENT_PLAN_INVALID",
                diagnostics={
                    "reason": "PLAN_SCHEMA_INVALID",
                    "issues": issues,
                },
            ) from error
        try:
            return persist(context, value)
        except AgentRunError:
            if stage_protocol_retry(database, context):
                return {
                    "status": "RETRY_REQUIRED",
                    "reason": "PLAN_VALUE_INVALID",
                    "issues": [],
                    "allowed_tools": sorted(all_tool_names),
                    "instruction": (
                        "Call record_plan once more with unique step_id values and "
                        "exact tool names listed in allowed_tools."
                    ),
                }
            raise

    return FunctionTool(
        name="record_plan",
        description=record_plan.description,
        params_json_schema=schema,
        on_invoke_tool=invoke,
        _use_default_failure_error_function=False,
        strict_json_schema=provider == "deepseek",
    )


def build_task_tools(
    database: Path,
    models_for_tasks: tuple[ExecutableModelDefinition, ...],
    *,
    provider: str = "deepseek",
    include_execution_facts: bool = True,
) -> list[FunctionTool]:
    async def update_task(context: ToolContext, arguments_json: str):
        if provider == "minimax":
            try:
                supplied = json.loads(arguments_json)
            except json.JSONDecodeError:
                supplied = None
            if isinstance(supplied, dict):
                arguments_json = json.dumps(
                    normalize_model_tool_arguments(
                        supplied,
                        TaskUpdate.model_json_schema(),
                        provider=provider,
                    ),
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                )
        try:
            update = TaskUpdate.model_validate_json(arguments_json)
        except PydanticValidationError as error:
            issues = [
                {
                    "path": "/" + "/".join(
                        str(part) for part in issue["loc"]
                    ),
                    "type": issue["type"],
                }
                for issue in error.errors(include_url=False)
            ]
            if (
                provider in {"minimax", "deepseek"}
                and stage_protocol_retry(database, context)
            ):
                return {
                    "status": "RETRY_REQUIRED",
                    "reason": "TASK_UPDATE_SCHEMA_INVALID",
                    "issues": issues,
                    "instruction": (
                        "Call update_task exactly once more with values matching its "
                        "exact schema. For an existing task that needs no metadata "
                        "change, supply only its current expected_revision."
                    ),
                }
            raise AgentRunError(
                "AGENT_TASK_UPDATE_INVALID",
                diagnostics={
                    "reason": "TASK_UPDATE_SCHEMA_INVALID",
                    "issues": issues,
                },
            ) from error
        try:
            return stage_update(
                database,
                context.context,
                context.tool_call_id,
                update,
                models_for_tasks,
            )
        except AgentRunError as error:
            if (
                provider in {"minimax", "deepseek"}
                and str(error) == "AGENT_TASK_UPDATE_INVALID"
                and stage_protocol_retry(database, context)
            ):
                reason = (
                    error.diagnostics.get("reason")
                    if isinstance(error.diagnostics, dict)
                    else "TASK_UPDATE_VALUE_INVALID"
                )
                return {
                    "status": "RETRY_REQUIRED",
                    "reason": reason,
                    "issues": [],
                    "instruction": (
                        "Call update_task exactly once more with the current staged "
                        "revision and only user-supported task metadata. All task CAS "
                        "and source checks still apply."
                    ),
                }
            raise

    update_tool = FunctionTool(
        name="update_task",
        description="Stage durable multi-turn task metadata, never execution. Omit for self-contained current-turn workflow drafts. source_quote is authoritative; text is interpretation. Related work: expected_revision alone links usage without revision; exclude unrelated chat. Starts at 0. Replacement: exact 将<old>改为<new> (or 把). USER_REVIEW: exact 确认完成：<text> entries only. Topological steps; atomic reply/confirmation commit. REPLY proves delivery only.",
        params_json_schema=TaskUpdate.model_json_schema(),
        on_invoke_tool=update_task, strict_json_schema=False,
    )

    @function_tool(failure_error_function=None)
    def list_execution_facts(
        context: RunContextWrapper[dict[str, Any]],
        progress_intent: AgentProgressIntent,
        after_goal_revision: StrictInt = 0,
        limit: StrictInt = 20,
        statuses: list[Utf8String] | None = None,
    ) -> dict[str, object]:
        """List execution statuses, WorkflowRun IDs, outputs and errors.
        Session/Project scoped; no parameters or observations.
        """
        validate_builtin_progress_intent("list_execution_facts", progress_intent)
        try:
            result = read_execution_facts(
                database,
                context.context["agent_run_id"],
                after_goal_revision=after_goal_revision,
                limit=limit,
                statuses=None if statuses is None else tuple(statuses),
            )
            return {
                **result,
                "progressOutcome": builtin_tool_outcome(
                    "list_execution_facts",
                    "execution_facts.listed",
                    {"factCount": len(result["items"])},
                ),
            }
        except ValueError as error:
            raise AgentRunError(
                "AGENT_MODEL_OUTPUT_INVALID",
                diagnostics={"reason": str(error)},
            ) from error

    @function_tool(failure_error_function=None)
    def get_execution_fact(
        context: RunContextWrapper[dict[str, Any]],
        progress_intent: AgentProgressIntent,
        workflow_run_id: Utf8String,
    ) -> dict[str, object]:
        """Get prior WorkflowRun facts. Session/Project scoped; no parameters or
        observations.
        """
        validate_builtin_progress_intent("get_execution_fact", progress_intent)
        try:
            result = read_execution_fact(
                database, context.context["agent_run_id"], workflow_run_id
            )
            return {
                **result,
                "progressOutcome": builtin_tool_outcome(
                    "get_execution_fact",
                    "execution_fact.read"
                    if result["status"] == "OK" else "execution_fact.not_found",
                    {},
                ),
            }
        except ValueError as error:
            raise AgentRunError(
                "AGENT_MODEL_OUTPUT_INVALID",
                diagnostics={"reason": str(error)},
            ) from error

    for tool in (list_execution_facts, get_execution_fact):
        tool.params_json_schema.pop("$defs", None)
        tool.params_json_schema["properties"]["progress_intent"] = (
            builtin_progress_intent_schema(tool.name)
        )
    return [
        update_tool,
        *(
            [list_execution_facts, get_execution_fact]
            if include_execution_facts else []
        ),
    ]
