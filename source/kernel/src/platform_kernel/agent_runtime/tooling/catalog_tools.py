"""Read-only tools over the frozen operator catalog."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from agents import FunctionTool, RunContextWrapper, function_tool
from pydantic import ValidationError
from pydantic import StrictInt
from platform_protocol_contracts import AgentProgressIntent
from platform_workflow_compiler import (
    WorkflowValidationError, get_operator as read_operator,
    list_operators as browse_operators,
)

from ...errors import AgentRunError
from ...tool_progress import (
    builtin_progress_intent_schema,
    builtin_tool_outcome,
    control_tool_outcome,
    validate_builtin_progress_intent,
)
from ..governance import advance_from_discovery, governance, non_submit_enabled
from ..draft_authoring_contract import DraftAuthoringContractError, draft_authoring_repair_instruction
from ..evidence import TURN_EVIDENCE_TOOLS
from ..models import Utf8String, WorkflowDraftInput
from ..workflow_draft import prepare_workflow_draft


def build_catalog_tools(
    operator_catalog: dict | None, composition_operators: dict | None = None, *,
    rule_schemas: dict | None = None,
    worker_status: Callable[[str], Awaitable[str]] | None = None,
    metadata: Callable[[dict], dict] | None = None,
) -> list[Any]:
    @function_tool(failure_error_function=None)
    def list_operators(
        context: RunContextWrapper[dict[str, Any]],
        progress_intent: AgentProgressIntent,
        capability: Utf8String | None = None,
        query: Utf8String | None = None,
        domain: Utf8String | None = None,
        input_format: Utf8String | None = None,
        output_role: Utf8String | None = None,
        after: Utf8String | None = None,
        limit: StrictInt = 20,
    ) -> dict:
        """Search the frozen operator catalog by operation phrase, ID or domain.
        Filter by a known input file format or requested output port or role.
        For query use one shortest literal operation phrase, such as 稳定排序 or
        描述统计, never the full goal sentence. Inspect full details before
        composing a draft. A search may advance at most three pages and cannot
        invoke an operator.
        """
        validate_builtin_progress_intent("list_operators", progress_intent)
        if not 1 <= limit <= 50:
            raise AgentRunError("AGENT_MODEL_OUTPUT_INVALID", diagnostics={"reason": "OPERATOR_PAGE_LIMIT_INVALID"})
        if all(value is None for value in (capability, query, domain, input_format, output_role)):
            raise AgentRunError(
                "AGENT_MODEL_OUTPUT_INVALID",
                diagnostics={"reason": "OPERATOR_QUERY_REQUIRED"},
            )
        control = governance(context.context)
        search_key = json.dumps(
            {"capability": capability, "query": query, "domain": domain,
             "input_format": input_format, "output_role": output_role},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        cursors = control["catalog_queries"].get(search_key, [])
        if after in cursors or len(cursors) >= 3:
            advance_from_discovery(control, "DUPLICATE")
            return {
                "status": "PAGINATION_STOPPED",
                "next_phase": control["phase"],
                "progressOutcome": control_tool_outcome(
                    "control.pagination_stopped"
                ),
            }
        try:
            result = browse_operators(
                operator_catalog, capability=capability, query=query,
                domain=domain, after=after, limit=limit,
                input_format=input_format, output_role=output_role,
                collection_operators=composition_operators,
            )
        except WorkflowValidationError as error:
            raise AgentRunError("AGENT_MODEL_OUTPUT_INVALID", diagnostics={"reason": error.code}) from error
        if after is not None and result["next_cursor"] == after:
            advance_from_discovery(control, "DUPLICATE")
            return {
                "status": "PAGINATION_STOPPED",
                "next_phase": control["phase"],
                "progressOutcome": control_tool_outcome(
                    "control.pagination_stopped"
                ),
            }
        control["catalog_queries"].setdefault(search_key, []).append(after)
        count = len(result["items"])
        return {
            **result,
            "items": [
                {**item, "confirmation_supported": (item["operator_id"], item["operator_version"]) in composition_operators}
                for item in result["items"]
            ] if composition_operators is not None else result["items"],
            "progressOutcome": builtin_tool_outcome(
                "list_operators",
                "operators.found" if count else "operators.empty",
                {"operatorCount": count} if count else {},
            ),
        }

    @function_tool(failure_error_function=None)
    async def get_operator(
        progress_intent: AgentProgressIntent,
        operator_id: Utf8String,
        version: Utf8String,
    ) -> dict:
        """Read one frozen operator's input/output ports and parameter schema. No execution."""
        validate_builtin_progress_intent("get_operator", progress_intent)
        try:
            contract = (composition_operators or {}).get((operator_id, version))
            collections = contract is not None and any(p.get("cardinality", {}).get("maxItems", 1) > 1 for p in contract["inputs"])
            definition = read_operator(operator_catalog, operator_id, version, collections=collections)
            if rule_schemas is not None and contract is not None:
                rules = {
                    item["name"]: rule_schemas[item["rulesSchema"]]
                    for item in definition["inputs"] if "rulesSchema" in item
                }
                if rules:
                    definition["rules_schemas"] = rules
            if worker_status is not None:
                definition["worker_status"] = await worker_status(definition["runtime"])
            return {
                **definition,
                **(metadata(operator_catalog[operator_id, version]) if metadata is not None else {}),
                **({"confirmation_supported": contract is not None} if metadata is not None else {}),
                **({
                    "confirmation_supported": (operator_id, version) in composition_operators,
                    "composition_contract": contract,
                } if composition_operators is not None else {}),
                "progressOutcome": builtin_tool_outcome(
                    "get_operator", "operator.inspected", {}
                ),
            }
        except WorkflowValidationError as error:
            raise AgentRunError(
                "AGENT_MODEL_OUTPUT_INVALID",
                diagnostics={"reason": error.code},
            ) from error

    tools = [list_operators, get_operator]
    for tool in tools:
        tool.params_json_schema.pop("$defs", None)
        tool.params_json_schema["properties"]["progress_intent"] = (
            builtin_progress_intent_schema(tool.name)
        )
    return tools


def add_workflow_validation(tools, settings, request_context):
    """Use the same evidence and compiler as finalization for a read-only preview."""
    evidence_items = []

    def capture(tool):
        invoke = tool.on_invoke_tool

        async def observed(context, arguments):
            result = await invoke(context, arguments)
            call_id = context.tool_call_id
            evidence_items.extend([
                {"type": "function_call", "call_id": call_id, "name": tool.name},
                {"type": "function_call_output", "call_id": call_id, "output": result},
            ])
            return result

        tool.on_invoke_tool = observed

    for tool in tools:
        if tool.name in TURN_EVIDENCE_TOOLS:
            capture(tool)

    async def validate_workflow(context, arguments):
        try:
            draft = WorkflowDraftInput.model_validate_json(arguments)
        except ValidationError as error:
            return {
                "status": "INVALID", "code": "WORKFLOW_DRAFT_VALUE_INVALID",
                "issues": [{"path": list(issue["loc"]), "type": issue["type"]} for issue in error.errors()],
            }
        try:
            prepared = prepare_workflow_draft(
                draft, evidence_items, settings,
                request_context.get("workflow_draft_id") or context.context["agent_run_id"],
                user_goal=request_context["current_message"],
                check_authoring_contract=governance(context.context)["mode"] == "WORKFLOW_DRAFT",
            )
        except DraftAuthoringContractError as error:
            return {
                "status": "INVALID",
                **(error.diagnostics or {}),
                "issues": error.issues,
                "instruction": draft_authoring_repair_instruction(error.issues),
            }
        except AgentRunError as error:
            if str(error) != "AGENT_WORKFLOW_DRAFT_INVALID":
                raise
            return {"status": "INVALID", **(error.diagnostics or {})}
        check = prepared.get("composition_check")
        if check and (
            check["status"] == "VALIDATED"
            or governance(context.context)["mode"] != "WORKFLOW_DRAFT"
        ):
            return check

        missing = prepared["missing_inputs"]
        unresolved = draft.unresolved_items
        result = {
            "status": "INCOMPLETE" if missing or unresolved or check is None else "DRAFT_ONLY",
            "missing_inputs": missing,
            "unresolved_items": unresolved,
        }
        if check is not None:
            # A compiled draft can be saved before its execution is eligible.
            result.update(execution_allowed=False, composition_check=check)
        return result

    tools.append(FunctionTool(
        name="validate_workflow",
        description=(
            "Validate the same structured draft accepted by submit_reply against current "
            "operator and Data evidence. Returns missing inputs or diagnostics. "
            "For workflow authoring, an incomplete or draft-only graph can be saved. "
            "Its composition_check reports execution eligibility separately. "
            "No execution, data changes or confirmation are created."
        ),
        params_json_schema=WorkflowDraftInput.model_json_schema(),
        on_invoke_tool=validate_workflow,
        strict_json_schema=False,
        is_enabled=non_submit_enabled,
    ))
    return tools
