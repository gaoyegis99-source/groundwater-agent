from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from platform_lightweight_agent import (
    AgentBudget,
    AgentDecision,
    ApprovalDecision,
    DomainLock,
    FunctionToolExecutor,
    InMemoryRunJournal,
    NodeContract,
    PortableTarget,
    SQLiteRunJournal,
    ScriptedAgentDriver,
    ToolReference,
    ValidationIssueV1,
    ValidationReportV1,
    ValueBinding,
    WorkflowSpec,
    WorkflowValidationError,
    agent_step,
    approval_step,
    check_domain_pack,
    compile_domain_pack,
    condition_step,
    export_project_bundle,
    import_bundle_project,
    load_domain_lock,
    load_project_runtime,
    load_resume_runtime,
    tool_step,
    workflow,
    workflow_v4,
    WorkflowEngine,
)
from platform_lightweight_agent.cli import main as cli_main

DIALECT = "https://json-schema.org/draft/2020-12/schema"
LOCK = 'lock-version = "1.0"\ncreated-by = "uv"\nrequires-python = ">=3.12"\n'
EXAMPLE_PACKS = Path(__file__).parents[1] / "examples" / "domain-packs"


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _schema(properties: dict, required: list[str]) -> dict:
    return {
        "$schema": DIALECT,
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _domain_workflow(
    domain_id: str,
) -> tuple[object, tuple[ToolReference, ToolReference, ToolReference]]:
    """Build the minimal required-check topology used by both example domains."""

    report = ValidationReportV1.schema(f"{domain_id}.input-v1")
    workflow_input = _schema({"value": {"type": "number"}}, ["value"])
    approval_input = _schema({}, [])
    approval_output = _schema({"approved": {"type": "boolean"}}, ["approved"])
    condition_input = _schema(
        {"status": {"enum": ["PASS", "FAIL", "UNKNOWN"]}}, ["status"]
    )
    final_output = _schema(
        {
            "business_status": {"enum": ["APPROVED", "BLOCKED"]},
            "result": {"type": ["number", "null"]},
            "validation": report,
        },
        ["business_status", "result", "validation"],
    )
    validate_tool = ToolReference(
        f"{domain_id}.validate", "1", "sha256:" + "a" * 63 + "1"
    )
    calculate_tool = ToolReference(
        f"{domain_id}.calculate", "1", "sha256:" + "b" * 63 + "2"
    )
    blocked_tool = ToolReference(
        f"{domain_id}.blocked", "1", "sha256:" + "c" * 63 + "3"
    )
    validate = tool_step(
        "validate",
        validate_tool,
        approval_required=False,
        contract=NodeContract(
            workflow_input,
            report,
            {"/value": ValueBinding("workflow_input", pointer="/value")},
        ),
    )
    approval = approval_step(
        "approve",
        "Review the checked analysis before calculation.",
        contract=NodeContract(approval_input, approval_output),
    )
    calculate = tool_step(
        "calculate",
        calculate_tool,
        approval_required=False,
        contract=NodeContract(
            _schema(
                {"value": {"type": "number"}, "validation": report},
                ["value", "validation"],
            ),
            final_output,
            {
                "/value": ValueBinding("workflow_input", pointer="/value"),
                "/validation": ValueBinding(
                    "step_output", pointer="", step_id="validate"
                ),
            },
        ),
    )
    blocked = tool_step(
        "blocked",
        blocked_tool,
        approval_required=False,
        contract=NodeContract(
            _schema({"validation": report}, ["validation"]),
            final_output,
            {
                "/validation": ValueBinding(
                    "step_output", pointer="", step_id="validate"
                )
            },
        ),
    )
    gate = condition_step(
        "gate",
        "/status",
        "PASS",
        (approval, calculate),
        (blocked,),
        contract=NodeContract(
            condition_input,
            final_output,
            {
                "/status": ValueBinding(
                    "step_output", pointer="/status", step_id="validate"
                )
            },
        ),
    )
    return (
        workflow(
            f"{domain_id}.analysis",
            "1",
            (validate, gate),
            schema_version="v6-light-workflow-v3",
            input_schema=workflow_input,
            output_schema=final_output,
            output_bindings={
                "/business_status": ValueBinding(
                    "step_output", pointer="/business_status", step_id="gate"
                ),
                "/result": ValueBinding(
                    "step_output", pointer="/result", step_id="gate"
                ),
                "/validation": ValueBinding(
                    "step_output", pointer="/validation", step_id="gate"
                ),
            },
        ),
        (validate_tool, calculate_tool, blocked_tool),
    )


def _write_domain_pack(
    root: Path, domain_id: str
) -> tuple[object, tuple[ToolReference, ...]]:
    root.mkdir()
    workflow_spec, tools = _domain_workflow(domain_id)
    workflow_path = root / "workflows" / "analysis.json"
    workflow_path.parent.mkdir()
    workflow_path.write_text(
        json.dumps(workflow_spec.to_mapping()),
        encoding="utf-8",  # type: ignore[union-attr]
    )
    resources = root / "resources"
    resources.mkdir()
    method = resources / "method.md"
    method.write_text(f"{domain_id} method constraints", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    cases = tests / "cases.json"
    cases.write_text("[]\n", encoding="utf-8")
    manifests = [
        {
            **tool.to_mapping(),
            "permissions": ["files.read"] if index == 0 else ["files.write"],
        }
        for index, tool in enumerate(tools)
    ]
    (root / "tools.json").write_text(json.dumps(manifests), encoding="utf-8")
    (root / "pylock.toml").write_text(LOCK, encoding="utf-8")
    pack = {
        "schema_version": "v6-domain-pack-v1",
        "domain_id": domain_id,
        "version": "1",
        "entry_workflows": [
            {
                "name": "analysis",
                "workflow": "workflows/analysis.json",
                "workflow_id": workflow_spec.workflow_id,  # type: ignore[union-attr]
                "version": workflow_spec.version,  # type: ignore[union-attr]
                "definition_digest": workflow_spec.definition_digest,  # type: ignore[union-attr]
                "required_checks": [
                    {
                        "rule_ref": f"{domain_id}.input-v1",
                        "step_id": "validate",
                        "condition_step_id": "gate",
                        "protected_steps": ["calculate"],
                    }
                ],
            }
        ],
        "subworkflows": [],
        "tool_catalog": "tools.json",
        "dependency_lock": "pylock.toml",
        "tools": [tool.to_mapping() for tool in tools],
        "requested_permissions": ["files.read", "files.write"],
        "description": {
            "terms": {"value": "A normalized local measurement."},
            "applicability": ["A controlled local example."],
            "limitations": ["Not a production scientific result."],
        },
        "checks": [
            {
                "rule_ref": f"{domain_id}.input-v1",
                "required": True,
                "description": "Value must be non-negative.",
                "input_paths": ["/value"],
                "argument_paths": ["/value"],
            }
        ],
        "parameter_profiles": [
            {"name": "standard", "defaults": {"multiplier": 2}},
            {"name": "development", "defaults": {"multiplier": 1}},
        ],
        "resources": [
            {
                "path": "resources/method.md",
                "sha256": _digest(method.read_bytes()),
                "role": "method",
            }
        ],
        "tests": [
            {
                "path": "tests/cases.json",
                "sha256": _digest(cases.read_bytes()),
                "role": "scenario",
            }
        ],
        "evaluation_cases": "tests/cases.json",
        "context_policy": {
            "schema_version": "v6-context-selection-policy-v1",
            "strategy": "declared-step-inputs-v1",
            "resources": ["resources/method.md"],
        },
        "driver_capabilities": [],
        "agent_instructions": {},
    }
    (root / "domain.json").write_text(json.dumps(pack), encoding="utf-8")
    return workflow_spec, tools


def _target(tools: tuple[ToolReference, ...]) -> PortableTarget:
    return PortableTarget(
        trusted_tools=tools,
        allowed_permissions=frozenset({"files.read", "files.write"}),
    )


def _rewrite_entry_workflow(source: Path, mutate) -> None:  # type: ignore[no-untyped-def]
    workflow_path = source / "workflows" / "analysis.json"
    raw = json.loads(workflow_path.read_text(encoding="utf-8"))
    mutate(raw)
    updated = WorkflowSpec.from_mapping(raw)
    workflow_path.write_text(json.dumps(updated.to_mapping()), encoding="utf-8")
    declaration_path = source / "domain.json"
    declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
    declaration["entry_workflows"][0]["definition_digest"] = updated.definition_digest
    declaration_path.write_text(json.dumps(declaration), encoding="utf-8")


def _approval_id(snapshot) -> str:  # type: ignore[no-untyped-def]
    pending = snapshot.state["v3"]["pending_approvals"]
    return next(iter(pending))


def test_two_domain_packs_compile_and_recover_with_one_v6_runtime(
    tmp_path: Path,
) -> None:
    groundwater_source = tmp_path / "groundwater-domain"
    statistics_source = tmp_path / "statistics-domain"
    _groundwater_workflow, groundwater_tools = _write_domain_pack(
        groundwater_source, "groundwater"
    )
    _statistics_workflow, statistics_tools = _write_domain_pack(
        statistics_source, "csv-statistics"
    )
    groundwater = compile_domain_pack(groundwater_source, tmp_path / "groundwater")
    statistics = compile_domain_pack(statistics_source, tmp_path / "statistics")

    assert set(groundwater.projects) == {"analysis"}
    assert set(statistics.projects) == {"analysis"}
    domain_lock = load_domain_lock(
        groundwater.projects["analysis"].domain_lock_path  # type: ignore[arg-type]
    )
    assert domain_lock.entry.workflow_id == "groundwater.analysis"
    assert domain_lock.context_policy.strategy == "declared-step-inputs-v1"

    calls: dict[str, int] = {"calculate": 0, "blocked": 0}
    executor = FunctionToolExecutor()

    def register_tools(tools: tuple[ToolReference, ...], rule_ref: str) -> None:
        validate_tool, calculate_tool, blocked_tool = tools

        def validate(arguments):  # type: ignore[no-untyped-def]
            if arguments["value"] >= 0:
                return ValidationReportV1.passed(
                    rule_ref, arguments, arguments
                ).to_mapping()
            return ValidationReportV1.failed(
                "FAIL",
                rule_ref,
                arguments,
                arguments,
                (
                    ValidationIssueV1(
                        "/value", "NEGATIVE_VALUE", "Value must be non-negative."
                    ),
                ),
            ).to_mapping()

        def calculate(arguments):  # type: ignore[no-untyped-def]
            report = ValidationReportV1.from_mapping(arguments["validation"])
            assert report.matches(
                rule_ref=rule_ref,
                inputs={"value": arguments["value"]},
                arguments={"value": arguments["value"]},
            )
            calls["calculate"] += 1
            return {
                "business_status": "APPROVED",
                "result": arguments["value"] * 2,
                "validation": arguments["validation"],
            }

        def blocked(arguments):  # type: ignore[no-untyped-def]
            calls["blocked"] += 1
            return {
                "business_status": "BLOCKED",
                "result": None,
                "validation": arguments["validation"],
            }

        executor.register(validate_tool, validate)
        executor.register(calculate_tool, calculate)
        executor.register(blocked_tool, blocked)

    register_tools(groundwater_tools, "groundwater.input-v1")
    register_tools(statistics_tools, "csv-statistics.input-v1")
    target = _target((*groundwater_tools, *statistics_tools))
    journal = SQLiteRunJournal(tmp_path / "runs.sqlite")

    async def exercise() -> None:
        runtime = load_project_runtime(
            groundwater.projects["analysis"], target, journal, executor=executor
        )
        waiting = await runtime.engine.run_async(
            runtime.workflow, {"value": 3}, run_id="groundwater-approved"
        )
        assert waiting.status == "WAITING_APPROVAL"
        assert (
            waiting.execution_manifest["schema_version"] == "v6-execution-manifest-v3"
        )
        assert (
            waiting.execution_manifest["runtime_binding"]["domain_lock"]
            == domain_lock.to_mapping()
        )

        # A process restart rebuilds only the existing runtime from the frozen
        # manifest.  It does not recompile the mutable source declaration.
        resumed = load_resume_runtime(
            groundwater.projects["analysis"],
            target,
            journal,
            waiting.run_id,
            executor=executor,
        )
        done = await resumed.engine.resume_async(
            waiting.run_id,
            approvals=(ApprovalDecision(_approval_id(waiting), True),),
        )
        assert done.status == "SUCCEEDED"
        assert done.state["v3"]["output"]["business_status"] == "APPROVED"
        assert done.state["v3"]["output"]["result"] == 6
        assert calls["calculate"] == 1

        statistics_runtime = load_project_runtime(
            statistics.projects["analysis"], target, journal, executor=executor
        )
        blocked = await statistics_runtime.engine.run_async(
            statistics_runtime.workflow, {"value": -2}, run_id="statistics-blocked"
        )
        assert blocked.status == "SUCCEEDED"
        assert blocked.state["v3"]["output"]["business_status"] == "BLOCKED"
        assert calls["calculate"] == 1
        assert calls["blocked"] == 1

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("name", "workflow_id", "parameter_profile"),
    (
        ("groundwater", "groundwater.analysis", None),
        ("csv-statistics", "csv-statistics.analysis", "small"),
    ),
)
def test_checked_example_domain_packs_compile_to_existing_projects(
    tmp_path: Path,
    name: str,
    workflow_id: str,
    parameter_profile: str | None,
) -> None:
    """The developer-facing examples are checked source packs, not prose only."""

    compilation = compile_domain_pack(
        EXAMPLE_PACKS / name,
        tmp_path / name,
        parameter_profile=parameter_profile,
    )
    project = compilation.projects["analysis"]
    assert project.workflow_path is not None
    assert project.workflow_path.name == "workflow.json"
    assert compilation.locks["analysis"].entry.workflow_id == workflow_id
    assert project.domain_lock_path is not None
    assert project.domain_lock_path.is_file()


def test_csv_parameter_profile_is_explicit_and_frozen_into_node_bindings(
    tmp_path: Path,
) -> None:
    source = EXAMPLE_PACKS / "csv-statistics"
    check_domain_pack(source)

    with pytest.raises(WorkflowValidationError, match="explicit parameter_profile"):
        compile_domain_pack(source, tmp_path / "missing")
    with pytest.raises(WorkflowValidationError, match="not declared"):
        compile_domain_pack(
            source,
            tmp_path / "unknown",
            parameter_profile="unbounded",
        )

    compilation = compile_domain_pack(
        source,
        tmp_path / "compiled",
        parameter_profile="small",
    )
    lock = compilation.locks["analysis"]
    assert lock.parameter_profile is not None
    assert lock.parameter_profile.name == "small"
    workflow_value = WorkflowSpec.from_mapping(
        json.loads(
            compilation.projects["analysis"].workflow_path.read_text(encoding="utf-8")  # type: ignore[union-attr]
        )
    )
    validate = workflow_value.steps[0]
    calculate = workflow_value.steps[1].children[0][1]
    expected = {
        "max_input_bytes": 1_048_576,
        "max_rows": 10_000,
        "round_digits": 3,
    }
    assert validate.contract is not None
    assert calculate.contract is not None
    assert validate.contract.bindings["/profile"].value == expected
    assert calculate.contract.bindings["/profile"].value == expected
    assert "$domain_parameter_profile" not in json.dumps(workflow_value.to_mapping())


def _write_agent_context_domain(root: Path) -> None:
    _write_domain_pack(root, "context-domain")
    resource_schema = _schema(
        {
            "schema_version": {"const": "v6-domain-agent-context-v1"},
            "resources": {
                "type": "array",
                "items": _schema(
                    {
                        "path": {"type": "string"},
                        "sha256": {
                            "type": "string",
                            "pattern": "^sha256:[0-9a-f]{64}$",
                        },
                        "role": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    ["path", "sha256", "role", "content"],
                ),
            },
        },
        ["schema_version", "resources"],
    )
    workflow_input = _schema({"request": {"type": "string"}}, ["request"])
    reply = _schema({"message": {"type": "string"}}, ["message"])
    step = agent_step(
        "answer",
        "primary",
        tools=(),
        budget=AgentBudget(),
        contract=NodeContract(
            _schema(
                {
                    "request": {"type": "string"},
                    "domain_context": resource_schema,
                },
                ["request", "domain_context"],
            ),
            reply,
            {
                "/request": ValueBinding("workflow_input", pointer="/request"),
                "/domain_context": ValueBinding(
                    "literal", value={"$domain_context": True}
                ),
            },
        ),
    )
    spec = workflow_v4(
        "context-domain.answer",
        "1",
        (step,),
        input_schema=workflow_input,
        output_schema=reply,
        output_bindings={
            "/message": ValueBinding(
                "step_output", step_id="answer", pointer="/message"
            )
        },
    )
    workflow_path = root / "workflows" / "analysis.json"
    workflow_path.write_text(json.dumps(spec.to_mapping()), encoding="utf-8")
    instruction = root / "instructions" / "answer.md"
    instruction.parent.mkdir()
    instruction.write_text(
        "Answer only from the declared domain context.", encoding="utf-8"
    )
    (root / "tools.json").write_text("[]\n", encoding="utf-8")
    declaration_path = root / "domain.json"
    declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
    declaration["entry_workflows"][0].update(
        {
            "workflow_id": spec.workflow_id,
            "version": spec.version,
            "definition_digest": spec.definition_digest,
            "required_checks": [],
        }
    )
    declaration["tools"] = []
    declaration["requested_permissions"] = []
    declaration["checks"] = []
    declaration["context_policy"] = {
        "schema_version": "v6-context-selection-policy-v2",
        "strategy": "declared-agent-resources-v1",
        "resources": ["resources/method.md"],
        "agent_steps": ["context-domain.answer@1:answer"],
        "max_bytes": 4096,
    }
    declaration["driver_capabilities"] = ["structured_output"]
    declaration["agent_instructions"] = {
        "context-domain.answer@1:answer": "instructions/answer.md"
    }
    declaration_path.write_text(json.dumps(declaration), encoding="utf-8")


def test_agent_context_resources_are_scoped_bounded_and_materialized(
    tmp_path: Path,
) -> None:
    source = tmp_path / "domain"
    _write_agent_context_domain(source)
    checked = check_domain_pack(source)
    assert checked.context_policy.agent_steps == ("context-domain.answer@1:answer",)

    compiled = compile_domain_pack(source, tmp_path / "compiled")
    raw = json.loads(
        compiled.projects["analysis"].workflow_path.read_text(encoding="utf-8")  # type: ignore[union-attr]
    )
    binding = raw["steps"][0]["contract"]["bindings"]["/domain_context"]
    payload = binding["value"]
    assert payload["schema_version"] == "v6-domain-agent-context-v1"
    assert payload["resources"] == [
        {
            "path": "resources/method.md",
            "sha256": checked.resources[0].sha256,
            "role": "method",
            "content": "context-domain method constraints",
        }
    ]
    assert "$domain_context" not in json.dumps(raw)

    class CapturingDriver(ScriptedAgentDriver):
        def __init__(self) -> None:
            super().__init__((AgentDecision("", output={"message": "grounded"}),))
            self.contexts = []

        async def decide_async(self, context):  # type: ignore[no-untyped-def]
            self.contexts.append(context)
            return await super().decide_async(context)

    driver = CapturingDriver()
    engine = WorkflowEngine(
        InMemoryRunJournal(), FunctionToolExecutor(), {"primary": driver}
    )
    completed = asyncio.run(
        engine.run_async(
            WorkflowSpec.from_mapping(raw),
            {"request": "Use the method."},
            run_id="context-consumption",
        )
    )
    assert completed.status == "SUCCEEDED"
    assert driver.contexts[0].step_input["domain_context"] == payload

    unknown = json.loads((source / "domain.json").read_text(encoding="utf-8"))
    unknown["context_policy"]["agent_steps"] = ["context-domain.answer@1:not-declared"]
    (source / "domain.json").write_text(json.dumps(unknown), encoding="utf-8")
    with pytest.raises(WorkflowValidationError, match="unknown Agent"):
        check_domain_pack(source)

    unknown["context_policy"]["agent_steps"] = ["context-domain.answer@1:answer"]
    unknown["context_policy"]["max_bytes"] = 32
    (source / "domain.json").write_text(json.dumps(unknown), encoding="utf-8")
    with pytest.raises(WorkflowValidationError, match="exceeds its declared"):
        check_domain_pack(source)


def test_domain_lock_survives_existing_project_bundle_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "domain"
    _workflow_spec, tools = _write_domain_pack(source, "groundwater")
    compiled = compile_domain_pack(source, tmp_path / "compiled")
    original = compiled.projects["analysis"]
    archive = tmp_path / "domain.v6.zip"

    export_project_bundle(original.root, archive)
    restored = import_bundle_project(
        archive,
        tmp_path / "restored",
        _target(tools),
        check_dependencies=False,
    )

    assert restored.domain_lock_path is not None
    assert (
        load_domain_lock(restored.domain_lock_path).to_mapping()
        == load_domain_lock(
            original.domain_lock_path  # type: ignore[arg-type]
        ).to_mapping()
    )

    # A compiled project needs the normal workflow runtime but never invokes
    # source-pack JSON Schema validation on its run/resume path.
    import platform_lightweight_agent.domain as domain_module

    monkeypatch.setattr(
        domain_module,
        "_require_authoring_jsonschema",
        lambda: pytest.fail("compiled domain runtime loaded authoring validation"),
    )
    runtime = load_project_runtime(
        restored,
        _target(tools),
        InMemoryRunJournal(),
        executor=FunctionToolExecutor(),
    )
    assert runtime.workflow.workflow_id == "groundwater.analysis"


def test_domain_lock_rejects_mutation_and_revoked_target_permission(
    tmp_path: Path,
) -> None:
    source = tmp_path / "domain"
    _workflow_spec, tools = _write_domain_pack(source, "groundwater")
    compiled = compile_domain_pack(source, tmp_path / "compiled")
    project = compiled.projects["analysis"]

    # The compiled project validates every declared resource against the lock;
    # replacing a document cannot silently preserve a domain identity.
    method = project.root / "resources" / "method.md"
    method.write_text("replacement", encoding="utf-8")
    with pytest.raises(WorkflowValidationError, match="no longer matches"):
        load_project_runtime(
            project,
            _target(tools),
            SQLiteRunJournal(tmp_path / "runs.sqlite"),
            executor=FunctionToolExecutor(),
        )

    # Source-pack checks likewise reject a changed source asset before compile.
    source_method = source / "resources" / "method.md"
    source_method.write_text("changed source", encoding="utf-8")
    with pytest.raises(WorkflowValidationError, match="digest does not match"):
        check_domain_pack(source)

    # Fresh host authorization is checked at every assembly.  A domain lock
    # cannot carry a former permission grant across environments.
    clean_source = tmp_path / "clean-domain"
    _workflow_spec, clean_tools = _write_domain_pack(clean_source, "csv-statistics")
    clean = compile_domain_pack(clean_source, tmp_path / "clean-compiled")
    denied = PortableTarget(
        trusted_tools=clean_tools,
        allowed_permissions=frozenset({"files.read"}),
    )
    with pytest.raises(WorkflowValidationError, match="target does not grant"):
        load_project_runtime(
            clean.projects["analysis"],
            denied,
            SQLiteRunJournal(tmp_path / "denied.sqlite"),
            executor=FunctionToolExecutor(),
        )


def test_domain_pack_rejects_code_bearing_metadata_and_unused_driver_requirements(
    tmp_path: Path,
) -> None:
    source = tmp_path / "domain"
    _workflow_spec, _tools = _write_domain_pack(source, "groundwater")

    raw = json.loads((source / "domain.json").read_text(encoding="utf-8"))
    raw["tool_registry"] = "example.module:configure"
    (source / "domain.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(WorkflowValidationError, match="domain pack schema"):
        check_domain_pack(source)

    source = tmp_path / "credential-domain"
    _workflow_spec, _tools = _write_domain_pack(source, "groundwater")
    catalog = json.loads((source / "tools.json").read_text(encoding="utf-8"))
    catalog[0]["credential_ref"] = "external-service"
    (source / "tools.json").write_text(json.dumps(catalog), encoding="utf-8")
    with pytest.raises(WorkflowValidationError, match="credential references"):
        check_domain_pack(source)

    source = tmp_path / "driver-domain"
    _workflow_spec, _tools = _write_domain_pack(source, "groundwater")
    raw = json.loads((source / "domain.json").read_text(encoding="utf-8"))
    raw["driver_capabilities"] = ["structured_output"]
    (source / "domain.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(WorkflowValidationError, match="require an Agent definition"):
        check_domain_pack(source)


def test_validation_report_binds_the_checked_input_and_arguments() -> None:
    report = ValidationReportV1.passed(
        "groundwater.input-v1", {"value": 4}, {"value": 4}
    )
    assert report.matches(
        rule_ref="groundwater.input-v1",
        inputs={"value": 4},
        arguments={"value": 4},
    )
    assert not report.matches(
        rule_ref="groundwater.input-v1",
        inputs={"value": 5},
        arguments={"value": 4},
    )
    with pytest.raises(WorkflowValidationError, match="cannot contain issues"):
        ValidationReportV1(
            "PASS",
            "groundwater.input-v1",
            report.input_digest,
            report.arguments_digest,
            (ValidationIssueV1("/value", "INVALID", "unexpected"),),
        )


def test_required_domain_gate_rejects_swapped_pass_and_block_branches(
    tmp_path: Path,
) -> None:
    source = tmp_path / "domain"
    _write_domain_pack(source, "groundwater")

    def swap(raw):  # type: ignore[no-untyped-def]
        gate = raw["steps"][1]
        gate["then"], gate["otherwise"] = gate["otherwise"], gate["then"]

    _rewrite_entry_workflow(source, swap)

    with pytest.raises(
        WorkflowValidationError, match="reachable without the PASS gate"
    ):
        check_domain_pack(source)


def test_required_domain_gate_binds_rule_receipt_and_checked_values(
    tmp_path: Path,
) -> None:
    source = tmp_path / "domain"
    _write_domain_pack(source, "groundwater")

    def replace_value_binding(raw):  # type: ignore[no-untyped-def]
        calculate = raw["steps"][1]["then"][1]
        calculate["contract"]["bindings"]["/value"] = {
            "source": "literal",
            "value": 9,
        }

    _rewrite_entry_workflow(source, replace_value_binding)
    with pytest.raises(WorkflowValidationError, match="same checked inputs"):
        check_domain_pack(source)

    source = tmp_path / "wrong-rule-domain"
    _write_domain_pack(source, "groundwater")

    def loosen_rule(raw):  # type: ignore[no-untyped-def]
        raw["steps"][0]["contract"]["output_schema"] = ValidationReportV1.schema()

    _rewrite_entry_workflow(source, loosen_rule)
    with pytest.raises(WorkflowValidationError, match="rule-bound"):
        check_domain_pack(source)


def test_domain_manifest_protocol_does_not_migrate_existing_projects() -> None:
    """A domain lock is opt-in; ordinary existing projects stay on v2."""

    legacy = workflow(
        "legacy-project",
        "1",
        (approval_step("approve", "ok"),),
        schema_version="v6-light-workflow-v1",
    )
    engine = WorkflowEngine(InMemoryRunJournal(), FunctionToolExecutor(), {})
    waiting = engine.run(legacy, {}, run_id="legacy")
    assert waiting.execution_manifest["schema_version"] == "v6-execution-manifest-v2"
    assert engine.resume("legacy", approved=True).status == "SUCCEEDED"


def test_cli_domain_check_and_compile_are_authoring_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "domain"
    _workflow_spec, _tools = _write_domain_pack(source, "groundwater")
    assert cli_main(["domain", "check", str(source)]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["effects"] == "not_executed"
    assert checked["domain_id"] == "groundwater"

    output = tmp_path / "compiled"
    assert cli_main(["domain", "compile", str(source), str(output)]) == 0
    compiled = json.loads(capsys.readouterr().out)
    assert compiled["schema_version"] == "v6-domain-compilation-v1"
    assert (output / "entries" / "analysis" / "pyproject.toml").is_file()

    csv_output = tmp_path / "csv-compiled"
    assert (
        cli_main(
            [
                "domain",
                "compile",
                str(EXAMPLE_PACKS / "csv-statistics"),
                str(csv_output),
                "--parameter-profile",
                "small",
            ]
        )
        == 0
    )
    csv_compiled = json.loads(capsys.readouterr().out)
    assert csv_compiled["parameter_profile"]["name"] == "small"


def test_domain_lock_wire_contract_is_strict(tmp_path: Path) -> None:
    source = tmp_path / "domain"
    _workflow_spec, _tools = _write_domain_pack(source, "groundwater")
    compiled = compile_domain_pack(source, tmp_path / "compiled")
    lock_path = compiled.projects["analysis"].domain_lock_path
    assert lock_path is not None
    original = json.loads(lock_path.read_text(encoding="utf-8"))
    raw = dict(original)
    raw["unexpected"] = True
    lock_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(WorkflowValidationError, match="domain lock is malformed"):
        load_domain_lock(lock_path)
    with pytest.raises(WorkflowValidationError, match="domain lock is malformed"):
        DomainLock.from_mapping(raw)

    raw = json.loads(json.dumps(original))
    del raw["entry"]["required_checks"]
    with pytest.raises(WorkflowValidationError, match="domain entry workflow"):
        DomainLock.from_mapping(raw)
