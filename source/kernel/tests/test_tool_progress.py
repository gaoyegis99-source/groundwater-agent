from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from platform_kernel.agent_runtime.sdk_execution.usage_capture import _RunUsageCapture
from platform_kernel.errors import AgentRunError, UpstreamServiceError
from platform_kernel.settings_types import (
    ObservationToolDefinition,
    ToolOutcomeProgressDefinition,
    ToolProgressDefinition,
)
from platform_kernel.tool_progress import (
    control_tool_outcome,
    parse_progress_intent,
    provider_progress_intent_schema,
    render_declared_tool_outcome,
    render_tool_outcome,
    validate_progress_definition,
)


def _progress() -> ToolProgressDefinition:
    return ToolProgressDefinition(
        capability="data.discovery",
        semantic_role="DATA_DISCOVERY",
        template_key="data.discovery",
        title="检索项目数据",
        running_summary_template="正在{objective}。",
        processing_title="筛选项目数据",
        processing_summary_template="Agent 正在根据“{objective}”的结果筛选数据。",
        outcomes={
            "candidates.found": ToolOutcomeProgressDefinition(
                summary_template="找到 {candidateCount} 个候选数据版本。",
                facts_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "candidateCount": {"type": "integer", "minimum": 1}
                    },
                    "required": ["candidateCount"],
                },
            ),
            "candidates.empty": ToolOutcomeProgressDefinition(
                summary_template="当前条件未找到候选数据版本。",
                facts_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {},
                    "required": [],
                },
            ),
        },
    )


def _tool() -> ObservationToolDefinition:
    return ObservationToolDefinition(
        name="discover_datasets",
        description="Discover datasets.",
        service="data",
        path="/internal/tools/v3/discover_datasets",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        output_schema={"type": "object"},
        input_schema_checksum="sha256:input",
        output_schema_checksum="sha256:output",
        timeout_seconds=10,
        progress=_progress(),
    )


class _Recorder:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def record(self, activity: dict) -> None:
        self.items.append(deepcopy(activity))


def test_progress_intent_schema_declares_semantics_not_business_arguments() -> None:
    schema = provider_progress_intent_schema(_tool())

    assert schema["properties"]["capability"]["const"] == "data.discovery"
    assert schema["properties"]["template_key"]["const"] == "data.discovery"
    assert set(schema["properties"]) == {"capability", "objective", "template_key"}
    assert schema["additionalProperties"] is False


def test_agent_intent_must_match_the_service_owned_progress_contract() -> None:
    arguments = {
        "progress_intent": {
            "capability": "knowledge.retrieve",
            "objective": "查找地下水数据",
            "template_key": "knowledge.retrieve",
        },
        "query": "地下水",
    }

    with pytest.raises(AgentRunError, match="AGENT_MODEL_OUTPUT_INVALID"):
        parse_progress_intent(_tool(), arguments)


def test_outcome_template_and_fact_schema_are_one_closed_contract() -> None:
    invalid = _progress()
    invalid = replace(
        invalid,
        outcomes={
            "candidates.found": ToolOutcomeProgressDefinition(
                "找到 {count} 个候选数据版本。",
                invalid.outcomes["candidates.found"].facts_schema,
            )
        },
    )

    with pytest.raises(ValueError, match="TOOL_PROGRESS_OUTCOME_INVALID"):
        validate_progress_definition(invalid)


def test_tool_outcome_rejects_undeclared_codes_and_facts() -> None:
    with pytest.raises(UpstreamServiceError, match="OBSERVATION_OUTCOME_UNKNOWN"):
        render_tool_outcome(
            _progress(), {"outcomeCode": "dataset.loaded", "facts": {}}
        )
    with pytest.raises(UpstreamServiceError, match="OBSERVATION_OUTCOME_INVALID"):
        render_tool_outcome(
            _progress(),
            {
                "outcomeCode": "candidates.found",
                "facts": {"candidateCount": 2, "privateQuery": "secret"},
            },
        )


def test_control_progress_is_explicit_and_business_status_is_not_interpreted() -> None:
    with pytest.raises(UpstreamServiceError, match="OBSERVATION_OUTCOME_INVALID"):
        render_declared_tool_outcome(
            _progress(), {"status": "RETRY_REQUIRED"}
        )

    outcome, summary, is_control = render_declared_tool_outcome(
        _progress(), control_tool_outcome("control.retry_required")
    )

    assert outcome.outcome_code == "control.retry_required"
    assert summary == "工具参数未通过合同校验，Agent 正在按合同修正后重试。"
    assert is_control is True


def test_activity_uses_declared_outcome_and_never_interprets_business_fields() -> None:
    recorder = _Recorder()
    definition = _tool()
    capture = _RunUsageCapture(
        10_000,
        segment={"agent_run_id": "run-1", "goal_revision": 1, "segment_id": "s-1"},
        activity_recorder=recorder,
        observation_names=frozenset({definition.name}),
        observation_tools=(definition,),
        purpose="PROCESSING",
    )
    context = SimpleNamespace(
        tool_call_id="call-1",
        tool_arguments=json.dumps(
            {
                "progress_intent": {
                    "capability": "data.discovery",
                    "objective": "查找适用于含水层分析的数据版本",
                    "template_key": "data.discovery",
                },
                "query": "含水层",
            },
            ensure_ascii=False,
        ),
    )
    tool = SimpleNamespace(name=definition.name)
    result = {
        "data": {
            "status": "PRIVATE_STATUS_MUST_NOT_DRIVE_PROGRESS",
            "summary": "PRIVATE_SUMMARY_MUST_NOT_APPEAR",
            "items": [{"name": "PRIVATE_ITEM"}] * 99,
        },
        "outcome": {
            "outcomeCode": "candidates.found",
            "facts": {"candidateCount": 2},
        },
    }

    async def exercise() -> None:
        await capture.on_tool_start(context, None, tool)
        await capture.on_tool_end(context, None, tool, result)

    asyncio.run(exercise())

    running, complete = recorder.items
    assert running["title"] == "检索项目数据"
    assert running["summary"] == "正在查找适用于含水层分析的数据版本。"
    assert "objective" not in running
    assert complete["summary"] == "找到 2 个候选数据版本。"
    assert complete["outcome_code"] == "candidates.found"
    assert complete["facts"] == {"candidateCount": 2}
    assert "PRIVATE_" not in json.dumps(complete, ensure_ascii=False)
    assert capture.next_model_activity == (
        "筛选项目数据",
        "Agent 正在根据“查找适用于含水层分析的数据版本”的结果筛选数据。",
    )
