from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from math import ceil, isclose, isfinite, sqrt
from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr

import numpy as np

from moperators.operators.expressions import evaluate_expression

VECTOR_FORMATS = {"geopackage", "geojson", "flatgeobuf", "geoparquet"}
RASTER_FORMATS = {"geotiff", "cog"}
POINT_FORMATS = {"las", "laz", "copc"}
TABLE_FORMATS = {"csv", "json", "parquet", "arrow-ipc-file"}
OUTPUT_DETAILS = {
    "geopackage": ("output.gpkg", "application/geopackage+sqlite3", "GPKG"),
    "geojson": ("output.geojson", "application/geo+json", "GeoJSON"),
    "flatgeobuf": ("output.fgb", "application/flatgeobuf", "FlatGeobuf"),
    "geoparquet": ("output.parquet", "application/vnd.apache.parquet", "Parquet"),
    "geotiff": ("output.tif", "image/tiff", "GTiff"),
    "cog": ("output.tif", "image/tiff", "COG"),
    "las": ("output.las", "application/vnd.las", "las"),
    "laz": ("output.laz", "application/vnd.laz", "laz"),
    "copc": ("output.copc.laz", "application/vnd.laz", "copc"),
    "csv": ("output.csv", "text/csv", "csv"),
    "json": ("output.json", "application/json", "json"),
    "parquet": ("output.parquet", "application/vnd.apache.parquet", "parquet"),
    "arrow-ipc-file": ("output.arrow", "application/vnd.apache.arrow.file", "arrow"),
}


@dataclass(frozen=True)
class StagedInput:
    path: Path
    media_type: str
    format_id: str
    metadata: dict[str, Any]


def _inspect(item: StagedInput, layer: str | None = None) -> dict[str, Any]:
    if item.format_id == "json":
        value = json.loads(item.path.read_text(encoding="utf-8"))
        if isinstance(value, list):
            fields = sorted({key for row in value if isinstance(row, dict) for key in row})
            return {"format": "json", "recordCount": len(value), "fields": fields}
        return {"format": "json", "valueType": type(value).__name__}
    if item.format_id == "csv":
        with item.path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            count = sum(1 for _ in reader)
            return {
                "format": "csv",
                "recordCount": count,
                "fields": reader.fieldnames or [],
            }
    if item.format_id == "npy":
        array = np.load(item.path, mmap_mode="r", allow_pickle=False)
        return {
            "format": "npy",
            "shape": list(array.shape),
            "dataType": str(array.dtype),
        }
    metadata = dict(item.metadata)
    if layer and metadata.get("layers"):
        selected = [value for value in metadata["layers"] if value.get("name") == layer]
        if not selected:
            raise ValueError("LAYER_REQUIRED")
        metadata["layers"] = selected
    return {"format": item.format_id, **metadata}


SUMMARY_GROUP_LIMIT = 100


def _describe(
    values: np.ndarray,
    *,
    row_count: int,
    null_count: int,
    nonfinite_count: int,
) -> dict[str, int | float | None]:
    empty_metrics = {
        "min": None,
        "max": None,
        "mean": None,
        "stddev": None,
        "q1": None,
        "median": None,
        "q3": None,
    }
    if values.size == 0:
        return {
            "rowCount": row_count,
            "count": 0,
            "nullCount": null_count,
            "nonFiniteCount": nonfinite_count,
            **empty_metrics,
        }
    return {
        "rowCount": row_count,
        "count": int(values.size),
        "nullCount": null_count,
        "nonFiniteCount": nonfinite_count,
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "mean": float(np.mean(values)),
        "stddev": float(np.std(values)),
        "q1": float(np.quantile(values, 0.25)),
        "median": float(np.quantile(values, 0.5)),
        "q3": float(np.quantile(values, 0.75)),
    }


def _declared_nulls(item: StagedInput) -> frozenset[str]:
    metadata = item.metadata
    if not isinstance(metadata, dict):
        raise ValueError("INPUT_METADATA_INVALID")
    contract = metadata.get("dataContract", {})
    if not isinstance(contract, dict):
        raise ValueError("INPUT_METADATA_INVALID")
    values = contract.get("nullValues", [])
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ValueError("INPUT_METADATA_INVALID")
    return frozenset(values)


def _is_declared_null(value: Any, null_values: frozenset[str]) -> bool:
    return value is None or (
        isinstance(value, str)
        and (not value.strip() or value in null_values or value.strip() in null_values)
    )


def _numeric_values(
    values: list[Any] | np.ndarray,
    *,
    null_values: frozenset[str] = frozenset(),
    allow_numeric_strings: bool = False,
) -> tuple[np.ndarray, int, int]:
    numbers: list[float] = []
    null_count = 0
    nonfinite_count = 0
    for value in values:
        if _is_declared_null(value, null_values):
            null_count += 1
            continue
        if isinstance(value, str):
            if not allow_numeric_strings:
                raise ValueError("NON_NUMERIC_VALUE")
            try:
                number = float(value.strip())
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("NON_NUMERIC_VALUE") from exc
        else:
            if isinstance(value, bool) or not isinstance(
                value,
                (int, float, Decimal, np.integer, np.floating),
            ):
                raise ValueError("NON_NUMERIC_VALUE")
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("NON_NUMERIC_VALUE") from exc
        if not isfinite(number):
            nonfinite_count += 1
        else:
            numbers.append(number)
    return np.asarray(numbers, dtype=np.float64), null_count, nonfinite_count


def _statistics_values(
    values: list[Any] | np.ndarray,
    *,
    null_values: frozenset[str] = frozenset(),
    allow_numeric_strings: bool = False,
) -> dict[str, int | float | None]:
    numbers, null_count, nonfinite_count = _numeric_values(
        values,
        null_values=null_values,
        allow_numeric_strings=allow_numeric_strings,
    )
    return _describe(
        numbers,
        row_count=len(values),
        null_count=null_count,
        nonfinite_count=nonfinite_count,
    )


def _table_rows(item: StagedInput) -> tuple[list[dict[str, Any]], list[str]]:
    if item.format_id == "csv":
        with item.path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            fields = reader.fieldnames or []
            return [dict(row) for row in reader], fields
    if item.format_id == "json":
        rows = json.loads(item.path.read_text(encoding="utf-8"))
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("INPUT_INVALID")
        fields = list(dict.fromkeys(key for row in rows for key in row))
        return rows, fields
    if item.format_id in {"parquet", "geoparquet", "arrow-ipc-file"}:
        try:
            import pyarrow.ipc as arrow_ipc
            import pyarrow.parquet as parquet
        except ImportError as exc:
            raise ValueError("BLOCKED_BY_ENVIRONMENT") from exc
        if item.format_id in {"parquet", "geoparquet"}:
            table = parquet.read_table(item.path)
        else:
            with arrow_ipc.open_file(item.path) as reader:
                table = reader.read_all()
        return table.to_pylist(), table.column_names
    raise ValueError("FORMAT_UNSUPPORTED")


def _group_key(value: Any, null_values: frozenset[str]) -> tuple[tuple[str, Any], dict[str, Any]]:
    if _is_declared_null(value, null_values):
        return ("null", None), {"type": "null", "value": None}
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, bool):
        return ("boolean", value), {"type": "boolean", "value": value}
    if isinstance(value, int):
        return ("integer", value), {"type": "integer", "value": value}
    if isinstance(value, (float, Decimal)):
        number = float(value)
        if not isfinite(number):
            raise ValueError("GROUP_VALUE_INVALID")
        return ("number", number), {"type": "number", "value": number}
    if isinstance(value, str):
        return ("string", value), {"type": "string", "value": value}
    if isinstance(value, (date, datetime)):
        normalized = value.isoformat()
        return ("date-time", normalized), {"type": "date-time", "value": normalized}
    raise ValueError("GROUP_VALUE_INVALID")


def _summary_details(
    item: StagedInput,
    parameters: dict[str, Any],
    *,
    source_count: int,
    source_count_kind: str,
    series: list[dict[str, Any]],
) -> dict[str, Any]:
    unit = parameters.get("unit", "")
    if not isinstance(unit, str) or len(unit) > 64:
        raise ValueError("PARAMETER_INVALID")
    normalized_unit = unit.strip() or None
    return {
        "schemaVersion": "statistics-describe-details.v1",
        "scope": {
            "basis": "FULL_STAGED_INPUT",
            "inputFormat": item.format_id,
            "sourceCount": source_count,
            "sourceCountKind": source_count_kind,
        },
        "selection": {
            "axis": parameters.get("axis", "columns"),
            "column": parameters.get("column") or None,
            "groupField": parameters.get("groupField") or None,
            "unit": normalized_unit,
        },
        "series": series,
        "conventions": {
            "declaredNulls": "excluded-and-counted",
            "nonFiniteValues": "excluded-and-counted",
            "stddev": "population-ddof-0",
            "quantiles": "linear-interpolation",
        },
        "interpretation": ("Descriptive statistics for the full staged input artifact."),
        "limitations": [
            (
                "FULL_STAGED_INPUT describes the staged file or array, not a preview sample, "
                "but it does not establish representativeness of a wider population."
            ),
            (
                "The unit is an explicit label only; no unit conversion or field-meaning "
                "inference is performed."
            ),
            (
                "Grouped summaries are descriptive partitions and do not establish "
                "statistical significance or causal differences."
            ),
        ],
    }


def _table_statistics(
    item: StagedInput, parameters: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    column = parameters.get("column")
    if not isinstance(column, str) or not column:
        raise ValueError("COLUMN_REQUIRED")
    group_field = parameters.get("groupField")
    if group_field is not None and (not isinstance(group_field, str) or not group_field):
        raise ValueError("GROUP_COLUMN_REQUIRED")
    rows, fields = _table_rows(item)
    if column not in fields:
        raise ValueError("COLUMN_REQUIRED")
    if group_field is not None and group_field not in fields:
        raise ValueError("GROUP_COLUMN_REQUIRED")
    null_values = _declared_nulls(item)
    allow_numeric_strings = item.format_id == "csv"
    selected = [row.get(column) for row in rows]
    statistics = _statistics_values(
        selected,
        null_values=null_values,
        allow_numeric_strings=allow_numeric_strings,
    )
    if statistics["count"] == 0:
        raise ValueError("NO_FINITE_VALUES")

    groups: list[dict[str, Any]] = []
    if group_field is not None:
        group_values: dict[tuple[str, Any], tuple[dict[str, Any], list[Any]]] = {}
        for row in rows:
            identity, key = _group_key(row.get(group_field), null_values)
            if identity not in group_values:
                if len(group_values) >= SUMMARY_GROUP_LIMIT:
                    raise ValueError("GROUP_LIMIT_EXCEEDED")
                group_values[identity] = (key, [])
            group_values[identity][1].append(row.get(column))
        groups = [
            {
                "key": key,
                "statistics": _statistics_values(
                    values,
                    null_values=null_values,
                    allow_numeric_strings=allow_numeric_strings,
                ),
            }
            for key, values in group_values.values()
        ]

    series = [
        {
            "field": {"kind": "table-column", "name": column},
            "statistics": statistics,
            "groups": groups,
        }
    ]
    return {column: statistics}, _summary_details(
        item,
        parameters,
        source_count=len(rows),
        source_count_kind="rows",
        series=series,
    )


def _array_statistics(
    item: StagedInput, parameters: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    if parameters.get("groupField"):
        raise ValueError("GROUP_FIELD_UNSUPPORTED")
    if parameters.get("column"):
        raise ValueError("COLUMN_UNSUPPORTED")
    array = np.load(item.path, mmap_mode="r", allow_pickle=False)
    if not np.issubdtype(array.dtype, np.number) or np.issubdtype(array.dtype, np.complexfloating):
        raise ValueError("NON_NUMERIC_VALUE")
    axis = parameters.get("axis", "columns")
    if axis not in {"columns", "flatten"}:
        raise ValueError("PARAMETER_INVALID")
    if axis == "flatten" or array.ndim == 1:
        columns = [("value", "array-values", np.asarray(array).reshape(-1))]
    elif array.ndim == 2:
        columns = [
            (str(index), "array-column", np.asarray(array[:, index]))
            for index in range(array.shape[1])
        ]
    else:
        raise ValueError("INPUT_INVALID")
    summary: dict[str, Any] = {}
    series: list[dict[str, Any]] = []
    for name, kind, values in columns:
        statistics = _statistics_values(values)
        summary[name] = statistics
        series.append(
            {
                "field": {"kind": kind, "name": name},
                "statistics": statistics,
                "groups": [],
            }
        )
    if not series or not any(item["statistics"]["count"] for item in series):
        raise ValueError("NO_FINITE_VALUES")
    return summary, _summary_details(
        item,
        parameters,
        source_count=int(array.size),
        source_count_kind="elements",
        series=series,
    )


def _statistics(
    item: StagedInput, parameters: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    if item.format_id == "npy":
        return _array_statistics(item, parameters)
    return _table_statistics(item, parameters)


def _artifact(path: Path, media_type: str, format_id: str) -> dict[str, Any]:
    return {
        "kind": "artifact",
        "path": str(path),
        "fileName": path.name,
        "mediaType": media_type,
        "format": format_id,
    }


def _run(command: list[str], timeout: int = 21600, cwd: Path | None = None) -> None:
    if shutil.which(command[0]) is None:
        raise ValueError("BLOCKED_BY_ENVIRONMENT")
    try:
        subprocess.run(
            command,
            check=True,
            timeout=timeout,
            capture_output=True,
            text=True,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("EXECUTION_TIMEOUT") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip()[-1000:] if exc.stderr else ""
        raise ValueError(f"OPERATOR_FAILED: {detail}") from exc


def _run_pdal_pipeline(stages: list[dict[str, Any]], path: Path) -> None:
    path.write_text(json.dumps({"pipeline": stages}, separators=(",", ":")), encoding="utf-8")
    _run(["pdal", "pipeline", str(path)])


def _json_lossless(value: Any) -> None:
    if value is None or type(value) in {str, bool}:
        return
    if type(value) is int:
        if abs(value) > (1 << 53) - 1:
            raise ValueError("CONVERSION_NOT_ALLOWED")
        return
    if type(value) is float:
        if not isfinite(value):
            raise ValueError("CONVERSION_NOT_ALLOWED")
        return
    if type(value) is list:
        for item in value:
            _json_lossless(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json_lossless(item)
        return
    raise ValueError("CONVERSION_NOT_ALLOWED")


def _json_arrow_type_lossless(data_type: Any, arrow: Any) -> bool:
    kinds = arrow.types
    if (
        kinds.is_null(data_type)
        or kinds.is_boolean(data_type)
        or kinds.is_integer(data_type)
        or kinds.is_floating(data_type)
        or kinds.is_string(data_type)
        or kinds.is_large_string(data_type)
    ):
        return True
    if (
        kinds.is_list(data_type)
        or kinds.is_large_list(data_type)
        or kinds.is_fixed_size_list(data_type)
    ):
        return _json_arrow_type_lossless(data_type.value_type, arrow)
    if kinds.is_struct(data_type):
        names = [field.name for field in data_type]
        return len(names) == len(set(names)) and all(
            _json_arrow_type_lossless(field.type, arrow) for field in data_type
        )
    return False


def _table_convert(source: StagedInput, output_format: str, output: Path) -> None:
    if source.format_id in {"parquet", "arrow-ipc-file"}:
        try:
            import pyarrow as pa
            import pyarrow.ipc as arrow_ipc
            import pyarrow.parquet as parquet
        except ImportError as exc:
            raise ValueError("BLOCKED_BY_ENVIRONMENT") from exc
        if source.format_id == "parquet":
            reader = parquet.ParquetFile(source.path)
            schema = reader.schema_arrow
            batches = reader.iter_batches(batch_size=65536)
        else:
            reader = arrow_ipc.open_file(source.path)
            schema = reader.schema
            batches = (reader.get_batch(index) for index in range(reader.num_record_batches))
        if output_format in {"json", "csv"} and len(schema.names) != len(set(schema.names)):
            raise ValueError("CONVERSION_NOT_ALLOWED")
        if output_format == "json" and not all(
            _json_arrow_type_lossless(field.type, pa) for field in schema
        ):
            raise ValueError("CONVERSION_NOT_ALLOWED")
        if output_format == "parquet":
            with parquet.ParquetWriter(output, schema) as writer:
                for batch in batches:
                    writer.write_batch(batch)
        else:
            if output_format == "arrow-ipc-file":
                with arrow_ipc.new_file(output, schema) as writer:
                    for batch in batches:
                        writer.write_batch(batch)
            elif output_format == "json":
                with output.open("w", encoding="utf-8") as stream:
                    stream.write("[")
                    first = True
                    for batch in batches:
                        for row in batch.to_pylist():
                            _json_lossless(row)
                            try:
                                value = json.dumps(
                                    row,
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                    allow_nan=False,
                                )
                            except (TypeError, ValueError) as exc:
                                raise ValueError("CONVERSION_NOT_ALLOWED") from exc
                            if not first:
                                stream.write(",")
                            stream.write(value)
                            first = False
                    stream.write("]")
            elif output_format == "csv":
                with output.open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=sorted(schema.names))
                    writer.writeheader()
                    for batch in batches:
                        writer.writerows(batch.to_pylist())
            else:
                raise ValueError("CONVERSION_NOT_ALLOWED")
        return
    if source.format_id == "csv":
        with source.path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
    elif source.format_id == "json":
        rows = json.loads(source.path.read_text(encoding="utf-8"))
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("INPUT_INVALID")
    else:
        raise ValueError("FORMAT_UNSUPPORTED")
    if output_format == "json":
        try:
            content = json.dumps(
                rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("CONVERSION_NOT_ALLOWED") from exc
        output.write_text(content, encoding="utf-8")
        return
    if output_format == "csv":
        fieldnames = sorted({key for row in rows for key in row})
        with output.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return
    try:
        import pyarrow as pa
        import pyarrow.ipc as arrow_ipc
        import pyarrow.parquet as parquet
    except ImportError as exc:
        raise ValueError("BLOCKED_BY_ENVIRONMENT") from exc
    table = pa.Table.from_pylist(rows)
    if output_format == "parquet":
        parquet.write_table(table, output)
    elif output_format == "arrow-ipc-file":
        with arrow_ipc.new_file(output, table.schema) as writer:
            writer.write_table(table)
    else:
        raise ValueError("CONVERSION_NOT_ALLOWED")


def _convert(source: StagedInput, parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    output_format = parameters["outputFormat"]
    details = OUTPUT_DETAILS.get(output_format)
    if details is None:
        raise ValueError("FORMAT_UNSUPPORTED")
    file_name, media_type, driver = details
    output = output_dir / file_name
    source_family = next(
        (
            family
            for family in (VECTOR_FORMATS, RASTER_FORMATS, POINT_FORMATS, TABLE_FORMATS)
            if source.format_id in family
        ),
        None,
    )
    if source_family is None or output_format not in source_family:
        raise ValueError("CONVERSION_NOT_ALLOWED")
    if source_family is TABLE_FORMATS:
        _table_convert(source, output_format, output)
    elif source_family is VECTOR_FORMATS:
        command = ["ogr2ogr", "-f", driver, str(output), str(source.path)]
        if output_format == "geojson":
            command.extend(["-lco", "RFC7946=YES"])
        if layer := parameters.get("layer"):
            command.append(layer)
        if output_layer := parameters.get("outputLayer"):
            command.extend(["-nln", output_layer])
        _run(command)
    elif source_family is RASTER_FORMATS:
        _run(["gdal_translate", "-of", driver, str(source.path), str(output)])
    else:
        writer = {
            "type": "writers.copc" if output_format == "copc" else "writers.las",
            "filename": str(output),
        }
        if output_format == "laz":
            writer["compression"] = "laszip"
        _run_pdal_pipeline(
            [{"type": "readers.las", "filename": str(source.path)}, writer],
            output_dir / "convert-pdal.json",
        )
    return _artifact(output, media_type, output_format)


def _layer(item: StagedInput, requested: str | None) -> str:
    if requested:
        layer = requested
        known = {
            value["name"] if isinstance(value, dict) else value
            for value in item.metadata.get("layers", [])
        }
        if known and layer not in known:
            raise ValueError("LAYER_REQUIRED")
    else:
        layers = item.metadata.get("layers", [])
        if len(layers) != 1:
            raise ValueError("LAYER_REQUIRED")
        layer = layers[0]["name"] if isinstance(layers[0], dict) else layers[0]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", layer):
        raise ValueError("LAYER_INVALID")
    return layer


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _buffer(item: StagedInput, parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    if item.metadata.get("hasZ") or item.metadata.get("hasM"):
        raise ValueError("UNSUPPORTED_DIMENSION")
    if item.metadata.get("crsUnit") != "metre":
        raise ValueError("PROJECTED_CRS_REQUIRED")
    layer = _layer(item, parameters.get("layer"))
    distance = float(parameters["distanceMeters"])
    segments = int(parameters.get("segments", 8))
    output = output_dir / "buffer.gpkg"
    try:
        import pyogrio
    except ImportError as exc:
        raise ValueError("BLOCKED_BY_ENVIRONMENT") from exc
    info = pyogrio.read_info(item.path, layer=layer)
    geometry = str(info["geometry_name"])
    fields = [str(value) for value in info.get("fields", [])]
    geometry_sql = _quote_identifier(geometry)
    selected = ", ".join(_quote_identifier(value) for value in fields)
    columns = f", {selected}" if selected else ""
    sql = (
        f"SELECT ST_Buffer({geometry_sql}, {distance}, {segments}) "
        f"AS {geometry_sql}{columns} FROM {_quote_identifier(layer)}"
    )
    _run(
        [
            "ogr2ogr",
            "-f",
            "GPKG",
            str(output),
            str(item.path),
            "-dialect",
            "SQLITE",
            "-sql",
            sql,
            "-nln",
            "buffer",
        ]
    )
    return _artifact(output, "application/geopackage+sqlite3", "geopackage")


def _idw(points: StagedInput, parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    source_path = points.path
    source_layer = parameters.get("layer")
    if points.format_id in TABLE_FORMATS:
        x_field = parameters.get("xField")
        y_field = parameters.get("yField")
        source_crs = parameters.get("sourceCrs")
        if not x_field or not y_field:
            raise ValueError("FIELD_REQUIRED")
        if not source_crs:
            raise ValueError("CRS_REQUIRED")
        csv_path = output_dir / "points-source.csv"
        _table_convert(points, "csv", csv_path)
        vrt_path = output_dir / "points-source.vrt"
        vrt_path.write_text(
            '<OGRVRTDataSource><OGRVRTLayer name="points">'
            f"<SrcDataSource>{csv_path}</SrcDataSource>"
            "<SrcLayer>points-source</SrcLayer>"
            "<GeometryType>wkbPoint</GeometryType>"
            f"<LayerSRS>{source_crs}</LayerSRS>"
            f'<GeometryField encoding="PointFromColumns" x={quoteattr(x_field)} '
            f"y={quoteattr(y_field)}/></OGRVRTLayer></OGRVRTDataSource>",
            encoding="utf-8",
        )
        source_path = vrt_path
        source_layer = "points"
    else:
        source_layer = _layer(points, source_layer)

    projected = output_dir / "idw-source.gpkg"
    _run(
        [
            "ogr2ogr",
            "-f",
            "GPKG",
            "-t_srs",
            parameters["targetCrs"],
            "-nln",
            "points",
            str(projected),
            str(source_path),
            source_layer,
        ]
    )
    try:
        import pyogrio
    except ImportError as exc:
        raise ValueError("BLOCKED_BY_ENVIRONMENT") from exc
    info = pyogrio.read_info(projected, layer="points")
    if not info.get("features"):
        raise ValueError("EMPTY_INTERPOLATION_INPUT")
    value_field = parameters["valueField"]
    if value_field not in info.get("fields", []):
        raise ValueError("FIELD_REQUIRED")
    extent_keys = ["extentMinX", "extentMinY", "extentMaxX", "extentMaxY"]
    present = [key in parameters for key in extent_keys]
    if any(present) and not all(present):
        raise ValueError("EXTENT_INCOMPLETE")
    if all(present):
        min_x, min_y, max_x, max_y = (float(parameters[key]) for key in extent_keys)
    else:
        bounds = info.get("total_bounds")
        if not bounds or len(bounds) != 4:
            raise ValueError("EMPTY_INTERPOLATION_INPUT")
        min_x, min_y, max_x, max_y = (float(value) for value in bounds)
    pixel_size = float(parameters["pixelSize"])
    if max_x <= min_x:
        min_x -= pixel_size / 2
        max_x += pixel_size / 2
    if max_y <= min_y:
        min_y -= pixel_size / 2
        max_y += pixel_size / 2
    width = max(1, ceil((max_x - min_x) / pixel_size))
    height = max(1, ceil((max_y - min_y) / pixel_size))
    algorithm = (
        f"invdist:power={parameters.get('power', 2)}:"
        f"radius1={parameters.get('searchRadius') or 0}:"
        f"radius2={parameters.get('searchRadius') or 0}:"
        f"max_points={parameters.get('neighborCount', 12)}:nodata=-9999"
    )
    output = output_dir / "idw.tif"
    _run(
        [
            "gdal_grid",
            "-l",
            "points",
            "-zfield",
            value_field,
            "-a",
            algorithm,
            "-txe",
            str(min_x),
            str(max_x),
            "-tye",
            str(min_y),
            str(max_y),
            "-outsize",
            str(width),
            str(height),
            "-a_srs",
            parameters["targetCrs"],
            "-of",
            "GTiff",
            str(projected),
            str(output),
        ]
    )
    return _artifact(output, "image/tiff", "geotiff")


def _raster_algebra(
    rasters: list[StagedInput], parameters: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    try:
        from osgeo import gdal  # ty: ignore[unresolved-import]
    except (ImportError, ModuleNotFoundError) as exc:
        raise ValueError("BLOCKED_BY_ENVIRONMENT") from exc
    datasets = [gdal.Open(str(item.path), gdal.GA_ReadOnly) for item in rasters]
    if any(dataset is None for dataset in datasets):
        raise ValueError("INPUT_INVALID")
    first = datasets[0]
    grid = (first.RasterXSize, first.RasterYSize, first.GetGeoTransform(), first.GetProjection())
    if any(
        (
            dataset.RasterXSize,
            dataset.RasterYSize,
            dataset.GetGeoTransform(),
            dataset.GetProjection(),
        )
        != grid
        for dataset in datasets[1:]
    ):
        raise ValueError("GRID_MISMATCH")
    data_types = {
        "uint8": gdal.GDT_Byte,
        "int16": gdal.GDT_Int16,
        "uint16": gdal.GDT_UInt16,
        "int32": gdal.GDT_Int32,
        "uint32": gdal.GDT_UInt32,
        "float32": gdal.GDT_Float32,
        "float64": gdal.GDT_Float64,
    }
    output = output_dir / "algebra.tif"
    target = gdal.GetDriverByName("GTiff").Create(
        str(output),
        first.RasterXSize,
        first.RasterYSize,
        1,
        data_types[parameters["outputDataType"]],
        options=["TILED=YES", "COMPRESS=DEFLATE"],
    )
    target.SetGeoTransform(first.GetGeoTransform())
    target.SetProjection(first.GetProjection())
    output_band = target.GetRasterBand(1)
    output_nodata = parameters.get("noData")
    if output_nodata is None:
        output_nodata = first.GetRasterBand(1).GetNoDataValue()
    if output_nodata is None and parameters["outputDataType"].startswith("float"):
        output_nodata = float("nan")
    if output_nodata is not None:
        output_band.SetNoDataValue(output_nodata)
    for y_offset in range(0, first.RasterYSize, 512):
        height = min(512, first.RasterYSize - y_offset)
        for x_offset in range(0, first.RasterXSize, 512):
            width = min(512, first.RasterXSize - x_offset)
            variables: dict[str, np.ndarray] = {}
            invalid = np.zeros((height, width), dtype=bool)
            for raster_index, dataset in enumerate(datasets, start=1):
                for band_index in range(1, dataset.RasterCount + 1):
                    band = dataset.GetRasterBand(band_index)
                    values = band.ReadAsArray(x_offset, y_offset, width, height)
                    variables[f"r{raster_index}_b{band_index}"] = values
                    nodata = band.GetNoDataValue()
                    if nodata is not None:
                        invalid |= np.isnan(values) if np.isnan(nodata) else values == nodata
            result = evaluate_expression(parameters["expression"], variables)
            result = np.broadcast_to(result, (height, width)).copy()
            if output_nodata is not None:
                result[invalid] = output_nodata
            output_band.WriteArray(result, x_offset, y_offset)
    output_band.FlushCache()
    target.FlushCache()
    target = None
    return _artifact(output, "image/tiff", "geotiff")


def _zonal_statistics(
    raster: StagedInput,
    zones: StagedInput,
    parameters: dict[str, Any],
    output_dir: Path,
) -> dict[str, dict[str, Any]]:
    if raster.metadata.get("crs") != zones.metadata.get("crs"):
        raise ValueError("CRS_MISMATCH")
    try:
        import pyarrow as pa
        import pyarrow.parquet as parquet
        from rasterstats import zonal_stats
    except (ImportError, ModuleNotFoundError) as exc:
        raise ValueError("BLOCKED_BY_ENVIRONMENT") from exc
    layer = parameters.get("zonesLayer")
    if zones.format_id == "geopackage":
        layer = _layer(zones, layer)
    statistics = zonal_stats(
        str(zones.path),
        str(raster.path),
        layer=layer,
        band=parameters.get("band", 1),
        stats=["count", "min", "max", "mean", "sum", "std"],
        geojson_out=parameters.get("outputKind", "table") == "vector",
    )
    for item in statistics:
        values = item.get("properties", item)
        if "std" in values:
            values["stddev"] = values.pop("std")
    summary = {
        "zoneCount": len(statistics),
        "emptyZoneCount": sum(
            item.get("properties", item).get("count", 0) == 0 for item in statistics
        ),
    }
    if parameters.get("outputKind", "table") == "vector":
        geojson = output_dir / "zonal.geojson"
        geojson.write_text(
            json.dumps({"type": "FeatureCollection", "features": statistics}, ensure_ascii=False),
            encoding="utf-8",
        )
        output = output_dir / "zonal.gpkg"
        _run(
            [
                "ogr2ogr",
                "-f",
                "GPKG",
                "-a_srs",
                zones.metadata["crs"],
                str(output),
                str(geojson),
                "-nln",
                "zonal_statistics",
            ]
        )
        artifact = _artifact(output, "application/geopackage+sqlite3", "geopackage")
    else:
        output = output_dir / "zonal.parquet"
        schema = pa.schema(
            [
                ("count", pa.int64()),
                ("min", pa.float64()),
                ("max", pa.float64()),
                ("mean", pa.float64()),
                ("sum", pa.float64()),
                ("stddev", pa.float64()),
            ]
        )
        parquet.write_table(pa.Table.from_pylist(statistics, schema=schema), output)
        artifact = _artifact(output, "application/vnd.apache.parquet", "parquet")
    return {
        "statistics": artifact,
        "summary": {"kind": "value", "value": summary},
    }


def _resample(raster: StagedInput, parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    output = output_dir / "resampled.tif"
    _run(
        [
            "gdalwarp",
            "-tr",
            str(parameters["pixelSizeX"]),
            str(parameters["pixelSizeY"]),
            "-r",
            parameters.get("resampling", "nearest"),
            str(raster.path),
            str(output),
        ]
    )
    return _artifact(output, "image/tiff", "geotiff")


def _mosaic(
    rasters: list[StagedInput], parameters: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    try:
        import rasterio
    except ImportError as exc:
        raise ValueError("BLOCKED_BY_ENVIRONMENT") from exc
    grid: tuple[Any, ...] | None = None
    origin: tuple[float, float] | None = None
    for item in rasters:
        with rasterio.open(item.path) as dataset:
            current = (
                dataset.crs,
                dataset.count,
                dataset.dtypes,
                tuple(str(value) for value in dataset.nodatavals),
                dataset.transform.a,
                dataset.transform.b,
                dataset.transform.d,
                dataset.transform.e,
            )
            if grid is not None and current != grid:
                raise ValueError("GRID_MISMATCH")
            if origin is not None:
                x_steps = (dataset.transform.c - origin[0]) / abs(dataset.transform.a)
                y_steps = (dataset.transform.f - origin[1]) / abs(dataset.transform.e)
                if not isclose(x_steps, round(x_steps), abs_tol=1e-9) or not isclose(
                    y_steps, round(y_steps), abs_tol=1e-9
                ):
                    raise ValueError("GRID_MISMATCH")
            grid = current
            origin = origin or (dataset.transform.c, dataset.transform.f)
    vrt = output_dir / "mosaic.vrt"
    output = output_dir / "mosaic.tif"
    build = ["gdalbuildvrt", "-overwrite"]
    if parameters.get("noData") is not None:
        build.extend(["-vrtnodata", str(parameters["noData"])])
    build.extend([str(vrt), *(str(item.path) for item in rasters)])
    _run(build)
    translate = ["gdal_translate", "-of", "GTiff"]
    if parameters.get("noData") is not None:
        translate.extend(["-a_nodata", str(parameters["noData"])])
    translate.extend([str(vrt), str(output)])
    _run(translate)
    return _artifact(output, "image/tiff", "geotiff")


def _rule_rows(source: StagedInput) -> list[dict[str, Any]]:
    if source.format_id == "csv":
        with source.path.open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream))
    if source.format_id == "json":
        value = json.loads(source.path.read_text(encoding="utf-8"))
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            raise ValueError("RULES_INVALID")
        return value
    try:
        import pyarrow.ipc as arrow_ipc
        import pyarrow.parquet as parquet
    except ImportError as exc:
        raise ValueError("BLOCKED_BY_ENVIRONMENT") from exc
    if source.format_id == "parquet":
        table = parquet.read_table(source.path)
    elif source.format_id == "arrow-ipc-file":
        with arrow_ipc.open_file(source.path) as reader:
            table = reader.read_all()
    else:
        raise ValueError("RULES_INVALID")
    return table.to_pylist()


def _reclass_rules(source: StagedInput) -> list[tuple[float, float, float]]:
    required = {"minInclusive", "maxExclusive", "value"}
    parsed: list[tuple[float, float, float]] = []
    previous_upper = float("-inf")
    for row in _rule_rows(source):
        if not required.issubset(row):
            raise ValueError("RULES_INVALID")
        try:
            lower = (
                float("-inf") if row["minInclusive"] in {None, ""} else float(row["minInclusive"])
            )
            upper = (
                float("inf") if row["maxExclusive"] in {None, ""} else float(row["maxExclusive"])
            )
            value = float(row["value"])
        except (TypeError, ValueError) as exc:
            raise ValueError("RULES_INVALID") from exc
        if lower >= upper or lower < previous_upper or not np.isfinite(value):
            raise ValueError("RULES_INVALID")
        parsed.append((lower, upper, value))
        previous_upper = upper
    if not parsed:
        raise ValueError("RULES_INVALID")
    return parsed


def _reclassify(
    raster: StagedInput,
    rules_source: StagedInput,
    parameters: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    try:
        import rasterio
        from rasterio.windows import Window
    except ImportError as exc:
        raise ValueError("BLOCKED_BY_ENVIRONMENT") from exc
    rules = _reclass_rules(rules_source)
    band = int(parameters.get("band", 1))
    output = output_dir / "reclassified.tif"
    with rasterio.open(raster.path) as source:
        if band > source.count:
            raise ValueError("BAND_INVALID")
        profile = source.profile.copy()
        profile.update(
            count=1,
            dtype=parameters["outputType"],
            nodata=parameters["noData"],
        )
        with rasterio.open(output, "w", **profile) as target:
            for y_offset in range(0, source.height, 512):
                height = min(512, source.height - y_offset)
                for x_offset in range(0, source.width, 512):
                    width = min(512, source.width - x_offset)
                    window = Window.from_slices(
                        (y_offset, y_offset + height),
                        (x_offset, x_offset + width),
                    )
                    values = source.read(band, window=window, masked=True)
                    output_values = np.full(
                        values.shape,
                        parameters.get("defaultValue", parameters["noData"]),
                        dtype=parameters["outputType"],
                    )
                    valid = ~np.ma.getmaskarray(values)
                    raw = np.asarray(values.data)
                    for lower, upper, result in rules:
                        matches = valid & (raw >= lower) & (raw < upper)
                        output_values[matches] = result
                    output_values[~valid] = parameters["noData"]
                    target.write(output_values, 1, window=window)
    return _artifact(output, "image/tiff", "geotiff")


def _polygonize(
    raster: StagedInput, parameters: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    band = int(parameters.get("band", 1))
    if raster.metadata.get("bandCount") and band > int(raster.metadata["bandCount"]):
        raise ValueError("BAND_INVALID")
    output = output_dir / "polygonized.gpkg"
    command = ["gdal_polygonize.py"]
    if parameters.get("connectivity", 4) == 8:
        command.append("-8")
    if not parameters.get("excludeNoData", True):
        command.append("-nomask")
    command.extend(
        [
            "-b",
            str(band),
            "-f",
            "GPKG",
            str(raster.path),
            str(output),
            "polygonized",
            "value",
        ]
    )
    _run(command)
    return _artifact(output, "application/geopackage+sqlite3", "geopackage")


def _rasterize(vector: StagedInput, parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    if ("burnField" in parameters) == ("burnValue" in parameters):
        raise ValueError("PARAMETERS_INVALID")
    if (
        parameters["extentMinX"] >= parameters["extentMaxX"]
        or parameters["extentMinY"] >= parameters["extentMaxY"]
    ):
        raise ValueError("EXTENT_INVALID")
    output = output_dir / "rasterized.tif"
    command = ["gdal_rasterize"]
    if layer := parameters.get("layer"):
        command.extend(["-l", _layer(vector, layer)])
    if field := parameters.get("burnField"):
        command.extend(["-a", field])
    else:
        command.extend(["-burn", str(parameters["burnValue"])])
    if parameters.get("allTouched", False):
        command.append("-at")
    command.extend(
        [
            "-te",
            str(parameters["extentMinX"]),
            str(parameters["extentMinY"]),
            str(parameters["extentMaxX"]),
            str(parameters["extentMaxY"]),
            "-tr",
            str(parameters["pixelSizeX"]),
            str(parameters["pixelSizeY"]),
            "-ot",
            parameters["outputType"],
            "-a_nodata",
            str(parameters["noData"]),
            "-init",
            str(parameters["noData"]),
            "-of",
            "GTiff",
            str(vector.path),
            str(output),
        ]
    )
    _run(command)
    return _artifact(output, "image/tiff", "geotiff")


def _raster_statistics(raster: StagedInput, parameters: dict[str, Any]) -> dict[str, Any]:
    try:
        import rasterio
        from rasterio.windows import Window
    except ImportError as exc:
        raise ValueError("BLOCKED_BY_ENVIRONMENT") from exc
    results: list[dict[str, int | float | None]] = []
    with rasterio.open(raster.path) as dataset:
        selected = [int(parameters["band"])] if "band" in parameters else list(dataset.indexes)
        if any(band not in dataset.indexes for band in selected):
            raise ValueError("BAND_INVALID")
        for band in selected:
            count = 0
            null_count = 0
            total = 0.0
            mean = 0.0
            m2 = 0.0
            minimum: float | None = None
            maximum: float | None = None
            for y_offset in range(0, dataset.height, 512):
                height = min(512, dataset.height - y_offset)
                for x_offset in range(0, dataset.width, 512):
                    width = min(512, dataset.width - x_offset)
                    values = (
                        dataset.read(
                            band,
                            window=Window.from_slices(
                                (y_offset, y_offset + height),
                                (x_offset, x_offset + width),
                            ),
                            masked=True,
                        )
                        .compressed()
                        .astype(np.float64, copy=False)
                    )
                    null_count += width * height - int(values.size)
                    if values.size == 0:
                        continue
                    chunk_count = int(values.size)
                    chunk_mean = float(np.mean(values))
                    chunk_m2 = float(np.sum((values - chunk_mean) ** 2, dtype=np.float64))
                    new_count = count + chunk_count
                    delta = chunk_mean - mean
                    m2 += chunk_m2 + delta * delta * count * chunk_count / new_count
                    mean += delta * chunk_count / new_count
                    count = new_count
                    total += float(np.sum(values, dtype=np.float64))
                    chunk_min, chunk_max = float(np.min(values)), float(np.max(values))
                    minimum = chunk_min if minimum is None else min(minimum, chunk_min)
                    maximum = chunk_max if maximum is None else max(maximum, chunk_max)
            results.append(
                {
                    "band": band,
                    "count": count,
                    "nullCount": null_count,
                    "min": minimum,
                    "max": maximum,
                    "sum": total if count else None,
                    "mean": mean if count else None,
                    "stddev": sqrt(m2 / count) if count else None,
                }
            )
    return {"bands": results}


def _terrain_derivative(
    operator_id: str,
    raster: StagedInput,
    parameters: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    if raster.metadata.get("crsUnit") != "metre":
        raise ValueError("PROJECTED_CRS_REQUIRED")
    operation = operator_id.removeprefix("raster.")
    output = output_dir / f"{operation}.tif"
    command = [
        "gdaldem",
        operation,
        str(raster.path),
        str(output),
        "-of",
        "GTiff",
        "-b",
        str(parameters.get("band", 1)),
    ]
    if operation == "slope" and parameters.get("units", "degrees") == "percent":
        command.append("-p")
    if operation == "hillshade":
        command.extend(
            [
                "-az",
                str(parameters.get("azimuthDegrees", 315)),
                "-alt",
                str(parameters.get("altitudeDegrees", 45)),
            ]
        )
    if parameters.get("algorithm", "horn") == "zevenbergenThorne":
        command.extend(["-alg", "ZevenbergenThorne"])
    if parameters.get("computeEdges", False):
        command.append("-compute_edges")
    if operation == "aspect" and parameters.get("zeroForFlat", False):
        command.append("-zero_for_flat")
    if operation in {"slope", "hillshade"}:
        command.extend(["-s", "1"])
    _run(command)
    return _artifact(output, "image/tiff", "geotiff")


def _contour(raster: StagedInput, parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    output = output_dir / "contours.gpkg"
    _run(
        [
            "gdal_contour",
            "-b",
            str(parameters.get("band", 1)),
            "-a",
            "elevation",
            "-i",
            str(parameters["intervalMeters"]),
            "-off",
            str(parameters.get("offsetMeters", 0)),
            "-f",
            "GPKG",
            "-nln",
            "contours",
            str(raster.path),
            str(output),
        ]
    )
    return _artifact(output, "application/geopackage+sqlite3", "geopackage")


def _proximity(raster: StagedInput, parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    if (
        parameters.get("distanceUnit", "pixel") == "metre"
        and raster.metadata.get("crsUnit") != "metre"
    ):
        raise ValueError("PROJECTED_CRS_REQUIRED")
    output = output_dir / "proximity.tif"
    command = [
        "gdal_proximity.py",
        str(raster.path),
        str(output),
        "-srcband",
        str(parameters.get("band", 1)),
        "-of",
        "GTiff",
        "-ot",
        "Float32",
        "-values",
        str(parameters["targetValue"]),
        "-distunits",
        "GEO" if parameters.get("distanceUnit", "pixel") == "metre" else "PIXEL",
        "-nodata",
        "-9999",
        "-use_input_nodata",
        "YES",
    ]
    if "maxDistance" in parameters:
        command.extend(["-maxdist", str(parameters["maxDistance"])])
    _run(command)
    return _artifact(output, "image/tiff", "geotiff")


def _fill_nodata(
    raster: StagedInput, parameters: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    output = output_dir / "filled.tif"
    _run(
        [
            "gdal_fillnodata.py",
            "-q",
            "-md",
            str(parameters.get("maxDistancePixels", 100)),
            "-si",
            str(parameters.get("smoothingIterations", 0)),
            "-b",
            str(parameters.get("band", 1)),
            "-of",
            "GTiff",
            str(raster.path),
            str(output),
        ],
        cwd=output_dir,
    )
    return _artifact(output, "image/tiff", "geotiff")


def _native_gis_operator(
    operator_id: str,
    inputs: dict[str, list[StagedInput]],
    parameters: dict[str, Any],
    output_dir: Path,
) -> dict[str, dict[str, Any]]:
    if operator_id == "data.convert":
        return {"data": _convert(inputs["data"][0], parameters, output_dir)}
    if operator_id == "vector.buffer":
        return {"vector": _buffer(inputs["vector"][0], parameters, output_dir)}
    if operator_id == "crs.reproject":
        source = inputs["data"][0]
        if not source.metadata.get("crs"):
            raise ValueError("CRS_REQUIRED")
        target = parameters["targetCrs"]
        if source.format_id in VECTOR_FORMATS:
            output = output_dir / "reprojected.gpkg"
            command = ["ogr2ogr", "-f", "GPKG", "-t_srs", target, str(output), str(source.path)]
            if layer := parameters.get("layer"):
                command.append(layer)
            _run(command)
            return {"data": _artifact(output, "application/geopackage+sqlite3", "geopackage")}
        output = output_dir / "reprojected.copc.laz"
        _run_pdal_pipeline(
            [
                {"type": "readers.las", "filename": str(source.path)},
                {"type": "filters.reprojection", "out_srs": target},
                {"type": "writers.copc", "filename": str(output)},
            ],
            output_dir / "reproject-pdal.json",
        )
        return {"data": _artifact(output, "application/vnd.laz", "copc")}
    if operator_id == "raster.reproject":
        source = inputs["raster"][0]
        output = output_dir / "reprojected.tif"
        command = [
            "gdalwarp",
            "-t_srs",
            parameters["targetCrs"],
            "-r",
            parameters.get("resampling", "nearest"),
        ]
        if "pixelSizeX" in parameters or "pixelSizeY" in parameters:
            if "pixelSizeX" not in parameters or "pixelSizeY" not in parameters:
                raise ValueError("RESOLUTION_INCOMPLETE")
            command.extend(["-tr", str(parameters["pixelSizeX"]), str(parameters["pixelSizeY"])])
        if parameters.get("noData") is not None:
            command.extend(["-dstnodata", str(parameters["noData"])])
        command.extend([str(source.path), str(output)])
        _run(command)
        return {"raster": _artifact(output, "image/tiff", "geotiff")}
    if operator_id == "raster.clip":
        raster, mask = inputs["raster"][0], inputs["mask"][0]
        if raster.metadata.get("crs") != mask.metadata.get("crs"):
            raise ValueError("CRS_MISMATCH")
        output = output_dir / "clipped.tif"
        command = ["gdalwarp", "-cutline", str(mask.path)]
        if parameters.get("maskLayer"):
            command.extend(["-cl", parameters["maskLayer"]])
        if parameters.get("cropToCutline", True):
            command.append("-crop_to_cutline")
        if parameters.get("noData") is not None:
            command.extend(["-dstnodata", str(parameters["noData"])])
        command.extend([str(raster.path), str(output)])
        _run(command)
        try:
            import rasterio
        except ImportError as exc:
            raise ValueError("BLOCKED_BY_ENVIRONMENT") from exc
        with rasterio.open(output) as dataset:
            if not any(dataset.read_masks(index).any() for index in dataset.indexes):
                raise ValueError("EMPTY_CLIP_RESULT")
        return {"raster": _artifact(output, "image/tiff", "geotiff")}
    if operator_id == "raster.algebra":
        return {"raster": _raster_algebra(inputs["rasters"], parameters, output_dir)}
    if operator_id == "raster.zonal_statistics":
        return _zonal_statistics(inputs["raster"][0], inputs["zones"][0], parameters, output_dir)
    if operator_id == "raster.resample":
        return {"raster": _resample(inputs["raster"][0], parameters, output_dir)}
    if operator_id == "raster.mosaic":
        return {"raster": _mosaic(inputs["rasters"], parameters, output_dir)}
    if operator_id == "raster.reclassify":
        return {
            "raster": _reclassify(inputs["raster"][0], inputs["rules"][0], parameters, output_dir)
        }
    if operator_id == "raster.polygonize":
        return {"vector": _polygonize(inputs["raster"][0], parameters, output_dir)}
    if operator_id == "raster.rasterize":
        return {"raster": _rasterize(inputs["vector"][0], parameters, output_dir)}
    if operator_id == "raster.statistics":
        return {
            "statistics": {
                "kind": "value",
                "value": _raster_statistics(inputs["raster"][0], parameters),
            }
        }
    if operator_id in {"raster.slope", "raster.aspect", "raster.hillshade"}:
        return {
            "raster": _terrain_derivative(operator_id, inputs["raster"][0], parameters, output_dir)
        }
    if operator_id == "raster.contour":
        return {"vector": _contour(inputs["raster"][0], parameters, output_dir)}
    if operator_id == "raster.proximity":
        return {"raster": _proximity(inputs["raster"][0], parameters, output_dir)}
    if operator_id == "raster.fill_nodata":
        return {"raster": _fill_nodata(inputs["raster"][0], parameters, output_dir)}
    if operator_id.startswith("hydrology."):
        from moperators.operators import hydrology

        if operator_id == "hydrology.flow_direction":
            output = output_dir / "flow-direction.tif"
            hydrology.flow_direction(inputs["dem"][0].path, output, int(parameters.get("band", 1)))
            return {"flowDirection": _artifact(output, "image/tiff", "geotiff")}
        if operator_id == "hydrology.flow_accumulation":
            output = output_dir / "flow-accumulation.tif"
            hydrology.flow_accumulation(inputs["flowDirection"][0].path, output, output_dir)
            return {"accumulation": _artifact(output, "image/tiff", "geotiff")}
        if operator_id == "hydrology.stream_extract":
            output = output_dir / "streams.tif"
            hydrology.stream_extract(
                inputs["accumulation"][0].path,
                output,
                int(parameters["minimumContributingCells"]),
            )
            return {"streams": _artifact(output, "image/tiff", "geotiff")}
        if operator_id == "hydrology.stream_order":
            output = output_dir / "stream-order.tif"
            hydrology.stream_order(
                inputs["flowDirection"][0].path,
                inputs["streams"][0].path,
                output,
                output_dir,
            )
            return {"streamOrder": _artifact(output, "image/tiff", "geotiff")}
        if operator_id == "hydrology.snap_pour_points":
            output = output_dir / "pour-points.gpkg"
            hydrology.snap_pour_points(
                inputs["points"][0].path,
                inputs["accumulation"][0].path,
                output,
                parameters.get("layer"),
                float(parameters["maxDistanceMeters"]),
            )
            return {"points": _artifact(output, "application/geopackage+sqlite3", "geopackage")}
        if operator_id == "hydrology.watershed":
            output = output_dir / "watershed.tif"
            hydrology.watershed(
                inputs["flowDirection"][0].path,
                inputs["pourPoints"][0].path,
                output,
                output_dir,
                parameters.get("layer"),
                parameters.get("idField", "hydro_id"),
            )
            return {"watershed": _artifact(output, "image/tiff", "geotiff")}
        if operator_id == "hydrology.basins":
            output = output_dir / "basins.tif"
            hydrology.basins(inputs["flowDirection"][0].path, output, output_dir)
            return {"basins": _artifact(output, "image/tiff", "geotiff")}
    if operator_id == "points.interpolate.idw":
        return {"raster": _idw(inputs["points"][0], parameters, output_dir)}
    if operator_id == "points.interpolate.kriging":
        from moperators.operators.kriging import run_kriging

        return run_kriging(inputs["points"][0], parameters, output_dir)
    raise ValueError(f"OPERATOR_NOT_IMPLEMENTED: {operator_id}")


def run_native_operator(
    operator_id: str,
    inputs: dict[str, list[StagedInput]],
    parameters: dict[str, Any],
    output_dir: Path,
) -> dict[str, dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    from moperators.operators.analysis import run_analysis_operator
    from moperators.operators.groundwater import run_groundwater_operator

    specialized = run_analysis_operator(operator_id, inputs, parameters, output_dir)
    if specialized is None:
        specialized = run_groundwater_operator(operator_id, inputs, parameters, output_dir)
    if specialized is not None:
        return specialized
    if operator_id == "data.inspect":
        return {
            "summary": {
                "kind": "value",
                "value": _inspect(inputs["data"][0], parameters.get("layer")),
            }
        }
    if operator_id == "statistics.describe":
        summary_value, details_value = _statistics(inputs["data"][0], parameters)
        return {
            "summary": {
                "kind": "value",
                "value": summary_value,
            },
            "details": {
                "kind": "value",
                "value": details_value,
            },
        }
    return _native_gis_operator(operator_id, inputs, parameters, output_dir)
