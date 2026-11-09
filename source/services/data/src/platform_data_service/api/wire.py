from __future__ import annotations

from platform_protocol_contracts import to_wire as _to_wire


_OPAQUE = frozenset({"provenance", "metadata", "contracts", "format_media_types"})
_NAMED_MAPS = frozenset({"fields", "semantic_ids"})


def to_wire(value: object) -> object:
    return _to_wire(value, opaque_keys=_OPAQUE, named_map_keys=_NAMED_MAPS)


def with_semantic_id(value: dict, field: str) -> dict:
    """Rename an owner's generic persistence key at the HTTP boundary."""
    return {field: value["id"], **{key: item for key, item in value.items() if key != "id"}}


def page_with_semantic_ids(value: dict, field: str) -> dict:
    return {
        **value,
        "items": [with_semantic_id(dict(item), field) for item in value["items"]],
    }


def fields_with_semantic_ids(value: dict | None) -> dict | None:
    """Expose physical field identities as ``fieldId`` without changing storage."""
    if value is None or not isinstance(value.get("fields"), list):
        return value
    return {
        **value,
        "fields": [with_semantic_id(dict(item), "field_id") for item in value["fields"]],
    }
