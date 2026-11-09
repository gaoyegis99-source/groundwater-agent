from __future__ import annotations

from string import Formatter

from jsonschema import Draft202012Validator, ValidationError
from platform_protocol_contracts import AgentProgressIntent, ToolOutcome

from .settings_types import (
    ObservationToolDefinition,
    ToolOutcomeProgressDefinition,
    ToolProgressDefinition,
)
from .errors import AgentRunError, UpstreamServiceError


def _facts_schema(properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


BUILTIN_TOOL_PROGRESS: dict[str, ToolProgressDefinition] = {
    "list_operators": ToolProgressDefinition(
        capability="workflow.operator.discovery",
        semantic_role="OPERATOR_DISCOVERY",
        template_key="workflow.operator.discovery",
        title="检索分析算子",
        running_summary_template="正在{objective}。",
        processing_title="筛选分析算子",
        processing_summary_template="Agent 正在根据“{objective}”的检索结果筛选适用算子。",
        outcomes={
            "operators.found": ToolOutcomeProgressDefinition(
                "找到 {operatorCount} 个候选分析算子。",
                _facts_schema({"operatorCount": {"type": "integer", "minimum": 1}}),
            ),
            "operators.empty": ToolOutcomeProgressDefinition(
                "当前条件未找到候选分析算子。", _facts_schema({})
            ),
        },
    ),
    "get_operator": ToolProgressDefinition(
        capability="workflow.operator.inspect",
        semantic_role="OPERATOR_INSPECTION",
        template_key="workflow.operator.inspect",
        title="核验分析算子契约",
        running_summary_template="正在{objective}。",
        processing_title="核对算子适用性",
        processing_summary_template="Agent 正在根据“{objective}”的契约结果核对输入、参数和输出。",
        outcomes={
            "operator.inspected": ToolOutcomeProgressDefinition(
                "已读取并核验所选分析算子的完整契约。", _facts_schema({})
            )
        },
    ),
    "list_execution_facts": ToolProgressDefinition(
        capability="execution.facts.list",
        semantic_role="EXECUTION_FACT_READ",
        template_key="execution.facts.list",
        title="读取执行记录",
        running_summary_template="正在{objective}。",
        processing_title="核对历史执行",
        processing_summary_template="Agent 正在根据“{objective}”的记录判断可复用结果。",
        outcomes={
            "execution_facts.listed": ToolOutcomeProgressDefinition(
                "已读取 {factCount} 条持久化执行记录。",
                _facts_schema({"factCount": {"type": "integer", "minimum": 0}}),
            )
        },
    ),
    "get_execution_fact": ToolProgressDefinition(
        capability="execution.fact.read",
        semantic_role="EXECUTION_FACT_READ",
        template_key="execution.fact.read",
        title="核验执行事实",
        running_summary_template="正在{objective}。",
        processing_title="整理执行事实",
        processing_summary_template="Agent 正在根据“{objective}”的事实更新当前判断。",
        outcomes={
            "execution_fact.read": ToolOutcomeProgressDefinition(
                "已核验指定任务的持久化执行状态和结果记录。", _facts_schema({})
            ),
            "execution_fact.not_found": ToolOutcomeProgressDefinition(
                "当前会话中未找到该 WorkflowRun 的可见执行记录。", _facts_schema({})
            )
        },
    ),
}

CONTROL_TOOL_OUTCOMES: dict[str, ToolOutcomeProgressDefinition] = {
    "control.retry_required": ToolOutcomeProgressDefinition(
        "工具参数未通过合同校验，Agent 正在按合同修正后重试。",
        _facts_schema({}),
    ),
    "control.duplicate_stopped": ToolOutcomeProgressDefinition(
        "检测到重复读取，本阶段已停止重复查询。", _facts_schema({})
    ),
    "control.read_budget_exhausted": ToolOutcomeProgressDefinition(
        "本阶段读取额度已用完，Agent 正在基于现有结果继续处理。",
        _facts_schema({}),
    ),
    "control.pagination_stopped": ToolOutcomeProgressDefinition(
        "目录分页已经收敛，Agent 正在基于现有候选继续处理。",
        _facts_schema({}),
    ),
}

def validate_progress_definition(definition: ToolProgressDefinition) -> None:
    if not definition.outcomes:
        raise ValueError("TOOL_PROGRESS_OUTCOMES_REQUIRED")
    if "{" in definition.title or "}" in definition.title:
        raise ValueError("TOOL_PROGRESS_TITLE_INVALID")
    if _template_fields(definition.running_summary_template) != ("objective",):
        raise ValueError("TOOL_PROGRESS_RUNNING_TEMPLATE_INVALID")
    if "{" in definition.processing_title or "}" in definition.processing_title:
        raise ValueError("TOOL_PROGRESS_PROCESSING_TITLE_INVALID")
    if _template_fields(definition.processing_summary_template) != ("objective",):
        raise ValueError("TOOL_PROGRESS_PROCESSING_TEMPLATE_INVALID")
    for outcome_code, outcome in definition.outcomes.items():
        Draft202012Validator.check_schema(outcome.facts_schema)
        properties = outcome.facts_schema.get("properties")
        required = outcome.facts_schema.get("required")
        if (
            outcome.facts_schema.get("type") != "object"
            or outcome.facts_schema.get("additionalProperties") is not False
            or not isinstance(properties, dict)
            or not isinstance(required, list)
            or set(required) != set(properties)
            or set(_template_fields(outcome.summary_template)) != set(properties)
        ):
            raise ValueError(f"TOOL_PROGRESS_OUTCOME_INVALID:{outcome_code}")


def builtin_progress_definition(name: str) -> ToolProgressDefinition | None:
    definition = BUILTIN_TOOL_PROGRESS.get(name)
    if definition is not None:
        validate_progress_definition(definition)
    return definition


def validate_builtin_progress_intent(
    name: str, value: AgentProgressIntent | object
) -> AgentProgressIntent:
    definition = builtin_progress_definition(name)
    if definition is None:
        raise AgentRunError("TOOL_PROGRESS_CONTRACT_MISSING")
    try:
        intent = (
            value
            if isinstance(value, AgentProgressIntent)
            else AgentProgressIntent.model_validate(value)
        )
    except (TypeError, ValueError) as error:
        raise AgentRunError(
            "AGENT_MODEL_OUTPUT_INVALID",
            diagnostics={"reason": "PROGRESS_INTENT_INVALID"},
        ) from error
    if (
        intent.capability != definition.capability
        or intent.template_key != definition.template_key
    ):
        raise AgentRunError(
            "AGENT_MODEL_OUTPUT_INVALID",
            diagnostics={"reason": "PROGRESS_INTENT_MISMATCH"},
        )
    return intent


def builtin_tool_outcome(
    name: str, outcome_code: str, facts: dict[str, object]
) -> dict[str, object]:
    definition = builtin_progress_definition(name)
    if definition is None:
        raise RuntimeError("TOOL_PROGRESS_CONTRACT_MISSING")
    outcome, _summary = render_tool_outcome(
        definition, {"outcomeCode": outcome_code, "facts": facts}
    )
    return outcome.model_dump(by_alias=True)


def control_tool_outcome(outcome_code: str) -> dict[str, object]:
    definition = CONTROL_TOOL_OUTCOMES.get(outcome_code)
    if definition is None:
        raise RuntimeError("TOOL_CONTROL_OUTCOME_UNKNOWN")
    outcome = ToolOutcome(outcomeCode=outcome_code, facts={})
    Draft202012Validator(definition.facts_schema).validate(outcome.facts)
    return outcome.model_dump(by_alias=True)


def parse_progress_intent(
    definition: ObservationToolDefinition, arguments: dict[str, object]
) -> tuple[AgentProgressIntent, dict[str, object]]:
    progress = definition.progress
    raw_intent = arguments.get("progress_intent")
    service_arguments = {
        key: value for key, value in arguments.items() if key != "progress_intent"
    }
    if progress is None:
        raise AgentRunError("TOOL_PROGRESS_CONTRACT_MISSING")
    try:
        intent = AgentProgressIntent.model_validate(raw_intent)
    except (TypeError, ValueError) as error:
        raise AgentRunError(
            "AGENT_MODEL_OUTPUT_INVALID",
            diagnostics={"reason": "PROGRESS_INTENT_INVALID"},
        ) from error
    if (
        intent.capability != progress.capability
        or intent.template_key != progress.template_key
    ):
        raise AgentRunError(
            "AGENT_MODEL_OUTPUT_INVALID",
            diagnostics={"reason": "PROGRESS_INTENT_MISMATCH"},
        )
    return intent, service_arguments


def render_running_progress(
    progress: ToolProgressDefinition, intent: AgentProgressIntent
) -> tuple[str, str]:
    if (
        intent.capability != progress.capability
        or intent.template_key != progress.template_key
    ):
        raise AgentRunError("PROGRESS_INTENT_MISMATCH")
    return progress.title, progress.running_summary_template.format(
        objective=intent.objective
    )


def render_tool_outcome(
    progress: ToolProgressDefinition, value: object
) -> tuple[ToolOutcome, str]:
    try:
        outcome = ToolOutcome.model_validate(value)
    except (TypeError, ValueError) as error:
        raise UpstreamServiceError("OBSERVATION_OUTCOME_INVALID") from error
    definition = progress.outcomes.get(outcome.outcome_code)
    if definition is None:
        raise UpstreamServiceError("OBSERVATION_OUTCOME_UNKNOWN")
    try:
        Draft202012Validator(definition.facts_schema).validate(outcome.facts)
        summary = definition.summary_template.format_map(outcome.facts)
    except (ValidationError, KeyError, ValueError) as error:
        raise UpstreamServiceError("OBSERVATION_OUTCOME_INVALID") from error
    return outcome, summary


def render_declared_tool_outcome(
    progress: ToolProgressDefinition, value: object
) -> tuple[ToolOutcome, str, bool]:
    """Render one explicit producer outcome without inspecting business fields."""

    try:
        outcome = ToolOutcome.model_validate(value)
    except (TypeError, ValueError) as error:
        raise UpstreamServiceError("OBSERVATION_OUTCOME_INVALID") from error
    control = CONTROL_TOOL_OUTCOMES.get(outcome.outcome_code)
    if control is None:
        declared, summary = render_tool_outcome(progress, outcome)
        return declared, summary, False
    try:
        Draft202012Validator(control.facts_schema).validate(outcome.facts)
        summary = control.summary_template.format_map(outcome.facts)
    except (ValidationError, KeyError, ValueError) as error:
        raise UpstreamServiceError("OBSERVATION_OUTCOME_INVALID") from error
    return outcome, summary, True


def render_processing_progress(
    progress: ToolProgressDefinition, intent: AgentProgressIntent
) -> tuple[str, str]:
    if (
        intent.capability != progress.capability
        or intent.template_key != progress.template_key
    ):
        raise AgentRunError("PROGRESS_INTENT_MISMATCH")
    return progress.processing_title, progress.processing_summary_template.format(
        objective=intent.objective
    )


def provider_progress_intent_schema(
    definition: ObservationToolDefinition,
) -> dict[str, object]:
    progress = definition.progress
    if progress is None:
        raise ValueError("TOOL_PROGRESS_CONTRACT_MISSING")
    return progress_intent_schema(progress)


def builtin_progress_intent_schema(name: str) -> dict[str, object]:
    progress = builtin_progress_definition(name)
    if progress is None:
        raise ValueError("TOOL_PROGRESS_CONTRACT_MISSING")
    return progress_intent_schema(progress)


def progress_intent_schema(progress: ToolProgressDefinition) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "capability": {"type": "string", "const": progress.capability},
            "objective": {
                "type": "string",
                "minLength": 1,
                "maxLength": 240,
                "pattern": r"^(?!正在)[^\r\n\t]*[^。！？.!?\s]$",
            },
            "template_key": {"type": "string", "const": progress.template_key},
        },
        "required": ["capability", "objective", "template_key"],
    }


def _template_fields(template: str) -> tuple[str, ...]:
    fields: list[str] = []
    for _literal, name, format_spec, conversion in Formatter().parse(template):
        if name is None:
            continue
        if not name or "." in name or "[" in name or format_spec or conversion:
            raise ValueError("TOOL_PROGRESS_TEMPLATE_INVALID")
        fields.append(name)
    return tuple(fields)
