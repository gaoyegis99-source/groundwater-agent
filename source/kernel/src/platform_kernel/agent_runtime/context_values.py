"""Canonical context value helpers shared by pure context modules."""

from __future__ import annotations

import hashlib
import json

def canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: object) -> str:
    encoded = (
        value if isinstance(value, str) else canonical_json(value)
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _labeled(value: dict[str, object]) -> str:
    return f"{value['label']}: " + canonical_json(value["data"])


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _dicts(value: object) -> list[dict[str, object]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, dict)]


def _integer(value: object) -> int | None:
    return value if type(value) is int else None
