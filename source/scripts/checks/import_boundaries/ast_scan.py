from __future__ import annotations

import ast
from pathlib import Path
import re

from .rules import (
    CROSS_OWNER_TRANSACTION_COMMANDS,
    EXCLUSIVE_TABLE_MODULES,
    INTERNAL_PACKAGES,
    KERNEL_MODULE_IMPORTS,
    PACKAGES,
    SDK_PERSISTENCE_IMPORTS,
    SQL_TABLE_PATTERN,
    TABLE_OWNERS,
)


def module_name(path: Path, source_root: Path) -> str:
    relative = path.relative_to(source_root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def module_exists(module: str, source_root: Path) -> bool:
    path = source_root.joinpath(*module.split("."))
    return path.with_suffix(".py").is_file() or (path / "__init__.py").is_file()


def imports(path: Path, source_root: Path) -> list[tuple[int, str]]:
    source = module_name(path, source_root)
    package = source if path.name == "__init__.py" else source.rpartition(".")[0]
    found = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parent = package.split(".")
                prefix = parent[: len(parent) - node.level + 1]
                target = ".".join([*prefix, node.module] if node.module else prefix)
            else:
                target = node.module or ""
            found.append((node.lineno, target))
            found.extend(
                (node.lineno, candidate)
                for alias in node.names
                if target
                and module_exists(candidate := f"{target}.{alias.name}", source_root)
            )
    return found


def declared_imports(path: Path) -> list[tuple[int, str]]:
    """Return import targets without resolving package-relative names."""
    found = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.lineno, node.module))
    return found


def sdk_persistence_imports(path: Path, source_root: Path) -> list[tuple[int, str]]:
    source = module_name(path, source_root)
    allowed = SDK_PERSISTENCE_IMPORTS.get(source)
    if allowed is None:
        return []
    package = source.rpartition(".")[0]
    target_owner = "platform_kernel.agent_runtime.persistence"
    problems = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            problems.extend(
                (node.lineno, alias.name)
                for alias in node.names
                if alias.name == target_owner
            )
        elif isinstance(node, ast.ImportFrom):
            parent = package.split(".")
            prefix = parent[: len(parent) - node.level + 1] if node.level else []
            target = ".".join([*prefix, node.module] if node.module else prefix)
            for alias in node.names:
                if (target == target_owner and alias.name not in allowed) or (
                    target == target_owner.rpartition(".")[0]
                    and alias.name == "persistence"
                ):
                    problems.append((node.lineno, f"{target}.{alias.name}"))
    return problems


def sql_tables(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        match.group(1).lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        for match in SQL_TABLE_PATTERN.finditer(node.value)
    }


READ_ONLY_TABLES = {
    "platform_kernel.agent_runtime.persistence.run_lifecycle": frozenset(
        {"run_controls"}
    ),
    "platform_kernel.tasks.persistence": frozenset(
        {
            "sessions",
            "messages",
            "agent_runs",
            "workflow_runs",
            "workflow_drafts",
            "confirmations",
            "session_events",
        }
    ),
    # Preparation is a read projection over Agent-owned lifecycle facts. It may
    # inspect actions, but all action writes remain exclusive to Agent persistence.
    "platform_kernel.tasks.preparation": frozenset(
        {"agent_actions", "agent_runs", "sessions"}
    ),
    # Task progress is a read projection over lifecycle facts owned by the
    # interaction, Agent and workflow modules. Writes remain with those owners.
    "platform_kernel.tasks.progress": frozenset(
        {
            "sessions",
            "agent_runs",
            "workflow_runs",
            "workflow_drafts",
            "confirmations",
            "session_events",
        }
    ),
    "platform_kernel.interaction.control_persistence": frozenset(
        {"agent_runs", "workflow_runs"}
    ),
    # Control proposal projections resolve and display lifecycle state owned by
    # other modules.  They are explicitly read-only; the command layer retains
    # all proposal writes and durable event ordering.
    "platform_kernel.interaction.control_proposal_queries": frozenset(
        {"agent_runs", "workflow_runs"}
    ),
    "platform_kernel.interaction.control_proposal_targets": frozenset(
        {"agent_runs", "confirmation_tasks", "tasks", "workflow_runs"}
    ),
    # Public ownership guards are a read projection spanning the three lifecycle
    # owners. They never change Agent or Workflow state.
    "platform_kernel.interaction.access": frozenset(
        {"agent_actions", "agent_runs", "workflow_runs"}
    ),
}


def sql_writes(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    pattern = re.compile(
        r"\b(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|UPDATE|DELETE\s+FROM|(?:CREATE|DROP|ALTER)\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?)\s+([a-z_][a-z0-9_]*)",
        re.IGNORECASE,
    )
    return {
        match.group(1).lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        for match in pattern.finditer(node.value)
    }


def transaction_command_declarations(path: Path) -> dict[str, frozenset[str]]:
    """Read literal ``@transaction_command`` declarations without importing code."""

    declarations: dict[str, frozenset[str]] = {}
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Name)
                and decorator.func.id == "transaction_command"
            ):
                continue
            values = {keyword.arg: keyword.value for keyword in decorator.keywords}
            name = values.get("name")
            tables = values.get("tables")
            if not (
                isinstance(name, ast.Constant)
                and isinstance(name.value, str)
                and isinstance(tables, (ast.Tuple, ast.List))
                and all(
                    isinstance(value, ast.Constant) and isinstance(value.value, str)
                    for value in tables.elts
                )
            ):
                declarations[f"INVALID:{node.name}"] = frozenset()
                continue
            declarations[node.name] = frozenset(value.value for value in tables.elts)
    return declarations


def contains_raw_sql(path: Path) -> bool:
    pattern = re.compile(
        r"\b(?:SELECT\s+.+?\s+FROM|INSERT(?:\s+OR\s+\w+)?\s+INTO|"
        r"UPDATE\s+[a-z_][a-z0-9_]*\s+SET|DELETE\s+FROM|CREATE\s+TABLE)\b",
        re.IGNORECASE | re.DOTALL,
    )
    return any(
        pattern.search(node.value)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def table_ownership_violation(source: str, table: str) -> bool:
    package = source.split(".", 1)[0]
    configured = TABLE_OWNERS.get(package, {}).get(table)
    if configured is None:
        return False
    declared_commands = CROSS_OWNER_TRANSACTION_COMMANDS.get(source, {})
    if any(table in tables for tables in declared_commands.values()):
        return False
    if source == f"{package}.database" or source.startswith(f"{package}.schema"):
        return False
    if table in EXCLUSIVE_TABLE_MODULES:
        exclusive = EXCLUSIVE_TABLE_MODULES[table]
        allowed = (exclusive,) if isinstance(exclusive, str) else exclusive
        return source not in allowed
    owners = (configured,) if isinstance(configured, str) else configured
    return all(
        source != owner and not source.startswith(f"{owner}.") for owner in owners
    )


def _kernel_violation(source: str, target: str) -> str | None:
    source_parts = source.split(".")
    source_module = source_parts[1] if len(source_parts) > 1 else ""
    target_parts = target.split(".")
    target_module = target_parts[1] if len(target_parts) > 1 else ""
    owner_modules = {"agent_runtime", "interaction", "workflow_execution", "tasks"}
    if (
        source_module in owner_modules
        and target_module in owner_modules
        and target_module != source_module
    ):
        return "OWNER_DEPENDENCY_INVALID"
    if target == "platform_kernel":
        return "CORE_DEPENDENCY_INVALID"
    if target_module == source_module:
        return None
    if any(
        target == allowed or target.startswith(allowed + ".")
        for allowed in KERNEL_MODULE_IMPORTS[source_module]
    ):
        return None
    return (
        "OWNER_DEPENDENCY_INVALID"
        if source_module
        in {
            "agent_runtime",
            "interaction",
            "workflow_execution",
        }
        else "CORE_DEPENDENCY_INVALID"
    )


def violation(source: str, target: str) -> str | None:
    source_root = source.split(".", 1)[0]
    target_root = target.split(".", 1)[0]
    package = PACKAGES[source_root]
    if target_root in INTERNAL_PACKAGES and target_root not in {
        source_root,
        *package.allowed_internal,
    }:
        return "DISTRIBUTION_DEPENDENCY_INVALID"
    if source_root == "platform_kernel" and target_root == "platform_kernel":
        return _kernel_violation(source, target)
    return None


def _scope_nodes(scope: ast.AST):
    pending = list(ast.iter_child_nodes(scope))
    while pending:
        node = pending.pop()
        yield node
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
        ):
            continue
        pending.extend(ast.iter_child_nodes(node))


def api_workflow_executions(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    scopes = [
        tree,
        *(
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
        ),
    ]
    found = []
    for scope in scopes:
        nodes = tuple(_scope_nodes(scope))
        aliases = {
            target.id
            for node in nodes
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "workflow_execution"
            for target in (
                node.targets if isinstance(node, ast.Assign) else (node.target,)
            )
            if isinstance(target, ast.Name)
        }
        found.extend(
            node.lineno
            for node in nodes
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"enqueue_confirmed_batch", "tick"}
            and (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id in aliases
                or isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "workflow_execution"
            )
        )
    return sorted(set(found))
