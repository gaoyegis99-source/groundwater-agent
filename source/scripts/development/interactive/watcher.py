from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path


_IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".generated",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "playwright-report",
        "release",
        "test-results",
        "verification",
    }
)
_WATCHED_SUFFIXES = frozenset(
    {
        ".css",
        ".html",
        ".js",
        ".json",
        ".jsx",
        ".lock",
        ".md",
        ".mjs",
        ".py",
        ".sql",
        ".svg",
        ".toml",
        ".ts",
        ".tsx",
        ".yaml",
        ".yml",
    }
)


def is_container_build_file(name: str) -> bool:
    return name in {"Dockerfile", ".dockerignore"} or name.startswith("Dockerfile.")


@dataclass(frozen=True)
class FileStamp:
    size: int
    modified_ns: int
    digest: str


@dataclass(frozen=True)
class SourceChange:
    kind: str
    path: str


@dataclass(frozen=True)
class SourceSnapshot:
    root: Path
    files: dict[str, FileStamp]

    @classmethod
    def capture(
        cls, root: Path, *, previous: "SourceSnapshot | None" = None
    ) -> "SourceSnapshot":
        resolved = root.resolve()
        previous_files = (
            previous.files if previous is not None and previous.root == resolved else {}
        )
        files: dict[str, FileStamp] = {}
        for current, directories, names in os.walk(
            resolved, topdown=True, followlinks=False
        ):
            directories[:] = sorted(
                name
                for name in directories
                if name not in _IGNORED_DIRECTORIES
                and not (Path(current) / name).is_symlink()
            )
            for name in sorted(names):
                path = Path(current) / name
                if path.is_symlink() or (
                    path.suffix.lower() not in _WATCHED_SUFFIXES
                    and not is_container_build_file(name)
                ):
                    continue
                try:
                    metadata = path.stat()
                except (FileNotFoundError, PermissionError):
                    continue
                relative = path.relative_to(resolved).as_posix()
                prior = previous_files.get(relative)
                if (
                    prior is not None
                    and prior.size == metadata.st_size
                    and prior.modified_ns == metadata.st_mtime_ns
                ):
                    files[relative] = prior
                    continue
                digest = hashlib.blake2b(digest_size=16)
                try:
                    with path.open("rb") as stream:
                        for block in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(block)
                except (FileNotFoundError, PermissionError):
                    continue
                files[relative] = FileStamp(
                    size=metadata.st_size,
                    modified_ns=metadata.st_mtime_ns,
                    digest=digest.hexdigest(),
                )
        return cls(root=resolved, files=files)


def diff_snapshots(
    before: SourceSnapshot, after: SourceSnapshot
) -> tuple[SourceChange, ...]:
    if before.root != after.root:
        raise ValueError("DEV_SNAPSHOT_ROOT_MISMATCH")
    changes = []
    for path in sorted(before.files.keys() | after.files.keys()):
        if path not in before.files:
            changes.append(SourceChange("added", path))
        elif path not in after.files:
            changes.append(SourceChange("deleted", path))
        elif before.files[path] != after.files[path]:
            changes.append(SourceChange("modified", path))
    return tuple(changes)
