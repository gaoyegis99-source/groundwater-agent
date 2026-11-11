"""Structured GIS operators; no workflow or cartography service dependencies."""

from __future__ import annotations

import math

import numpy as np

from moperators.operators.transfer_common import (
    artifact,
    numeric,
    one,
    read_json,
    table,
    write_json,
)


def _read(path, layer):
    import pyogrio
    import shapely

    metadata, data = pyogrio.read_arrow(path, layer=layer)
    if not metadata["crs"]:
        raise ValueError("CRS_REQUIRED")
    geometry = shapely.from_wkb(data[metadata["geometry_name"]].to_numpy())
    if not len(geometry) or shapely.is_missing(geometry).any() or shapely.is_empty(geometry).any():
        raise ValueError("EMPTY_GEOMETRY")
    if not shapely.is_valid(geometry).all():
        raise ValueError("INVALID_GEOMETRY")
    return metadata, data, geometry


def _write(data, geometries, crs, geometry_type, layer, path):
    import pyarrow as pa
    import pyogrio
    import shapely
    from pyproj import CRS

    path.parent.mkdir(parents=True, exist_ok=True)
    data = data.append_column("geometry", pa.array(shapely.to_wkb(geometries), type=pa.binary()))
    pyogrio.set_gdal_config_options({"OGR_CURRENT_DATE": "1980-01-01T00:00:00.000Z"})
    pyogrio.write_arrow(
        data,
        path,
        driver="GPKG",
        layer=layer,
        geometry_name="geometry",
        geometry_type=geometry_type,
        crs=CRS.from_user_input(crs).to_wkt(),
        layer_options={"SPATIAL_INDEX": "NO"},
    )
    return artifact(path, "geopackage")


def from_xy(_identity, inputs, parameters, output_dir):
    import shapely
    from pyproj import CRS

    data = table(inputs)
    if "geometry" in data.column_names:
        raise ValueError("OUTPUT_FIELD_CONFLICT")
    x = numeric(data, parameters["xField"], np.float64)
    y = numeric(data, parameters["yField"], np.float64)
    if not len(x):
        raise ValueError("EMPTY_RESULT")
    crs = CRS.from_user_input(parameters["sourceCrs"])
    if crs.is_geographic and ((np.abs(x) > 180).any() or (np.abs(y) > 90).any()):
        raise ValueError("COORDINATE_INVALID")
    return {
        "points": _write(
            data, shapely.points(x, y), crs, "Point", "points", output_dir / "points.gpkg"
        )
    }


def tag_membership(_identity, inputs, parameters, output_dir):
    import pyarrow as pa
    import shapely
    from pyproj import CRS

    point_meta, data, points = _read(one(inputs, "points"), "points")
    polygon_meta, _, polygons = _read(one(inputs, "polygons"), parameters["polygonLayer"])
    if CRS.from_user_input(point_meta["crs"]) != CRS.from_user_input(polygon_meta["crs"]):
        raise ValueError("CRS_MISMATCH")
    if (
        not np.isin(shapely.get_type_id(points), [0]).all()
        or not np.isin(shapely.get_type_id(polygons), [3, 6]).all()
    ):
        raise ValueError("GEOMETRY_TYPE_INVALID")
    if parameters["outputField"] in data.column_names:
        raise ValueError("OUTPUT_FIELD_CONFLICT")
    data = data.drop([point_meta["geometry_name"]])
    data = data.append_column(
        parameters["outputField"], pa.array(shapely.covers(shapely.union_all(polygons), points))
    )
    return {
        "points": _write(
            data, points, point_meta["crs"], "Point", "points", output_dir / "tagged-points.gpkg"
        )
    }


def envelope(_identity, inputs, parameters, output_dir):
    import pyarrow as pa
    import shapely
    from pyproj import CRS

    meta, _, geometries = _read(one(inputs, "vector"), parameters["layer"])
    crs = CRS.from_user_input(meta["crs"])
    if not crs.is_projected or any(axis.unit_conversion_factor != 1 for axis in crs.axis_info):
        raise ValueError("METRIC_CRS_REQUIRED")
    xmin, ymin, xmax, ymax = shapely.total_bounds(geometries)
    padding = parameters["paddingMeters"]
    xmin, ymin, xmax, ymax = xmin - padding, ymin - padding, xmax + padding, ymax + padding
    width, height, ratio = xmax - xmin, ymax - ymin, parameters["aspectRatio"]
    if min(width, height, ratio) <= 0:
        raise ValueError("EXTENT_INVALID")
    if width / height < ratio:
        extra = (height * ratio - width) / 2
        xmin, xmax = xmin - extra, xmax + extra
    else:
        extra = (width / ratio - height) / 2
        ymin, ymax = ymin - extra, ymax + extra
    return {
        "extent": _write(
            pa.table({"extent_id": [1]}),
            [shapely.box(xmin, ymin, xmax, ymax)],
            crs,
            "Polygon",
            "extent",
            output_dir / "extent.gpkg",
        )
    }


def raster_window(_identity, inputs, parameters, output_dir):
    import rasterio
    import shapely
    from pyproj import CRS
    from rasterio.windows import Window, from_bounds

    meta, _, geometries = _read(one(inputs, "extent"), parameters["extentLayer"])
    if len(geometries) != 1 or not shapely.equals(geometries[0], shapely.envelope(geometries[0])):
        raise ValueError("EXTENT_INVALID")
    rules = read_json(one(inputs, "rules"), "raster-window-bands.v1")
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "window.tif"
    with rasterio.open(one(inputs, "raster")) as source:
        if not source.crs or CRS.from_user_input(source.crs) != CRS.from_user_input(meta["crs"]):
            raise ValueError("CRS_MISMATCH")
        if source.transform.b or source.transform.d:
            raise ValueError("ROTATED_GRID_UNSUPPORTED")
        if max(rules["bands"]) > source.count:
            raise ValueError("BAND_INVALID")
        request = from_bounds(*shapely.bounds(geometries[0]), transform=source.transform)
        left, top = math.floor(request.col_off), math.floor(request.row_off)
        window = Window(
            left,
            top,
            math.ceil(request.col_off + request.width) - left,
            math.ceil(request.row_off + request.height) - top,
        )
        pixels = source.read(rules["bands"], window=window, boundless=True, fill_value=0)
        valid = source.read_masks(rules["bands"][0], window=window, boundless=True) > 0
        coverage = float(valid.mean())
        if coverage < parameters["minimumCoverage"]:
            raise ValueError("IMAGERY_COVERAGE_INSUFFICIENT")
        profile = source.profile.copy()
        profile.update(
            driver="GTiff",
            width=pixels.shape[2],
            height=pixels.shape[1],
            count=len(rules["bands"]),
            transform=source.window_transform(window),
            nodata=0,
            compress="deflate",
        )
        with (
            rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True),
            rasterio.open(path, "w", **profile) as destination,
        ):
            destination.write(pixels)
            destination.write_mask(valid.astype(np.uint8) * 255)
            destination.update_tags(**source.tags())
    return {
        "raster": artifact(path, "geotiff"),
        "coverage": write_json(
            {
                "schemaVersion": "raster-window-coverage.v1",
                "basis": "first-selected-band-mask-within-requested-window",
                "selectedBands": rules["bands"],
                "maskBand": rules["bands"][0],
                "minimumRequiredFraction": parameters["minimumCoverage"],
                "validFraction": coverage,
                "validPixels": int(valid.sum()),
                "totalPixels": int(valid.size),
                "meetsMinimumCoverage": True,
            },
            output_dir / "coverage.json",
        ),
    }
