from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from PIL import Image
from rasterio.transform import from_origin

from moperators.operators.native import StagedInput
from moperators.static_implementations import execute


def _render(source: Path, output: Path, *, format_id: str, **parameters):
    media_type = "application/geo+json" if format_id == "geojson" else "image/tiff"
    return execute(
        "cartography.render-png",
        "1.0.0",
        {"data": [StagedInput(source, media_type, format_id, {})]},
        parameters,
        output,
    )


def _write_vector(path: Path) -> Path:
    document = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:3857"}},
        "features": [
            {
                "type": "Feature",
                "properties": {"level": 2.0},
                "geometry": {"type": "Point", "coordinates": [10.0, 20.0]},
            },
            {
                "type": "Feature",
                "properties": {"level": None},
                "geometry": {"type": "Point", "coordinates": [30.0, 50.0]},
            },
            {
                "type": "Feature",
                "properties": {"level": 8.0},
                "geometry": {"type": "Point", "coordinates": [40.0, 60.0]},
            },
        ],
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _write_raster(path: Path) -> Path:
    values = np.arange(12, dtype="float32").reshape(3, 4)
    values[1, 2] = -9999
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=4,
        height=3,
        count=1,
        dtype="float32",
        crs="EPSG:3857",
        transform=from_origin(100, 200, 10, 10),
        nodata=-9999,
    ) as dataset:
        dataset.write(values, 1)
    return path


def _summary(result: dict) -> dict:
    assert result["summary"]["kind"] == "value"
    return result["summary"]["value"]


def test_vector_map_reports_style_crs_extent_and_explicit_nodata(tmp_path: Path) -> None:
    result = _render(
        _write_vector(tmp_path / "points.json"),
        tmp_path / "output",
        format_id="geojson",
        title="Monitoring levels",
        valueField="level",
        palette="cividis",
        width=800,
        height=600,
        unit="m",
    )

    with Image.open(result["image"]["path"]) as image:
        assert image.size == (800, 600)
    assert _summary(result) == {
        "sourceKind": "vector",
        "sourceFormat": "geojson",
        "crs": "EPSG:3857",
        "extent": {"minX": 10.0, "minY": 20.0, "maxX": 40.0, "maxY": 60.0},
        "image": {"width": 800, "height": 600},
        "style": {
            "title": "Monitoring levels",
            "valueField": "level",
            "palette": "cividis",
            "unit": "m",
            "colorbarLabel": "level (m)",
            "noDataLabel": "No data",
            "noDataColor": "#d9d9d9",
        },
        "data": {
            "countBasis": "FEATURES",
            "featureCount": 3,
            "validValueCount": 2,
            "noDataCount": 1,
        },
        "interpretation": "Static rendering of one existing spatial data file in its source CRS.",
        "limitations": [
            "No reprojection is performed; coordinates and extent use the source CRS.",
            (
                "The image contains one source only and has no basemap, interactive layers, "
                "or publication service."
            ),
            (
                "Colors visualize the selected values but do not establish data accuracy, "
                "provenance, or analytical validity."
            ),
        ],
    }


def test_raster_map_reports_rendered_nodata_and_source_grid(tmp_path: Path) -> None:
    result = _render(
        _write_raster(tmp_path / "surface.tif"),
        tmp_path / "output",
        format_id="geotiff",
        title="Hydraulic head",
        palette="plasma",
        width=900,
        height=500,
        unit="m",
    )

    with Image.open(result["image"]["path"]) as image:
        assert image.size == (900, 500)
    summary = _summary(result)
    assert summary["sourceKind"] == "raster"
    assert summary["sourceFormat"] == "geotiff"
    assert summary["crs"] == "EPSG:3857"
    assert summary["extent"] == {
        "minX": 100.0,
        "minY": 170.0,
        "maxX": 140.0,
        "maxY": 200.0,
    }
    assert summary["image"] == {"width": 900, "height": 500}
    assert summary["style"] == {
        "title": "Hydraulic head",
        "valueField": None,
        "palette": "plasma",
        "unit": "m",
        "colorbarLabel": "Band 1 (m)",
        "noDataLabel": "No data",
        "noDataColor": "#d9d9d9",
    }
    assert summary["data"] == {
        "countBasis": "RENDERED_PIXELS",
        "band": 1,
        "sourceWidth": 4,
        "sourceHeight": 3,
        "renderedWidth": 4,
        "renderedHeight": 3,
        "validValueCount": 11,
        "noDataCount": 1,
        "noDataValue": -9999.0,
    }


@pytest.mark.parametrize(
    ("actual", "declared"),
    [("vector", "geotiff"), ("raster", "geojson")],
)
def test_declared_format_mismatch_has_stable_error(
    tmp_path: Path, actual: str, declared: str
) -> None:
    source = (
        _write_vector(tmp_path / "input")
        if actual == "vector"
        else _write_raster(tmp_path / "input")
    )

    with pytest.raises(ValueError, match=r"^CARTOGRAPHY_FORMAT_MISMATCH$"):
        _render(source, tmp_path / "output", format_id=declared)
    assert not (tmp_path / "output").exists()
