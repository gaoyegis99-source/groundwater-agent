from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator


ResponseKind: TypeAlias = Literal[
    "DIALOGUE", "WORKFLOW_DRAFT", "CONTROL", "NEEDS_CLARIFICATION",
]
# Compatibility name for internal imports; V4 calls this dimension responseKind.
IntentDecision = ResponseKind
SemanticIntent: TypeAlias = Literal[
    "CONSULT", "COMPARE", "DESIGN", "EXECUTE_ON_DATA", "CREATE_WORKFLOW",
    "REVIEW_RESULT", "STATUS_QUERY", "UNSPECIFIED", "UNKNOWN",
]
TaskAction: TypeAlias = Literal["NONE", "CREATE", "REVISE", "CONTINUE", "REVIEW"]
DialoguePolicy: TypeAlias = Literal["READ_ONLY", "EXECUTION_PROPOSAL"]
ControlIntent: TypeAlias = Literal[
    "APPROVE_CONFIRMATION", "REJECT_CONFIRMATION", "STOP_ANALYSIS",
    "CANCEL_EXECUTION",
]
ClarificationReason: TypeAlias = Literal[
    "REQUEST_KIND_AMBIGUOUS", "TASK_CONFLICT", "CONTROL_TARGET_MISSING",
    "CONTROL_TARGET_AMBIGUOUS",
]

IntentRoutingFailureCode: TypeAlias = Literal[
    "AGENT_PROVIDER_UNAVAILABLE",
    "AGENT_DEADLINE_EXCEEDED",
    "AGENT_CONTEXT_TOO_LARGE",
    "AGENT_INTENT_ROUTING_INVALID",
    "AGENT_SDK_CONFIGURATION_INVALID",
]

INTENT_ROUTING_FAILURE_CODES = frozenset({
    "AGENT_PROVIDER_UNAVAILABLE",
    "AGENT_DEADLINE_EXCEEDED",
    "AGENT_CONTEXT_TOO_LARGE",
    "AGENT_INTENT_ROUTING_INVALID",
    "AGENT_SDK_CONFIGURATION_INVALID",
})


class IntentDecisionV4(BaseModel):
    """Semantic classification only; Kernel still owns targets and authorization."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        populate_by_name=False,
        serialize_by_alias=True,
    )

    schema_version: Literal["intent-decision.v4"] = Field(alias="schemaVersion")
    response_kind: ResponseKind = Field(alias="responseKind")
    semantic_intent: SemanticIntent = Field(alias="semanticIntent")
    task_action: TaskAction = Field(alias="taskAction")
    dialogue_policy: DialoguePolicy | None = Field(alias="dialoguePolicy")
    control_intent: ControlIntent | None = Field(alias="controlIntent")
    clarification_reason: ClarificationReason | None = Field(alias="clarificationReason")

    @classmethod
    def from_python(
        cls,
        *,
        response_kind: ResponseKind,
        semantic_intent: SemanticIntent,
        task_action: TaskAction,
        dialogue_policy: DialoguePolicy | None,
        control_intent: ControlIntent | None,
        clarification_reason: ClarificationReason | None,
    ) -> Self:
        """Build from Python names without making snake_case valid provider JSON."""
        return cls.model_validate({
            "schemaVersion": "intent-decision.v4",
            "responseKind": response_kind,
            "semanticIntent": semantic_intent,
            "taskAction": task_action,
            "dialoguePolicy": dialogue_policy,
            "controlIntent": control_intent,
            "clarificationReason": clarification_reason,
        })

    @model_validator(mode="after")
    def require_consistent_shape(self):
        if (self.response_kind == "DIALOGUE") != (self.dialogue_policy is not None):
            raise ValueError("INTENT_DIALOGUE_POLICY_INVALID")
        if (self.response_kind == "CONTROL") != (self.control_intent is not None):
            raise ValueError("INTENT_CONTROL_POLICY_INVALID")
        if (self.response_kind == "NEEDS_CLARIFICATION") != (
            self.clarification_reason is not None
        ):
            raise ValueError("INTENT_CLARIFICATION_REASON_INVALID")
        if self.semantic_intent == "EXECUTE_ON_DATA" and (
            self.response_kind != "DIALOGUE"
            or self.dialogue_policy != "EXECUTION_PROPOSAL"
        ):
            raise ValueError("INTENT_EXECUTION_POLICY_INVALID")
        if self.response_kind == "WORKFLOW_DRAFT" and (
            self.semantic_intent != "CREATE_WORKFLOW"
        ):
            raise ValueError("INTENT_WORKFLOW_SEMANTIC_INVALID")
        if self.semantic_intent == "CREATE_WORKFLOW" and (
            self.response_kind != "WORKFLOW_DRAFT"
        ):
            raise ValueError("INTENT_WORKFLOW_SEMANTIC_INVALID")
        if self.response_kind == "CONTROL" and (
            self.semantic_intent != "UNSPECIFIED" or self.task_action != "NONE"
        ):
            raise ValueError("INTENT_CONTROL_SHAPE_INVALID")
        return self


@dataclass(frozen=True)
class IntentRoutingUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class IntentRoutingResult:
    intent: IntentDecisionV4
    provider: str
    model: str
    trace_id: str
    provider_request_count: int
    usage: IntentRoutingUsage
    started_at: str
    finished_at: str
    duration_ms: int

    @property
    def response_kind(self) -> ResponseKind:
        return self.intent.response_kind

    @property
    def decision(self) -> ResponseKind:
        return self.intent.response_kind

    @property
    def dialogue_policy(self) -> DialoguePolicy | None:
        return self.intent.dialogue_policy
