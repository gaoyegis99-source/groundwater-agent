"""Explicit verification/build inputs, independent of caches and Git tracking.

The same inventory identifies and materializes the source that is actually
tested and built. Generated reports are outputs, never implicit inputs.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def input_manifest(root: Path) -> dict[str, Any]:
    root = root.resolve()
    profile = json.loads(
        (root / "verification-profile.json").read_text(encoding="utf-8")
    )
    if profile.get("schema_version") != "v6-developer-verification-profile-v1":
        raise ValueError("unsupported verification profile")
    patterns = profile.get("input_patterns")
    if not isinstance(patterns, list) or not patterns:
        raise ValueError("verification profile requires explicit input patterns")
    paths: set[Path] = set()
    for pattern in patterns:
        if (
            not isinstance(pattern, str)
            or Path(pattern).is_absolute()
            or ".." in Path(pattern).parts
        ):
            raise ValueError("input patterns must stay inside the package")
        matches = list(root.glob(pattern))
        if not any(character in pattern for character in "*?[") and not matches:
            raise ValueError(f"required verification input is missing: {pattern}")
        paths.update(path for path in matches if path.is_file() or path.is_symlink())
    files = []
    for path in sorted(paths):
        relative = path.relative_to(root)
        if path.resolve() != path or path.is_symlink():
            raise ValueError(
                f"verification input must not traverse a symlink: {relative}"
            )
        # A glob names source types and roots, not arbitrary dot-directory data.
        if any(part.startswith(".") for part in relative.parts):
            continue
        content = path.read_bytes()
        files.append(
            {
                "path": relative.as_posix(),
                "bytes": len(content),
                "sha256": sha256(content),
            }
        )
    payload = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "schema_version": "v6-source-inputs-v1",
        "sha256": sha256(payload),
        "files": files,
    }


def copy_inputs(root: Path, destination: Path) -> dict[str, Any]:
    """Copy exactly hashed inputs; fail if the source changed during capture."""
    manifest = input_manifest(root)
    destination.mkdir(parents=True, exist_ok=False)
    for item in manifest["files"]:
        content = (root / item["path"]).read_bytes()
        if sha256(content) != item["sha256"]:
            raise ValueError("verification source changed during capture")
        target = destination / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    if input_manifest(destination) != manifest or input_manifest(root) != manifest:
        raise ValueError("verification source changed during capture")
    return manifest
