"""Agent, RunConfig, and execution-context construction."""

from __future__ import annotations

from dataclasses import replace
import json
import logging
from typing import Any

from agents import (
    Agent,
    RunConfig,
    ToolExecutionConfig,
    ToolsToFinalOutputResult,
    gen_trace_id,
)

from ...errors import AgentRunError
from ...settings import KernelSettings
from ..context_policy import (
    canonical_json,
)
from ..memory import (
    ExecutionContextV1,
)
from ..mode import ModeResolutionError, allows_task_update, resolve_mode_capabilities
from ..models import SubmittedReplyV6
from ..prompts import build_draft_instructions, build_instructions
from ..tooling import build_agent_tools
from .activity import (
    _ZERO_USAGE,
)
from .types import (
    SDKExecutionDependencies,
    SDKExecutionError,
)

_LOGGER = logging.getLogger(__name__)


def _build_agent(
    dependencies: SDKExecutionDependencies,
    execution_context: ExecutionContextV1 | dict[str, object],
    request_context: dict | None = None,
) -> Agent[dict[str, Any]]:
    try:
        capabilities = resolve_mode_capabilities(
            request_context, dependencies.settings.models,
            composition_available=dependencies.settings.workflow_composition is not None,
            catalog_available=bool(dependencies.settings.operator_uses),
        )
    except ModeResolutionError as error:
        raise SDKExecutionError(str(error), upstream=False, usage=_ZERO_USAGE) from error
    mode = capabilities.mode
    models = capabilities.models
    task_updates_allowed = allows_task_update(capabilities, request_context)
    observations = dependencies.settings.observation_tools
    composition_settings = (
        dependencies.settings
        if capabilities.allow_operator_catalog and dependencies.settings.workflow_composition is not None
        and (mode == "WORKFLOW_DRAFT" or capabilities.allow_model_tools)
        else None
    )
    mode_instructions = "\nsubmit_reply must include workflow_draft=null."
    if capabilities.clarification_only:
        observations = ()
        mode_instructions = """
This request is clarification-only. Do not answer the subject, provide facts, recommend
a method, draft a workflow, inspect data, propose execution, or report execution status.
Call submit_reply alone with conclusion=null, details=[], referenced_workflow_runs=[],
include_execution_status=false and workflow_draft=null. Supply exactly one concise
natural-language question in unresolved_items, with no citation, ending in ? or ？,
plus 2–3 mutually exclusive natural-language interaction_options.
"""
        options = request_context.get("business_capability_options") if request_context else None
        if options:
            mode_instructions += (
                "Several governed business options match this calculation. Ask which outcome "
                "to prepare first. Offer only the supplied capability titles and distinguish "
                "options under the same title by the exact profile reference. For each option's "
                "message, restate the user's calculation request with an explicit 'use profile' "
                "choice for that option so the next turn has a clear goal.\n"
                + json.dumps(options, ensure_ascii=False)
            )
    elif composition_settings is not None:
        mode_instructions = (
            "\nCall validate_workflow with the structured draft before submitting it. "
            "Correct the reported graph, binding or parameter issue before submitting."
        )
    elif mode == "WORKFLOW_DRAFT":
        mode_instructions = ""
    execution_context_json = (
        execution_context.canonical_json()
        if isinstance(execution_context, ExecutionContextV1)
        else canonical_json(execution_context)
    )
    include_execution_facts = (
        bool(execution_context.actions)
        if isinstance(execution_context, ExecutionContextV1)
        else "retrievable" in execution_context
    )
    if mode == "WORKFLOW_DRAFT":
        instructions = build_draft_instructions(
            dependencies.settings.system_prompt, execution_context_json,
        )
    else:
        instructions = build_instructions(
            dependencies.settings.system_prompt,
            observations,
            models,
            execution_context_json,
            execution_allowed=capabilities.allow_model_tools,
            operator_proposal=composition_settings is not None,
            operator_discovery=capabilities.allow_operator_catalog,
            task_updates_allowed=task_updates_allowed,
        )
    selection = request_context.get("workflow_selection") if request_context else None
    if selection is not None and not request_context.get("finalization"):
        business = selection.get("business_capability")
        if business is not None:
            mode_instructions += (
                "\nA Kernel-verified business capability applies to this Turn. Use its title, input needs, "
                "expected result and material limits. Its version pins are in the authorized trace. "
                "For an authorized execution proposal, call prepare_task_inputs for the only selected model "
                "before recording a plan. It uses authoritative Data and pinned user/default values, "
                "so start with empty arguments. On BLOCKED, explain the first actionable issue and ask for "
                "the missing choice or attachment. On READY, use exactly the returned arguments and state "
                "the sources for Data facts, user choices, Profile values and Model defaults. Describe the plan before "
                "confirmation. Do not invent thresholds, defaults, previous results or acceptance.\n"
                + json.dumps({
                    "title": business["title"], "description": business["description"],
                    "limitations": business["limitations"], "expected_outputs": business["expectedOutputs"],
                    "tool": models[0].tool_name,
                    "inputs": selection["inputs"],
                }, ensure_ascii=False)
            )
        else:
            mode_instructions += (
                "\nThis turn selects one installed preset. Prepare only its model tool. "
                "Preserve the bound parameter values and exact input identities below. "
                "Find or ask for missing inputs before proposing and do not fabricate a value.\n"
                + json.dumps({"tool": models[0].tool_name, "inputs": selection["inputs"],
                              "parameters": selection["parameters"]}, ensure_ascii=False)
            )
    task = None if request_context is None else request_context.get("task_context")
    if task is not None:
        mode_instructions += "\nTaskContextV1 is projected here as TaskActiveContextV1 and preserves current user requirements and incomplete work. Interpretations/suggestions are non-authoritative, never execution approval. Recheck evidence.\n" + json.dumps(task, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    preparation = (
        None if request_context is None else request_context.get("preparation_context")
    )
    if preparation is not None:
        mode_instructions += (
            "\nTaskPreparationV1 is a compact Kernel preparation projection. It helps "
            "locate prior selections but fresh_for_current_turn=false means its evidence "
            "must be re-read before a new proposal. READY means contract readiness, not "
            "scientific adequacy or execution approval.\n"
            + json.dumps(
                preparation, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            )
        )
    input_references = [] if request_context is None else request_context.get("input_references", [])
    if input_references:
        mode_instructions += (
            "\nThe following KernelInputReferences are authoritative structured user inputs. "
            "Use these exact identities; do not derive object metadata using the message text.\n"
            + json.dumps(input_references, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    if mode != "WORKFLOW_DRAFT" and task_updates_allowed:
        mode_instructions += (
            "\nFor an explicit deliverable, use update_task to preserve user goals, "
            "requirements, steps and evidence across Turns. Casual questions need no task. "
            "Keep the original goal; unrelated goals need another session. Stop at missing "
            "inputs, budget, error or confirmation. Bind real evidence; use USER_REVIEW "
            "for business adequacy.\n"
        )
        if composition_settings is not None and selection is None:
            mode_instructions += (
                "For an operator execution proposal, the Kernel tracks the work "
                "through the draft, confirmation and run. Call update_task only "
                "if the user also asks to manage Task metadata.\n"
            )
    model_settings = dependencies.model_settings
    if capabilities.clarification_only:
        model_settings = replace(
            model_settings,
            tool_choice="submit_reply",
            parallel_tool_calls=False,
        )
    return Agent(
        name=f"{dependencies.settings.product_id} orchestration agent",
        instructions=instructions + mode_instructions,
        model=dependencies.model,
        model_settings=model_settings,
        tools=build_agent_tools(
            dependencies.database,
            dependencies.settings.services,
            observations,
            dependencies.settings.models,
            dependencies.observation_client,
            request_context=request_context,
            operator_catalog=dependencies.settings.operator_catalog,
            composition_settings=composition_settings,
            catalog_settings=dependencies.settings,
            worker_status=dependencies.operator_worker_status,
            composition_operators=(
                None if dependencies.settings.workflow_composition is None
                else dependencies.settings.workflow_composition.manifests
            ),
            operator_catalog_digest=(
                dependencies.settings.operator_catalog_digest
            ),
            capabilities=capabilities,
            provider=dependencies.settings.agent_provider,
            include_execution_facts=include_execution_facts,
        ),
        tool_use_behavior=_finish_when_submitted,
        reset_tool_choice=False,
    )


def _finish_when_submitted(context, results):
    for result in results:
        if result.tool.name != "submit_reply":
            continue
        if isinstance(result.output, SubmittedReplyV6):
            return ToolsToFinalOutputResult(True, result.output.model_dump_json())
        payload = result.output
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = None
        if (
            isinstance(payload, dict)
            and payload.get("status") == "RETRY_REQUIRED"
        ):
            continue
        if isinstance(payload, dict) and payload.get("status") == "RETRY_EXHAUSTED":
            return ToolsToFinalOutputResult(
                True,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            )
        raise AgentRunError("AGENT_MODEL_OUTPUT_INVALID")
    return ToolsToFinalOutputResult(False)


def _build_run_config(
    settings: KernelSettings,
    *,
    session_id: str,
    project_id: str,
    agent_run_id: str,
    trace_id: str,
    goal_revision: int,
) -> RunConfig:
    return RunConfig(
        workflow_name="Platform Kernel Agent",
        trace_id=trace_id,
        group_id=session_id,
        trace_metadata={
            "agent_run_id": agent_run_id,
            "session_id": session_id,
            "project_id": project_id,
            "goal_revision": goal_revision,
            "product_id": settings.product_id,
        },
        # Observation payloads must reach the in-process trace processor so it can
        # retain only the contractually allowed status/count/checksum envelope.
        trace_include_sensitive_data=False,
        tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1),
        # The usage hook admits at most one persisted protocol repair before
        # the SDK returns an unknown-tool error to the model. No alias executes.
        tool_not_found_behavior="return_error_to_model",
    )


def _build_run_context(
    *, project_id: str, agent_run_id: str, goal_revision: int, trace_id: str,
    governance_state: dict[str, Any],
) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "agent_run_id": agent_run_id,
        "goal_revision": goal_revision,
        "trace_id": trace_id,
        "governance": governance_state,
    }


def _capabilities(dependencies: SDKExecutionDependencies, request_context: dict | None):
    try:
        return resolve_mode_capabilities(
            request_context, dependencies.settings.models,
            composition_available=dependencies.settings.workflow_composition is not None,
        )
    except ModeResolutionError as error:
        raise SDKExecutionError(str(error), upstream=False, usage=_ZERO_USAGE) from error


def new_trace_id() -> str:
    return gen_trace_id()
