from __future__ import annotations

import json
from pathlib import Path
import re


BASE_IMAGE_NAMES = (
    "nginx", "node", "pdal", "pgvector", "postgres", "python", "ubuntu",
)
_DIGEST_REFERENCE = re.compile(r"^[^\s]+@sha256:[0-9a-f]{64}$")


def locked_base_image_contexts(source_root: Path) -> tuple[str, ...]:
    """Expose digest-pinned registry images as ordinary BuildKit contexts."""

    try:
        lock = json.loads((source_root / "system-deps.lock.json").read_text())
        images = lock["baseImages"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("SYSTEM_DEPS_LOCK_INVALID") from error
    if lock.get("schemaVersion") != "system-deps-lock.v1" or not isinstance(
        images, dict
    ):
        raise ValueError("SYSTEM_DEPS_LOCK_INVALID")
    result = []
    for name in BASE_IMAGE_NAMES:
        reference = images.get(name)
        if not isinstance(reference, str) or _DIGEST_REFERENCE.fullmatch(reference) is None:
            raise ValueError(f"BASE_IMAGE_REFERENCE_INVALID:{name}")
        result.append(f"platform-base-{name}=docker-image://{reference}")
    return tuple(result)
