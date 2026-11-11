from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts" / "checks" / "check_core_imports.py"
SPEC = importlib.util.spec_from_file_location("check_core_imports", CHECKER)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_current_distribution_and_owner_boundaries_pass() -> None:
    assert MODULE.check(ROOT) == []


def test_control_proposal_layers_are_one_way_and_read_only() -> None:
    problems = [
        problem for problem in MODULE.check(ROOT) if "CONTROL_PROPOSAL_" in problem
    ]
    assert problems == []


def test_control_proposal_layer_gate_rejects_backwards_and_write_dependencies(
    tmp_path: Path,
) -> None:
    interaction = tmp_path / "kernel/src/platform_kernel/interaction"
    interaction.mkdir(parents=True)
    (interaction / "control_proposals.py").write_text("pass\n")
    (interaction / "control_intents.py").write_text("import sqlite3\n")
    (interaction / "control_proposal_queries.py").write_text(
        "QUERY = \"UPDATE control_proposals SET status='PENDING'\"\n"
    )
    (interaction / "control_proposal_targets.py").write_text(
        "from .control_proposals import rule_intent\n"
    )
    (interaction / "control_proposal_commands.py").write_text("pass\n")
    problems = MODULE.check(tmp_path)
    assert any(
        "CONTROL_PROPOSAL_PURE_INTENT_INVALID" in problem for problem in problems
    )
    assert any(
        "CONTROL_PROPOSAL_QUERY_WRITE_INVALID" in problem for problem in problems
    )
    assert any(
        "CONTROL_PROPOSAL_FACADE_BACKWARD_IMPORT_INVALID" in problem
        for problem in problems
    )


def test_control_proposal_layer_gate_rejects_a_deferred_cycle(tmp_path: Path) -> None:
    interaction = tmp_path / "kernel/src/platform_kernel/interaction"
    interaction.mkdir(parents=True)
    (interaction / "control_proposals.py").write_text("pass\n")
    (interaction / "control_intents.py").write_text("pass\n")
    (interaction / "control_proposal_queries.py").write_text(
        "def query():\n"
        "    from .control_proposal_targets import resolve\n"
        "    return resolve\n"
    )
    (interaction / "control_proposal_targets.py").write_text(
        "from .control_proposal_queries import query\nresolve = query\n"
    )
    (interaction / "control_proposal_commands.py").write_text("pass\n")

    problems = MODULE.check(tmp_path)
    assert any(
        "CONTROL_PROPOSAL_LAYER_CYCLE_INVALID" in problem for problem in problems
    )


def test_trigger_update_clause_is_not_misclassified_as_a_table(tmp_path) -> None:
    source = tmp_path / "schema.py"
    source.write_text(
        'DDL = "CREATE TRIGGER immutable BEFORE UPDATE ON sessions '
        "WHEN NEW.owner <> OLD.owner BEGIN SELECT RAISE(ABORT, 'immutable'); END;\"\n"
    )
    assert MODULE.sql_tables(source) == set()


def test_declared_schema_tables_ignore_transient_migration_tables(
    tmp_path: Path,
) -> None:
    source = tmp_path / "database.py"
    source.write_text(
        'SCHEMA = "CREATE TABLE persistent_records(value TEXT);"\n'
        'MIGRATION = "CREATE TABLE persistent_records_v1_backup '
        'AS SELECT * FROM persistent_records;"\n'
    )

    assert MODULE.declared_schema_tables(source) == {"persistent_records"}


def test_current_runtime_cannot_import_legacy_code(tmp_path) -> None:
    source = tmp_path / "kernel/src/platform_kernel"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("from legacy.v8.resource import app\n")
    problems = MODULE.check(tmp_path)
    assert any("LEGACY_IMPORT_INVALID" in problem for problem in problems)


def test_workflow_compiler_rejects_runtime_side_effect_dependencies(tmp_path) -> None:
    source = tmp_path / "packages/workflow-compiler/src/platform_workflow_compiler"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("import sqlite3\n")
    problems = MODULE.check(tmp_path)
    assert any("WORKFLOW_COMPILER_IMPURE: sqlite3" in problem for problem in problems)


def test_assembler_cannot_import_service_runtime(tmp_path) -> None:
    source = tmp_path / "platform/assembler"
    source.mkdir(parents=True)
    (source / "assembler.py").write_text("import platform_data_service\n")
    problems = MODULE.check(tmp_path)
    assert any("ASSEMBLER_RUNTIME_IMPORT_INVALID" in problem for problem in problems)


def test_service_routers_cannot_own_sql(tmp_path) -> None:
    routers = (
        "kernel/src/platform_kernel/api/routes/sessions.py",
        "services/data/src/platform_data_service/api/routers/catalog.py",
        "services/knowledge-next/src/platform_knowledge_next/routers/documents.py",
        "services/executor/src/platform_executor_service/routers/execution.py",
    )
    for relative in routers:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('QUERY = "SELECT value FROM owned_table"\n')
    problems = MODULE.check(tmp_path)
    for relative in routers:
        assert f"{relative}: ROUTER_SQL_INVALID" in problems


def test_services_cannot_import_another_service_database() -> None:
    assert (
        MODULE.violation(
            "platform_knowledge_next.retrieval",
            "platform_data_service.adapters.database",
        )
        == "DISTRIBUTION_DEPENDENCY_INVALID"
    )
    assert (
        MODULE.violation(
            "platform_data_service.catalog.service",
            "platform_knowledge_next.database",
        )
        == "DISTRIBUTION_DEPENDENCY_INVALID"
    )
    assert (
        MODULE.violation(
            "platform_executor_service.control",
            "platform_kernel.database",
        )
        == "DISTRIBUTION_DEPENDENCY_INVALID"
    )


def test_lightweight_agent_is_an_isolated_distribution_boundary() -> None:
    assert MODULE.PACKAGES["platform_lightweight_agent"].allowed_internal == frozenset()
    assert (
        MODULE.violation(
            "platform_lightweight_agent.engine", "platform_kernel.database"
        )
        == "DISTRIBUTION_DEPENDENCY_INVALID"
    )
    assert (
        MODULE.violation("platform_lightweight_agent.runtime", "platform_data_client")
        == "DISTRIBUTION_DEPENDENCY_INVALID"
    )
    assert (
        MODULE.violation("platform_lightweight_agent.runtime", "groundwater_api_client")
        == "DISTRIBUTION_DEPENDENCY_INVALID"
    )
    assert (
        MODULE.violation(
            "platform_kernel.composition", "platform_lightweight_agent.engine"
        )
        == "DISTRIBUTION_DEPENDENCY_INVALID"
    )
    assert (
        MODULE.violation(
            "platform_workflow_compiler.compiler", "platform_lightweight_agent.spec"
        )
        == "DISTRIBUTION_DEPENDENCY_INVALID"
    )


def test_lightweight_optional_extra_cannot_add_an_internal_distribution(
    tmp_path: Path,
) -> None:
    internal = tmp_path / "services/data/client/pyproject.toml"
    internal.parent.mkdir(parents=True)
    internal.write_text('[project]\nname = "platform-data-client"\n')
    differently_named = tmp_path / "packages/groundwater-api-client/pyproject.toml"
    differently_named.parent.mkdir(parents=True)
    differently_named.write_text('[project]\nname = "groundwater-api-client"\n')
    project = tmp_path / "packages/lightweight-agent-core/pyproject.toml"
    project.parent.mkdir(parents=True)
    project.write_text(
        '[project]\nname = "platform-lightweight-agent-core"\n'
        "dependencies = []\n"
        "[project.optional-dependencies]\n"
        'workflow = ["anyio==4.15.1", "platform_data_client==1.0", '
        '"groundwater-api-client==1.0"]\n'
    )
    problems = MODULE.check(tmp_path)
    assert any(
        "LIGHTWEIGHT_INTERNAL_DEPENDENCY_INVALID: platform-data-client" in problem
        for problem in problems
    )
    assert any(
        "LIGHTWEIGHT_INTERNAL_DEPENDENCY_INVALID: groundwater-api-client" in problem
        for problem in problems
    )


def test_distribution_metadata_does_not_hide_unexpected_source_packages(
    tmp_path,
) -> None:
    for name, package in MODULE.PACKAGES.items():
        source_root = tmp_path / package.root.relative_to(ROOT)
        (source_root / name).mkdir(parents=True)
        metadata = source_root / f"{name}.egg-info"
        metadata.mkdir()
        (metadata / "PKG-INFO").write_text(f"Name: {name}\n")
    assert MODULE.check(tmp_path) == []

    unexpected = tmp_path / "services/executor/src/unexpected_package"
    unexpected.mkdir()
    (unexpected / "__init__.py").write_text("")
    assert MODULE.check(tmp_path) == [
        "services/executor/src/unexpected_package: SOURCE_PACKAGE_INVALID: unexpected_package"
    ]


def test_kernel_cannot_import_capability_service_or_private_owner_module() -> None:
    assert (
        MODULE.violation("platform_kernel.agent_runtime.owner", "platform_data_service")
        == "DISTRIBUTION_DEPENDENCY_INVALID"
    )
    assert (
        MODULE.violation(
            "platform_kernel.interaction.owner",
            "platform_kernel.agent_runtime.persistence",
        )
        == "OWNER_DEPENDENCY_INVALID"
    )


def test_kernel_adapter_and_sql_free_boundaries_are_declared() -> None:
    assert "openai" in MODULE.KERNEL_FORBIDDEN_IMPORTS["platform_kernel.composition"]
    assert MODULE.KERNEL_FORBIDDEN_IMPORTS[
        "platform_kernel.agent_runtime.owner"
    ] == frozenset({"agents", "openai"})
    assert MODULE.KERNEL_FORBIDDEN_IMPORTS[
        "platform_kernel.workflow_execution.owner"
    ] == frozenset({"httpx"})
    assert "platform_kernel.interaction.owner" in MODULE.SQL_FREE_KERNEL_MODULES
    assert "platform_kernel.agent_runtime.owner" in MODULE.SQL_FREE_KERNEL_MODULES
    assert "platform_kernel.agent_runtime.memory" in MODULE.SQL_FREE_KERNEL_MODULES
    assert (
        "platform_kernel.agent_runtime.sdk_execution.segment_runner"
        in MODULE.SQL_FREE_KERNEL_MODULES
    )
    assert (
        "platform_kernel.agent_runtime.result_processing"
        in MODULE.SQL_FREE_KERNEL_MODULES
    )
    assert "platform_kernel.workflow_execution.owner" in MODULE.SQL_FREE_KERNEL_MODULES
    assert (
        MODULE.violation(
            "platform_kernel.workflow_execution.owner",
            "platform_kernel.agent_runtime.models",
        )
        == "OWNER_DEPENDENCY_INVALID"
    )
    assert (
        "platform_kernel.workflow_execution"
        in MODULE.KERNEL_FORBIDDEN_IMPORTS[
            "platform_kernel.agent_runtime.sdk_execution.segment_runner"
        ]
    )
    assert (
        "sqlite3"
        in MODULE.KERNEL_FORBIDDEN_IMPORTS[
            "platform_kernel.agent_runtime.result_processing"
        ]
    )
    assert {"agents", "openai", "sqlite3"} <= MODULE.KERNEL_FORBIDDEN_IMPORTS[
        "platform_kernel.agent_runtime.memory"
    ]
    assert {
        "agents",
        "openai",
        "httpx",
        "sqlite3",
        "platform_kernel.database",
        "platform_kernel.agent_runtime.persistence",
        "platform_kernel.workflow_execution",
    } <= MODULE.KERNEL_FORBIDDEN_IMPORTS["platform_kernel.agent_runtime.evidence"]
    assert "platform_kernel.agent_runtime.evidence" in MODULE.SQL_FREE_KERNEL_MODULES


def test_table_ownership_rejects_cross_owner_access() -> None:
    assert MODULE.table_ownership_violation(
        "platform_kernel.workflow_execution.persistence", "agent_actions"
    )
    assert MODULE.table_ownership_violation(
        "platform_kernel.interaction.owner", "workflow_dispatches"
    )
    assert not MODULE.table_ownership_violation(
        "platform_kernel.agent_runtime.persistence.run_lifecycle", "agent_actions"
    )
    assert MODULE.table_ownership_violation(
        "platform_kernel.agent_runtime.owner", "agent_actions"
    )
    assert MODULE.table_ownership_violation(
        "platform_kernel.agent_runtime.memory", "agent_memory"
    )
    assert not MODULE.table_ownership_violation(
        "platform_executor_service.task_execution", "node_attempts"
    )
    assert MODULE.table_ownership_violation(
        "platform_executor_service.worker", "node_attempts"
    )
    assert "schema_version" in MODULE.TABLE_OWNERS["platform_kernel"]


def test_embedding_client_has_no_service_dependencies() -> None:
    value = tomllib.loads(
        (ROOT / "packages/embedding-client/pyproject.toml").read_text(encoding="utf-8")
    )
    assert value["project"]["dependencies"] == [
        "httpx==0.28.1",
        "pydantic==2.13.5",
    ]


def test_data_has_no_embedding_dependency() -> None:
    value = tomllib.loads(
        (ROOT / "services/data/pyproject.toml").read_text(encoding="utf-8")
    )
    assert not any(
        dependency.startswith("platform-embedding-client")
        for dependency in value["project"]["dependencies"]
    )
    assert MODULE.PACKAGES["platform_data_service"].allowed_internal == frozenset(
        {"platform_protocol_contracts"}
    )


def test_owner_facades_depend_on_ports_instead_of_other_owner_modules() -> None:
    assert (
        MODULE.violation(
            "platform_kernel.agent_runtime.owner",
            "platform_kernel.workflow_execution.owner",
        )
        == "OWNER_DEPENDENCY_INVALID"
    )
    assert (
        MODULE.violation(
            "platform_kernel.interaction.owner",
            "platform_kernel.agent_runtime.owner",
        )
        == "OWNER_DEPENDENCY_INVALID"
    )
    assert (
        MODULE.violation(
            "platform_kernel.agent_runtime.owner",
            "platform_kernel.application.ports",
        )
        is None
    )
    assert (
        MODULE.violation(
            "platform_kernel.interaction.history",
            "platform_kernel.agent_runtime.persistence.run_queries",
        )
        == "OWNER_DEPENDENCY_INVALID"
    )
    assert (
        MODULE.violation(
            "platform_kernel.agent_runtime.tooling",
            "platform_kernel.tasks",
        )
        == "OWNER_DEPENDENCY_INVALID"
    )


def test_kernel_metadata_has_no_capability_or_product_dependency() -> None:
    value = tomllib.loads((ROOT / "kernel/pyproject.toml").read_text(encoding="utf-8"))
    dependencies = value["project"]["dependencies"]
    forbidden = (
        "platform-resource-service",
        "platform-knowledge-service",
        "platform-mapfactory-service",
        "platform-executor-service",
    )
    assert not any(
        dependency.startswith(name) for dependency in dependencies for name in forbidden
    )


def test_single_reply_sdk_has_no_persistence_access_and_renderer_has_named_reads(
    tmp_path,
):
    root = tmp_path / "src"
    module = root / "platform_kernel/agent_runtime/sdk_execution/initial_execution.py"
    module.parent.mkdir(parents=True)
    module.write_text("from .models import SubmittedReplyV6\n")
    assert MODULE.sdk_persistence_imports(module, root) == []
    for forbidden in (
        "from ..persistence import get_run\n",
        "from ..persistence import save_plan\n",
        "from ..persistence import *\n",
        "from .. import persistence\n",
        "import platform_kernel.agent_runtime.persistence as store\n",
    ):
        module.write_text(forbidden)
        assert MODULE.sdk_persistence_imports(module, root)
    renderer = module.parents[1] / "reply.py"
    renderer.write_text(
        "from .persistence import allowed_workflow_run_ids, get_run, load_execution_context\n"
    )
    assert MODULE.sdk_persistence_imports(renderer, root) == []
    renderer.write_text("from .persistence import save_plan\n")
    assert MODULE.sdk_persistence_imports(renderer, root)
    assert {
        "sqlite3",
        "platform_kernel.database",
        "platform_kernel.workflow_execution",
    } <= MODULE.KERNEL_FORBIDDEN_IMPORTS[
        "platform_kernel.agent_runtime.sdk_execution.initial_execution"
    ]
    assert "platform_kernel.agent_runtime.reply" in MODULE.SQL_FREE_KERNEL_MODULES
