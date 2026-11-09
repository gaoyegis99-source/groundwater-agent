from __future__ import annotations

from platform_protocol_contracts import to_camel


_OPAQUE_OBJECTS = {"arguments", "metadata", "provenance"}
_NAMED_OBJECTS = {
    "input_references", "inputs", "parameter_sources", "parameters",
    "default_parameters",
}


def to_wire(value: object, *, parent: str | None = None) -> object:
    """Convert a Kernel projection to v12 without rewriting open-map identities."""
    if isinstance(value, list | tuple):
        return [to_wire(item) for item in value]
    if not isinstance(value, dict):
        return value
    if parent in _OPAQUE_OBJECTS:
        return value
    if parent in _NAMED_OBJECTS:
        return {str(key): to_wire(item) for key, item in value.items()}
    return {
        to_camel(str(key)): to_wire(item, parent=str(key))
        for key, item in value.items()
    }
