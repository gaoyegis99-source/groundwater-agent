"""Content identity for every third-party release dependency."""

from __future__ import annotations

import hashlib
from pathlib import Path


DEPENDENCY_INPUTS = (
    "uv.lock",
    "environments/common/requirements.lock",
    "environments/native/uv.lock",
    "environments/native/requirements.lock",
    "environments/qgis/uv.lock",
    "environments/qgis/requirements.lock",
    "environments/build/requirements.lock",
    "environments/testing/requirements.lock",
    "package-lock.json",
    "system-deps.lock.json",
    "toolchain.lock.json",
)


def dependency_digest(source_root: Path) -> str:
    digest = hashlib.sha256()
    for relative in DEPENDENCY_INPUTS:
        path = source_root / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"DEPENDENCY_LOCK_MISSING:{relative}")
        digest.update(relative.encode() + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def dependency_name(source_root: Path) -> str:
    return "sha256-" + dependency_digest(source_root)
