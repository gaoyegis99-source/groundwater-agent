from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from platform_lightweight_agent import (
    BUNDLE_V2,
    AgentBudget,
    AgentDecision,
    AgentToolSpec,
    ApprovalDecision,
    DriverBinding,
    DriverFactory,
    InMemoryRunJournal,
    NodeContract,
    PortableProject,
    PortableTarget,
    SQLiteRunJournal,
    ToolCall,
    ToolExecutionRequest,
    ToolReference,
    ValueBinding,
    WorkflowEngine,
    WorkflowValidationError,
    agent_step,
    approval_step,
    export_bundle,
    import_bundle_project,
    load_project_runtime,
    load_resume_runtime,
    subworkflow_step,
    tool_step,
    workflow,
)
from platform_lightweight_agent import runtime as runtime_module
from platform_lightweight_agent.cli import main as cli_main


class _BoundDriver:
    driver_version = "pydantic-ai-native-v2@test"

    def __init__(self, instructions: str) -> None:
        self.instructions = instructions
        self.contexts = []

    def execution_binding(self):  # type: ignore[no-untyped-def]
        return {
            "driver_version": self.driver_version,
            "provider": "pydantic-ai",
            "model": "test:model",
            "instructions": self.instructions,
            "protocol": "native-tools-v2",
        }

    def decide(self, context):  # type: ignore[no-untyped-def]
        self.contexts.append(context)
        return AgentDecision("project ran")

    async def decide_async(self, context):  # type: ignore[no-untyped-def]
        return self.decide(context)


def _project(root: Path) -> None:
    (root / "instructions").mkdir(parents=True)
    (root / "instructions" / "answer.md").write_text(
        "original frozen instructions", encoding="utf-8"
    )
    (root / "workflow.json").write_text(
        json.dumps(
            {
                "schema_version": "v6-light-workflow-v2",
                "workflow_id": "runtime-project",
                "version": "1",
                "steps": [
                    {
                        "id": "answer",
                        "kind": "agent",
                        "config": {
                            "driver": "primary",
                            "input": {},
                            "max_turns": 1,
                            "tools": [],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / "tools.json").write_text("[]", encoding="utf-8")
    (root / "cases.json").write_text("[]", encoding="utf-8")
    (root / "pylock.toml").write_text(
        'lock-version = "1.0"\ncreated-by = "uv"\nrequires-python = ">=3.12"\n',
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        """[project]
name = "runtime-project"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = []

[tool.platform-agent-workflow]
schema_version = "v6-agent-project-v1"
kind = "workflow"
workflow = "workflow.json"
tools = "tools.json"
dependency_lock = "pylock.toml"
files = []
evaluation_cases = "cases.json"

[tool.platform-agent-workflow.agent_instructions]
answer = "instructions/answer.md"

[tool.platform-agent-workflow.subworkflows]
""",
        encoding="utf-8",
    )


def _target() -> PortableTarget:
    return PortableTarget(
        driver_bindings=(
            DriverBinding(
                "answer",
                "pydantic-ai",
                "pydantic-ai-native-v2@test",
                "test:model",
                frozenset(
                    {
                        "host_managed_execution",
                        "native_tools",
                        "structured_output",
                    }
                ),
            ),
        )
    )


def _target_with_tool_bindings(
    *,
    permissions: frozenset[str] = frozenset(),
    credentials: frozenset[str] = frozenset(),
) -> PortableTarget:
    base = _target()
    return PortableTarget(
        allowed_permissions=permissions,
        credential_bindings=credentials,
        driver_bindings=base.driver_bindings,
    )


def test_project_runtime_runs_and_resume_uses_frozen_instructions(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    project = tmp_path / "project"
    project.mkdir()
    _project(project)
    seen: list[str] = []

    def adapter(binding, instructions):  # type: ignore[no-untyped-def]
        del binding
        seen.append(instructions)
        return _BoundDriver(instructions)

    monkeypatch.setattr(runtime_module, "_adapter", adapter)
    journal = SQLiteRunJournal(tmp_path / "runs.sqlite")
    runtime = load_project_runtime(project, _target(), journal)
    completed = runtime.engine.run(runtime.workflow, {}, run_id="project-run")
    assert completed.status == "SUCCEEDED"
    assert completed.state["values"]["answer"] == {"message": "project ran"}

    (project / "instructions" / "answer.md").write_text(
        "mutable replacement", encoding="utf-8"
    )
    (project / "workflow.json").write_text("not the frozen workflow", encoding="utf-8")
    (project / "pyproject.toml").write_text("not mutable config", encoding="utf-8")
    resumed_runtime = load_resume_runtime(project, _target(), journal, completed.run_id)
    resumed = resumed_runtime.engine.resume(completed.run_id)

    assert resumed.status == "SUCCEEDED"
    assert seen == ["original frozen instructions", "original frozen instructions"]
    with pytest.raises(WorkflowValidationError, match="frozen runtime assembly"):
        load_resume_runtime(
            project,
            _target(),
            journal,
            completed.run_id,
            registries=("changed:register",),
        )


def test_custom_driver_requires_an_explicit_versioned_host_factory(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _project(project)
    version = "acme-native-v2@1.2.3"
    capabilities = frozenset(
        {"host_managed_execution", "native_tools", "structured_output"}
    )
    target = PortableTarget(
        driver_bindings=(
            DriverBinding(
                "answer", "acme", version, "acme:model", capabilities
            ),
        )
    )

    class CustomDriver(_BoundDriver):
        driver_version = version

        def execution_binding(self):  # type: ignore[no-untyped-def]
            return {
                "driver_version": self.driver_version,
                "provider": "acme",
                "model": "acme:model",
                "instructions": self.instructions,
                "protocol": "native-tools-v2",
            }

    factory = DriverFactory(
        "acme",
        version,
        capabilities,
        (),
        lambda _binding, instructions: CustomDriver(instructions),
    )
    journal = SQLiteRunJournal(tmp_path / "runs.sqlite")

    with pytest.raises(WorkflowValidationError, match="not authorized"):
        load_project_runtime(project, target, journal)

    runtime = load_project_runtime(
        project, target, journal, driver_factories={"acme": factory}
    )
    completed = runtime.engine.run(runtime.workflow, {}, run_id="custom-driver")
    assert completed.status == "SUCCEEDED"

    with pytest.raises(WorkflowValidationError, match="not authorized"):
        load_resume_runtime(project, target, journal, completed.run_id)
    resumed = load_resume_runtime(
        project,
        target,
        journal,
        completed.run_id,
        driver_factories={"acme": factory},
    ).engine.resume(completed.run_id)
    assert resumed.status == "SUCCEEDED"


def test_runtime_freezes_and_enforces_tool_manifest_bindings(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    project = tmp_path / "project"
    project.mkdir()
    _project(project)
    manifest = {
        "tool_id": "example.lookup",
        "version": "1.0.0",
        "package_digest": "sha256:" + "a" * 64,
        "permissions": ["filesystem.read"],
        "credential_ref": "lookup_binding",
    }
    (project / "tools.json").write_text(json.dumps([manifest]), encoding="utf-8")
    monkeypatch.setattr(
        runtime_module,
        "_adapter",
        lambda binding, instructions: _BoundDriver(instructions),
    )
    journal = SQLiteRunJournal(tmp_path / "runs.sqlite")

    with pytest.raises(WorkflowValidationError, match="permission"):
        load_project_runtime(project, _target(), journal)
    with pytest.raises(WorkflowValidationError, match="credential"):
        load_project_runtime(
            project,
            _target_with_tool_bindings(permissions=frozenset({"filesystem.read"})),
            journal,
        )

    target = _target_with_tool_bindings(
        permissions=frozenset({"filesystem.read"}),
        credentials=frozenset({"lookup_binding"}),
    )
    runtime = load_project_runtime(project, target, journal)
    completed = runtime.engine.run(runtime.workflow, {}, run_id="manifest-run")
    frozen = completed.execution_manifest["runtime_binding"]
    assert frozen == {
        "tool_registries": [],
        "tool_manifests": [manifest],
        "dependency_lock_sha256": "sha256:"
        + hashlib.sha256((project / "pylock.toml").read_bytes()).hexdigest(),
    }

    (project / "tools.json").write_text("not mutable state", encoding="utf-8")
    resumed = load_resume_runtime(
        project, target, journal, completed.run_id
    ).engine.resume(completed.run_id)
    assert resumed.status == "SUCCEEDED"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("permissions", [{}], "permissions"),
        ("credential_ref", 7, "credential_ref"),
    ),
)
def test_runtime_rejects_malformed_tool_security_metadata(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _project(project)
    manifest = {
        "tool_id": "example.lookup",
        "version": "1.0.0",
        "package_digest": "sha256:" + "a" * 64,
        field: value,
    }
    (project / "tools.json").write_text(json.dumps([manifest]), encoding="utf-8")

    with pytest.raises(WorkflowValidationError, match=message):
        load_project_runtime(
            project,
            _target(),
            SQLiteRunJournal(tmp_path / "runs.sqlite"),
        )


def test_project_registries_are_isolated_between_embedding_roots(
    tmp_path: Path,
) -> None:
    tool = ToolReference("example.lookup", "1.0.0", "sha256:" + "b" * 64)
    executors = []
    for label in ("first", "second"):
        root = tmp_path / label
        root.mkdir()
        (root / "runtime_tools.py").write_text(
            "\n".join(
                (
                    "from platform_lightweight_agent import ToolReference",
                    "",
                    "def register(executor):",
                    "    executor.register(",
                    f"        ToolReference('example.lookup', '1.0.0', {tool.package_digest!r}),",
                    f"        lambda arguments: {{'source': {label!r}}},",
                    "    )",
                )
            ),
            encoding="utf-8",
        )
        project = PortableProject(
            root=root,
            kind="workflow",
            workflow_path=None,
            tools_path=None,
            agent_instruction_paths={},
            subworkflow_paths={},
            files=(),
            dependency_lock_path=root / "pylock.toml",
        )
        executors.append(runtime_module._executor(project, ("runtime_tools:register",)))

    outputs = [
        executor.execute(ToolExecutionRequest(f"action-{index}", tool, {})).output
        for index, executor in enumerate(executors)
    ]
    assert outputs == [{"source": "first"}, {"source": "second"}]


def test_standard_project_registry_accepts_native_async_tool_handlers(
    tmp_path: Path,
) -> None:
    """Changing a registry handler to ``async def`` needs no new assembly."""

    reference = ToolReference("project.async", "1", "sha256:" + "c" * 64)
    root = tmp_path / "async-project"
    root.mkdir()
    (root / "runtime_tools.py").write_text(
        "\n".join(
            (
                "from platform_lightweight_agent import ToolReference",
                "",
                "async def lookup(arguments):",
                "    return {'value': arguments['value']}",
                "",
                "def register(executor):",
                "    executor.register(",
                f"        ToolReference('project.async', '1', {reference.package_digest!r}),",
                "        lookup,",
                "    )",
            )
        ),
        encoding="utf-8",
    )
    project = PortableProject(
        root=root,
        kind="workflow",
        workflow_path=None,
        tools_path=None,
        agent_instruction_paths={},
        subworkflow_paths={},
        files=(),
        dependency_lock_path=root / "pylock.toml",
    )
    value = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    spec = workflow(
        "async-project-tool",
        "1",
        (
            tool_step(
                "lookup",
                reference,
                approval_required=False,
                contract=NodeContract(value, value, {"/value": ValueBinding("workflow_input", "/value")}),
            ),
        ),
        schema_version="v6-light-workflow-v3",
        input_schema=value,
        output_schema=value,
        output_bindings={"/value": ValueBinding("step_output", "/value", "lookup")},
    )

    executor = runtime_module._executor(project, ("runtime_tools:register",))
    completed = WorkflowEngine(InMemoryRunJournal(), executor, {}).run(
        spec, {"value": 9}
    )

    assert completed.status == "SUCCEEDED"
    assert completed.state["v3"]["output"] == {"value": 9}


def test_cli_runs_and_resumes_the_same_portable_project(
    tmp_path: Path, monkeypatch, capsys
) -> None:  # type: ignore[no-untyped-def]
    project = tmp_path / "project"
    project.mkdir()
    _project(project)
    target_path = project / "target.json"
    target_path.write_text(json.dumps(_target().to_mapping()), encoding="utf-8")
    monkeypatch.setattr(
        runtime_module,
        "_adapter",
        lambda binding, instructions: _BoundDriver(instructions),
    )
    journal = tmp_path / "cli.sqlite"

    assert (
        cli_main(
            [
                "run",
                str(project),
                "--target-config",
                str(target_path),
                "--journal",
                str(journal),
                "--run-id",
                "cli-project-run",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "SUCCEEDED"
    assert (
        cli_main(
            [
                "resume",
                "cli-project-run",
                "--project",
                str(project),
                "--target-config",
                str(target_path),
                "--journal",
                str(journal),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "SUCCEEDED"


def test_bundle_project_can_be_bound_run_approved_and_reopened(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    empty = _strict_schema({})
    approved = _strict_schema({"approved": {"type": "boolean"}}, ("approved",))
    reply = _strict_schema({"message": {"type": "string"}}, ("message",))
    portable = workflow(
        "portable-runtime",
        "1",
        (
            approval_step(
                "review", "continue?", contract=NodeContract(empty, approved, {})
            ),
            agent_step(
                "answer", "primary", tools=(), contract=NodeContract(empty, reply, {})
            ),
        ),
        schema_version="v6-light-workflow-v3",
        input_schema=empty,
        output_schema=reply,
        output_bindings={"/message": ValueBinding("step_output", "/message", "answer")},
    )
    source = tmp_path / "portable.bundle"
    export_bundle(
        source,
        portable,
        bundle_format=BUNDLE_V2,
        agent_instructions={"answer": "frozen imported instruction"},
        dependency_lock=(
            'lock-version = "1.0"\ncreated-by = "uv"\nrequires-python = ">=3.12"\n'
        ),
    )
    target = PortableTarget(
        driver_bindings=(
            DriverBinding(
                "answer",
                "pydantic-ai",
                "pydantic-ai-native-v2@test",
                "test:model",
                frozenset(
                    {
                        "host_managed_execution",
                        "native_tools",
                        "structured_output",
                    }
                ),
            ),
        )
    )
    project = import_bundle_project(
        source,
        tmp_path / "assembled-project",
        target,
        check_dependencies=False,
    )
    seen: list[str] = []

    def adapter(binding, instructions):  # type: ignore[no-untyped-def]
        del binding
        seen.append(instructions)
        return _BoundDriver(instructions)

    monkeypatch.setattr(runtime_module, "_adapter", adapter)
    journal = SQLiteRunJournal(tmp_path / "portable.sqlite")
    runtime = load_project_runtime(project, target, journal)
    waiting = runtime.engine.run(runtime.workflow, {}, run_id="portable-imported")

    assert waiting.status == "WAITING_APPROVAL"
    approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))
    complete = runtime.engine.resume(
        waiting.run_id,
        approvals=(ApprovalDecision(approval_id, True),),
    )
    assert complete.status == "SUCCEEDED"
    (project.root / "instructions" / "answer.md").write_text(
        "changed after execution", encoding="utf-8"
    )

    reopened = load_resume_runtime(
        project,
        target,
        SQLiteRunJournal(tmp_path / "portable.sqlite"),
        complete.run_id,
    ).engine.resume(complete.run_id)

    assert reopened.status == "SUCCEEDED"
    assert seen == ["frozen imported instruction", "frozen imported instruction"]


def _strict_schema(properties, required=()):  # type: ignore[no-untyped-def]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _nested_project(root: Path, *, instructions: str, v4: bool = False) -> tuple:
    empty = _strict_schema({})
    reply = _strict_schema({"message": {"type": "string"}}, ("message",))
    child = workflow(
        "nested-child",
        "1",
        (
            agent_step(
                "answer",
                "primary",
                tools=(),
                budget=AgentBudget(max_context_bytes=1024) if v4 else None,
                contract=NodeContract(empty, reply, {}),
            ),
        ),
        schema_version=("v6-light-workflow-v4" if v4 else "v6-light-workflow-v3"),
        input_schema=empty,
        output_schema=reply,
        output_bindings={"/message": ValueBinding("step_output", "/message", "answer")},
    )
    parent = workflow(
        "nested-parent",
        "1",
        (
            subworkflow_step(
                "call",
                child.workflow_id,
                child.version,
                contract=NodeContract(empty, reply, {}),
            ),
        ),
        schema_version=child.schema_version,
        input_schema=empty,
        output_schema=reply,
        output_bindings={"/message": ValueBinding("step_output", "/message", "call")},
    )
    (root / "instructions").mkdir(parents=True)
    (root / "instructions" / "answer.md").write_text(instructions, encoding="utf-8")
    (root / "workflow.json").write_text(
        json.dumps(parent.to_mapping()), encoding="utf-8"
    )
    (root / "child.json").write_text(json.dumps(child.to_mapping()), encoding="utf-8")
    (root / "tools.json").write_text("[]", encoding="utf-8")
    (root / "cases.json").write_text("[]", encoding="utf-8")
    (root / "pylock.toml").write_text(
        'lock-version = "1.0"\ncreated-by = "uv"\nrequires-python = ">=3.12"\n',
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        """[project]
name = "nested-runtime-project"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = []

[tool.platform-agent-workflow]
schema_version = "v6-agent-project-v1"
kind = "workflow"
workflow = "workflow.json"
tools = "tools.json"
dependency_lock = "pylock.toml"
files = []
evaluation_cases = "cases.json"

[tool.platform-agent-workflow.agent_instructions]
answer = "instructions/answer.md"

[tool.platform-agent-workflow.subworkflows]
"nested-child@1" = "child.json"
""",
        encoding="utf-8",
    )
    target = PortableTarget(
        driver_bindings=_target().driver_bindings,
        subworkflow_bindings={
            "nested-child@1": {
                "workflow_id": child.workflow_id,
                "version": child.version,
                "definition_digest": child.definition_digest,
            }
        },
    )
    return parent, child, target


def test_nested_agent_uses_definition_address_not_instance_path(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    project = tmp_path / "nested"
    project.mkdir()
    _parent, _child, target = _nested_project(
        project, instructions="nested instructions"
    )
    adapters = []

    def adapter(binding, instructions):  # type: ignore[no-untyped-def]
        del binding
        value = _BoundDriver(instructions)
        adapters.append(value)
        return value

    monkeypatch.setattr(runtime_module, "_adapter", adapter)
    runtime = load_project_runtime(
        project, target, SQLiteRunJournal(tmp_path / "nested.sqlite")
    )

    completed = runtime.engine.run(runtime.workflow, {})

    assert completed.status == "SUCCEEDED"
    context = adapters[0].contexts[0]
    assert context.step_id == "call/subworkflow/answer"
    assert (
        context.definition_workflow_id,
        context.definition_workflow_version,
        context.definition_step_id,
    ) == ("nested-child", "1", "answer")


def test_project_router_effective_instructions_count_toward_v4_context_budget(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    project = tmp_path / "budget"
    project.mkdir()
    _parent, _child, target = _nested_project(
        project, instructions="x" * 10_000, v4=True
    )
    adapters = []

    def adapter(binding, instructions):  # type: ignore[no-untyped-def]
        del binding
        value = _BoundDriver(instructions)
        adapters.append(value)
        return value

    monkeypatch.setattr(runtime_module, "_adapter", adapter)
    runtime = load_project_runtime(
        project, target, SQLiteRunJournal(tmp_path / "budget.sqlite")
    )

    blocked = runtime.engine.run(runtime.workflow, {})

    assert blocked.status == "BLOCKED"
    assert blocked.state["failure"]["code"] == "AGENT_CONTEXT_LIMIT"
    assert adapters[0].contexts == []


def test_imported_child_agents_with_same_short_name_route_by_definition_address(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """A short ``answer`` is only a convenience, never an identity boundary."""

    child_a = workflow(
        "portable-child-a",
        "1",
        (agent_step("answer", "primary", tools=()),),
        schema_version="v6-light-workflow-v2",
    )
    child_b = workflow(
        "portable-child-b",
        "1",
        (agent_step("answer", "primary", tools=()),),
        schema_version="v6-light-workflow-v2",
    )
    parent = workflow(
        "portable-parent",
        "1",
        (
            subworkflow_step("first", child_a.workflow_id, child_a.version),
            subworkflow_step("second", child_b.workflow_id, child_b.version),
        ),
        schema_version="v6-light-workflow-v2",
    )
    source = tmp_path / "children.v6bundle"
    child_a_address = f"{child_a.workflow_id}@{child_a.version}:answer"
    child_b_address = f"{child_b.workflow_id}@{child_b.version}:answer"
    export_bundle(
        source,
        parent,
        bundle_format=BUNDLE_V2,
        subworkflows=(child_a, child_b),
        agent_instructions={
            child_a_address: "Instructions for the first child.",
            child_b_address: "Instructions for the second child.",
        },
        dependency_lock=(
            'lock-version = "1.0"\ncreated-by = "uv"\nrequires-python = ">=3.12"\n'
        ),
    )
    source_a = tmp_path / "child-a.json"
    source_b = tmp_path / "child-b.json"
    source_a.write_text(json.dumps(child_a.to_mapping()), encoding="utf-8")
    source_b.write_text(json.dumps(child_b.to_mapping()), encoding="utf-8")
    capabilities = frozenset(
        {"host_managed_execution", "native_tools", "structured_output"}
    )
    target = PortableTarget(
        driver_bindings=(
            DriverBinding(
                "answer",
                "pydantic-ai",
                "pydantic-ai-native-v2@test",
                "test:first",
                capabilities,
                child_a.workflow_id,
                child_a.version,
            ),
            DriverBinding(
                "answer",
                "pydantic-ai",
                "pydantic-ai-native-v2@test",
                "test:second",
                capabilities,
                child_b.workflow_id,
                child_b.version,
            ),
        ),
        subworkflow_bindings={
            f"{child_a.workflow_id}@{child_a.version}": {
                "workflow_id": child_a.workflow_id,
                "version": child_a.version,
                "definition_digest": child_a.definition_digest,
            },
            f"{child_b.workflow_id}@{child_b.version}": {
                "workflow_id": child_b.workflow_id,
                "version": child_b.version,
                "definition_digest": child_b.definition_digest,
            },
        },
    )
    project = import_bundle_project(
        source,
        tmp_path / "assembled-children",
        target,
        subworkflow_sources={
            f"{child_a.workflow_id}@{child_a.version}": source_a,
            f"{child_b.workflow_id}@{child_b.version}": source_b,
        },
        check_dependencies=False,
    )
    assert child_a_address in project.agent_instruction_paths
    assert child_b_address in project.agent_instruction_paths
    assert "answer" not in project.agent_instruction_paths

    seen: list[tuple[tuple[str, str, str] | None, str]] = []

    def adapter(binding, instructions):  # type: ignore[no-untyped-def]
        seen.append((binding.address, instructions))
        return _BoundDriver(instructions)

    monkeypatch.setattr(runtime_module, "_adapter", adapter)
    runtime = load_project_runtime(
        project, target, SQLiteRunJournal(tmp_path / "children.sqlite")
    )
    completed = runtime.engine.run(runtime.workflow, {})

    assert completed.status == "SUCCEEDED"
    assert seen == [
        (
            (child_a.workflow_id, child_a.version, "answer"),
            "Instructions for the first child.",
        ),
        (
            (child_b.workflow_id, child_b.version, "answer"),
            "Instructions for the second child.",
        ),
    ]


def test_bundle_v2_assembles_into_project_and_resumes_after_approval(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    empty = _strict_schema({})
    value_schema = _strict_schema({"value": {"type": "integer"}}, ("value",))
    reply = _strict_schema({"message": {"type": "string"}}, ("message",))
    tool = ToolReference("portable.bound", "1", "sha256:" + "d" * 64)
    tool_contract = AgentToolSpec(
        "bound",
        tool,
        "Return the bound integer.",
        value_schema,
        value_schema,
    )
    spec = workflow(
        "assembled-runtime",
        "1",
        (
            agent_step(
                "answer",
                "primary",
                tools=(tool_contract,),
                max_turns=2,
                contract=NodeContract(empty, reply, {}),
            ),
        ),
        schema_version="v6-light-workflow-v3",
        input_schema=empty,
        output_schema=reply,
        output_bindings={"/message": ValueBinding("step_output", "/message", "answer")},
    )
    bundle_path = tmp_path / "runtime.v6bundle"
    export_bundle(
        bundle_path,
        spec,
        tools=({**tool.to_mapping(), "permissions": []},),
        bundle_format=BUNDLE_V2,
        agent_instructions={"answer": "Use the bound tool once."},
        dependency_lock=(
            'lock-version = "1.0"\ncreated-by = "uv"\nrequires-python = ">=3.12"\n'
        ),
    )
    target = PortableTarget(
        trusted_tools=(tool,),
        driver_bindings=_target().driver_bindings,
    )
    project = import_bundle_project(
        bundle_path,
        tmp_path / "assembled",
        target,
        tool_registries=("bound_tools:register",),
        check_dependencies=False,
    )
    (project.root / "bound_tools.py").write_text(
        "\n".join(
            (
                "from platform_lightweight_agent import ToolReference",
                "",
                "def register(executor):",
                "    executor.register(",
                f"        ToolReference('portable.bound', '1', {tool.package_digest!r}),",
                "        lambda arguments: {'value': arguments['value']},",
                "    )",
            )
        ),
        encoding="utf-8",
    )

    class ToolDriver(_BoundDriver):
        def decide(self, context):  # type: ignore[no-untyped-def]
            self.contexts.append(context)
            if not context.prior_tool_results:
                return AgentDecision("", (ToolCall(tool, {"value": 7}),))
            return AgentDecision("finished")

    monkeypatch.setattr(
        runtime_module,
        "_adapter",
        lambda binding, instructions: ToolDriver(instructions),
    )
    journal = SQLiteRunJournal(tmp_path / "assembled.sqlite")
    first_runtime = load_project_runtime(project, target, journal)
    waiting = first_runtime.engine.run(
        first_runtime.workflow, {}, run_id="assembled-run"
    )
    approval_id = next(iter(waiting.state["v3"]["pending_approvals"]))

    restarted = load_resume_runtime(project.root, target, journal, waiting.run_id)
    completed = restarted.engine.resume(
        waiting.run_id,
        approvals=(ApprovalDecision(approval_id, True),),
    )

    assert waiting.status == "WAITING_APPROVAL"
    assert completed.status == "SUCCEEDED"
    assert completed.state["v3"]["output"] == {"message": "finished"}
    assert (project.root / "pyproject.toml").is_file()
    assert json.loads((project.root / "target.json").read_text())["target_kind"] == "v6"
