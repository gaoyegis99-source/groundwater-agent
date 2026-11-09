"""Bounded Provider-attempt execution for the stable IntentRouter facade."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from agents import ModelBehaviorError
from pydantic import ValidationError

from ..agent_runtime.sdk_execution.activity import _RunUsageCapture
from .models import IntentRoutingUsage
from .provider_call import run_provider_call
from .result_mapping import map_provider_result


@dataclass(frozen=True)
class RouteExecutionRequest:
    agent: object
    capture: _RunUsageCapture
    input_items: list[object]
    trace_id: str
    timeout_seconds: float
    uses_sdk_structured_output: bool
    task_context: dict | None
    candidate_context: dict | None


@dataclass(frozen=True)
class RouteExecutionResult:
    decision: object
    usage: IntentRoutingUsage


class RouteExecutionAttemptError(Exception):
    """Preserve completed-attempt usage when a final protocol parse fails."""

    def __init__(self, cause: Exception, usage: IntentRoutingUsage) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.usage = usage


async def execute_route_attempts(
    request: RouteExecutionRequest,
) -> RouteExecutionResult:
    """Run one transport attempt with at most one protocol repair."""

    input_items = list(request.input_items)
    attempt_usage = IntentRoutingUsage(0, 0, 0)
    async with asyncio.timeout(request.timeout_seconds):
        for protocol_attempt in (1, 2):
            try:
                call = await run_provider_call(
                    agent=request.agent,
                    input_items=input_items,
                    capture=request.capture,
                    trace_id=request.trace_id,
                )
                current = IntentRoutingUsage(
                    call.usage.input_tokens,
                    call.usage.output_tokens,
                    call.usage.total_tokens,
                )
                attempt_usage = IntentRoutingUsage(
                    attempt_usage.input_tokens + current.input_tokens,
                    attempt_usage.output_tokens + current.output_tokens,
                    attempt_usage.total_tokens + current.total_tokens,
                )
                return RouteExecutionResult(
                    decision=map_provider_result(
                        call.result,
                        uses_sdk_structured_output=request.uses_sdk_structured_output,
                        task_context=request.task_context,
                        candidate_context=request.candidate_context,
                    ),
                    usage=attempt_usage,
                )
            except (
                ModelBehaviorError,
                TypeError,
                UnicodeEncodeError,
                ValueError,
                ValidationError,
            ) as error:
                if protocol_attempt == 2:
                    raise RouteExecutionAttemptError(
                        cause=error,
                        usage=attempt_usage,
                    ) from error
                request.capture.mark_last_protocol_failure()
                input_items = [
                    *input_items,
                    {
                        "role": "assistant",
                        "content": "The previous response violated the required output protocol. Return the exact schema/literal now.",
                    },
                ]
