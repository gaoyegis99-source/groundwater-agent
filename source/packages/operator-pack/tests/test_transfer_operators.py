from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from jsonschema import Draft202012Validator

from moperators.catalog import OperatorCatalog
from moperators.operators import transfer_data, transfer_spatial
from moperators.operators.native import StagedInput
from moperators.operators.transfer_common import MEDIA, rule_schemas

PACK = Path(__file__).resolve().parents[1]
NEW_IDS = {
    "table.timeseries_unpivot",
    "timeseries.chronological_split",
    "preprocessing.standard_scaler_fit",
    "preprocessing.standard_scaler_transform",
    "preprocessing.standard_scaler_inverse",
    "timeseries.supervised_windows",
    "statistics.regression_metrics",
    "ml.gru_attention_pretrain",
    "ml.gru_attention_finetune",
    "ml.gru_attention_predict",
    "vector.from_xy",
    "vector.tag_membership",
    "vector.envelope",
    "raster.window",
}


def staged(path, fmt):
    return [StagedInput(path, MEDIA[fmt], fmt, {})]


def call_smoke(catalog, identity, directory):
    case = catalog.smoke_cases[(identity, "1.0.0")]
    directory.mkdir(parents=True)
    inputs = {}
    for port, ref in case.inputs.items():
        fixture = catalog.fixtures[ref.fixture_id]
        path = directory / port  # Production staging deliberately has no extension.
        shutil.copyfile(catalog.fixture_root / fixture.path, path)
        inputs[port] = [
            {
                "path": str(path),
                "mediaType": fixture.media_type,
                "format": fixture.format,
                "metadata": {"crs": "EPSG:0000"},
            }
        ]
    payload = {
        "operatorId": identity,
        "operatorVersion": "1.0.0",
        "inputs": inputs,
        "parameters": case.parameters,
        "outputDirectory": str(directory / "outputs"),
    }
    (directory / "payload.json").write_text(json.dumps(payload))
    subprocess.run(
        [
            sys.executable,
            "-m",
            "moperators.runtime.task",
            str(directory / "payload.json"),
            str(directory / "result.json"),
        ],
        check=True,
        timeout=120,
        env={
            **os.environ,
            "PYTHONPATH": str(PACK / "src"),
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
        },
    )
    result = json.loads((directory / "result.json").read_text())
    assert "error" not in result, result
    return result["outputs"]


@pytest.mark.parametrize("identity", sorted(NEW_IDS))
def test_new_operator_reproduces_artifact_bytes_in_independent_processes(identity, tmp_path):
    catalog = OperatorCatalog.load(PACK / "catalog/operators")
    operator = catalog.get(identity, "1.0.0")
    assert operator.memory_limit_bytes == (
        16 if operator.id in {"ml.gru_attention_pretrain", "ml.gru_attention_finetune"} else 8
    ) * 1024**3
    assert operator.timeout_seconds == (
        7200 if identity.endswith(("pretrain", "finetune")) else 1800
    )
    Draft202012Validator(operator.parameters_schema).validate(
        catalog.smoke_cases[(identity, "1.0.0")].parameters
    )
    outputs = [call_smoke(catalog, identity, tmp_path / str(i)) for i in range(2)]
    assert set(outputs[0]) == set(outputs[1])
    for port in outputs[0]:
        artifacts = [Path(output[port]["path"]).read_bytes() for output in outputs]
        assert hashlib.sha256(artifacts[0]).digest() == hashlib.sha256(artifacts[1]).digest(), port


def test_scaler_excludes_holdout_and_windows_follow_time_then_original_well_order(tmp_path):
    # Deliberately unsorted well IDs and very large held-out values expose leakage/order errors.
    rows = [
        {
            "well": well,
            "t": f"2020-{month + 1:02}-01",
            "series_order": order,
            "v": float(month + order) if month < 8 else 10000.0,
            "x": float(order),
            "y": 1.0,
        }
        for order, well in enumerate(["z", "a"])
        for month in range(10)
    ]
    path = tmp_path / "input"
    pq.write_table(pa.Table.from_pylist(rows), path)
    inputs = {"data": staged(path, "parquet")}
    split = transfer_data.split(
        "",
        inputs,
        {"groupField": "well", "timeField": "t", "trainRatio": 0.8, "inputWindow": 3},
        tmp_path / "split",
    )
    rules = tmp_path / "rules"
    rules.write_text(json.dumps({"schemaVersion": "standard-scaler-fields.v1", "fields": ["v"]}))
    fit = transfer_data.scaler_fit(
        "",
        {"data": staged(Path(split["fitRows"]["path"]), "parquet"), "rules": staged(rules, "json")},
        {},
        tmp_path / "fit",
    )
    state = json.loads(Path(fit["scaler"]["path"]).read_text())["features"][0]
    assert state["mean"] == 4 and state["count"] == 16
    assert state["variance"] == 5.5
    rules.write_text(
        json.dumps(
            {
                "schemaVersion": "supervised-window-fields.v1",
                "featureFields": ["v"],
                "carryFields": ["x", "y"],
            }
        )
    )
    output = transfer_data.windows(
        "",
        {
            **inputs,
            "targets": staged(Path(split["holdoutTargets"]["path"]), "parquet"),
            "rules": staged(rules, "json"),
        },
        {"groupField": "well", "timeField": "t", "targetField": "v", "inputWindow": 3},
        tmp_path / "windows",
    )
    index = pq.read_table(output["sampleIndex"]["path"]).to_pylist()
    assert [r["well"] for r in index] == ["z", "a", "z", "a"]
    x = np.load(output["features"]["path"], allow_pickle=False)
    assert x.shape == (4, 3, 1)
    np.testing.assert_array_equal(x[0, :, 0], [5, 6, 7])
    np.testing.assert_array_equal(x[2, :, 0], [6, 7, 10000])  # One-step observed holdout history.


def test_scaler_transform_retains_fitted_precision_across_fields_and_ranges(tmp_path):
    cases = {
        "small": (0.000123456789, 0.000456789123, [-0.000021, 0.000015, 0.00099, 0.00412]),
        "large": (1234500.123456789, 17.987654321, [1234400.0, 1234590.25, 1234620.5, 1234700.0]),
        "mixed": (1.23456789123, 3.456789123, [-8.0, -0.004125, 0.125, 18.333]),
    }
    arrays = {name: np.asarray(values, dtype=np.float32) for name, (_, _, values) in cases.items()}
    path, scaler = tmp_path / "data", tmp_path / "scaler"
    pq.write_table(pa.table({**arrays, "label": ["a", "b", "c", "d"]}), path)
    scaler.write_text(json.dumps({
        "schemaVersion": "standard-scaler.v1",
        "features": [
            {"field": name, "mean": mean, "scale": scale, "variance": scale ** 2, "count": 4}
            for name, (mean, scale, _) in cases.items()
        ],
    }))
    result = transfer_data.scaler_transform(
        "", {"data": staged(path, "parquet"), "scaler": staged(scaler, "json")},
        {}, tmp_path / "out",
    )
    actual = pq.read_table(result["data"]["path"])
    assert actual["label"].to_pylist() == ["a", "b", "c", "d"]
    for name, (mean, scale, _) in cases.items():
        # Independently compute each high-precision operation, rounding only its stored result.
        centered = (arrays[name].astype(np.float64) - mean).astype(np.float32)
        expected = (centered.astype(np.float64) / scale).astype(np.float32)
        assert actual.schema.field(name).type == pa.float32()
        np.testing.assert_array_equal(actual[name].to_numpy(), expected)


@pytest.mark.parametrize("mean,scale", [
    (0.000123456789, 0.000456789123), (1234500.123456789, 17.987654321),
    (1.23456789123, 3.456789123),
])
def test_scaler_inverse_retains_fitted_precision_for_multiple_output_fields(tmp_path, mean, scale):
    values = np.asarray([-1.234567, 0.012345, 0.99991, 2.7182818], dtype=np.float32)
    arrays = {"actual": values, "predicted": values[::-1], "persistence": values * np.float32(0.3)}
    path, scaler, rules = (tmp_path / name for name in ("data", "scaler", "rules"))
    pq.write_table(pa.table(arrays), path)
    scaler.write_text(json.dumps({
        "schemaVersion": "standard-scaler.v1", "features": [
            {"field": "signal", "mean": mean, "scale": scale, "variance": scale ** 2, "count": 4},
        ],
    }))
    rules.write_text(json.dumps({
        "schemaVersion": "standard-scaler-fields.v1", "fields": list(arrays),
    }))
    result = transfer_data.scaler_inverse(
        "", {
            "data": staged(path, "parquet"), "scaler": staged(scaler, "json"),
            "rules": staged(rules, "json"),
        },
        {"scalerField": "signal"}, tmp_path / "out",
    )
    actual = pq.read_table(result["data"]["path"])
    for name, values in arrays.items():
        unscaled = (values.astype(np.float64) * scale).astype(np.float32)
        expected = (unscaled.astype(np.float64) + mean).astype(np.float32)
        assert actual.schema.field(name).type == pa.float32()
        np.testing.assert_array_equal(actual[name].to_numpy(), expected)


def test_degenerate_metrics_are_explicit_nulls(tmp_path):
    path = tmp_path / "input"
    pq.write_table(
        pa.table(
            {
                "well": ["x", "x"],
                "actual": [2.0, 2.0],
                "predicted": [1.0, 3.0],
                "baseline": [2.0, 2.0],
            }
        ),
        path,
    )
    outputs = transfer_data.regression_metrics(
        "",
        {"predictions": staged(path, "parquet")},
        {
            "groupField": "well",
            "actualField": "actual",
            "predictedField": "predicted",
            "baselineField": "baseline",
            "unit": "m",
        },
        tmp_path / "out",
    )
    value = json.loads(Path(outputs["overall"]["path"]).read_text())
    assert value["mae"] == value["rmse"] == 1
    assert value["r2"] is None and value["r2_reason"] == "CONSTANT_OBSERVATIONS"
    assert (
        value["rmse_improvement_pct"] is None
        and value["improvement_reason"] == "ZERO_BASELINE_RMSE"
    )


def test_membership_preserves_boundary_and_outside_points(tmp_path):
    import pyogrio
    import shapely
    from pyproj import CRS

    path = tmp_path / "input"
    pq.write_table(
        pa.table({"x": [0.0, 1.0, 2.0], "y": [0.5, 0.5, 0.5], "id": ["inside", "edge", "outside"]}),
        path,
    )
    points = transfer_spatial.from_xy(
        "",
        {"data": staged(path, "parquet")},
        {"xField": "x", "yField": "y", "sourceCrs": "EPSG:4326"},
        tmp_path / "points",
    )
    boundary = tmp_path / "boundary.gpkg"
    transfer_spatial._write(
        pa.table({"id": [1]}),
        [shapely.box(-1, 0, 1, 1)],
        CRS.from_epsg(4326),
        "Polygon",
        "boundary",
        boundary,
    )
    result = transfer_spatial.tag_membership(
        "",
        {
            "points": staged(Path(points["points"]["path"]), "geopackage"),
            "polygons": staged(boundary, "geopackage"),
        },
        {"polygonLayer": "boundary", "outputField": "covered"},
        tmp_path / "tag",
    )
    _, data = pyogrio.read_arrow(result["points"]["path"], layer="points")
    assert data["covered"].to_pylist() == [True, True, False]
    assert data["id"].to_pylist() == ["inside", "edge", "outside"]


def test_array_rules_have_strict_published_schema():
    for name, schema in rule_schemas().items():
        assert schema == json.loads((PACK / "catalog/schemas" / f"{name}.schema.json").read_text())
        assert schema["additionalProperties"] is False


def test_static_dispatch_defers_heavy_dependencies():
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import moperators.static_implementations; "
                "assert not ({'torch', 'pyarrow', 'pyogrio', 'rasterio', 'scipy'} "
                "& sys.modules.keys())"
            ),
        ],
        check=True,
        env={**os.environ, "PYTHONPATH": str(PACK / "src")},
    )


def test_npy_boundary_rejects_pickle_arrays_and_archive_containers(tmp_path):
    from moperators.operators.transfer_common import array

    for fmt in ("pickle", "archive"):
        path = tmp_path / fmt
        with path.open("wb") as stream:
            if fmt == "pickle":
                np.save(stream, np.array([{"x": 1}], dtype=object), allow_pickle=True)
            else:
                np.savez(stream, values=np.ones((1, 2, 1), dtype=np.float32))
        with pytest.raises(ValueError, match=r"^TENSOR_SHAPE_INVALID$"):
            array({"features": staged(path, "npy")}, "features", 3)


def test_weights_boundary_rejects_checkpoint_objects_and_non_tensor_values(tmp_path):
    import torch

    from moperators.operators.gru_attention import inspect_weights

    path = tmp_path / "weights"
    torch.save({"model": {"state_dict": {"value": torch.zeros(1)}}}, path)
    with pytest.raises(ValueError, match=r"^MODEL_WEIGHTS_INVALID$"):
        inspect_weights(path)
    torch.save({"value": torch.tensor([float("nan")])}, path)
    with pytest.raises(ValueError, match=r"^MODEL_WEIGHTS_INVALID$"):
        inspect_weights(path)


def test_raster_window_enforces_98_percent_first_band_coverage(tmp_path):
    import rasterio
    import shapely
    from rasterio.transform import from_origin

    boundary = tmp_path / "extent.gpkg"
    transfer_spatial._write(
        pa.table({"id": [1]}),
        [shapely.box(0, 0, 10, 10)],
        "EPSG:32650",
        "Polygon",
        "extent",
        boundary,
    )
    rules = tmp_path / "rules"
    rules.write_text(json.dumps({"schemaVersion": "raster-window-bands.v1", "bands": [1, 2, 3]}))
    for missing in (2, 3):
        raster = tmp_path / f"image-{missing}"
        pixels = np.full((3, 10, 10), 100, dtype=np.uint8)
        pixels[0].flat[:missing] = 0
        with rasterio.open(
            raster,
            "w",
            driver="GTiff",
            width=10,
            height=10,
            count=3,
            dtype="uint8",
            nodata=0,
            crs="EPSG:32650",
            transform=from_origin(0, 10, 1, 1),
        ) as dataset:
            dataset.write(pixels)
        inputs = {
            "raster": staged(raster, "geotiff"),
            "extent": staged(boundary, "geopackage"),
            "rules": staged(rules, "json"),
        }
        parameters = {"extentLayer": "extent", "minimumCoverage": 0.98}
        if missing == 2:
            result = transfer_spatial.raster_window("", inputs, parameters, tmp_path / "accepted")
            assert json.loads(Path(result["coverage"]["path"]).read_text())["validFraction"] == 0.98
        else:
            with pytest.raises(ValueError, match=r"^IMAGERY_COVERAGE_INSUFFICIENT$"):
                transfer_spatial.raster_window("", inputs, parameters, tmp_path / "rejected")
