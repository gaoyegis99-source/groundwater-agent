from __future__ import annotations

import io
import json
import math
from typing import Any

from ..errors import DataProblemError


MAX_JSON_NESTING = 100


class _DuplicateKeyError(ValueError):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


class _InvalidConstantError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise _InvalidConstantError


def _validate_tree(value: Any) -> None:
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > MAX_JSON_NESTING:
            raise DataProblemError(
                "JSON_NESTING_TOO_DEEP", max_depth=MAX_JSON_NESTING
            )
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError:
                raise DataProblemError("JSON_INVALID_UNICODE") from None
        elif isinstance(item, float) and not math.isfinite(item):
            raise DataProblemError("JSON_NONFINITE_NUMBER")
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, dict):
            for key, child in item.items():
                try:
                    key.encode("utf-8")
                except UnicodeEncodeError:
                    raise DataProblemError("JSON_INVALID_UNICODE") from None
                pending.append((child, depth + 1))


def validate_json(source, *, size: int, max_bytes: int) -> None:
    """Validate declared JSON without allowing ambiguous object keys.

    The standard decoder is intentionally bounded because it materializes the
    document. Oversized JSON fails closed instead of being marked READY without
    inspection.
    """
    if size > max_bytes:
        raise DataProblemError(
            "JSON_VALIDATION_BUDGET", 413, size=size, max_bytes=max_bytes
        )
    wrapper = io.TextIOWrapper(source, encoding="utf-8-sig")
    try:
        try:
            value = json.load(
                wrapper,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
        except _DuplicateKeyError:
            raise DataProblemError("JSON_DUPLICATE_KEY") from None
        except _InvalidConstantError:
            raise DataProblemError("JSON_NONFINITE_NUMBER") from None
        except (json.JSONDecodeError, UnicodeError, RecursionError, ValueError):
            raise DataProblemError("JSON_INVALID") from None
        _validate_tree(value)
    finally:
        wrapper.detach()
