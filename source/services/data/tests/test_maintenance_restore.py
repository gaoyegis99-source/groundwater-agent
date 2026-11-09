"""Process-failure boundaries for the offline PostgreSQL/object restore."""

from contextlib import contextmanager
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import uuid

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from platform_data_service import maintenance
from platform_data_service.adapters.database import Database
from platform_data_service.adapters.storage import LocalStore
from platform_data_service.composition import Application
from platform_data_service.operations.worker import Worker
from platform_data_service.settings import Settings


def _source(tmp_path):
    source = tmp_path / "backup"
    objects = source / "files" / "objects"
    objects.mkdir(parents=True)
    (objects / ("a" * 32)).write_bytes(b"object")
    (source / "database.dump").write_bytes(b"dump")
    (source / "manifest.json").write_text(json.dumps({
        "schema": 1,
        "project": "local-data",
        "database_sha256": "sha256:" + hashlib.sha256(b"dump").hexdigest(),
        "objects_sha256": maintenance._file_tree_sha256(objects),
    }))
    return source


def _harness(monkeypatch, tmp_path):
    source = _source(tmp_path)
    root = tmp_path / "restored"
    root.mkdir()
    (root / ".runtime.lock").touch()
    settings = Settings("postgresql://postgres@localhost/restore_test", root)
    state = {"restored": False, "restore_calls": 0, "audits": []}

    class Connection:
        def execute(self, query):
            assert "pg_tables" in query
            return self

        def fetchone(self):
            return {"tablename": "schema_version"} if state["restored"] else None

    class Database:
        def __init__(self, dsn):
            assert dsn == settings.dsn

        @contextmanager
        def transaction(self):
            yield Connection()

    def audit(target, **_kwargs):
        state["audits"].append(target.root)
        assert (target.root / "objects" / ("a" * 32)).read_bytes() == b"object"
        return {"verified_objects": 1, "unreferenced_objects": []}

    def pg_restore(*_args, **_kwargs):
        state["restore_calls"] += 1
        state["restored"] = True

    monkeypatch.setattr(maintenance, "Database", Database)
    monkeypatch.setattr(maintenance, "audit", audit)
    monkeypatch.setattr(maintenance.subprocess, "run", pg_restore)
    return settings, source, state


def test_failed_pg_restore_leaves_target_clean_and_can_retry(monkeypatch, tmp_path):
    settings, source, state = _harness(monkeypatch, tmp_path)

    def fail_once(*_args, **_kwargs):
        state["restore_calls"] += 1
        if state["restore_calls"] == 1:
            raise subprocess.CalledProcessError(1, "pg_restore")
        state["restored"] = True

    monkeypatch.setattr(maintenance.subprocess, "run", fail_once)
    with pytest.raises(subprocess.CalledProcessError):
        maintenance.restore(settings, source)
    assert {item.name for item in settings.root.iterdir()} == {".runtime.lock"}
    assert not (settings.root / ".restore-stage").exists()

    assert maintenance.restore(settings, source)["verified_objects"] == 1
    assert state["restore_calls"] == 2
    assert state["audits"] == [
        settings.root / ".restore-stage" / "files",
        settings.root,
    ]


def test_restart_after_database_commit_publishes_without_replaying_dump(
    monkeypatch, tmp_path
):
    settings, source, state = _harness(monkeypatch, tmp_path)
    original_audit = maintenance.audit
    interrupted = False

    def audit(target, **kwargs):
        nonlocal interrupted
        if not interrupted and target.root != settings.root:
            interrupted = True
            raise RuntimeError("PROCESS_EXIT_AFTER_DATABASE_COMMIT")
        return original_audit(target, **kwargs)

    monkeypatch.setattr(maintenance, "audit", audit)
    with pytest.raises(RuntimeError, match="PROCESS_EXIT_AFTER_DATABASE_COMMIT"):
        maintenance.restore(settings, source)
    assert state["restored"] and state["restore_calls"] == 1
    assert not (settings.root / "objects").exists()
    assert (settings.root / ".restore-stage").is_dir()
    with pytest.raises(RuntimeError, match="DATA_RESTORE_INCOMPLETE"):
        LocalStore(settings.root)
    with pytest.raises(RuntimeError, match="DATA_RESTORE_INCOMPLETE"):
        Application(settings)
    with pytest.raises(RuntimeError, match="DATA_RESTORE_INCOMPLETE"):
        Worker(settings)
    assert not (settings.root / "objects").exists()

    assert maintenance.restore(settings, source)["verified_objects"] == 1
    assert state["restore_calls"] == 1
    assert (settings.root / "objects" / ("a" * 32)).read_bytes() == b"object"
    assert not (settings.root / ".restore-stage").exists()


def test_restart_after_object_publication_only_finishes_cleanup(monkeypatch, tmp_path):
    settings, source, state = _harness(monkeypatch, tmp_path)
    stage = settings.root / ".restore-stage"
    original_remove = maintenance.shutil.rmtree
    interrupted = False

    def remove(path, *args, **kwargs):
        nonlocal interrupted
        if path == stage and not interrupted:
            interrupted = True
            raise RuntimeError("PROCESS_EXIT_AFTER_OBJECT_PUBLISH")
        return original_remove(path, *args, **kwargs)

    monkeypatch.setattr(maintenance.shutil, "rmtree", remove)
    with pytest.raises(RuntimeError, match="PROCESS_EXIT_AFTER_OBJECT_PUBLISH"):
        maintenance.restore(settings, source)
    assert state["restore_calls"] == 1
    assert (settings.root / "objects" / ("a" * 32)).read_bytes() == b"object"
    assert stage.is_dir()

    assert maintenance.restore(settings, source)["verified_objects"] == 1
    assert state["restore_calls"] == 1
    assert not stage.exists()


def test_restore_rejects_tampered_dump_before_database_or_target_write(
    monkeypatch, tmp_path
):
    settings, source, state = _harness(monkeypatch, tmp_path)
    (source / "database.dump").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="digest mismatch"):
        maintenance.restore(settings, source)
    assert state["restore_calls"] == 0
    assert {item.name for item in settings.root.iterdir()} == {".runtime.lock"}


def test_restore_rejects_tampered_object_before_database_or_target_write(
    monkeypatch, tmp_path
):
    settings, source, state = _harness(monkeypatch, tmp_path)
    (source / "files" / "objects" / ("a" * 32)).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="objects digest mismatch"):
        maintenance.restore(settings, source)
    assert state["restore_calls"] == 0
    assert {item.name for item in settings.root.iterdir()} == {".runtime.lock"}


def test_restore_rejects_symlinked_backup_object(monkeypatch, tmp_path):
    settings, source, state = _harness(monkeypatch, tmp_path)
    object_path = source / "files" / "objects" / ("a" * 32)
    object_path.unlink()
    object_path.symlink_to(source / "database.dump")
    with pytest.raises(ValueError, match="unsafe entry"):
        maintenance.restore(settings, source)
    assert state["restore_calls"] == 0


def test_restore_accepts_existing_manifest_without_dump_digest(monkeypatch, tmp_path):
    settings, source, state = _harness(monkeypatch, tmp_path)
    (source / "manifest.json").write_text(json.dumps({
        "schema": 1, "project": "local-data",
    }))
    assert maintenance.restore(settings, source)["verified_objects"] == 1
    assert state["restore_calls"] == 1


def test_restore_only_needs_writable_target_mount(monkeypatch, tmp_path):
    settings, source, state = _harness(monkeypatch, tmp_path)
    original_mode = tmp_path.stat().st_mode & 0o777
    tmp_path.chmod(0o500)
    try:
        assert maintenance.restore(settings, source)["verified_objects"] == 1
    finally:
        tmp_path.chmod(original_mode)
    assert state["restore_calls"] == 1


@pytest.mark.parametrize("matching", [True, False])
def test_restore_restarts_only_its_own_interrupted_copy(
    monkeypatch, tmp_path, matching
):
    settings, source, state = _harness(monkeypatch, tmp_path)
    identity, dump_digest, objects_digest = maintenance._restore_source(settings, source)
    copying = settings.root / ".restore-copying"
    copying.mkdir()
    (copying / "restore-identity.json").write_text(json.dumps({
        "identity": identity if matching else "another-backup",
        "database_sha256": dump_digest,
        "objects_sha256": objects_digest,
    }))
    (copying / "partial").write_bytes(b"incomplete")
    if matching:
        assert maintenance.restore(settings, source)["verified_objects"] == 1
        assert state["restore_calls"] == 1
        assert not copying.exists()
    else:
        with pytest.raises(ValueError, match="belongs to another backup"):
            maintenance.restore(settings, source)
        assert state["restore_calls"] == 0
        assert (copying / "partial").is_file()
        assert not (settings.root / "objects").exists()


@pytest.mark.parametrize(
    ("damage", "error"),
    [
        ("identity", "belongs to another backup"),
        ("dump", "staged database digest mismatch"),
        ("object_bytes", "staged objects digest mismatch"),
        ("objects", "staged objects are missing"),
        ("extra", "staging contains unexpected entries"),
    ],
)
def test_restore_after_commit_rejects_changed_staging(
    monkeypatch, tmp_path, damage, error
):
    settings, source, state = _harness(monkeypatch, tmp_path)
    original_audit = maintenance.audit

    def interrupt_after_commit(target, **kwargs):
        if target.root != settings.root:
            raise RuntimeError("PROCESS_EXIT_AFTER_DATABASE_COMMIT")
        return original_audit(target, **kwargs)

    monkeypatch.setattr(maintenance, "audit", interrupt_after_commit)
    with pytest.raises(RuntimeError, match="PROCESS_EXIT_AFTER_DATABASE_COMMIT"):
        maintenance.restore(settings, source)
    stage = settings.root / ".restore-stage"
    if damage == "identity":
        manifest = json.loads((source / "manifest.json").read_text())
        manifest["note"] = "different backup"
        (source / "manifest.json").write_text(json.dumps(manifest))
    elif damage == "dump":
        (stage / "database.dump").write_bytes(b"changed")
    elif damage == "object_bytes":
        (stage / "files" / "objects" / ("a" * 32)).write_bytes(b"changed")
    elif damage == "objects":
        shutil.rmtree(stage / "files" / "objects")
    else:
        (stage / "unexpected").write_bytes(b"keep for inspection")
    monkeypatch.setattr(maintenance, "audit", original_audit)

    with pytest.raises(ValueError, match=error):
        maintenance.restore(settings, source)
    assert state["restore_calls"] == 1
    assert not (settings.root / "objects").exists()


def test_real_pg_dump_and_restore_with_staged_object_tree(
    monkeypatch, postgres, settings, tmp_path
):
    if shutil.which("docker") is None:
        pytest.skip("PostgreSQL client image requires Docker")

    original_run = subprocess.run

    def client_in_container(args, *, env, check):
        args = list(args)
        dump_target = None
        archive = None
        if args[0] == "pg_dump":
            file_index = args.index("--file")
            dump_target = Path(args[file_index + 1])
            del args[file_index:file_index + 2]
        else:
            archive = Path(args.pop()).read_bytes()
        command = [
            "docker", "run", "--rm", "-i", "--network", "host",
        ]
        for key in ("PGDATABASE", "PGUSER", "PGPASSWORD", "PGHOST", "PGPORT"):
            if key in env:
                command.extend(("-e", key))
        command.extend(("postgres:16.10-bookworm", *args))
        result = original_run(
            command, env=env, input=archive, check=check, capture_output=True
        )
        if dump_target is not None:
            dump_target.write_bytes(result.stdout)
        return result

    monkeypatch.setattr(maintenance.subprocess, "run", client_in_container)
    obj = LocalStore(settings.root).put(io.BytesIO(b"restored-object"), 100)
    source = tmp_path / "backup"
    maintenance.backup(settings, source)

    target_name = "restore_" + uuid.uuid4().hex
    with psycopg.connect(postgres, autocommit=True) as connection:
        connection.execute(
            psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(target_name))
        )
    target = replace(
        settings,
        dsn=make_conninfo(postgres, dbname=target_name),
        root=tmp_path / "restored",
    )
    try:
        report = maintenance.restore(target, source)
        Database(target.dsn).check()
        assert report["unreferenced_objects"] == [obj["id"]]
        assert (target.root / "objects" / obj["id"]).read_bytes() == b"restored-object"
        assert not (target.root / ".restore-stage").exists()
    finally:
        with psycopg.connect(postgres, autocommit=True) as connection:
            connection.execute(
                psycopg.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    psycopg.sql.Identifier(target_name)
                )
            )
