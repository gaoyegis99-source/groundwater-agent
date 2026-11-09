"""Map one completed provider result into the durable intent contract."""

from __future__ import annotations

from .decision import _authorize_model_decision, _semantic_literal_decision
from .models import IntentDecisionV4


def map_provider_result(
    result: object,
    *,
    uses_sdk_structured_output: bool,
    task_context: dict | None,
    candidate_context: dict | None,
) -> IntentDecisionV4:
    """Validate provider syntax, then apply Kernel authorization constraints."""

    decision = (
        result.final_output_as(IntentDecisionV4, raise_if_incorrect_type=True)
        if uses_sdk_structured_output
        else _semantic_literal_decision(result.final_output)
    )
    return _authorize_model_decision(decision, task_context, candidate_context)


__all__ = ("map_provider_result",)
