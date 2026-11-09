"""Body-aware construction of one safe MiniMax protocol-repair input."""

from __future__ import annotations

import json
from typing import Any

from agents import Agent, ItemHelpers, TResponseInputItem

from ...errors import AgentRunError


def rejected_call_repair_input(
    *,
    active_input_items: list[TResponseInputItem],
    response: object,
    calls: list[object],
    agent: Agent[dict[str, Any]],
    reason: str,
    detail: str,
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


__all__ = ("rejected_call_repair_input",)
