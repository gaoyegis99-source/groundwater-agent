"""Primitive validation and identity helpers for Kernel settings."""

from __future__ import annotations

import hashlib
import json
import re


def _text(value: object, error: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(error)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as cause:
        raise ValueError(error) from cause
    return value


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("KERNEL_CONFIG_INVALID")
    return dict(value)


def _positive_integer(value: str | int | None, name: str) -> int:
    if value is None or value == "":
        raise ValueError(f"{name}_REQUIRED")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name}_INVALID") from error
    if isinstance(value, bool) or parsed <= 0 or str(parsed) != str(value):
        raise ValueError(f"{name}_INVALID")
    return parsed


def _optional_url(value: str | None, name: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.startswith(("http://", "https://")):
        raise ValueError(f"{name}_INVALID")
    if any(character.isspace() for character in value):
        raise ValueError(f"{name}_INVALID")
    return value.rstrip("/")


def _health_path(value: str, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or value.startswith("//")
        or "://" in value
        or any(character.isspace() for character in value)
    ):
        raise ValueError(f"{name}_INVALID")
    return value


def _checksum(value: object) -> str:
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _digest(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ValueError("KERNEL_CONFIG_INVALID")
    return value
