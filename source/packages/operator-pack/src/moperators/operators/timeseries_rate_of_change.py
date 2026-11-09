from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import pyarrow as pa

from moperators.operators.advanced_timeseries_common import (
    column,
    ensure_unique,
    numbers,
    one,
    read_table,
    timestamps,
    value_summary,
    write_rows,
)


def run(inputs: dict[str, Any], parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    group_field = parameters["groupField"]
    time_field = parameters["timeField"]
    value_field = parameters["valueField"]
    table = read_table(one(inputs))
    groups = column(table, group_field).to_pylist()
    moments = timestamps(table, time_field)
    values = numbers(table, value_field)
    ensure_unique(zip(groups, moments, strict=True), "DUPLICATE_TIMESTAMP")
    grouped: dict[Any, list[tuple[Any, float]]] = defaultdict(list)
    for group, moment, value in zip(groups, moments, values, strict=True):
        if value is None:
            raise AssertionError("strict numeric parsing cannot return null")
        grouped[group].append((moment, value))

    def rows() -> Any:
        for group in sorted(grouped, key=str):
            previous: tuple[Any, float] | None = None
            for moment, current in sorted(grouped[group]):
                if previous is None:
                    elapsed = change = daily = yearly = percent = None
                    status = "NOT_EVALUABLE"
                    previous_time = previous_value = None
                else:
                    previous_time, previous_value = previous
                    elapsed = (moment - previous_time).total_seconds() / 86_400
                    change = current - previous_value
                    daily = change / elapsed
                    yearly = daily * 365.25
                    percent = change / previous_value * 100 if previous_value != 0 else None
                    status = "OK" if previous_value != 0 else "ZERO_BASELINE"
                yield {
                    group_field: group,
                    time_field: moment,
                    value_field: current,
                    "previous_time": previous_time,
                    "previous_value": previous_value,
                    "elapsed_days": elapsed,
                    "absolute_change": change,
                    "rate_per_day": daily,
                    "rate_per_year": yearly,
                    "percent_change": percent,
                    "status": status,
                }
                previous = (moment, current)

    schema = pa.schema(
        [
            pa.field(group_field, table.schema.field(group_field).type),
            pa.field(time_field, pa.timestamp("us")),
            pa.field(value_field, pa.float64()),
            pa.field("previous_time", pa.timestamp("us")),
            pa.field("previous_value", pa.float64()),
            pa.field("elapsed_days", pa.float64()),
            pa.field("absolute_change", pa.float64()),
            pa.field("rate_per_day", pa.float64()),
            pa.field("rate_per_year", pa.float64()),
            pa.field("percent_change", pa.float64()),
            pa.field("status", pa.string(), nullable=False),
        ]
    )
    return {
        "rates": write_rows(rows(), output_dir / "rates-of-change.parquet", schema),
        "summary": value_summary({"groupCount": len(grouped), "rowCount": len(moments)}),
    }
