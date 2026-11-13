"""Checksum-addressed V12 research archives and stopped-instance snapshots."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
import json
import os
import platform
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from typing import Any

from scripts.checks.check_research_acceptance import evaluate

from .contracts import canonical_bytes, sha256_digest
from .instance_runtime import read_manifest
from .project_environment import docker_executable
from .xdg_layout import LifecyclePaths, owned_path, validate_instance_id


class ResearchArtifactError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    )


def _manifest(root: Path, schema: str, metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": schema,
        **metadata,
        "files": [
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": sha256_digest(path.read_bytes()),
                "sizeBytes": path.stat().st_size,
            }
            for path in _files(root)
            if path.name != "manifest.json"
        ],
    }


def _pack(root: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise ResearchArtifactError("ARTIFACT_ALREADY_EXISTS")
    with tarfile.open(destination, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for path in _files(root):
            info = archive.gettarinfo(str(path), path.relative_to(root).as_posix())
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            with path.open("rb") as stream:
                archive.addfile(info, stream)


def _json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _checksums(value: Any) -> list[str]:
    found: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, str) and item.startswith("sha256:"):
            found.add(item)

    visit(value)
    return sorted(found)


def _seeds(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if "seed" in key.lower() and isinstance(item, (int, str))
    }


def _sensitive_source_path(path: Path) -> bool:
    lowered = tuple(part.lower() for part in path.parts)
    return (
        any(
            part in {"secrets", "credentials"}
            or part == ".env"
            or part.startswith(".env.")
            for part in lowered
        )
        or path.name.lower() in {"id_rsa", "id_ed25519"}
        or path.suffix.lower() in {".key", ".pem", ".p12", ".pfx", ".jks"}
    )


def _source_pathspecs() -> list[str]:
    """Return one closed set of secret-bearing paths excluded from Git payloads."""

    return [
        ".",
        ":(exclude,glob)**/.env",
        ":(exclude,glob)**/.env.*",
        ":(exclude,glob)**/secrets/**",
        ":(exclude,glob)**/credentials/**",
        ":(exclude,glob)**/*.key",
        ":(exclude,glob)**/*.pem",
        ":(exclude,glob)**/*.p12",
        ":(exclude,glob)**/*.pfx",
        ":(exclude,glob)**/*.jks",
        ":(exclude,glob)**/id_rsa",
        ":(exclude,glob)**/id_ed25519",
    ]


def _safe_extract(artifact: Path, destination: Path) -> None:
    with tarfile.open(artifact, "r:gz") as archive:
        for member in archive.getmembers():
            target = destination / member.name
            if member.issym() or member.islnk() or not target.absolute().is_relative_to(destination.absolute()):
                raise ResearchArtifactError("ARTIFACT_MEMBER_INVALID")
        archive.extractall(destination, filter="data")


def verify(artifact: Path, expected_schema: str) -> dict[str, Any]:
    if artifact.is_symlink() or not artifact.is_file():
        raise ResearchArtifactError("ARTIFACT_MISSING")
    with tempfile.TemporaryDirectory(prefix="gw-artifact-verify-") as temporary:
        root = Path(temporary)
        _safe_extract(artifact, root)
        try:
            manifest = json.loads((root / "manifest.json").read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise ResearchArtifactError("ARTIFACT_MANIFEST_INVALID") from error
        if manifest.get("schemaVersion") != expected_schema:
            raise ResearchArtifactError("ARTIFACT_SCHEMA_INVALID")
        expected = {item["path"]: item for item in manifest.get("files", [])}
        actual = {
            path.relative_to(root).as_posix(): {
                "sha256": sha256_digest(path.read_bytes()), "sizeBytes": path.stat().st_size
            }
            for path in _files(root) if path.name != "manifest.json"
        }
        if set(expected) != set(actual) or any(
            expected[name]["sha256"] != value["sha256"]
            or expected[name]["sizeBytes"] != value["sizeBytes"]
            for name, value in actual.items()
        ):
            raise ResearchArtifactError("ARTIFACT_CHECKSUM_MISMATCH")
    return {"status": "PASS", "schemaVersion": expected_schema, "artifact": str(artifact)}


@contextmanager
def _stopped_kernel_inspection(database: Path) -> Iterator[sqlite3.Connection]:
    # Even a read-only WAL connection can create -wal and -shm. Inspect a
    # detached copy of the stopped store, including its committed journal.
    with tempfile.TemporaryDirectory(prefix="gw-kernel-inspection-") as temporary:
        probe = Path(temporary) / database.name
        shutil.copyfile(database, probe)
        journal = database.with_name(database.name + "-wal")
        if journal.is_file():
            shutil.copyfile(journal, probe.with_name(probe.name + "-wal"))
        with closing(sqlite3.connect(probe.as_uri() + "?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            yield connection


_READ_WORKFLOW = """
from contextlib import closing
import json
import sqlite3
import sys
with closing(sqlite3.connect('file:/data/kernel.sqlite3?mode=ro', uri=True)) as connection:
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        'SELECT * FROM workflow_runs WHERE run_id=?', (sys.stdin.read(),)
    ).fetchone()
print(json.dumps(None if row is None else dict(row)))
"""


def _read_workflow_evidence(instance: dict[str, Any], run_id: str) -> dict[str, Any] | None:
    database = Path(instance["dataRoot"]) / "kernel/kernel.sqlite3"
    try:
        if instance["status"] == "active":
            docker = docker_executable()
            ids = subprocess.check_output(
                [docker, "ps", "-q", "--no-trunc",
                 "--filter", f"label=com.docker.compose.project={instance['composeProject']}",
                 "--filter", "label=com.docker.compose.service=kernel-service"],
                text=True, stderr=subprocess.PIPE, timeout=10,
            ).splitlines()
            if len(ids) != 1:
                raise ResearchArtifactError("ARCHIVE_KERNEL_CONTAINER_NOT_UNIQUE")
            container = ids[0]
            mount = subprocess.check_output(
                [docker, "inspect", "--format",
                 '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}', container],
                text=True, stderr=subprocess.PIPE, timeout=10,
            ).strip()
            if not mount or Path(mount).resolve() != database.parent.resolve():
                raise ResearchArtifactError("ARCHIVE_KERNEL_DATA_MOUNT_MISMATCH")
            raw = subprocess.check_output(
                [docker, "exec", "-i", container, "python", "-c", _READ_WORKFLOW],
                input=run_id, text=True, stderr=subprocess.PIPE, timeout=40,
            )
            return json.loads(raw)
        if not database.is_file():
            return None
        with _stopped_kernel_inspection(database) as connection:
            row = connection.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
            return None if row is None else dict(row)
    except (OSError, subprocess.SubprocessError, sqlite3.Error, json.JSONDecodeError) as error:
        raise ResearchArtifactError("ARCHIVE_KERNEL_EVIDENCE_UNREADABLE") from error


def _snapshot_schema_versions(
    instance: dict[str, Any], source_versions: dict[str, int] | None
) -> dict[str, Any]:
    database = Path(instance["dataRoot"]) / "kernel/kernel.sqlite3"
    try:
        with _stopped_kernel_inspection(database) as connection:
            row = connection.execute("SELECT version FROM schema_version").fetchone()
    except (OSError, sqlite3.Error) as error:
        raise ResearchArtifactError("SNAPSHOT_KERNEL_SCHEMA_UNREADABLE") from error
    if row is None or not isinstance(row[0], int):
        raise ResearchArtifactError("SNAPSHOT_KERNEL_SCHEMA_UNREADABLE")
    kernel_version = row[0]
    manifest_version = instance["schemaVersion"]
    if manifest_version == "instance-manifest.v4":
        versions: dict[str, Any] = {
            "publicApi": "v12",
            "kernel": 34,
            "data": 8,
            "knowledge": 7,
            "manifest": 4,
        }
    elif manifest_version == "instance-manifest.v3" and source_versions is not None:
        if set(source_versions) != {"kernel", "data", "knowledge"}:
            raise ResearchArtifactError("SNAPSHOT_SCHEMA_VERSIONS_INVALID")
        versions = {
            "publicApi": "v11",
            **source_versions,
            "manifest": 3,
        }
    else:
        raise ResearchArtifactError("SNAPSHOT_SCHEMA_VERSIONS_REQUIRED")
    if kernel_version != versions["kernel"]:
        raise ResearchArtifactError("SNAPSHOT_KERNEL_SCHEMA_MISMATCH")
    return versions


def create_snapshot(
    paths: LifecyclePaths,
    instance_id: str,
    *,
    source_versions: dict[str, int] | None = None,
) -> dict[str, Any]:
    _path, instance = read_manifest(paths, validate_instance_id(instance_id))
    if instance["status"] != "stopped":
        raise ResearchArtifactError("SNAPSHOT_REQUIRES_STOPPED_INSTANCE")
    schema_versions = _snapshot_schema_versions(instance, source_versions)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = owned_path(f"snapshots/{instance_id}-{stamp}.tar.gz", paths.state)
    with tempfile.TemporaryDirectory(prefix="gw-snapshot-") as temporary:
        root = Path(temporary)
        shutil.copytree(Path(instance["dataRoot"]), root / "data", symlinks=False)
        state_source = Path(instance["stateRoot"])
        shutil.copytree(state_source, root / "state", symlinks=False)
        (root / "instance.json").write_bytes(canonical_bytes(instance))
        manifest = _manifest(
            root, "research-snapshot.v1",
            {
                "instanceId": instance_id,
                "createdAt": _now(),
                "sourceStatus": "stopped",
                "schemaVersions": schema_versions,
            },
        )
        (root / "manifest.json").write_bytes(canonical_bytes(manifest))
        _pack(root, destination)
    verify(destination, "research-snapshot.v1")
    return {"status": "PASS", "snapshot": str(destination), "instanceId": instance_id}


def restore_plan(paths: LifecyclePaths, snapshot: Path, instance_id: str) -> dict[str, Any]:
    verify(snapshot, "research-snapshot.v1")
    _path, instance = read_manifest(paths, validate_instance_id(instance_id))
    if instance["status"] != "stopped":
        raise ResearchArtifactError("RESTORE_REQUIRES_STOPPED_INSTANCE")
    body = {
        "schemaVersion": "research-restore-plan.v1",
        "snapshot": str(snapshot.absolute()),
        "snapshotSha256": sha256_digest(snapshot.read_bytes()),
        "instanceId": instance_id,
        "dataRoot": instance["dataRoot"],
        "stateRoot": instance["stateRoot"],
    }
    return body | {"confirmationDigest": sha256_digest(canonical_bytes(body))}


def apply_restore(paths: LifecyclePaths, plan: dict[str, Any], confirmation: str) -> dict[str, Any]:
    expected = plan.get("confirmationDigest")
    body = {key: value for key, value in plan.items() if key != "confirmationDigest"}
    if expected != sha256_digest(canonical_bytes(body)) or confirmation != expected:
        raise ResearchArtifactError("RESTORE_CONFIRMATION_MISMATCH")
    snapshot = Path(plan["snapshot"])
    if sha256_digest(snapshot.read_bytes()) != plan["snapshotSha256"]:
        raise ResearchArtifactError("SNAPSHOT_CHANGED")
    _path, instance = read_manifest(paths, plan["instanceId"])
    if (
        instance["status"] != "stopped"
        or instance["dataRoot"] != plan["dataRoot"]
        or instance["stateRoot"] != plan["stateRoot"]
    ):
        raise ResearchArtifactError("RESTORE_TARGET_CHANGED")
    data = Path(plan["dataRoot"])
    state = Path(plan["stateRoot"])
    suffix = expected.removeprefix("sha256:")[:16]
    old_data = data.with_name(f".{data.name}.pre-restore-{suffix}")
    old_state = state.with_name(f".{state.name}.pre-restore-{suffix}")
    if old_data.exists() or old_state.exists():
        raise ResearchArtifactError("RESTORE_QUARANTINE_EXISTS")
    with tempfile.TemporaryDirectory(prefix="gw-snapshot-restore-") as temporary:
        extracted = Path(temporary)
        _safe_extract(snapshot, extracted)
        try:
            data.rename(old_data)
            state.rename(old_state)
            shutil.copytree(extracted / "data", data)
            shutil.copytree(extracted / "state", state)
        except BaseException:
            shutil.rmtree(data, ignore_errors=True)
            shutil.rmtree(state, ignore_errors=True)
            if old_data.exists():
                old_data.rename(data)
            if old_state.exists():
                old_state.rename(state)
            raise
    return {
        "status": "PASS",
        "instanceId": plan["instanceId"],
        "confirmationDigest": expected,
        "recoverablePreviousData": str(old_data),
        "recoverablePreviousState": str(old_state),
    }


def create_archive(
    paths: LifecyclePaths,
    source_root: Path,
    instance_id: str,
    run_id: str,
    level: str,
    assessment: Path | None,
) -> dict[str, Any]:
    if level not in {"experiment", "benchmark"}:
        raise ResearchArtifactError("ARCHIVE_LEVEL_INVALID")
    _path, instance = read_manifest(paths, validate_instance_id(instance_id))
    assessment_value = None
    acceptance_report = None
    if assessment is not None:
        assessment_value = json.loads(assessment.read_text())
        policy = json.loads(
            (source_root / "docs/reference/v12-research-acceptance-policy.v1.json").read_text()
        )
        acceptance_report = evaluate(policy, assessment_value, level)
    if level == "benchmark":
        if acceptance_report is None:
            raise ResearchArtifactError("BENCHMARK_ASSESSMENT_REQUIRED")
        if acceptance_report["archiveStatus"] != "PASS":
            raise ResearchArtifactError("BENCHMARK_GATES_NOT_PASSED")
    destination = owned_path(f"archives/{run_id}.research.tar.gz", paths.state)
    with tempfile.TemporaryDirectory(prefix="gw-research-archive-") as temporary:
        root = Path(temporary)
        subprocess.run(
            [
                "git", "archive", "--format=tar", "HEAD",
                "-o", str(root / "source.tar"), "--", *_source_pathspecs(),
            ],
            cwd=source_root, check=True,
        )
        diff = subprocess.run(
            ["git", "diff", "--binary", "HEAD", "--", *_source_pathspecs()],
            cwd=source_root,
            check=True, capture_output=True,
        ).stdout
        (root / "dirty.patch").write_bytes(diff)
        untracked_output = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=source_root,
            check=True,
            capture_output=True,
        ).stdout
        untracked = [
            Path(os.fsdecode(value))
            for value in untracked_output.split(b"\0")
            if value
        ]
        archived_untracked = 0
        for relative in untracked:
            source = source_root / relative
            if _sensitive_source_path(relative) or not source.is_file():
                continue
            destination_file = root / "untracked" / relative
            destination_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination_file)
            archived_untracked += 1
        for name in ("uv.lock", "package-lock.json", "toolchain.lock.json", "system-deps.lock.json"):
            path = source_root / name
            if path.is_file():
                shutil.copy2(path, root / name)
        for name in ("assembly-lock.json", "kernel-config.json", "executor-config.json", "compose.json"):
            path = Path(instance["stateRoot"]) / name
            if path.is_file():
                shutil.copy2(path, root / name)
        workflow = _read_workflow_evidence(instance, run_id)
        if workflow is None:
            raise ResearchArtifactError("ARCHIVE_RUN_NOT_FOUND")
        parameters = _json(workflow.get("arguments_json"))
        inputs = _json(workflow.get("input_references_json"))
        outputs = _json(workflow.get("outputs_json"))
        evidence = {
            "runId": run_id,
            "status": "FOUND",
            "parameters": parameters,
            "randomSeeds": _seeds(parameters),
            "inputs": inputs,
            "inputChecksums": _checksums(inputs),
            "outputs": outputs,
            "outputChecksums": _checksums(outputs),
            "modelImplementation": {
                key: workflow.get(key)
                for key in (
                    "model_id", "model_version", "model_checksum",
                    "workflow_id", "workflow_version", "workflow_checksum",
                    "execution_service", "executor_id",
                )
            },
            "lineage": {
                key: workflow.get(key)
                for key in (
                    "session_id", "agent_run_id", "confirmation_id",
                    "retry_of_workflow_run_id", "attempt",
                )
            },
            "workflowRun": workflow,
        }
        (root / "run-evidence.json").write_bytes(canonical_bytes(evidence))
        compose = _json((root / "compose.json").read_text()) if (root / "compose.json").is_file() else {}
        images = sorted(
            {
                definition["image"]
                for definition in compose.get("services", {}).values()
                if isinstance(definition, dict) and isinstance(definition.get("image"), str)
            }
        ) if isinstance(compose, dict) else []
        (root / "environment-summary.json").write_bytes(
            canonical_bytes(
                {
                    "platform": platform.platform(),
                    "python": platform.python_version(),
                    "assemblyDigest": instance["assemblyDigest"],
                    "images": images,
                }
            )
        )
        if assessment_value is not None:
            (root / "acceptance.json").write_bytes(canonical_bytes(assessment_value))
            (root / "acceptance-report.json").write_bytes(
                canonical_bytes(acceptance_report)
            )
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=source_root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        manifest = _manifest(
            root, "research-archive.v1",
            {
                "instanceId": instance_id, "runId": run_id, "level": level,
                "createdAt": _now(), "revision": revision,
                "dirty": bool(diff or untracked),
                "untrackedFiles": archived_untracked,
                "excludedSensitiveUntrackedFiles": len(untracked) - archived_untracked,
            },
        )
        (root / "manifest.json").write_bytes(canonical_bytes(manifest))
        _pack(root, destination)
    verify(destination, "research-archive.v1")
    return {"status": "PASS", "archive": str(destination), "runId": run_id, "level": level}
