"""Provider-specific normalization at executable-model trust boundaries."""

from __future__ import annotations

import json
import logging
import math
import re

from ..settings import ExecutableModelDefinition

_JSON_NUMBER = re.compile(
    r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z"
)
_JSON_INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")
_NAME_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NAME_SEPARATOR = re.compile(r"[^A-Za-z0-9]+")

_LOGGER = logging.getLogger(__name__)


def _name_tokens(value: str) -> tuple[str, ...]:
    separated = _NAME_BOUNDARY.sub("_", value)
    return tuple(
        item.lower() for item in _NAME_SEPARATOR.split(separated) if item
    )


def _canonicalize_property_names(
    arguments: dict[str, object], properties: dict[str, object],
) -> tuple[dict[str, object], dict[str, str]]:
    """Map only unique schema-derived aliases without hiding collisions."""
    candidates: dict[tuple[str, ...], set[str]] = {}
    for property_name in properties:
        if not isinstance(property_name, str):
            continue
        tokens = _name_tokens(property_name)
        if tokens:
            candidates.setdefault(tokens, set()).add(property_name)

    proposed: dict[str, str] = {}
    target_sources: dict[str, list[str]] = {}
    for supplied_name in arguments:
        if supplied_name in properties:
            continue
        matches = candidates.get(_name_tokens(supplied_name), set())
        if len(matches) != 1:
            continue
        target = next(iter(matches))
        proposed[supplied_name] = target
        target_sources.setdefault(target, []).append(supplied_name)

    mappings = {
        source: target
        for source, target in proposed.items()
        if target not in arguments and len(target_sources[target]) == 1
    }
    if not mappings:
        return arguments, {}
    normalized = {
        mappings.get(name, name): value for name, value in arguments.items()
    }
    return normalized, mappings


def _flatten_schema_paths(
    arguments: dict[str, object], properties: dict[str, object],
) -> tuple[dict[str, object], tuple[str, ...]]:
    """Flatten nested MiniMax objects only when the schema uses dotted paths."""
    schema_aliases: dict[tuple[str, ...], set[str]] = {}
    for name in properties:
        if not isinstance(name, str):
            continue
        tokens = _name_tokens(name)
        if tokens:
            schema_aliases.setdefault(tokens, set()).add(name)
    normalized: dict[str, object] = {}
    flattened: list[str] = []

    def visit(path: str, value: object) -> None:
        matches_schema_object = False
        targets = schema_aliases.get(_name_tokens(path), set())
        if len(targets) == 1:
            target = next(iter(targets))
            definition = properties.get(target)
            matches_schema_object = (
                isinstance(definition, dict)
                and definition.get("type") == "object"
            )
        if not isinstance(value, dict) or path in properties or matches_schema_object:
            normalized[path] = value
            return
        for child, child_value in value.items():
            if not isinstance(child, str):
                normalized[path] = value
                return
            child_path = f"{path}.{child}"
            flattened.append(child_path)
            visit(child_path, child_value)

    for name, value in arguments.items():
        visit(name, value)
    return normalized, tuple(flattened)


def normalize_model_arguments(
    arguments: dict[str, object],
    schema: dict[str, object],
    *,
    provider: str,
) -> dict[str, object]:
    """Restore unambiguous MiniMax names and numeric scalars before validation.

    MiniMax sometimes changes camel/dotted names to snake case, converts dotted
    properties into nested objects, and serializes a JSON Schema ``number`` or
    ``integer`` as a JSON string. Only schema-derived paths, unique aliases and
    exact numeric properties are adapted. Contract validation still enforces
    bounds, enums, required fields and extra fields.
    """
    if provider != "minimax":
        return arguments
    return _normalize_model_arguments(arguments, schema, root_schema=schema)


def _resolve_schema(
    definition: object, root_schema: dict[str, object], value: object,
) -> dict[str, object] | None:
    if not isinstance(definition, dict):
        return None
    reference = definition.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        target = root_schema.get("$defs", {}).get(reference.removeprefix("#/$defs/"))
        return target if isinstance(target, dict) else None
    variants = definition.get("anyOf") or definition.get("oneOf")
    if isinstance(variants, list):
        expected = (
            "object" if isinstance(value, dict)
            else "array" if isinstance(value, list)
            else "boolean" if isinstance(value, bool)
            else "integer" if isinstance(value, int)
            else "number" if isinstance(value, float)
            else "string" if isinstance(value, str)
            else "null" if value is None
            else None
        )
        for variant in variants:
            resolved = _resolve_schema(variant, root_schema, value)
            if resolved is not None and resolved.get("type") == expected:
                return resolved
        return None
    return definition


def _normalize_model_arguments(
    arguments: dict[str, object],
    schema: dict[str, object],
    *,
    root_schema: dict[str, object],
) -> dict[str, object]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return arguments
    flattened_arguments, flattened = _flatten_schema_paths(arguments, properties)
    normalized, mappings = _canonicalize_property_names(
        flattened_arguments, properties,
    )
    if flattened:
        _LOGGER.info(
            "normalized MiniMax executable-model nested paths=%s",
            sorted(flattened),
        )
    if mappings:
        _LOGGER.info(
            "normalized MiniMax executable-model argument names aliases=%s",
            sorted(f"{source}->{target}" for source, target in mappings.items()),
        )
    normalized = dict(normalized)
    for name, value in tuple(normalized.items()):
        definition = _resolve_schema(properties.get(name), root_schema, value)
        if isinstance(value, list) and value:
            scalar_definition = _resolve_schema(
                properties.get(name), root_schema, value[0],
            )
            if (
                isinstance(scalar_definition, dict)
                and scalar_definition.get("type")
                in {"string", "integer", "number", "boolean"}
                and all(item == value[0] for item in value)
            ):
                value = value[0]
                normalized[name] = value
                definition = scalar_definition
        if not isinstance(value, str) or not isinstance(definition, dict):
            pass
        else:
            expected = definition.get("type")
            if expected == "integer":
                pattern = _JSON_INTEGER
            elif expected == "number":
                pattern = _JSON_NUMBER
            else:
                pattern = None
            if pattern is not None and pattern.fullmatch(value) is not None:
                parsed = json.loads(value)
                if (
                    not isinstance(parsed, bool)
                    and isinstance(parsed, (int, float))
                    and (
                        not isinstance(parsed, float) or math.isfinite(parsed)
                    )
                ):
                    normalized[name] = parsed
                    value = parsed
        definition = _resolve_schema(properties.get(name), root_schema, value)
        if not isinstance(definition, dict):
            continue
        if isinstance(value, dict) and definition.get("type") == "object":
            normalized[name] = _normalize_model_arguments(
                value, definition, root_schema=root_schema,
            )
        elif isinstance(value, list) and definition.get("type") == "array":
            item_schema = definition.get("items")
            normalized[name] = [
                _normalize_model_arguments(
                    item, resolved, root_schema=root_schema,
                )
                if isinstance(item, dict)
                and (resolved := _resolve_schema(item_schema, root_schema, item))
                is not None
                else item
                for item in value
            ]
    return normalized


def normalize_model_tool_arguments(
    arguments: dict[str, object],
    schema: dict[str, object],
    *,
    provider: str,
) -> dict[str, object]:
    """Normalize an executable call without mixing control and model inputs.

    ``progress_intent`` belongs to the Agent activity protocol.  Providers can
    copy it from observation tools onto the eventual executable call even
    though it is deliberately absent from the executable model schema.  Keep
    that control metadata out of the frozen business arguments; a model that
    actually declares a property with this name retains it normally.
    """
    properties = schema.get("properties")
    nested = arguments.get("arguments")
    if provider == "minimax" and isinstance(nested, str):
        try:
            decoded = json.loads(nested)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, dict):
            nested = decoded
    if (
        provider == "minimax"
        and set(arguments) == {"arguments"}
        and isinstance(nested, dict)
        and isinstance(properties, dict)
        and "arguments" not in properties
    ):
        arguments = nested
    if (
        "progress_intent" in arguments
        and isinstance(properties, dict)
        and "progress_intent" not in properties
    ):
        arguments = {
            name: value
            for name, value in arguments.items()
            if name != "progress_intent"
        }
    return normalize_model_arguments(arguments, schema, provider=provider)


def omit_unset_field_arguments(
    arguments: dict[str, object],
    model: ExecutableModelDefinition,
    explicit_request: object = None,
) -> dict[str, object]:
    """Keep optional field selectors absent when the Provider fills an unset slot."""
    properties = model.input_schema.get("properties")
    required = model.input_schema.get("required", [])
    if not isinstance(properties, dict) or not isinstance(required, list):
        return arguments
    explicit_arguments = (
        explicit_request.get("arguments")
        if isinstance(explicit_request, dict) and explicit_request.get("tool") == model.tool_name
        else None
    )
    omitted = set()
    for field in model.field_requirements:
        name = field.parameter
        definition = properties.get(name)
        if (
            name not in arguments or name in required or name in model.default_parameters
            or not isinstance(definition, dict) or definition.get("type") != "string"
            or not isinstance(min_length := definition.get("minLength"), int) or min_length < 1
        ):
            continue
        if isinstance(explicit_arguments, dict):
            # The structured request defines which optional selectors the user supplied.
            unset = name not in explicit_arguments
        else:
            unset = arguments[name] is None or arguments[name] == ""
        if unset:
            omitted.add(name)
    return {name: value for name, value in arguments.items() if name not in omitted}
