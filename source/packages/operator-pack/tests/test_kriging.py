from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import xy

from moperators.operators.native import StagedInput, run_native_operator


def _write_points(path: Path, rows: list[tuple[float, float, float]]) -> StagedInput:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["x", "y", "value"])
        writer.writeheader()
        for x_value, y_value, value in rows:
            writer.writerow({"x": x_value, "y": y_value, "value": value})
    return StagedInput(path, "text/csv", "csv", {"dataContract": {"nullValues": []}})


def _parameters(**overrides: object) -> dict[str, object]:
    parameters: dict[str, object] = {
        "valueField": "value",
        "xField": "x",
        "yField": "y",
        "sourceCrs": "EPSG:3857",
        "targetCrs": "EPSG:3857",
        "pixelSize": 1.0,
        "krigingType": "ordinary",
        "variogramModel": "spherical",
        "neighborCount": 12,
        "minimumNeighbors": 4,
        "exact": True,
        "variogramBinCount": 8,
        "idwPower": 2.0,
        "valueUnit": "m",
    }
    parameters.update(overrides)
    return parameters


def _run(
    tmp_path: Path,
    rows: list[tuple[float, float, float]],
    *,
    name: str,
    parameters: dict[str, object],
) -> dict[str, dict]:
    source = _write_points(tmp_path / f"{name}.csv", rows)
    return run_native_operator(
        "points.interpolate.kriging",
        {"points": [source]},
        parameters,
        tmp_path / name,
    )


def _artifact_path(output: dict) -> Path:
    assert output["kind"] == "artifact"
    return Path(output["path"])


def _value(output: dict) -> dict:
    assert output["kind"] == "value"
    assert isinstance(output["value"], dict)
    return output["value"]


def test_universal_kriging_reproduces_planar_drift_and_loo_metrics(
    tmp_path: Path,
) -> None:
    rows = [
        (float(x_value), float(y_value), 2.0 + 0.5 * x_value - 0.25 * y_value)
        for y_value in range(3)
        for x_value in range(3)
    ]
    outputs = _run(
        tmp_path,
        rows,
        name="universal",
        parameters=_parameters(
            krigingType="universal",
            neighborCount=9,
            extentMinX=-0.5,
            extentMinY=-0.5,
            extentMaxX=2.5,
            extentMaxY=2.5,
            variogramRange=2.5,
            variogramSill=2.0,
            variogramNugget=0.0,
        ),
    )

    with rasterio.open(_artifact_path(outputs["prediction"])) as dataset:
        prediction = dataset.read(1)
        expected = np.empty_like(prediction, dtype=np.float64)
        for row_index in range(dataset.height):
            for column_index in range(dataset.width):
                x_value, y_value = xy(dataset.transform, row_index, column_index, offset="center")
                expected[row_index, column_index] = 2.0 + 0.5 * x_value - 0.25 * y_value
        np.testing.assert_allclose(prediction, expected, atol=1e-9, rtol=1e-9)

    summary = _value(outputs["summary"])
    diagnostics = _value(outputs["variogramDiagnostics"])
    assert summary["krigingType"] == "universal"
    assert summary["drift"] == "linear-x-y"
    assert summary["trainingExactness"]["maxAbsoluteError"] <= 1e-10
    assert summary["loo"]["meanError"] == pytest.approx(0.0, abs=1e-9)
    assert summary["loo"]["rmse"] == pytest.approx(0.0, abs=1e-9)
    assert summary["loo"]["rmsse"] == pytest.approx(0.0, abs=1e-8)
    assert summary["idwLooComparison"]["rmse"] >= 0
    assert "not guaranteed" in summary["idwLooComparison"]["interpretation"].lower()
    assert summary["idwGridComparison"]["comparisonGrid"] == "same-grid"
    assert summary["idwGridComparison"]["commonCellCount"] == 9
    assert summary["idwGridComparison"]["rmseDifference"] >= 0
    assert "not guaranteed" in summary["idwGridComparison"]["interpretation"].lower()
    assert diagnostics["driftBasis"] == ["intercept", "x", "y"]
    assert diagnostics["fitSource"] == "user-specified"

    with _artifact_path(outputs["loo"]).open(encoding="utf-8", newline="") as stream:
        loo_rows = list(csv.DictReader(stream))
    assert len(loo_rows) == len(rows)
    assert all(row["status"] == "EVALUATED" for row in loo_rows)
    assert max(abs(float(row["residual"])) for row in loo_rows) <= 1e-8


def test_exact_mode_has_zero_variance_at_wells_and_positive_between_them(
    tmp_path: Path,
) -> None:
    rows = [(0.0, 0.0, 0.0), (2.0, 0.0, 1.0), (0.0, 2.0, 1.0), (2.0, 2.0, 0.0)]
    outputs = _run(
        tmp_path,
        rows,
        name="variance",
        parameters=_parameters(
            neighborCount=4,
            extentMinX=-0.5,
            extentMinY=-0.5,
            extentMaxX=2.5,
            extentMaxY=2.5,
            variogramRange=3.0,
            variogramSill=1.0,
            variogramNugget=0.0,
        ),
    )

    with rasterio.open(_artifact_path(outputs["variance"])) as dataset:
        variance = dataset.read(1)
    assert np.all(np.isfinite(variance))
    assert np.min(variance) >= 0
    np.testing.assert_allclose(
        variance[[0, 0, 2, 2], [0, 2, 0, 2]],
        np.zeros(4),
        atol=1e-10,
    )
    assert variance[1, 1] > 0


def test_fixed_seed_auto_variogram_is_reproducible(tmp_path: Path) -> None:
    generator = np.random.default_rng(20261110)
    coordinates = generator.uniform(0.25, 3.75, size=(14, 2))
    values = np.sin(coordinates[:, 0]) + 0.3 * coordinates[:, 1]
    rows = [
        (float(point[0]), float(point[1]), float(value))
        for point, value in zip(coordinates, values, strict=True)
    ]
    parameters = _parameters(
        neighborCount=10,
        extentMinX=0.0,
        extentMinY=0.0,
        extentMaxX=4.0,
        extentMaxY=4.0,
    )

    first = _run(tmp_path, rows, name="first", parameters=parameters)
    second = _run(tmp_path, rows, name="second", parameters=parameters)

    for port in ("prediction", "variance"):
        with rasterio.open(_artifact_path(first[port])) as first_dataset:
            first_values = first_dataset.read(1)
            first_transform = first_dataset.transform
        with rasterio.open(_artifact_path(second[port])) as second_dataset:
            second_values = second_dataset.read(1)
            second_transform = second_dataset.transform
        np.testing.assert_array_equal(first_values, second_values)
        assert first_transform == second_transform
    assert _value(first["variogramDiagnostics"]) == _value(second["variogramDiagnostics"])
    assert _value(first["summary"]) == _value(second["summary"])
    assert _artifact_path(first["loo"]).read_bytes() == _artifact_path(second["loo"]).read_bytes()


@pytest.mark.parametrize(
    ("rows", "overrides", "error"),
    [
        (
            [(0.0, 0.0, 1.0), (1.0, 0.0, 2.0), (0.0, 1.0, 3.0), (1.0, 1.0, 4.0)],
            {"targetCrs": "EPSG:4326"},
            "PROJECTED_CRS_REQUIRED",
        ),
        (
            [(0.0, 0.0, 1.0), (0.0, 0.0, 2.0), (0.0, 1.0, 3.0), (1.0, 1.0, 4.0)],
            {},
            "DUPLICATE_COORDINATES",
        ),
        (
            [(0.0, 0.0, 1.0), (1.0, 0.0, 2.0), (2.0, 0.0, 3.0), (3.0, 0.0, 4.0)],
            {"krigingType": "universal"},
            "DRIFT_RANK_DEFICIENT",
        ),
        (
            [(0.0, 0.0, 1.0), (1.0, 0.0, 2.0), (0.0, 1.0, 3.0)],
            {},
            "INSUFFICIENT_INTERPOLATION_POINTS",
        ),
        (
            [(0.0, 0.0, 1.0), (1.0, 0.0, 2.0), (0.0, 1.0, 3.0), (1.0, 1.0, 4.0)],
            {"extentMinX": 0.0},
            "EXTENT_INCOMPLETE",
        ),
        (
            [(0.0, 0.0, 1.0), (1.0, 0.0, 2.0), (0.0, 1.0, 3.0), (1.0, 1.0, 4.0)],
            {"variogramRange": 2.0},
            "VARIOGRAM_PARAMETERS_INCOMPLETE",
        ),
        (
            [(0.0, 0.0, 1.0), (1.0, 0.0, 2.0), (0.0, 1.0, 3.0), (1.0, 1.0, 4.0)],
            {"idwPower": 0.0},
            "IDW_POWER_INVALID",
        ),
    ],
)
def test_invalid_kriging_inputs_fail_stably(
    tmp_path: Path,
    rows: list[tuple[float, float, float]],
    overrides: dict[str, object],
    error: str,
) -> None:
    with pytest.raises(ValueError, match=f"^{error}$"):
        _run(
            tmp_path,
            rows,
            name=error.lower(),
            parameters=_parameters(**overrides),
        )


def test_outputs_keep_prediction_variance_diagnostics_loo_and_summary_separate(
    tmp_path: Path,
) -> None:
    outputs = _run(
        tmp_path,
        [(0.0, 0.0, 1.0), (2.0, 0.0, 2.0), (0.0, 2.0, 2.0), (2.0, 2.0, 1.0)],
        name="ports",
        parameters=_parameters(
            neighborCount=4,
            extentMinX=0.0,
            extentMinY=0.0,
            extentMaxX=2.0,
            extentMaxY=2.0,
        ),
    )

    assert set(outputs) == {
        "prediction",
        "variance",
        "variogramDiagnostics",
        "loo",
        "summary",
    }
    assert outputs["prediction"]["format"] == "geotiff"
    assert outputs["variance"]["format"] == "geotiff"
    assert outputs["loo"]["format"] == "csv"
    assert outputs["variogramDiagnostics"]["kind"] == "value"
    assert outputs["summary"]["kind"] == "value"
