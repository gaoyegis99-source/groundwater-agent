from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa

from moperators.operators.advanced_timeseries_common import (
    column,
    ensure_unique,
    numbers,
    one,
    read_table,
    timestamp,
    timestamps,
    value_summary,
    write_rows,
)


def run(inputs: dict[str, Any], parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    season_frequency = parameters["seasonFrequency"]
    if season_frequency not in {"month", "quarter"}:
        raise ValueError("FREQUENCY_UNSUPPORTED")
    start = timestamp(parameters["baselineStart"])
    end = timestamp(parameters["baselineEnd"])
    minimum = int(parameters["minBaselineObservations"])
    if start > end:
        raise ValueError("TIME_RANGE_INVALID")
    if minimum < 1:
        raise ValueError("INSUFFICIENT_OBSERVATIONS")
    group_field = parameters["groupField"]
    time_field = parameters["timeField"]
    value_field = parameters["valueField"]
    table = read_table(one(inputs))
    groups = column(table, group_field).to_pylist()
    moments = timestamps(table, time_field)
    values = numbers(table, value_field)
    ensure_unique(zip(groups, moments, strict=True), "DUPLICATE_TIMESTAMP")
    baseline: dict[tuple[Any, int], list[float]] = defaultdict(list)
    source_rows: list[tuple[Any, Any, float]] = []
    for group, moment, value in zip(groups, moments, values, strict=True):
        if value is None:
            raise AssertionError("strict numeric parsing cannot return null")
        source_rows.append((group, moment, value))
        if start <= moment <= end:
            baseline[(group, _season(moment, season_frequency))].append(value)

    def rows() -> Any:
        for group, moment, value in sorted(source_rows, key=lambda row: (str(row[0]), row[1])):
            samples = np.asarray(
                baseline[(group, _season(moment, season_frequency))], dtype=np.float64
            )
            count = len(samples)
            mean = float(np.mean(samples)) if count >= minimum else None
            stddev = float(np.std(samples)) if count >= minimum else None
            anomaly = value - mean if mean is not None else None
            if count < minimum:
                status = "NOT_EVALUABLE"
            elif stddev == 0:
                status = "ZERO_VARIANCE"
            else:
                status = "OK"
            yield {
                group_field: group,
                time_field: moment,
                value_field: value,
                "season": _season(moment, season_frequency),
                "baseline_count": count,
                "baseline_mean": mean,
                "baseline_population_stddev": stddev,
                "anomaly": anomaly,
                "standardized_anomaly": (
                    anomaly / stddev if anomaly is not None and stddev not in {None, 0} else None
                ),
                "status": status,
            }

    schema = pa.schema(
        [
            pa.field(group_field, table.schema.field(group_field).type),
            pa.field(time_field, pa.timestamp("us")),
            pa.field(value_field, pa.float64()),
            pa.field("season", pa.int8(), nullable=False),
            pa.field("baseline_count", pa.int64(), nullable=False),
            pa.field("baseline_mean", pa.float64()),
            pa.field("baseline_population_stddev", pa.float64()),
            pa.field("anomaly", pa.float64()),
            pa.field("standardized_anomaly", pa.float64()),
            pa.field("status", pa.string(), nullable=False),
        ]
    )
    return {
        "anomalies": write_rows(rows(), output_dir / "seasonal-anomalies.parquet", schema),
        "summary": value_summary({"groupCount": len(set(groups)), "rowCount": len(moments)}),
    }


def _season(moment: Any, frequency: str) -> int:
    return moment.month if frequency == "month" else (moment.month - 1) // 3 + 1
