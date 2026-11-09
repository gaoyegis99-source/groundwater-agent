from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
COMMANDS = {
    "scripts/development/dev.py": "--product",
    "scripts/release/export_source_snapshot.py": "--manifest",
}


@pytest.mark.parametrize("command,required_option", COMMANDS.items())
def test_canonical_command_exposes_help(command: str, required_option: str) -> None:
    result = subprocess.run(
        [sys.executable, command, "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert required_option in result.stdout


def test_current_compose_command_exposes_help() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/checks/check_compose_e2e.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "--embedding-base-url" in result.stdout
    assert "--embedding-report" in result.stdout
    assert "--reply-quality-only" in result.stdout
    assert "--reply-quality-key-env" in result.stdout
    assert "--network-subnet" in result.stdout
    assert "--webui-only" in result.stdout


def test_data_knowledge_e2e_command_exposes_isolation_options() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/checks/check_data_knowledge_e2e.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "--run-id" in result.stdout
    assert "--network-subnet" in result.stdout
    assert "--output" in result.stdout
