"""Offline schema loading and canonical identities for composition documents."""

from __future__ import annotations

from datetime import date, datetime, time
from functools import cache
import hashlib
from importlib.resources import files
import json
import math
import re

from jsonschema import FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry, Resource

from ..validation import StrictValidator, WorkflowValidationError, pointer, require_json


SCHEMA_NAMES = (
    "operator-manifest.v2", "workflow.v7", "composition-types.v1",
    "composition-vocabulary.v1", "composition-fact.v1", "composition-validation.v1",
)
SAFE_INTEGER = 2**53 - 1
JSON_SCHEMA = "https://json-schema.org/draft/2020-12/schema"
FORMAT_CHECKER = FormatChecker(formats=[])
_DATE = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
_TIME = r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{1,6})?(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"


@FORMAT_CHECKER.checks("date", raises=ValueError)
def _date_format(value):
    return not isinstance(value, str) or bool(re.fullmatch(_DATE, value) and date.fromisoformat(value))


@FORMAT_CHECKER.checks("time", raises=ValueError)
def _time_format(value):
    return not isinstance(value, str) or bool(re.fullmatch(_TIME, value) and time.fromisoformat(value.upper()))


@FORMAT_CHECKER.checks("date-time", raises=ValueError)
def _datetime_format(value):
    return not isinstance(value, str) or bool(
        re.fullmatch(_DATE + "[Tt]" + _TIME, value) and datetime.fromisoformat(value.upper())
    )


def load_json(content: str | bytes) -> object:
    def unique(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise WorkflowValidationError("COMPOSITION_JSON_DUPLICATE_KEY")
            result[name] = value
        return result

    try:
        value = json.loads(content.decode("utf-8") if isinstance(content, bytes) else content, object_pairs_hook=unique)
    except WorkflowValidationError:
        raise
    except (UnicodeError, ValueError) as cause:
        raise WorkflowValidationError("COMPOSITION_JSON_INVALID") from cause
    require_document(value)
    return value


def require_document(value: object) -> None:
    require_json(value)
    pending = [("", value)]
    while pending:
        path, item = pending.pop()
        if isinstance(item, dict):
            pending.extend((path + pointer([key]), child) for key, child in item.items())
        elif isinstance(item, list):
            pending.extend((path + pointer([index]), child) for index, child in enumerate(item))
        elif type(item) is int and abs(item) > SAFE_INTEGER:
            raise WorkflowValidationError("COMPOSITION_INTEGER_PRECISION", path)


def _number(value: float) -> str:
    """Render binary64 shortest round-trip digits using JCS exponent thresholds."""
    if not math.isfinite(value):
        raise WorkflowValidationError("COMPOSITION_JSON_INVALID")
    if value == 0:
        return "0"
    coefficient, _, exponent = repr(abs(value)).partition("e")
    whole, _, fraction = coefficient.partition(".")
    raw = whole + fraction
    digits = raw.lstrip("0").rstrip("0")
    point = len(whole) + int(exponent or "0") - (len(raw) - len(raw.lstrip("0")))
    if 0 < point <= 21:
        rendered = digits.ljust(point, "0") if len(digits) <= point else digits[:point] + "." + digits[point:]
    elif -6 < point <= 0:
        rendered = "0." + "0" * -point + digits
    else:
        mantissa = digits[0] + ("." + digits[1:] if len(digits) > 1 else "")
        rendered = mantissa + f"e{point - 1:+d}"
    return ("-" if value < 0 else "") + rendered


def _canonical(value: object) -> str:
    if isinstance(value, dict):
        keys = sorted(value, key=lambda key: key.encode("utf-16-be"))
        return "{" + ",".join(_canonical(key) + ":" + _canonical(value[key]) for key in keys) + "}"
    if isinstance(value, list):
        return "[" + ",".join(map(_canonical, value)) + "]"
    if type(value) is float:
        return _number(value)
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def definition_digest(document: dict) -> str:
    require_document(document)
    payload = {key: value for key, value in document.items() if key != "definitionDigest"}
    return "sha256:" + hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


@cache
def _schemas() -> dict[str, StrictValidator]:
    documents = {}
    for name in SCHEMA_NAMES:
        path = files(__package__).joinpath("schemas", name + ".schema.json")
        schema = json.loads(path.read_text(encoding="utf-8"))
        StrictValidator.check_schema(schema)
        documents[name] = schema
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in documents.values()
    )
    return {
        name: StrictValidator(schema, registry=registry, format_checker=FORMAT_CHECKER)
        for name, schema in documents.items()
    }


def validate_document(name: str, document: object) -> None:
    """Validate a packaged schema without retrieving external references."""
    require_document(document)
    try:
        _schemas()[name].validate(document)
    except ValidationError as cause:
        raise WorkflowValidationError("COMPOSITION_SCHEMA_INVALID", pointer(cause.absolute_path)) from cause


_SCHEMA_KEYWORDS = {
    "$schema", "type", "properties", "required", "additionalProperties",
    "items", "minItems", "maxItems", "uniqueItems", "minProperties", "maxProperties",
    "minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "pattern", "enum", "const", "default", "title", "description", "format",
    "allOf", "anyOf", "oneOf", "not", "if", "then", "else",
}


def parameter_schema(schema: dict, path: str) -> StrictValidator:
    """Check the supported inline schema subset and return its instance validator."""
    pending = [(path, schema)]
    while pending:
        location, item = pending.pop()
        if isinstance(item, bool):
            continue
        for keyword in sorted(item.keys() - _SCHEMA_KEYWORDS):
            raise WorkflowValidationError("COMPOSITION_SCHEMA_KEYWORD_UNSUPPORTED", location + pointer([keyword]))
        if "$schema" in item and item["$schema"] != JSON_SCHEMA:
            raise WorkflowValidationError("COMPOSITION_SCHEMA_KEYWORD_UNSUPPORTED", location + "/$schema")
        if "format" in item and item["format"] not in FORMAT_CHECKER.checkers:
            raise WorkflowValidationError("COMPOSITION_SCHEMA_KEYWORD_UNSUPPORTED", location + "/format")
        if "pattern" in item:
            try:
                re.compile(item["pattern"])
            except (TypeError, re.error) as cause:
                raise WorkflowValidationError("COMPOSITION_PARAMETER_SCHEMA_INVALID", location + "/pattern") from cause
        pending.extend((location + pointer(["properties", key]), child) for key, child in item.get("properties", {}).items())
        for key in ("items", "additionalProperties", "not", "if", "then", "else"):
            if key in item:
                pending.append((location + pointer([key]), item[key]))
        for key in ("allOf", "anyOf", "oneOf"):
            pending.extend((location + pointer([key, i]), child) for i, child in enumerate(item.get(key, [])))
        if "default" in item and not StrictValidator(item, format_checker=FORMAT_CHECKER).is_valid(item["default"]):
            raise WorkflowValidationError("COMPOSITION_PARAMETER_DEFAULT_INVALID", location + "/default")
    return StrictValidator(schema, format_checker=FORMAT_CHECKER)


def validate_parameter_schema(schema: dict, path: str) -> StrictValidator:
    try:
        StrictValidator.check_schema(schema)
    except SchemaError as cause:
        raise WorkflowValidationError("COMPOSITION_PARAMETER_SCHEMA_INVALID", path + pointer(cause.absolute_path)) from cause
    return parameter_schema(schema, path)
