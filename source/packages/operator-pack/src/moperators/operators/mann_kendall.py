from __future__ import annotations

import math
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from moperators.operators.analysis_common import TableInput, grouped_series, write


def _s_statistic(values: np.ndarray) -> int:
    ranks = {value: index + 1 for index, value in enumerate(sorted(set(values.tolist())))}
    tree = [0] * (len(ranks) + 1)

    def count(rank: int) -> int:
        total = 0
        while rank:
            total += tree[rank]
            rank -= rank & -rank
        return total

    def add(rank: int) -> None:
        while rank < len(tree):
            tree[rank] += 1
            rank += rank & -rank

    result = 0
    for index, value in enumerate(values):
        rank = ranks[float(value)]
        result += count(rank - 1) - (index - count(rank))
        add(rank)
    return result


def run(source: TableInput, parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    from scipy import stats

    grouped = grouped_series(source, parameters, reject_duplicates=True)
    group_field = parameters["groupField"]
    alpha = float(parameters.get("alpha", 0.05))
    result = []
    for group in sorted(grouped, key=str):
        series = grouped[group]
        n = len(series)
        if n < 2:
            result.append({group_field: group, "status": "NOT_EVALUABLE"})
            continue
        times = np.asarray(
            [(moment - series[0][0]).total_seconds() / 86400 for moment, _ in series]
        )
        values = np.asarray([value for _, value in series])
        s_value = _s_statistic(values)
        tie_term = sum(count * (count - 1) * (2 * count + 5) for count in Counter(values).values())
        variance = (n * (n - 1) * (2 * n + 5) - tie_term) / 18
        if variance == 0:
            z_value = 0.0
        elif s_value > 0:
            z_value = (s_value - 1) / math.sqrt(variance)
        elif s_value < 0:
            z_value = (s_value + 1) / math.sqrt(variance)
        else:
            z_value = 0.0
        p_value = float(2 * stats.norm.sf(abs(z_value)))
        sen_slope = float(stats.theilslopes(values, times).slope)
        result.append(
            {
                group_field: group,
                "status": "EVALUATED",
                "observation_count": n,
                "s": s_value,
                "variance_s": float(variance),
                "z": float(z_value),
                "p_value": p_value,
                "kendall_tau": s_value / (n * (n - 1) / 2),
                "sen_slope_per_day": sen_slope,
                "sen_slope_per_year": sen_slope * 365.2425,
                "significant": p_value <= alpha,
            }
        )
    return {
        "trends": write(result, output_dir / "mann-kendall.parquet"),
        "summary": {
            "kind": "value",
            "value": {"groupCount": len(result), "alpha": alpha},
        },
    }
