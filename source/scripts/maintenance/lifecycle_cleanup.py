"""Independent plan/apply/receipt cleanup domains for XDG runtime state."""

from __future__ import annotations

import os
import shutil
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import canonical_bytes, sha256_digest
from .xdg_layout import LifecyclePaths, ensure_private_roots, owned_path


class CleanupRefusalError(RuntimeError):
    """A cleanup target crossed a lifecycle, process, link, or mount boundary."""


CleanupRefusal = CleanupRefusalError


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _mounts() -> set[Path]:
    result: set[Path] = set()
    try:
        lines = Path("/proc/self/mountinfo").read_text(errors="replace").splitlines()
    except OSError:
        return result
    for line in lines:
        fields = line.split()
        if len(fields) > 4:
            result.add(Path(fields[4].replace("\\040", " ")).absolute())
    return result


def _assert_safe_tree(path: Path, allowed: Path) -> None:
    owned_path(path, allowed)
    if path == allowed:
        raise CleanupRefusal("CLEANUP_LIFECYCLE_ROOT_REJECTED")
    if path.is_symlink():
        raise CleanupRefusal("CLEANUP_SYMLINK_REJECTED")
    if path.exists() and not (path.is_dir() or path.is_file()):
        raise CleanupRefusal("CLEANUP_TYPE_REJECTED")
    absolute = path.absolute()
    if any(mount == absolute or mount.is_relative_to(absolute) for mount in _mounts()):
        raise CleanupRefusal("CLEANUP_MOUNT_REJECTED")


def _tree_identity(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "digest": None, "bytes": 0, "files": 0}
    entries = []
    total = 0
    files = 0
    candidates = [path]
    if path.is_dir():
        candidates.extend(sorted(path.rglob("*"), key=lambda item: item.as_posix()))
    for candidate in candidates:
        metadata = candidate.lstat()
        relative = "." if candidate == path else candidate.relative_to(path).as_posix()
        if stat.S_ISLNK(metadata.st_mode):
            kind = "symlink"
            entries.append(
                [
                    relative,
                    kind,
                    stat.S_IMODE(metadata.st_mode),
                    os.readlink(candidate),
                    metadata.st_mtime_ns,
                    metadata.st_ino,
                    metadata.st_dev,
                ]
            )
            continue
        if stat.S_ISDIR(metadata.st_mode):
            kind = "directory"
        elif stat.S_ISREG(metadata.st_mode):
            kind = "file"
            total += metadata.st_size
            files += 1
        else:
            raise CleanupRefusal("CLEANUP_TYPE_REJECTED")
        entries.append(
            [
                relative,
                kind,
                stat.S_IMODE(metadata.st_mode),
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ino,
                metadata.st_dev,
            ]
        )
    return {
        "exists": True,
        "digest": sha256_digest(canonical_bytes(entries)),
        "bytes": total,
        "files": files,
    }


def _plan(action: str, targets: list[tuple[Path, Path]]) -> dict[str, Any]:
    values = []
    for target, allowed in targets:
        _assert_safe_tree(target, allowed)
        values.append({"path": str(target), "identity": _tree_identity(target)})
    body = {"schemaVersion": "operation-plan.v1", "action": action, "targets": values}
    return body | {"confirmationDigest": sha256_digest(canonical_bytes(body))}


def cache_clean_plan(paths: LifecyclePaths) -> dict[str, Any]:
    return _plan(
        "cache-clean",
        [
            (owned_path(name, paths.cache), paths.cache)
            for name in ("build", "uv", "npm", "deps", "toolchains")
        ],
    )


def _docker_processes(data_root: Path) -> list[int]:
    matches = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                errors="replace"
            )
        except OSError:
            continue
        if "dockerd" in command and str(data_root) in command:
            matches.append(int(entry.name))
    return matches


def engine_reset_plan(paths: LifecyclePaths) -> dict[str, Any]:
    if paths.socket.exists() or paths.socket.is_symlink():
        raise CleanupRefusal("ENGINE_SOCKET_STILL_PRESENT")
    processes = _docker_processes(paths.docker)
    if processes:
        raise CleanupRefusal("ENGINE_PROCESS_STILL_RUNNING:" + ",".join(map(str, processes)))
    return _plan("engine-reset", [(paths.docker, paths.cache)])


def _process_uses(target: Path) -> list[int]:
    result = []
    absolute = target.absolute()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            executable = (entry / "exe").resolve()
            cwd = (entry / "cwd").resolve()
        except OSError:
            continue
        if executable.is_relative_to(absolute) or cwd.is_relative_to(absolute):
            result.append(int(entry.name))
    return result


def apply_plan(
    paths: LifecyclePaths, plan: dict[str, Any], confirmation: str
) -> dict[str, Any]:
    expected = plan.get("confirmationDigest")
    body = {key: value for key, value in plan.items() if key != "confirmationDigest"}
    if expected != sha256_digest(canonical_bytes(body)) or confirmation != expected:
        raise CleanupRefusal("CLEANUP_CONFIRMATION_MISMATCH")
    action = plan.get("action")
    if action not in {"cache-clean", "engine-reset"}:
        raise CleanupRefusal("CLEANUP_ACTION_INVALID")
    ensure_private_roots(paths, ("state",))
    if action == "engine-reset":
        if paths.socket.exists() or _docker_processes(paths.docker):
            raise CleanupRefusal("ENGINE_NOT_STOPPED")
    deleted = []
    for item in plan.get("targets", []):
        path = Path(item.get("path", "")).absolute()
        allowed = paths.cache
        _assert_safe_tree(path, allowed)
        if _tree_identity(path) != item.get("identity"):
            raise CleanupRefusal("CLEANUP_TARGET_CHANGED")
        processes = _process_uses(path)
        if processes:
            raise CleanupRefusal("CLEANUP_TARGET_IN_USE:" + ",".join(map(str, processes)))
    receipt = {
        "schemaVersion": "operation-receipt.v1",
        "action": action,
        "status": "PASS",
        "confirmationDigest": expected,
        "completedAt": _now(),
        "deleted": [
            item
            for item in plan.get("targets", [])
            if item.get("identity", {}).get("exists")
        ],
    }
    audit = owned_path(Path("cleanup") / action, paths.audit)
    audit.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = expected.removeprefix("sha256:")
    receipt_path = audit / f"{name}.json"
    pending_path = audit / f"{name}.in-progress.json"
    if (
        receipt_path.exists()
        or receipt_path.is_symlink()
        or pending_path.exists()
        or pending_path.is_symlink()
    ):
        raise CleanupRefusal("CLEANUP_RECEIPT_EXISTS")
    pending = receipt | {"status": "IN_PROGRESS"}
    descriptor = os.open(pending_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(canonical_bytes(pending))
        stream.flush()
        os.fsync(stream.fileno())
    try:
        for item in plan.get("targets", []):
            path = Path(item["path"])
            if not path.exists():
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            deleted.append(str(path))
        descriptor = os.open(
            receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_bytes(receipt | {"deletedPaths": deleted}))
            stream.flush()
            os.fsync(stream.fileno())
        pending_path.unlink()
    except Exception:
        # The immutable in-progress record intentionally survives a partial delete.
        raise
    return receipt | {"deletedPaths": deleted, "receipt": str(receipt_path)}
