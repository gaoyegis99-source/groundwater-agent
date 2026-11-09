from __future__ import annotations

from pathlib import Path
import shutil

from assembler_runtime import (
    _executor_services,
    _worker_runtime_config,
)
from executor_profile import ExecutorOperationalProfile


def test_executor_config_and_worker_commands_share_one_capacity_profile(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    root = Path(__file__).resolve().parents[2]
    shutil.copy2(root / "system-deps.lock.json", source / "system-deps.lock.json")
    profile = ExecutorOperationalProfile(
        native_slots=3,
        native_memory_bytes=512 * 1024**2,
        qgis_slots=1,
        qgis_memory_bytes=512 * 1024**2,
    )
    configuration = _worker_runtime_config({"native"}, profile)
    services = _executor_services(
        source, Path("/output"), {}, "demo", {"native"}, profile,
    )
    assert configuration == {"native": {"slots": 3, "memoryBytes": 512 * 1024**2}}
    command = services["executor-native-worker"]["command"]
    assert command[command.index("--slots") + 1] == "3"
    assert command[command.index("--memory-bytes") + 1] == str(512 * 1024**2)
    assert services["executor-native-worker"]["mem_limit"] == 512 * 1024**2
    assert "${EXECUTOR_" not in str(services)
    assert "executor-qgis-worker" not in services


def test_executor_profile_parses_the_same_values_from_environment_and_metadata():
    environment = {
        "EXECUTOR_NATIVE_SLOTS": "3",
        "EXECUTOR_NATIVE_MEMORY_BYTES": str(512 * 1024**2),
        "EXECUTOR_QGIS_SLOTS": "2",
        "EXECUTOR_QGIS_MEMORY_BYTES": str(768 * 1024**2),
    }
    from_environment = ExecutorOperationalProfile.from_environment(environment)
    from_metadata = ExecutorOperationalProfile.from_environment(
        from_environment.environment_values()
    )

    assert from_metadata == from_environment
