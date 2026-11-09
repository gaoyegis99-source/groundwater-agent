#!/usr/bin/env python3
"""Validate the maintained V6 source and XDG lifecycle layout contract.

The checker is deliberately read-only.  It validates the schema-3 declaration
and rejects a repository-local ``runtime`` lifecycle without reading Docker,
instance data, configuration values, or secrets.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = SOURCE_ROOT.parent
LAYOUT_PATH = PROJECT_ROOT / "project-layout.json"

SCRIPT_AREAS = frozenset(
    {
        "checks",
        "development",
        "evaluation",
        "lib",
        "maintenance",
        "migration",
        "operations",
        "release",
    }
)
SOURCE_AREAS = frozenset(
    {
        "kernel",
        "services",
        "packages",
        "domains",
        "products",
        "platform",
        "web",
        "scripts",
        "tests",
        "docs",
        "migration-assets",
    }
)
DOC_AREAS = frozenset(
    {"architecture", "capabilities", "operations", "maintenance", "reference"}
)
FORBIDDEN_RETRIEVAL_PATHS = (
    Path("archive"),
    Path("evidence"),
    Path("golden"),
    Path("release"),
    Path("docs/verification"),
)
TEXT_SUFFIXES = frozenset({".json", ".md", ".py", ".sh", ".toml", ".yaml", ".yml"})
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\n]+)\)")
LEGACY_REFERENCE_ALLOWLIST = frozenset(
    {
        Path("scripts/maintenance/check_internal_layout.py"),
        Path("tests/architecture/test_internal_layout.py"),
    }
)
LEGACY_REFERENCES = {
    "LEGACY_DEPLOYMENT_COMMAND": re.compile(r"(?<![A-Za-z0-9_.-])scripts/deployment/"),
    "LEGACY_VERIFICATION_ROOT": re.compile(r"(?<![A-Za-z0-9_.-])verification/current/"),
}


def _child_names(directory: Path) -> set[str]:
    if not directory.is_dir():
        return set()
    return {child.name for child in directory.iterdir() if child.is_dir()}


def _require_directories(
    errors: list[str], parent: Path, names: frozenset[str]
) -> None:
    missing = sorted(names - _child_names(parent))
    if missing:
        errors.append(
            f"missing directories beneath {parent.relative_to(SOURCE_ROOT)}: {', '.join(missing)}"
        )


def _markdown_target(raw_target: str) -> str | None:
    target = raw_target.strip()
    if target.startswith("<") and ">" in target:
        target = target[1 : target.index(">")]
    else:
        target = target.split(maxsplit=1)[0]
    target = target.split("#", maxsplit=1)[0]
    if (
        not target
        or target.startswith(("/", "http://", "https://", "mailto:"))
        or "${" in target
    ):
        return None
    return target


def validate_layout_contract(errors: list[str]) -> None:
    if not LAYOUT_PATH.is_file():
        errors.append(f"missing project layout contract: {LAYOUT_PATH}")
        return
    try:
        layout = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        errors.append(f"invalid project layout contract: {error}")
        return
    if layout.get("schema_version") != 4:
        errors.append("project layout schema_version must be 4")
    if layout.get("project") != "groundwater-v6":
        errors.append("project layout project must be groundwater-v6")
    if layout.get("source") != "source":
        errors.append("project layout source must be source")
    expected_lifecycle = {
        "config": "xdg-config",
        "data": "xdg-data",
        "state": "xdg-state",
        "cache": "xdg-cache",
        "runtime": "xdg-runtime",
    }
    if layout.get("lifecycle") != expected_lifecycle:
        errors.append("project layout lifecycle must use the schema-3 XDG roles")
    docker = layout.get("docker")
    if not isinstance(docker, dict) or docker.get("data_root") != "xdg-cache/docker":
        errors.append("project layout Docker data root must be xdg-cache/docker")
    elif docker.get("socket") != "docker.sock" or docker.get("authoritative_volumes") is not False:
        errors.append("project layout Docker boundary is invalid")
    if layout.get("shared_runtime_paths") != [] or layout.get("shared_docker_resources") != []:
        errors.append("project layout must not declare shared runtime resources")


def validate_runtime(errors: list[str]) -> None:
    """Retain the public checker hook while enforcing the no-runtime invariant."""

    legacy = PROJECT_ROOT / "runtime"
    if legacy.exists() or legacy.is_symlink():
        errors.append("legacy repository runtime directory remains: runtime")


def validate_source_tree(errors: list[str]) -> None:
    _require_directories(errors, SOURCE_ROOT, SOURCE_AREAS)
    _require_directories(errors, SOURCE_ROOT / "scripts", SCRIPT_AREAS)
    _require_directories(errors, SOURCE_ROOT / "docs", DOC_AREAS)
    for path in SOURCE_ROOT.glob("scripts/*"):
        if path.is_file() and path.name not in {"__init__.py", "README.md"}:
            errors.append(
                f"flat maintenance command remains at scripts root: {path.name}"
            )


def validate_retrieval_inputs(errors: list[str]) -> None:
    for relative in FORBIDDEN_RETRIEVAL_PATHS:
        path = SOURCE_ROOT / relative
        if path.exists() or path.is_symlink():
            errors.append(f"retrieval pollution path remains: {relative}")
    maintenance_history = PROJECT_ROOT / "operations/maintenance"
    if maintenance_history.exists() or maintenance_history.is_symlink():
        errors.append("retrieval pollution path remains: ../operations/maintenance")


def validate_maintenance_module_boundaries(errors: list[str]) -> None:
    thin_commands = {
        Path("scripts/evaluation/run_kernel_sandbox_pressure.py"):
            Path("scripts/evaluation/kernel_sandbox_pressure/command.py"),
        Path("scripts/checks/check_compose_e2e.py"):
            Path("scripts/checks/compose_e2e/command.py"),
        Path("scripts/checks/check_data_knowledge_e2e.py"):
            Path("scripts/checks/data_knowledge_e2e/command.py"),
    }
    for facade, implementation in thin_commands.items():
        facade_path = SOURCE_ROOT / facade
        implementation_path = SOURCE_ROOT / implementation
        if not facade_path.is_file() or len(facade_path.read_text().splitlines()) > 30:
            errors.append(f"maintenance CLI is not a thin facade: {facade}")
        if not implementation_path.is_file():
            errors.append(f"maintenance CLI package is missing: {implementation}")

    required_modules = {
        Path("scripts/evaluation/kernel_sandbox_pressure"): {
            "command.py", "context.py", "environment.py", "faults.py", "probe.py",
            "report.py", "scenarios/control.py", "scenarios/dialogue.py",
            "scenarios/provider.py", "scenarios/recovery.py",
        },
        Path("scripts/checks/compose_e2e"): {
            "assertions.py", "command.py", "environment.py", "report.py",
            "scenarios.py", "topology.py",
        },
        Path("scripts/checks/data_knowledge_e2e"): {
            "command.py", "context.py", "diagnostics.py", "environment.py",
            "report.py", "scenarios.py",
        },
    }
    forbidden_command_names = {
        "Client", "AsyncClient", "MockTransport", "docker_executable",
        "subprocess", "run_capacity", "run_suite",
    }
    for package, required in required_modules.items():
        package_root = SOURCE_ROOT / package
        present = {
            str(path.relative_to(package_root))
            for path in package_root.rglob("*.py")
        }
        missing = sorted(required - present)
        if missing:
            errors.append(f"maintenance package modules missing in {package}: {missing}")
        for path in package_root.rglob("*.py"):
            line_count = len(path.read_text(encoding="utf-8").splitlines())
            limit = 200 if path.name == "command.py" else 650
            if line_count > limit:
                errors.append(
                    f"maintenance module exceeds {limit} lines: "
                    f"{path.relative_to(SOURCE_ROOT)} ({line_count})"
                )
        command = package_root / "command.py"
        if command.is_file():
            tree = ast.parse(command.read_text(encoding="utf-8"))
            defined = {
                node.name
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            }
            if not defined <= {"main", "_parse_args"}:
                errors.append(f"maintenance command owns implementation: {package}")
            loaded = {
                node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
            }
            if loaded & forbidden_command_names:
                errors.append(f"maintenance command owns runtime behavior: {package}")

    for facade in thin_commands:
        path = SOURCE_ROOT / facade
        if path.is_file():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            if any(
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "sys"
                and node.attr == "modules"
                for node in ast.walk(tree)
            ):
                errors.append(f"maintenance CLI replaces sys.modules: {facade}")

    usage_root = (
        SOURCE_ROOT
        / "kernel/src/platform_kernel/agent_runtime/sdk_execution"
    )
    for name in (
        "usage_capture.py",
        "usage_context_views.py",
        "usage_provider.py",
        "usage_tools.py",
    ):
        if not (usage_root / name).is_file():
            errors.append(f"SDK usage-capture module is missing: {name}")
    capture = usage_root / "usage_capture.py"
    if capture.is_file() and len(capture.read_text().splitlines()) > 525:
        errors.append("SDK usage_capture facade exceeds 525 lines")

    retired_cleanup = SOURCE_ROOT / "scripts/maintenance/cleanup_runtime.py"
    if retired_cleanup.exists() or retired_cleanup.is_symlink():
        errors.append("retired cleanup_runtime facade has reappeared")


def validate_symlinks(errors: list[str]) -> None:
    skipped = {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
    }
    for directory, names, filenames in os.walk(SOURCE_ROOT, followlinks=False):
        current = Path(directory)
        names[:] = [name for name in names if name not in skipped]
        for name in [*names, *filenames]:
            path = current / name
            if not path.is_symlink():
                continue
            target = (path.parent / os.readlink(path)).resolve(strict=False)
            if not target.is_relative_to(SOURCE_ROOT):
                errors.append(
                    f"source symlink escapes source root: {path.relative_to(SOURCE_ROOT)}"
                )


def validate_active_references(errors: list[str]) -> None:
    skipped = {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
    }
    markdown_files: list[Path] = []
    for directory, names, filenames in os.walk(SOURCE_ROOT, followlinks=False):
        current = Path(directory)
        names[:] = [
            name
            for name in names
            if name not in skipped
        ]
        for filename in filenames:
            path = current / filename
            if path.suffix not in TEXT_SUFFIXES:
                continue
            relative = path.relative_to(SOURCE_ROOT)
            if relative not in LEGACY_REFERENCE_ALLOWLIST:
                content = path.read_text(encoding="utf-8", errors="replace")
                for label, pattern in LEGACY_REFERENCES.items():
                    match = pattern.search(content)
                    if match:
                        errors.append(f"{relative}: {label}:{match.group()}")
            if path.suffix == ".md":
                markdown_files.append(path)
    for path in markdown_files:
        relative = path.relative_to(SOURCE_ROOT)
        content = path.read_text(encoding="utf-8", errors="replace")
        for raw_target in MARKDOWN_LINK.findall(content):
            target = _markdown_target(raw_target)
            if target is not None and not (path.parent / target).resolve().exists():
                errors.append(f"{relative}: BROKEN_LOCAL_MARKDOWN_LINK:{target}")


def check(scope: str = "all") -> list[str]:
    errors: list[str] = []
    validate_layout_contract(errors)
    if scope in {"runtime", "all"}:
        validate_runtime(errors)
    validate_source_tree(errors)
    validate_retrieval_inputs(errors)
    validate_maintenance_module_boundaries(errors)
    validate_symlinks(errors)
    validate_active_references(errors)
    return sorted(errors)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=("source", "runtime", "all"), default="all")
    errors = check(parser.parse_args().scope)
    if errors:
        raise SystemExit("INTERNAL_LAYOUT_CHECK_FAILED\n- " + "\n- ".join(errors))
    print("INTERNAL_LAYOUT_CHECK_OK")


if __name__ == "__main__":
    main()
