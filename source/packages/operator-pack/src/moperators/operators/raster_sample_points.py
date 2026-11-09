from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as parquet
import pyogrio
import rasterio
from shapely import from_wkb, get_coordinate_dimension

from moperators.operators.extension_common import one, summary


def run(inputs: dict[str, Any], parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    raster_input = one(inputs, "raster")
    point_input = one(inputs, "points")
    metadata, table = pyogrio.read_arrow(
        point_input.path,
        layer=parameters.get("pointsLayer"),
        force_2d=False,
    )
    geometry_name = metadata.get("geometry_name") or _geometry_column(table)
    geometry_type = metadata.get("geometry_type")
    if geometry_type != "Point":
        raise ValueError("GEOMETRY_TYPE_UNSUPPORTED")
    id_field = parameters["idField"]
    if id_field not in table.column_names:
        raise ValueError(f"FIELD_REQUIRED: {id_field}")
    ids = table[id_field].to_pylist()
    if len(set(ids)) != len(ids):
        raise ValueError("DUPLICATE_ID")
    geometries = from_wkb(table[geometry_name].to_numpy(zero_copy_only=False))
    if any(geometry is None or geometry.is_empty for geometry in geometries):
        raise ValueError("GEOMETRY_TYPE_UNSUPPORTED")
    if any(get_coordinate_dimension(geometry) != 2 for geometry in geometries):
        raise ValueError("GEOMETRY_TYPE_UNSUPPORTED")
    band = int(parameters.get("band", 1))
    method = parameters.get("method", "nearest")
    if method not in {"nearest", "bilinear"}:
        raise ValueError("PARAMETER_INVALID")
    values: list[float | None] = []
    statuses: list[str] = []
    coordinates = [(float(geometry.x), float(geometry.y)) for geometry in geometries]
    with rasterio.open(raster_input.path) as dataset:
        if band < 1 or band > dataset.count:
            raise ValueError("BAND_INVALID")
        if dataset.crs is None or not metadata.get("crs"):
            raise ValueError("CRS_REQUIRED")
        if dataset.crs != rasterio.crs.CRS.from_user_input(metadata["crs"]):
            raise ValueError("CRS_MISMATCH")
        raster = dataset.read(band, masked=True)
        for x, y in coordinates:
            value, status = (
                _nearest(dataset, raster, x, y)
                if method == "nearest"
                else _bilinear(dataset, raster, x, y)
            )
            values.append(value)
            statuses.append(status)
    order = sorted(range(len(ids)), key=lambda index: str(ids[index]))
    output_kind = parameters.get("outputKind", "table")
    if output_kind == "table":
        output = output_dir / "raster-samples.parquet"
        parquet.write_table(
            pa.table(
                {
                    id_field: [ids[index] for index in order],
                    "x": [coordinates[index][0] for index in order],
                    "y": [coordinates[index][1] for index in order],
                    "sample_value": [values[index] for index in order],
                    "status": [statuses[index] for index in order],
                }
            ),
            output,
        )
        artifact = _artifact(output, "application/vnd.apache.parquet", "parquet")
    elif output_kind == "vector":
        output = output_dir / "raster-samples.gpkg"
        sorted_table = table.take(pa.array(order, type=pa.int64()))
        sorted_table = sorted_table.append_column(
            "sample_value", pa.array([values[index] for index in order], type=pa.float64())
        ).append_column("status", pa.array([statuses[index] for index in order]))
        pyogrio.write_arrow(
            sorted_table,
            output,
            driver="GPKG",
            layer="samples",
            geometry_name=geometry_name,
            geometry_type="Point",
            crs=metadata["crs"],
        )
        artifact = _artifact(output, "application/geopackage+sqlite3", "geopackage")
    else:
        raise ValueError("PARAMETER_INVALID")
    return {
        "samples": artifact,
        "summary": summary(
            {
                "pointCount": len(ids),
                "sampledCount": statuses.count("OK"),
                "noDataCount": statuses.count("NODATA"),
                "outsideCount": statuses.count("OUTSIDE"),
            }
        ),
    }


def _nearest(dataset: Any, raster: Any, x: float, y: float) -> tuple[float | None, str]:
    row, column = dataset.index(x, y)
    if row < 0 or column < 0 or row >= dataset.height or column >= dataset.width:
        return None, "OUTSIDE"
    value = raster[row, column]
    if np.ma.is_masked(value):
        return None, "NODATA"
    return float(value), "OK"


def _bilinear(dataset: Any, raster: Any, x: float, y: float) -> tuple[float | None, str]:
    column_corner, row_corner = (~dataset.transform) * (x, y)
    column_center, row_center = column_corner - 0.5, row_corner - 0.5
    column0, row0 = int(np.floor(column_center)), int(np.floor(row_center))
    if row0 < 0 or column0 < 0 or row0 + 1 >= dataset.height or column0 + 1 >= dataset.width:
        return None, "OUTSIDE"
    block = raster[row0 : row0 + 2, column0 : column0 + 2]
    if np.any(np.ma.getmaskarray(block)):
        return None, "NODATA"
    dx, dy = column_center - column0, row_center - row0
    raw = np.asarray(block.data, dtype=np.float64)
    value = (
        raw[0, 0] * (1 - dx) * (1 - dy)
        + raw[0, 1] * dx * (1 - dy)
        + raw[1, 0] * (1 - dx) * dy
        + raw[1, 1] * dx * dy
    )
    return float(value), "OK"


def _artifact(path: Path, media_type: str, format_id: str) -> dict[str, Any]:
    return {
        "kind": "artifact",
        "path": str(path),
        "fileName": path.name,
        "mediaType": media_type,
        "format": format_id,
    }


def _geometry_column(table: pa.Table) -> str:
    for schema_field in table.schema:
        metadata = schema_field.metadata or {}
        if metadata.get(b"ARROW:extension:name", b"").startswith(b"geoarrow."):
            return schema_field.name
    raise ValueError("GEOMETRY_TYPE_UNSUPPORTED")
