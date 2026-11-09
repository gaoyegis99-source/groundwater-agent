"""One bounded, effect-free Provider call for intent routing."""

from __future__ import annotations

from dataclasses import dataclass

from agents import RunConfig, Runner

from ..agent_runtime.sdk_execution.activity import _responses_usage


@dataclass(frozen=True)
class ProviderRouteCall:
    """The raw response and usage reported by one Provider response."""

    result: object
    usage: object


async def run_provider_call(
    *,
    agent: object,
    input_items: list[object],
    capture: object,
    trace_id: str,
) -> ProviderRouteCall:
    """Run exactly one protocol attempt; retry policy stays with ``IntentRouter``."""

    result = await Runner.run(
        agent,
        input_items,
        max_turns=1,
        hooks=capture,
        run_config=RunConfig(
            workflow_name="Intent Router",
            tracing_disabled=True,
            trace_include_sensitive_data=False,
            trace_id=trace_id,
        ),
    )
    return ProviderRouteCall(
        result=result,
        usage=_responses_usage(result.raw_responses),
    )


__all__ = ("ProviderRouteCall", "run_provider_call")
