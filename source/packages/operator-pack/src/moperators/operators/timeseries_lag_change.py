from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import pyarrow as pa

from moperators.operators.advanced_timeseries_common import (
    bucket,
    column,
    ensure_unique,
    numbers,
    one,
    read_table,
    shift_period,
    timestamps,
    value_summary,
    write_rows,
)
from moperators.operators.analysis_common import input_null_values, input_timezone


def run(inputs: dict[str, Any], parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    source = one(inputs)
    timezone_ = input_timezone(source)
    lag = int(parameters["lagPeriods"])
    if lag < 1 or lag > 120:
        raise ValueError("LAG_RANGE_INVALID")
    group_field = parameters["groupField"]
    time_field = parameters["timeField"]
    value_field = parameters["valueField"]
    frequency = parameters["frequency"]
    table = read_table(source, time_field, value_field)
    groups = column(table, group_field).to_pylist()
    periods = [bucket(value, frequency) for value in timestamps(table, time_field, timezone_)]
    values = numbers(table, value_field, input_null_values(source))
    ensure_unique(zip(groups, periods, strict=True), "DUPLICATE_PERIOD")
    grouped: dict[Any, dict[Any, float]] = defaultdict(dict)
    for group, period, value in zip(groups, periods, values, strict=True):
        if value is None:
            continue
        grouped[group][period] = value

    def rows() -> Any:
        for group in sorted(grouped, key=str):
            series = grouped[group]
            for period, current in sorted(series.items()):
                lag_period = shift_period(period, frequency, -lag)
                previous = series.get(lag_period)
                change = current - previous if previous is not None else None
                if previous is None:
                    percent = None
                    status = "NOT_EVALUABLE"
                elif previous == 0:
                    percent = None
                    status = "ZERO_BASELINE"
                else:
                    if change is None:
                        raise AssertionError("available lag value must produce a change")
                    percent = change / previous * 100
                    status = "OK"
                yield {
                    group_field: group,
                    time_field: period,
                    value_field: current,
                    "lag_periods": lag,
                    "lag_time": lag_period,
                    "lag_value": previous,
                    "absolute_change": change,
                    "percent_change": percent,
                    "status": status,
                }

    schema = pa.schema(
        [
            pa.field(group_field, table.schema.field(group_field).type),
            pa.field(time_field, pa.timestamp("us", tz=timezone_.key if timezone_ else None)),
            pa.field(value_field, pa.float64()),
            pa.field("lag_periods", pa.int32(), nullable=False),
            pa.field("lag_time", pa.timestamp("us", tz=timezone_.key if timezone_ else None)),
            pa.field("lag_value", pa.float64()),
            pa.field("absolute_change", pa.float64()),
            pa.field("percent_change", pa.float64()),
            pa.field("status", pa.string(), nullable=False),
        ]
    )
    return {
        "changes": write_rows(rows(), output_dir / "lag-changes.parquet", schema),
        "summary": value_summary(
            {
                "groupCount": len(grouped),
                "rowCount": sum(value is not None for value in values),
                "lagPeriods": lag,
            }
        ),
    }
