"""Change-boundary coverage for every progress-bearing Agent tool."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from platform_kernel.agent_runtime.sdk_execution.usage_capture import _RunUsageCapture
from platform_kernel.agent_runtime.tooling.observations import _observation_input_schema
from platform_kernel.tool_progress import (
    BUILTIN_TOOL_PROGRESS,
    builtin_progress_intent_schema,
    parse_progress_intent,
    render_processing_progress,
    validate_builtin_progress_intent,
)


ROOT = Path(__file__).resolve().parents[2]


def _fixed_provider():
    path = ROOT / "tests/e2e/compose-stub/stub_server.py"
    spec = importlib.util.spec_from_file_location("progress_contract_provider", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _facts(schema: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, definition in schema.get("properties", {}).items():
        kind = definition.get("type")
        if kind in {"integer", "number"}:
            result[name] = max(1, definition.get("minimum", 0))
        elif kind == "boolean":
            result[name] = True
        elif kind == "array":
            result[name] = []
        elif kind == "object":
            result[name] = {}
        else:
            result[name] = "verified"
    return result


class _Recorder:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def record(self, activity: dict) -> None:
        self.items.append(deepcopy(activity))


def _project_activity(name, progress, arguments, *, definition=None) -> tuple[str, str]:
    recorder = _Recorder()
    capture = _RunUsageCapture(
        10_000,
        segment={"agent_run_id": "run", "goal_revision": 1, "segment_id": "segment"},
        activity_recorder=recorder,
        observation_names=frozenset({name}) if definition is not None else frozenset(),
        observation_tools=() if definition is None else (definition,),
        purpose="PROCESSING",
    )
    context = SimpleNamespace(
        tool_call_id="call",
        tool_arguments=json.dumps(arguments, ensure_ascii=False),
        context={},
    )
    outcome_code, outcome = next(iter(progress.outcomes.items()))
    result = {
        "progressOutcome" if definition is None else "outcome": {
            "outcomeCode": outcome_code,
            "facts": _facts(outcome.facts_schema),
        }
    }

    async def exercise() -> None:
        tool = SimpleNamespace(name=name)
        await capture.on_tool_start(context, None, tool)
        await capture.on_tool_end(context, None, tool, result)

    asyncio.run(exercise())
    assert len(recorder.items) == 2
    assert recorder.items[0]["capability"] == progress.capability
    assert recorder.items[0]["template_key"] == progress.template_key
    assert recorder.items[1]["outcome_code"] == outcome_code
    assert capture.next_model_activity is not None
    return capture.next_model_activity


def test_every_progress_tool_updates_schema_provider_activity_and_audit_projection(
    kernel_settings,
) -> None:
    provider = _fixed_provider()
    observations = {
        item.name: item for item in kernel_settings("groundwater").observation_tools
    }
    declared = set(observations) | set(BUILTIN_TOOL_PROGRESS)

    # A newly assembled tool is incomplete until its Provider objective is
    # explicit. This prevents silently falling back to a generic status phrase.
    assert set(provider.PROGRESS_OBJECTIVES) == declared

    for name in sorted(declared):
        definition = observations.get(name)
        progress = BUILTIN_TOOL_PROGRESS[name] if definition is None else definition.progress
        assert progress is not None
        intent_schema = (
            builtin_progress_intent_schema(name)
            if definition is None
            else _observation_input_schema(definition, "deepseek")["properties"][
                "progress_intent"
            ]
        )
        full_schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"progress_intent": intent_schema},
            "required": ["progress_intent"],
        }
        tool_wire = [{"type": "function", "name": name, "parameters": full_schema}]
        selected, enriched = provider._add_progress_intent(
            name, {}, tool_wire, chat=False
        )
        assert selected == name
        assert enriched["progress_intent"]["objective"] == provider.PROGRESS_OBJECTIVES[name]
        assert enriched["progress_intent"]["capability"] == progress.capability
        assert enriched["progress_intent"]["template_key"] == progress.template_key

        if definition is None:
            intent = validate_builtin_progress_intent(name, enriched["progress_intent"])
            service_arguments = provider._business_arguments(enriched)
        else:
            provider_schema = _observation_input_schema(definition, "deepseek")
            assert "progress_intent" in provider_schema["required"]
            intent, service_arguments = parse_progress_intent(definition, enriched)

        # Control metadata drives Agent activity, but cannot enter service
        # business arguments and therefore cannot enter service argument audit.
        assert service_arguments == {}
        expected_processing = render_processing_progress(progress, intent)
        assert _project_activity(
            name, progress, enriched, definition=definition
        ) == expected_processing
