from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from moperators.operators.analysis_common import timestamp
from moperators.operators.extension_common import grouped_values, one, summary, write_rows


def run(inputs: dict[str, Any], parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    start = timestamp(parameters["baselineStart"])
    end = timestamp(parameters["baselineEnd"])
    if start > end:
        raise ValueError("TIME_RANGE_INVALID")
    group_field = parameters["groupField"]
    grouped = grouped_values(
        one(inputs, "observations"),
        group_field=group_field,
        time_field=parameters["timeField"],
        value_field=parameters["valueField"],
    )
    result: list[dict[str, Any]] = []
    not_evaluable = 0
    for group in sorted(grouped, key=str):
        baseline = np.asarray(
            [value for moment, value in grouped[group] if start <= moment <= end],
            dtype=np.float64,
        )
        mean = float(np.mean(baseline)) if baseline.size else None
        stddev = float(np.std(baseline)) if baseline.size else None
        if mean is None:
            status = "NOT_EVALUABLE"
            not_evaluable += 1
        elif stddev == 0:
            status = "ZERO_VARIANCE"
        else:
            status = "OK"
        for moment, value in grouped[group]:
            anomaly = value - mean if mean is not None else None
            result.append(
                {
                    group_field: group,
                    parameters["timeField"]: moment,
                    parameters["valueField"]: value,
                    "baseline_mean": mean,
                    "baseline_population_stddev": stddev,
                    "anomaly": anomaly,
                    "standardized_anomaly": (
                        anomaly / stddev
                        if anomaly is not None and stddev not in {None, 0}
                        else None
                    ),
                    "status": status,
                }
            )
    return {
        "anomalies": write_rows(result, output_dir / "baseline-anomalies.parquet"),
        "summary": summary(
            {"groupCount": len(grouped), "rowCount": len(result), "notEvaluable": not_evaluable}
        ),
    }
