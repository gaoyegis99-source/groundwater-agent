from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from moperators.operators.analysis_common import (
    TableInput,
    grouped_series,
    input_timezone,
    timestamp,
    write,
)


def run(source: TableInput, parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    group_field = parameters["groupField"]
    timezone_ = input_timezone(source)
    baseline_start = timestamp(parameters["baselineStart"], timezone_)
    baseline_end = timestamp(parameters["baselineEnd"], timezone_)
    comparison_start = timestamp(parameters["comparisonStart"], timezone_)
    comparison_end = timestamp(parameters["comparisonEnd"], timezone_)
    if (
        baseline_start > baseline_end
        or comparison_start > comparison_end
        or not (baseline_end < comparison_start or comparison_end < baseline_start)
    ):
        raise ValueError("TIME_RANGE_INVALID")
    grouped = grouped_series(source, parameters)
    method = parameters["aggregation"]

    def aggregate(values: list[float]) -> float | None:
        if not values:
            return None
        operations = {"mean": np.mean, "median": np.median, "min": np.min, "max": np.max}
        if method not in operations:
            raise ValueError("AGGREGATION_UNSUPPORTED")
        return float(operations[method](np.asarray(values)))

    result = []
    for group in sorted(grouped, key=str):
        series = grouped[group]
        baseline = aggregate(
            [value for moment, value in series if baseline_start <= moment <= baseline_end]
        )
        comparison = aggregate(
            [value for moment, value in series if comparison_start <= moment <= comparison_end]
        )
        complete = baseline is not None and comparison is not None
        change = comparison - baseline if complete else None
        percent_change = (
            change / baseline * 100
            if change is not None and baseline is not None and baseline != 0
            else None
        )
        result.append(
            {
                group_field: group,
                "baseline_value": baseline,
                "comparison_value": comparison,
                "absolute_change": change,
                "percent_change": percent_change,
                "status": "COMPLETE" if complete else "NOT_EVALUABLE",
            }
        )
    return {
        "changes": write(result, output_dir / "period-change.parquet"),
        "summary": {
            "kind": "value",
            "value": {"groupCount": len(result), "aggregation": method},
        },
    }
