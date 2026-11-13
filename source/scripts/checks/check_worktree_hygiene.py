#!/usr/bin/env python3
"""Reject tracked local state and active dependencies outside the V6 boundary."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess


ROOT = Path(__file__).resolve().parents[2]
REQUIRED_IGNORE_RULES = {
    ".generated/",
    "**/.venv/",
    "**/.mypy_cache/",
    "node_modules/",
    "test-results/",
    "playwright-report/",
    "**/.v6-agent-runs.sqlite*",
}
FORBIDDEN_PATH_PARTS = {
    ".generated",
    ".p8-runtime",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "playwright-report",
    "test-results",
}
FORBIDDEN_RETRIEVAL_ROOTS = {"archive", "evidence", "golden", "release"}
IGNORED_DIRECTORIES = {
    ".git",
    ".generated",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "docs",
    "node_modules",
    "tests",
}
SCANNED_SUFFIXES = {".json", ".py", ".sh", ".toml", ".yaml", ".yml"}
SCANNED_NAMES = {"Dockerfile", "compose.yaml", "compose.yml"}
WORKSPACE_PATH = re.compile(r"/home/mtd/liweigis/workflow/([A-Za-z0-9][A-Za-z0-9_.-]*)")
HOST_DOCKER_SOCKET_SOURCE = re.compile(
    r"(?m)^\s*-\s*/var/run/docker\.sock:|"
    r"DOCKER_HOST\s*=\s*[\"']unix:///var/run/docker\.sock"
)
CROSS_PROJECT_IMPORT = re.compile(
    r"(?m)^\s*(?:from|import)\s+(?:groundwater_v7|groundwater_platform_v7)(?:\.|\s|$)"
)


def _tracked_paths(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [path for path in result.stdout.decode("utf-8").split("\0") if path]


def check_paths(paths: list[str]) -> list[str]:
    """Return deterministic violations for paths present in the Git index."""
    violations: list[str] = []
    for raw_path in paths:
        path = PurePosixPath(raw_path)
        if path.parts and path.parts[0] in FORBIDDEN_RETRIEVAL_ROOTS:
            violations.append(
                f"{raw_path}: TRACKED_RETRIEVAL_POLLUTION_ROOT:{path.parts[0]}"
            )
            continue
        if path.parts[:2] == ("docs", "verification"):
            violations.append(f"{raw_path}: TRACKED_HISTORICAL_VERIFICATION")
            continue
        if path.name.startswith(".v6-agent-runs.sqlite"):
            violations.append(f"{raw_path}: TRACKED_EPHEMERAL_JOURNAL")
            continue
        if path.name == ".env" or (
            path.name.startswith(".env.") and path.name != ".env.example"
        ):
            violations.append(f"{raw_path}: TRACKED_SECRET_CONFIGURATION")
            continue
        if path.parts[:5] == (
            "products",
            "groundwater",
            "development",
            "yaozhanbao-transfer-case",
            "payload",
        ):
            violations.append(f"{raw_path}: TRACKED_CASE_PAYLOAD")
            continue
        blocked = next(
            (part for part in path.parts if part in FORBIDDEN_PATH_PARTS), None
        )
        if blocked is not None:
            violations.append(f"{raw_path}: TRACKED_EPHEMERAL_PATH:{blocked}")
    return sorted(violations)


def _activity_plan_violations(root: Path) -> list[str]:
    plans = root.parent / "plans"
    if not plans.is_dir() or plans.is_symlink():
        return ["../plans: ACTIVITY_PLAN_ROOT_INVALID"]
    index = plans / "README.md"
    violations = [
        f"../plans/{child.name}: EXTRA_ACTIVITY_PLAN_CONTENT"
        for child in plans.iterdir()
        if child != index
    ]
    if not index.is_file() or index.is_symlink():
        violations.append("../plans/README.md: PLAN_ARCHIVE_INDEX_INVALID")
    return sorted(violations)


def _groundwater_version_violations(root: Path) -> list[str]:
    product_path = root / "products/groundwater/product-manifest.json"
    try:
        product = json.loads(product_path.read_text(encoding="utf-8"))
        selected = product["domainPackage"]
        if selected["domainId"] != "groundwater":
            raise ValueError("groundwater domain not selected")
        version = selected["version"]
    except (OSError, ValueError, KeyError, StopIteration, TypeError):
        return ["products/groundwater/product-manifest.json: DOMAIN_SELECTION_INVALID"]
    domain_root = root / "domains/groundwater"
    actual = sorted(path.name for path in domain_root.iterdir() if path.is_dir())
    violations = []
    if actual != [version]:
        violations.append(
            "domains/groundwater: SELECTED_VERSION_ONLY_REQUIRED:"
            + ",".join(actual)
        )
    legacy_path = re.compile(r"domains/groundwater/(?:5[.]0[.]0|6[.]0[.]0)")
    for base in (root, root.parent / "plans"):
        for path in base.rglob("*"):
            if not path.is_file() or ".git" in path.parts:
                continue
            if path.suffix not in {".json", ".md", ".py", ".sh", ".toml", ".yaml", ".yml"}:
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            if legacy_path.search(content):
                relative = path.relative_to(root.parent).as_posix()
                violations.append(f"../{relative}: RETIRED_DOMAIN_PATH_REFERENCE")
    return sorted(violations)


def _workspace_dependency_violations(root: Path) -> list[str]:
    violations: list[str] = []
    for directory, names, files in os.walk(root, followlinks=False):
        current = Path(directory)
        names[:] = [name for name in names if name not in IGNORED_DIRECTORIES]
        for name in files:
            path = current / name
            if path.suffix not in SCANNED_SUFFIXES and path.name not in SCANNED_NAMES:
                continue
            relative = path.relative_to(root)
            content = path.read_text(encoding="utf-8", errors="replace")
            if HOST_DOCKER_SOCKET_SOURCE.search(content):
                violations.append(f"{relative.as_posix()}: HOST_DOCKER_SOCKET_SOURCE")
            if path.suffix == ".py" and CROSS_PROJECT_IMPORT.search(content):
                violations.append(
                    f"{relative.as_posix()}: CROSS_PROJECT_IMPORT:groundwater-v7"
                )
            own_projects = {root.name, root.parent.name}
            for sibling in sorted(set(WORKSPACE_PATH.findall(content))):
                if sibling not in own_projects:
                    violations.append(
                        f"{relative.as_posix()}: WORKSPACE_SIBLING_DEPENDENCY:{sibling}"
                    )
    return sorted(violations)


def _layout_violations(root: Path) -> list[str]:
    """Validate project-level isolation declarations not visible below source/."""
    project_root = root.parent
    layout_path = project_root / "project-layout.json"
    if layout_path.is_symlink() or not layout_path.is_file():
        return ["../project-layout.json: PROJECT_LAYOUT_MISSING_OR_SYMLINK"]
    try:
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["../project-layout.json: PROJECT_LAYOUT_INVALID"]
    lifecycle = layout.get("lifecycle") if isinstance(layout, dict) else None
    docker = layout.get("docker") if isinstance(layout, dict) else None
    if (
        layout.get("schema_version") != 4
        or layout.get("project") != "groundwater-v6"
        or layout.get("source") != "source"
        or not isinstance(lifecycle, dict)
        or not isinstance(docker, dict)
        or "runtime" in layout
    ):
        return ["../project-layout.json: PROJECT_LAYOUT_INVALID"]
    expected_lifecycle = {
        "config": "xdg-config",
        "data": "xdg-data",
        "state": "xdg-state",
        "cache": "xdg-cache",
        "runtime": "xdg-runtime",
    }
    violations: list[str] = []
    if lifecycle != expected_lifecycle:
        violations.append("../project-layout.json: XDG_LIFECYCLE_INVALID")
    if docker.get("data_root") != "xdg-cache/docker":
        violations.append("../project-layout.json: DOCKER_DATA_ROOT_INVALID")
    if docker.get("socket") != "docker.sock":
        violations.append("../project-layout.json: DOCKER_SOCKET_INVALID")
    if docker.get("default_address_pool") != "10.206.0.0/16":
        violations.append("../project-layout.json: DOCKER_ADDRESS_POOL_INVALID")
    if docker.get("network_size") != 24:
        violations.append("../project-layout.json: DOCKER_NETWORK_SIZE_INVALID")
    if docker.get("authoritative_volumes") is not False:
        violations.append("../project-layout.json: AUTHORITATIVE_VOLUME_DECLARED")
    if layout.get("ports") != {"research": {"gateway": 28186}}:
        violations.append("../project-layout.json: RESEARCH_PORTS_INVALID")
    accounts = layout.get("accounts")
    if (
        not isinstance(accounts, dict)
        or set(accounts) != {"research"}
        or not isinstance(accounts.get("research"), str)
        or not accounts["research"]
    ):
        violations.append("../project-layout.json: RESEARCH_ACCOUNT_INVALID")
    for key in ("shared_runtime_paths", "shared_docker_resources"):
        if layout.get(key) != []:
            violations.append(f"../project-layout.json: SHARED_RESOURCE_DECLARED:{key}")
    return sorted(violations)


def _symlink_violations(root: Path) -> list[str]:
    """Reject links which could silently make the source depend on another tree."""
    violations: list[str] = []
    for directory, names, files in os.walk(root, followlinks=False):
        current = Path(directory)
        names[:] = [name for name in names if name not in IGNORED_DIRECTORIES]
        for name in [*names, *files]:
            path = current / name
            if not path.is_symlink():
                continue
            target = (path.parent / os.readlink(path)).resolve(strict=False)
            if not target.is_relative_to(root):
                relative = path.relative_to(root).as_posix()
                violations.append(f"{relative}: SYMLINK_OUTSIDE_SOURCE:{target}")
    return violations


def _project_bin_violations(root: Path) -> list[str]:
    """Apply the same boundary to project-level stable maintenance entrypoints."""
    bin_root = root.parent / "bin"
    if bin_root.is_symlink() or not bin_root.is_dir():
        return ["../bin: PROJECT_BIN_MISSING_OR_SYMLINK"]
    violations: list[str] = []
    own_projects = {root.name, root.parent.name}
    for directory, names, files in os.walk(bin_root, followlinks=False):
        current = Path(directory)
        for name in names:
            path = current / name
            if path.is_symlink():
                violations.append(
                    f"../bin/{path.relative_to(bin_root).as_posix()}: PROJECT_BIN_SYMLINK"
                )
        names[:] = [name for name in names if not (current / name).is_symlink()]
        for name in files:
            path = current / name
            relative = f"../bin/{path.relative_to(bin_root).as_posix()}"
            if path.is_symlink() or not path.is_file():
                violations.append(f"{relative}: PROJECT_BIN_SYMLINK_OR_TYPE")
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            if HOST_DOCKER_SOCKET_SOURCE.search(content):
                violations.append(f"{relative}: HOST_DOCKER_SOCKET_SOURCE")
            if CROSS_PROJECT_IMPORT.search(content):
                violations.append(f"{relative}: CROSS_PROJECT_IMPORT:groundwater-v7")
            for sibling in sorted(set(WORKSPACE_PATH.findall(content))):
                if sibling not in own_projects:
                    violations.append(
                        f"{relative}: WORKSPACE_SIBLING_DEPENDENCY:{sibling}"
                    )
    return violations


def check(root: Path = ROOT, tracked_paths: list[str] | None = None) -> list[str]:
    """Validate ignores, tracked content and executable workspace boundaries."""
    rules = set((root / ".gitignore").read_text(encoding="utf-8").splitlines())
    violations = [
        f".gitignore: REQUIRED_IGNORE_RULE_MISSING:{rule}"
        for rule in sorted(REQUIRED_IGNORE_RULES - rules)
    ]
    paths = _tracked_paths(root) if tracked_paths is None else tracked_paths
    return sorted(
        [
            *violations,
            *check_paths(paths),
            *_workspace_dependency_violations(root),
            *_symlink_violations(root),
            *_project_bin_violations(root),
            *_layout_violations(root),
            *_activity_plan_violations(root),
            *_groundwater_version_violations(root),
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    violations = check(args.root)
    if violations:
        print("WORKTREE_HYGIENE_CHECK_FAILED")
        print("\n".join(violations))
        raise SystemExit(1)
    print("WORKTREE_HYGIENE_CHECK_OK")


if __name__ == "__main__":
    main()
