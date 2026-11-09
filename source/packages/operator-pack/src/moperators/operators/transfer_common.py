"""File contracts shared by the statically bound supervised-learning operators."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from jsonschema import Draft202012Validator, ValidationError

MEDIA = {
    "parquet": "application/vnd.apache.parquet",
    "json": "application/json",
    "npy": "application/x-npy",
    "pytorch-state-dict": "application/vnd.pytorch",
    "geopackage": "application/geopackage+sqlite3",
    "geotiff": "image/tiff",
}


def object_schema(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def rule_schemas():
    text = {"type": "string", "minLength": 1}
    fields = {"type": "array", "items": text, "minItems": 1, "maxItems": 1024, "uniqueItems": True}
    bodies = {
        "table-timeseries-unpivot.v1": {
            "idFields": fields,
            "uniqueKeys": fields,
            "series": {
                "type": "array",
                "minItems": 1,
                "maxItems": 64,
                "items": object_schema(
                    {
                        "field": text,
                        "columns": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 10000,
                            "items": object_schema({"time": text, "column": text}),
                        },
                    }
                ),
            },
        },
        "standard-scaler-fields.v1": {"fields": fields},
        "supervised-window-fields.v1": {"featureFields": fields, "carryFields": fields},
        "raster-window-bands.v1": {
            "bands": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
                "minItems": 1,
                "maxItems": 64,
                "uniqueItems": True,
            }
        },
        "standard-scaler.v1": {
            "features": {
                "type": "array",
                "minItems": 1,
                "maxItems": 1024,
                "items": object_schema(
                    {
                        "field": text,
                        "mean": {"type": "number"},
                        "variance": {"type": "number", "minimum": 0},
                        "scale": {"type": "number", "exclusiveMinimum": 0},
                        "count": {"type": "integer", "minimum": 1},
                    }
                ),
            }
        },
        "gru-attention-model.v1": {
            **{
                name: {"type": "integer", "minimum": 1}
                for name in ("inputWindow", "inputFeatures", "hiddenSize", "numLayers", "numHeads")
            },
            "dropout": {"type": "number", "minimum": 0, "exclusiveMaximum": 1},
        },
    }
    return {
        name: {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": name,
            **object_schema({"schemaVersion": {"const": name}, **body}),
        }
        for name, body in bodies.items()
    }


def one(inputs, port):
    items = inputs[port]
    if len(items) != 1:
        raise ValueError("INPUT_CARDINALITY_INVALID")
    return items[0].path


def read_json(path, schema_id):
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("RULESET_TOO_LARGE")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator(rule_schemas()[schema_id]).validate(value)
    except (UnicodeError, json.JSONDecodeError, ValidationError) as error:
        raise ValueError("RULESET_INVALID") from error
    return value


def table(inputs, port="data"):
    import pyarrow.parquet as pq

    result = pq.read_table(one(inputs, port))
    if len(result.column_names) != len(set(result.column_names)):
        raise ValueError("DUPLICATE_FIELD")
    return result


def numeric(data, field, dtype=np.float32):
    import pyarrow as pa

    if field not in data.column_names:
        raise ValueError("FIELD_REQUIRED")
    column = data[field]
    if not (pa.types.is_integer(column.type) or pa.types.is_floating(column.type)):
        raise ValueError("NON_NUMERIC_VALUE")
    values = np.asarray(column.to_numpy(), dtype=dtype)
    if column.null_count or not np.isfinite(values).all():
        raise ValueError("NON_NUMERIC_VALUE")
    return values


def artifact(path: Path, format_id: str):
    return {
        "kind": "artifact",
        "path": str(path),
        "mediaType": MEDIA[format_id],
        "format": format_id,
    }


def write_table(data, path):
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(data, path, compression="zstd", version="2.6")
    return artifact(path, "parquet")


def write_json(value, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ),
        encoding="utf-8",
    )
    return artifact(path, "json")


def write_array(value, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        np.save(stream, value, allow_pickle=False)
    return artifact(path, "npy")


def array(inputs, port, dimensions):
    try:
        value = np.load(one(inputs, port), allow_pickle=False)
    except (ValueError, EOFError) as error:
        raise ValueError("TENSOR_SHAPE_INVALID") from error
    if not isinstance(value, np.ndarray):
        value.close()
        raise ValueError("TENSOR_SHAPE_INVALID")
    if value.dtype != np.float32 or value.ndim != dimensions or not value.size:
        raise ValueError("TENSOR_SHAPE_INVALID")
    if not np.isfinite(value).all():
        raise ValueError("NON_NUMERIC_VALUE")
    return value
