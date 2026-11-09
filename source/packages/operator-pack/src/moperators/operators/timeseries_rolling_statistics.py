from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np

from moperators.operators.extension_common import grouped_values, one, summary, write_rows


def run(inputs: dict[str, Any], parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    window_days = int(parameters["windowDays"])
    minimum = int(parameters.get("minObservations", 1))
    if window_days <= 0 or minimum <= 0:
        raise ValueError("WINDOW_INVALID")
    group_field = parameters["groupField"]
    grouped = grouped_values(
        one(inputs, "observations"),
        group_field=group_field,
        time_field=parameters["timeField"],
        value_field=parameters["valueField"],
    )
    result: list[dict[str, Any]] = []
    for group in sorted(grouped, key=str):
        series = grouped[group]
        left = 0
        for index, (moment, value) in enumerate(series):
            threshold = moment - timedelta(days=window_days)
            while series[left][0] < threshold:
                left += 1
            values = np.asarray([item[1] for item in series[left : index + 1]], dtype=np.float64)
            evaluable = len(values) >= minimum
            result.append(
                {
                    group_field: group,
                    parameters["timeField"]: moment,
                    parameters["valueField"]: value,
                    "rolling_count": len(values),
                    "rolling_min": float(np.min(values)) if evaluable else None,
                    "rolling_max": float(np.max(values)) if evaluable else None,
                    "rolling_mean": float(np.mean(values)) if evaluable else None,
                    "rolling_median": float(np.median(values)) if evaluable else None,
                    "rolling_population_stddev": float(np.std(values)) if evaluable else None,
                    "status": "OK" if evaluable else "NOT_EVALUABLE",
                }
            )
    return {
        "statistics": write_rows(result, output_dir / "rolling-statistics.parquet"),
        "summary": summary({"groupCount": len(grouped), "rowCount": len(result)}),
    }
