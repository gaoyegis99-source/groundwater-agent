#!/usr/bin/env python3
"""Build a populated V11 fixture and execute the complete V12 shadow migration."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
from typing import Any
from urllib.request import urlopen

from scripts.maintenance.contracts import canonical_bytes, sha256_digest
from scripts.maintenance.instance_runtime import (
    INSTANCE_DIRECTORIES,
    stop_instance,
)
from scripts.maintenance.migration_runtime import (
    DATA_LINEAGE_QUERIES,
    KNOWLEDGE_LINEAGE_QUERIES,
    _ensure_service_imports,
    _legacy_databases,
    _postgres_lineage,
    _postgres_schema_versions,
    _postgres_subjects,
    _prepare_migration_storage,
    apply_plan,
    create_plan,
)
from scripts.maintenance.project_environment import child_environment
from scripts.maintenance.research_artifacts import verify
from scripts.maintenance.xdg_layout import (
    LifecyclePaths,
    ensure_private_roots,
    resolve_lifecycle_paths,
)


ROOT = Path(__file__).resolve().parents[2]
DOCKER = ROOT.parent / "bin/docker"
PROJECT_ID = "groundwater-demo"
LEGACY_SUBJECTS = {"legacy:alice", "legacy:bob"}


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _isolated_paths(root: Path) -> LifecyclePaths:
    base = resolve_lifecycle_paths()
    isolated = resolve_lifecycle_paths(
        {
            "HOME": str(root / "home"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_DATA_HOME": str(root / "data"),
            "XDG_STATE_HOME": str(root / "state"),
            "XDG_CACHE_HOME": str(root / "cache"),
            "XDG_RUNTIME_DIR": str(base.runtime_home),
        }
    )
    # Persist all fixture state below the temporary root while using the
    # already verified project-owned rootless daemon and dependency cache.
    return replace(
        isolated,
        runtime_home=base.runtime_home,
        runtime=base.runtime,
        socket=base.socket,
        docker=base.docker,
    )


def _copy_runtime_configuration(paths: LifecyclePaths, instance_id: str) -> Path:
    source_paths = resolve_lifecycle_paths()
    profile_source = source_paths.config / "profiles/research.env"
    secret_source = source_paths.config / "secrets/research"
    if not profile_source.is_file() or not secret_source.is_dir():
        raise RuntimeError("REHEARSAL_RESEARCH_CONFIGURATION_MISSING")
    profile = paths.config / "profiles/research.env"
    profile.parent.mkdir(parents=True, mode=0o700)
    shutil.copyfile(profile_source, profile)
    profile.chmod(0o600)
    destination = paths.config / f"instances/{instance_id}/secrets"
    destination.mkdir(parents=True, mode=0o700)
    for name in (
        "agent-api-key",
        "intent-router-api-key",
        "embedding-api-key",
        "data-db-password",
        "knowledge-db-password",
    ):
        source = secret_source / name
        value = source.read_bytes() if source.is_file() else b"rehearsal-only\n"
        target = destination / name
        target.write_bytes(value)
        target.chmod(0o444)
    return destination


def _legacy_compose(bundle: Path) -> dict[str, Any]:
    compose = json.loads((bundle / "compose.json").read_text())
    python_image = json.loads((ROOT / "system-deps.lock.json").read_text())[
        "baseImages"
    ]["python"]
    # V11 release Compose carried immutable images.  For the ownership helper
    # only /bin/sh and chown are needed, so locked base images are sufficient.
    for service in ("kernel-service", "data-service", "executor-control"):
        compose["services"][service]["image"] = python_image
    return compose


def _create_kernel_v33(path: Path) -> None:
    _ensure_service_imports()
    from platform_kernel.database import (
        V33_SCHEMA_DIGEST,
        _schema_digest,
        _schema_signature,
        initialize_database,
    )

    initialize_database(path)
    with sqlite3.connect(path) as connection:
        for table in (
            "research_api_tokens",
            "research_sessions",
            "research_project_memberships",
            "research_users",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("UPDATE schema_version SET version=33")
        connection.execute(
            "INSERT INTO sessions(session_id,project_id,goal_revision,owner_subject) "
            "VALUES ('legacy-session',?,0,'legacy:alice')",
            (PROJECT_ID,),
        )
        connection.execute(
            "INSERT INTO confirmations(confirmation_id,session_id,goal_revision,"
            "agent_run_id,status,decision_subject) VALUES "
            "('legacy-confirmation','legacy-session',0,'legacy-run','APPROVED','legacy:bob')"
        )
        signature = _schema_digest(_schema_signature(connection))
    if signature != V33_SCHEMA_DIGEST:
        raise RuntimeError("REHEARSAL_KERNEL_V33_SIGNATURE_MISMATCH")


def _initialize_legacy_postgres(
    paths: LifecyclePaths,
    instance_id: str,
    root: Path,
    state: Path,
) -> tuple[dict[str, str], dict[str, str]]:
    _ensure_service_imports()
    from importlib.resources import files
    from platform_knowledge_next.config import MODEL_PROFILE

    with _legacy_databases(
        paths, instance_id, root, state, DOCKER, "fixture"
    ) as (data, knowledge):
        with data.transaction() as connection:
            connection.execute(
                files("platform_data_service").joinpath("migrations/001.sql").read_text()
            )
            connection.execute(
                "INSERT INTO publications(id,project,owner_subject,publication_kind,"
                "idem,digest,manifest,state) VALUES "
                "('pub',%s,'legacy:alice','MANAGED','idem','sha256:pub','{}','PUBLISHED')",
                (PROJECT_ID,),
            )
            connection.execute(
                "INSERT INTO datasets(id,project,title,tags) VALUES "
                "('dataset',%s,'Legacy dataset','[]')",
                (PROJECT_ID,),
            )
            for version in ("version-1", "version-2"):
                connection.execute(
                    "INSERT INTO versions(id,dataset_id,publication_id,descriptor) "
                    "VALUES (%s,'dataset','pub','{}')",
                    (version,),
                )
                connection.execute(
                    "INSERT INTO objects(id,version_id,descriptor) VALUES "
                    "(%s,%s,'{}')",
                    ("object-" + version[-1], version),
                )
            connection.execute(
                "UPDATE datasets SET current_version='version-2' WHERE id='dataset'"
            )
            connection.execute(
                "INSERT INTO lineage(version_id,input_version_id) "
                "VALUES ('version-2','version-1')"
            )
            for name in ("002.sql", "003.sql", "004.sql"):
                connection.execute(
                    files("platform_data_service").joinpath("migrations", name).read_text()
                )

        with knowledge.transaction() as connection:
            connection.execute(
                files("platform_knowledge_next").joinpath("migrations/001.sql").read_text()
            )
            connection.execute(
                "INSERT INTO meta(schema_version,project,profile) VALUES(4,%s,%s)",
                (PROJECT_ID, MODEL_PROFILE),
            )
            connection.execute(
                "INSERT INTO imports(id,idempotency_key,digest,payload,actor,state) "
                "VALUES ('import','idem','sha256:import','{}',"
                "'{\"subject\":\"legacy:bob\"}','SUCCEEDED')"
            )
            connection.execute("INSERT INTO documents(id) VALUES ('document')")
            connection.execute(
                "INSERT INTO versions(id,document_id,body,checksum,import_id) VALUES "
                "('knowledge-version','document','{}','sha256:knowledge','import')"
            )
            connection.execute(
                "UPDATE documents SET current_version='knowledge-version' "
                "WHERE id='document'"
            )
            connection.execute(
                "INSERT INTO lifecycle(version_id,revision,status,review_after,reason,actor) "
                "VALUES ('knowledge-version',1,'ACTIVE',CURRENT_DATE,'fixture','{}')"
            )
            connection.execute(
                "INSERT INTO units(version_id,unit_id,ordinal,checksum,body) VALUES "
                "('knowledge-version','unit',0,'sha256:unit','{}')"
            )
            connection.execute(
                files("platform_knowledge_next").joinpath("migrations/002.sql").read_text()
            )
            connection.execute("UPDATE meta SET schema_version=5 WHERE singleton=true")
            connection.execute(
                files("platform_knowledge_next").joinpath("migrations/003.sql").read_text()
            )
            connection.execute("UPDATE meta SET schema_version=6 WHERE singleton=true")

        if _postgres_schema_versions(data, knowledge) != (7, 6):
            raise RuntimeError("REHEARSAL_LEGACY_SCHEMA_MISMATCH")
        if _postgres_subjects(data, knowledge) != LEGACY_SUBJECTS:
            raise RuntimeError("REHEARSAL_LEGACY_OWNER_MISMATCH")
        return (
            _postgres_lineage(data, DATA_LINEAGE_QUERIES),
            _postgres_lineage(knowledge, KNOWLEDGE_LINEAGE_QUERIES),
        )


def _write_fixture(paths: LifecyclePaths, instance_id: str, bundle: Path) -> dict[str, Any]:
    ensure_private_roots(paths, ("config", "data", "state", "cache"))
    secrets = _copy_runtime_configuration(paths, instance_id)
    data = paths.instance_data(instance_id)
    state = paths.instance_state(instance_id)
    data.mkdir(parents=True, mode=0o700)
    data.chmod(0o700)
    for relative in INSTANCE_DIRECTORIES:
        (data / relative).mkdir(parents=True, exist_ok=True, mode=0o700)
    state.mkdir(parents=True, mode=0o700)
    state.chmod(0o700)
    _create_kernel_v33(data / "kernel/kernel.sqlite3")
    (data / "data/objects/legacy-object.bin").write_bytes(b"legacy-object\n")
    compose = _legacy_compose(bundle)
    (state / "compose.json").write_bytes(canonical_bytes(compose))
    dependency_root = child_environment()["PLATFORM_DEPENDENCY_ROOT"]
    profile = (paths.config / "profiles/research.env").read_text().rstrip()
    (state / "instance.env").write_text(
        profile
        + "\n"
        + f"PLATFORM_INSTANCE_DATA_ROOT={data}\n"
        f"PLATFORM_SECRETS_DIR={secrets}\n"
        "PLATFORM_GATEWAY_PORT=28196\n"
        "PLATFORM_OPERATOR_PORT=28197\n"
        f"PLATFORM_DEPENDENCY_ROOT={dependency_root}\n"
    )
    manifest = {
        "schemaVersion": "instance-manifest.v3",
        "instanceId": instance_id,
        "releaseId": "sha256-" + "a" * 64,
        "profile": "development",
        "composeProject": f"groundwater-v6-{instance_id}",
        "dataRoot": str(data),
        "stateRoot": str(state),
        "ports": {"gateway": 28196, "operator": 28197},
        "backupRoot": None,
        "createdAt": _now(),
        "status": "stopped",
    }
    (state / "instance-manifest.v3.json").write_bytes(canonical_bytes(manifest))
    # Make bind roots usable by the rootless database containers, initialize
    # authentic v7/v6 stores, and leave the stopped fixture readable so the
    # snapshot stage can prove every source byte before the shadow copy.
    _prepare_migration_storage(paths, instance_id, data, compose, DOCKER, private=False)
    data_lineage, knowledge_lineage = _initialize_legacy_postgres(
        paths, instance_id, data, state
    )
    mapping = paths.state / "identity-map.json"
    mapping.write_bytes(
        canonical_bytes(
            {
                "users": [
                    {
                        "legacySubject": "legacy:alice",
                        "username": "alice",
                        "displayName": "Alice",
                        "role": "admin",
                        "initialPassword": "test-password-123",
                    },
                    {
                        "legacySubject": "legacy:bob",
                        "username": "bob",
                        "displayName": "Bob",
                        "role": "researcher",
                        "initialPassword": "test-password-123",
                    },
                ]
            }
        )
    )
    mapping.chmod(0o600)
    return {
        "mapping": mapping,
        "dataLineage": data_lineage,
        "knowledgeLineage": knowledge_lineage,
        "objectChecksum": sha256_digest(
            (data / "data/objects/legacy-object.bin").read_bytes()
        ),
    }


def run(output: Path) -> dict[str, Any]:
    if output.resolve().is_relative_to(ROOT):
        raise RuntimeError("REHEARSAL_REPORT_MUST_BE_OUTSIDE_SOURCE")
    _ensure_service_imports()
    from assembler import compile_product

    bundle = compile_product(ROOT, ROOT / "products/groundwater/product-manifest.json")
    instance_id = "migration-rehearsal"
    with tempfile.TemporaryDirectory(
        prefix="gw-v12-migration-rehearsal-", ignore_cleanup_errors=True
    ) as temporary:
        paths = _isolated_paths(Path(temporary))
        try:
            evidence = _write_fixture(paths, instance_id, bundle)
            planned = create_plan(paths, instance_id, evidence["mapping"], docker=DOCKER)
            result = apply_plan(
                paths,
                Path(planned["plan"]),
                planned["confirmationDigest"],
                docker=DOCKER,
                source_root=ROOT,
            )
            snapshot = Path(result["snapshot"])
            verify(snapshot, "research-snapshot.v1")
            with urlopen("http://127.0.0.1:28196/api/v12/health", timeout=10) as response:
                health = json.loads(response.read())
            if response.status != 200 or health.get("status") != "UP":
                raise RuntimeError("REHEARSAL_V12_HEALTH_FAILED")
            stop_instance(paths, instance_id, docker=DOCKER)

            current = paths.instance_data(instance_id)
            previous = Path(result["previousData"])
            legacy_compose = _legacy_compose(bundle)
            _prepare_migration_storage(
                paths, instance_id, current, legacy_compose, DOCKER, private=False
            )
            _prepare_migration_storage(
                paths, instance_id, previous, legacy_compose, DOCKER, private=False
            )
            with sqlite3.connect(current / "kernel/kernel.sqlite3") as connection:
                kernel_version = connection.execute(
                    "SELECT version FROM schema_version"
                ).fetchone()[0]
                owners = {
                    row[0] for row in connection.execute("SELECT owner_subject FROM sessions")
                }
            with sqlite3.connect(previous / "kernel/kernel.sqlite3") as connection:
                previous_kernel_version = connection.execute(
                    "SELECT version FROM schema_version"
                ).fetchone()[0]
                previous_owners = {
                    row[0] for row in connection.execute("SELECT owner_subject FROM sessions")
                }
            with _legacy_databases(
                paths, instance_id, current, paths.instance_state(instance_id), DOCKER,
                "verify",
            ) as (data, knowledge):
                schemas = _postgres_schema_versions(data, knowledge)
                mapped_subjects = _postgres_subjects(data, knowledge)
                data_lineage = _postgres_lineage(data, DATA_LINEAGE_QUERIES)
                knowledge_lineage = _postgres_lineage(
                    knowledge, KNOWLEDGE_LINEAGE_QUERIES
                )
            if (
                kernel_version != 34
                or previous_kernel_version != 33
                or previous_owners != {"legacy:alice"}
                or owners & LEGACY_SUBJECTS
                or schemas != (8, 7)
                or mapped_subjects & LEGACY_SUBJECTS
                or data_lineage != evidence["dataLineage"]
                or knowledge_lineage != evidence["knowledgeLineage"]
                or sha256_digest(
                    (current / "data/objects/legacy-object.bin").read_bytes()
                )
                != evidence["objectChecksum"]
                or sha256_digest(
                    (previous / "data/objects/legacy-object.bin").read_bytes()
                )
                != evidence["objectChecksum"]
            ):
                raise RuntimeError("REHEARSAL_POST_MIGRATION_EVIDENCE_MISMATCH")
            report = {
                "schemaVersion": "migration-rehearsal-report.v1",
                "status": "PASS",
                "instanceId": instance_id,
                "sourceSchemas": planned["sourceSchemas"],
                "targetSchemas": result["schemas"],
                "snapshotSha256": sha256_digest(snapshot.read_bytes()),
                "shadowSwitch": "PASS",
                "previousStorageRetained": "PASS",
                "identityMapping": "PASS",
                "recordAndLineagePreservation": "PASS",
                "objectChecksumPreservation": "PASS",
                "v12RuntimeSmoke": "PASS",
                "completedAt": _now(),
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(canonical_bytes(report))
            return report
        finally:
            subprocess.run(
                [
                    str(DOCKER), "run", "--rm", "--network", "none", "--user", "0",
                    "--entrypoint", "/bin/sh", "--mount",
                    f"type=bind,src={temporary},dst=/target",
                    json.loads((ROOT / "system-deps.lock.json").read_text())[
                        "baseImages"
                    ]["python"],
                    "-c", "chmod -R a+rwX /target",
                ],
                capture_output=True,
                check=False,
                timeout=120,
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
