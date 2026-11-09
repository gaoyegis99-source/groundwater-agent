"""Output-shape validation for SDK usage capture.

This module intentionally has no persistence dependency.  It validates the
response contract and leaves durable activity and ledger settlement to the
capture and settlement modules.
"""

from __future__ import annotations

from agents import ModelBehaviorError

from .activity_projection import _minimax_final_text_json


def response_function_calls(response: object) -> list[object]:
    """Return function-call output items in their original response order."""

    return [item for item in response.output if item.type == "function_call"]


def has_nonempty_output_text(response: object) -> bool:
    """Whether the response contains meaningful assistant text."""

    return any(
        item.type == "message"
        and any(
            part.type == "output_text"
            and isinstance(part.text, str)
            and bool(part.text.strip())
            for part in item.content
        )
        for item in response.output
    )


def allows_minimax_final_text(response: object, control: dict[str, object]) -> bool:
    """Whether a governed MiniMax final prose reply is an allowed terminal output."""

    return _minimax_final_text_json(response, control) is not None


def validate_terminal_output(
    *,
    agent_name: str,
    require_tool_call: bool,
    calls: list[object],
    repairing_missing_tool: bool,
    allow_minimax_final_text: bool,
    response: object,
) -> None:
    """Enforce the provider-independent terminal output contract."""

    if agent_name != "Memory Compactor" and require_tool_call:
        if (
            not calls
            and not repairing_missing_tool
            and not allow_minimax_final_text
        ) or (
            any(item.name == "submit_reply" for item in calls)
            and len(calls) != 1
        ):
            raise ModelBehaviorError(
                "Agent response must call a tool; submit_reply must stand alone"
            )
    elif not has_nonempty_output_text(response):
        raise ModelBehaviorError("Memory compactor returned no final text")


__all__ = (
    "allows_minimax_final_text",
    "has_nonempty_output_text",
    "response_function_calls",
    "validate_terminal_output",
)
