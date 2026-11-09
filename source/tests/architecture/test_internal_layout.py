from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
import subprocess
import sys


SOURCE_ROOT = Path(__file__).resolve().parents[2]
CHECK = SOURCE_ROOT / "scripts" / "maintenance" / "check_internal_layout.py"
SPEC = importlib.util.spec_from_file_location("check_internal_layout", CHECK)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_internal_layout_contract_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECK)],
        cwd=SOURCE_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "INTERNAL_LAYOUT_CHECK_OK" in result.stdout


def test_runtime_validation_rejects_legacy_repository_lifecycle(tmp_path: Path) -> None:
    (tmp_path / "runtime").mkdir()
    errors: list[str] = []
    original_project_root = MODULE.PROJECT_ROOT
    try:
        MODULE.PROJECT_ROOT = tmp_path
        MODULE.validate_runtime(errors)
    finally:
        MODULE.PROJECT_ROOT = original_project_root
    assert errors == ["legacy repository runtime directory remains: runtime"]


def test_retrieval_validation_rejects_historical_roots(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "evidence").mkdir(parents=True)
    (tmp_path / "operations/maintenance").mkdir(parents=True)
    errors: list[str] = []
    original_source_root = MODULE.SOURCE_ROOT
    original_project_root = MODULE.PROJECT_ROOT
    try:
        MODULE.SOURCE_ROOT = source
        MODULE.PROJECT_ROOT = tmp_path
        MODULE.validate_retrieval_inputs(errors)
    finally:
        MODULE.SOURCE_ROOT = original_source_root
        MODULE.PROJECT_ROOT = original_project_root
    assert errors == [
        "retrieval pollution path remains: evidence",
        "retrieval pollution path remains: ../operations/maintenance",
    ]


def test_large_maintenance_commands_are_cli_only_facades() -> None:
    facades = (
        "scripts.evaluation.run_kernel_sandbox_pressure",
        "scripts.checks.check_compose_e2e",
        "scripts.checks.check_data_knowledge_e2e",
    )
    for public_name in facades:
        public = importlib.import_module(public_name)
        assert callable(public.main)
        facade_path = SOURCE_ROOT.joinpath(*public_name.split(".")).with_suffix(".py")
        source = facade_path.read_text(encoding="utf-8")
        assert len(source.splitlines()) <= 30
        assert "sys.modules" not in source


def test_usage_capture_keeps_public_imports_after_internal_split() -> None:
    from platform_kernel.agent_runtime.sdk_execution import usage_capture
    from platform_kernel.agent_runtime.sdk_execution.usage_provider import (
        _ProtocolRepairRequiredError,
    )

    assert usage_capture._ProtocolRepairRequiredError is _ProtocolRepairRequiredError
    assert usage_capture._RunUsageCapture.__module__ == usage_capture.__name__


def test_retired_cleanup_runtime_facade_is_absent() -> None:
    assert not (SOURCE_ROOT / "scripts/maintenance/cleanup_runtime.py").exists()
