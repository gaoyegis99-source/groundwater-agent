from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.checks.import_boundaries.ast_scan import (
    READ_ONLY_TABLES,
    api_workflow_executions,
    contains_raw_sql,
    declared_imports,
    declared_schema_tables,
    imports,
    module_exists,
    module_name,
    sdk_persistence_imports,
    sql_tables,
    sql_writes,
    table_ownership_violation,
    transaction_command_declarations,
    violation,
)
from scripts.checks.import_boundaries.report import check, render
from scripts.checks.import_boundaries.rules import (
    CROSS_OWNER_TRANSACTION_COMMANDS,
    CURRENT_SOURCE_DIRECTORIES,
    EXCLUSIVE_TABLE_MODULES,
    INTERNAL_PACKAGES,
    KERNEL_FORBIDDEN_IMPORTS,
    KERNEL_MODULE_IMPORTS,
    PACKAGES,
    ROOT,
    SDK_PERSISTENCE_IMPORTS,
    SQL_FREE_KERNEL_MODULES,
    TABLE_OWNERS,
    WORKFLOW_COMPILER_FORBIDDEN_IMPORTS,
    Package,
)

__all__ = [
    "CURRENT_SOURCE_DIRECTORIES",
    "CROSS_OWNER_TRANSACTION_COMMANDS",
    "EXCLUSIVE_TABLE_MODULES",
    "INTERNAL_PACKAGES",
    "KERNEL_FORBIDDEN_IMPORTS",
    "KERNEL_MODULE_IMPORTS",
    "PACKAGES",
    "READ_ONLY_TABLES",
    "ROOT",
    "SDK_PERSISTENCE_IMPORTS",
    "SQL_FREE_KERNEL_MODULES",
    "TABLE_OWNERS",
    "WORKFLOW_COMPILER_FORBIDDEN_IMPORTS",
    "Package",
    "api_workflow_executions",
    "check",
    "contains_raw_sql",
    "declared_imports",
    "declared_schema_tables",
    "imports",
    "module_exists",
    "module_name",
    "render",
    "sdk_persistence_imports",
    "sql_tables",
    "sql_writes",
    "table_ownership_violation",
    "transaction_command_declarations",
    "violation",
]


def main() -> None:
    problems = check()
    if problems:
        print(render(problems), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
