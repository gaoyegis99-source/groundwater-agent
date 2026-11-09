from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .http import CamelModel


ProgressFact = str | int | float | bool | None

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_FACT_NAME = re.compile(r"^[a-z][A-Za-z0-9]*$")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _validate_identifier(value: str) -> str:
    if _IDENTIFIER.fullmatch(value) is None:
        raise ValueError("PROGRESS_IDENTIFIER_INVALID")
    return value


def _validate_facts(value: dict[str, ProgressFact]) -> dict[str, ProgressFact]:
    if len(value) > 32:
        raise ValueError("PROGRESS_FACTS_TOO_LARGE")
    for key, item in value.items():
        if _FACT_NAME.fullmatch(key) is None:
            raise ValueError("PROGRESS_FACT_NAME_INVALID")
        if isinstance(item, str) and (
            len(item) > 512
            or any(character in item for character in "\r\n\t")
            or _CONTROL_CHARACTER.search(item)
        ):
            raise ValueError("PROGRESS_FACT_INVALID")
    return value


class AgentProgressIntent(BaseModel):
    """Provider-authored intent for one planned tool use.

    This describes what the agent is trying to accomplish. It never authorizes
    an operation and it cannot declare completion.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    capability: str = Field(min_length=1, max_length=96)
    objective: str = Field(min_length=1, max_length=240)
    template_key: str = Field(min_length=1, max_length=96)

    @field_validator("capability", "template_key")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        return _validate_identifier(value)

    @field_validator("objective")
    @classmethod
    def validate_objective(cls, value: str) -> str:
        normalized = value.strip()
        if (
            not normalized
            or normalized.startswith("正在")
            or normalized[-1] in "。！？.!?"
            or any(character in normalized for character in "\r\n\t")
            or _CONTROL_CHARACTER.search(normalized)
        ):
            raise ValueError("PROGRESS_OBJECTIVE_INVALID")
        return normalized


class ToolOutcome(CamelModel):
    """Tool-owned outcome code plus a small, declared set of display-safe facts."""

    outcome_code: str = Field(min_length=1, max_length=96)
    facts: dict[str, ProgressFact] = Field(default_factory=dict)

    @field_validator("outcome_code")
    @classmethod
    def validate_outcome_code(cls, value: str) -> str:
        return _validate_identifier(value)

    @field_validator("facts")
    @classmethod
    def validate_outcome_facts(
        cls, value: dict[str, ProgressFact]
    ) -> dict[str, ProgressFact]:
        return _validate_facts(value)


