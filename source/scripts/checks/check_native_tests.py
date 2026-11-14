"""Run Native operator and worker-wheel tests in their locked dependency domain."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from scripts.maintenance.dependency_materializer import (
    DependencyRefusal,
    root as dependency_root,
    verify_scope,
)
from scripts.maintenance.xdg_layout import resolve_lifecycle_paths


ROOT = Path(__file__).resolve().parents[2]


def _locked_python_version(root: Path = ROOT) -> str:
    try:
        lock = json.loads((root / "toolchain.lock.json").read_text(encoding="utf-8"))
        version = lock["python"]["version"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("TOOLCHAIN_LOCK_INVALID") from error
    if not isinstance(version, str) or not version.startswith("3.12."):
        raise RuntimeError("NATIVE_PYTHON_LOCK_INVALID")
    return version


def _venv_command(uv: str, destination: Path, version: str) -> list[str]:
    return [
        uv,
        "venv",
        "--python",
        version,
        "--offline",
        "--no-python-downloads",
        str(destination),
    ]


def _pytest_command(python: str, temporary: Path) -> list[str]:
    """Keep Toil/worker temporary trees inside this run's owned directory."""

    return [
        python,
        "-m",
        "pytest",
        "-q",
        "-rs",
        "--basetemp",
        str(temporary / "pytest"),
        "-o",
        "pythonpath=",
        "packages/operator-pack/tests",
        "services/executor/native_tests",
    ]


def _run(command: list[str]) -> None:
    environment = {
        **os.environ,
        "PYTHONPATH": "",
        "UV_CACHE_DIR": str(resolve_lifecycle_paths().cache / "uv"),
    }
    subprocess.run(command, cwd=ROOT, env=environment, check=True)


def main() -> int:
    uv = shutil.which("uv")
    if uv is None:
        print("NOT_RUN: UV_MISSING", file=sys.stderr)
        return 2
    paths = resolve_lifecycle_paths()
    try:
        verify_scope(ROOT, paths, "python")
    except DependencyRefusal as error:
        print(f"NOT_RUN: {error}", file=sys.stderr)
        return 2
    wheelhouses = dependency_root(ROOT, paths) / "python"
    for name in ("build", "native", "testing"):
        if not (wheelhouses / name).is_dir():
            print(f"NOT_RUN: {name.upper()}_WHEELHOUSE_MISSING", file=sys.stderr)
            return 2
    paths.build.mkdir(parents=True, exist_ok=True)
    expected_python = _locked_python_version()
    with tempfile.TemporaryDirectory(
        prefix="native-tests-", dir=paths.build
    ) as temporary:
        environment = Path(temporary) / "venv"
        python = str(environment / "bin/python")
        _run(_venv_command(uv, environment, expected_python))
        actual_python = subprocess.check_output(
            [python, "-c", "import platform; print(platform.python_version())"],
            cwd=ROOT,
            text=True,
        ).strip()
        if actual_python != expected_python:
            print(
                f"NOT_RUN: NATIVE_PYTHON_VERSION_MISMATCH:"
                f"{actual_python}!={expected_python}",
                file=sys.stderr,
            )
            return 2
        for name in ("build", "native", "testing"):
            _run(
                [
                    uv,
                    "pip",
                    "install",
                    "--python",
                    python,
                    "--require-hashes",
                    "--offline",
                    "--no-index",
                    "--find-links",
                    str(wheelhouses / name),
                    "--strict",
                    "-r",
                    str(ROOT / "environments" / name / "requirements.lock"),
                ]
            )
        _run(
            [
                uv,
                "pip",
                "install",
                "--python",
                python,
                "--offline",
                "--no-index",
                "--no-deps",
                "--no-build-isolation",
                "--strict",
                *(
                    str(ROOT / path)
                    for path in (
                        "packages/protocol-contracts",
                        "packages/workflow-compiler",
                        "services/data/client",
                        "services/executor",
                        "packages/operator-pack",
                    )
                ),
            ]
        )
        _run([uv, "pip", "check", "--python", python, "--offline"])
        _run(_pytest_command(python, Path(temporary)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
