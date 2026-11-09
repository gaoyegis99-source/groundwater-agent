"""Pure parsing and authorization of model-proposed intent decisions."""

from pydantic import ValidationError

from .models import IntentDecisionV4


def _semantic_literal_decision(value: object) -> IntentDecisionV4:
    if not isinstance(value, str):
        raise TypeError("AGENT_INTENT_ROUTING_INVALID")
    literal = value.strip(" \t\r\n")
    fields = literal.split("|")
    if len(fields) != 3:
        raise ValueError("AGENT_INTENT_ROUTING_INVALID")
    semantic, task_action, clarification = fields
    if clarification == "NONE":
        clarification_reason = None
        if semantic == "CREATE_WORKFLOW":
            response_kind = "WORKFLOW_DRAFT"
            dialogue_policy = None
        else:
            response_kind = "DIALOGUE"
            dialogue_policy = (
                "EXECUTION_PROPOSAL" if semantic == "EXECUTE_ON_DATA" else "READ_ONLY"
            )
    else:
        response_kind = "NEEDS_CLARIFICATION"
        dialogue_policy = None
        clarification_reason = clarification
    try:
        return IntentDecisionV4.from_python(
            response_kind=response_kind,
            semantic_intent=semantic,
            task_action=task_action,
            dialogue_policy=dialogue_policy,
            control_intent=None,
            clarification_reason=clarification_reason,
        )
    except ValidationError as error:
        raise ValueError("AGENT_INTENT_ROUTING_INVALID") from error


def _authorize_model_decision(
    value: IntentDecisionV4,
    task_context: dict | None,
    candidate_context: dict | None,
) -> IntentDecisionV4:
    """Derive runtime policy using semantics; never trust model-granted authority."""
    if value.response_kind == "CONTROL" or value.control_intent is not None:
        raise ValueError("AGENT_INTENT_ROUTING_INVALID")
    has_task = task_context is not None
    default_actions = (
        {"NONE", "REVISE", "CONTINUE", "REVIEW"} if has_task else {"NONE", "CREATE"}
    )
    allowed_actions = default_actions
    if candidate_context is not None:
        candidates = candidate_context.get("allowed_task_actions")
        if isinstance(candidates, list) and all(
            isinstance(item, str) for item in candidates
        ):
            allowed_actions = set(candidates)
    if value.task_action not in allowed_actions:
        return IntentDecisionV4.from_python(
            response_kind="NEEDS_CLARIFICATION",
            semantic_intent=value.semantic_intent,
            task_action=value.task_action,
            dialogue_policy=None,
            control_intent=None,
            clarification_reason="TASK_CONFLICT",
        )
    if value.response_kind == "NEEDS_CLARIFICATION":
        return value.model_copy(
            update={"dialogue_policy": None, "control_intent": None}
        )
    if value.semantic_intent == "CREATE_WORKFLOW":
        return value.model_copy(
            update={
                "response_kind": "WORKFLOW_DRAFT",
                "dialogue_policy": None,
                "control_intent": None,
                "clarification_reason": None,
            }
        )
    return value.model_copy(
        update={
            "response_kind": "DIALOGUE",
            "dialogue_policy": (
                "EXECUTION_PROPOSAL"
                if value.semantic_intent == "EXECUTE_ON_DATA"
                else "READ_ONLY"
            ),
            "control_intent": None,
            "clarification_reason": None,
        }
    )
