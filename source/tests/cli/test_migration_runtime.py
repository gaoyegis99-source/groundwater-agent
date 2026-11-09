from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from scripts.maintenance.contracts import canonical_bytes
from scripts.maintenance import migration_runtime
from scripts.maintenance.migration_runtime import MigrationError, apply_plan, create_plan
from scripts.maintenance import instance_runtime
from scripts.maintenance.xdg_layout import ensure_private_roots, resolve_lifecycle_paths


ROOT = Path(__file__).resolve().parents[2]
FAKE_DOCKER = Path("/test/project/bin/docker")


@pytest.fixture(autouse=True)
def inspect_legacy_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        migration_runtime,
        "_inspect_legacy_storage",
        lambda *_args, **_kwargs: (set(), 7, 6),
    )


def _legacy_instance(tmp_path: Path, *, status: str = "stopped"):
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
    instance_id = "legacy-one"
    data = paths.instance_data(instance_id)
    state = paths.instance_state(instance_id)
    (data / "kernel").mkdir(parents=True)
    state.mkdir(parents=True)
    with sqlite3.connect(data / "kernel/kernel.sqlite3") as connection:
        connection.execute("CREATE TABLE schema_version(version INTEGER NOT NULL)")
        connection.execute("INSERT INTO schema_version VALUES (33)")
        connection.execute("CREATE TABLE sessions(owner_subject TEXT NOT NULL)")
        connection.execute("CREATE TABLE confirmations(decision_subject TEXT)")
        connection.executemany(
            "INSERT INTO sessions VALUES (?)", (("legacy:alice",), ("legacy:bob",))
        )
    manifest = {
        "schemaVersion": "instance-manifest.v3",
        "instanceId": instance_id,
        "releaseId": "sha256-" + "a" * 64,
        "profile": "development",
        "composeProject": "groundwater-v6-legacy-one",
        "dataRoot": str(data),
        "stateRoot": str(state),
        "ports": {"gateway": 28186, "operator": 28187},
        "backupRoot": None,
        "createdAt": "2026-11-01T00:00:00Z",
        "status": status,
    }
    (state / "instance-manifest.v3.json").write_bytes(canonical_bytes(manifest))
    mapping = tmp_path / "identity-map.json"
    mapping.write_text(
        json.dumps(
            {
                "users": [
                    {
                        "legacySubject": subject,
                        "username": username,
                        "displayName": username.title(),
                        "role": role,
                        "initialPassword": "correct horse battery staple",
                    }
                    for subject, username, role in (
                        ("legacy:alice", "alice", "admin"),
                        ("legacy:bob", "bob", "researcher"),
                    )
                ]
            }
        )
    )
    mapping.chmod(0o600)
    return paths, instance_id, mapping


def test_migration_plan_freezes_identity_map_and_all_target_versions(
    tmp_path: Path,
) -> None:
    paths, instance_id, mapping = _legacy_instance(tmp_path)
    result = create_plan(paths, instance_id, mapping, docker=FAKE_DOCKER)

    assert result["legacySubjects"] == ["legacy:alice", "legacy:bob"]
    assert result["targets"] == {
        "publicApi": "v12",
        "kernel": 34,
        "data": 8,
        "knowledge": 7,
        "manifest": 4,
    }
    assert result["sourceSchemas"] == {"kernel": 33, "data": 7, "knowledge": 6}
    assert Path(result["plan"]).is_file()
    with pytest.raises(MigrationError, match="MIGRATION_CONFIRMATION_MISMATCH"):
        apply_plan(
            paths,
            Path(result["plan"]),
            "sha256:" + "0" * 64,
            docker=ROOT.parent / "bin/docker",
            source_root=ROOT,
        )


def test_migration_plan_rejects_active_instance_and_incomplete_mapping(
    tmp_path: Path,
) -> None:
    paths, instance_id, mapping = _legacy_instance(tmp_path, status="active")
    with pytest.raises(MigrationError, match="MIGRATION_REQUIRES_STOPPED_INSTANCE"):
        create_plan(paths, instance_id, mapping, docker=FAKE_DOCKER)

    manifest = paths.instance_state(instance_id) / "instance-manifest.v3.json"
    value = json.loads(manifest.read_text())
    value["status"] = "stopped"
    manifest.write_bytes(canonical_bytes(value))
    identity_map = json.loads(mapping.read_text())
    identity_map["users"].pop()
    mapping.write_text(json.dumps(identity_map))
    mapping.chmod(0o600)
    with pytest.raises(MigrationError, match="IDENTITY_MAP_INCOMPLETE:legacy:bob"):
        create_plan(paths, instance_id, mapping, docker=FAKE_DOCKER)


def test_identity_map_must_be_owner_only(tmp_path: Path) -> None:
    paths, instance_id, mapping = _legacy_instance(tmp_path)
    mapping.chmod(0o644)
    with pytest.raises(MigrationError, match="IDENTITY_MAP_PERMISSIONS_INVALID"):
        create_plan(paths, instance_id, mapping, docker=FAKE_DOCKER)


def test_migration_plan_requires_subjects_found_only_in_postgres(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, instance_id, mapping = _legacy_instance(tmp_path)
    monkeypatch.setattr(
        migration_runtime,
        "_inspect_legacy_storage",
        lambda *_args, **_kwargs: ({"legacy:data-only"}, 7, 6),
    )

    with pytest.raises(
        MigrationError, match="IDENTITY_MAP_INCOMPLETE:legacy:data-only"
    ):
        create_plan(paths, instance_id, mapping, docker=FAKE_DOCKER)


@pytest.mark.parametrize(
    ("data_version", "knowledge_version", "error"),
    ((3, 6, "DATA_SCHEMA_UNSUPPORTED"), (7, 7, "KNOWLEDGE_SCHEMA_UNSUPPORTED")),
)
def test_migration_plan_rejects_unsupported_postgres_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    data_version: int,
    knowledge_version: int,
    error: str,
) -> None:
    paths, instance_id, mapping = _legacy_instance(tmp_path)
    monkeypatch.setattr(
        migration_runtime,
        "_inspect_legacy_storage",
        lambda *_args, **_kwargs: (set(), data_version, knowledge_version),
    )

    with pytest.raises(MigrationError, match=error):
        create_plan(paths, instance_id, mapping, docker=FAKE_DOCKER)


def test_legacy_instance_can_be_stopped_before_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, instance_id, _mapping = _legacy_instance(tmp_path, status="active")
    monkeypatch.setattr(
        instance_runtime,
        "compose_command",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stderr=""),
    )

    result = instance_runtime.stop_instance(
        paths, instance_id, docker=ROOT.parent / "bin/docker"
    )

    assert result == {"status": "PASS", "instanceId": instance_id}
    manifest = paths.instance_state(instance_id) / "instance-manifest.v3.json"
    assert json.loads(manifest.read_text())["status"] == "stopped"


def test_storage_tree_readability_checks_nested_database_paths(
    tmp_path: Path,
) -> None:
    root = tmp_path / "instance"
    nested = root / "data-db" / "base"
    nested.mkdir(parents=True)
    (nested / "record").write_text("value")

    assert migration_runtime._storage_tree_readable(root)
    nested.chmod(0o000)
    try:
        assert not migration_runtime._storage_tree_readable(root)
    finally:
        nested.chmod(0o700)


def test_host_readable_storage_restores_privacy_after_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, instance_id, _mapping = _legacy_instance(tmp_path)
    root = paths.instance_data(instance_id)
    calls: list[bool] = []
    monkeypatch.setattr(
        migration_runtime,
        "_storage_tree_readable",
        lambda _root: False,
    )
    monkeypatch.setattr(
        migration_runtime,
        "_prepare_migration_storage",
        lambda *_args, private, **_kwargs: calls.append(private),
    )

    with pytest.raises(RuntimeError, match="injected snapshot failure"):
        with migration_runtime._host_readable_storage(
            paths, instance_id, root, {"services": {}}, FAKE_DOCKER
        ):
            raise RuntimeError("injected snapshot failure")

    assert calls == [False, True]
