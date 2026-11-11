from __future__ import annotations

import subprocess

import pytest

from scripts.checks import check_change
from scripts.checks.v6_test_suite import (
    V6_PYTEST_SLICES,
    V6_UI_WORKSPACES,
    pytest_command,
)
from scripts.development import dev


def test_check_scope_uses_existing_commands_and_propagates_failure(monkeypatch):
    commands = []

    def run(command, **kwargs):
        commands.append(tuple(command))
        if command[:3] == (check_change.PYTHON, "-m", "pytest"):
            raise subprocess.CalledProcessError(3, command)

    monkeypatch.setattr(check_change, "_preflight", lambda scope: [])
    monkeypatch.setattr(check_change.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError) as error:
        check_change.run_checks("executor")
    assert error.value.returncode == 3
    assert commands == [
        (check_change.PYTHON, "-m", "pytest", "-q", "services/executor/tests")
    ]


def test_missing_prerequisite_skips_every_command(monkeypatch):
    monkeypatch.setattr(
        check_change, "_preflight", lambda scope: ["isolated test database"]
    )
    monkeypatch.setattr(
        check_change.subprocess, "run", lambda *args, **kwargs: pytest.fail("ran check")
    )
    with pytest.raises(SystemExit, match="NOT_RUN.*isolated test database"):
        check_change.run_checks("knowledge")


def test_native_tests_have_their_own_executor_and_full_gate():
    native = (check_change.PYTHON, "-m", "scripts.checks.check_native_tests")
    assert native in [step.command for step in check_change._steps("executor")]
    assert native in [step.command for step in check_change._steps("all")]


def test_full_scope_runs_each_v6_python_slice_once():
    commands = [step.command for step in check_change._steps("all")]
    for test_slice in V6_PYTEST_SLICES:
        expected = (check_change.PYTHON, *pytest_command(test_slice, report_skips=True))
        assert commands.count(expected) == 1


def test_full_scope_tests_and_builds_every_v6_ui():
    commands = [step.command for step in check_change._steps("all")]
    for workspace in V6_UI_WORKSPACES:
        for action in ("test", "typecheck", "build"):
            assert ("npm", "--prefix", workspace, "run", action) in commands


def test_dev_check_dispatches_selected_scope(monkeypatch):
    selected = []
    monkeypatch.setattr(check_change, "run_checks", selected.append)
    monkeypatch.setattr(
        "sys.argv", ["scripts/development/dev.py", "check", "--scope", "kernel"]
    )
    dev.main()
    assert selected == ["kernel"]
