"""Body-aware construction of a bounded Provider protocol repair."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from agents import Agent, ItemHelpers, TResponseInputItem

from ...errors import AgentRunError
from ..result_processing import prepared_approval_correction
from .usage_provider import _ProtocolRepairRequiredError
from .usage_settlement import settle_output


def rejected_call_repair_input(
    *,
    active_input_items: list[TResponseInputItem],
    response: object,
    calls: list[object],
    agent: Agent[dict[str, Any]],
    reason: str,
    detail: str,
    prepared_arguments: dict[str, object] | None = None,
) -> list[TResponseInputItem]:
    """Preserve the rejected response while requesting exactly one safe retry."""

    output = getattr(response, "output", None)
    if not isinstance(output, list):
        raise AgentRunError("AGENT_MODEL_OUTPUT_INVALID")
    tool_choice = agent.model_settings.tool_choice
    instruction = (
        f"Call {tool_choice} exactly once with arguments matching its schema."
        if isinstance(tool_choice, str)
        and tool_choice not in {"auto", "required", "none"}
        else "Call exactly one available tool with arguments matching its schema."
    )
    rejected = json.dumps(
        {
            "status": "RETRY_REQUIRED",
            "reason": reason,
            "instruction": (
                f"This tool call was not executed because {detail}. {instruction}"
            ),
            **({"prepared_arguments": prepared_arguments} if prepared_arguments is not None else {}),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return [
        *ItemHelpers.input_to_new_input_list(active_input_items),
        *[
            item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else item
            for item in output
        ],
        *[ItemHelpers.tool_call_output_item(call, rejected) for call in calls],
    ]


class _PreparedApprovalRepairMixin:
    def _check_prepared_retry_tool(self, context, calls) -> None:
        if self.prepared_retry_tool is None:
            return
        if len(calls) != 1 or calls[0].name != self.prepared_retry_tool:
            self._finish_provider_request("FAILED", "PROTOCOL_OR_SCHEMA_INVALID", context)
            raise AgentRunError("AGENT_MODEL_OUTPUT_INVALID", diagnostics={
                "reason": "TASK_PREPARATION_REQUIRED",
                "issues": [{"path": "/tool_calls", "type": "prepared_tool_mismatch"}],
            })
        self.prepared_retry_tool = None

    def _check_prepared_approval(self, context, agent, response, call) -> None:
        model = self.approval_models.get(call.name)
        if model is None or self.provider != "deepseek" or self.usage_ledger is None:
            return
        correction = prepared_approval_correction(
            call.arguments, model, self.active_input_items,
            provider=self.provider, explicit_model_tool_request=self.explicit_model_tool_request,
            has_workflow_selection=self.has_workflow_selection,
        )
        if correction is None:
            return
        prepared, diagnostics = correction
        self._finish_provider_request("FAILED", "PROTOCOL_OR_SCHEMA_INVALID", context)
        if not self.usage_ledger.stage_protocol_retry(context):
            raise AgentRunError("AGENT_MODEL_OUTPUT_INVALID", diagnostics=diagnostics)
        agent.model_settings = replace(
            agent.model_settings, tool_choice=call.name, parallel_tool_calls=False,
        )
        self.prepared_retry_tool = call.name
        repair_input = rejected_call_repair_input(
            active_input_items=self.active_input_items, response=response, calls=[call], agent=agent,
            reason="TASK_PREPARATION_REQUIRED",
            detail="its arguments do not match the single READY preparation. Copy prepared_arguments verbatim",
            prepared_arguments=prepared,
        )
        settlement = settle_output(
            usage_ledger=self.usage_ledger, active_context_view_id=self.active_context_view_id,
            context=context, context_input_start=self.context_input_start,
            status="FAILED", error_code="AGENT_MODEL_OUTPUT_INVALID", purpose=self.purpose,
            model_activity=self.model_activity, activity_recorder=self.activity_recorder,
        )
        self.active_context_view_id = settlement.active_context_view_id
        self.model_activity = settlement.model_activity
        raise _ProtocolRepairRequiredError(repair_input)


__all__ = ("_PreparedApprovalRepairMixin", "rejected_call_repair_input")
