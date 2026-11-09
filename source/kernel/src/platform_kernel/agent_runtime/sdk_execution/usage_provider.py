"""Provider-attempt accounting used by the SDK execution hooks."""

from __future__ import annotations

import time

from agents import TResponseInputItem

class _ProtocolRepairRequiredError(Exception):
    """Carry one rejected Provider response to a safe outer retry."""

    def __init__(self, repair_input: list[TResponseInputItem], response: object) -> None:
        super().__init__("PROTOCOL_REPAIR_REQUIRED")
        self.repair_input = repair_input
        self.response = response


class _ProviderUsageCaptureMixin:
    def fail_provider_request(self, error_class: str) -> None:
        self._finish_provider_request("FAILED", error_class, self.context)

    def mark_last_protocol_failure(self) -> None:
        if self.usage_ledger is None or self.request_agent_run_id is None:
            return
        marked = self.usage_ledger.mark_protocol_failure(self.request_agent_run_id)
        if marked is not None and marked[1] == 1:
            self.retry_provider_stage = (marked[0], 2)

    def _finish_provider_request(
        self, outcome: str, error_class: str | None, context
    ) -> None:
        if self.usage_ledger is None or self.active_provider_request is None:
            return
        request_id, started, before = self.active_provider_request
        usage = getattr(context, "usage", None)
        values = (0, 0, 0) if usage is None else (
            max(0, usage.input_tokens - before.input_tokens),
            max(0, usage.output_tokens - before.output_tokens),
            max(0, usage.total_tokens - before.total_tokens),
        )
        self.usage_ledger.finish_provider_request(
            request_id,
            outcome=outcome,
            error_class=error_class,
            input_tokens=values[0],
            output_tokens=values[1],
            total_tokens=values[2],
            latency_ms=max(0, round((time.monotonic() - started) * 1000)),
        )
        self.active_provider_request = None


__all__ = ["_ProtocolRepairRequiredError", "_ProviderUsageCaptureMixin"]
