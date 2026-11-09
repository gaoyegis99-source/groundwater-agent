from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import platform_lightweight_agent.portability as portability_module
import pytest
from platform_lightweight_agent import (
    BUNDLE_V2,
    AgentToolSpec,
    DriverBinding,
    DriverRuntimeDependency,
    NodeContract,
    PortableTarget,
    ToolDelivery,
    ToolReference,
    ValueBinding,
    WorkflowSpec,
    WorkflowValidationError,
    agent_address_key,
    agent_step,
    approval_step,
    build_tool_package,
    check_project,
    compile_execution_plan,
    diagnose_bundle,
    export_bundle,
    export_project_bundle,
    import_bundle,
    import_bundle_project,
    inspect_target_environment,
    lock_project,
    parse_agent_address_key,
    read_bundle,
    scaffold_project,
    subworkflow_step,
    workflow,
)
from platform_lightweight_agent.cli import main as cli_main

DIALECT = "https://json-schema.org/draft/2020-12/schema"
TOOL = ToolReference("portable.lookup", "1.0.0", "sha256:" + "a" * 64)
LOCK = 'lock-version = "1.0"\ncreated-by = "uv"\nrequires-python = ">=3.12"\n'


def _schema(field: str) -> dict:
    return {
        "$schema": DIALECT,
        "type": "object",
        "properties": {field: {"type": "string"}},
        "required": [field],
        "additionalProperties": False,
    }


def _workflow():
    return workflow(
        "portable-agent",
        "1.0.0",
        [
            agent_step(
                "research",
                "primary",
                max_turns=4,
                tools=(
                    AgentToolSpec(
                        "lookup",
                        TOOL,
                        "Look up a record.",
                        _schema("query"),
                        _schema("answer"),
                    ),
                ),
            )
        ],
        schema_version="v6-light-workflow-v2",
    )


def _target(*, target_kind: str = "v6") -> PortableTarget:
    return PortableTarget(
        trusted_tools=(TOOL,),
        driver_bindings=(
            DriverBinding(
                "research",
                "pydantic-ai",
                "pydantic-ai-native-v2@1.107.9",
                "test:model",
                frozenset(
                    {
                        "host_managed_execution",
                        "native_tools",
                        "structured_output",
                    }
                ),
            ),
        ),
        python_executable=sys.executable,
        target_kind=target_kind,
    )


def test_bundle_v2_round_trip_requires_compatible_target(tmp_path: Path) -> None:
    source = tmp_path / "portable.v6bundle"
    exported = export_bundle(
        source,
        _workflow(),
        tools=[TOOL.to_mapping()],
        bundle_format=BUNDLE_V2,
        agent_instructions={"research": "Use lookup before answering."},
        dependency_lock=LOCK,
    )
    repeated = tmp_path / "portable-repeat.v6bundle"
    export_bundle(
        repeated,
        _workflow(),
        tools=[TOOL.to_mapping()],
        bundle_format=BUNDLE_V2,
        agent_instructions={"research": "Use lookup before answering."},
        dependency_lock=LOCK,
    )

    assert exported.schema_version == BUNDLE_V2
    assert repeated.read_bytes() == source.read_bytes()
    assert set(exported.manifest) == {"schema_version", "source", "entries"}
    assert exported.portability is not None
    assert exported.portability["runtime_state"] == "excluded"
    assert read_bundle(source).portability == exported.portability
    report = diagnose_bundle(source, _target(), check_dependencies=False)
    assert report.status == "compatible"
    target = tmp_path / "imported"
    imported = import_bundle(
        source,
        target,
        target_capabilities=_target(),
        check_dependencies=False,
    )
    assert imported.workflow.definition_digest == _workflow().definition_digest
    assert not (target / "checkpoint.json").exists()

    with pytest.raises(WorkflowValidationError, match="PortableTarget"):
        import_bundle(source, tmp_path / "unchecked")


def test_cli_can_assemble_bundle_v2_into_a_checked_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "portable.v6bundle"
    assets = tmp_path / "assets"
    (assets / "data").mkdir(parents=True)
    (assets / "data" / "input.json").write_text('{"value":1}', encoding="utf-8")
    export_bundle(
        source,
        _workflow(),
        tools=[TOOL.to_mapping()],
        files_root=assets,
        files=("data/input.json",),
        bundle_format=BUNDLE_V2,
        agent_instructions={"research": "Use the installed lookup tool."},
        dependency_lock=LOCK,
    )
    target_path = tmp_path / "target.json"
    target_path.write_text(json.dumps(_target().to_mapping()), encoding="utf-8")
    project = tmp_path / "project"

    exit_code = cli_main(
        [
            "import",
            str(source),
            str(project),
            "--target-config",
            str(target_path),
            "--as-project",
            "--tool-registry",
            "installed_tools:register",
            "--skip-dependency-check",
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["schema_version"] == (
        "v6-agent-project-v1"
    )
    checked = check_project(project)
    assert checked.tool_registries == ("installed_tools:register",)
    assert checked.files == ("data/input.json",)
    assert (project / "data" / "input.json").read_text() == '{"value":1}'
    assert checked.agent_instruction_paths["research"].read_text() == (
        "Use the installed lookup tool."
    )


def test_project_import_keeps_a_user_file_named_files_out_of_transport_namespace(
    tmp_path: Path,
) -> None:
    """The archive's ``files/`` directory is never a project-reserved name."""

    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "files").write_text("user payload", encoding="utf-8")
    source = tmp_path / "source.v6bundle"
    export_bundle(
        source,
        _workflow(),
        tools=[TOOL.to_mapping()],
        files_root=assets,
        files=("files",),
        bundle_format=BUNDLE_V2,
        agent_instructions={"research": "Use lookup."},
        dependency_lock=LOCK,
    )

    project = import_bundle_project(
        source,
        tmp_path / "project",
        _target(),
        check_dependencies=False,
    )

    assert project.files == ("files",)
    assert (project.root / "files").read_text(encoding="utf-8") == "user payload"


def test_project_import_failure_leaves_no_half_assembled_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.v6bundle"
    export_bundle(
        source,
        _workflow(),
        tools=[TOOL.to_mapping()],
        bundle_format=BUNDLE_V2,
        agent_instructions={"research": "Use lookup."},
        dependency_lock=LOCK,
    )
    destination = tmp_path / "project"
    destination.mkdir()
    original_check_project = portability_module.check_project

    def reject_staging(source: str | Path):  # type: ignore[no-untyped-def]
        if Path(source).name.startswith(".project.v6-project-"):
            raise WorkflowValidationError("injected staging validation failure")
        return original_check_project(source)

    monkeypatch.setattr(portability_module, "check_project", reject_staging)
    with pytest.raises(WorkflowValidationError, match="injected staging"):
        import_bundle_project(
            source,
            destination,
            _target(),
            check_dependencies=False,
        )

    assert destination.is_dir()
    assert list(destination.iterdir()) == []
    assert not list(tmp_path.glob(".project.v6-project-*"))
    assert not list(tmp_path.glob(".project.v6-payload-*"))


def test_bundle_v2_rejects_v1_agent_and_tampering(tmp_path: Path) -> None:
    legacy = workflow(
        "legacy-agent",
        "1",
        [agent_step("agent", "legacy", tools=(TOOL,))],
        schema_version="v6-light-workflow-v1",
    )
    with pytest.raises(WorkflowValidationError, match="v1 Agent"):
        export_bundle(
            tmp_path / "legacy.bundle",
            legacy,
            tools=[TOOL.to_mapping()],
            bundle_format=BUNDLE_V2,
            agent_instructions={"agent": "Do work."},
            dependency_lock=LOCK,
        )
    with pytest.raises(WorkflowValidationError, match="requires bounded instructions"):
        export_bundle(
            tmp_path / "missing-instructions.bundle",
            _workflow(),
            tools=[TOOL.to_mapping()],
            bundle_format=BUNDLE_V2,
            dependency_lock=LOCK,
        )
    with pytest.raises(WorkflowValidationError, match="requires bounded instructions"):
        export_bundle(
            tmp_path / "credential-instructions.bundle",
            _workflow(),
            tools=[TOOL.to_mapping()],
            bundle_format=BUNDLE_V2,
            agent_instructions={"research": "api_key=must-not-travel"},
            dependency_lock=LOCK,
        )

    source = tmp_path / "source.bundle"
    export_bundle(
        source,
        _workflow(),
        tools=[TOOL.to_mapping()],
        bundle_format=BUNDLE_V2,
        agent_instructions={"research": "Use lookup."},
        dependency_lock=LOCK,
    )
    tampered = tmp_path / "tampered.bundle"
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(tampered, "w") as edited:
        for info in original.infolist():
            content = original.read(info.filename)
            if info.filename == "portability.json":
                content = content.replace(b"Use lookup.", b"Skip lookup")
            edited.writestr(info, content)
    with pytest.raises(WorkflowValidationError, match="digest"):
        read_bundle(tampered)


def test_execution_plan_is_non_executing_and_exposes_agent_scope() -> None:
    plan = compile_execution_plan(_workflow()).to_mapping()

    assert plan["schema_version"] == "v6-execution-plan-v1"
    assert plan["effects"] == "not_executed"
    assert plan["steps"][0]["driver_requirement"] == "primary"
    assert plan["steps"][0]["tool_scope"] == ["lookup"]
    assert plan["steps"][0]["max_turns"] == 4


def test_v7_report_never_claims_agent_importability(tmp_path: Path) -> None:
    source = tmp_path / "source.bundle"
    export_bundle(
        source,
        _workflow(),
        tools=[TOOL.to_mapping()],
        bundle_format=BUNDLE_V2,
        agent_instructions={"research": "Use lookup."},
        dependency_lock=LOCK,
    )

    report = diagnose_bundle(
        source, _target(target_kind="v7"), check_dependencies=False
    )

    assert report.status == "binding_required"
    assert "TARGET_IMPORTER_SUPPORT_REQUIRED" in {
        item.code for item in report.diagnostics
    }


def test_missing_tool_binding_differs_from_a_digest_conflict(tmp_path: Path) -> None:
    source = tmp_path / "source.bundle"
    export_bundle(
        source,
        _workflow(),
        tools=[TOOL.to_mapping()],
        bundle_format=BUNDLE_V2,
        agent_instructions={"research": "Use lookup."},
        dependency_lock=LOCK,
    )
    target = _target()
    missing = PortableTarget(
        driver_bindings=target.driver_bindings,
        python_executable=target.python_executable,
    )
    missing_report = diagnose_bundle(source, missing, check_dependencies=False)
    assert missing_report.status == "binding_required"
    assert "TOOL_BINDING_REQUIRED" in {item.code for item in missing_report.diagnostics}

    conflicting = PortableTarget(
        trusted_tools=(
            ToolReference(TOOL.tool_id, TOOL.version, "sha256:" + "f" * 64),
        ),
        driver_bindings=target.driver_bindings,
        python_executable=target.python_executable,
    )
    conflict_report = diagnose_bundle(source, conflicting, check_dependencies=False)
    assert conflict_report.status == "incompatible"
    assert "TOOL_DIGEST_MISMATCH" in {item.code for item in conflict_report.diagnostics}


def test_subworkflow_target_binding_uses_the_exact_definition_digest(
    tmp_path: Path,
) -> None:
    child = workflow(
        "portable-child",
        "1.0.0",
        (approval_step("review", "continue?"),),
        schema_version="v6-light-workflow-v2",
    )
    parent = workflow(
        "portable-parent",
        "1.0.0",
        (subworkflow_step("child", child.workflow_id, child.version),),
        schema_version="v6-light-workflow-v2",
    )
    source = tmp_path / "subworkflow.bundle"
    export_bundle(
        source,
        parent,
        bundle_format=BUNDLE_V2,
        subworkflows=(child,),
        dependency_lock=LOCK,
    )
    missing = diagnose_bundle(
        source,
        PortableTarget(python_executable=sys.executable),
        check_dependencies=False,
    )
    assert missing.status == "binding_required"
    assert "SUBWORKFLOW_BINDING_REQUIRED" in {item.code for item in missing.diagnostics}

    conflicting = PortableTarget(
        subworkflow_bindings={
            "portable-child@1.0.0": {
                "workflow_id": child.workflow_id,
                "version": child.version,
                "definition_digest": "sha256:" + "0" * 64,
            }
        },
        python_executable=sys.executable,
    )
    conflict = diagnose_bundle(source, conflicting, check_dependencies=False)
    assert conflict.status == "incompatible"
    assert "SUBWORKFLOW_DIGEST_MISMATCH" in {item.code for item in conflict.diagnostics}


def test_duplicate_child_agent_names_require_addressed_driver_bindings(
    tmp_path: Path,
) -> None:
    children = tuple(
        workflow(
            workflow_id,
            "1",
            (agent_step("answer", "primary", tools=()),),
            schema_version="v6-light-workflow-v2",
        )
        for workflow_id in ("child-a", "child-b")
    )
    parent = workflow(
        "parent",
        "1",
        tuple(
            subworkflow_step(child.workflow_id, child.workflow_id, child.version)
            for child in children
        ),
        schema_version="v6-light-workflow-v2",
    )
    assert agent_address_key("child-a", "1", "answer") == "child-a@1:answer"
    assert parse_agent_address_key("child-a@1:answer") == ("child-a", "1", "answer")
    source = tmp_path / "children.v6bundle"
    export_bundle(
        source,
        parent,
        bundle_format=BUNDLE_V2,
        subworkflows=children,
        agent_instructions={
            agent_address_key(child.workflow_id, child.version, "answer"): (
                f"Instructions for {child.workflow_id}."
            )
            for child in children
        },
        dependency_lock=LOCK,
    )
    capabilities = frozenset(
        {"host_managed_execution", "native_tools", "structured_output"}
    )
    legacy = PortableTarget(
        driver_bindings=(
            DriverBinding(
                "answer",
                "pydantic-ai",
                "pydantic-ai-native-v2@1.107.9",
                "test:model",
                capabilities,
            ),
        )
    )

    unresolved = diagnose_bundle(source, legacy, check_dependencies=False)

    assert unresolved.status == "binding_required"
    assert "AGENT_DRIVER_ADDRESS_BINDING_REQUIRED" in {
        item.code for item in unresolved.diagnostics
    }
    addressed = PortableTarget(
        driver_bindings=tuple(
            DriverBinding(
                "answer",
                "pydantic-ai",
                "pydantic-ai-native-v2@1.107.9",
                f"test:{child.workflow_id}",
                capabilities,
                child.workflow_id,
                child.version,
            )
            for child in children
        ),
        subworkflow_bindings={
            f"{child.workflow_id}@{child.version}": {
                "workflow_id": child.workflow_id,
                "version": child.version,
                "definition_digest": child.definition_digest,
            }
            for child in children
        },
    )

    assert (
        diagnose_bundle(source, addressed, check_dependencies=False).status
        == "compatible"
    )


def test_portable_target_wire_contract_rejects_credentials() -> None:
    raw = _target().to_mapping()
    assert PortableTarget.from_mapping(raw) == _target()
    raw["subworkflow_bindings"] = {
        "child@1": {
            "workflow_id": "child",
            "version": "1",
            "definition_digest": "sha256:" + "1" * 64,
            "api_key": "forbidden",
        }
    }
    with pytest.raises(WorkflowValidationError):
        PortableTarget.from_mapping(raw)


def test_environment_readiness_checks_release_delivery_without_importing_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = tmp_path / "portable-tool-1.0.0.whl"
    wheel.write_bytes(b"release wheel artifact")
    reference = ToolReference(
        "portable.delivery",
        "1.0.0",
        "sha256:" + hashlib.sha256(wheel.read_bytes()).hexdigest(),
    )
    target = PortableTarget(
        trusted_tools=(reference,),
        tool_deliveries=(
            ToolDelivery(
                reference,
                "release",
                distribution="portable-tool",
                entry_point="v6.tools:lookup",
                wheel_path=str(wheel),
            ),
        ),
        driver_bindings=(
            DriverBinding(
                "research",
                "pydantic-ai",
                "pydantic-ai-native-v2@1.107.9",
                "test:model",
                distribution="pydantic-ai-slim",
            ),
        ),
        python_executable="target-python",
    )

    def metadata_probe(
        executable: str,
        distributions: list[str],
        modules: list[str],
        imports: list[str],
    ):
        assert executable == "target-python"
        assert set(distributions) == {"portable-tool", "pydantic-ai-slim"}
        assert modules == []
        assert imports == []
        return None, {
            "distributions": {
                "portable-tool": {
                    "version": "1.0.0",
                    "entry_points": ["v6.tools:lookup"],
                },
                "pydantic-ai-slim": {"version": "1.107.9", "entry_points": []},
            },
            "modules": {},
            "imports": {},
        }

    monkeypatch.setattr(
        "platform_lightweight_agent.portability._inspect_python_environment",
        metadata_probe,
    )
    report = inspect_target_environment(target)

    assert report.status == "ready"
    assert "TOOL_INSTALL_RECORD_UNAVAILABLE" in {
        item.code for item in report.diagnostics
    }
    assert PortableTarget.from_mapping(target.to_mapping()) == target

    wheel.write_bytes(b"different artifact")
    changed = inspect_target_environment(target)

    assert changed.status == "incompatible"
    assert "TOOL_WHEEL_DIGEST_MISMATCH" in {item.code for item in changed.diagnostics}


def test_environment_readiness_does_not_claim_a_dotted_development_callable_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = ToolReference("portable.dev", "1", "sha256:" + "d" * 64)
    target = PortableTarget(
        trusted_tools=(reference,),
        tool_deliveries=(
            ToolDelivery(
                reference,
                "development",
                development_callable="demo_tool.optional_plugin:run",
            ),
        ),
        python_executable="target-python",
    )
    monkeypatch.setattr(
        "platform_lightweight_agent.portability._inspect_python_environment",
        lambda *_: (
            None,
            {"distributions": {}, "modules": {"demo_tool": True}, "imports": {}},
        ),
    )
    monkeypatch.setattr(
        "platform_lightweight_agent.portability._inspect_development_entrypoints",
        lambda *_: pytest.fail("default inspection must not load development code"),
    )

    report = inspect_target_environment(target)

    assert report.status == "binding_required"
    assert {
        "DEVELOPMENT_TOOL_ENTRYPOINT_NOT_CHECKED",
        "DEVELOPMENT_TOOL_IMPLEMENTATION_UNVERIFIABLE",
    } <= {item.code for item in report.diagnostics}
    assert report.to_mapping()["checks"] == {
        "package_metadata": "passed",
        "provider_imports": "passed",
        "runtime_construction": "not_checked",
        "development_top_level_modules": "passed",
        "development_full_module_locator": "not_checked",
        "development_entrypoints": "not_checked",
        "tool_registration": "not_checked",
        "tool_business_invocation": "not_checked",
        "model_network": "not_checked",
    }


def test_environment_readiness_can_opt_in_to_trusted_callable_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = ToolReference("portable.dev", "1", "sha256:" + "d" * 64)
    target = PortableTarget(
        trusted_tools=(reference,),
        tool_deliveries=(
            ToolDelivery(
                reference,
                "development",
                development_callable="demo_tool.optional_plugin:run",
            ),
        ),
        python_executable="target-python",
    )
    monkeypatch.setattr(
        "platform_lightweight_agent.portability._inspect_python_environment",
        lambda *_: (
            None,
            {"distributions": {}, "modules": {"demo_tool": True}, "imports": {}},
        ),
    )
    monkeypatch.setattr(
        "platform_lightweight_agent.portability._inspect_development_entrypoints",
        lambda *_: (
            None,
            {
                "demo_tool.optional_plugin:run": {
                    "module_locator": True,
                    "callable": True,
                }
            },
        ),
    )

    report = inspect_target_environment(target, verify_development_entrypoints=True)

    assert report.status == "ready"
    assert report.to_mapping()["checks"]["development_full_module_locator"] == "passed"
    assert report.to_mapping()["checks"]["development_entrypoints"] == "passed"
    assert report.to_mapping()["checks"]["tool_registration"] == "not_checked"
    assert report.to_mapping()["checks"]["tool_business_invocation"] == "not_checked"


def test_environment_readiness_rejects_an_unloadable_opted_in_callable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = ToolReference("portable.dev", "1", "sha256:" + "d" * 64)
    target = PortableTarget(
        trusted_tools=(reference,),
        tool_deliveries=(
            ToolDelivery(
                reference,
                "development",
                development_callable="demo_tool.optional_plugin:run",
            ),
        ),
        python_executable="target-python",
    )
    monkeypatch.setattr(
        "platform_lightweight_agent.portability._inspect_python_environment",
        lambda *_: (
            None,
            {"distributions": {}, "modules": {"demo_tool": True}, "imports": {}},
        ),
    )
    monkeypatch.setattr(
        "platform_lightweight_agent.portability._inspect_development_entrypoints",
        lambda *_: (
            None,
            {
                "demo_tool.optional_plugin:run": {
                    "module_locator": True,
                    "callable": False,
                }
            },
        ),
    )

    report = inspect_target_environment(target, verify_development_entrypoints=True)

    assert report.status == "incompatible"
    assert "DEVELOPMENT_TOOL_ENTRYPOINT_UNAVAILABLE" in {
        item.code for item in report.diagnostics
    }


def test_environment_readiness_distinguishes_full_module_locator_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = ToolReference("portable.dev", "1", "sha256:" + "d" * 64)
    target = PortableTarget(
        trusted_tools=(reference,),
        tool_deliveries=(
            ToolDelivery(
                reference,
                "development",
                development_callable="demo_tool.optional_plugin:run",
            ),
        ),
        python_executable="target-python",
    )
    monkeypatch.setattr(
        "platform_lightweight_agent.portability._inspect_python_environment",
        lambda *_: (
            None,
            {"distributions": {}, "modules": {"demo_tool": True}, "imports": {}},
        ),
    )
    monkeypatch.setattr(
        "platform_lightweight_agent.portability._inspect_development_entrypoints",
        lambda *_: (
            None,
            {
                "demo_tool.optional_plugin:run": {
                    "module_locator": False,
                    "callable": False,
                }
            },
        ),
    )

    report = inspect_target_environment(target, verify_development_entrypoints=True)

    assert report.status == "incompatible"
    assert "DEVELOPMENT_TOOL_FULL_MODULE_UNAVAILABLE" in {
        item.code for item in report.diagnostics
    }
    assert report.to_mapping()["checks"]["development_full_module_locator"] == "failed"
    assert report.to_mapping()["checks"]["development_entrypoints"] == "not_checked"


def test_diagnose_requires_environment_inspection_for_callable_loading(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.v6bundle"
    export_bundle(
        source,
        _workflow(),
        tools=[TOOL.to_mapping()],
        bundle_format=BUNDLE_V2,
        agent_instructions={"research": "Use lookup."},
        dependency_lock=LOCK,
    )

    with pytest.raises(WorkflowValidationError, match="requires check_environment"):
        diagnose_bundle(
            source,
            _target(),
            check_dependencies=False,
            verify_development_entrypoints=True,
        )


def test_environment_readiness_requires_bound_model_provider_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = PortableTarget(
        driver_bindings=(
            DriverBinding(
                "research",
                "pydantic-ai",
                "pydantic-ai-native-v2@1.107.9",
                "anthropic:claude-sonnet-4-5",
                distribution="pydantic-ai-slim",
            ),
        ),
        python_executable="target-python",
    )

    def probe(_executable, distributions, _modules, imports):  # type: ignore[no-untyped-def]
        assert set(distributions) == {"anthropic", "pydantic-ai-slim"}
        assert imports == ["anthropic"]
        return None, {
            "distributions": {
                "pydantic-ai-slim": {"version": "1.107.9", "entry_points": []},
                "anthropic": None,
            },
            "modules": {},
            "imports": {"anthropic": False},
        }

    monkeypatch.setattr(
        "platform_lightweight_agent.portability._inspect_python_environment", probe
    )
    monkeypatch.setattr(
        "platform_lightweight_agent.portability._inspect_driver_runtime",
        lambda *_: {"legacy:research": False},
    )
    report = inspect_target_environment(target)

    assert report.status == "incompatible"
    assert report.to_mapping()["checks"] == {
        "package_metadata": "failed",
        "provider_imports": "failed",
        "runtime_construction": "failed",
        "development_top_level_modules": "not_checked",
        "development_full_module_locator": "not_checked",
        "development_entrypoints": "not_checked",
        "tool_registration": "not_checked",
        "tool_business_invocation": "not_checked",
        "model_network": "not_checked",
    }
    diagnostic = next(
        item
        for item in report.diagnostics
        if item.code == "DRIVER_RUNTIME_DEPENDENCY_MISSING"
    )
    assert "pydantic-ai-anthropic" in diagnostic.message


def test_environment_readiness_does_not_claim_checks_without_an_interpreter() -> None:
    report = inspect_target_environment(
        PortableTarget(
            driver_bindings=(
                DriverBinding(
                    "research",
                    "pydantic-ai",
                    "pydantic-ai-native-v2@1.107.9",
                    "anthropic:claude-sonnet-4-5",
                    distribution="pydantic-ai-slim",
                ),
            )
        )
    )

    assert report.status == "binding_required"
    assert report.to_mapping()["checks"] == {
        "package_metadata": "not_checked",
        "provider_imports": "not_checked",
        "runtime_construction": "not_checked",
        "development_top_level_modules": "not_checked",
        "development_full_module_locator": "not_checked",
        "development_entrypoints": "not_checked",
        "tool_registration": "not_checked",
        "tool_business_invocation": "not_checked",
        "model_network": "not_checked",
    }


def test_environment_readiness_checks_frozen_provider_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = PortableTarget(
        driver_bindings=(
            DriverBinding(
                "research",
                "pydantic-ai",
                "pydantic-ai-native-v2@1.107.9",
                "anthropic:claude-sonnet-4-5",
                distribution="pydantic-ai-slim",
                runtime_dependencies=(
                    DriverRuntimeDependency(
                        "anthropic",
                        "anthropic",
                        "platform-lightweight-agent-core[pydantic-ai-anthropic]",
                        "1.2.3",
                    ),
                ),
            ),
        ),
        python_executable="target-python",
    )
    monkeypatch.setattr(
        "platform_lightweight_agent.portability._inspect_python_environment",
        lambda *_: (
            None,
            {
                "python_version": "3.12.0",
                "distributions": {
                    "pydantic-ai-slim": {
                        "version": "1.107.9",
                        "entry_points": [],
                    },
                    "anthropic": {"version": "9.9.9", "entry_points": []},
                },
                "modules": {},
                "imports": {"anthropic": True},
            },
        ),
    )
    monkeypatch.setattr(
        "platform_lightweight_agent.portability._inspect_driver_runtime",
        lambda *_: {"legacy:research": True},
    )

    report = inspect_target_environment(target)

    assert report.status == "incompatible"
    assert "DRIVER_RUNTIME_DEPENDENCY_VERSION_MISMATCH" in {
        item.code for item in report.diagnostics
    }


@pytest.mark.parametrize(
    ("schema_version", "required"),
    [
        ("v6-light-workflow-v3", ("anyio", "jsonschema")),
        ("v6-light-workflow-v4", ("anyio", "jsonschema", "immutables")),
    ],
)
def test_environment_readiness_derives_async_runtime_from_workflow_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    schema_version: str,
    required: tuple[str, ...],
) -> None:
    empty = {
        "$schema": DIALECT,
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    approved = {
        "$schema": DIALECT,
        "type": "object",
        "properties": {"approved": {"type": "boolean"}},
        "required": ["approved"],
        "additionalProperties": False,
    }
    definition = workflow(
        "portable-runtime-" + schema_version.rsplit("-", 1)[-1],
        "1",
        (
            approval_step(
                "review", "Approve?", contract=NodeContract(empty, approved, {})
            ),
        ),
        schema_version=schema_version,
        input_schema=empty,
        output_schema=approved,
        output_bindings={
            "/approved": ValueBinding("step_output", "/approved", "review")
        },
    )
    source = tmp_path / "runtime.bundle"
    export_bundle(
        source,
        definition,
        tools=(),
        bundle_format=BUNDLE_V2,
        dependency_lock=LOCK,
    )
    target = PortableTarget(python_executable="target-python")

    def metadata_probe(
        executable: str,
        distributions: list[str],
        modules: list[str],
        imports: list[str],
    ):
        assert executable == "target-python"
        assert set(distributions) == set(required)
        assert modules == []
        assert imports == []
        return None, {
            "distributions": {name: None for name in required},
            "modules": {},
            "imports": {},
        }

    monkeypatch.setattr(
        "platform_lightweight_agent.portability._inspect_python_environment",
        metadata_probe,
    )
    report = inspect_target_environment(target, bundle=source)

    assert report.status == "incompatible"
    assert [item.code for item in report.diagnostics].count(
        "WORKFLOW_RUNTIME_DEPENDENCY_MISSING"
    ) == len(required)


def test_environment_probe_uses_the_selected_python_metadata_only() -> None:
    error, probe = portability_module._inspect_python_environment(
        sys.executable, (), ()
    )

    assert error is None
    assert probe is not None
    assert isinstance(probe["python_version"], str)
    assert probe["distributions"] == {}
    assert probe["modules"] == {}


def test_trusted_development_entrypoint_probe_reports_locator_and_callable_layers() -> (
    None
):
    error, evidence = portability_module._inspect_development_entrypoints(
        sys.executable,
        ("json.decoder:JSONDecoder", "json.v6_missing_module:configure"),
    )

    assert error is None
    assert evidence == {
        "json.decoder:JSONDecoder": {"module_locator": True, "callable": True},
        "json.v6_missing_module:configure": {
            "module_locator": False,
            "callable": False,
        },
    }


def test_diagnose_separates_contract_installability_and_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.bundle"
    export_bundle(
        source,
        _workflow(),
        tools=[TOOL.to_mapping()],
        bundle_format=BUNDLE_V2,
        agent_instructions={"research": "Use lookup."},
        dependency_lock=LOCK,
    )
    target = PortableTarget(
        trusted_tools=(TOOL,),
        tool_deliveries=(
            ToolDelivery(
                TOOL,
                "development",
                development_callable="demo_tool:run",
            ),
        ),
        driver_bindings=(
            DriverBinding(
                "research",
                "pydantic-ai",
                "pydantic-ai-native-v2@1.107.9",
                "test:model",
                frozenset(
                    {
                        "host_managed_execution",
                        "native_tools",
                        "structured_output",
                    }
                ),
            ),
        ),
        python_executable="target-python",
    )
    monkeypatch.setattr(
        "platform_lightweight_agent.portability._inspect_python_environment",
        lambda *_: (
            None,
            {
                "distributions": {
                    "pydantic-ai-slim": {
                        "version": "1.107.9",
                        "entry_points": [],
                    }
                },
                "modules": {"demo_tool": True},
                "imports": {},
            },
        ),
    )

    report = diagnose_bundle(
        source,
        target,
        check_dependencies=False,
        check_environment=True,
    )

    assert report.status == "binding_required"
    assert report.contract_status == "compatible"
    assert report.installability_status == "not_checked"
    assert report.environment_status == "binding_required"
    assert report.to_mapping()["checks"] == {
        "contract": "compatible",
        "dependency_installability": "not_checked",
        "environment_readiness": "binding_required",
    }


@pytest.mark.parametrize(
    ("outcome", "expected_code", "expected_class"),
    (
        (
            subprocess.TimeoutExpired(["uv"], 120),
            "DEPENDENCY_CHECK_TIMEOUT",
            "binding_required",
        ),
        (
            OSError("uv launch failed"),
            "DEPENDENCY_CHECK_UNAVAILABLE",
            "binding_required",
        ),
        (
            subprocess.CompletedProcess(["uv"], 1, "", "network unavailable"),
            "DEPENDENCY_CHECK_NETWORK_UNAVAILABLE",
            "binding_required",
        ),
        (
            subprocess.CompletedProcess(["uv"], 1, "", "No solution found"),
            "DEPENDENCY_LOCK_INCOMPATIBLE",
            "incompatible",
        ),
        (
            subprocess.CompletedProcess(
                ["uv"], 1, "", "No interpreter found at path /missing-python"
            ),
            "DEPENDENCY_TARGET_PYTHON_INCOMPATIBLE",
            "incompatible",
        ),
        (
            subprocess.CompletedProcess(["uv"], 1, "", "unexpected exit"),
            "DEPENDENCY_CHECK_INCOMPLETE",
            "binding_required",
        ),
    ),
)
def test_dependency_dry_run_distinguishes_incompatible_from_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: object,
    expected_code: str,
    expected_class: str,
) -> None:
    source = tmp_path / "source.v6bundle"
    export_bundle(
        source,
        _workflow(),
        tools=[TOOL.to_mapping()],
        bundle_format=BUNDLE_V2,
        agent_instructions={"research": "Use lookup."},
        dependency_lock=LOCK,
    )
    monkeypatch.setattr(portability_module.shutil, "which", lambda _: "uv")

    def dry_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, subprocess.CompletedProcess)
        return outcome

    monkeypatch.setattr(portability_module.subprocess, "run", dry_run)
    diagnostic = portability_module._diagnose_dependencies(
        read_bundle(source), _target()
    )[0]

    assert diagnostic.code == expected_code
    assert diagnostic.classification == expected_class
    assert "network unavailable" not in diagnostic.message


def test_bundled_copier_templates_lock_export_and_build(tmp_path: Path) -> None:
    pytest.importorskip("copier")
    workflow_project = scaffold_project(
        "workflow", tmp_path / "workflow", project_name="portable-demo"
    )
    generated_tests = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=workflow_project.root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert generated_tests.returncode == 0, (
        generated_tests.stdout + generated_tests.stderr
    )
    assert "2 passed" in generated_tests.stdout
    workflow_path = workflow_project.root / "workflow.json"
    original_workflow = workflow_path.read_text(encoding="utf-8")
    broken_workflow = json.loads(original_workflow)
    broken_workflow["output_bindings"]["/message"]["step_id"] = "missing-step"
    workflow_path.write_text(json.dumps(broken_workflow), encoding="utf-8")
    negative = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_project.py::test_project_contract_and_plan_compile_together",
        ],
        cwd=workflow_project.root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert negative.returncode != 0
    workflow_path.write_text(original_workflow, encoding="utf-8")
    v3_example = WorkflowSpec.from_mapping(
        json.loads(
            (workflow_project.root / "workflow-v3.example.json").read_text(
                encoding="utf-8"
            )
        )
    )
    assert v3_example.schema_version == "v6-light-workflow-v3"
    lock_project(workflow_project.root)
    lock_project(workflow_project.root, check=True)
    exported = tmp_path / "project.bundle"
    project_bundle = export_project_bundle(workflow_project.root, exported)
    assert project_bundle.schema_version == BUNDLE_V2
    imported_project_bundle = read_bundle(exported)
    assert imported_project_bundle.workflow.workflow_id == "portable-research"
    manifest = imported_project_bundle.tools[0]
    exact_tool = ToolReference.from_mapping(
        {key: manifest[key] for key in ("tool_id", "version", "package_digest")}
    )
    dry_run_target = PortableTarget(
        trusted_tools=(exact_tool,),
        driver_bindings=(
            DriverBinding(
                "research",
                "pydantic-ai",
                "pydantic-ai-native-v2@1.107.9",
                "test:model",
                frozenset(
                    {
                        "host_managed_execution",
                        "native_tools",
                        "structured_output",
                    }
                ),
            ),
        ),
        python_executable=sys.executable,
    )
    assert diagnose_bundle(exported, dry_run_target).status == "compatible"

    missing_python = PortableTarget(
        trusted_tools=dry_run_target.trusted_tools,
        driver_bindings=dry_run_target.driver_bindings,
        python_executable=str(tmp_path / "missing-python"),
    )
    dependency_report = diagnose_bundle(exported, missing_python)
    assert dependency_report.status == "incompatible"
    assert "DEPENDENCY_TARGET_PYTHON_INCOMPATIBLE" in {
        item.code for item in dependency_report.diagnostics
    }

    tool_project = scaffold_project(
        "tool", tmp_path / "tool", project_name="portable-tool"
    )
    built_manifest = build_tool_package(tool_project.root)
    assert str(built_manifest["package_digest"]).startswith("sha256:")
    assert built_manifest["package_digest"] != "sha256:" + "0" * 64
    assert (tool_project.root / "dist" / "tool-manifest.json").is_file()

    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(WorkflowValidationError, match="new or empty"):
        scaffold_project("workflow", occupied)


def test_project_check_rejects_symlinked_declared_files(tmp_path: Path) -> None:
    pytest.importorskip("copier")
    project = scaffold_project(
        "workflow", tmp_path / "workflow", project_name="symlink-demo"
    )
    instruction = project.root / "instructions" / "research.md"
    real_instruction = instruction.with_name("real.md")
    instruction.rename(real_instruction)
    instruction.symlink_to(real_instruction.name)

    with pytest.raises(WorkflowValidationError, match="must not be a symlink"):
        check_project(project.root)


def test_project_check_compiles_registered_v3_subworkflow_contracts(
    tmp_path: Path,
    capsys,
) -> None:
    pytest.importorskip("copier")
    pytest.importorskip("anyio")
    project = scaffold_project(
        "workflow", tmp_path / "workflow", project_name="subflow-demo"
    )
    empty = {
        "$schema": DIALECT,
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    approved = {
        "$schema": DIALECT,
        "type": "object",
        "properties": {"approved": {"type": "boolean"}},
        "required": ["approved"],
        "additionalProperties": False,
    }
    child = workflow(
        "checked-child",
        "1",
        (
            approval_step(
                "review", "Approve?", contract=NodeContract(empty, approved, {})
            ),
        ),
        schema_version="v6-light-workflow-v3",
        input_schema=empty,
        output_schema=approved,
        output_bindings={
            "/approved": ValueBinding("step_output", "/approved", "review")
        },
    )
    parent = workflow(
        "checked-parent",
        "1",
        (
            subworkflow_step(
                "child",
                child.workflow_id,
                child.version,
                contract=NodeContract(empty, approved, {}),
            ),
        ),
        schema_version="v6-light-workflow-v3",
        input_schema=empty,
        output_schema=approved,
        output_bindings={
            "/approved": ValueBinding("step_output", "/approved", "child")
        },
    )
    (project.root / "workflow.json").write_text(
        json.dumps(parent.to_mapping()), encoding="utf-8"
    )
    (project.root / "child.json").write_text(
        json.dumps(child.to_mapping()), encoding="utf-8"
    )
    (project.root / "pyproject.toml").write_text(
        """[project]
name = "subflow-demo"
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
evaluation_cases = "eval/cases.json"
tool_registries = ["runtime_tools:register"]

[tool.platform-agent-workflow.agent_instructions]

[tool.platform-agent-workflow.subworkflows]
"checked-child@1" = "child.json"
""",
        encoding="utf-8",
    )

    checked = check_project(project.root)

    assert set(checked.subworkflow_paths) == {"checked-child@1"}
    plan = compile_execution_plan(project.root).to_mapping()
    requirement = plan["steps"][0]["requirement"]
    assert requirement["workflow_id"] == "checked-child"
    assert requirement["version"] == "1"
    assert requirement["definition_digest"] == child.definition_digest
    assert plan["effects"] == "not_executed"
    assert plan["subworkflows"][0]["steps"][0]["approval_boundary"] is True
    assert cli_main(["check", str(project.root)]) == 0
    capsys.readouterr()
    assert cli_main(["plan", str(project.root)]) == 0
    cli_plan = json.loads(capsys.readouterr().out)
    assert cli_plan["steps"][0]["requirement"]["definition_digest"] == (
        child.definition_digest
    )
    assert cli_plan["subworkflows"][0]["steps"][0]["approval_boundary"] is True
