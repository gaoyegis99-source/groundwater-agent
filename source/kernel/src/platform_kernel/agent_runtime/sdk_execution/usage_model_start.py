"""Model-request start accounting for the SDK usage hook."""

from __future__ import annotations

import time
from typing import Any

from agents import Agent, RunContextWrapper, TResponseInputItem

from ...errors import AgentRunError
from ..context_usage import context_usage
from ..governance import finalization_error, governance
from .activity_projection import _activity
from .types import ExecutionUsage
from .usage import _add_usage


class _ModelStartCaptureMixin:
    async def on_llm_start(
        self,
        context: RunContextWrapper[dict[str, Any]],
        agent: Agent[dict[str, Any]],
        system_prompt: str | None,
        input_items: list[TResponseInputItem],
    ) -> None:
        self.active_input_items = list(input_items)
        if self.context is None:
            self.context = context
            self.start = ExecutionUsage(
                context.usage.input_tokens,
                context.usage.output_tokens,
                context.usage.total_tokens,
            )
        elif context is not self.context:
            self.completed_usage = _add_usage(
                self.completed_usage,
                self._active_usage_delta(),
            )
            self.context = context
            self.start = ExecutionUsage(
                context.usage.input_tokens,
                context.usage.output_tokens,
                context.usage.total_tokens,
            )
        run_context = context.context
        control = (
            governance(run_context)
            if isinstance(run_context, dict) and "governance" in run_context
            else None
        )
        pending_retry = None if control is None else control["provider_retry_stage"]
        is_retry = False
        if (
            isinstance(pending_retry, (list, tuple))
            and len(pending_retry) == 2
            and isinstance(pending_retry[0], str)
            and pending_retry[1] == 2
        ):
            self.retry_provider_stage = (pending_retry[0], 2)
            control["provider_retry_stage"] = None
            is_retry = True
        run_context = getattr(context, "context", None)
        if isinstance(run_context, dict) and "governance" in run_context:
            control = governance(run_context)
            if not is_retry and control["request_count"] >= control["request_limit"]:
                raise AgentRunError(finalization_error(run_context))
            if not is_retry:
                control["request_count"] += 1
            control["provider_attempt_count"] += 1
        usage = context_usage(
            agent,
            system_prompt,
            input_items,
            available=self.available_input_tokens,
            observation_names=self.observation_names,
        )
        if usage["excess_tokens"]:
            self._begin_context_view(
                agent,
                system_prompt,
                input_items,
                usage,
                status="REJECTED",
                error_code="AGENT_CONTEXT_TOO_LARGE",
            )
            self.record_activity(
                {**self.segment, "status": "RUNNING", "context_usage": usage}
            )
            raise AgentRunError(
                "AGENT_CONTEXT_TOO_LARGE",
                diagnostics={"kind": "CONTEXT_LIMIT", **usage},
            )
        self.context_input_start = context.usage.input_tokens
        self._begin_context_view(
            agent,
            system_prompt,
            input_items,
            usage,
            status="DISPATCHED",
        )
        self.provider_request_count += 1
        if self.usage_ledger is not None:
            snapshot = ExecutionUsage(
                context.usage.input_tokens,
                context.usage.output_tokens,
                context.usage.total_tokens,
            )
            if self.retry_provider_stage is None:
                stage, attempt = (
                    f"{self.context_purpose}:{self.provider_request_count}",
                    1,
                )
            else:
                stage, attempt = self.retry_provider_stage
                self.retry_provider_stage = None
            provider_request_id = self.usage_ledger.begin_provider_request(
                agent_run_id=self.request_agent_run_id,
                stage=stage,
                provider=self.provider,
                model=self.provider_model,
                protocol=self.protocol,
                attempt=attempt,
            )
            self.active_provider_request = (
                provider_request_id,
                time.monotonic(),
                snapshot,
            )
        self.model_activity = _activity(
            self.segment,
            "MODEL_REQUEST",
            self.purpose,
        )
        if self.tool_retry_settings is not None:
            self.model_activity.update(
                {
                    "title": "修正工具调用",
                    "summary": "Agent 正在根据合同校验结果修正参数并重新调用同一能力。",
                }
            )
        elif self.next_model_activity is not None:
            title, summary = self.next_model_activity
            self.model_activity.update({"title": title, "summary": summary})
            self.next_model_activity = None
        self.model_activity["context_usage"] = usage
        self.record_activity(self.model_activity)
