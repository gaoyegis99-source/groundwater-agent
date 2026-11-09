from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.release.check_wheel_contents import check_wheel


IMPORT_CHECK = """
from importlib import import_module
from importlib.util import find_spec

current = (
    "platform_workflow_compiler",
    "assembler",
    "platform_executor_service",
    "platform_kernel",
    "platform_kernel.agent_runtime",
    "platform_kernel.interaction",
    "platform_kernel.workflow_execution",
    "platform_embedding_client",
    "platform_data_service",
    "platform_data_client",
    "platform_knowledge_next",
    "platform_knowledge_client",
    "platform_protocol_contracts",
    "platform_executor_client",
    "groundwater_api_client",
    "moperators",
)
legacy = (
    "platform_auth_contract",
    "platform_auth_service",
    "platform_core",
    "platform_core_services",
    "platform_runtime",
    "platform_runtime_sdk",
    "platform_worker",
    "platform_worker_host",
    "platform_kernel.asset",
    "platform_kernel.knowledge",
    "platform_kernel.results",
    "platform_kernel.map",
    "platform_kernel.execution_control",
    "platform_asset_service",
    "platform_result_service",
    "platform_resource_service",
    "platform_knowledge_service",
)

for name in current:
    import_module(name)
for name in legacy:
    try:
        spec = find_spec(name)
    except ModuleNotFoundError:
        spec = None
    if spec is not None:
        raise SystemExit(f"legacy import present: {name}")
print("Release wheel imports PASS")
"""


def _release_python() -> str:
    """Return the required clean-environment interpreter for wheel checks.

    The release contract is Python 3.12.  Using the interpreter that happened
    to launch this checker made a developer's Python 3.13 environment fail
    valid wheels whose metadata intentionally excludes 3.13.  Prefer an
    explicit override for CI, otherwise discover ``python3.12`` and fail
    clearly when the required interpreter is unavailable.
    """

    candidates = []
    configured = os.environ.get("RELEASE_PYTHON")
    if configured:
        candidates.append(configured)
    candidates.extend(["python3.12", "python3.12m"])
    for candidate in candidates:
        executable = shutil.which(candidate) or candidate
        try:
            result = subprocess.run(
                [executable, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
                capture_output=True,
                text=True,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError):
            continue
        if result.stdout.strip() == "3.12":
            return executable
    raise RuntimeError("RELEASE_PYTHON_3_12_REQUIRED")


def _create_venv(path: Path, python_executable: str) -> None:
    standard = subprocess.run(
        [python_executable, "-m", "venv", str(path)],
        capture_output=True,
        text=True,
    )
    if standard.returncode == 0:
        return
    uv = shutil.which("uv")
    if uv is None:
        raise subprocess.CalledProcessError(
            standard.returncode,
            standard.args,
            output=standard.stdout,
            stderr=standard.stderr,
        )
    subprocess.run(
        [
            uv,
            "venv",
            "--clear",
            "--seed",
            "--python",
            python_executable,
            str(path),
        ],
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    arguments = {
        "workflow-compiler": "platform-workflow-compiler",
        "assembler": "platform-assembler",
        "executor-service": "platform-executor-service",
        "kernel-services": "platform-kernel-services",
        "embedding-client": "platform-embedding-client",
        "operator-pack": "platform-operator-pack",
        "data-service": "platform-data-service",
        "data-client": "platform-data-client",
        "knowledge-next": "platform-knowledge-next",
        "knowledge-client": "platform-knowledge-client",
        "protocol-contracts": "platform-protocol-contracts",
        "executor-client": "platform-executor-client",
        "groundwater-api-client": "groundwater-api-client",
    }
    for option in arguments:
        parser.add_argument(f"--{option}", type=Path, required=True)
    args = parser.parse_args()
    wheels = {
        distribution: getattr(args, option.replace("-", "_")).resolve()
        for option, distribution in arguments.items()
    }
    problems = [
        f"{distribution}: {problem}"
        for distribution, wheel in wheels.items()
        for problem in check_wheel(distribution, wheel)
    ]
    if problems:
        print("\n".join(problems))
        raise SystemExit(1)

    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    release_python = _release_python()
    with tempfile.TemporaryDirectory(prefix="platform-release-wheels-") as directory:
        root = Path(directory)
        venv = root / "venv"
        _create_venv(venv, release_python)
        python = venv / "bin" / "python"
        subprocess.run(
            [python, "-m", "pip", "install", *(str(item) for item in wheels.values())],
            cwd=root,
            env=environment,
            check=True,
        )
        subprocess.run(
            [python, "-m", "pip", "check"], cwd=root, env=environment, check=True
        )
        subprocess.run(
            [python, "-c", IMPORT_CHECK], cwd=root, env=environment, check=True
        )
    print("Release wheels PASS")


if __name__ == "__main__":
    main()
