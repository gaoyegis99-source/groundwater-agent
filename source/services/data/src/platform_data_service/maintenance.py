"""Offline maintenance. API and Workers must be stopped before invocation."""

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from .adapters.database import Database
from .adapters.storage import LocalStore


def references(db):
    objects = {}
    with db.transaction() as c:
        for row in c.execute("SELECT descriptor FROM objects"):
            obj = row["descriptor"]
            objects[obj["id"]] = obj
        for row in c.execute("""SELECT u.object FROM uploads u JOIN publications p ON p.id=u.publication_id
            WHERE p.state NOT IN ('ABORTED','FAILED','PUBLISHED')"""):
            obj = row["object"]
            objects[obj["id"]] = obj
        for row in c.execute("SELECT prepared FROM publications WHERE state='READY'"):
            for item in row["prepared"]:
                for obj in item["objects"]:
                    objects[obj["id"]] = obj
        for row in c.execute(
            "SELECT result_object FROM queries WHERE expires_at>now() AND result_object IS NOT NULL"
        ):
            obj = row["result_object"]
            objects[obj["id"]] = obj
        for row in c.execute(
            "SELECT result_object FROM previews WHERE expires_at>now() AND result_object IS NOT NULL"
        ):
            obj = row["result_object"]
            objects[obj["id"]] = obj
    return objects


def audit(settings, *, allow_incomplete_restore: bool = False):
    db = Database(settings.dsn)
    store = LocalStore(
        settings.root, allow_incomplete_restore=allow_incomplete_restore
    )
    db.check()
    objects = references(db)
    for obj in objects.values():
        store.verify(obj)
    with db.transaction() as c:
        broken = c.execute("""SELECT count(*) AS n FROM datasets d LEFT JOIN versions v ON v.id=d.current_version
            WHERE v.id IS NULL OR v.dataset_id<>d.id""").fetchone()["n"]
        broken += c.execute("""SELECT count(*) AS n FROM versions v JOIN publications p ON p.id=v.publication_id
            LEFT JOIN semantic_revisions s ON s.id=v.semantic_id
            WHERE p.state<>'PUBLISHED' OR s.id IS NULL OR s.version_id<>v.id""").fetchone()[
            "n"
        ]
        if broken:
            raise RuntimeError("CATALOG_INTEGRITY_FAILED")
    return {
        "verified_objects": len(objects),
        "unreferenced_objects": sorted(
            x.name for x in store.objects.iterdir() if x.name not in objects
        ),
    }


def gc(settings):
    result = audit(settings)
    store = LocalStore(settings.root)
    for key in result["unreferenced_objects"]:
        store.delete({"id": key})
    for path in (settings.root / "work").iterdir():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    return {"removed_objects": len(result["unreferenced_objects"])}


def backup(settings, destination: Path):
    if destination.resolve().is_relative_to(settings.root.resolve()):
        raise ValueError("backup must be outside the data root")
    if destination.exists():
        raise ValueError("backup destination must not exist")
    report = audit(settings)
    _safe_restore_tree(settings.root)
    destination.mkdir(parents=True)
    # pg_dump reads credentials through environment, never command-line arguments.
    from psycopg.conninfo import conninfo_to_dict

    params = conninfo_to_dict(settings.dsn)
    env = os.environ.copy()
    for name, value in params.items():
        env[
            {
                "dbname": "PGDATABASE",
                "user": "PGUSER",
                "password": "PGPASSWORD",
                "host": "PGHOST",
                "port": "PGPORT",
            }.get(name, "PG" + name.upper())
        ] = value
    subprocess.run(
        ["pg_dump", "--format=custom", "--file", str(destination / "database.dump")],
        env=env,
        check=True,
    )
    shutil.copytree(
        settings.root,
        destination / "files",
        ignore=shutil.ignore_patterns(".runtime.lock", ".worker.lock"),
    )
    (destination / "manifest.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "service": "1.0.0",
                "project": settings.project,
                "database_sha256": _file_sha256(destination / "database.dump"),
                "objects_sha256": _file_tree_sha256(destination / "files" / "objects"),
                "audit": report,
            },
            indent=2,
        )
    )
    return {"backup": str(destination)}


def _file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()


def _safe_restore_tree(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("backup files contain an unsafe entry")


def _file_tree_sha256(root: Path) -> str:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("backup objects are invalid")
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("backup files contain an unsafe entry")
        if path.is_file():
            relative = path.relative_to(root).as_posix().encode("utf-8")
            digest.update(relative)
            digest.update(b"\0")
            digest.update(str(path.stat().st_size).encode("ascii"))
            digest.update(b"\0")
            digest.update(_file_sha256(path).encode("ascii"))
            digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


def _restore_source(settings, source: Path) -> tuple[str, str, str]:
    manifest_path = source / "manifest.json"
    dump_path = source / "database.dump"
    files_path = source / "files"
    if (
        manifest_path.is_symlink()
        or not manifest_path.is_file()
        or dump_path.is_symlink()
        or not dump_path.is_file()
        or files_path.is_symlink()
        or not files_path.is_dir()
        or (files_path / "objects").is_symlink()
        or not (files_path / "objects").is_dir()
    ):
        raise ValueError("backup files are incomplete")
    _safe_restore_tree(files_path)
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("backup manifest invalid") from error
    if not isinstance(manifest, dict) or (
        manifest.get("schema") != 1 or manifest.get("project") != settings.project
    ):
        raise ValueError("backup identity mismatch")
    dump_digest = _file_sha256(dump_path)
    if manifest.get("database_sha256", dump_digest) != dump_digest:
        raise ValueError("backup database digest mismatch")
    objects_digest = _file_tree_sha256(files_path / "objects")
    if manifest.get("objects_sha256", objects_digest) != objects_digest:
        raise ValueError("backup objects digest mismatch")
    identity = hashlib.sha256(
        manifest_bytes + b"\n" + dump_digest.encode("ascii")
        + b"\n" + objects_digest.encode("ascii")
    ).hexdigest()
    return identity, dump_digest, objects_digest


def _restore_stage(
    root: Path, source: Path, identity: str, dump_digest: str,
    objects_digest: str, *, published: bool,
) -> Path:
    # DATA_ROOT may be /data in a read-only container. Keep both directories
    # inside the locked writable mount, on the same filesystem as objects/.
    stage = root / ".restore-stage"
    copying = root / ".restore-copying"
    marker = "restore-identity.json"
    expected = {
        "identity": identity,
        "database_sha256": dump_digest,
        "objects_sha256": objects_digest,
    }
    if stage.exists() or stage.is_symlink():
        if stage.is_symlink() or not stage.is_dir():
            raise ValueError("restore staging is invalid")
        if {path.name for path in stage.iterdir()} != {
            marker, "database.dump", "files"
        }:
            raise ValueError("restore staging contains unexpected entries")
        if (stage / "files").is_symlink() or not (stage / "files").is_dir():
            raise ValueError("restore staging files are invalid")
        if (stage / marker).is_symlink():
            raise ValueError("restore staging identity is invalid")
        try:
            recorded = json.loads((stage / marker).read_text())
        except (OSError, ValueError) as error:
            raise ValueError("restore staging identity is invalid") from error
        if recorded != expected:
            raise ValueError("restore staging belongs to another backup")
        _safe_restore_tree(stage / "files")
        staged_dump = stage / "database.dump"
        if staged_dump.is_symlink() or not staged_dump.is_file():
            raise ValueError("restore staged database is missing")
        if _file_sha256(staged_dump) != dump_digest:
            raise ValueError("restore staged database digest mismatch")
        staged_objects = stage / "files" / "objects"
        if published:
            if staged_objects.exists() or staged_objects.is_symlink():
                raise ValueError("restore staging publication is inconsistent")
        else:
            if staged_objects.is_symlink() or not staged_objects.is_dir():
                raise ValueError("restore staged objects are missing")
            if _file_tree_sha256(staged_objects) != objects_digest:
                raise ValueError("restore staged objects digest mismatch")
        return stage
    if copying.exists() or copying.is_symlink():
        if copying.is_symlink() or not copying.is_dir():
            raise ValueError("restore copying path is invalid")
        if (copying / marker).is_symlink():
            raise ValueError("restore copying identity is invalid")
        try:
            recorded = json.loads((copying / marker).read_text())
        except (OSError, ValueError) as error:
            raise ValueError("restore copying identity is invalid") from error
        if recorded != expected:
            raise ValueError("restore copying belongs to another backup")
        shutil.rmtree(copying)
    copying.mkdir()
    try:
        (copying / marker).write_text(json.dumps(expected))
        shutil.copytree(source / "files", copying / "files")
        _safe_restore_tree(copying / "files")
        if _file_tree_sha256(copying / "files" / "objects") != objects_digest:
            raise ValueError("restore staged objects digest mismatch")
        shutil.copy2(source / "database.dump", copying / "database.dump")
        if _file_sha256(copying / "database.dump") != dump_digest:
            raise ValueError("restore staged database digest mismatch")
        os.replace(copying, stage)
    except BaseException:
        if copying.exists():
            shutil.rmtree(copying)
        raise
    return stage


def _restore_database_is_empty(db: Database) -> bool:
    with db.transaction() as connection:
        return connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public'"
        ).fetchone() is None


def restore(settings, source: Path):
    from psycopg.conninfo import conninfo_to_dict

    root = settings.root
    if root.is_symlink():
        raise ValueError("restore target is invalid")
    if source.resolve().is_relative_to(root.resolve()):
        raise ValueError("backup must be outside the data root")
    identity, dump_digest, objects_digest = _restore_source(settings, source)
    stage = root / ".restore-stage"
    root.mkdir(parents=True, exist_ok=True)
    existing = {path.name for path in root.iterdir()}
    already_published = "objects" in existing and stage.exists()
    if already_published:
        allowed = {".runtime.lock", ".restore-stage", "objects", "work"}
    elif stage.exists():
        allowed = {".runtime.lock", ".restore-stage"}
    elif (root / ".restore-copying").exists():
        allowed = {".runtime.lock", ".restore-copying"}
    else:
        allowed = {".runtime.lock"}
    if existing - allowed:
        raise ValueError("restore requires an empty data directory")
    if already_published and (
        (root / "objects").is_symlink() or not (root / "objects").is_dir()
    ):
        raise ValueError("restore target objects are invalid")
    db = Database(settings.dsn)
    database_empty = _restore_database_is_empty(db)
    if not database_empty and not stage.exists():
        raise ValueError("restore requires an empty database")
    stage = _restore_stage(
        root, source, identity, dump_digest, objects_digest,
        published=already_published,
    )
    env = os.environ.copy()
    for name, value in conninfo_to_dict(settings.dsn).items():
        env[
            {
                "dbname": "PGDATABASE",
                "user": "PGUSER",
                "password": "PGPASSWORD",
                "host": "PGHOST",
                "port": "PGPORT",
            }.get(name, "PG" + name.upper())
        ] = value
    if database_empty:
        try:
            subprocess.run(
                [
                    "pg_restore", "--exit-on-error", "--single-transaction",
                    "--no-owner", "--dbname", env["PGDATABASE"],
                    str(stage / "database.dump"),
                ],
                env=env,
                check=True,
            )
        except BaseException:
            if _restore_database_is_empty(db):
                shutil.rmtree(stage)
            raise
    if not already_published:
        # Verify all database object descriptors against the staged bytes. The
        # root lock stays in place while one rename publishes the object tree.
        staged_objects = stage / "files" / "objects"
        if staged_objects.is_symlink() or not staged_objects.is_dir():
            raise ValueError("restore staged objects are missing")
        audit(replace(settings, root=stage / "files"))
        os.replace(staged_objects, root / "objects")
    if _file_tree_sha256(root / "objects") != objects_digest:
        raise ValueError("restore published objects digest mismatch")
    report = audit(settings, allow_incomplete_restore=True)
    shutil.rmtree(stage)
    return report
