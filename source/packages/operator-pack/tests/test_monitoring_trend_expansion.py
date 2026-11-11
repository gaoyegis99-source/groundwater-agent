from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow.parquet as parquet
from jsonschema import Draft202012Validator

from moperators.operators import (
    seasonal_mann_kendall,
    statistics_pettitt,
    timeseries_gap_runs,
    timeseries_threshold_events,
)

PACK_ROOT = Path(__file__).resolve().parents[1]
COMMON = {"groupField": "station", "timeField": "time", "valueField": "value"}


@dataclass(frozen=True)
class StagedInput:
    path: Path
    media_type: str
    format_id: str
    metadata: dict[str, Any]


def _source(tmp_path: Path, rows: list[tuple[str, str, str]]) -> StagedInput:
    path = tmp_path / "observations.csv"
    body = "".join(f"{group},{moment},{value}\n" for group, moment, value in rows)
    path.write_text("station,time,value\n" + body, encoding="utf-8")
    return StagedInput(
        path=path,
        media_type="text/csv",
        format_id="csv",
        metadata={
            "dataContract": {
                "timezone": "UTC",
                "nullValues": ["", "NA"],
                "types": {"f2": "float64"},
                "fields": [
                    {"fieldId": "f0", "name": "station", "type": "string", "nullable": False},
                    {"fieldId": "f1", "name": "time", "type": "string", "nullable": False},
                    {"fieldId": "f2", "name": "value", "type": "double", "nullable": True},
                ],
            }
        },
    )


def _output(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.mkdir()
    return path


def _rows(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    return parquet.read_table(artifact["path"]).to_pylist()


def test_pettitt_synthetic_shift_reports_interpretation_and_missingness(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        [
            (
                "A",
                f"2024-{month:02d}-01T00:00:00Z",
                "NA" if month == 2 else str(1 if month < 6 else 10),
            )
            for month in range(1, 10)
        ],
    )

    result = statistics_pettitt.run(
        {"observations": [source]},
        {**COMMON, "alpha": 0.05, "frequency": "month"},
        _output(tmp_path, "pettitt"),
    )

    row = _rows(result["changePoints"])[0]
    assert row["status"] == "OK"
    assert row["observation_count"] == 8
    assert row["missing_value_count"] == 1
    summary = result["summary"]["value"]
    assert summary["analysisFrequency"] == "month"
    assert summary["minimumObservations"] == 4
    assert summary["interpretation"]
    assert summary["limitations"]


def test_seasonal_mann_kendall_requires_opt_in_and_explicit_season(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        [
            ("A", f"{year}-{month:02d}-01T00:00:00Z", str(year - 2020 + month / 100))
            for year in range(2021, 2025)
            for month in (1, 4, 7, 10)
        ],
    )
    disabled = seasonal_mann_kendall.run(
        {"observations": [source]},
        {**COMMON, "enabled": False, "alpha": 0.05},
        _output(tmp_path, "seasonal-disabled"),
    )
    assert disabled["summary"]["value"]["status"] == "NOT_REQUESTED"
    assert _rows(disabled["trends"]) == []

    result = seasonal_mann_kendall.run(
        {"observations": [source]},
        {**COMMON, "enabled": True, "seasonFrequency": "quarter", "alpha": 0.05},
        _output(tmp_path, "seasonal"),
    )

    row = _rows(result["trends"])[0]
    assert row["status"] == "OK"
    assert row["season_count"] == 4
    assert row["cycle_count"] == 4
    summary = result["summary"]["value"]
    assert summary["seasonFrequency"] == "quarter"
    assert summary["minimumCycles"] == 2
    assert summary["interpretation"]
    assert summary["limitations"]


def test_gap_runs_reports_contiguous_runs_separately_from_completeness(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        [
            ("A", "2024-01-01T00:00:00Z", "1"),
            ("A", "2024-04-01T00:00:00Z", "4"),
            ("A", "2024-06-01T00:00:00Z", "6"),
        ],
    )

    result = timeseries_gap_runs.run(
        {"observations": [source]},
        {
            **COMMON,
            "frequency": "month",
            "expectedStart": "2024-01-01",
            "expectedEnd": "2024-06-30",
        },
        _output(tmp_path, "gaps"),
    )

    gaps = _rows(result["gaps"])
    actual = [
        (row["gap_start"].date(), row["gap_end"].date(), row["missing_period_count"])
        for row in gaps
    ]
    assert actual == [
        (date(2024, 2, 1), date(2024, 3, 1), 2),
        (date(2024, 5, 1), date(2024, 5, 1), 1),
    ]
    summary = result["summary"]["value"]
    assert summary["missingPeriodCount"] == 3
    assert summary["gapRunCount"] == 2
    assert "contiguous" in summary["interpretation"].lower()
    assert "overall completeness" in summary["limitations"][0].lower()


def test_threshold_events_are_disabled_without_an_explicit_user_threshold(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        [
            ("A", "2024-01-01T00:00:00Z", "1"),
            ("A", "2024-02-01T00:00:00Z", "3"),
            ("A", "2024-03-01T00:00:00Z", "4"),
            ("A", "2024-04-01T00:00:00Z", "1"),
        ],
    )
    disabled = timeseries_threshold_events.run(
        {"observations": [source]},
        {**COMMON, "frequency": "month", "enabled": False},
        _output(tmp_path, "threshold-disabled"),
    )
    assert disabled["summary"]["value"]["status"] == "NOT_REQUESTED"
    assert _rows(disabled["events"]) == []
    assert _rows(disabled["classified"]) == []

    result = timeseries_threshold_events.run(
        {"observations": [source]},
        {
            **COMMON,
            "frequency": "month",
            "enabled": True,
            "comparison": "above",
            "thresholdSource": "scalar",
            "thresholdValue": 2.0,
        },
        _output(tmp_path, "threshold-enabled"),
    )
    assert len(_rows(result["events"])) == 1
    summary = result["summary"]["value"]
    assert summary["thresholdValue"] == 2.0
    assert summary["comparison"] == "above"
    assert summary["interpretation"]
    assert summary["limitations"]


def test_operator_schemas_keep_legacy_calls_and_allow_explicitly_disabled_branches() -> None:
    seasonal = json.loads(
        (
            PACK_ROOT
            / "catalog/operators/statistics.seasonal_mann_kendall/1.0.0/manifest.json"
        ).read_text()
    )["parametersSchema"]
    threshold = json.loads(
        (
            PACK_ROOT
            / "catalog/operators/timeseries.threshold_events/1.0.0/manifest.json"
        ).read_text()
    )["parametersSchema"]

    Draft202012Validator(seasonal).validate({**COMMON, "enabled": False})
    Draft202012Validator(seasonal).validate({**COMMON, "seasonFrequency": "month"})
    Draft202012Validator(threshold).validate({**COMMON, "frequency": "month", "enabled": False})
    Draft202012Validator(threshold).validate({
        **COMMON,
        "frequency": "month",
        "comparison": "above",
        "thresholdSource": "scalar",
        "thresholdValue": 2.0,
    })
