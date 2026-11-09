"""One persisted protocol repair per Provider stage, without replaying tools."""

from __future__ import annotations

from pathlib import Path

from .governance import governance
from .persistence import get_run, mark_provider_protocol_failure


def stage_protocol_retry(database: Path, context) -> bool:
    """Bind one protocol repair call to the same persisted Provider stage."""
    if context is None:
        return False
    state = governance(context.context)
    marked = mark_provider_protocol_failure(
        database, context.context["agent_run_id"]
    )
    if marked is None:
        return False
    stage, attempt = marked
    if attempt == 2:
        return False
    if stage in state["schema_retry_stages"]:
        return False
    state["schema_retry_stages"].append(stage)
    state["provider_retry_stage"] = [stage, 2]
    return True


def single_planned_approval_tool(
    database: Path, agent_run_id: str, enabled_tools,
) -> str | None:
    """Return one persisted planned model tool when it is currently enabled."""
    run = get_run(database, agent_run_id)
    if not run.plans:
        return None
    planned = {step.tool for step in run.plans[-1].steps}
    candidates = [
        tool.name for tool in enabled_tools
        if tool.name in planned and tool.needs_approval is True
    ]
    return candidates[0] if len(candidates) == 1 else None
