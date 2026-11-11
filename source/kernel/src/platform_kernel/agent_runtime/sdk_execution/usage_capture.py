"""SDK hooks that persist body-free usage and activity metadata."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from agents import (
    RunContextWrapper,
    RunHooks,
    TResponseInputItem,
)

from ...errors import AgentRunError
from ...settings import ObservationToolDefinition
from ..governance import finalization_error, governance
from .types import ExecutionUsage
from .usage_ports import ActivityRecorder, UsageLedgerPort

from .activity_projection import _activity
from .usage_output_validation import (
    allows_minimax_final_text,
    response_function_calls,
    validate_terminal_output,
)
from .usage_protocol_repair import rejected_call_repair_input
from .usage_settlement import settle_output
from .usage import _ZERO_USAGE, _add_usage
from .usage_context_views import _ContextViewCaptureMixin
from .usage_model_start import _ModelStartCaptureMixin
from .usage_provider import (
    _ProtocolRepairRequiredError,
    _ProviderUsageCaptureMixin,
)
from .usage_tools import _ToolUsageCaptureMixin


class _RunUsageCapture(
    _ContextViewCaptureMixin,
    _ModelStartCaptureMixin,
    _ProviderUsageCaptureMixin,
    _ToolUsageCaptureMixin,
    RunHooks,
):
    def __init__(
        self,
        available_input_tokens: int,
        *,
        segment: dict,
        activity_recorder: ActivityRecorder,
        observation_names: frozenset[str],
        observation_tools: tuple[ObservationToolDefinition, ...] = (),
        purpose: str,
        require_tool_call: bool = True,
        request_agent_run_id: str | None = None,
        usage_ledger: UsageLedgerPort | None = None,
        policy_version: str = "context-policy.v1",
        context_purpose: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        protocol: str | None = None,
    ) -> None:
        self.available_input_tokens = available_input_tokens
        self.context: RunContextWrapper[dict[str, Any]] | None = None
        self.start: ExecutionUsage | None = None
        self.completed_usage = _ZERO_USAGE
        self.segment = segment
        self.activity_recorder = activity_recorder
        self.observation_names = observation_names
        self.observation_tools = {tool.name: tool for tool in observation_tools}
        self.purpose = purpose
        self.require_tool_call = require_tool_call
        self.provider_request_count = 0
        self.model_activity: dict | None = None
        self.tool_activities: dict[str, dict] = {}
        self.next_model_activity: tuple[str, str] | None = None
        self.request_agent_run_id = request_agent_run_id
        self.usage_ledger = usage_ledger
        self.policy_version = policy_version
        self.context_purpose = context_purpose or (
            purpose if purpose in {"INTENT_ROUTING", "COMPACTION"} else "AGENT_INITIAL"
        )
        self.initial_context_manifest: dict[str, object] | None = None
        self.active_context_view_id: str | None = None
        self.context_input_start = 0
        self.provider = provider
        self.provider_model = model
        self.protocol = protocol
        self.active_provider_request: tuple[str, float, ExecutionUsage] | None = None
        self.retry_provider_stage: tuple[str, int] | None = None
        self.tool_retry_settings: tuple[str, object, bool | None] | None = None
        self.token_warning_emitted = False
        self.active_input_items: list[TResponseInputItem] = []

    def record_activity(self, activity: dict[str, Any]) -> None:
        self.activity_recorder.record(activity)

    def stage_model_behavior_retry(self) -> bool:
        """Persist one safe retry when the SDK rejects Provider output early.

        Some adapters raise ``ModelBehaviorError`` while decoding a response,
        before ``on_llm_end`` can inspect its tool calls.  At that point no tool
        has executed, so the same bounded, per-stage protocol repair used by
        the normal end hook is safe.  Settle both the body-free Provider ledger
        and the context view before the retry starts; otherwise attempt 2 would
        overwrite the active attempt-1 accounting.
        """
        if (
            self.provider not in {"minimax", "deepseek"}
            or self.context is None
            or self.usage_ledger is None
            or "governance" not in self.context.context
        ):
            return False
        self._finish_provider_request(
            "FAILED",
            "PROTOCOL_OR_SCHEMA_INVALID",
            self.context,
        )
        if not self.usage_ledger.stage_protocol_retry(self.context):
            return False
        settlement = settle_output(
            usage_ledger=self.usage_ledger,
            active_context_view_id=self.active_context_view_id,
            context=self.context,
            context_input_start=self.context_input_start,
            status="FAILED",
            error_code="AGENT_MODEL_OUTPUT_INVALID",
            purpose=self.purpose,
            model_activity=self.model_activity,
            activity_recorder=self.activity_recorder,
        )
        self.active_context_view_id = settlement.active_context_view_id
        self.model_activity = settlement.model_activity
        return True

    async def on_llm_end(self, context, agent, response) -> None:
        calls = response_function_calls(response)
        repairing_missing_tool = False
        allow_minimax_final_text = False
        if isinstance(context.context, dict) and "governance" in context.context:
            control = governance(context.context)
            control["total_tokens"] = context.usage.total_tokens
            if (
                not self.token_warning_emitted
                and control["token_limit"] is not None
                and control["total_tokens"] >= control["token_limit"]
            ):
                self.token_warning_emitted = True
                warning = _activity(
                    self.segment,
                    "DIAGNOSTIC",
                    "TOKEN_SOFT_TARGET",
                    status="COMPLETE",
                )
                warning.update(
                    {
                        "severity": "WARNING",
                        "observed_tokens": control["total_tokens"],
                        "soft_target_tokens": control["token_limit"],
                        "message": "Token 软目标已超过；任务仍按进展和质量要求继续。",
                    }
                )
                self.record_activity(warning)
            if (
                control["finalizing"]
                and (len(calls) != 1 or calls[0].name != "submit_reply")
                and self.provider == "minimax"
            ):
                # A MiniMax final prose response is safe to project only after
                # Kernel governance has already closed tool discovery. Let the
                # SDK return it as final_output; segment_runner applies the
                # SubmittedReply contract after the run.
                allow_minimax_final_text = allows_minimax_final_text(response, control)
            finalization_call_mismatch = (
                control["finalizing"]
                and bool(calls)
                and (len(calls) != 1 or calls[0].name != "submit_reply")
            )
            mixed_submit_batch = len(calls) > 1 and any(
                call.name == "submit_reply" for call in calls
            )
            if (
                self.provider == "minimax"
                and self.require_tool_call
                and (finalization_call_mismatch or mixed_submit_batch)
            ):
                # MiniMax can disregard the selected final tool or
                # parallel_tool_calls=False. Reject the whole response before
                # any tool runs, then carry the full response into one bounded
                # outer retry with every rejected call accounted for.
                self._finish_provider_request(
                    "FAILED",
                    "PROTOCOL_OR_SCHEMA_INVALID",
                    context,
                )
                reason = (
                    "FINALIZATION_TOOL_INVALID"
                    if finalization_call_mismatch
                    else "MULTIPLE_TOOL_CALLS"
                )
                if (
                    self.usage_ledger is None
                    or not self.usage_ledger.stage_protocol_retry(context)
                ):
                    raise AgentRunError(
                        "AGENT_MODEL_OUTPUT_INVALID",
                        diagnostics={
                            "reason": reason,
                            "issues": [
                                {
                                    "path": "/tool_calls",
                                    "validator": (
                                        "finalTool"
                                        if finalization_call_mismatch
                                        else "maxItems"
                                    ),
                                }
                            ],
                        },
                    )
                if control["finalizing"]:
                    agent.model_settings = replace(
                        agent.model_settings,
                        tool_choice="submit_reply",
                        parallel_tool_calls=False,
                    )
                repair_input = rejected_call_repair_input(
                    active_input_items=self.active_input_items,
                    response=response,
                    calls=calls,
                    agent=agent,
                    reason=reason,
                    detail=(
                        "the finalization response did not call submit_reply exactly once"
                        if finalization_call_mismatch
                        else "the response contained multiple tool calls while parallel "
                        "calls are disabled"
                    ),
                )
                settlement = settle_output(
                    usage_ledger=self.usage_ledger,
                    active_context_view_id=self.active_context_view_id,
                    context=context,
                    context_input_start=self.context_input_start,
                    status="FAILED",
                    error_code="AGENT_MODEL_OUTPUT_INVALID",
                    purpose=self.purpose,
                    model_activity=self.model_activity,
                    activity_recorder=self.activity_recorder,
                )
                self.active_context_view_id = settlement.active_context_view_id
                self.model_activity = settlement.model_activity
                raise _ProtocolRepairRequiredError(repair_input, response)
            if (
                not calls
                and not repairing_missing_tool
                and not allow_minimax_final_text
                and self.provider in {"minimax", "deepseek"}
                and self.require_tool_call
            ):
                enabled_tools = await agent.get_all_tools(context)
                self._finish_provider_request(
                    "FAILED",
                    "PROTOCOL_OR_SCHEMA_INVALID",
                    context,
                )
                if (
                    self.usage_ledger is None
                    or not self.usage_ledger.stage_protocol_retry(context)
                ):
                    raise AgentRunError("AGENT_MODEL_OUTPUT_INVALID")
                planned_tool = (
                    None
                    if self.usage_ledger is None or self.request_agent_run_id is None
                    else self.usage_ledger.planned_approval_tool(
                        self.request_agent_run_id,
                        enabled_tools,
                    )
                )
                repair_tool = planned_tool or (
                    "prepare_task_inputs"
                    if any(tool.name == "prepare_task_inputs" for tool in enabled_tools)
                    else None
                )
                if repair_tool is not None:
                    # A Provider can ignore generic `required` for very large
                    # schemas. Name the persisted approval tool when available;
                    # otherwise require input preparation so an execution
                    # proposal returns structured READY or BLOCKED state.
                    agent.model_settings = replace(
                        agent.model_settings,
                        tool_choice=repair_tool,
                        parallel_tool_calls=False,
                    )
                repairing_missing_tool = True
            if calls and self.require_tool_call:
                enabled = {tool.name for tool in await agent.get_all_tools(context)}
                missing = [call for call in calls if call.name not in enabled]
                if missing:
                    declared = {tool.name for tool in agent.tools}
                    replayed_disabled = any(call.name in declared for call in missing)
                    repairable_deepseek_read_replay = (
                        replayed_disabled
                        and self.provider == "deepseek"
                        and len(missing) == len(calls)
                        and all(
                            call.name
                            in self.observation_names
                            | {
                                "list_operators",
                                "get_operator",
                                "list_execution_facts",
                                "get_execution_fact",
                            }
                            for call in missing
                        )
                    )
                    if repairable_deepseek_read_replay:
                        # Discovery has already closed, so none of these calls can
                        # execute. Preserve that boundary while giving DeepSeek one
                        # body-aware correction to publish the authored draft.
                        self._finish_provider_request(
                            "FAILED",
                            "PROTOCOL_OR_SCHEMA_INVALID",
                            context,
                        )
                        reason = (
                            "DRAFT_DISCOVERY_CLOSED"
                            if control["mode"] == "WORKFLOW_DRAFT"
                            and control["phase"] == "PROPOSAL"
                            else (
                                "DISCOVERY_CLOSED"
                                if control["finalizing"]
                                else "OBSERVATION_TOOL_ALREADY_CONSUMED"
                            )
                        )
                        if (
                            self.usage_ledger is None
                            or not self.usage_ledger.stage_protocol_retry(context)
                        ):
                            raise AgentRunError(
                                "AGENT_MODEL_OUTPUT_INVALID",
                                diagnostics={
                                    "reason": reason,
                                    "issues": [
                                        {
                                            "path": "/tool_calls",
                                            "validator": "enabledTool",
                                        }
                                    ],
                                },
                            )
                        terminal_repair = (
                            control["mode"] == "WORKFLOW_DRAFT"
                            and control["phase"] == "PROPOSAL"
                            or control["finalizing"]
                        )
                        if terminal_repair:
                            agent.model_settings = replace(
                                agent.model_settings,
                                tool_choice="submit_reply",
                                parallel_tool_calls=False,
                            )
                        elif "prepare_task_inputs" in enabled:
                            # Execution proposals cannot establish a blocker
                            # through prose. Input preparation revalidates exact
                            # versions and returns READY or structured BLOCKED,
                            # so naming it is a fail-closed protocol repair.
                            agent.model_settings = replace(
                                agent.model_settings,
                                tool_choice="prepare_task_inputs",
                                parallel_tool_calls=False,
                            )
                        repair_input = rejected_call_repair_input(
                            active_input_items=self.active_input_items,
                            response=response,
                            calls=calls,
                            agent=agent,
                            reason=reason,
                            detail=(
                                "the requested observation tool is no longer "
                                "enabled; submit the answer using observations "
                                "already collected"
                                if terminal_repair
                                else "the requested observation tool was already "
                                "consumed or is not valid for the current evidence "
                                "phase; call one currently enabled tool"
                            ),
                        )
                        settlement = settle_output(
                            usage_ledger=self.usage_ledger,
                            active_context_view_id=self.active_context_view_id,
                            context=context,
                            context_input_start=self.context_input_start,
                            status="FAILED",
                            error_code="AGENT_MODEL_OUTPUT_INVALID",
                            purpose=self.purpose,
                            model_activity=self.model_activity,
                            activity_recorder=self.activity_recorder,
                        )
                        self.active_context_view_id = settlement.active_context_view_id
                        self.model_activity = settlement.model_activity
                        raise _ProtocolRepairRequiredError(repair_input, response)
                    if replayed_disabled and self.provider != "minimax":
                        # Other Providers treat a disabled declared tool as a
                        # governance/budget violation. MiniMax can replay a tool
                        # from the preceding phase after the enabled set changes;
                        # it is effect-free because the SDK has not invoked it,
                        # so the normal single protocol correction is safe.
                        raise AgentRunError(
                            finalization_error(context.context)
                            if control["finalizing"]
                            else "AGENT_MODEL_OUTPUT_INVALID"
                        )
                    self._finish_provider_request(
                        "FAILED",
                        "PROTOCOL_OR_SCHEMA_INVALID",
                        context,
                    )
                    # Reject a mixed batch before any valid call can run. Only
                    # a wholly invalid response can be repaired without effects.
                    if (
                        len(missing) != len(calls)
                        or self.usage_ledger is None
                        or not self.usage_ledger.stage_protocol_retry(context)
                    ):
                        raise AgentRunError("AGENT_MODEL_OUTPUT_INVALID")
                    if (
                        replayed_disabled
                        and self.provider == "minimax"
                        and control["phase"] != "DISCOVERY"
                    ):
                        enabled_tools = await agent.get_all_tools(context)
                        planned_tool = (
                            None
                            if self.request_agent_run_id is None
                            else self.usage_ledger.planned_approval_tool(
                                self.request_agent_run_id,
                                enabled_tools,
                            )
                        )
                        # Once discovery is closed, repeating a hidden read tool
                        # cannot add facts. Select the persisted approval action
                        # when one is unambiguous; otherwise require a terminal
                        # blocker reply instead of repeating the stale tool.
                        agent.model_settings = replace(
                            agent.model_settings,
                            tool_choice=planned_tool or "submit_reply",
                            parallel_tool_calls=False,
                        )
                    repairing_missing_tool = True
            if (
                control["finalizing"]
                and not repairing_missing_tool
                and not allow_minimax_final_text
                and (len(calls) != 1 or calls[0].name != "submit_reply")
            ):
                raise AgentRunError(finalization_error(context.context))
            if (
                calls
                and not repairing_missing_tool
                and calls[0].name != "submit_reply"
                and (
                    control["phase"] == "DISCOVERY"
                    and (control["request_count"] >= control["discovery_request_limit"])
                    or control["request_count"] >= control["request_limit"] - 1
                )
            ):
                control["finalization_pending"] = True
                control["finalization_reason"] = (
                    control["finalization_reason"] or "BUDGET"
                )
        validate_terminal_output(
            agent_name=agent.name,
            require_tool_call=self.require_tool_call,
            calls=calls,
            repairing_missing_tool=repairing_missing_tool,
            allow_minimax_final_text=allow_minimax_final_text,
            response=response,
        )
        settlement = settle_output(
            usage_ledger=self.usage_ledger,
            active_context_view_id=self.active_context_view_id,
            context=context,
            context_input_start=self.context_input_start,
            status="FAILED" if repairing_missing_tool else "COMPLETE",
            error_code=(
                "AGENT_MODEL_OUTPUT_INVALID" if repairing_missing_tool else None
            ),
            purpose=self.purpose,
            model_activity=self.model_activity,
            activity_recorder=self.activity_recorder,
        )
        self.active_context_view_id = settlement.active_context_view_id
        self.model_activity = settlement.model_activity
        self._finish_provider_request("SUCCEEDED", None, context)

    def _active_usage_delta(self) -> ExecutionUsage:
        if self.context is None or self.start is None:
            return _ZERO_USAGE
        return ExecutionUsage(
            self.context.usage.input_tokens - self.start.input_tokens,
            self.context.usage.output_tokens - self.start.output_tokens,
            self.context.usage.total_tokens - self.start.total_tokens,
        )

    def delta(self) -> ExecutionUsage:
        return _add_usage(self.completed_usage, self._active_usage_delta())
