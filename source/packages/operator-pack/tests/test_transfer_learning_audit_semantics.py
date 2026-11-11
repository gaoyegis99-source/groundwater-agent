from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pyarrow as pa
import pyarrow.parquet as parquet

from moperators.operators import transfer_data, transfer_spatial
from moperators.operators.native import StagedInput
from moperators.operators.timeseries_snapshot import run as select_snapshot
from moperators.operators.transfer_common import MEDIA


def _staged(path: Path, format_id: str) -> list[StagedInput]:
    return [StagedInput(path, MEDIA[format_id], format_id, {})]


def test_target_split_audits_boundaries_and_scaler_excludes_holdout(
    tmp_path: Path,
) -> None:
    source = tmp_path / "target.parquet"
    parquet.write_table(
        pa.Table.from_pylist(
            [
                {
                    "well": "A",
                    "time": f"2024-{month:02d}-01",
                    "series_order": 0,
                    "value": value,
                }
                for month, value in enumerate([1.0, 2.0, 3.0, 4.0, 100.0, 200.0], start=1)
            ]
        ),
        source,
    )
    split = transfer_data.split(
        "",
        {"data": _staged(source, "parquet")},
        {
            "groupField": "well",
            "timeField": "time",
            "trainRatio": 0.67,
            "inputWindow": 2,
        },
        tmp_path / "split",
    )

    audit = json.loads(Path(split["split"]["path"]).read_text())
    assert audit == {
        "schemaVersion": "chronological-split.v1",
        "strategy": "shared-time-axis-chronological",
        "monthCount": 6,
        "trainMonthCount": 4,
        "holdoutMonthCount": 2,
        "groupCount": 1,
        "trainRatio": 0.67,
        "trainingStart": "2024-01-01T00:00:00",
        "trainingEnd": "2024-04-01T00:00:00",
        "holdoutStart": "2024-05-01T00:00:00",
        "holdoutEnd": "2024-06-01T00:00:00",
        "inputWindow": 2,
        "fitScope": "training-rows-only",
        "evaluationMode": "single-step-holdout-with-observed-history",
    }

    scaler_rules = tmp_path / "scaler-rules.json"
    scaler_rules.write_text(
        json.dumps({"schemaVersion": "standard-scaler-fields.v1", "fields": ["value"]})
    )
    scaler = transfer_data.scaler_fit(
        "",
        {
            "data": _staged(Path(split["fitRows"]["path"]), "parquet"),
            "rules": _staged(scaler_rules, "json"),
        },
        {},
        tmp_path / "scaler",
    )
    state = json.loads(Path(scaler["scaler"]["path"]).read_text())["features"][0]
    assert state["count"] == 4
    assert state["mean"] == 2.5

    scaled = transfer_data.scaler_transform(
        "",
        {
            "data": _staged(source, "parquet"),
            "scaler": _staged(Path(scaler["scaler"]["path"]), "json"),
        },
        {},
        tmp_path / "scaled",
    )
    window_rules = tmp_path / "window-rules.json"
    window_rules.write_text(
        json.dumps(
            {
                "schemaVersion": "supervised-window-fields.v1",
                "featureFields": ["value"],
                "carryFields": ["series_order"],
            }
        )
    )
    windows = transfer_data.windows(
        "",
        {
            "data": _staged(Path(scaled["data"]["path"]), "parquet"),
            "targets": _staged(Path(split["holdoutTargets"]["path"]), "parquet"),
            "rules": _staged(window_rules, "json"),
        },
        {
            "groupField": "well",
            "timeField": "time",
            "targetField": "value",
            "inputWindow": 2,
        },
        tmp_path / "windows",
    )
    features = np.load(windows["features"]["path"], allow_pickle=False)
    scale = state["scale"]
    np.testing.assert_allclose(
        features[:, :, 0],
        [
            [(3.0 - 2.5) / scale, (4.0 - 2.5) / scale],
            [(4.0 - 2.5) / scale, (100.0 - 2.5) / scale],
        ],
    )


def test_per_well_metrics_preserve_baseline_and_null_reasons(tmp_path: Path) -> None:
    source = tmp_path / "predictions.parquet"
    parquet.write_table(
        pa.table(
            {
                "well": ["A", "A", "B", "B"],
                "actual": [2.0, 2.0, 1.0, 3.0],
                "predicted": [1.0, 3.0, 1.0, 4.0],
                "persistence": [2.0, 2.0, 0.0, 2.0],
            }
        ),
        source,
    )
    outputs = transfer_data.regression_metrics(
        "",
        {"predictions": _staged(source, "parquet")},
        {
            "groupField": "well",
            "actualField": "actual",
            "predictedField": "predicted",
            "baselineField": "persistence",
            "unit": "m",
        },
        tmp_path / "metrics",
    )

    overall = json.loads(Path(outputs["overall"]["path"]).read_text())
    assert overall["schemaVersion"] == "regression-metrics.v1"
    assert overall["comparison"] == "model-vs-declared-baseline-on-identical-rows"
    assert overall["fields"] == {
        "group": "well",
        "actual": "actual",
        "predicted": "predicted",
        "baseline": "persistence",
    }

    per_well = parquet.read_table(outputs["perGroup"]["path"]).to_pylist()
    assert {row["well"] for row in per_well} == {"A", "B"}
    first = next(row for row in per_well if row["well"] == "A")
    assert first["r2"] is None
    assert first["r2_reason"] == "CONSTANT_OBSERVATIONS"
    assert first["baseline_rmse"] == 0
    assert first["baseline_r2"] is None
    assert first["baseline_r2_reason"] == "CONSTANT_OBSERVATIONS"
    assert first["rmse_improvement_pct"] is None
    assert first["improvement_reason"] == "ZERO_BASELINE_RMSE"
    assert first["unit"] == "m"


def test_imagery_coverage_records_mask_basis_threshold_and_bands(
    tmp_path: Path,
) -> None:
    import rasterio
    import shapely
    from rasterio.transform import from_origin

    extent = tmp_path / "extent.gpkg"
    transfer_spatial._write(
        pa.table({"id": [1]}),
        [shapely.box(0, 0, 10, 10)],
        "EPSG:32650",
        "Polygon",
        "extent",
        extent,
    )
    raster = tmp_path / "image.tif"
    pixels = np.full((3, 10, 10), 100, dtype=np.uint8)
    pixels[0].flat[:2] = 0
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
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"schemaVersion": "raster-window-bands.v1", "bands": [1, 2, 3]}))

    result = transfer_spatial.raster_window(
        "",
        {
            "raster": _staged(raster, "geotiff"),
            "extent": _staged(extent, "geopackage"),
            "rules": _staged(rules, "json"),
        },
        {"extentLayer": "extent", "minimumCoverage": 0.98},
        tmp_path / "window",
    )
    coverage = json.loads(Path(result["coverage"]["path"]).read_text())
    assert coverage == {
        "schemaVersion": "raster-window-coverage.v1",
        "basis": "first-selected-band-mask-within-requested-window",
        "selectedBands": [1, 2, 3],
        "maskBand": 1,
        "minimumRequiredFraction": 0.98,
        "validFraction": 0.98,
        "validPixels": 98,
        "totalPixels": 100,
        "meetsMinimumCoverage": True,
    }


def test_map_month_diagnostics_record_effective_selection_and_selected_time(
    tmp_path: Path,
) -> None:
    source = tmp_path / "predictions.parquet"
    parquet.write_table(
        pa.table(
            {
                "well": ["A", "A", "B"],
                "time": ["2024-11-01", "2024-12-01", "2024-11-01"],
                "predicted": [1.0, 2.0, 3.0],
            }
        ),
        source,
    )
    output = tmp_path / "snapshot"
    output.mkdir()
    result = select_snapshot(
        SimpleNamespace(path=source, format_id="parquet", metadata={}),
        {
            "groupField": "well",
            "timeField": "time",
            "valueField": "predicted",
            "targetTime": "2024-12-01",
            "selection": "exact",
            "maxAgeDays": 0,
        },
        output,
    )

    diagnostics = parquet.read_table(result["diagnostics"]["path"]).to_pylist()
    assert diagnostics == [
        {
            "well": "A",
            "status": "SELECTED",
            "target_time": "2024-12-01T00:00:00",
            "selected_time": "2024-12-01T00:00:00",
            "selection": "exact",
            "max_age_days": 0.0,
            "age_days": 0.0,
        },
        {
            "well": "B",
            "status": "NO_MATCH",
            "target_time": "2024-12-01T00:00:00",
            "selected_time": None,
            "selection": "exact",
            "max_age_days": 0.0,
            "age_days": None,
        },
    ]
    assert result["summary"]["value"] == {
        "groupCount": 2,
        "selectedCount": 1,
        "noMatchCount": 1,
        "maxAgeExceededCount": 0,
        "targetTime": "2024-12-01T00:00:00",
        "selection": "exact",
        "maxAgeDays": 0.0,
    }
