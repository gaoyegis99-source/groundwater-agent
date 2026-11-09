from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import types
from typing import Annotated, get_args, get_origin

from pydantic import BaseModel, ConfigDict


def to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def to_wire(
    value: object,
    *,
    opaque_keys: frozenset[str] = frozenset(),
    named_map_keys: frozenset[str] = frozenset(),
) -> object:
    """Convert platform-owned mapping keys to camel case for an HTTP response.

    Opaque maps retain their contents exactly. Named maps retain their keys while
    recursively converting each platform-owned value.
    """
    def convert(item: object, parent: str | None = None) -> object:
        if isinstance(item, list | tuple):
            return [convert(nested) for nested in item]
        if not isinstance(item, dict):
            return item
        if parent in opaque_keys:
            return item
        if parent in named_map_keys:
            return {
                str(key): convert(nested)
                for key, nested in item.items()
            }
        return {
            to_camel(str(key)): convert(nested, str(key))
            for key, nested in item.items()
        }

    return convert(value)


class CamelModel(BaseModel):
    """Strict platform-owned HTTP JSON object.

    Python code uses snake_case. The wire accepts and emits only camelCase.
    Open mappings such as operator arguments remain opaque to this model.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        populate_by_name=False,
        serialize_by_alias=True,
    )


def validate_camel_json(
    value: object,
    *,
    opaque_keys: frozenset[str] = frozenset(),
    named_map_keys: frozenset[str] = frozenset(),
) -> None:
    """Validate JSON and reject snake_case keys in platform-owned objects.

    ``opaque_keys`` identify user or provider-owned maps whose keys retain their
    original spelling. ``named_map_keys`` identify maps with owner-defined keys
    but platform-owned values, such as a map of field IDs to field DTOs.
    """

    def visit(item: object, parent: str | None = None, *, enforce_case: bool = True) -> None:
        if isinstance(item, dict):
            opaque = parent in opaque_keys
            named = parent in named_map_keys
            for key, nested in item.items():
                if not isinstance(key, str):
                    raise ValueError("WIRE_JSON_INVALID")
                try:
                    key.encode("utf-8")
                except UnicodeEncodeError as cause:
                    raise ValueError("WIRE_JSON_INVALID") from cause
                if enforce_case and not opaque and not named and "_" in key:
                    raise ValueError(f"WIRE_FIELD_CASE_INVALID:{key}")
                visit(
                    nested,
                    None if named else key,
                    enforce_case=enforce_case and not opaque,
                )
            return
        if isinstance(item, list):
            for nested in item:
                visit(nested, parent, enforce_case=enforce_case)
            return
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError as cause:
                raise ValueError("WIRE_JSON_INVALID") from cause
            return
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("WIRE_JSON_INVALID")
        if item is not None and not isinstance(item, (bool, int, float)):
            raise ValueError("WIRE_JSON_INVALID")

    visit(value)


def reject_snake_wire(value: object, annotation: object) -> None:
    """Reject Python field names at an HTTP boundary, including nested DTOs.

    Mapping identities remain opaque. Their values are checked only when the type
    explicitly declares another DTO, so operator arguments and metadata keep their
    owner-defined keys.
    """
    origin = get_origin(annotation)
    if origin is Annotated:
        reject_snake_wire(value, get_args(annotation)[0])
        return
    if origin in {types.UnionType} or str(origin) == "typing.Union":
        for candidate in get_args(annotation):
            if candidate is type(None):
                continue
            reject_snake_wire(value, candidate)
        return
    if origin in {list, tuple, set, frozenset, Sequence}:
        arguments = get_args(annotation)
        if isinstance(value, list) and arguments:
            for item in value:
                reject_snake_wire(item, arguments[0])
        return
    if origin in {dict, Mapping}:
        arguments = get_args(annotation)
        if isinstance(value, dict) and len(arguments) == 2:
            for item in value.values():
                reject_snake_wire(item, arguments[1])
        return
    try:
        is_model = isinstance(annotation, type) and issubclass(annotation, BaseModel)
    except TypeError:
        is_model = False
    if not is_model or not isinstance(value, dict):
        return
    fields = annotation.model_fields
    for name, field in fields.items():
        alias = field.alias or name
        if alias != name and name in value:
            raise ValueError(f"WIRE_FIELD_CASE_INVALID:{name}")
        if alias in value:
            reject_snake_wire(value[alias], field.annotation)
