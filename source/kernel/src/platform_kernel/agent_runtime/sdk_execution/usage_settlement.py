"""Durable, body-free settlement for a completed SDK model response."""

from __future__ import annotations

from dataclasses import dataclass

from .activity_projection import _activity_status
from .usage_ports import ActivityRecorder, UsageLedgerPort


@dataclass(frozen=True)
class OutputSettlement:
    active_context_view_id: None
    model_activity: dict | None


def settle_output(
    *,
    usage_ledger: UsageLedgerPort | None,
    active_context_view_id: str | None,
    context: object,
    context_input_start: int,
    status: str,
    error_code: str | None,
    purpose: str,
    model_activity: dict | None,
    activity_recorder: ActivityRecorder,
) -> OutputSettlement:
    """Finish context and activity projections without exposing response bodies."""

    if usage_ledger is not None:
        usage = context.usage
        usage_ledger.finish_context_view(
            active_context_view_id,
            status=status,
            actual_input_tokens=max(0, usage.input_tokens - context_input_start),
            error_code=error_code,
        )
    if purpose != "COMPACTION":
        assert model_activity is not None
        _activity_status(activity_recorder.record, model_activity, status)
        model_activity = None
    return OutputSettlement(
        active_context_view_id=None,
        model_activity=model_activity,
    )


__all__ = ("OutputSettlement", "settle_output")
