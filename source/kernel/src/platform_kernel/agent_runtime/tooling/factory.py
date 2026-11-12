"""Compose mode-specific Agent tools; handlers own their individual policies."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ...settings import (
    ExecutableModelDefinition,
    ObservationToolDefinition,
    ServiceBinding,
)
from ..mode import AgentModeCapabilities, resolve_mode_capabilities
from ..model_tool import build_model_tool
from ..observation_client import ObservationClient
from ..preparation_tool import build_preparation_tool
from .catalog_tools import build_catalog_tools
from .observations import _observation_tool
from .read_governance import _prepare_tools
from .reply_submission import ReplySubmission
from .task_tools import build_plan_tool, build_task_tools


def build_agent_tools(
    database: Path,
    services: tuple[ServiceBinding, ...],
    observations: tuple[ObservationToolDefinition, ...],
    models: tuple[ExecutableModelDefinition, ...],
    observation_client: ObservationClient,
    *, request_context: dict | None = None,
    operator_catalog: dict | None = None,
    operator_catalog_digest: str | None = None,
    capabilities: AgentModeCapabilities | None = None,
    provider: str = "deepseek",
    include_execution_facts: bool = True,
) -> list[Any]:
    all_installed_models = models
    capabilities = capabilities or resolve_mode_capabilities(request_context, models)
    models = capabilities.models
    converge_read_only_evidence = (
        capabilities.mode == "DIALOGUE" and capabilities.read_only
    )
    active_observations = tuple(
        item for item in observations
        if not (
            provider == "minimax"
            and converge_read_only_evidence
            and item.name == "lookup_knowledge"
        )
    )
    all_tool_names = {
        "record_plan",
        "submit_reply",
        *(item.name for item in active_observations),
        *(item.tool_name for item in models),
    }

    services_by_name = {item.name: item for item in services}
    tools: list[Any] = (
        [build_plan_tool(database, all_tool_names, provider=provider)]
        if capabilities.allow_plan else []
    )
    explicit_model_tool_request = (
        None if request_context is None else request_context.get("explicit_model_tool_request")
    )
    if capabilities.allow_model_tools:
        tools.append(build_preparation_tool(
            database,
            observations=observations,
            services_by_name=services_by_name,
            models=models,
            observation_client=observation_client,
            provider=provider,
            explicit_model_tool_request=explicit_model_tool_request,
        ))
    tools.append(ReplySubmission(
        database,
        request_context,
        provider,
        operator_catalog=operator_catalog,
        operator_catalog_digest=operator_catalog_digest,
    ).tool(capabilities))
    if not capabilities.clarification_only:
        tools.extend(build_task_tools(
            database,
            tuple(models) if models else all_installed_models,
            provider=provider,
            include_execution_facts=include_execution_facts,
        ))
    if capabilities.allow_observations:
        tools.extend(
            _observation_tool(
                item, services_by_name[item.service], observation_client,
                provider=provider,
            )
            for item in active_observations
        )
    if capabilities.allow_operator_catalog:
        tools.extend(build_catalog_tools(operator_catalog))
    if capabilities.allow_model_tools:
        tools.extend(
            build_model_tool(
                database, item, provider=provider,
                explicit_model_tool_request=explicit_model_tool_request,
            ) for item in models
        )
    return _prepare_tools(
        tools,
        database=database,
        provider=provider,
        budget_names={
            item.name for item in active_observations
            if "budget" in item.input_schema.get("properties", {})
        },
        converge_evidence=converge_read_only_evidence,
        finalize_after_evidence=converge_read_only_evidence,
        preparation_requires_knowledge=bool(
            request_context
            and request_context.get("requires_project_evidence")
        ),
        read_names={
            *(
                item.name for item in active_observations
                if capabilities.allow_observations
            ),
            *({"list_operators", "get_operator"} if capabilities.allow_operator_catalog else set()),
            *(
                {"list_execution_facts", "get_execution_fact"}
                if include_execution_facts else set()
            ),
            *({"prepare_task_inputs"} if capabilities.allow_model_tools else set()),
        },
    )
