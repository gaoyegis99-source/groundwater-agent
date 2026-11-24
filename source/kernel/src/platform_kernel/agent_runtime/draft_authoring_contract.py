"""Check the requested deliverables for an observed sort-to-statistics draft."""

from __future__ import annotations

import re
from collections.abc import Set

from ..errors import AgentRunError
from .models import WorkflowDraftInput, WorkflowInputBindingDraft, WorkflowNodeBindingDraft


_REQUESTS_ALL_NUMERIC_STATISTICS = re.compile(
    r"先\s*(?:按[^，,。！？；;\n]+?)?(?:稳定)?排序\s*[，,]?\s*(?:再|然后)\s*"
    r"(?:(?:计算|生成|给出|做)\s*)?(?:各|每个|所有|全部)\s*数值(?:列|字段)的?\s*描述(?:性)?统计"
    r"|\bfirst\s+(?:stably\s+)?sort\b[^.;\n]*?\bthen\s+"
    r"(?:(?:calculate|compute|produce)\s+)?descriptive\s+statistics\s+"
    r"for\s+(?:all|each|every)\s+numeric\s+(?:columns?|fields?)\b",
    re.IGNORECASE,
)
_ADDITIONAL_DELIVERY = re.compile(
    r"(?:排序(?:后|结果|表)|中间(?:表|结果|产物))[^。！？；\n]{0,16}"
    r"(?:也要|另存|单独|保留|输出|导出|发布|交付|保存|展示|返回)"
    r"|(?:导出|输出|发布|保留|交付|保存|展示|返回|另存)[^。！？；\n]{0,24}"
    r"(?:排序(?:后|结果|表)|中间(?:表|结果|产物))"
    r"|\b(?:export|include|return|save|keep|publish|deliver|show)\b[^.;\n]{0,36}"
    r"\b(?:sorted|intermediate)\b",
    re.IGNORECASE,
)
_DECLINES_INTERMEDIATE = re.compile(
    r"(?:不(?:要|必|需要)?|无需|禁止)\s*(?:导出|输出|发布|保留|交付|保存|展示|返回|另存)"
    r"[^。！？；\n]{0,24}(?:排序(?:后|结果|表)|中间(?:表|结果|产物))"
    r"|\b(?:do not|don't|never)\s+(?:export|include|return|save|keep|publish|deliver|show)\b"
    r"[^.;\n]{0,36}\b(?:sorted|intermediate)\b",
    re.IGNORECASE,
)
_EXPLICIT_TYPE_LIMIT = re.compile(
    r"\binferNumericStrings\s*[=:：]\s*false\b"
    r"|(?:不(?:要|得)?|禁止)\s*(?:识别|推断|转换|解析)\s*(?:数值文本|数字字符串|文本数值|文本列)"
    r"|只\s*(?:统计|使用)\s*(?:物理|原生)\s*数值"
    r"|\b(?:do not|never)\s+(?:infer|parse|coerce)\s+(?:numeric\s+text|numeric\s+strings)\b",
    re.IGNORECASE,
)
_OUTPUT_ISSUE = "WORKFLOW_DRAFT_INTERMEDIATE_OUTPUT"
_TEXT_ISSUE = "WORKFLOW_DRAFT_NUMERIC_TEXT_REQUIRED"


class DraftAuthoringContractError(AgentRunError):
    def __init__(self, issues: list[dict[str, str]]) -> None:
        super().__init__(
            "AGENT_WORKFLOW_DRAFT_INVALID",
            diagnostics={"kind": "WORKFLOW_VALIDATION", **issues[0]},
        )
        self.issues = issues


def validate_draft_authoring_contract(
    draft: WorkflowDraftInput, user_goal: str, observed_csv_versions: Set[str],
) -> None:
    if len(draft.graph.nodes) != 2 or not _REQUESTS_ALL_NUMERIC_STATISTICS.search(user_goal):
        return
    by_operator = {
        (node.operator_id, node.operator_version): (index, node)
        for index, node in enumerate(draft.graph.nodes)
    }
    sort_entry = by_operator.get(("table.sort", "1.0.0"))
    describe_entry = by_operator.get(("statistics.describe_columns", "1.0.0"))
    if sort_entry is None or describe_entry is None:
        return
    _, sort = sort_entry
    describe_index, describe = describe_entry
    ranked_table = describe.inputs.get("data")
    if (
        not isinstance(ranked_table, WorkflowNodeBindingDraft)
        or (ranked_table.node_output.node_id, ranked_table.node_output.port) != (sort.node_id, "data")
    ):
        return

    issues = []
    delivery_clauses = re.split(r"[,，;；.。！？!?\n]", user_goal)
    if not any(
        _ADDITIONAL_DELIVERY.search(clause) and not _DECLINES_INTERMEDIATE.search(clause)
        for clause in delivery_clauses
    ):
        for index, output in enumerate(draft.graph.outputs):
            ref = output.node_output
            if (ref.node_id, ref.port) != (describe.node_id, "summary"):
                issues.append({"code": _OUTPUT_ISSUE, "path": f"/graph/outputs/{index}"})

    source = sort.inputs.get("data")
    observed_csv = isinstance(source, WorkflowInputBindingDraft) and any(
        binding.input_name == source.workflow_input
        and binding.data_ref.version_id in observed_csv_versions
        for binding in draft.data_bindings
    )
    option = describe.parameters.get("inferNumericStrings")
    if (
        observed_csv and not _EXPLICIT_TYPE_LIMIT.search(user_goal)
        and (option is None or draft.parameter_values.get(option.workflow_parameter) is not True)
    ):
        issues.append({
            "code": _TEXT_ISSUE,
            "path": f"/graph/nodes/{describe_index}/parameters/inferNumericStrings",
        })
    if issues:
        raise DraftAuthoringContractError(issues)


def draft_authoring_repair_instruction(issues: list[dict[str, str]]) -> str:
    steps = []
    codes = {issue["code"] for issue in issues}
    if _OUTPUT_ISSUE in codes:
        steps.append(
            "Keep the sort data port as the statistics node's input. Remove the reported "
            "workflow outputs. Export only the describe-columns node's summary port."
        )
    if _TEXT_ISSUE in codes:
        steps.append(
            "The observed CSV stays textual through sorting. Bind inferNumericStrings to "
            "a declared workflowParameter and set that parameter to JSON true. Give the "
            "matching parameter_sources entry with source DATA and explain the observed CSV "
            "format. Do not infer field meaning or units."
        )
    return " ".join([
        *steps,
        "Preserve the inspected Data binding and every missing input. Do not invent sorting rules.",
    ])
