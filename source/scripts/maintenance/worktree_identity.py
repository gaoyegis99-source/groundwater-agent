"""Git source identity shared by local instances and validation checkpoints."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import subprocess


def worktree_identity(
    root: Path, *, exclude: tuple[Path, ...] = (),
) -> tuple[str, bool, str]:
    """Return the revision, dirty state, and digest of mounted source inputs.

    ``git diff`` covers tracked index and worktree changes. Untracked, non-ignored
    files are hashed separately because local builds and source mounts can consume
    them even though they do not appear in a diff against ``HEAD``.
    """
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    pathspecs = ()
    if exclude:
        top = Path(subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], cwd=root, text=True,
        ).strip()).resolve()
        pathspecs = tuple(
            f":(top,exclude,literal){path.relative_to(top).as_posix()}"
            for path in exclude if path.is_relative_to(top)
        )
    filters = ("--", *pathspecs) if pathspecs else ()
    diff = subprocess.run(
        [
            "git",
            "diff",
            "--binary",
            "--no-ext-diff",
            "--no-textconv",
            "HEAD",
            *filters,
        ],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout
    untracked_output = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z", *filters],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout
    untracked = sorted(path for path in untracked_output.split(b"\0") if path)

    digest = hashlib.sha256()

    def add_record(name: bytes, payload: bytes) -> None:
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)

    add_record(b"identity-version", b"worktree.v1")
    add_record(b"revision", revision.encode("utf-8"))
    add_record(b"tracked-diff", diff)
    for relative_bytes in untracked:
        relative = Path(os.fsdecode(relative_bytes))
        path = root / relative
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode).to_bytes(4, "big")
        if stat.S_ISREG(metadata.st_mode):
            kind = b"file"
            content = path.read_bytes()
        elif stat.S_ISLNK(metadata.st_mode):
            kind = b"symlink"
            content = os.fsencode(os.readlink(path))
        else:
            kind = b"special"
            content = b""
        add_record(b"untracked-path", relative_bytes)
        add_record(b"untracked-kind", kind)
        add_record(b"untracked-mode", mode)
        add_record(b"untracked-content", content)
    return revision, bool(diff or untracked), digest.hexdigest()
