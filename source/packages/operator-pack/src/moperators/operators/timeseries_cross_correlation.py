from __future__ import annotations

from pathlib import Path
from typing import Any

from moperators.operators.extension_common import (
    correlation_row,
    one,
    period_values,
    shift_period,
    summary,
    write_rows,
)


def run(inputs: dict[str, Any], parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    minimum_lag = int(parameters["minimumLagPeriods"])
    maximum_lag = int(parameters["maximumLagPeriods"])
    minimum_pairs = int(parameters.get("minPairs", 3))
    if minimum_lag > maximum_lag or abs(minimum_lag) > 120 or abs(maximum_lag) > 120:
        raise ValueError("LAG_RANGE_INVALID")
    if minimum_pairs < 2:
        raise ValueError("LAG_RANGE_INVALID")
    frequency = parameters["frequency"]
    left = period_values(
        one(inputs, "left"),
        group_field=parameters["leftGroupField"],
        time_field=parameters["leftTimeField"],
        value_field=parameters["leftValueField"],
        frequency=frequency,
    )
    right = period_values(
        one(inputs, "right"),
        group_field=parameters["rightGroupField"],
        time_field=parameters["rightTimeField"],
        value_field=parameters["rightValueField"],
        frequency=frequency,
    )
    output_group = parameters["leftGroupField"]
    result: list[dict[str, Any]] = []
    groups = sorted(set(left) & set(right), key=str)
    for group in groups:
        for lag in range(minimum_lag, maximum_lag + 1):
            pairs = [
                (value, right[group][shift_period(period, frequency, -lag)])
                for period, value in sorted(left[group].items())
                if shift_period(period, frequency, -lag) in right[group]
            ]
            result.append(correlation_row(output_group, group, lag, pairs, minimum_pairs))
    return {
        "correlations": write_rows(result, output_dir / "cross-correlation.parquet"),
        "summary": summary({"groupCount": len(groups), "rowCount": len(result)}),
    }
