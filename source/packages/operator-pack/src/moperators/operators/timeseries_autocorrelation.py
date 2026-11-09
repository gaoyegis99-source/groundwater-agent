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
    maximum = int(parameters["maxLagPeriods"])
    minimum_pairs = int(parameters.get("minPairs", 3))
    if maximum < 1 or maximum > 120 or minimum_pairs < 2:
        raise ValueError("LAG_RANGE_INVALID")
    group_field = parameters["groupField"]
    frequency = parameters["frequency"]
    grouped = period_values(
        one(inputs, "observations"),
        group_field=group_field,
        time_field=parameters["timeField"],
        value_field=parameters["valueField"],
        frequency=frequency,
    )
    result: list[dict[str, Any]] = []
    for group in sorted(grouped, key=str):
        values = grouped[group]
        for lag in range(1, maximum + 1):
            pairs = [
                (value, values[shift_period(period, frequency, -lag)])
                for period, value in sorted(values.items())
                if shift_period(period, frequency, -lag) in values
            ]
            result.append(correlation_row(group_field, group, lag, pairs, minimum_pairs))
    return {
        "correlations": write_rows(result, output_dir / "autocorrelation.parquet"),
        "summary": summary({"groupCount": len(grouped), "rowCount": len(result)}),
    }
