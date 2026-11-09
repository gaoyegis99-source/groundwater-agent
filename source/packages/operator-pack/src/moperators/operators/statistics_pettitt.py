from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from moperators.operators.extension_common import grouped_values, one, summary, write_rows


def run(inputs: dict[str, Any], parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    from scipy.stats import rankdata

    alpha = float(parameters.get("alpha", 0.05))
    if not 0 < alpha < 1:
        raise ValueError("PARAMETER_INVALID")
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
        if len(series) < 4:
            result.append(_not_evaluable(group_field, group, len(series), alpha))
            continue
        values = np.asarray([item[1] for item in series], dtype=np.float64)
        ranks = rankdata(values, method="average")
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
        "summary": summary({"groupCount": len(result)}),
    }


def _not_evaluable(field_name: str, group: Any, count: int, alpha: float) -> dict[str, Any]:
    return {
        field_name: group,
        "observation_count": count,
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
