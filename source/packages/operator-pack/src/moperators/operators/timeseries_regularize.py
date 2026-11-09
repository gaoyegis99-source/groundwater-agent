from __future__ import annotations

from bisect import bisect_left
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
    period_range,
    read_table,
    timestamp,
    timestamps,
    value_summary,
    write_rows,
)


def run(inputs: dict[str, Any], parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    method = parameters["interpolationMethod"]
    maximum_gap = int(parameters["maxGapPeriods"])
    maximum_rows = int(parameters["maxOutputRows"])
    if method not in {"linear", "previous"} or maximum_gap < 1:
        raise ValueError("INTERPOLATION_INVALID")
    if maximum_rows < 1 or maximum_rows > 5_000_000:
        raise ValueError("OUTPUT_ROW_LIMIT_EXCEEDED")

    group_field = parameters["groupField"]
    time_field = parameters["timeField"]
    value_field = parameters["valueField"]
    frequency = parameters["frequency"]
    periods = period_range(
        timestamp(parameters["expectedStart"]),
        timestamp(parameters["expectedEnd"]),
        frequency,
    )
    table = read_table(one(inputs))
    groups = column(table, group_field).to_pylist()
    moments = timestamps(table, time_field)
    values = numbers(table, value_field, allow_null=True)
    period_keys = [bucket(moment, frequency) for moment in moments]
    ensure_unique(zip(groups, period_keys, strict=True), "DUPLICATE_PERIOD")

    grouped: dict[Any, dict[Any, float | None]] = defaultdict(dict)
    for group, period, value in zip(groups, period_keys, values, strict=True):
        grouped[group][period] = value
    output_count = len(grouped) * len(periods)
    if output_count > maximum_rows:
        raise ValueError("OUTPUT_ROW_LIMIT_EXCEEDED")

    period_positions = {period: index for index, period in enumerate(periods)}
    counters = {"observed": 0, "imputed": 0, "unfilled": 0}

    def output_rows() -> Any:
        for group in sorted(grouped, key=str):
            series = grouped[group]
            observed = sorted(
                period_positions[period]
                for period, value in series.items()
                if period in period_positions and value is not None
            )
            for index, period in enumerate(periods):
                value = series.get(period)
                if value is not None:
                    counters["observed"] += 1
                    yield _row(
                        group_field,
                        group,
                        time_field,
                        period,
                        value_field,
                        value,
                        False,
                        None,
                        period,
                        period,
                        "OBSERVED",
                    )
                    continue
                insertion = bisect_left(observed, index)
                previous_index = observed[insertion - 1] if insertion else None
                next_index = observed[insertion] if insertion < len(observed) else None
                if previous_index is None or next_index is None:
                    counters["unfilled"] += 1
                    yield _row(
                        group_field,
                        group,
                        time_field,
                        period,
                        value_field,
                        None,
                        False,
                        None,
                        periods[previous_index] if previous_index is not None else None,
                        periods[next_index] if next_index is not None else None,
                        "UNFILLED_BOUNDARY",
                    )
                    continue
                gap_periods = next_index - previous_index - 1
                if gap_periods > maximum_gap:
                    counters["unfilled"] += 1
                    yield _row(
                        group_field,
                        group,
                        time_field,
                        period,
                        value_field,
                        None,
                        False,
                        None,
                        periods[previous_index],
                        periods[next_index],
                        "UNFILLED_GAP_LIMIT",
                    )
                    continue
                previous_value = series[periods[previous_index]]
                next_value = series[periods[next_index]]
                if previous_value is None or next_value is None:
                    raise AssertionError("observed positions must have numeric values")
                interpolated = previous_value
                if method == "linear":
                    fraction = (index - previous_index) / (next_index - previous_index)
                    interpolated = previous_value + (next_value - previous_value) * fraction
                counters["imputed"] += 1
                yield _row(
                    group_field,
                    group,
                    time_field,
                    period,
                    value_field,
                    interpolated,
                    True,
                    method,
                    periods[previous_index],
                    periods[next_index],
                    "IMPUTED",
                )

    group_type = table.schema.field(group_field).type
    schema = pa.schema(
        [
            pa.field(group_field, group_type),
            pa.field(time_field, pa.timestamp("us")),
            pa.field(value_field, pa.float64()),
            pa.field("is_imputed", pa.bool_(), nullable=False),
            pa.field("interpolation_method", pa.string()),
            pa.field("previous_observed_time", pa.timestamp("us")),
            pa.field("next_observed_time", pa.timestamp("us")),
            pa.field("status", pa.string(), nullable=False),
        ]
    )
    artifact = write_rows(output_rows(), output_dir / "regularized-series.parquet", schema)
    return {
        "series": artifact,
        "summary": value_summary(
            {
                "groupCount": len(grouped),
                "rowCount": output_count,
                "observedCount": counters["observed"],
                "imputedCount": counters["imputed"],
                "unfilledCount": counters["unfilled"],
            }
        ),
    }


def _row(
    group_field: str,
    group: Any,
    time_field: str,
    period: Any,
    value_field: str,
    value: float | None,
    is_imputed: bool,
    method: str | None,
    previous: Any,
    following: Any,
    status: str,
) -> dict[str, Any]:
    return {
        group_field: group,
        time_field: period,
        value_field: value,
        "is_imputed": is_imputed,
        "interpolation_method": method,
        "previous_observed_time": previous,
        "next_observed_time": following,
        "status": status,
    }
