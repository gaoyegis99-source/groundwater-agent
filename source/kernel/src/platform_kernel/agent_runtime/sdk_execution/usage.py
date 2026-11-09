"""Token-usage arithmetic shared across SDK execution stages."""

from __future__ import annotations

from typing import Any


from .types import ExecutionUsage


def _responses_usage(responses: list[Any]) -> ExecutionUsage:
    return ExecutionUsage(
        sum(item.usage.input_tokens for item in responses),
        sum(item.usage.output_tokens for item in responses),
        sum(item.usage.total_tokens for item in responses),
    )


def _add_usage(left: ExecutionUsage, right: ExecutionUsage) -> ExecutionUsage:
    return ExecutionUsage(
        left.input_tokens + right.input_tokens,
        left.output_tokens + right.output_tokens,
        left.total_tokens + right.total_tokens,
    )


def _usage_data(usage: ExecutionUsage) -> dict[str, int]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
    }

_ZERO_USAGE = ExecutionUsage(0, 0, 0)
