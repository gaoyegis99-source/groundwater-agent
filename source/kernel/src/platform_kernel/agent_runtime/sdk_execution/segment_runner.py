"""One bounded Agent SDK compute segment and its failure mapping."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from agents import (
    Agent,
    ItemHelpers,
    MaxTurnsExceeded,
    ModelBehaviorError,
    ModelTimeoutError,
    RunConfig,
    Runner,
    TResponseInputItem,
    UserError,
)
from openai import APIConnectionError, APIStatusError, APITimeoutError
import httpx
import httpx2
from openai.types.responses import ResponseFunctionToolCall
from uuid import uuid4

from ...errors import AgentRunError, UpstreamServiceError
from ..availability import ProviderUnavailableError
from ..governance import (
    finalization_error,
    governance,
)
from ..memory import (
    MemoryUpdate,
)
from ..preview import ReplyPreview
from .activity import (
    _RunUsageCapture,
    _add_usage,
    _minimax_final_reply_json,
    _responses_usage,
)
from .usage_capture import _ProtocolRepairRequiredError
from .types import (
    ExecutionUsage,
    SDKExecutionError,
    SDKExecutionResult,
)

_LOGGER = logging.getLogger(__name__)


async def _execute_agent(
    agent: Agent[dict[str, Any]],
    agent_input: Any,
    run_config: RunConfig,
    *,
    response_start: int,
    previous_usage: ExecutionUsage,
    memory_update: MemoryUpdate | None,
    usage_capture: _RunUsageCapture,
    deadline_at: float,
    context: dict[str, Any] | None = None,
    phase: str,
    max_turns: int,
    request_timeout: float | None = None,
    preview=None,
) -> SDKExecutionResult:
    arguments: dict[str, Any] = {} if context is None else {"context": context}
    deadline = asyncio.timeout_at(deadline_at)
    request_deadline = asyncio.timeout(request_timeout)
    try:
        async with deadline, request_deadline:
            async def run_once(value):
                if preview is None:
                    return await Runner.run(
                        agent, value, max_turns=max_turns, run_config=run_config,
                        hooks=usage_capture, **arguments,
                    )
                streamed = Runner.run_streamed(
                    agent, value, max_turns=max_turns, run_config=run_config,
                    hooks=usage_capture, **arguments,
                )
                renderer = ReplyPreview(preview)
                try:
                    async for event in streamed.stream_events():
                        renderer.feed(event)
                finally:
                    if not streamed.is_complete:
                        streamed.cancel()
                    preview(None)
                return streamed

            result: Any
            try:
                try:
                    result = await run_once(agent_input)
                    repaired_early_model_error = False
                except ModelBehaviorError:
                    if not usage_capture.stage_model_behavior_retry():
                        raise
                    result = await run_once(_early_model_error_repair_input(
                        usage_capture, agent,
                    ))
                    repaired_early_model_error = True
            except _ProtocolRepairRequiredError as repair:
                response_batch = [repair.response]
                result = await run_once(repair.repair_input)
                response_batch.extend(result.raw_responses)
                repaired_early_model_error = False
            else:
                response_batch = list(result.raw_responses[response_start:])
            if _needs_missing_tool_retry(result, usage_capture):
                repair_input = _missing_tool_repair_input(result, agent)
                result = await run_once(repair_input)
                response_batch.extend(result.raw_responses)
    except TimeoutError as error:
        if not deadline.expired() and not request_deadline.expired():
            raise
        raise _execution_failure(
            "AGENT_DEADLINE_EXCEEDED" if deadline.expired() else "AGENT_PROVIDER_UNAVAILABLE",
            not deadline.expired(),
            previous_usage,
            usage_capture,
            memory_update,
            error,
        ) from error
    except UpstreamServiceError as error:
        raise _execution_failure(
            str(error), True, previous_usage, usage_capture, memory_update, error
        ) from error
    except AgentRunError as error:
        raise _execution_failure(
            str(error), False, previous_usage, usage_capture, memory_update, error
        ) from error
    except UserError as error:
        cause = error.__cause__
        if isinstance(cause, UpstreamServiceError):
            raise _execution_failure(
                str(cause), True, previous_usage, usage_capture, memory_update, error
            ) from error
        if isinstance(cause, AgentRunError):
            raise _execution_failure(
                str(cause), False, previous_usage, usage_capture, memory_update, error
            ) from error
        # SDK UserError denotes an invalid SDK configuration, not bad user input.
        # Preserve the failure lifecycle without exposing SDK/provider details.
        raise _execution_failure(
            "AGENT_SDK_CONFIGURATION_INVALID",
            False,
            previous_usage,
            usage_capture,
            memory_update,
            error,
        ) from error
    except APIStatusError as error:
        _LOGGER.warning(
            "agent provider failure phase=%s upstream_status=%d",
            phase, error.status_code,
        )
        raise _execution_failure(
            "AGENT_PROVIDER_UNAVAILABLE",
            True,
            previous_usage,
            usage_capture,
            memory_update,
            error,
        ) from error
    except (
        APIConnectionError,
        APITimeoutError,
        ModelTimeoutError,
        ProviderUnavailableError,
        httpx.RequestError,
        httpx2.RequestError,
    ) as error:
        raise _execution_failure(
            "AGENT_PROVIDER_UNAVAILABLE",
            True,
            previous_usage,
            usage_capture,
            memory_update,
            error,
        ) from error
    except MaxTurnsExceeded as error:
        code = (
            finalization_error(usage_capture.context.context)
            if usage_capture.context is not None
            and "governance" in usage_capture.context.context
            else "AGENT_BUDGET_EXCEEDED"
        )
        raise _execution_failure(
            code,
            False,
            previous_usage,
            usage_capture,
            memory_update,
            error,
        ) from error
    except (ModelBehaviorError, UnicodeEncodeError) as error:
        raise _execution_failure(
            "AGENT_MODEL_OUTPUT_INVALID",
            False,
            previous_usage,
            usage_capture,
            memory_update,
            error,
        ) from error
    if (
        usage_capture.provider == "minimax"
        and isinstance(result.final_output, str)
        and usage_capture.context is not None
        and "governance" in usage_capture.context.context
        and governance(usage_capture.context.context)["finalizing"]
    ):
        adapted = _minimax_final_reply_json(
            result.final_output, governance(usage_capture.context.context),
        )
        if adapted is not None:
            result.final_output = adapted
    usage = _add_usage(previous_usage, (
        usage_capture.delta()
        if repaired_early_model_error
        else _responses_usage(response_batch)
    ))
    return SDKExecutionResult(result, usage, memory_update)


def _needs_missing_tool_retry(result, capture: _RunUsageCapture) -> bool:
    if (
        capture.provider not in {"minimax", "deepseek"}
        or not isinstance(result.final_output, str)
        or capture.context is None
        or "governance" not in capture.context.context
    ):
        return False
    state = governance(capture.context.context)
    if state["provider_retry_stage"] is not None:
        return True
    # The SDK can terminate an entirely empty Provider response before its
    # normal end hook runs. Persist and repair that no-tool response here so it
    # receives the same single, body-aware correction as a prose-only reply.
    if not result.final_output.strip() and capture.usage_ledger is not None:
        return capture.usage_ledger.stage_protocol_retry(capture.context)
    return False


def _missing_tool_repair_input(
    result, agent: Agent[dict[str, Any]],
) -> list[TResponseInputItem]:
    """Continue once with the same tool-not-found observation as an SDK repair."""
    call_id = f"provider-protocol-repair-{uuid4()}"
    call = ResponseFunctionToolCall(
        arguments="{}",
        call_id=call_id,
        name="protocol_repair_required",
        type="function_call",
        id=call_id,
    )
    tool_choice = agent.model_settings.tool_choice
    instruction = (
        f"Call {tool_choice} with arguments matching its schema."
        if isinstance(tool_choice, str)
        and tool_choice not in {"auto", "required", "none"}
        else "Call exactly one available tool with arguments matching its schema."
    )
    return [
        *result.to_input_list(),
        call.model_dump(exclude_none=True),
        ItemHelpers.tool_call_output_item(
            call,
            f"Tool 'protocol_repair_required' not found. {instruction}",
        ),
    ]


def _early_model_error_repair_input(
    capture: _RunUsageCapture,
    agent: Agent[dict[str, Any]],
) -> list[TResponseInputItem]:
    """Build a body-free correction after SDK-level response decoding failed."""
    call_id = f"provider-protocol-repair-{uuid4()}"
    call = ResponseFunctionToolCall(
        arguments="{}",
        call_id=call_id,
        name="protocol_repair_required",
        type="function_call",
        id=call_id,
    )
    tool_choice = agent.model_settings.tool_choice
    instruction = (
        f"Call {tool_choice} with arguments matching its advertised schema."
        if isinstance(tool_choice, str)
        and tool_choice not in {"auto", "required", "none"}
        else (
            "The previous response could not be decoded as an advertised tool "
            "call. Call exactly one currently available tool and include only "
            "fields defined by that tool's schema."
        )
    )
    return [
        *capture.active_input_items,
        call.model_dump(exclude_none=True),
        ItemHelpers.tool_call_output_item(call, instruction),
    ]


def _execution_failure(
    code: str,
    upstream: bool,
    previous_usage: ExecutionUsage,
    capture: _RunUsageCapture,
    memory_update: MemoryUpdate | None,
    _error: BaseException,
) -> SDKExecutionError:
    capture.fail_context_view(code)
    capture.fail_provider_request(code)
    cause = _error.__cause__ if isinstance(_error, UserError) else _error
    return SDKExecutionError(
        code,
        upstream=upstream,
        usage=_add_usage(previous_usage, capture.delta()),
        memory_update=memory_update,
        failed_activity_id=capture.failed_activity_id(),
        diagnostics=(cause.diagnostics if isinstance(cause, (AgentRunError, UpstreamServiceError)) else None),
    )
