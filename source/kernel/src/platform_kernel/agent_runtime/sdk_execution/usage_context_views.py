"""Context-view persistence for SDK execution usage capture."""

from __future__ import annotations

from .context_envelope import (
    _envelope_blocks,
    _envelope_manifest,
    _state_envelope_block,
)


class _ContextViewCaptureMixin:
    def set_initial_context_manifest(self, value: dict[str, object]) -> None:
        if self.provider_request_count or self.initial_context_manifest is not None:
            raise ValueError("CONTEXT_MANIFEST_ALREADY_SET")
        self.initial_context_manifest = value

    def fail_context_view(self, error_code: str) -> None:
        if self.usage_ledger is None or self.active_context_view_id is None:
            return
        actual = None
        if self.context is not None:
            actual = max(
                0, self.context.usage.input_tokens - self.context_input_start
            )
        self.usage_ledger.finish_context_view(
            self.active_context_view_id,
            status="FAILED",
            actual_input_tokens=actual,
            error_code=error_code,
        )
        self.active_context_view_id = None

    def reject_context_view(
        self, manifest: dict[str, object], *, error_code: str,
    ) -> None:
        """Persist a compiler rejection before the SDK starts a request."""
        if self.usage_ledger is None or self.request_agent_run_id is None:
            return
        self.usage_ledger.create_context_view(
            request_agent_run_id=self.request_agent_run_id,
            segment_id=self.segment["segment_id"],
            request_ordinal=self.provider_request_count + 1,
            purpose=self.context_purpose,
            manifest=manifest,
            status="REJECTED",
            error_code=error_code,
        )

    def _begin_context_view(
        self, agent, system_prompt, input_items, usage: dict,
        *, status: str, error_code: str | None = None,
    ) -> None:
        if self.usage_ledger is None or self.request_agent_run_id is None:
            return
        manifest = (
            self.initial_context_manifest
            if self.provider_request_count == 0
            else None
        )
        if manifest is None:
            manifest = _envelope_manifest(
                purpose=self.context_purpose,
                policy_version=self.policy_version,
                available=self.available_input_tokens,
                estimated=usage["total_tokens"],
                system_prompt=system_prompt,
                input_items=input_items,
                agent=agent,
            )
        else:
            state_blocks = (
                [_state_envelope_block(input_items)]
                if self.context_purpose == "AGENT_RESUME" else []
            )
            manifest = {
                **manifest,
                "available_input_tokens": self.available_input_tokens,
                "estimated_input_tokens": usage["total_tokens"],
                "blocks": [
                    *manifest["blocks"],
                    *state_blocks,
                    *_envelope_blocks(agent, system_prompt, usage),
                ],
            }
        context_view_id = self.usage_ledger.create_context_view(
            request_agent_run_id=self.request_agent_run_id,
            segment_id=self.segment["segment_id"],
            request_ordinal=self.provider_request_count + 1,
            purpose=self.context_purpose,
            manifest=manifest,
            status=status,
            error_code=error_code,
        )
        self.active_context_view_id = (
            None if status == "REJECTED" else context_view_id
        )


__all__ = ["_ContextViewCaptureMixin"]
