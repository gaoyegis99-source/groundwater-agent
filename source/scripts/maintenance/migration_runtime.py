"""Offline, shadow-storage migration from managed V11 instances to V12."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import sqlite3
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterator
from urllib.parse import quote
from urllib.request import urlopen

from .contracts import canonical_bytes, sha256_digest, validate_instance_manifest_v4
from .daemon_runtime import compose_command, docker_environment
from .instance_runtime import STORAGE_INITIALIZERS, read_manifest
from .project_environment import child_environment
from .research_artifacts import create_snapshot
from .xdg_layout import LifecyclePaths, owned_path, validate_instance_id


class MigrationError(RuntimeError):
    pass


DATA_RETAINED_TABLES = (
    "publications", "uploads", "datasets", "versions", "objects",
    "semantic_revisions", "lineage", "metrics", "operations",
    "operation_events", "queries", "publication_events", "previews",
    "quality_suites", "quality_suite_versions", "quality_bindings",
    "quality_runs", "quality_rule_results", "quality_alerts", "outbox_events",
    "ml_experiments", "ml_runs", "model_registry_versions",
    "model_stage_events", "model_alias_revisions",
)
KNOWLEDGE_RETAINED_TABLES = (
    "imports", "documents", "versions", "lifecycle", "units", "embeddings",
    "search_entries", "receipts", "import_manual_retries",
)
DATA_LINEAGE_QUERIES = {
    "datasetVersions": """SELECT jsonb_build_array(
        id,dataset_id,publication_id)::text AS value FROM versions ORDER BY id""",
    "versionObjects": """SELECT jsonb_build_array(
        id,version_id)::text AS value FROM objects ORDER BY id""",
    "versionLineage": """SELECT jsonb_build_array(
        version_id,input_version_id)::text AS value
        FROM lineage ORDER BY version_id,input_version_id""",
}
KNOWLEDGE_LINEAGE_QUERIES = {
    "documentVersions": """SELECT jsonb_build_array(
        id,document_id,import_id,checksum)::text AS value FROM versions ORDER BY id""",
    "versionUnits": """SELECT jsonb_build_array(
        version_id,unit_id,ordinal,checksum)::text AS value
        FROM units ORDER BY version_id,unit_id""",
    "versionLifecycle": """SELECT jsonb_build_array(
        version_id,revision,status)::text AS value
        FROM lifecycle ORDER BY version_id,revision""",
}


def _ensure_service_imports() -> None:
    source = Path(__file__).resolve().parents[2]
    for path in (
        source,
        source / "platform/assembler",
        source / "kernel/src",
        source / "packages/embedding-client/src",
        source / "packages/operator-pack/src",
        source / "packages/protocol-contracts/src",
        source / "packages/workflow-compiler/src",
        source / "services/data/src",
        source / "services/executor/src",
        source / "services/knowledge-next/src",
    ):
        value = str(path)
        if value not in sys.path:
            sys.path.insert(0, value)


def _write_atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _identity_map(path: Path) -> tuple[dict[str, dict[str, str]], str]:
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise MigrationError("IDENTITY_MAP_PERMISSIONS_INVALID")
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise MigrationError("IDENTITY_MAP_INVALID") from error
    users = value.get("users") if isinstance(value, dict) else None
    if not isinstance(users, list) or not users:
        raise MigrationError("IDENTITY_MAP_INVALID")
    result: dict[str, dict[str, str]] = {}
    usernames: set[str] = set()
    for item in users:
        required = {"legacySubject", "username", "displayName", "role", "initialPassword"}
        if not isinstance(item, dict) or set(item) != required:
            raise MigrationError("IDENTITY_MAP_INVALID")
        if item["role"] not in {"admin", "researcher", "viewer"}:
            raise MigrationError("IDENTITY_MAP_INVALID")
        subject = item["legacySubject"]
        username = item["username"]
        if not all(isinstance(item[name], str) and item[name] for name in required):
            raise MigrationError("IDENTITY_MAP_INVALID")
        if subject in result or username in usernames:
            raise MigrationError("IDENTITY_MAP_DUPLICATE")
        result[subject] = item
        usernames.add(username)
    return result, sha256_digest(path.read_bytes())


def _kernel_subjects(database: Path) -> set[str]:
    with sqlite3.connect(database) as connection:
        version = connection.execute("SELECT version FROM schema_version").fetchone()[0]
        if version not in range(29, 35):
            raise MigrationError("KERNEL_SCHEMA_UNSUPPORTED")
        rows = connection.execute(
            "SELECT DISTINCT owner_subject AS subject FROM sessions "
            "WHERE owner_subject <> '' UNION SELECT DISTINCT decision_subject AS subject "
            "FROM confirmations WHERE decision_subject IS NOT NULL AND decision_subject <> ''"
        ).fetchall()
    return {row[0] for row in rows}


def _kernel_schema_version(database: Path) -> int:
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT version FROM schema_version").fetchone()
    if row is None or not isinstance(row[0], int):
        raise MigrationError("KERNEL_SCHEMA_UNSUPPORTED")
    return row[0]


def _prepare_migration_storage(
    paths: LifecyclePaths,
    instance_id: str,
    root: Path,
    compose: dict[str, Any],
    docker: Path,
    *,
    private: bool,
) -> None:
    boundary = paths.instance_data(instance_id).parent
    if (
        root.is_symlink()
        or root.parent.resolve() != boundary.resolve()
        or not root.is_dir()
        or root.stat().st_uid != os.getuid()
        or root.stat().st_mode & 0o077
    ):
        raise MigrationError("MIGRATION_STORAGE_BOUNDARY_INVALID")
    services = compose.get("services")
    if not isinstance(services, dict):
        raise MigrationError("MIGRATION_COMPOSE_INVALID")
    public_script = "set -eu; chmod -R a+rwX /target"
    private_script = (
        "set -eu; owner=$1; "
        'case $owner in *[!0-9]*) uid=$(id -u "$owner");; *) uid=$owner;; esac; '
        'chown -R "$uid" /target; chmod -R u+rwX,go-rwx /target'
    )
    for relative, service, owner in STORAGE_INITIALIZERS:
        target = root / relative
        definition = services.get(service)
        image = definition.get("image") if isinstance(definition, dict) else None
        if target.is_symlink() or not target.is_dir() or not isinstance(image, str):
            raise MigrationError("MIGRATION_STORAGE_INITIALIZER_INVALID")
        completed = subprocess.run(
            [
                str(docker), "run", "--rm", "--network", "none", "--user", "0",
                "--entrypoint", "/bin/sh", "--mount",
                f"type=bind,src={target},dst=/target", image, "-c",
                private_script if private else public_script, "--", owner,
            ],
            env=docker_environment(paths),
            capture_output=True,
            text=True,
            check=False,
            timeout=1800,
        )
        if completed.returncode:
            raise MigrationError("MIGRATION_STORAGE_PREPARE_FAILED:" + relative)


def _storage_tree_readable(root: Path) -> bool:
    """Prove every stopped-instance directory can be copied by the host user."""

    try:
        for current, directories, files in os.walk(root):
            current_path = Path(current)
            if not os.access(current_path, os.R_OK | os.X_OK):
                return False
            for name in directories:
                if not os.access(current_path / name, os.R_OK | os.X_OK):
                    return False
            for name in files:
                if not os.access(current_path / name, os.R_OK):
                    return False
    except OSError:
        return False
    return True


@contextmanager
def _host_readable_storage(
    paths: LifecyclePaths,
    instance_id: str,
    root: Path,
    compose: dict[str, Any],
    docker: Path,
) -> Iterator[None]:
    """Temporarily expose rootless-owned storage and always restore privacy."""

    prepared = not _storage_tree_readable(root)
    if prepared:
        _prepare_migration_storage(
            paths, instance_id, root, compose, docker, private=False
        )
    try:
        yield
    finally:
        if prepared and root.exists():
            _prepare_migration_storage(
                paths, instance_id, root, compose, docker, private=True
            )


def create_plan(
    paths: LifecyclePaths,
    instance_id: str,
    identity_map_path: Path,
    *,
    docker: Path | None = None,
) -> dict[str, Any]:
    instance_id = validate_instance_id(instance_id)
    _manifest_path, instance = read_manifest(paths, instance_id)
    if instance["status"] != "stopped":
        raise MigrationError("MIGRATION_REQUIRES_STOPPED_INSTANCE")
    if instance["schemaVersion"] == "instance-manifest.v4":
        raise MigrationError("INSTANCE_ALREADY_V12")
    if docker is None:
        raise MigrationError("MIGRATION_DOCKER_REQUIRED")
    identities, mapping_digest = _identity_map(identity_map_path)
    source_data = Path(instance["dataRoot"])
    kernel = source_data / "kernel/kernel.sqlite3"
    state = Path(instance["stateRoot"])
    compose_path = state / "compose.json"
    compose = (
        json.loads(compose_path.read_text())
        if compose_path.is_file()
        else {}
    )
    if not _storage_tree_readable(source_data) and not compose:
        raise MigrationError("MIGRATION_COMPOSE_INVALID")
    with _host_readable_storage(
        paths, instance_id, source_data, compose, docker
    ):
        kernel_version = _kernel_schema_version(kernel)
        kernel_subjects = _kernel_subjects(kernel)
        if kernel_version not in range(29, 34):
            raise MigrationError("KERNEL_SCHEMA_UNSUPPORTED")
        postgres_subjects, data_version, knowledge_version = _inspect_legacy_storage(
            paths,
            instance_id,
            source_data,
            state,
            docker,
            mapping_digest[7:23],
        )
        if data_version not in range(4, 8):
            raise MigrationError("DATA_SCHEMA_UNSUPPORTED")
        if knowledge_version not in range(4, 7):
            raise MigrationError("KNOWLEDGE_SCHEMA_UNSUPPORTED")
        subjects = kernel_subjects | postgres_subjects
        missing = sorted(subjects - identities.keys())
        if missing:
            raise MigrationError("IDENTITY_MAP_INCOMPLETE:" + ",".join(missing))
        body = {
            "schemaVersion": "research-migration-plan.v1",
            "instanceId": instance_id,
            "sourceManifest": str(_manifest_path),
            "sourceManifestSha256": sha256_digest(_manifest_path.read_bytes()),
            "identityMap": str(identity_map_path.absolute()),
            "identityMapSha256": mapping_digest,
            "legacySubjects": sorted(subjects),
            "sourceSchemas": {
                "kernel": kernel_version,
                "data": data_version,
                "knowledge": knowledge_version,
            },
            "targets": {
                "publicApi": "v12",
                "kernel": 34,
                "data": 8,
                "knowledge": 7,
                "manifest": 4,
            },
        }
        plan = body | {"confirmationDigest": sha256_digest(canonical_bytes(body))}
        destination = owned_path(
            f"migration-plans/{instance_id}-{plan['confirmationDigest'][7:23]}.json",
            paths.state,
        )
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _write_atomic(destination, plan)
    return {"status": "PLAN", "plan": str(destination), **plan}


def _compose_command(
    docker: Path, compose: Path, override: Path, env_file: Path, project: str
) -> list[str]:
    return [
        str(docker), "compose", "--project-name", project,
        "--env-file", str(env_file), "-f", str(compose), "-f", str(override),
    ]


def _reserve_loopback_ports(count: int) -> tuple[list[socket.socket], list[int]]:
    reservations: list[socket.socket] = []
    ports: list[int] = []
    for _index in range(count):
        reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        reservation.bind(("127.0.0.1", 0))
        reservations.append(reservation)
        ports.append(int(reservation.getsockname()[1]))
    return reservations, ports


def _owner_subjects(database: Any) -> set[str]:
    with database.transaction() as connection:
        tables = [
            row["table_name"]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND column_name='owner_subject' "
                "ORDER BY table_name"
            ).fetchall()
        ]
        subjects: set[str] = set()
        for table in tables:
            identifier = '"' + table.replace('"', '""') + '"'
            subjects.update(
                row["owner_subject"]
                for row in connection.execute(
                    f"SELECT DISTINCT owner_subject FROM {identifier} "
                    "WHERE owner_subject IS NOT NULL AND owner_subject <> ''"
                ).fetchall()
            )
    return subjects


def _postgres_subjects(data: Any, knowledge: Any) -> set[str]:
    return _owner_subjects(data) | _owner_subjects(knowledge)


def _postgres_schema_versions(data: Any, knowledge: Any) -> tuple[int, int]:
    with data.transaction() as connection:
        data_row = connection.execute(
            "SELECT version FROM schema_version"
        ).fetchone()
    with knowledge.transaction() as connection:
        knowledge_row = connection.execute(
            "SELECT schema_version FROM meta WHERE singleton=true"
        ).fetchone()
    if data_row is None or knowledge_row is None:
        raise MigrationError("POSTGRES_SCHEMA_METADATA_MISSING")
    return int(data_row["version"]), int(knowledge_row["schema_version"])


@contextmanager
def _legacy_databases(
    paths: LifecyclePaths,
    instance_id: str,
    storage_root: Path,
    state: Path,
    docker: Path,
    purpose: str,
) -> Iterator[tuple[Any, Any]]:
    """Open legacy databases through loopback ports against an isolated storage root."""

    _ensure_service_imports()
    from platform_data_service.adapters.database import Database as DataDatabase
    from platform_knowledge_next.config import Settings as KnowledgeSettings
    from platform_knowledge_next.database import Database as KnowledgeDatabase

    compose = state / "compose.json"
    compose_value = json.loads(compose.read_text())
    try:
        knowledge_project = compose_value["services"]["knowledge-next-service"][
            "environment"
        ]["KNOWLEDGE_PROJECT"]
    except (KeyError, TypeError) as error:
        raise MigrationError("MIGRATION_COMPOSE_INVALID") from error
    source_lines = (state / "instance.env").read_text().splitlines()
    env_lines = [
        f"PLATFORM_INSTANCE_DATA_ROOT={storage_root}"
        if line.startswith("PLATFORM_INSTANCE_DATA_ROOT=")
        else line
        for line in source_lines
    ]
    if not any(
        line.startswith("PLATFORM_INSTANCE_DATA_ROOT=") for line in source_lines
    ):
        raise MigrationError("MIGRATION_DATA_ROOT_ENV_MISSING")
    project = (
        f"gw-v12-{purpose}-{instance_id.replace('.', '-')}"
    )
    with tempfile.TemporaryDirectory(prefix="gw-v12-database-") as temporary:
        temporary_root = Path(temporary)
        reservations, (data_port, knowledge_port) = _reserve_loopback_ports(2)
        env_file = temporary_root / "migration.env"
        env_file.write_text("\n".join(env_lines) + "\n")
        override = temporary_root / "compose.override.json"
        override.write_bytes(
            canonical_bytes(
                {
                    "networks": {"migration": {"internal": False}},
                    "services": {
                        "data-db": {
                            "networks": {"migration": {}},
                            "ports": [f"127.0.0.1:{data_port}:5432"],
                        },
                        "knowledge-next-db": {
                            "networks": {"migration": {}},
                            "ports": [f"127.0.0.1:{knowledge_port}:5432"],
                        },
                    },
                }
            )
        )
        command = _compose_command(docker, compose, override, env_file, project)
        for reservation in reservations:
            reservation.close()
        try:
            started = subprocess.run(
                [*command, "up", "-d", "--wait", "data-db", "knowledge-next-db"],
                capture_output=True,
                text=True,
                check=False,
                timeout=300,
            )
            if started.returncode:
                raise MigrationError(
                    "MIGRATION_DATABASE_START_FAILED:" + started.stderr.strip()[-500:]
                )
            data_password = (
                paths.config / f"instances/{instance_id}/secrets/data-db-password"
            ).read_text().strip()
            knowledge_password = (
                paths.config / f"instances/{instance_id}/secrets/knowledge-db-password"
            ).read_text().strip()
            data = DataDatabase(
                f"postgresql://data:{quote(data_password, safe='')}@127.0.0.1:{data_port}/data"
            )
            knowledge = KnowledgeDatabase(
                KnowledgeSettings(
                    f"postgresql://knowledge:{quote(knowledge_password, safe='')}@127.0.0.1:{knowledge_port}/knowledge",
                    project=knowledge_project,
                )
            )
            deadline = time.monotonic() + 30
            while True:
                try:
                    with data.transaction() as connection:
                        connection.execute("SELECT 1").fetchone()
                    with knowledge.transaction() as connection:
                        connection.execute("SELECT 1").fetchone()
                    break
                except Exception as error:
                    if time.monotonic() >= deadline:
                        raise MigrationError(
                            "MIGRATION_DATABASE_CONNECTION_FAILED"
                        ) from error
                    time.sleep(0.25)
            yield data, knowledge
        finally:
            subprocess.run(
                [*command, "down"], capture_output=True, check=False, timeout=300
            )


def _inspect_legacy_storage(
    paths: LifecyclePaths,
    instance_id: str,
    source_data: Path,
    state: Path,
    docker: Path,
    suffix: str,
) -> tuple[set[str], int, int]:
    """Inspect owner identities and schema versions without starting source storage."""

    compose = json.loads((state / "compose.json").read_text())
    inspection = source_data.with_name(f".{source_data.name}.inspect-{suffix}")
    if inspection.exists():
        raise MigrationError("MIGRATION_INSPECTION_CONFLICT")
    source_prepared = False
    try:
        if not _storage_tree_readable(source_data):
            _prepare_migration_storage(
                paths, instance_id, source_data, compose, docker, private=False
            )
            source_prepared = True
        shutil.copytree(source_data, inspection, symlinks=False)
        _prepare_migration_storage(
            paths, instance_id, inspection, compose, docker, private=False
        )
        with _legacy_databases(
            paths, instance_id, inspection, state, docker, f"inspect-{suffix}"
        ) as (data, knowledge):
            data_version, knowledge_version = _postgres_schema_versions(data, knowledge)
            return _postgres_subjects(data, knowledge), data_version, knowledge_version
    finally:
        try:
            if inspection.exists():
                try:
                    for relative, _service, _owner in STORAGE_INITIALIZERS:
                        (inspection / relative).mkdir(parents=True, exist_ok=True)
                    _prepare_migration_storage(
                        paths, instance_id, inspection, compose, docker, private=False
                    )
                finally:
                    shutil.rmtree(inspection)
        finally:
            if source_prepared:
                _prepare_migration_storage(
                    paths, instance_id, source_data, compose, docker, private=True
                )


def _sqlite_counts(
    database: Path, names: tuple[str, ...] | None = None
) -> dict[str, int]:
    with sqlite3.connect(database) as connection:
        present = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            if row[0] != "schema_version" and not row[0].startswith("sqlite_")
        }
        selected = sorted(present) if names is None else list(names)
        return {
            name: connection.execute(
                f'SELECT COUNT(*) FROM "{name.replace(chr(34), chr(34) * 2)}"'
            ).fetchone()[0]
            for name in selected
            if name in present
        }


def _postgres_counts(database: Any, names: tuple[str, ...]) -> dict[str, int]:
    with database.transaction() as connection:
        return {
            name: connection.execute(f"SELECT COUNT(*) AS count FROM {name}").fetchone()[
                "count"
            ]
            for name in names
            if connection.execute(
                "SELECT to_regclass(%s) AS name", (name,)
            ).fetchone()["name"] is not None
        }


def _postgres_lineage(database: Any, queries: dict[str, str]) -> dict[str, str]:
    """Freeze identity relationships independently from mutable owner columns."""

    with database.transaction() as connection:
        return {
            name: sha256_digest(
                canonical_bytes(
                    [row["value"] for row in connection.execute(query).fetchall()]
                )
            )
            for name, query in queries.items()
        }


def _object_checksums(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): sha256_digest(path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def _map_owners(
    kernel_path: Path,
    data: Any,
    knowledge: Any,
    project_id: str,
    identities: dict[str, dict[str, str]],
) -> dict[str, str]:
    _ensure_service_imports()
    from platform_kernel.access.research_auth import create_user, put_membership
    from platform_kernel.schema.interaction import SESSIONS_OWNER_SUBJECT_TRIGGER

    mapped: dict[str, str] = {}
    for subject, item in identities.items():
        user = create_user(
            kernel_path, item["username"], item["displayName"], item["initialPassword"]
        )
        user_id = str(user["userId"])
        put_membership(kernel_path, project_id, user_id, item["role"])
        mapped[subject] = user_id
    with sqlite3.connect(kernel_path) as connection:
        connection.execute("DROP TRIGGER sessions_owner_subject_immutable")
        for old, new in mapped.items():
            connection.execute(
                "UPDATE sessions SET owner_subject=? WHERE owner_subject=?", (new, old)
            )
            connection.execute(
                "UPDATE confirmations SET decision_subject=? WHERE decision_subject=?",
                (new, old),
            )
        connection.executescript(SESSIONS_OWNER_SUBJECT_TRIGGER)
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise MigrationError("KERNEL_FOREIGN_KEY_CHECK_FAILED")
    for database in (data, knowledge):
        with database.transaction() as connection:
            tables = [
                row["table_name"]
                for row in connection.execute(
                    "SELECT table_name FROM information_schema.columns "
                    "WHERE table_schema=current_schema() "
                    "AND column_name='owner_subject' ORDER BY table_name"
                ).fetchall()
            ]
            for table in tables:
                identifier = '"' + table.replace('"', '""') + '"'
                for old, new in mapped.items():
                    connection.execute(
                        f"UPDATE {identifier} SET owner_subject=%s "
                        "WHERE owner_subject=%s",
                        (new, old),
                    )
    return mapped


def apply_plan(
    paths: LifecyclePaths,
    plan_path: Path,
    confirmation: str,
    *,
    docker: Path,
    source_root: Path,
) -> dict[str, Any]:
    _ensure_service_imports()
    from platform_kernel.database import migrate_database

    try:
        plan = json.loads(plan_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise MigrationError("MIGRATION_PLAN_INVALID") from error
    body = {key: value for key, value in plan.items() if key != "confirmationDigest"}
    expected = sha256_digest(canonical_bytes(body))
    if plan.get("confirmationDigest") != expected or confirmation != expected:
        raise MigrationError("MIGRATION_CONFIRMATION_MISMATCH")
    _manifest_path, instance = read_manifest(paths, plan["instanceId"])
    if instance["status"] != "stopped" or instance["schemaVersion"] != "instance-manifest.v3":
        raise MigrationError("MIGRATION_SOURCE_CHANGED")
    if sha256_digest(_manifest_path.read_bytes()) != plan["sourceManifestSha256"]:
        raise MigrationError("MIGRATION_SOURCE_CHANGED")
    mapping_path = Path(plan["identityMap"])
    identities, mapping_digest = _identity_map(mapping_path)
    if mapping_digest != plan["identityMapSha256"]:
        raise MigrationError("IDENTITY_MAP_CHANGED")

    from assembler import compile_product

    bundle = compile_product(
        source_root,
        source_root / "products/groundwater/product-manifest.json",
    )
    if not (bundle / "compose.json").is_file():
        raise MigrationError("V12_ASSEMBLY_FAILED")

    source_data = Path(instance["dataRoot"])
    legacy_compose = json.loads(
        (Path(instance["stateRoot"]) / "compose.json").read_text()
    )
    suffix = expected[7:23]
    shadow = source_data.with_name(f".{source_data.name}.v12-shadow-{suffix}")
    previous = source_data.with_name(f".{source_data.name}.v11-{suffix}")
    if shadow.exists() or previous.exists():
        raise MigrationError("MIGRATION_SHADOW_CONFLICT")
    with _host_readable_storage(
        paths, plan["instanceId"], source_data, legacy_compose, docker
    ):
        snapshot = create_snapshot(
            paths, plan["instanceId"], source_versions=plan["sourceSchemas"]
        )
        shutil.copytree(source_data, shadow, symlinks=False)
        object_checksums = _object_checksums(source_data / "data/objects")
        if object_checksums != _object_checksums(shadow / "data/objects"):
            raise MigrationError("OBJECT_CHECKSUM_MISMATCH")
    kernel_path = shadow / "kernel/kernel.sqlite3"
    if _kernel_schema_version(kernel_path) != plan["sourceSchemas"]["kernel"]:
        raise MigrationError("MIGRATION_SOURCE_SCHEMA_CHANGED")
    kernel_counts = _sqlite_counts(kernel_path)
    migrate_database(kernel_path)
    if kernel_counts != _sqlite_counts(kernel_path, tuple(kernel_counts)):
        raise MigrationError("KERNEL_RECORD_COUNT_MISMATCH")

    state = Path(instance["stateRoot"])
    compose = state / "compose.json"
    compose_value = json.loads(compose.read_text())
    kernel_environment = compose_value["services"]["kernel-service"]["environment"]
    data_environment = compose_value["services"]["data-service"]["environment"]
    knowledge_environment = compose_value["services"]["knowledge-next-service"]["environment"]
    project_id = kernel_environment["PLATFORM_PROJECT_ID"]
    data_project = data_environment["DATA_PROJECT"]
    knowledge_project = knowledge_environment["KNOWLEDGE_PROJECT"]
    with _legacy_databases(
        paths, plan["instanceId"], shadow, state, docker, f"migrate-{suffix}"
    ) as (data, knowledge):
        source_versions = _postgres_schema_versions(data, knowledge)
        planned_versions = (
            plan["sourceSchemas"]["data"],
            plan["sourceSchemas"]["knowledge"],
        )
        if source_versions != planned_versions:
            raise MigrationError("MIGRATION_SOURCE_SCHEMA_CHANGED")
        data_counts = _postgres_counts(data, DATA_RETAINED_TABLES)
        knowledge_counts = _postgres_counts(knowledge, KNOWLEDGE_RETAINED_TABLES)
        data_lineage = _postgres_lineage(data, DATA_LINEAGE_QUERIES)
        knowledge_lineage = _postgres_lineage(knowledge, KNOWLEDGE_LINEAGE_QUERIES)
        data.migrate()
        knowledge.migrate()
        subjects = _kernel_subjects(kernel_path) | _postgres_subjects(data, knowledge)
        if subjects != set(plan["legacySubjects"]):
            raise MigrationError("MIGRATION_LEGACY_SUBJECTS_CHANGED")
        missing = sorted(subjects - identities.keys())
        if missing:
            raise MigrationError("IDENTITY_MAP_INCOMPLETE:" + ",".join(missing))
        if data_project != project_id or knowledge_project != project_id:
            raise MigrationError("PROJECT_CONTRACT_MISMATCH")
        mapped = _map_owners(kernel_path, data, knowledge, project_id, identities)
        remaining_legacy = (
            _kernel_subjects(kernel_path) | _postgres_subjects(data, knowledge)
        ).intersection(identities)
        if remaining_legacy:
            raise MigrationError(
                "OWNER_MAPPING_INCOMPLETE:" + ",".join(sorted(remaining_legacy))
            )
        data.check()
        knowledge.check()
        if data_counts != _postgres_counts(data, tuple(data_counts)):
            raise MigrationError("DATA_RECORD_COUNT_MISMATCH")
        if knowledge_counts != _postgres_counts(knowledge, tuple(knowledge_counts)):
            raise MigrationError("KNOWLEDGE_RECORD_COUNT_MISMATCH")
        if data_lineage != _postgres_lineage(data, DATA_LINEAGE_QUERIES):
            raise MigrationError("DATA_LINEAGE_MISMATCH")
        if knowledge_lineage != _postgres_lineage(
            knowledge, KNOWLEDGE_LINEAGE_QUERIES
        ):
            raise MigrationError("KNOWLEDGE_LINEAGE_MISMATCH")
        if object_checksums != _object_checksums(shadow / "data/objects"):
            raise MigrationError("OBJECT_CHECKSUM_MISMATCH")

    _prepare_migration_storage(
        paths, plan["instanceId"], shadow, legacy_compose, docker,
        private=True,
    )

    manifest = {
        **{
            key: value
            for key, value in instance.items()
            if key not in {"releaseId", "backupRoot", "ports", "profile", "schemaVersion"}
        },
        "schemaVersion": "instance-manifest.v4",
        "profile": "research",
        "ports": {"gateway": instance["ports"]["gateway"]},
        "assemblyDigest": sha256_digest((bundle / "assembly-lock.json").read_bytes()),
        "legacyReleaseId": instance["releaseId"],
        "status": "stopped",
    }
    validate_instance_manifest_v4(manifest)
    target_manifest = state / "instance-manifest.v4.json"
    state_backups: list[tuple[Path, Path]] = []
    installed_targets: list[Path] = []
    runtime_attempted = False
    source_data.rename(previous)
    try:
        shadow.rename(source_data)
        for name in (
            "compose.json",
            "nginx.conf",
            "kernel-config.json",
            "executor-config.json",
            "assembly-lock.json",
            "product-ui.json",
            "product-icon.svg",
        ):
            source = bundle / name
            if not source.is_file():
                continue
            target = state / name
            backup = state / f"{name}.v11-{suffix}"
            if target.exists():
                target.replace(backup)
                state_backups.append((target, backup))
            shutil.copy2(source, target)
            target.chmod(0o400)
            installed_targets.append(target)
        research_profile = paths.config / "profiles/research.env"
        if not research_profile.is_file():
            raise MigrationError("RESEARCH_PROFILE_MISSING")
        env_target = state / "instance.env"
        env_backup = state / f"instance.env.v11-{suffix}"
        env_target.replace(env_backup)
        state_backups.append((env_target, env_backup))
        env_target.write_text(
            research_profile.read_text().rstrip()
            + "\n"
            + f"PLATFORM_INSTANCE_DATA_ROOT={source_data}\n"
            + f"PLATFORM_SECRETS_DIR={paths.config / f'instances/{plan['instanceId']}/secrets'}\n"
            + f"PLATFORM_GATEWAY_PORT={manifest['ports']['gateway']}\n"
            + f"PLATFORM_DEPENDENCY_ROOT={child_environment()['PLATFORM_DEPENDENCY_ROOT']}\n",
            encoding="utf-8",
        )
        env_target.chmod(0o600)
        _write_atomic(target_manifest, manifest)
        runtime_attempted = True
        build_environment: dict[str, str] = {}
        for line in env_target.read_text(encoding="utf-8").splitlines():
            name, separator, value = line.partition("=")
            if separator and name == "PLATFORM_DEPENDENCY_ROOT":
                build_environment[name] = value
                break
        started = compose_command(
            docker,
            paths,
            state / "compose.json",
            env_target,
            manifest["composeProject"],
            ["up", "--detach", "--build", "--pull", "missing", "--wait"],
            environment=build_environment,
        )
        if started.returncode:
            raise MigrationError(
                "V12_START_FAILED:" + started.stderr.strip()[-500:]
            )
        try:
            with urlopen(
                f"http://127.0.0.1:{manifest['ports']['gateway']}/api/v12/health",
                timeout=10,
            ) as response:
                health = json.loads(response.read())
            if response.status != 200 or health.get("status") != "UP":
                raise MigrationError("V12_SCIENTIFIC_SMOKE_FAILED")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise MigrationError("V12_SCIENTIFIC_SMOKE_FAILED") from error
        manifest = {**manifest, "status": "active"}
        _write_atomic(target_manifest, manifest)
    except BaseException:
        if runtime_attempted:
            try:
                compose_command(
                    docker,
                    paths,
                    state / "compose.json",
                    state / "instance.env",
                    manifest["composeProject"],
                    ["down", "--remove-orphans"],
                    environment={},
                )
            except Exception:
                pass
        target_manifest.unlink(missing_ok=True)
        for target in installed_targets:
            target.unlink(missing_ok=True)
        for target, backup in reversed(state_backups):
            if backup.exists():
                backup.replace(target)
        if source_data.exists() and not shadow.exists():
            source_data.rename(shadow)
        if previous.exists() and not source_data.exists():
            previous.rename(source_data)
        raise
    return {
        "status": "PASS",
        "instanceId": plan["instanceId"],
        "snapshot": snapshot["snapshot"],
        "previousData": str(previous),
        "identityMapping": mapped,
        "schemas": plan["targets"],
        "runtimeStatus": "active",
    }
