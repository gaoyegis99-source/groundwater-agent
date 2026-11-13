"""Auditable cleanup of rebuildable, Git-ignored workspace residue."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
from typing import Any

from .contracts import canonical_bytes, sha256_digest
from .xdg_layout import LifecyclePaths, ensure_private_roots, owned_path


class WorkspaceCleanupRefusalError(RuntimeError):
    """A workspace cleanup target failed a safety condition."""


WorkspaceCleanupRefusal = WorkspaceCleanupRefusalError

_DIRECTORY_NAMES = {
    ".generated",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}
_BUILD_NAMES = {"build", "dist"}


def _run_git(root: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )


def _is_ignored(root: Path, path: Path) -> bool:
    relative = path.relative_to(root).as_posix()
    return _run_git(root, ["check-ignore", "-q", "--", relative]).returncode == 0


def _has_tracked_content(root: Path, path: Path) -> bool:
    relative = path.relative_to(root).as_posix()
    result = _run_git(root, ["ls-files", "-z", "--", relative, relative + "/**"])
    return bool(result.stdout)


@lru_cache(maxsize=8)
def _linked_worktrees(root_text: str) -> tuple[Path, ...]:
    result = _run_git(Path(root_text), ["worktree", "list", "--porcelain"])
    if result.returncode:
        raise WorkspaceCleanupRefusal("WORKSPACE_WORKTREE_LIST_FAILED")
    return tuple(
        Path(line.removeprefix("worktree ")).absolute()
        for line in result.stdout.splitlines()
        if line.startswith("worktree ")
    )


def _mounts() -> set[Path]:
    try:
        lines = Path("/proc/self/mountinfo").read_text(errors="replace").splitlines()
    except OSError:
        return set()
    return {
        Path(fields[4].replace("\\040", " ")).absolute()
        for line in lines
        if len(fields := line.split()) > 4
    }


def _assert_safe(root: Path, source_root: Path, path: Path) -> None:
    absolute = path.absolute()
    if not absolute.is_relative_to(root) or absolute in {root, source_root}:
        raise WorkspaceCleanupRefusal("WORKSPACE_TARGET_OUTSIDE_ROOT")
    if absolute == root / ".git" or (root / ".git") in absolute.parents:
        raise WorkspaceCleanupRefusal("WORKSPACE_GIT_PROTECTED")
    if any(
        worktree != root and (absolute == worktree or absolute.is_relative_to(worktree))
        for worktree in _linked_worktrees(str(root))
    ):
        raise WorkspaceCleanupRefusal("WORKSPACE_LINKED_WORKTREE_PROTECTED")
    if absolute.is_symlink():
        raise WorkspaceCleanupRefusal("WORKSPACE_SYMLINK_REJECTED")
    if any(mount == absolute or mount.is_relative_to(absolute) for mount in _mounts()):
        raise WorkspaceCleanupRefusal("WORKSPACE_MOUNT_REJECTED")
    if _has_tracked_content(root, absolute):
        raise WorkspaceCleanupRefusal("WORKSPACE_TRACKED_CONTENT_PROTECTED")
    if not _is_ignored(root, absolute):
        raise WorkspaceCleanupRefusal("WORKSPACE_UNIGNORED_CONTENT_PROTECTED")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _identity(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "files": 0, "bytes": 0, "fingerprint": None}
    rows: list[list[object]] = []
    total = 0
    files = 0
    candidates = [path]
    if path.is_dir():
        candidates.extend(sorted(path.rglob("*"), key=lambda item: item.as_posix()))
    for candidate in candidates:
        metadata = candidate.lstat()
        relative = "." if candidate == path else candidate.relative_to(path).as_posix()
        if stat.S_ISLNK(metadata.st_mode):
            rows.append([relative, "symlink", os.readlink(candidate)])
            continue
        if stat.S_ISDIR(metadata.st_mode):
            rows.append([relative, "directory", stat.S_IMODE(metadata.st_mode)])
        elif stat.S_ISREG(metadata.st_mode):
            files += 1
            total += metadata.st_size
            rows.append(
                [
                    relative,
                    "file",
                    stat.S_IMODE(metadata.st_mode),
                    metadata.st_size,
                    _file_sha256(candidate),
                ]
            )
        else:
            raise WorkspaceCleanupRefusal("WORKSPACE_TYPE_REJECTED")
    return {
        "exists": True,
        "files": files,
        "bytes": total,
        "fingerprint": sha256_digest(canonical_bytes(rows)),
    }


def _candidate_paths(root: Path, source_root: Path) -> list[Path]:
    candidates: set[Path] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            continue
        if path.is_dir() and (
            path.name in _DIRECTORY_NAMES
            or path.name.endswith(".egg-info")
            or (
                path.name in _BUILD_NAMES
                and path != source_root / "environments/build"
            )
        ):
            candidates.add(path.absolute())
    for path in (source_root / "data").glob("tasks.sqlite3*"):
        if path.is_file() and not path.is_symlink():
            candidates.add(path.absolute())
    logs = source_root / "data/logs"
    if logs.exists() and not logs.is_symlink():
        candidates.add(logs.absolute())
    ordered = sorted(candidates, key=lambda item: (len(item.parts), item.as_posix()))
    topmost: list[Path] = []
    for candidate in ordered:
        if not any(candidate.is_relative_to(parent) for parent in topmost):
            topmost.append(candidate)
    return sorted(topmost, key=lambda item: item.as_posix())


def workspace_clean_plan(root: Path, source_root: Path) -> dict[str, Any]:
    root = root.absolute()
    source_root = source_root.absolute()
    targets = []
    for path in _candidate_paths(root, source_root):
        _assert_safe(root, source_root, path)
        targets.append(
            {
                "path": str(path),
                "type": "directory" if path.is_dir() else "file",
                "identity": _identity(path),
            }
        )
    body = {
        "schemaVersion": "workspace-clean-plan.v1",
        "action": "workspace-clean",
        "workspace": str(root),
        "sourceRoot": str(source_root),
        "candidateCount": len(targets),
        "files": sum(item["identity"]["files"] for item in targets),
        "bytes": sum(item["identity"]["bytes"] for item in targets),
        "targets": targets,
    }
    return body | {"confirmationDigest": sha256_digest(canonical_bytes(body))}


def _processes_using(path: Path) -> list[int]:
    users: list[int] = []
    for process in Path("/proc").iterdir():
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        links = [process / "cwd", process / "exe"]
        try:
            links.extend((process / "fd").iterdir())
        except OSError:
            pass
        for link in links:
            try:
                resolved = link.resolve()
            except OSError:
                continue
            if resolved == path or resolved.is_relative_to(path):
                users.append(int(process.name))
                break
    return users


def apply_workspace_clean(
    paths: LifecyclePaths, plan: dict[str, Any], confirmation: str
) -> dict[str, Any]:
    expected = plan.get("confirmationDigest")
    body = {key: value for key, value in plan.items() if key != "confirmationDigest"}
    if expected != sha256_digest(canonical_bytes(body)) or confirmation != expected:
        raise WorkspaceCleanupRefusal("WORKSPACE_CONFIRMATION_MISMATCH")
    if plan.get("schemaVersion") != "workspace-clean-plan.v1":
        raise WorkspaceCleanupRefusal("WORKSPACE_PLAN_INVALID")
    root = Path(str(plan["workspace"]))
    source_root = Path(str(plan["sourceRoot"]))
    current = workspace_clean_plan(root, source_root)
    if current != plan:
        raise WorkspaceCleanupRefusal("WORKSPACE_PLAN_CHANGED")
    for item in plan["targets"]:
        path = Path(item["path"])
        _assert_safe(root, source_root, path)
        if _identity(path) != item["identity"]:
            raise WorkspaceCleanupRefusal("WORKSPACE_TARGET_CHANGED")
        users = _processes_using(path)
        if users:
            raise WorkspaceCleanupRefusal(
                "WORKSPACE_TARGET_IN_USE:" + ",".join(map(str, users))
            )

    ensure_private_roots(paths, ("state",))
    audit = owned_path("cleanup/workspace-clean", paths.audit)
    audit.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = str(expected).removeprefix("sha256:")
    receipt_path = audit / f"{name}.json"
    pending_path = audit / f"{name}.in-progress.json"
    if (
        receipt_path.exists()
        or receipt_path.is_symlink()
        or pending_path.exists()
        or pending_path.is_symlink()
    ):
        raise WorkspaceCleanupRefusal("WORKSPACE_RECEIPT_EXISTS")
    pending = {
        "schemaVersion": "workspace-clean-receipt.v1",
        "status": "IN_PROGRESS",
        "action": "workspace-clean",
        "confirmationDigest": expected,
        "targets": plan["targets"],
    }
    descriptor = os.open(pending_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(canonical_bytes(pending))
        stream.flush()
        os.fsync(stream.fileno())
    deleted = []
    for item in plan["targets"]:
        path = Path(item["path"])
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        deleted.append(str(path))
    receipt = {
        "schemaVersion": "workspace-clean-receipt.v1",
        "status": "PASS",
        "action": "workspace-clean",
        "confirmationDigest": expected,
        "completedAt": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "candidateCount": len(deleted),
        "files": plan["files"],
        "bytes": plan["bytes"],
        "deletedPaths": deleted,
    }
    descriptor = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(canonical_bytes(receipt))
        stream.flush()
        os.fsync(stream.fileno())
    pending_path.unlink()
    return receipt | {"receipt": str(receipt_path)}
