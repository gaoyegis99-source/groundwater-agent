from __future__ import annotations

import pytest
from pydantic import ValidationError

from platform_protocol_contracts import AgentProgressIntent, ToolOutcome


def test_agent_progress_intent_is_small_structured_and_rejects_arguments() -> None:
    intent = AgentProgressIntent.model_validate(
        {
            "capability": "data.discovery",
            "objective": "查找可用于地下水分析的数据集",
            "template_key": "data.discovery",
        }
    )

    assert intent.capability == "data.discovery"
    with pytest.raises(ValidationError):
        AgentProgressIntent.model_validate(
            {
                "capability": "data.discovery",
                "objective": "查找数据",
                "template_key": "data.discovery",
                "safe_arguments": {"query": "地下水"},
            }
        )
    with pytest.raises(ValidationError, match="PROGRESS_OBJECTIVE_INVALID"):
        AgentProgressIntent.model_validate(
            {
                "capability": "data.discovery",
                "objective": "正在查找数据。",
                "template_key": "data.discovery",
            }
        )


def test_tool_outcome_uses_declared_code_and_scalar_facts() -> None:
    outcome = ToolOutcome.model_validate(
        {"outcomeCode": "candidates.found", "facts": {"candidateCount": 3}}
    )

    assert outcome.outcome_code == "candidates.found"
    assert outcome.model_dump(by_alias=True) == {
        "outcomeCode": "candidates.found",
        "facts": {"candidateCount": 3},
    }
    with pytest.raises(ValidationError, match="PROGRESS_FACT_INVALID"):
        ToolOutcome.model_validate(
            {"outcomeCode": "candidates.found", "facts": {"label": "line 1\nline 2"}}
        )
