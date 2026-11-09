"""Tool activity accounting and post-tool governance transitions."""

from __future__ import annotations

from dataclasses import replace
import json

from ...errors import AgentRunError
from ..governance import advance_from_discovery, governance
from ..memory import token_count
from ...tool_progress import (
    builtin_progress_definition,
    parse_progress_intent,
    render_declared_tool_outcome,
    render_processing_progress,
    render_running_progress,
    validate_builtin_progress_intent,
)
from .activity_projection import (
    _activity,
    _activity_status,
)


class _ToolUsageCaptureMixin:
    async def on_tool_start(self, context, agent, tool) -> None:
        definition = self.observation_tools.get(tool.name)
        progress = (
            definition.progress
            if definition is not None and definition.progress is not None
            else builtin_progress_definition(tool.name)
        )
        if progress is not None:
            try:
                arguments = json.loads(context.tool_arguments)
            except (TypeError, json.JSONDecodeError) as error:
                raise AgentRunError(
                    "AGENT_MODEL_OUTPUT_INVALID",
                    diagnostics={"reason": "PROGRESS_INTENT_INVALID"},
                ) from error
            if not isinstance(arguments, dict):
                raise AgentRunError(
                    "AGENT_MODEL_OUTPUT_INVALID",
                    diagnostics={"reason": "PROGRESS_INTENT_INVALID"},
                )
            if definition is not None and definition.progress is not None:
                intent, _service_arguments = parse_progress_intent(definition, arguments)
            else:
                intent = validate_builtin_progress_intent(
                    tool.name, arguments.get("progress_intent")
                )
            title, summary = render_running_progress(progress, intent)
            activity = _activity(
                self.segment, "TOOL", "DECLARED_TOOL",
                tool_name=tool.name, tool_call_id=context.tool_call_id,
            )
            activity.update({
                "title": title,
                "summary": summary,
                "capability": progress.capability,
                "semantic_role": progress.semantic_role,
                "template_key": progress.template_key,
            })
            self.record_activity(activity)
            activity["_progress_intent"] = intent
            self.tool_activities[context.tool_call_id] = activity
            return
        if tool.name not in self.observation_names:
            return
        activity = _activity(
            self.segment, "TOOL", "OBSERVATION",
            tool_name=tool.name, tool_call_id=context.tool_call_id,
        )
        self.record_activity(activity)
        self.tool_activities[context.tool_call_id] = activity

    async def on_tool_end(self, context, agent, tool, result) -> None:
        run_context = getattr(context, "context", None)
        if isinstance(run_context, dict) and "governance" in run_context:
            control = governance(run_context)
            retry_required = _retry_required(result)
            if retry_required:
                # An invalid read did not produce evidence and its bounded second
                # attempt belongs to the same Provider stage. Let that correction
                # run before applying a request-budget finalization transition.
                control["finalization_pending"] = False
                if self.tool_retry_settings is None:
                    self.tool_retry_settings = (
                        tool.name,
                        agent.model_settings.tool_choice,
                        agent.model_settings.parallel_tool_calls,
                    )
                agent.model_settings = replace(
                    agent.model_settings,
                    tool_choice=tool.name,
                    parallel_tool_calls=False,
                )
            else:
                repair_settings = self.tool_retry_settings
                if repair_settings is not None and repair_settings[0] == tool.name:
                    agent.model_settings = replace(
                        agent.model_settings,
                        tool_choice=repair_settings[1],
                        parallel_tool_calls=repair_settings[2],
                    )
                    self.tool_retry_settings = None
            if not retry_required and control["finalization_pending"]:
                control["finalization_pending"] = False
                if control["phase"] == "DISCOVERY":
                    advance_from_discovery(control, control["finalization_reason"])
                else:
                    control["phase"] = "FINALIZATION"
                    control["finalizing"] = True
            if control["finalizing"]:
                agent.model_settings = replace(
                    agent.model_settings,
                    tool_choice="submit_reply",
                    parallel_tool_calls=False,
                )
            elif (
                tool.name == "prepare_task_inputs"
                and self.provider in {"minimax", "deepseek"}
                and _preparation_is_ready(result)
            ):
                planned_tool = (
                    None
                    if self.usage_ledger is None
                    or self.request_agent_run_id is None
                    else self.usage_ledger.planned_approval_tool(
                        self.request_agent_run_id,
                        await agent.get_all_tools(context),
                    )
                )
                agent.model_settings = replace(
                    agent.model_settings,
                    tool_choice=planned_tool or "record_plan",
                    parallel_tool_calls=False,
                )
            elif (
                tool.name == "record_plan"
                and self.usage_ledger is not None
                and self.request_agent_run_id is not None
            ):
                planned_tool = self.usage_ledger.planned_approval_tool(
                    self.request_agent_run_id,
                    await agent.get_all_tools(context),
                )
                if planned_tool is not None:
                    agent.model_settings = replace(
                        agent.model_settings,
                        tool_choice=planned_tool,
                        parallel_tool_calls=False,
                    )
            elif (
                control["phase"] == "PROPOSAL"
                and self.provider in {"minimax", "deepseek"}
            ):
                enabled = {
                    item.name for item in await agent.get_all_tools(context)
                }
                next_tool = (
                    "submit_reply"
                    if control["mode"] == "WORKFLOW_DRAFT"
                    and "submit_reply" in enabled
                    else (
                        "prepare_task_inputs"
                        if "prepare_task_inputs" in enabled
                        else (
                            "read_evidence"
                            if "read_evidence" in enabled
                            else None
                        )
                    )
                )
                if next_tool is not None:
                    # Proposal reserve is a deterministic state machine. Once
                    # discovery closes, select its next boundary tool instead
                    # of relying on Provider prose or another stale read.
                    agent.model_settings = replace(
                        agent.model_settings,
                        tool_choice=next_tool,
                        parallel_tool_calls=False,
                    )
        if (
            tool.name not in self.observation_names
            and builtin_progress_definition(tool.name) is None
        ):
            return
        activity = self.tool_activities.pop(context.tool_call_id)
        encoded = json.dumps(
            result, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        activity["output_bytes"] = len(encoded)
        activity["output_tokens"] = token_count(result)
        definition = self.observation_tools.get(tool.name)
        progress = (
            definition.progress
            if definition is not None and definition.progress is not None
            else builtin_progress_definition(tool.name)
        )
        if progress is not None:
            body = _decoded_result(result)
            intent = activity.pop("_progress_intent", None)
            if intent is None:
                raise AgentRunError("PROGRESS_INTENT_MISSING")
            outcome_value = None
            if isinstance(body, dict):
                outcome_value = body.get("progressOutcome")
                if (
                    outcome_value is None
                    and definition is not None
                    and definition.progress is not None
                ):
                    outcome_value = body.get("outcome")
            outcome, summary, is_control = render_declared_tool_outcome(
                progress, outcome_value
            )
            activity["outcome_code"] = outcome.outcome_code
            activity["facts"] = outcome.facts
            if not is_control:
                self.next_model_activity = render_processing_progress(
                    progress, intent
                )
        else:
            summary = None
        if summary is not None:
            activity["summary"] = summary
        terminal_status = (
            "FAILED"
            if activity.get("outcome_code") == "control.retry_required"
            else "COMPLETE"
        )
        _activity_status(self.record_activity, activity, terminal_status)

    def failed_activity_id(self) -> str | None:
        active = list(self.tool_activities.values())
        if self.model_activity is not None:
            active.append(self.model_activity)
        return active[0]["activity_id"] if len(active) == 1 else None


def _preparation_is_ready(result: object) -> bool:
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return False
    return isinstance(result, dict) and result.get("status") == "READY"


def _retry_required(result: object) -> bool:
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return False
    return isinstance(result, dict) and result.get("status") == "RETRY_REQUIRED"


def _decoded_result(result: object) -> object:
    if not isinstance(result, str):
        return result
    try:
        return json.loads(result)
    except json.JSONDecodeError:
        return result


__all__ = ["_ToolUsageCaptureMixin", "_preparation_is_ready", "_retry_required"]
