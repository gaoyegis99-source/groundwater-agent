"""Run existing development checks by affected component."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys

# Direct Python invocations must use the same project Docker boundary as bin/gw.
import sys as _maintenance_sys  # noqa: E402
from dataclasses import dataclass
from pathlib import Path
from pathlib import Path as _MaintenancePath  # noqa: E402

_maintenance_source = next(
    parent
    for parent in _MaintenancePath(__file__).resolve().parents
    if (parent / "scripts/maintenance/project_environment.py").is_file()
)
_maintenance_sys.path.insert(0, str(_maintenance_source))
from scripts.maintenance.project_environment import docker_executable  # noqa: E402
from scripts.checks.v6_test_suite import (  # noqa: E402
    V6_PYTEST_SLICES,
    V6_UI_WORKSPACES,
    pytest_command,
)

ROOT = Path(__file__).resolve().parents[2]
SCOPES = (
    "kernel",
    "data",
    "knowledge",
    "executor",
    "assembler",
    "contracts",
    "web",
    "integration",
    "all",
)


@dataclass(frozen=True)
class Check:
    name: str
    command: tuple[str, ...]
    requires_knowledge_database: bool = False


PYTHON = sys.executable
_CHECKS = {
    "kernel": (
        Check("Kernel tests", (PYTHON, "-m", "pytest", "-q", "kernel/tests")),
        Check(
            "Kernel types",
            (
                PYTHON,
                "-m",
                "mypy",
                "--follow-imports=silent",
                "kernel/src/platform_kernel/application/ports.py",
                "kernel/src/platform_kernel/application/agent_projection.py",
                "kernel/src/platform_kernel/interaction/owner.py",
                "kernel/src/platform_kernel/agent_runtime/run_dependencies.py",
                "kernel/src/platform_kernel/agent_runtime/run_start.py",
                "kernel/src/platform_kernel/agent_runtime/run_resume.py",
                "kernel/src/platform_kernel/agent_runtime/run_finalization.py",
                "kernel/src/platform_kernel/agent_runtime/owner.py",
                "tests/architecture/kernel_port_types.py",
            ),
        ),
    ),
    "data": (
        Check("Data tests", (PYTHON, "-m", "pytest", "-q", "services/data/tests")),
    ),
    "knowledge": (
        Check(
            "Knowledge tests",
            (PYTHON, "-m", "pytest", "-q", "services/knowledge-next/tests"),
            requires_knowledge_database=True,
        ),
    ),
    "executor": (
        Check(
            "Executor tests", (PYTHON, "-m", "pytest", "-q", "services/executor/tests")
        ),
        Check(
            "Native operator and worker-wheel tests",
            (PYTHON, "-m", "scripts.checks.check_native_tests"),
        ),
    ),
    "assembler": (
        Check("Compile assembly", (PYTHON, "scripts/development/dev.py", "compile")),
        Check("Assembler tests", (PYTHON, "-m", "pytest", "-q", "tests/assembler")),
    ),
    "contracts": (
        Check(
            "Runtime OpenAPI",
            (PYTHON, "-m", "scripts.development.check_runtime_openapi"),
        ),
        Check(
            "Public and TypeScript contracts",
            ("npm", "--prefix", "web", "run", "contracts:check"),
        ),
        Check(
            "Python SDK contracts",
            (
                PYTHON,
                "-m",
                "scripts.development.generate_python_sdk_contracts",
                "--check",
            ),
        ),
        Check("Contract tests", (PYTHON, "-m", "pytest", "-q", "tests/contracts")),
    ),
    "web": (
        Check("Web tests", ("npm", "--prefix", "web", "test")),
        Check("Web types", ("npm", "--prefix", "web", "run", "typecheck")),
        Check("Web build", ("npm", "--prefix", "web", "run", "build")),
    ),
    "integration": (
        Check(
            "Production-build browser path",
            ("npm", "--prefix", "web", "run", "test:e2e:build"),
        ),
        Check(
            "Deterministic Compose",
            (PYTHON, "scripts/checks/check_compose_e2e.py", "--product", "groundwater"),
        ),
    ),
}


def _preflight(
    scope: str, *, knowledge_test_database_url: str | None = None
) -> list[str]:
    missing: list[str] = []
    if shutil.which("ruff") is None:
        missing.append("ruff")
    needs_docker = scope in {"data", "integration", "all"} and (
        scope != "data" or not os.getenv("DATA_TEST_DATABASE_URL")
    )
    if needs_docker:
        if subprocess.run(
            [docker_executable(), "info", "--format", "{{.ServerVersion}}"],
            cwd=ROOT,
            capture_output=True,
            check=False,
        ).returncode != 0:
            missing.append("Docker Engine")
    if scope in {"knowledge", "all"}:
        url = knowledge_test_database_url or os.getenv("TEST_KNOWLEDGE_DATABASE_URL", "")
        if not url or not url.rsplit("/", 1)[-1].endswith("_test"):
            missing.append("TEST_KNOWLEDGE_DATABASE_URL (isolated *_test database)")
    if scope in {"kernel", "all"} and importlib.util.find_spec("mypy") is None:
        missing.append("mypy; install scripts/checks/requirements-dev.lock")
    if (
        scope in {"contracts", "web", "integration", "all"}
        and shutil.which("npm") is None
    ):
        missing.append("npm")
    workspace_dependencies = (ROOT / "node_modules").is_dir()
    if (
        scope in {"contracts", "web", "integration", "all"}
        and not workspace_dependencies
        and not (ROOT / "web/node_modules").is_dir()
    ):
        missing.append("npm workspace node_modules; run npm ci")
    if scope == "all":
        for name in ("services/data/web", "services/knowledge-next/ui"):
            if not workspace_dependencies and not (ROOT / name / "node_modules").is_dir():
                missing.append(f"{name}/node_modules; run npm ci")
    return missing


def _steps(scope: str) -> tuple[Check, ...]:
    if scope == "all":
        steps = [
            Check(
                "Compile assembly", (PYTHON, "scripts/development/dev.py", "compile")
            ),
            *(
                Check(
                    test_slice.title,
                    (PYTHON, *pytest_command(test_slice, report_skips=True)),
                    requires_knowledge_database=(
                        test_slice.requires_knowledge_database
                    ),
                )
                for test_slice in V6_PYTEST_SLICES
            ),
            _CHECKS["executor"][1],
            _CHECKS["kernel"][1],
            *_CHECKS["contracts"][:3],
            *(
                Check(f"{name} {action}", ("npm", "--prefix", name, "run", action))
                for name in V6_UI_WORKSPACES
                for action in ("test", "typecheck", "build")
            ),
            *_CHECKS["integration"],
        ]
    else:
        steps = list(_CHECKS[scope])
    steps.extend(
        (
            Check("Ruff", ("ruff", "check", ".")),
            Check("Diff whitespace", ("git", "diff", "--check")),
        )
    )
    if scope in {"kernel", "data", "knowledge", "executor", "assembler", "all"}:
        steps.extend(
            (
                Check(
                    "Core imports", (PYTHON, "-m", "scripts.checks.check_core_imports")
                ),
                Check(
                    "Architecture boundaries",
                    ("bash", "scripts/checks/check-boundaries.sh"),
                ),
            )
        )
    if scope == "all":
        steps.append(
            Check("Documentation", (PYTHON, "-m", "scripts.checks.check_documentation"))
        )
    return tuple(steps)


def run_checks(scope: str, *, knowledge_test_database_url: str | None = None) -> None:
    if scope not in SCOPES:
        raise ValueError(f"CHECK_SCOPE_INVALID:{scope}")
    missing = (
        _preflight(scope, knowledge_test_database_url=knowledge_test_database_url)
        if knowledge_test_database_url is not None
        else _preflight(scope)
    )
    if missing:
        raise SystemExit("NOT_RUN: missing prerequisites: " + ", ".join(missing))
    for step in _steps(scope):
        print("CHECK " + step.name, flush=True)
        environment = None
        if knowledge_test_database_url and step.requires_knowledge_database:
            environment = {
                **os.environ,
                "TEST_KNOWLEDGE_DATABASE_URL": knowledge_test_database_url,
            }
        subprocess.run(step.command, cwd=ROOT, check=True, env=environment)
