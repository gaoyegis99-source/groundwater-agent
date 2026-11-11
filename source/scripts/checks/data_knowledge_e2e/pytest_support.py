"""Isolated pytest command and cleanup helpers for the E2E orchestrator."""

from __future__ import annotations

from pathlib import Path
import shutil
import sys


def _pytest_command(
    tests: tuple[str, ...],
    *,
    junit: Path,
    basetemp: Path,
    clear_repository_addopts: bool = False,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--basetemp",
        str(basetemp),
    ]
    if clear_repository_addopts:
        command.extend(("-o", "addopts="))
    return [*command, *tests, "--junitxml", str(junit)]


def _remove_pytest_basetemp(path: Path) -> None:
    """Fail explicitly if a test leaves a temp tree the runner cannot own."""

    if path.exists():
        shutil.rmtree(path)
