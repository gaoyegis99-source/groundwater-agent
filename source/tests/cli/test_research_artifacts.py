from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tarfile

import pytest

from scripts.maintenance.contracts import canonical_bytes
from scripts.maintenance.research_artifacts import (
    ResearchArtifactError,
    apply_restore,
    create_archive,
    create_snapshot,
    restore_plan,
    verify,
)
from scripts.maintenance.xdg_layout import ensure_private_roots, resolve_lifecycle_paths


ROOT = Path(__file__).resolve().parents[2]


def _instance(tmp_path: Path):
    paths = resolve_lifecycle_paths(
        {
            "HOME": str(tmp_path / "home"),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_DATA_HOME": str(tmp_path / "data"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "XDG_RUNTIME_DIR": str(tmp_path / "runtime"),
        }
    )
    ensure_private_roots(paths, ("config", "data", "state", "cache", "runtime"))
    instance_id = "research-test"
    data = paths.instance_data(instance_id)
    state = paths.instance_state(instance_id)
    data.mkdir(parents=True)
    state.mkdir(parents=True)
    (data / "objects").mkdir()
    (data / "objects/input.bin").write_bytes(b"scientific-input")
    (data / "kernel").mkdir()
    with sqlite3.connect(data / "kernel/kernel.sqlite3") as connection:
        connection.execute("CREATE TABLE schema_version(version INTEGER NOT NULL)")
        connection.execute("INSERT INTO schema_version VALUES (34)")
        connection.execute(
            """CREATE TABLE workflow_runs(
               run_id TEXT PRIMARY KEY,
               status TEXT NOT NULL,
               arguments_json TEXT NOT NULL,
               input_references_json TEXT NOT NULL,
               outputs_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO workflow_runs VALUES (?,?,?,?,?)",
            (
                "failed-experiment",
                "FAILED",
                '{"randomSeed":42}',
                '{"input":"sha256:' + "1" * 64 + '"}',
                '{"output":"sha256:' + "2" * 64 + '"}',
            ),
        )
    manifest = {
        "schemaVersion": "instance-manifest.v4",
        "instanceId": instance_id,
        "profile": "research",
        "composeProject": "groundwater-v6-research-test",
        "dataRoot": str(data),
        "stateRoot": str(state),
        "ports": {"gateway": 28186},
        "assemblyDigest": "sha256:" + "a" * 64,
        "createdAt": "2026-11-03T00:00:00+00:00",
        "status": "stopped",
    }
    (state / "instance-manifest.v4.json").write_bytes(canonical_bytes(manifest))
    return paths, instance_id, data, state


def test_snapshot_verifies_and_restore_is_plan_apply(tmp_path: Path) -> None:
    paths, instance_id, data, _state = _instance(tmp_path)
    result = create_snapshot(paths, instance_id)
    snapshot = Path(result["snapshot"])
    assert verify(snapshot, "research-snapshot.v1")["status"] == "PASS"
    with tarfile.open(snapshot, "r:gz") as packed:
        manifest = json.loads(packed.extractfile("manifest.json").read())
    assert manifest["schemaVersions"] == {
        "publicApi": "v12",
        "kernel": 34,
        "data": 8,
        "knowledge": 7,
        "manifest": 4,
    }

    (data / "objects/input.bin").write_bytes(b"changed")
    plan = restore_plan(paths, snapshot, instance_id)
    with pytest.raises(ResearchArtifactError, match="RESTORE_CONFIRMATION_MISMATCH"):
        apply_restore(paths, plan, "sha256:" + "0" * 64)
    applied = apply_restore(paths, plan, plan["confirmationDigest"])

    assert applied["status"] == "PASS"
    assert (data / "objects/input.bin").read_bytes() == b"scientific-input"
    assert Path(applied["recoverablePreviousData"]).is_dir()


def test_experiment_archive_has_no_runtime_secret_and_benchmark_needs_gates(
    tmp_path: Path,
) -> None:
    paths, instance_id, _data, state = _instance(tmp_path)
    (state / "instance.env").write_text("SECRET=must-not-archive\n")

    result = create_archive(
        paths, ROOT, instance_id, "failed-experiment", "experiment", None
    )
    archive = Path(result["archive"])
    assert verify(archive, "research-archive.v1")["status"] == "PASS"
    with tarfile.open(archive, "r:gz") as packed:
        names = set(packed.getnames())
        assert "instance.env" not in names
        assert "source.tar" in names
        assert "dirty.patch" in names
        assert "environment-summary.json" in names
        assert "run-evidence.json" in names

    with pytest.raises(ResearchArtifactError, match="BENCHMARK_ASSESSMENT_REQUIRED"):
        create_archive(paths, ROOT, instance_id, "benchmark", "benchmark", None)


def test_archive_refuses_unknown_run(tmp_path: Path) -> None:
    paths, instance_id, _data, _state = _instance(tmp_path)

    with pytest.raises(ResearchArtifactError, match="ARCHIVE_RUN_NOT_FOUND"):
        create_archive(paths, ROOT, instance_id, "missing-run", "experiment", None)


def test_snapshot_schema_probe_leaves_stopped_wal_database_untouched(tmp_path: Path) -> None:
    from contextlib import closing

    paths, instance_id, data, _state = _instance(tmp_path)
    database = data / "kernel/kernel.sqlite3"
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)

    before = {file.name: file.read_bytes() for file in database.parent.iterdir()}
    assert set(before) == {"kernel.sqlite3"}
    result = create_snapshot(paths, instance_id)

    assert {file.name: file.read_bytes() for file in database.parent.iterdir()} == before
    with tarfile.open(Path(result["snapshot"]), "r:gz") as archive:
        assert "data/kernel/kernel.sqlite3-wal" not in archive.getnames()
        assert "data/kernel/kernel.sqlite3-shm" not in archive.getnames()


def test_active_archive_reads_the_kernel_store_as_its_container_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.maintenance import research_artifacts

    paths, instance_id, data, state = _instance(tmp_path)
    manifest_path = state / "instance-manifest.v4.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["status"] = "active"
    manifest_path.write_bytes(canonical_bytes(manifest))
    container = "a" * 64
    calls = []

    def docker_command(command, **kwargs):
        calls.append(command)
        if command[1] == "ps":
            assert "label=com.docker.compose.project=groundwater-v6-research-test" in command
            assert "label=com.docker.compose.service=kernel-service" in command
            return container + "\n"
        if command[1] == "inspect":
            assert command[-1] == container
            return str(data / "kernel") + "\n"
        assert command[1:4] == ["exec", "-i", container]
        assert kwargs["input"] == "container-only-run"
        return json.dumps({
            "run_id": "container-only-run",
            "status": "FAILED",
            "arguments_json": "{}",
            "input_references_json": "[]",
            "outputs_json": "[]",
        })

    monkeypatch.setattr(research_artifacts.subprocess, "check_output", docker_command)
    result = create_archive(paths, ROOT, instance_id, "container-only-run", "experiment", None)
    assert len(calls) == 3
    with tarfile.open(Path(result["archive"]), "r:gz") as archive:
        evidence = json.loads(archive.extractfile("run-evidence.json").read())
        assert evidence["workflowRun"]["run_id"] == "container-only-run"


def test_snapshot_schema_probe_reads_uncheckpointed_committed_wal(tmp_path: Path) -> None:
    from contextlib import closing
    import subprocess
    import sys

    paths, instance_id, data, _state = _instance(tmp_path)
    database = data / "kernel/kernel.sqlite3"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE schema_version SET version=33")
        connection.commit()
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    subprocess.run([sys.executable, "-c", """
import os
import sqlite3
import sys
connection = sqlite3.connect(sys.argv[1])
connection.execute("UPDATE schema_version SET version=34")
connection.commit()
os._exit(0)
""", str(database)], check=True, timeout=5)

    before = {file.name: file.read_bytes() for file in database.parent.iterdir()}
    assert before["kernel.sqlite3-wal"]
    result = create_snapshot(paths, instance_id)

    assert {file.name: file.read_bytes() for file in database.parent.iterdir()} == before
    with tarfile.open(Path(result["snapshot"]), "r:gz") as archive:
        assert archive.extractfile("data/kernel/kernel.sqlite3-wal").read() == before["kernel.sqlite3-wal"]
        manifest = json.loads(archive.extractfile("manifest.json").read())
        assert manifest["schemaVersions"]["kernel"] == 34
