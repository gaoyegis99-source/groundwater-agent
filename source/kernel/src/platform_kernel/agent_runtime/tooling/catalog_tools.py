"""Read-only tools over the frozen operator catalog."""
from __future__ import annotations

import json
from typing import Any

from agents import RunContextWrapper, function_tool
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
from ..governance import advance_from_discovery, governance
from ..models import Utf8String


def build_catalog_tools(operator_catalog: dict | None) -> list[Any]:
    @function_tool(failure_error_function=None)
    def list_operators(
        context: RunContextWrapper[dict[str, Any]],
        progress_intent: AgentProgressIntent,
        capability: Utf8String | None = None,
        query: Utf8String | None = None,
        domain: Utf8String | None = None,
        after: Utf8String | None = None,
        limit: StrictInt = 20,
    ) -> dict:
        """Search the frozen operator catalog by capability, query, or domain.
        For query use one shortest literal operation phrase, such as 稳定排序 or
        描述统计, never the full goal sentence. Inspect full details before
        composing a draft. A search may advance at most three pages and cannot
        invoke an operator.
        """
        validate_builtin_progress_intent("list_operators", progress_intent)
        if not 1 <= limit <= 50:
            raise AgentRunError("AGENT_MODEL_OUTPUT_INVALID", diagnostics={"reason": "OPERATOR_PAGE_LIMIT_INVALID"})
        if capability is None and query is None and domain is None:
            raise AgentRunError(
                "AGENT_MODEL_OUTPUT_INVALID",
                diagnostics={"reason": "OPERATOR_QUERY_REQUIRED"},
            )
        control = governance(context.context)
        search_key = json.dumps(
            {"capability": capability, "query": query, "domain": domain},
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
            "progressOutcome": builtin_tool_outcome(
                "list_operators",
                "operators.found" if count else "operators.empty",
                {"operatorCount": count} if count else {},
            ),
        }

    @function_tool(failure_error_function=None)
    def get_operator(
        progress_intent: AgentProgressIntent,
        operator_id: Utf8String,
        version: Utf8String,
    ) -> dict:
        """Read one frozen operator's input/output ports and parameter schema. No execution."""
        validate_builtin_progress_intent("get_operator", progress_intent)
        try:
            return {
                **read_operator(operator_catalog, operator_id, version),
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
