from __future__ import annotations

import ast
from pathlib import Path
import tomllib

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from .ast_scan import (
    READ_ONLY_TABLES,
    api_workflow_executions,
    contains_raw_sql,
    declared_schema_tables,
    declared_imports,
    imports,
    module_name,
    sdk_persistence_imports,
    sql_tables,
    sql_writes,
    table_ownership_violation,
    transaction_command_declarations,
    violation,
)
from .rules import (
    CURRENT_SOURCE_DIRECTORIES,
    CROSS_OWNER_TRANSACTION_COMMANDS,
    INTERNAL_PACKAGES,
    KERNEL_FORBIDDEN_IMPORTS,
    KERNEL_MODULE_IMPORTS,
    PACKAGES,
    ROOT,
    SQL_FREE_KERNEL_MODULES,
    TABLE_OWNERS,
    WORKFLOW_COMPILER_FORBIDDEN_IMPORTS,
)


_CONTROL_PROPOSAL_FACADE = "platform_kernel.interaction.control_proposals"
_CONTROL_PROPOSAL_LAYERS = {
    "intent": "control_intents.py",
    "queries": "control_proposal_queries.py",
    "targets": "control_proposal_targets.py",
    "commands": "control_proposal_commands.py",
}


def _strongly_connected_modules(edges: dict[str, set[str]]) -> list[list[str]]:
    """Return non-trivial SCCs without depending on import order.

    The general import rule checker validates allowed edges.  This smaller
    graph protects the new responsibility split itself, including imports
    nested inside functions, so an implementation cannot hide a circular
    dependency behind a deferred import.
    """

    index = 0
    stack: list[str] = []
    active: set[str] = set()
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    groups: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indexes[node] = lowlinks[node] = index
        index += 1
        stack.append(node)
        active.add(node)
        for child in sorted(edges.get(node, ())):
            if child not in indexes:
                visit(child)
                lowlinks[node] = min(lowlinks[node], lowlinks[child])
            elif child in active:
                lowlinks[node] = min(lowlinks[node], indexes[child])
        if lowlinks[node] == indexes[node]:
            group: list[str] = []
            while True:
                child = stack.pop()
                active.remove(child)
                group.append(child)
                if child == node:
                    break
            if len(group) > 1:
                groups.append(sorted(group))

    for node in sorted(edges):
        if node not in indexes:
            visit(node)
    return sorted(groups)


def _control_proposal_layer_problems(root: Path) -> list[str]:
    """Enforce the directional split of the control-proposal implementation."""

    source_root = root / "kernel" / "src"
    interaction_root = source_root / "platform_kernel" / "interaction"
    if not interaction_root.is_dir():
        return []
    facade = interaction_root / "control_proposals.py"
    problems: list[str] = []
    if not facade.is_file():
        return [
            "kernel/src/platform_kernel/interaction/control_proposals.py: CONTROL_PROPOSAL_FACADE_MISSING"
        ]
    layers: dict[str, Path] = {}
    for role, filename in _CONTROL_PROPOSAL_LAYERS.items():
        path = interaction_root / filename
        if not path.is_file():
            problems.append(
                f"{path.relative_to(root)}: CONTROL_PROPOSAL_LAYER_MISSING: {role}"
            )
        else:
            layers[role] = path

    facade_tree = ast.parse(facade.read_text(encoding="utf-8"), filename=str(facade))
    if any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        for node in facade_tree.body
    ):
        problems.append(
            f"{facade.relative_to(root)}: CONTROL_PROPOSAL_FACADE_IMPLEMENTATION_INVALID"
        )

    for role, path in layers.items():
        for line, target in imports(path, source_root):
            if target == _CONTROL_PROPOSAL_FACADE or target.startswith(
                f"{_CONTROL_PROPOSAL_FACADE}."
            ):
                problems.append(
                    f"{path.relative_to(root)}:{line}: "
                    f"CONTROL_PROPOSAL_FACADE_BACKWARD_IMPORT_INVALID: {role}"
                )
        writes = sql_writes(path)
        if role in {"queries", "targets"} and writes:
            problems.append(
                f"{path.relative_to(root)}: CONTROL_PROPOSAL_QUERY_WRITE_INVALID: "
                f"{role} -> {sorted(writes)}"
            )
        if role == "intent":
            forbidden = {
                target
                for _, target in imports(path, source_root)
                if target == "sqlite3" or target.startswith("platform_kernel.database")
            }
            if forbidden:
                problems.append(
                    f"{path.relative_to(root)}: CONTROL_PROPOSAL_PURE_INTENT_INVALID: "
                    f"{sorted(forbidden)}"
                )

    layer_modules = {module_name(path, source_root) for path in layers.values()}
    layer_edges = {
        module_name(path, source_root): {
            target
            for _, target in imports(path, source_root)
            if target in layer_modules
        }
        for path in layers.values()
    }
    for group in _strongly_connected_modules(layer_edges):
        problems.append(
            "kernel/src/platform_kernel/interaction: "
            f"CONTROL_PROPOSAL_LAYER_CYCLE_INVALID: {group}"
        )

    for path in sorted((source_root / "platform_kernel").rglob("*.py")):
        if path == facade:
            continue
        for line, target in imports(path, source_root):
            if target == _CONTROL_PROPOSAL_FACADE or target.startswith(
                f"{_CONTROL_PROPOSAL_FACADE}."
            ):
                problems.append(
                    f"{path.relative_to(root)}:{line}: "
                    "CONTROL_PROPOSAL_FACADE_IMPORT_INVALID"
                )
    return problems


def check(root: Path = ROOT) -> list[str]:
    problems = []
    lightweight_project = root / "packages/lightweight-agent-core/pyproject.toml"
    if lightweight_project.is_file():
        internal_projects = {
            root / configured.root.relative_to(ROOT).parent / "pyproject.toml"
            for configured in PACKAGES.values()
        }
        workspace_project = root / "pyproject.toml"
        if workspace_project.is_file():
            workspace = tomllib.loads(workspace_project.read_text(encoding="utf-8"))
            internal_projects.update(
                root / member / "pyproject.toml"
                for member in workspace.get("tool", {})
                .get("uv", {})
                .get("workspace", {})
                .get("members", [])
            )
        internal_names = {
            canonicalize_name(
                tomllib.loads(path.read_text(encoding="utf-8"))["project"]["name"]
            )
            for path in internal_projects
            if path.is_file()
        }
        project = tomllib.loads(lightweight_project.read_text(encoding="utf-8"))[
            "project"
        ]
        declared = list(project.get("dependencies", []))
        declared.extend(
            dependency
            for group in project.get("optional-dependencies", {}).values()
            for dependency in group
        )
        for dependency in declared:
            try:
                name = canonicalize_name(Requirement(dependency).name)
            except InvalidRequirement:
                problems.append(
                    "packages/lightweight-agent-core/pyproject.toml: "
                    "LIGHTWEIGHT_DEPENDENCY_INVALID"
                )
                continue
            if name in internal_names:
                problems.append(
                    "packages/lightweight-agent-core/pyproject.toml: "
                    f"LIGHTWEIGHT_INTERNAL_DEPENDENCY_INVALID: {name}"
                )
    for package_name, configured in PACKAGES.items():
        source_root = root / configured.root.relative_to(ROOT)
        if not source_root.is_dir():
            problems.append(f"{source_root.relative_to(root)}: SOURCE_ROOT_MISSING")
            continue
        for path in sorted(source_root.iterdir()):
            # Local wheel/editable builds leave distribution metadata beside sources.
            if path.is_dir() and path.name.endswith(".egg-info"):
                continue
            if path.name != package_name:
                problems.append(
                    f"{path.relative_to(root)}: SOURCE_PACKAGE_INVALID: {path.name}"
                )
        package_root = source_root / package_name
        owners = TABLE_OWNERS.get(package_name)
        database_module = package_root / "database.py"
        if owners is not None and database_module.is_file():
            schema_package = package_root / "schema"
            # Classify the declared persistent schema, not SQL embedded in the
            # migration implementation.  A migration can legitimately mention
            # a transient rename target (for example a v29 upgrade table), and
            # treating that as a product table turns the ownership gate into a
            # path-text allowlist again.
            if schema_package.is_dir():
                schema_tables = {
                    table
                    for schema_path in sorted(schema_package.rglob("*.py"))
                    for table in sql_tables(schema_path)
                    if table != "sqlite_master"
                }
            else:
                schema_tables = declared_schema_tables(database_module)
            if schema_tables != set(owners):
                problems.append(
                    f"{database_module.relative_to(root)}: TABLE_CLASSIFICATION_INVALID: "
                    f"schema={sorted(schema_tables)} owners={sorted(owners)}"
                )
        for path in sorted(package_root.rglob("*.py")):
            source = module_name(path, source_root)
            expected_commands = CROSS_OWNER_TRANSACTION_COMMANDS.get(source)
            if expected_commands is not None:
                declared_commands = transaction_command_declarations(path)
                if declared_commands != expected_commands:
                    problems.append(
                        f"{path.relative_to(root)}: "
                        "CROSS_OWNER_TRANSACTION_COMMAND_INVALID: "
                        f"declared={sorted(declared_commands)} "
                        f"expected={sorted(expected_commands)}"
                    )
            tables = sql_tables(path)
            problems.extend(
                f"{path.relative_to(root)}: TABLE_OWNERSHIP_INVALID: "
                f"{source} -> {table}"
                for table in sorted(tables)
                if table_ownership_violation(source, table)
                and not (
                    table in READ_ONLY_TABLES.get(source, ())
                    and table not in sql_writes(path)
                )
            )
            if package_name == "platform_kernel":
                parts = source.split(".")
                module = parts[1] if len(parts) > 1 else ""
                if module not in KERNEL_MODULE_IMPORTS:
                    problems.append(
                        f"{path.relative_to(root)}: CORE_MODULE_UNCLASSIFIED: {source}"
                    )
                    continue
                if module == "api":
                    problems.extend(
                        f"{path.relative_to(root)}:{line}: "
                        "API_WORKFLOW_EXECUTION_INVALID"
                        for line in api_workflow_executions(path)
                    )
                if source in SQL_FREE_KERNEL_MODULES and tables:
                    problems.append(
                        f"{path.relative_to(root)}: RAW_SQL_INVALID: {source}"
                    )
            problems.extend(
                f"{path.relative_to(root)}:{line}: SDK_PERSISTENCE_BOUNDARY_INVALID: "
                f"{source} -> {target}"
                for line, target in sdk_persistence_imports(path, source_root)
            )
            for line, target in imports(path, source_root):
                if any(
                    target == forbidden or target.startswith(f"{forbidden}.")
                    for forbidden in KERNEL_FORBIDDEN_IMPORTS.get(source, ())
                ):
                    problems.append(
                        f"{path.relative_to(root)}:{line}: "
                        f"ADAPTER_BOUNDARY_INVALID: {source} -> {target}"
                    )
                error = violation(source, target)
                if error:
                    problems.append(
                        f"{path.relative_to(root)}:{line}: {error}: {source} -> {target}"
                    )
    router_files = [
        *(root / "kernel/src/platform_kernel/api/routes").rglob("*.py"),
        *(root / "services/data/src/platform_data_service/api").rglob("*.py"),
        *(root / "services/knowledge-next/src/platform_knowledge_next/routers").rglob(
            "*.py"
        ),
        *(root / "services/executor/src/platform_executor_service/routers").rglob(
            "*.py"
        ),
        root / "services/knowledge-next/src/platform_knowledge_next/api.py",
        root / "services/executor/src/platform_executor_service/app.py",
    ]
    problems.extend(
        f"{path.relative_to(root)}: ROUTER_SQL_INVALID"
        for path in router_files
        if path.is_file() and contains_raw_sql(path)
    )
    for service, public_prefix in (
        ("data", "/api/v12/data"),
        ("knowledge-next", "/api/v12/knowledge"),
    ):
        source = root / "services" / service / "src"
        for path in source.rglob("*.py"):
            if public_prefix in path.read_text(encoding="utf-8"):
                problems.append(
                    f"{path.relative_to(root)}: GATEWAY_PUBLIC_PATH_INVALID"
                )
    current_directories = tuple(
        root / directory.relative_to(ROOT) for directory in CURRENT_SOURCE_DIRECTORIES
    )
    for directory in current_directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.py")):
            for line, target in declared_imports(path):
                if target == "legacy" or target.startswith("legacy."):
                    problems.append(
                        f"{path.relative_to(root)}:{line}: LEGACY_IMPORT_INVALID"
                    )
    assembler = root / "platform" / "assembler"
    for path in sorted(assembler.glob("*.py")):
        for line, target in declared_imports(path):
            target_root = target.split(".", 1)[0]
            if (
                target_root in INTERNAL_PACKAGES
                and target_root != "platform_workflow_compiler"
            ):
                problems.append(
                    f"{path.relative_to(root)}:{line}: ASSEMBLER_RUNTIME_IMPORT_INVALID: "
                    f"{target}"
                )
    compiler = root / "packages/workflow-compiler/src/platform_workflow_compiler"
    for path in sorted(compiler.rglob("*.py")):
        for line, target in declared_imports(path):
            if any(
                target == forbidden or target.startswith(f"{forbidden}.")
                for forbidden in WORKFLOW_COMPILER_FORBIDDEN_IMPORTS
            ):
                problems.append(
                    f"{path.relative_to(root)}:{line}: WORKFLOW_COMPILER_IMPURE: "
                    f"{target}"
                )
    problems.extend(_control_proposal_layer_problems(root))
    return problems


def render(problems: list[str]) -> str:
    return "\n".join(problems)
