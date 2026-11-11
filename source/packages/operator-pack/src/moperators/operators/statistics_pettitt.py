from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from moperators.operators.extension_common import (
    grouped_values_with_missing,
    one,
    summary,
    write_rows,
)

INTERPRETATION = (
    "Each evaluated row identifies the earliest maximum absolute Pettitt statistic "
    "as one candidate location shift."
)
LIMITATIONS = [
    "Pettitt detects at most one location shift per group and does not establish its cause.",
    "The test uses non-null observations in timestamp order; inspect completeness and "
    "gap-run outputs before interpreting a change.",
]


def run(inputs: dict[str, Any], parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    alpha = float(parameters.get("alpha", 0.05))
    if not 0 < alpha < 1:
        raise ValueError("PARAMETER_INVALID")
    frequency = parameters.get("frequency")
    if frequency not in {None, "day", "month", "quarter", "year"}:
        raise ValueError("FREQUENCY_UNSUPPORTED")
    group_field = parameters["groupField"]
    grouped, missing = grouped_values_with_missing(
        one(inputs, "observations"),
        group_field=group_field,
        time_field=parameters["timeField"],
        value_field=parameters["valueField"],
    )
    result: list[dict[str, Any]] = []
    for group in sorted(grouped, key=str):
        series = grouped[group]
        if len(series) < 4:
            result.append(
                _not_evaluable(group_field, group, len(series), missing.get(group, 0), alpha)
            )
            continue
        values = np.asarray([item[1] for item in series], dtype=np.float64)
        ranks = _average_ranks(values)
        indices = np.arange(1, len(values), dtype=np.float64)
        statistics = 2 * np.cumsum(ranks)[:-1] - indices * (len(values) + 1)
        absolute = np.abs(statistics)
        change_index = int(np.argmax(absolute))
        k = float(absolute[change_index])
        p_value = min(1.0, 2 * math.exp((-6 * k * k) / (len(values) ** 3 + len(values) ** 2)))
        before = values[: change_index + 1]
        after = values[change_index + 1 :]
        median_before, median_after = float(np.median(before)), float(np.median(after))
        result.append(
            {
                group_field: group,
                "observation_count": len(series),
                "missing_value_count": missing.get(group, 0),
                "change_time": series[change_index][0],
                "pettitt_k": k,
                "p_value": p_value,
                "alpha": alpha,
                "significant": p_value < alpha,
                "median_before": median_before,
                "median_after": median_after,
                "median_shift": median_after - median_before,
                "status": "OK",
            }
        )
    return {
        "changePoints": write_rows(result, output_dir / "pettitt-change-points.parquet"),
        "summary": summary(
            {
                "groupCount": len(result),
                "evaluatedGroupCount": sum(item["status"] == "OK" for item in result),
                "notEvaluableGroupCount": sum(
                    item["status"] == "NOT_EVALUABLE" for item in result
                ),
                "analysisFrequency": frequency,
                "alpha": alpha,
                "minimumObservations": 4,
                "interpretation": INTERPRETATION,
                "limitations": LIMITATIONS,
            }
        ),
    }


def _not_evaluable(
    field_name: str, group: Any, count: int, missing_count: int, alpha: float
) -> dict[str, Any]:
    return {
        field_name: group,
        "observation_count": count,
        "missing_value_count": missing_count,
        "change_time": None,
        "pettitt_k": None,
        "p_value": None,
        "alpha": alpha,
        "significant": False,
        "median_before": None,
        "median_after": None,
        "median_shift": None,
        "status": "NOT_EVALUABLE",
    }


def _average_ranks(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=np.float64)
    start = 0
    while start < len(values):
        end = start + 1
        while end < len(values) and values[order[end]] == values[order[start]]:
            end += 1
        ranks[order[start:end]] = (start + 1 + end) / 2
        start = end
    return ranks
