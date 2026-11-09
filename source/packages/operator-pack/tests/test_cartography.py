from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio
from PIL import Image
from rasterio.shutil import copy as copy_raster
from rasterio.transform import Affine, from_origin

from moperators.catalog import OperatorCatalog
from moperators.operators.native import StagedInput
from moperators.static_implementations import execute

PACK = Path(__file__).resolve().parents[1]


def render(source, output_dir, fmt="geojson", **parameters):
    media = "application/geo+json" if fmt == "geojson" else "image/tiff"
    return execute(
        "cartography.render-png",
        "1.0.0",
        {"data": [StagedInput(source, media, fmt, {})]},
        parameters,
        output_dir,
    )["image"]


def assert_png(artifact):
    assert artifact["kind"] == "artifact"
    assert (artifact["mediaType"], artifact["format"]) == ("image/png", "png")
    path = Path(artifact["path"])
    assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(path) as image:
        assert image.size == (1024, 768)
        pixels = np.asarray(image.convert("RGB"))
        assert np.unique(pixels.reshape(-1, 3), axis=0).shape[0] > 100


def feature(geometry=None, value=3):
    return {
        "type": "Feature",
        "properties": {"level": value},
        "geometry": geometry or {"type": "Point", "coordinates": [116.2, 39.9]},
    }


def write_geojson(path, document):
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def write_raster(path, *, crs="EPSG:4326", all_nodata=False, transform=None):
    values = np.arange(400, dtype="float32").reshape(20, 20)
    values[0, :] = -9999
    if all_nodata:
        values[:] = -9999
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=20,
        height=20,
        count=1,
        dtype="float32",
        crs=crs,
        transform=transform or from_origin(116, 40, 0.02, 0.02),
        nodata=-9999,
    ) as dataset:
        dataset.write(values, 1)
    return path


@pytest.mark.parametrize("value_field", [None, "", "level"])
def test_geojson_feature_collection_and_single_feature(tmp_path, value_field):
    document = feature()
    if value_field is not None:
        document = {"type": "FeatureCollection", "features": [document, feature(value=9)]}
    source = write_geojson(tmp_path / "extensionless", document)
    parameters = {} if value_field is None else {"valueField": value_field, "title": "Levels"}
    assert_png(render(source, tmp_path / "output", **parameters))


@pytest.mark.parametrize(
    "geometry",
    [
        {"type": "MultiPoint", "coordinates": [[0, 0], [1, 1]]},
        {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
        {"type": "MultiLineString", "coordinates": [[[0, 0], [1, 1]], [[1, 0], [2, 1]]]},
        {
            "type": "Polygon",
            "coordinates": [
                [[0, 0], [3, 0], [3, 3], [0, 3], [0, 0]],
                [[1, 1], [1, 2], [2, 2], [2, 1], [1, 1]],
            ],
        },
        {"type": "MultiPolygon", "coordinates": [[[[0, 0], [1, 0], [1, 1], [0, 0]]]]},
        {
            "type": "GeometryCollection",
            "geometries": [
                {"type": "Point", "coordinates": [0, 0]},
                {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
            ],
        },
    ],
)
def test_standard_geojson_geometry_types(tmp_path, geometry):
    source = write_geojson(tmp_path / "input", feature(geometry))
    assert_png(render(source, tmp_path / "output", valueField="level"))


@pytest.mark.parametrize("fmt", ["geotiff", "cog"])
def test_real_raster_bytes_with_nodata_and_extensionless_staging(tmp_path, fmt):
    source = write_raster(tmp_path / "input")
    if fmt == "cog":
        copy_raster(source, tmp_path / "cog", driver="COG")
        source = tmp_path / "cog"
        with rasterio.open(source) as dataset:
            assert dataset.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"
    assert_png(render(source, tmp_path / "output", fmt=fmt, title="Band values"))


@pytest.mark.parametrize(
    "document",
    [
        [],
        {},
        {"type": "FeatureCollection", "features": []},
        {"type": "FeatureCollection", "features": [None]},
        {"type": "Feature", "geometry": None},
        feature({"type": "Point", "coordinates": []}),
        feature({"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]}),
    ],
)
def test_invalid_geojson_fails_without_output(tmp_path, document):
    source = write_geojson(tmp_path / "input", document)
    with pytest.raises(ValueError, match=r"^CARTOGRAPHY_SOURCE_INVALID$"):
        render(source, tmp_path / "output")
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("value", [None, True, "3", float("nan"), float("inf")])
def test_color_field_must_be_finite_numeric(tmp_path, value):
    source = write_geojson(tmp_path / "input", feature(value=value))
    with pytest.raises(ValueError, match=r"^CARTOGRAPHY_FIELD_INVALID$"):
        render(source, tmp_path / "output", valueField="level")


def test_missing_field_is_an_error(tmp_path):
    source = write_geojson(tmp_path / "input", feature())
    with pytest.raises(ValueError, match=r"^CARTOGRAPHY_FIELD_INVALID$"):
        render(source, tmp_path / "output", valueField="absent")


@pytest.mark.parametrize("fmt", ["geojson", "geotiff"])
def test_malformed_bytes_are_rejected(tmp_path, fmt):
    source = tmp_path / "input"
    source.write_bytes(b"invalid data")
    with pytest.raises(ValueError, match=r"^CARTOGRAPHY_SOURCE_INVALID$"):
        render(source, tmp_path / "output", fmt=fmt)


@pytest.mark.parametrize(
    ("options", "parameters", "error"),
    [
        ({"crs": None}, {}, "CRS_REQUIRED"),
        ({"all_nodata": True}, {}, "CARTOGRAPHY_SOURCE_INVALID"),
        ({"transform": Affine(0.02, 0.001, 116, 0, -0.02, 40)}, {}, "ROTATED_GRID_UNSUPPORTED"),
        ({}, {"valueField": "level"}, "CARTOGRAPHY_FIELD_INVALID"),
    ],
)
def test_raster_constraints_fail_explicitly(tmp_path, options, parameters, error):
    source = write_raster(tmp_path / "input", **options)
    with pytest.raises(ValueError, match=rf"^{error}$"):
        render(source, tmp_path / "output", fmt="geotiff", **parameters)


def test_unsupported_format_is_rejected(tmp_path):
    with pytest.raises(ValueError, match=r"^CARTOGRAPHY_FORMAT_UNSUPPORTED$"):
        render(tmp_path / "input", tmp_path / "output", fmt="geopackage")


def test_checked_in_smoke_runs_in_independent_worker_processes(tmp_path):
    catalog = OperatorCatalog.load(PACK / "catalog/operators")
    smoke = catalog.smoke_cases[("cartography.render-png", "1.0.0")]
    fixture = catalog.fixtures[smoke.inputs["data"].fixture_id]
    source = tmp_path / "extensionless"
    shutil.copyfile(catalog.fixture_root / fixture.path, source)
    results = []
    for number in range(2):
        payload = {
            "operatorId": "cartography.render-png",
            "operatorVersion": "1.0.0",
            "inputs": {
                "data": [
                    {"path": str(source), "mediaType": fixture.media_type, "format": fixture.format}
                ]
            },
            "parameters": smoke.parameters,
            "outputDirectory": str(tmp_path / f"output-{number}"),
        }
        payload_path, result_path = tmp_path / "payload.json", tmp_path / "result.json"
        payload_path.write_text(json.dumps(payload))
        subprocess.run(
            [sys.executable, "-m", "moperators.runtime.task", str(payload_path), str(result_path)],
            check=True,
            timeout=30,
            env={**os.environ, "PYTHONPATH": str(PACK / "src")},
        )
        result = json.loads(result_path.read_text())
        assert "error" not in result, result
        artifact = result["outputs"]["image"]
        assert_png(artifact)
        results.append(Path(artifact["path"]).read_bytes())
    assert results[0] == results[1]
