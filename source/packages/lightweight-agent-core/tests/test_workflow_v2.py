from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from platform_lightweight_agent import (
    AgentDecision,
    AgentDriverFailure,
    AgentToolError,
    AgentToolExample,
    AgentToolSpec,
    InMemoryRunJournal,
    LocalToolExecutor,
    ToolCall,
    ToolReference,
    WorkflowEngine,
    WorkflowSpec,
    WorkflowValidationError,
    agent_step,
    export_bundle,
    import_bundle,
    workflow,
)

DIALECT = "https://json-schema.org/draft/2020-12/schema"
TOOL = ToolReference("demo.lookup", "1.2.3", "sha256:" + "1" * 64)
OTHER_TOOL = ToolReference("demo.other", "2.0.0", "sha256:" + "2" * 64)
INPUT_SCHEMA = {
    "$schema": DIALECT,
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
    "additionalProperties": False,
}
OUTPUT_SCHEMA = {
    "$schema": DIALECT,
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def _contract(
    *,
    name: str = "lookup",
    tool: ToolReference = TOOL,
    input_schema: dict | None = None,
    output_schema: dict | None = None,
) -> AgentToolSpec:
    return AgentToolSpec(
        name=name,
        tool=tool,
        description="Look up a record by its query.",
        input_schema=input_schema or INPUT_SCHEMA,
        output_schema=output_schema or OUTPUT_SCHEMA,
        examples=(
            AgentToolExample({"query": "alpha"}, output={"answer": "A"}),
            AgentToolExample({"query": "missing"}, error="NOT_FOUND"),
        ),
        errors=(
            AgentToolError(
                "NOT_FOUND",
                "No record matched the query.",
                model_retryable=True,
            ),
        ),
    )


def test_v2_contract_round_trip_and_schema_version_separation() -> None:
    contract = _contract()
    spec = workflow(
        "native-agent",
        "1",
        [agent_step("plan", "native", tools=(contract,))],
        schema_version="v6-light-workflow-v2",
    )

    assert WorkflowSpec.from_mapping(spec.to_mapping()) == spec
    assert spec.to_mapping()["steps"][0]["config"]["tools"] == [contract.to_mapping()]

    with pytest.raises(WorkflowValidationError, match="v1 agent steps"):
        workflow(
            "bad-v1",
            "1",
            [agent_step("plan", "native", tools=(contract,))],
            schema_version="v6-light-workflow-v1",
        )
    with pytest.raises(WorkflowValidationError, match="v2 agent steps"):
        workflow(
            "bad-v2",
            "1",
            [agent_step("plan", "native", tools=(TOOL,))],
            schema_version="v6-light-workflow-v2",
        )


def test_v2_contract_rejects_unknown_fields_and_alias_or_reference_conflicts() -> None:
    raw = _contract().to_mapping()
    raw["unexpected"] = True
    with pytest.raises(WorkflowValidationError, match="unknown or missing"):
        AgentToolSpec.from_mapping(raw)

    with pytest.raises(WorkflowValidationError, match="names must be unique"):
        agent_step(
            "plan",
            "native",
            tools=(_contract(), _contract(tool=OTHER_TOOL)),
        )
    with pytest.raises(WorkflowValidationError, match="references must be unique"):
        agent_step(
            "plan",
            "native",
            tools=(_contract(), _contract(name="lookup_again")),
        )


@pytest.mark.parametrize("name", ["", "1lookup", "lookup.record", "x" * 65])
def test_v2_contract_requires_provider_compatible_short_names(name: str) -> None:
    with pytest.raises(WorkflowValidationError, match="short name"):
        _contract(name=name)


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        (
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "Draft 2020-12",
        ),
        (
            {
                "$schema": DIALECT,
                "type": "array",
                "items": {"type": "string"},
            },
            "root type must be object",
        ),
        (
            {
                "$schema": DIALECT,
                "type": "object",
                "properties": {"query": {"$ref": "https://schemas.example/query.json"}},
                "additionalProperties": False,
            },
            "only local",
        ),
        (
            {
                "$schema": DIALECT,
                "type": "object",
                "properties": {
                    "query": {"$dynamicRef": "https://schemas.example/query.json"}
                },
                "additionalProperties": False,
            },
            "only local",
        ),
        (
            {
                "$schema": DIALECT,
                "type": "object",
                "properties": {"options": {"type": "object", "properties": {}}},
                "additionalProperties": False,
            },
            "additionalProperties",
        ),
        (
            {
                "$schema": DIALECT,
                "type": "object",
                "properties": {
                    "options": {
                        "type": ["object", "null"],
                        "properties": {},
                    }
                },
                "additionalProperties": False,
            },
            "additionalProperties",
        ),
        (
            {
                "$schema": DIALECT,
                "type": "object",
                "properties": {"api_key": {"type": "string"}},
                "additionalProperties": False,
            },
            "credential",
        ),
    ],
)
def test_v2_contract_enforces_strict_safe_schema_subset(
    schema: dict, message: str
) -> None:
    with pytest.raises(WorkflowValidationError, match=message):
        _contract(input_schema=schema)


def test_v2_contract_enforces_examples_errors_and_size_limits() -> None:
    example = AgentToolExample({"query": "x"}, output={"answer": "x"})
    with pytest.raises(WorkflowValidationError, match="exactly one"):
        AgentToolExample({"query": "x"})
    with pytest.raises(WorkflowValidationError, match="exactly one"):
        AgentToolExample({"query": "x"}, output={"answer": "x"}, error="NOT_FOUND")
    with pytest.raises(WorkflowValidationError, match="must be declared"):
        AgentToolSpec(
            "lookup",
            TOOL,
            "Lookup.",
            INPUT_SCHEMA,
            OUTPUT_SCHEMA,
            examples=(AgentToolExample({"query": "x"}, error="NOT_FOUND"),),
        )
    with pytest.raises(WorkflowValidationError, match="must be unique"):
        AgentToolSpec(
            "lookup",
            TOOL,
            "Lookup.",
            INPUT_SCHEMA,
            OUTPUT_SCHEMA,
            errors=(
                AgentToolError("NOT_FOUND", "First."),
                AgentToolError("NOT_FOUND", "Second."),
            ),
        )
    with pytest.raises(WorkflowValidationError, match="at most four"):
        AgentToolSpec(
            "lookup",
            TOOL,
            "Lookup.",
            INPUT_SCHEMA,
            OUTPUT_SCHEMA,
            examples=(example,) * 5,
        )
    with pytest.raises(WorkflowValidationError, match="at most 32"):
        AgentToolSpec(
            "lookup",
            TOOL,
            "Lookup.",
            INPUT_SCHEMA,
            OUTPUT_SCHEMA,
            errors=tuple(
                AgentToolError(f"ERROR_{index}", "A declared error.")
                for index in range(33)
            ),
        )
    with pytest.raises(WorkflowValidationError, match="description"):
        AgentToolSpec(
            "lookup",
            TOOL,
            "x" * 4_097,
            INPUT_SCHEMA,
            OUTPUT_SCHEMA,
        )
    with pytest.raises(WorkflowValidationError, match="description"):
        AgentToolSpec(
            "lookup",
            TOOL,
            "界" * 1_366,
            INPUT_SCHEMA,
            OUTPUT_SCHEMA,
        )
    with pytest.raises(WorkflowValidationError, match="credentials"):
        AgentToolExample({"api_key": "not-portable"}, output={"answer": "x"})
    with pytest.raises(WorkflowValidationError, match="credentials"):
        AgentToolExample({"clientSecret": "not-portable"}, output={"answer": "x"})

    oversized_schema = deepcopy(INPUT_SCHEMA)
    oversized_schema["properties"]["query"]["description"] = "x" * 65_536
    with pytest.raises(WorkflowValidationError, match="64 KiB"):
        _contract(input_schema=oversized_schema)

    contracts = []
    for index in range(5):
        large_schema = deepcopy(INPUT_SCHEMA)
        large_schema["properties"]["query"]["description"] = "x" * 52_500
        contracts.append(
            AgentToolSpec(
                f"lookup_{index}",
                ToolReference(
                    f"demo.lookup_{index}",
                    "1",
                    "sha256:" + str(index + 3) * 64,
                ),
                "Lookup.",
                large_schema,
                OUTPUT_SCHEMA,
            )
        )
    with pytest.raises(WorkflowValidationError, match="256 KiB"):
        agent_step("plan", "native", tools=contracts)


class _RecordingDriver:
    driver_version = "recording-native-v2"

    def __init__(self, decisions: list[AgentDecision]) -> None:
        self.decisions = list(decisions)
        self.contexts = []

    def decide(self, context):  # type: ignore[no-untyped-def]
        self.contexts.append(context)
        return self.decisions.pop(0)


def test_v2_engine_resolves_alias_without_expanding_validated_arguments() -> None:
    literal_reference = {"$ref": "/input/should-not-expand"}
    nested_input_schema = {
        "$schema": DIALECT,
        "type": "object",
        "properties": {
            "query": {
                "type": "object",
                "properties": {"$ref": {"type": "string"}},
                "required": ["$ref"],
                "additionalProperties": False,
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    contract = _contract(input_schema=nested_input_schema)
    driver = _RecordingDriver(
        [
            AgentDecision("", (ToolCall(TOOL, {"query": literal_reference}),)),
            AgentDecision("finished"),
        ]
    )
    observed: list[dict] = []
    executor = LocalToolExecutor()

    def lookup(arguments: dict) -> dict:
        observed.append(arguments)
        return {"answer": "kept literal"}

    executor.register(TOOL, lookup)
    journal = InMemoryRunJournal()
    engine = WorkflowEngine(journal, executor, {"native": driver})
    spec = workflow(
        "native-engine",
        "1",
        [agent_step("plan", "native", max_turns=2, tools=(contract,))],
        schema_version="v6-light-workflow-v2",
    )

    waiting = engine.run(
        spec,
        {"should-not-expand": "host-value"},
        run_id="native-engine-run",
    )
    assert waiting.status == "WAITING_APPROVAL"
    complete = engine.resume(waiting.run_id, approved=True)

    assert complete.status == "SUCCEEDED"
    assert observed == [{"query": literal_reference}]
    assert driver.contexts[0].workflow_schema_version == "v6-light-workflow-v2"
    assert driver.contexts[0].tool_specs == (contract,)
    assert driver.contexts[0].allowed_tools == (TOOL,)
    result = driver.contexts[1].prior_tool_results[0]["tool_results"][0]
    assert result == {
        "name": "lookup",
        "arguments": {"query": literal_reference},
        "succeeded": True,
        "output": {"answer": "kept literal"},
        "error": None,
    }
    serialized_history = json.dumps(driver.contexts[1].prior_tool_results)
    assert "action_id" not in serialized_history
    assert "tool_id" not in serialized_history
    assert "package_digest" not in serialized_history


def test_agent_driver_failure_code_is_preserved_by_the_kernel() -> None:
    class FailingDriver:
        driver_version = "failing-native-v2"

        def decide(self, context):  # type: ignore[no-untyped-def]
            del context
            raise AgentDriverFailure("AGENT_MODEL_OUTPUT_INVALID")

    tools = LocalToolExecutor()
    tools.register(TOOL, lambda arguments: arguments)
    result = WorkflowEngine(
        InMemoryRunJournal(), tools, {"native": FailingDriver()}
    ).run(
        workflow(
            "native-failure",
            "1",
            [agent_step("plan", "native", tools=(_contract(),))],
            schema_version="v6-light-workflow-v2",
        ),
        run_id="native-failure-run",
    )

    assert result.status == "BLOCKED"
    assert result.state["failure"] == {
        "code": "AGENT_MODEL_OUTPUT_INVALID",
        "step_id": "plan",
    }


def test_v2_kernel_rechecks_exact_tool_scope_after_driver_translation() -> None:
    driver = _RecordingDriver(
        [AgentDecision("", (ToolCall(OTHER_TOOL, {"query": "outside"}),))]
    )
    tools = LocalToolExecutor()
    tools.register(TOOL, lambda arguments: arguments)
    result = WorkflowEngine(InMemoryRunJournal(), tools, {"native": driver}).run(
        workflow(
            "native-scope",
            "1",
            [agent_step("plan", "native", tools=(_contract(),))],
            schema_version="v6-light-workflow-v2",
        ),
        run_id="native-scope-run",
    )

    assert result.status == "BLOCKED"
    assert result.state["failure"]["code"] == "AGENT_TOOL_SCOPE_DENIED"


def test_bundle_extracts_nested_v2_tool_reference(tmp_path: Path) -> None:
    spec = workflow(
        "portable-native",
        "1",
        [agent_step("plan", "native", tools=(_contract(),))],
        schema_version="v6-light-workflow-v2",
    )

    with pytest.raises(WorkflowValidationError, match="declare"):
        export_bundle(tmp_path / "missing.v6bundle", spec)

    bundle_path = tmp_path / "native.v6bundle"
    exported = export_bundle(
        bundle_path,
        spec,
        tools=[TOOL.to_mapping()],
    )
    assert exported.workflow == spec
    assert exported.manifest["schema_version"] == "v6-light-bundle-v1"
    assert import_bundle(bundle_path, tmp_path / "imported").workflow == spec
