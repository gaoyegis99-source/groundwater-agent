from __future__ import annotations

from dataclasses import dataclass

from ..settings import ExecutableModelDefinition


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


def resolve_mode_capabilities(
    request_context: dict | None,
    installed_models: tuple[ExecutableModelDefinition, ...],
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
                mode, installed_models, False, False, False, True, False
            )
        if dialogue_policy != "EXECUTION_PROPOSAL":
            raise ModeResolutionError("AGENT_REQUEST_MODE_INVALID")
        return AgentModeCapabilities(
            mode, installed_models, True, True, False, True, False
        )
    if mode == "WORKFLOW_DRAFT":
        if dialogue_policy is not None:
            raise ModeResolutionError("AGENT_REQUEST_MODE_INVALID")
        return AgentModeCapabilities(mode, (), False, False, True, True, False)
    raise ModeResolutionError("AGENT_REQUEST_MODE_INVALID")
