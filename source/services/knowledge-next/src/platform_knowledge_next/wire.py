from __future__ import annotations

from pydantic import BaseModel
from platform_protocol_contracts import to_wire as _to_wire


_OPAQUE = frozenset({"metadata", "provenance", "locator"})
_NAMED_MAPS = frozenset({"filters"})


def to_wire(value: object) -> object:
    return _to_wire(value, opaque_keys=_OPAQUE, named_map_keys=_NAMED_MAPS)


def model_to_wire(value: BaseModel) -> dict[str, object]:
    """Map a service model, including platform-owned mapping fields, to HTTP JSON."""
    mapped = to_wire(value.model_dump(mode="json"))
    if not isinstance(mapped, dict):
        raise TypeError("WIRE_MODEL_NOT_OBJECT")
    return mapped


def with_semantic_id(value: dict, field: str) -> dict:
    return {field: value["id"], **{key: item for key, item in value.items() if key != "id"}}
