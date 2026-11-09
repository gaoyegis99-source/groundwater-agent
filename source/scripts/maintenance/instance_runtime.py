"""Plan/apply/receipt lifecycle for XDG-backed Groundwater instances."""

from __future__ import annotations

import json
import os
import pwd
import secrets
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import (
    canonical_bytes,
    sha256_digest,
    validate_instance_manifest_v3,
    validate_instance_manifest_v4,
)
from .daemon_runtime import compose_command, docker_environment, verify_daemon
from .xdg_layout import (
    LifecyclePaths,
    owned_path,
    validate_instance_id,
)


INSTANCE_DIRECTORIES = (
    "kernel",
    "data/database",
    "data/objects",
    "knowledge/database",
    "executor/state",
    "executor/work",
)

STORAGE_INITIALIZERS = (
    ("kernel", "kernel-service", "10001"),
    ("data/database", "data-db", "postgres"),
    ("data/objects", "data-service", "10001"),
    ("knowledge/database", "knowledge-next-db", "postgres"),
    ("executor/state", "executor-control", "10001"),
    ("executor/work", "executor-control", "10001"),
)


class InstanceRefusalError(RuntimeError):
    """An instance operation cannot prove its ownership or requested identity."""


InstanceRefusal = InstanceRefusalError


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _write_new(path: Path, value: dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _atomic_write(path: Path, content: bytes, mode: int = 0o600) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _plan(value: dict[str, Any]) -> dict[str, Any]:
    return value | {"confirmationDigest": sha256_digest(canonical_bytes(value))}


def _read_subid(path: Path, user: str) -> list[tuple[int, int]]:
    ranges = []
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return ranges
    for line in lines:
        fields = line.split(":")
        if len(fields) != 3 or fields[0] != user:
            continue
        try:
            ranges.append((int(fields[1]), int(fields[2])))
        except ValueError:
            continue
    return ranges


def rootless_mapping_status(user: str | None = None) -> dict[str, Any]:
    account = user or pwd.getpwuid(os.getuid()).pw_name
    uid_ranges = _read_subid(Path("/etc/subuid"), account)
    gid_ranges = _read_subid(Path("/etc/subgid"), account)
    valid = any(size >= 65536 for _, size in uid_ranges) and any(
        size >= 65536 for _, size in gid_ranges
    )
    return {
        "account": account,
        "status": "PASS" if valid else "MISSING",
        "subuid": [{"start": start, "count": count} for start, count in uid_ranges],
        "subgid": [{"start": start, "count": count} for start, count in gid_ranges],
    }


def _assert_tree_safe(path: Path, allowed: Path) -> None:
    owned_path(path, allowed)
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise InstanceRefusal("INSTANCE_PATH_INVALID")
    if path.exists():
        try:
            for child in path.rglob("*"):
                if child.is_symlink():
                    raise InstanceRefusal("INSTANCE_SYMLINK_REJECTED")
        except PermissionError:
            # Rootless container users own authoritative leaves. Deletion never
            # follows links and reclaims those leaves inside the same userns.
            pass
    mountinfo = Path("/proc/self/mountinfo")
    if mountinfo.is_file():
        prefix = str(path.absolute())
        for line in mountinfo.read_text(errors="replace").splitlines():
            fields = line.split()
            if len(fields) > 4:
                mount = fields[4].replace("\\040", " ")
                if mount == prefix or mount.startswith(prefix + "/"):
                    raise InstanceRefusal("INSTANCE_MOUNT_BOUNDARY_REJECTED")


def read_manifest(
    paths: LifecyclePaths, instance_id: str
) -> tuple[Path, dict[str, Any]]:
    state = paths.instance_state(validate_instance_id(instance_id))
    path = state / "instance-manifest.v4.json"
    legacy = False
    if not path.exists():
        path = state / "instance-manifest.v3.json"
        legacy = True
    if path.is_symlink():
        raise InstanceRefusal("INSTANCE_MANIFEST_INVALID")
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise InstanceRefusal("INSTANCE_MANIFEST_INVALID") from error
    (validate_instance_manifest_v3 if legacy else validate_instance_manifest_v4)(value)
    if value["stateRoot"] != str(state) or value["dataRoot"] != str(
        paths.instance_data(instance_id)
    ):
        raise InstanceRefusal("INSTANCE_MANIFEST_PATH_MISMATCH")
    return path, value


def _update_status(path: Path, manifest: dict[str, Any], status: str) -> None:
    value = dict(manifest)
    value["status"] = status
    schema = manifest.get("schemaVersion")
    if schema == "instance-manifest.v3":
        validate_instance_manifest_v3(value)
    elif schema == "instance-manifest.v4":
        validate_instance_manifest_v4(value)
    else:
        raise InstanceRefusal("INSTANCE_MANIFEST_INVALID")
    _atomic_write(path, canonical_bytes(value))


def _runtime_compose(paths: LifecyclePaths, instance_id: str) -> dict[str, Any]:
    compose_path = paths.instance_state(instance_id) / "compose.json"
    if compose_path.is_symlink() or not compose_path.is_file():
        raise InstanceRefusal("INSTANCE_COMPOSE_INVALID")
    try:
        compose = json.loads(compose_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise InstanceRefusal("INSTANCE_COMPOSE_INVALID") from error
    if not isinstance(compose.get("services"), dict):
        raise InstanceRefusal("INSTANCE_COMPOSE_INVALID")
    return compose


def storage_initialization_plan(
    paths: LifecyclePaths, instance_id: str
) -> list[dict[str, str]]:
    """Return exact bind roots and signed images used for rootless ownership."""

    instance_id = validate_instance_id(instance_id)
    root = paths.instance_data(instance_id)
    compose = _runtime_compose(paths, instance_id)
    result = []
    for relative, service, owner in STORAGE_INITIALIZERS:
        target = owned_path(relative, root)
        definition = compose["services"].get(service)
        image = definition.get("image") if isinstance(definition, dict) else None
        if (
            target.is_symlink()
            or not target.is_dir()
            or not isinstance(image, str)
            or not image.startswith("groundwater-v6/")
        ):
            raise InstanceRefusal("INSTANCE_STORAGE_INITIALIZER_INVALID")
        result.append(
            {
                "relativePath": relative,
                "path": str(target),
                "service": service,
                "image": image,
                "owner": owner,
            }
        )
    return result


def prepare_storage(
    paths: LifecyclePaths, instance_id: str, *, docker: Path
) -> dict[str, Any]:
    """Map bind roots to their container users through the owned rootless daemon."""

    verify_daemon(paths, docker)
    entries = storage_initialization_plan(paths, instance_id)
    script = (
        "set -eu; owner=$1; "
        'case $owner in *[!0-9]*) uid=$(id -u "$owner");; *) uid=$owner;; esac; '
        'chown -R "$uid" /target; chmod -R u+rwX,go-rwx /target'
    )
    for entry in entries:
        completed = subprocess.run(
            [
                str(docker),
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "0",
                "--entrypoint",
                "/bin/sh",
                "--mount",
                f"type=bind,src={entry['path']},dst=/target",
                entry["image"],
                "-c",
                script,
                "--",
                entry["owner"],
            ],
            env=docker_environment(paths),
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        if completed.returncode:
            raise InstanceRefusal(
                "INSTANCE_STORAGE_PREPARE_FAILED:" + entry["relativePath"]
            )
    receipt = {
        "schemaVersion": "instance-storage-receipt.v1",
        "action": "instance-storage-prepare",
        "status": "PASS",
        "instanceId": instance_id,
        "completedAt": _now(),
        "entries": entries,
    }
    audit = owned_path(Path("instances") / instance_id, paths.audit)
    _write_new(audit / f"storage-{secrets.token_hex(8)}.json", receipt)
    return receipt


def stop_instance(
    paths: LifecyclePaths, instance_id: str, *, docker: Path
) -> dict[str, Any]:
    manifest_path, manifest = read_manifest(paths, instance_id)
    if manifest["status"] != "active":
        raise InstanceRefusal("INSTANCE_STOP_STATE_INVALID")
    state = paths.instance_state(instance_id)
    completed = compose_command(
        docker,
        paths,
        state / "compose.json",
        state / "instance.env",
        manifest["composeProject"],
        ["down", "--remove-orphans"],
    )
    if completed.returncode:
        raise InstanceRefusal("INSTANCE_STOP_FAILED:" + completed.stderr.strip()[-500:])
    _update_status(manifest_path, manifest, "stopped")
    return {"status": "PASS", "instanceId": instance_id}


def destroy_plan(paths: LifecyclePaths, instance_id: str) -> dict[str, Any]:
    manifest_path, manifest = read_manifest(paths, instance_id)
    if manifest["status"] == "active":
        raise InstanceRefusal("INSTANCE_STILL_ACTIVE")
    data = paths.instance_data(instance_id)
    state = paths.instance_state(instance_id)
    config = owned_path(Path("instances") / instance_id, paths.config)
    for path, allowed in (
        (data, paths.instances),
        (state, paths.state),
        (config, paths.config),
    ):
        _assert_tree_safe(path, allowed)
    body = {
        "schemaVersion": "operation-plan.v1",
        "action": "instance-destroy",
        "instanceId": instance_id,
        "assemblyDigest": manifest["assemblyDigest"],
        "manifestSha256": sha256_digest(manifest_path.read_bytes()),
        "paths": [str(data), str(state), str(config)],
        "storageOwners": {
            relative: (data / relative).stat().st_uid
            for relative in INSTANCE_DIRECTORIES
        },
    }
    return _plan(body)


def _reclaim_storage_ownership(
    paths: LifecyclePaths, instance_id: str, *, docker: Path
) -> None:
    verify_daemon(paths, docker)
    script = (
        "set -eu; find /target -mindepth 1 -xdev -delete; "
        "chown 0 /target; chmod 0700 /target"
    )
    for entry in storage_initialization_plan(paths, instance_id):
        completed = subprocess.run(
            [
                str(docker),
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "0",
                "--entrypoint",
                "/bin/sh",
                "--mount",
                f"type=bind,src={entry['path']},dst=/target",
                entry["image"],
                "-c",
                script,
            ],
            env=docker_environment(paths),
            capture_output=True,
            text=True,
            check=False,
            timeout=1800,
        )
        if completed.returncode:
            raise InstanceRefusal(
                "INSTANCE_STORAGE_RECLAIM_FAILED:" + entry["relativePath"]
            )


def apply_destroy(
    paths: LifecyclePaths,
    plan: dict[str, Any],
    confirmation: str,
    *,
    docker: Path | None = None,
) -> dict[str, Any]:
    expected = plan.get("confirmationDigest")
    body = {key: value for key, value in plan.items() if key != "confirmationDigest"}
    if expected != sha256_digest(canonical_bytes(body)) or confirmation != expected:
        raise InstanceRefusal("INSTANCE_CONFIRMATION_MISMATCH")
    instance_id = validate_instance_id(plan["instanceId"])
    manifest_path, manifest = read_manifest(paths, instance_id)
    if manifest["status"] == "active":
        raise InstanceRefusal("INSTANCE_STILL_ACTIVE")
    if sha256_digest(manifest_path.read_bytes()) != plan["manifestSha256"]:
        raise InstanceRefusal("INSTANCE_CHANGED_AFTER_PLAN")
    data = paths.instance_data(instance_id)
    state = paths.instance_state(instance_id)
    config = owned_path(Path("instances") / instance_id, paths.config)
    targets = [data, state, config]
    if plan["paths"] != [str(path) for path in targets]:
        raise InstanceRefusal("INSTANCE_PLAN_PATH_MISMATCH")
    current_owners = {
        relative: (data / relative).stat().st_uid for relative in INSTANCE_DIRECTORIES
    }
    if plan.get("storageOwners") != current_owners:
        raise InstanceRefusal("INSTANCE_STORAGE_CHANGED_AFTER_PLAN")
    for path, allowed in (
        (data, paths.instances),
        (state, paths.state),
        (config, paths.config),
    ):
        _assert_tree_safe(path, allowed)
    if any(owner != os.getuid() for owner in current_owners.values()):
        if docker is None:
            raise InstanceRefusal("INSTANCE_DOCKER_REQUIRED_FOR_ROOTLESS_STORAGE")
        _reclaim_storage_ownership(paths, instance_id, docker=docker)
    receipt = {
        "schemaVersion": "operation-receipt.v1",
        "action": "instance-destroy",
        "status": "PASS",
        "instanceId": instance_id,
        "assemblyDigest": manifest["assemblyDigest"],
        "confirmationDigest": expected,
        "completedAt": _now(),
        "deletedPaths": [str(path) for path in targets if path.exists()],
    }
    audit = owned_path(Path("instances") / instance_id, paths.audit)
    _write_new(audit / f"destroy-{expected.removeprefix('sha256:')[:16]}.json", receipt)
    for path in targets:
        if path.exists():
            shutil.rmtree(path)
    return receipt
