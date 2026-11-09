"""Digest-addressed toolchain identity shared by bootstrap and launchers."""

from __future__ import annotations

import hashlib
from pathlib import Path

TOOLCHAIN_INPUTS = (
    "pyproject.toml",
    "uv.lock",
    "toolchain.lock.json",
)


def toolchain_digest(source_root: Path) -> str:
    digest = hashlib.sha256()
    for relative in TOOLCHAIN_INPUTS:
        path = source_root / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"TOOLCHAIN_INPUT_MISSING:{relative}")
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def toolchain_name(source_root: Path) -> str:
    return "v6-" + toolchain_digest(source_root)[:16]
