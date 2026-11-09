from __future__ import annotations

import csv
import json
import math
import shutil
import subprocess
from io import BytesIO
from pathlib import Path
from typing import Any


def _crs_metadata(value: Any) -> tuple[str, str]:
    from rasterio.crs import CRS

    crs = CRS.from_user_input(value)
    authority = crs.to_authority()
    if authority is None:
        raise ValueError("CRS_REQUIRED")
    unit_name = (crs.linear_units or "").lower()
    unit = "metre" if unit_name in {"metre", "meter"} else unit_name
    return f"{authority[0]}:{authority[1]}", unit


def _has_z(value: Any) -> bool:
    if isinstance(value, list):
        if value and all(isinstance(item, (int, float)) for item in value):
            return len(value) >= 3
        return any(_has_z(item) for item in value)
    if isinstance(value, dict):
        if value.get("type") == "FeatureCollection":
            return _has_z(value.get("features", []))
        if value.get("type") == "Feature":
            return _has_z(value.get("geometry"))
        if value.get("type") == "GeometryCollection":
            return _has_z(value.get("geometries", []))
        return _has_z(value.get("coordinates", []))
    return False


def _inspect_json(path: Path, format_id: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if format_id == "json":
        metadata: dict[str, Any] = {"valueType": type(value).__name__}
        if isinstance(value, list) and all(isinstance(row, dict) for row in value):
            metadata["fields"] = sorted({key for row in value for key in row})
        return metadata
    if not isinstance(value, dict) or value.get("type") not in {"Feature", "FeatureCollection"}:
        raise ValueError("MEDIA_TYPE_MISMATCH")
    features = value.get("features", []) if value.get("type") == "FeatureCollection" else [value]
    properties = [
        feature.get("properties") or {} for feature in features if isinstance(feature, dict)
    ]
    fields = sorted({key for row in properties for key in row})
    return {
        "crs": "OGC:CRS84",
        "crsUnit": "degree",
        "layers": [
            {
                "name": "data",
                "fields": [
                    {
                        "name": field,
                        "type": next(
                            (
                                type(row[field]).__name__
                                for row in properties
                                if row.get(field) is not None
                            ),
                            "null",
                        ),
                    }
                    for field in fields
                ],
            }
        ],
        "hasZ": _has_z(value.get("geometry") or value.get("features", [])),
        "hasM": False,
    }


def _inspect_vector(path: Path) -> dict[str, Any]:
    import pyogrio

    layers: list[dict[str, Any]] = []
    crs_values: set[str] = set()
    unit = ""
    has_z = False
    for name, _ in pyogrio.list_layers(path):
        info = pyogrio.read_info(path, layer=name)
        crs_value = info.get("crs")
        if not crs_value:
            raise ValueError("CRS_REQUIRED")
        crs, unit = _crs_metadata(crs_value)
        crs_values.add(crs)
        geometry_type = str(info.get("geometry_type") or "")
        has_z = has_z or geometry_type.startswith("3D ")
        layers.append(
            {
                "name": str(name),
                "geometryType": geometry_type,
                "featureCount": int(info.get("features") or 0),
                "fields": [
                    {"name": str(field), "type": str(dtype)}
                    for field, dtype in zip(
                        info.get("fields", []), info.get("dtypes", []), strict=True
                    )
                ],
            }
        )
    if not layers:
        raise ValueError("INPUT_INVALID")
    if len(crs_values) != 1:
        raise ValueError("CRS_MISMATCH")
    return {
        "crs": next(iter(crs_values)),
        "crsUnit": unit,
        "layers": layers,
        "hasZ": has_z,
        "hasM": False,
    }


def _inspect_raster(path: Path) -> dict[str, Any]:
    import rasterio

    with rasterio.open(path) as dataset:
        if dataset.crs is None:
            raise ValueError("CRS_REQUIRED")
        authority = dataset.crs.to_authority()
        crs = ":".join(authority) if authority else dataset.crs.to_string()
        _, unit = _crs_metadata(crs)
        bounds = dataset.bounds
        return {
            "crs": crs,
            "crsUnit": unit,
            "width": dataset.width,
            "height": dataset.height,
            "bandCount": dataset.count,
            "dataTypes": list(dataset.dtypes),
            "bounds": [bounds.left, bounds.bottom, bounds.right, bounds.top],
            "noData": [
                value if value is None or math.isfinite(value) else None
                for value in dataset.nodatavals
            ],
            "resolution": [abs(dataset.transform.a), abs(dataset.transform.e)],
            "gridOrigin": [dataset.transform.c, dataset.transform.f],
            "rotation": [dataset.transform.b, dataset.transform.d],
        }


def _inspect_table(path: Path, format_id: str) -> dict[str, Any]:
    if format_id == "csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, strict=True)
            header = next(reader, None)
            if not header:
                raise ValueError("HEADER_INVALID")
            return {"fields": header, "rowCount": sum(1 for _ in reader)}
    import pyarrow.parquet as parquet

    schema = parquet.ParquetFile(path).schema_arrow
    metadata = schema.metadata or {}
    if b"geo" not in metadata:
        return {"fields": schema.names}
    geo = json.loads(metadata[b"geo"])
    primary = geo.get("primary_column")
    crs_value = geo.get("columns", {}).get(primary, {}).get("crs")
    if not crs_value:
        raise ValueError("CRS_REQUIRED")
    crs, unit = _crs_metadata(crs_value)
    return {
        "fields": schema.names,
        "crs": crs,
        "crsUnit": unit,
        "layers": [{"name": "data"}],
        "hasZ": False,
        "hasM": False,
    }


def _inspect_point_cloud(path: Path, format_id: str) -> dict[str, Any]:
    if shutil.which("pdal") is None:
        raise ValueError("VALIDATOR_UNAVAILABLE")
    completed = subprocess.run(
        ["pdal", "info", "--metadata", str(path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    metadata = json.loads(completed.stdout).get("metadata", {})
    srs = metadata.get("srs", {})
    authority = srs.get("horizontal") or srs.get("compoundwkt") or srs.get("wkt")
    if not authority:
        raise ValueError("CRS_REQUIRED")
    crs, unit = _crs_metadata(authority)
    return {
        "crs": crs,
        "crsUnit": unit,
        "pointCount": metadata.get("count"),
        "format": format_id,
    }


def inspect_fixture(path: Path, format_id: str) -> dict[str, Any]:
    if format_id in {"json", "geojson"}:
        return _inspect_json(path, format_id)
    if format_id in {"geopackage", "flatgeobuf"}:
        return _inspect_vector(path)
    if format_id in {"geotiff", "cog"}:
        return _inspect_raster(path)
    if format_id in {"csv", "parquet", "geoparquet"}:
        return _inspect_table(path, format_id)
    if format_id in {"las", "laz", "copc"}:
        return _inspect_point_cloud(path, format_id)
    if format_id == "npy":
        import numpy as np

        value = np.load(path, allow_pickle=False, mmap_mode="r")
        if not np.issubdtype(value.dtype, np.number) or not np.isfinite(value).all():
            raise ValueError("NON_NUMERIC_VALUE")
        return {"shape": list(value.shape), "dataType": str(value.dtype)}
    if format_id == "pytorch-state-dict":
        from moperators.operators.gru_attention import inspect_weights

        return {"tensors": sorted(inspect_weights(path))}
    if format_id == "arrow-ipc-file":
        import pyarrow.ipc as ipc

        with ipc.open_file(path) as reader:
            return {"fields": reader.schema.names}
    if format_id == "xlsx":
        from openpyxl import load_workbook

        workbook = load_workbook(BytesIO(path.read_bytes()), read_only=False, data_only=False)
        try:
            return {
                "sheetCount": len(workbook.worksheets),
                "sheets": [
                    {
                        "name": sheet.title,
                        "maxRow": sheet.max_row,
                        "maxColumn": sheet.max_column,
                        "mergedRangeCount": len(sheet.merged_cells.ranges),
                    }
                    for sheet in workbook.worksheets
                ],
            }
        finally:
            workbook.close()
    if format_id == "docx":
        from docx import Document

        document = Document(str(path))
        return {"paragraphCount": len(document.paragraphs), "tableCount": len(document.tables)}
    if format_id == "pdf":
        from moperators.operators.pdf_text_common_v1 import inspect_pdf

        inspection = inspect_pdf(path)
        return {
            "pageCount": inspection.page_count,
            "textPageCount": inspection.page_count - len(inspection.empty_pages),
            "emptyTextPageCount": len(inspection.empty_pages),
        }
    if format_id == "text":
        text = path.read_text(encoding="utf-8")
        return {"characterCount": len(text), "lineCount": len(text.splitlines())}
    raise ValueError("FORMAT_UNSUPPORTED")
