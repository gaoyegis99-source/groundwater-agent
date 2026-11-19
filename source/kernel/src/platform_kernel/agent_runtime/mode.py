from __future__ import annotations

from dataclasses import dataclass

from ..errors import ConfirmationConflictError
from ..settings import ExecutableModelDefinition
from ..workflow_selection import selected_model


@dataclass(frozen=True)
class AgentModeCapabilities:
    mode: str
    models: tuple[ExecutableModelDefinition, ...]
    allow_plan: bool
    allow_model_tools: bool
    allow_operator_catalog: bool
    allow_observations: bool
    clarification_only: bool

    @property
    def read_only(self) -> bool:
        return not self.allow_plan and not self.allow_model_tools

class ModeResolutionError(ValueError):
    pass


def allows_operator_proposal(context: dict | None) -> bool:
    return bool(
        context
        and context.get("mode") == "DIALOGUE"
        and context.get("dialogue_policy") == "EXECUTION_PROPOSAL"
        and not context.get("clarification_only")
        and context.get("workflow_selection") is None
        and not context.get("explicit_model_tool_request")
    )


def resolve_mode_capabilities(
    request_context: dict | None,
    installed_models: tuple[ExecutableModelDefinition, ...],
    *,
    composition_available: bool = False,
    catalog_available: bool = False,
) -> AgentModeCapabilities:
    mode = "DIALOGUE" if request_context is None else request_context["mode"]
    clarification_only = (
        False if request_context is None
        else request_context.get("clarification_only", False)
    )
    dialogue_policy = (
        "READ_ONLY" if request_context is None
        else request_context.get("dialogue_policy")
    )
    if type(clarification_only) is not bool or (
        clarification_only and mode != "DIALOGUE"
    ):
        raise ModeResolutionError("AGENT_REQUEST_MODE_INVALID")
    if mode == "DIALOGUE":
        if clarification_only:
            if dialogue_policy is not None:
                raise ModeResolutionError("AGENT_REQUEST_MODE_INVALID")
            return AgentModeCapabilities(
                mode, (), False, False, False, False, True
            )
        if dialogue_policy == "READ_ONLY":
            return AgentModeCapabilities(
                mode, installed_models, False, False, catalog_available or composition_available, True, False
            )
        if dialogue_policy != "EXECUTION_PROPOSAL":
            raise ModeResolutionError("AGENT_REQUEST_MODE_INVALID")
        selection = request_context.get("workflow_selection") if request_context else None
        if selection is not None:
            try:
                installed_models = (selected_model(selection, installed_models),)
            except ConfirmationConflictError as error:
                raise ModeResolutionError(str(error)) from error
        return AgentModeCapabilities(
            mode, installed_models, True, True,
            composition_available and allows_operator_proposal(request_context), True, False,
        )
    if mode == "WORKFLOW_DRAFT":
        if dialogue_policy is not None:
            raise ModeResolutionError("AGENT_REQUEST_MODE_INVALID")
        return AgentModeCapabilities(mode, (), False, False, True, True, False)
    raise ModeResolutionError("AGENT_REQUEST_MODE_INVALID")
