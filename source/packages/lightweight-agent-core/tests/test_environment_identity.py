from __future__ import annotations

from pathlib import Path

import platform_lightweight_agent.environment as environment_module
import pytest
from platform_lightweight_agent import (
    FileWorkspace,
    LocalRecoveryError,
    RunSnapshot,
    RuntimeEnvironmentIdentity,
    RuntimePackageIdentity,
    SQLiteRunJournal,
    capture_runtime_environment,
    check_local_recovery_environment,
    compare_runtime_environment,
    create_local_recovery_package,
    restore_local_recovery_package,
)


def _journal(path: Path) -> SQLiteRunJournal:
    journal = SQLiteRunJournal(path)
    journal.create(
        RunSnapshot(
            run_id="environment-run",
            workflow={
                "schema_version": "v6-light-workflow-v1",
                "workflow_id": "environment",
                "version": "1",
                "steps": [],
            },
            inputs={},
            state={"values": {}},
            status="SUCCEEDED",
            driver_bindings={},
        ),
        "RUN_STARTED",
        {},
    )
    return journal


def test_environment_identity_round_trip_and_lock_warning(tmp_path: Path) -> None:
    lock = tmp_path / "pylock.toml"
    lock.write_text("lock-version = '1.0'\n", encoding="utf-8")
    expected = capture_runtime_environment(
        _journal(tmp_path / "runs.sqlite"), dependency_lock=lock
    )
    assert RuntimeEnvironmentIdentity.from_mapping(expected.to_mapping()) == expected
    assert compare_runtime_environment(expected, dependency_lock=lock).status == (
        "compatible"
    )

    lock.write_text("lock-version = '1.1'\n", encoding="utf-8")
    changed = compare_runtime_environment(expected, dependency_lock=lock)
    assert changed.status == "warning"
    assert [item.code for item in changed.diagnostics] == [
        "RUNTIME_DEPENDENCY_LOCK_CHANGED"
    ]


def test_environment_identity_distinguishes_required_and_warning_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = capture_runtime_environment(
        SQLiteRunJournal(tmp_path / "environment.sqlite")
    )
    expected = RuntimeEnvironmentIdentity(
        baseline.core_version,
        baseline.python_implementation,
        baseline.python_version,
        baseline.python_cache_tag,
        baseline.platform_system,
        baseline.platform_machine,
        None,
        (
            RuntimePackageIdentity("required-driver", "driver", "1", "1", "required"),
            RuntimePackageIdentity("provider-sdk", "provider", "1", "1", "warning"),
        ),
    )

    monkeypatch.setattr(
        environment_module,
        "_installed_version",
        lambda distribution: "2" if distribution in {"required-driver", "provider-sdk"} else baseline.core_version,
    )
    report = compare_runtime_environment(expected)

    assert report.status == "incompatible"
    assert [(item.code, item.severity) for item in report.diagnostics] == [
        ("RUNTIME_REQUIRED_PACKAGE_MISMATCH", "error"),
        ("RUNTIME_PROVIDER_VERSION_CHANGED", "warning"),
    ]


def test_restore_rejects_critical_environment_change_before_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = _journal(tmp_path / "runs.sqlite")
    workspace = FileWorkspace.under(tmp_path / "files")
    package = create_local_recovery_package(
        journal, workspace, tmp_path / "backup.zip"
    )
    original = environment_module._installed_version

    def changed(distribution: str) -> str | None:
        if distribution == "platform-lightweight-agent-core":
            return "999.0"
        return original(distribution)

    monkeypatch.setattr(environment_module, "_installed_version", changed)
    report = check_local_recovery_environment(package)
    assert report.status == "incompatible"
    with pytest.raises(LocalRecoveryError, match="ENVIRONMENT_INCOMPATIBLE"):
        restore_local_recovery_package(package, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
