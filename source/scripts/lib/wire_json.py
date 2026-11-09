"""Explicit transport mapping for platform-owned camelCase JSON contracts."""

from __future__ import annotations

import re


OPAQUE_MAPS = frozenset(
    {
        "arguments",
        "default_parameters",
        "locator",
        "metadata",
        "provenance",
        "rows",
    }
)
NAMED_MAPS = frozenset(
    {
        "fields",
        "inputs",
        "input_references",
        "semantic_ids",
    }
)


def _camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def to_wire_json(value: object, *, parent: str | None = None) -> object:
    """Map owned object keys to camelCase while preserving open-map identities."""
    if isinstance(value, list | tuple):
        # A named field such as ``inputs`` can hold either an identity-keyed
        # object map or an array of owned contract objects.  Named-map key
        # preservation applies only to the former; array elements must resume
        # normal owned-key conversion.  Opaque arrays (for example rows) keep
        # their element keys verbatim.
        item_parent = parent if parent in OPAQUE_MAPS else None
        return [to_wire_json(item, parent=item_parent) for item in value]
    if not isinstance(value, dict):
        return value
    if parent in OPAQUE_MAPS:
        return value
    if parent in NAMED_MAPS:
        return {str(key): to_wire_json(item) for key, item in value.items()}
    return {
        _camel(str(key)): to_wire_json(item, parent=str(key))
        for key, item in value.items()
    }


def from_wire_json(value: object, *, parent: str | None = None) -> object:
    """Map owned camelCase object keys to Python snake_case projections."""
    if isinstance(value, list):
        item_parent = parent if parent in OPAQUE_MAPS else None
        return [from_wire_json(item, parent=item_parent) for item in value]
    if not isinstance(value, dict):
        return value
    if parent in OPAQUE_MAPS:
        return value
    if parent in NAMED_MAPS:
        return {str(key): from_wire_json(item) for key, item in value.items()}
    return {
        _snake(str(key)): from_wire_json(item, parent=_snake(str(key)))
        for key, item in value.items()
    }
