from __future__ import annotations

from platform_protocol_contracts import to_wire as _to_wire


_OPAQUE = frozenset({"provenance", "metadata", "contracts", "format_media_types"})
_NAMED_MAPS = frozenset({"fields", "semantic_ids"})
_JS_MAX_SAFE_INTEGER = 2**53 - 1


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


def _unsafe_integer_text(value: object) -> str | None:
    if type(value) is int and abs(value) > _JS_MAX_SAFE_INTEGER:
        return str(value)
    return None


def _field_with_exact_integers(field: dict) -> dict:
    profile = field.get("profile")
    if field.get("type") not in ("int64", "uint64") or not isinstance(profile, dict):
        return field

    exact = {}
    samples = profile.get("sample_values")
    if isinstance(samples, list):
        sample_text = [_unsafe_integer_text(value) for value in samples]
        if any(sample_text):
            exact["sample_values"] = sample_text
    value_range = profile.get("value_range")
    if isinstance(value_range, dict):
        for endpoint in ("minimum", "maximum"):
            if (text := _unsafe_integer_text(value_range.get(endpoint))) is not None:
                exact[endpoint] = text
    if exact:
        return {**field, "profile": {**profile, "exact_integers": exact}}
    return field


def fields_with_semantic_ids(value: dict | None) -> dict | None:
    """Project physical field identities and exact profile values without changing storage."""
    if value is None or not isinstance(value.get("fields"), list):
        return value
    return {
        **value,
        "fields": [
            _field_with_exact_integers(with_semantic_id(dict(item), "field_id"))
            for item in value["fields"]
        ],
    }
