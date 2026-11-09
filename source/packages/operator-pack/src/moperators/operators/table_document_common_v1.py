from __future__ import annotations

import csv
import json
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol

import duckdb
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as arrow_csv
import pyarrow.ipc as arrow_ipc
import pyarrow.parquet as parquet
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

MAX_RULE_BYTES = 1024 * 1024


def rule_schemas() -> dict[str, dict[str, Any]]:
    """Return the frozen v1 schemas used both at runtime and for catalog publication."""
    text = {"type": "string", "minLength": 1}
    scalar = {"type": ["string", "number", "boolean", "null"]}
    comparable_scalar = {"type": ["string", "number", "boolean"]}

    def root(name: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
        version = f"table-{name}-rules.v1"
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": version,
            "type": "object",
            "additionalProperties": False,
            "properties": {"schemaVersion": {"const": version}, **properties},
            "required": ["schemaVersion", *required],
        }

    filter_condition = {
        "oneOf": [
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "op": {"enum": ["and", "or"]},
                    "conditions": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 256,
                        "items": {"$ref": "#/$defs/condition"},
                    },
                },
                "required": ["op", "conditions"],
            },
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "op": {"const": "not"},
                    "condition": {"$ref": "#/$defs/condition"},
                },
                "required": ["op", "condition"],
            },
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "op": {"enum": ["eq", "ne", "lt", "lte", "gt", "gte"]},
                    "field": text,
                    "value": comparable_scalar,
                },
                "required": ["op", "field", "value"],
            },
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "op": {"const": "in"},
                    "field": text,
                    "value": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 256,
                        "items": comparable_scalar,
                    },
                },
                "required": ["op", "field", "value"],
            },
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "op": {"const": "between"},
                    "field": text,
                    "minimum": comparable_scalar,
                    "maximum": comparable_scalar,
                },
                "required": ["op", "field", "minimum", "maximum"],
            },
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "op": {"enum": ["isNull", "isNotNull"]},
                    "field": text,
                },
                "required": ["op", "field"],
            },
        ]
    }
    filter_schema = root("filter", {"condition": {"$ref": "#/$defs/condition"}}, ["condition"])
    filter_schema["$defs"] = {"condition": filter_condition}

    constraint_common = {"id": text}

    def constraint(kind: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {**constraint_common, "kind": {"const": kind}, **properties},
            "required": ["id", "kind", *required],
        }

    schemas = {
        "table-project-rules.v1": root(
            "project",
            {
                "columns": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 1024,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["source", "target", "type"],
                        "properties": {
                            "source": text,
                            "target": text,
                            "type": {
                                "enum": [
                                    "string",
                                    "boolean",
                                    "int64",
                                    "float64",
                                    "date",
                                    "timestamp",
                                ]
                            },
                        },
                    },
                }
            },
            ["columns"],
        ),
        "table-filter-rules.v1": filter_schema,
        "table-sort-rules.v1": root(
            "sort",
            {
                "keys": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 64,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["field", "direction", "nulls"],
                        "properties": {
                            "field": text,
                            "direction": {"enum": ["asc", "desc"]},
                            "nulls": {"enum": ["first", "last"]},
                        },
                    },
                }
            },
            ["keys"],
        ),
        "table-deduplicate-rules.v1": root(
            "deduplicate",
            {"keys": {"type": "array", "minItems": 1, "maxItems": 64, "items": text}},
            ["keys"],
        ),
        "table-join-rules.v1": root(
            "join",
            {
                "keys": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 64,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["left", "right"],
                        "properties": {"left": text, "right": text},
                    },
                }
            },
            ["keys"],
        ),
        "table-validation-rules.v1": root(
            "validation",
            {
                "constraints": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 1024,
                    "items": {
                        "oneOf": [
                            constraint("requiredField", {"field": text}, ["field"]),
                            constraint(
                                "type", {"field": text, "dataType": text}, ["field", "dataType"]
                            ),
                            constraint("notNull", {"field": text}, ["field"]),
                            {
                                **constraint(
                                    "range",
                                    {
                                        "field": text,
                                        "minimum": {"type": "number"},
                                        "maximum": {"type": "number"},
                                    },
                                    ["field"],
                                ),
                                "anyOf": [{"required": ["minimum"]}, {"required": ["maximum"]}],
                            },
                            {
                                **constraint(
                                    "length",
                                    {
                                        "field": text,
                                        "minimum": {"type": "integer", "minimum": 0},
                                        "maximum": {"type": "integer", "minimum": 0},
                                    },
                                    ["field"],
                                ),
                                "anyOf": [{"required": ["minimum"]}, {"required": ["maximum"]}],
                            },
                            constraint(
                                "enum",
                                {
                                    "field": text,
                                    "values": {
                                        "type": "array",
                                        "minItems": 1,
                                        "maxItems": 1024,
                                        "items": scalar,
                                    },
                                },
                                ["field", "values"],
                            ),
                            constraint(
                                "unique",
                                {
                                    "fields": {
                                        "type": "array",
                                        "minItems": 1,
                                        "maxItems": 64,
                                        "items": text,
                                    }
                                },
                                ["fields"],
                            ),
                        ]
                    },
                }
            },
            ["constraints"],
        ),
    }
    return schemas


RULE_SCHEMAS = rule_schemas()


class Input(Protocol):
    @property
    def path(self) -> Path: ...

    @property
    def format_id(self) -> str: ...


def one(inputs: Mapping[str, Sequence[Input]], port: str) -> Input:
    values = inputs.get(port, ())
    if len(values) != 1:
        raise ValueError(f"FIELD_REQUIRED: {port}")
    return values[0]


def read_table(source: Input) -> pa.Table:
    if source.format_id == "csv":
        with source.path.open(encoding="utf-8-sig", newline="") as stream:
            header = next(csv.reader(stream), None)
        if (
            not header
            or any(not item.strip() for item in header)
            or len(set(header)) != len(header)
        ):
            raise ValueError("HEADER_INVALID")
        return arrow_csv.read_csv(
            source.path,
            read_options=arrow_csv.ReadOptions(encoding="utf8"),
            convert_options=arrow_csv.ConvertOptions(
                column_types={name: pa.string() for name in header},
                strings_can_be_null=True,
                null_values=[""],
            ),
        )
    if source.format_id == "parquet":
        return parquet.read_table(source.path)
    if source.format_id == "arrow-ipc-file":
        with arrow_ipc.open_file(source.path) as reader:
            return reader.read_all()
    raise ValueError("FORMAT_UNSUPPORTED")


def read_rules(source: Input, schema_version: str) -> dict[str, Any]:
    if source.format_id != "json" or source.path.stat().st_size > MAX_RULE_BYTES:
        raise ValueError("RULESET_INVALID")
    try:
        value = json.loads(source.path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("RULESET_INVALID") from exc
    schema = RULE_SCHEMAS.get(schema_version)
    if schema is None:
        raise ValueError("RULESET_INVALID")
    try:
        Draft202012Validator(schema).validate(value)
    except ValidationError as exc:
        raise ValueError("RULESET_INVALID") from exc
    return value


def require_fields(table: pa.Table, fields: Sequence[str]) -> None:
    if not fields or any(not isinstance(field, str) or not field for field in fields):
        raise ValueError("RULESET_INVALID")
    if any(field not in table.column_names for field in fields):
        raise ValueError("FIELD_REQUIRED")


def write_table(table: pa.Table, path: Path) -> dict[str, Any]:
    parquet.write_table(
        table.replace_schema_metadata(None),
        path,
        compression="zstd",
        version="2.6",
        write_statistics=True,
    )
    return {
        "kind": "artifact",
        "path": str(path),
        "fileName": path.name,
        "mediaType": "application/vnd.apache.parquet",
        "format": "parquet",
    }


def write_markdown(text: str, path: Path) -> dict[str, Any]:
    path.write_text(text, encoding="utf-8", newline="\n")
    return {
        "kind": "artifact",
        "path": str(path),
        "fileName": path.name,
        "mediaType": "text/markdown",
        "format": "markdown",
    }


def summary(value: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "value", "value": value}


def connection(output_dir: Path) -> duckdb.DuckDBPyConnection:
    database = duckdb.connect(config={"threads": 1})
    escaped = str(output_dir).replace("'", "''")
    database.execute(f"SET temp_directory='{escaped}'")
    return database


def identifier(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("RULESET_INVALID")
    return '"' + value.replace('"', '""') + '"'


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def table_with_ordinal(table: pa.Table, name: str = "__moperators_ordinal") -> pa.Table:
    if name in table.column_names:
        raise ValueError("OUTPUT_FIELD_CONFLICT")
    return table.append_column(name, pa.array(range(table.num_rows), type=pa.int64()))


def strict_cast(array: pa.ChunkedArray, target: str) -> pa.ChunkedArray:
    types: dict[str, pa.DataType] = {
        "string": pa.string(),
        "boolean": pa.bool_(),
        "int64": pa.int64(),
        "float64": pa.float64(),
        "date": pa.date32(),
        "timestamp": pa.timestamp("us"),
    }
    data_type = types.get(target)
    if data_type is None:
        raise ValueError("RULESET_INVALID")
    try:
        result = pc.cast(array, data_type, safe=True)
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError, ValueError) as exc:
        raise ValueError("TYPE_CAST_FAILED") from exc
    return pa.chunked_array([result]) if isinstance(result, pa.Array) else result


def json_scalar(value: Any) -> bool:
    return value is None or (
        isinstance(value, (str, bool, int, float))
        and not (isinstance(value, float) and not math.isfinite(value))
    )


def comparable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value
