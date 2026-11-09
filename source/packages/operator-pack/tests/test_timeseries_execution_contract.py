from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import pyarrow.parquet as parquet
import pytest

from moperators.operators import (
    linear_trend,
    mad_outliers,
    mann_kendall,
    period_change,
    timeseries_aggregate,
    timeseries_completeness,
)
from moperators.operators.native import StagedInput

COMMON = {"groupField": "station", "timeField": "time", "valueField": "value"}
OPERATOR_NAMES = (
    "aggregate",
    "completeness",
    "linear-trend",
    "mann-kendall",
    "mad-outliers",
    "period-change",
)
TIME_AWARE_OPERATOR_NAMES = tuple(name for name in OPERATOR_NAMES if name != "mad-outliers")
EDGE_ROWS = (
    "A,2024-01-01T00:00:00+08:00,10\n"
    "A,2024-02-01T00:00:00+08:00,12\n"
    "B,2024-01-01T00:00:00+08:00,20\n"
    "B,2024-02-01T00:00:00+08:00,NA\n"
    "C,2024-01-01T00:00:00+08:00,NA\n"
    "C,2024-02-01T00:00:00+08:00,NA\n"
)


def _source(
    tmp_path: Path,
    *,
    rows: str = EDGE_ROWS,
    timezone: str | None = "Asia/Shanghai",
    null_values: list[str] | None = None,
) -> StagedInput:
    path = tmp_path / "observations.csv"
    path.write_text("station,time,value\n" + rows, encoding="utf-8")
    data_contract: dict[str, Any] = {
        "nullValues": ["", "NA"] if null_values is None else null_values,
        "types": {"f2": "float64"},
        "fields": [
            {"fieldId": "f0", "name": "station", "type": "string", "nullable": True},
            {"fieldId": "f1", "name": "time", "type": "string", "nullable": True},
            {"fieldId": "f2", "name": "value", "type": "double", "nullable": True},
        ],
    }
    if timezone is not None:
        data_contract["timezone"] = timezone
    return StagedInput(
        path=path,
        media_type="text/csv",
        format_id="csv",
        metadata={"dataContract": data_contract},
    )


def _output(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.mkdir()
    return path


def _rows(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    return parquet.read_table(artifact["path"]).to_pylist()


def _by_station(artifact: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["station"]: row for row in _rows(artifact)}


def _run_operator(name: str, source: StagedInput, output_dir: Path) -> dict[str, Any]:
    if name == "aggregate":
        return timeseries_aggregate.run(source, {**COMMON, "frequency": "month"}, output_dir)
    if name == "completeness":
        return timeseries_completeness.run(
            source,
            {
                **COMMON,
                "frequency": "month",
                "expectedStart": "2024-01-01",
                "expectedEnd": "2024-02-29T23:59:59",
            },
            output_dir,
        )
    if name == "linear-trend":
        return linear_trend.run(source, COMMON, output_dir)
    if name == "mann-kendall":
        return mann_kendall.run(source, {**COMMON, "alpha": 0.05}, output_dir)
    if name == "mad-outliers":
        return mad_outliers.run(
            source,
            {"groupField": "station", "valueField": "value", "threshold": 3.5},
            output_dir,
        )
    if name == "period-change":
        return period_change.run(
            source,
            {
                **COMMON,
                "baselineStart": "2024-01-01",
                "baselineEnd": "2024-01-31T23:59:59",
                "comparisonStart": "2024-02-01",
                "comparisonEnd": "2024-02-29T23:59:59",
                "aggregation": "mean",
            },
            output_dir,
        )
    raise AssertionError(f"unknown operator test case: {name}")


def test_aggregate_preserves_local_month_boundary_and_empty_group(tmp_path: Path) -> None:
    result = _run_operator("aggregate", _source(tmp_path), _output(tmp_path, "aggregate"))
    rows = _rows(result["series"])
    evaluated = {
        (row["station"], row["period_start"]): row for row in rows if row["status"] == "EVALUATED"
    }
    assert evaluated[("A", "2024-01-01T00:00:00+08:00")]["mean"] == 10.0
    assert evaluated[("A", "2024-02-01T00:00:00+08:00")]["mean"] == 12.0
    assert evaluated[("B", "2024-01-01T00:00:00+08:00")]["observation_count"] == 1
    absent = next(row for row in rows if row["station"] == "C")
    assert absent == {
        "station": "C",
        "period_start": None,
        "observation_count": 0,
        "minimum": None,
        "maximum": None,
        "mean": None,
        "median": None,
        "stddev": None,
        "status": "NOT_EVALUABLE",
    }
    assert result["summary"]["value"] == {
        "groupPeriodCount": 3,
        "notEvaluableGroupCount": 1,
    }


def test_aggregate_uses_declared_timezone_across_utc_month_boundary(
    tmp_path: Path,
) -> None:
    source = _source(
        tmp_path,
        rows=("A,2024-01-31T15:59:59Z,10\nA,2024-01-31T16:00:00Z,12\n"),
    )
    result = _run_operator("aggregate", source, _output(tmp_path, "utc-boundary"))
    rows = _rows(result["series"])

    assert [(row["period_start"], row["mean"]) for row in rows] == [
        ("2024-01-01T00:00:00+08:00", 10.0),
        ("2024-02-01T00:00:00+08:00", 12.0),
    ]


def test_completeness_counts_declared_missing_and_empty_group(tmp_path: Path) -> None:
    result = _run_operator("completeness", _source(tmp_path), _output(tmp_path, "completeness"))
    profiles = _by_station(result["profiles"])
    assert profiles["A"]["observed_period_count"] == 2
    assert profiles["A"]["completeness_ratio"] == 1.0
    assert profiles["B"]["observed_period_count"] == 1
    assert profiles["B"]["missing_period_count"] == 1
    assert profiles["B"]["null_value_count"] == 1
    assert profiles["C"]["observed_period_count"] == 0
    assert profiles["C"]["missing_period_count"] == 2
    assert profiles["C"]["null_value_count"] == 2
    assert profiles["C"]["status"] == "NOT_EVALUABLE"
    assert result["summary"]["value"] == {"groupCount": 3}


def test_linear_trend_retains_metrics_and_empty_group_status(tmp_path: Path) -> None:
    result = _run_operator("linear-trend", _source(tmp_path), _output(tmp_path, "linear"))
    trends = _by_station(result["trends"])
    assert trends["A"]["status"] == "EVALUATED"
    assert trends["A"]["observation_count"] == 2
    assert trends["A"]["slope_per_day"] == pytest.approx(2 / 31)
    assert trends["A"]["slope_per_year"] == pytest.approx(2 / 31 * 365.2425)
    assert trends["A"]["intercept_at_first_observation"] == pytest.approx(10.0)
    assert trends["A"]["r_squared"] == pytest.approx(1.0)
    assert trends["A"]["evidence_band"] == "TWO_POINT"
    assert trends["B"]["status"] == "NOT_EVALUABLE"
    assert trends["C"]["status"] == "NOT_EVALUABLE"
    assert result["summary"]["value"] == {"groupCount": 3}


def test_mann_kendall_retains_metrics_and_empty_group_status(tmp_path: Path) -> None:
    result = _run_operator("mann-kendall", _source(tmp_path), _output(tmp_path, "mann"))
    trends = _by_station(result["trends"])
    assert trends["A"]["status"] == "EVALUATED"
    assert trends["A"]["observation_count"] == 2
    assert trends["A"]["s"] == 1
    assert trends["A"]["variance_s"] == pytest.approx(1.0)
    assert trends["A"]["kendall_tau"] == pytest.approx(1.0)
    assert trends["A"]["sen_slope_per_day"] == pytest.approx(2 / 31)
    assert trends["A"]["significant"] is False
    assert trends["B"]["status"] == "NOT_EVALUABLE"
    assert trends["C"]["status"] == "NOT_EVALUABLE"
    assert result["summary"]["value"] == {"groupCount": 3, "alpha": 0.05}


@pytest.mark.parametrize(
    ("operator_name", "metric"),
    (("linear-trend", "slope_per_day"), ("mann-kendall", "sen_slope_per_day")),
)
def test_first_sorted_empty_group_does_not_drop_later_trend_metrics(
    tmp_path: Path, operator_name: str, metric: str
) -> None:
    source = _source(
        tmp_path,
        rows=(
            "A-ABSENT,2024-01-01T00:00:00+08:00,NA\n"
            "A-ABSENT,2024-02-01T00:00:00+08:00,NA\n"
            "Z-COMPLETE,2024-01-01T00:00:00+08:00,10\n"
            "Z-COMPLETE,2024-02-01T00:00:00+08:00,12\n"
        ),
    )
    result = _run_operator(operator_name, source, _output(tmp_path, operator_name))
    trends = _by_station(result["trends"])
    assert trends["A-ABSENT"]["status"] == "NOT_EVALUABLE"
    assert trends["Z-COMPLETE"]["status"] == "EVALUATED"
    assert trends["Z-COMPLETE"][metric] == pytest.approx(2 / 31)


def test_mad_outliers_preserves_missing_rows_and_empty_group(tmp_path: Path) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        result = _run_operator("mad-outliers", _source(tmp_path), _output(tmp_path, "outliers"))
    rows = _rows(result["observations"])
    assert {row["status"] for row in rows if row["station"] == "A"} == {"EVALUATED"}
    assert {row["status"] for row in rows if row["station"] == "B"} == {
        "EVALUATED",
        "MISSING_VALUE",
    }
    assert {row["status"] for row in rows if row["station"] == "C"} == {"NOT_EVALUABLE"}
    assert all(row["is_outlier"] is None for row in rows if row["value"] == "NA")
    assert result["summary"]["value"] == {
        "rowCount": 6,
        "outlierCount": 0,
        "missingValueCount": 3,
        "notEvaluableGroupCount": 1,
        "threshold": 3.5,
    }


def test_period_change_keeps_partial_and_empty_groups_explicit(tmp_path: Path) -> None:
    result = _run_operator("period-change", _source(tmp_path), _output(tmp_path, "period-change"))
    changes = _by_station(result["changes"])
    assert changes["A"] == {
        "station": "A",
        "baseline_value": 10.0,
        "comparison_value": 12.0,
        "absolute_change": 2.0,
        "percent_change": 20.0,
        "status": "COMPLETE",
    }
    assert changes["B"]["status"] == "NOT_EVALUABLE"
    assert changes["B"]["baseline_value"] == 20.0
    assert changes["B"]["comparison_value"] is None
    assert changes["C"]["status"] == "NOT_EVALUABLE"
    assert result["summary"]["value"] == {"groupCount": 3, "aggregation": "mean"}


@pytest.mark.parametrize("operator_name", OPERATOR_NAMES)
def test_each_operator_rejects_unexpected_non_numeric_value(
    tmp_path: Path, operator_name: str
) -> None:
    source = _source(
        tmp_path,
        rows="A,2024-01-01T00:00:00+08:00,INVALID\n",
    )
    with pytest.raises(ValueError, match="NON_NUMERIC_VALUE"):
        _run_operator(operator_name, source, _output(tmp_path, operator_name))


@pytest.mark.parametrize("operator_name", OPERATOR_NAMES)
def test_na_is_missing_only_when_declared(tmp_path: Path, operator_name: str) -> None:
    source = _source(
        tmp_path,
        rows="A,2024-01-01T00:00:00+08:00,NA\n",
        null_values=[],
    )
    with pytest.raises(ValueError, match="NON_NUMERIC_VALUE"):
        _run_operator(operator_name, source, _output(tmp_path, operator_name))


@pytest.mark.parametrize("operator_name", TIME_AWARE_OPERATOR_NAMES)
def test_offset_timestamp_requires_declared_calendar_timezone(
    tmp_path: Path, operator_name: str
) -> None:
    source = _source(
        tmp_path,
        rows="A,2024-01-01T00:00:00+08:00,10\n",
        timezone=None,
    )
    with pytest.raises(ValueError, match="TIMEZONE_UNSUPPORTED"):
        _run_operator(operator_name, source, _output(tmp_path, operator_name))
