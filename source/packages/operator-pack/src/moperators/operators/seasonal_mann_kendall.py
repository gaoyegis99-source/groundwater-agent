from __future__ import annotations

import math
from collections import Counter, defaultdict
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
    "Each evaluated row combines within-season Mann-Kendall scores and reports a "
    "seasonal Sen slope per year."
)
LIMITATIONS = [
    "At least two cycles with comparable observations in one or more seasons are "
    "required for evaluation.",
    "Missing seasonal periods reduce power; inspect completeness and gap-run outputs "
    "before interpreting significance.",
]


def run(inputs: dict[str, Any], parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    alpha = float(parameters.get("alpha", 0.05))
    if not 0 < alpha < 1:
        raise ValueError("PARAMETER_INVALID")
    enabled = parameters.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("PARAMETER_INVALID")
    if not enabled:
        return {
            "trends": write_rows([], output_dir / "seasonal-mann-kendall.parquet"),
            "summary": summary(
                {
                    "status": "NOT_REQUESTED",
                    "groupCount": 0,
                    "interpretation": "Seasonal trend analysis was not requested.",
                    "limitations": LIMITATIONS,
                }
            ),
        }
    season_frequency = parameters.get("seasonFrequency")
    if season_frequency not in {"month", "quarter"}:
        raise ValueError("FREQUENCY_UNSUPPORTED")
    group_field = parameters["groupField"]
    grouped, missing = grouped_values_with_missing(
        one(inputs, "observations"),
        group_field=group_field,
        time_field=parameters["timeField"],
        value_field=parameters["valueField"],
    )
    output: list[dict[str, Any]] = []
    for group in sorted(grouped, key=str):
        seasons: dict[int, list[tuple[int, float]]] = defaultdict(list)
        seen: set[tuple[int, int]] = set()
        for moment, value in grouped[group]:
            season = moment.month if season_frequency == "month" else (moment.month - 1) // 3 + 1
            key = (moment.year, season)
            if key in seen:
                raise ValueError("DUPLICATE_PERIOD")
            seen.add(key)
            seasons[season].append((moment.year, value))
        s_total = 0
        variance_total = 0.0
        slopes: list[float] = []
        for observations in seasons.values():
            observations.sort()
            values = [item[1] for item in observations]
            s_total += _s(values)
            variance_total += _variance(values)
            for right in range(1, len(observations)):
                for left in range(right):
                    years = observations[right][0] - observations[left][0]
                    if years:
                        slopes.append((observations[right][1] - observations[left][1]) / years)
        if variance_total == 0 or not slopes:
            z = p_value = slope = None
            status = "NOT_EVALUABLE"
        else:
            z = (s_total - 1) / math.sqrt(variance_total) if s_total > 0 else (
                (s_total + 1) / math.sqrt(variance_total) if s_total < 0 else 0.0
            )
            p_value = math.erfc(abs(z) / math.sqrt(2))
            slope = float(np.median(np.asarray(slopes, dtype=np.float64)))
            status = "OK"
        output.append(
            {
                group_field: group,
                "observation_count": len(grouped[group]),
                "missing_value_count": missing.get(group, 0),
                "season_count": len(seasons),
                "cycle_count": len({item[0].year for item in grouped[group]}),
                "s": s_total,
                "variance_s": variance_total,
                "z": z,
                "p_value": p_value,
                "alpha": alpha,
                "significant": p_value is not None and p_value < alpha,
                "seasonal_sen_slope_per_year": slope,
                "status": status,
            }
        )
    return {
        "trends": write_rows(output, output_dir / "seasonal-mann-kendall.parquet"),
        "summary": summary(
            {
                "status": "COMPLETE",
                "groupCount": len(output),
                "evaluatedGroupCount": sum(item["status"] == "OK" for item in output),
                "notEvaluableGroupCount": sum(
                    item["status"] == "NOT_EVALUABLE" for item in output
                ),
                "seasonFrequency": season_frequency,
                "minimumCycles": 2,
                "alpha": alpha,
                "interpretation": INTERPRETATION,
                "limitations": LIMITATIONS,
            }
        ),
    }


def _s(values: list[float]) -> int:
    return sum(
        1 if values[right] > values[left] else -1 if values[right] < values[left] else 0
        for right in range(1, len(values))
        for left in range(right)
    )


def _variance(values: list[float]) -> float:
    count = len(values)
    ties = Counter(values)
    correction = sum(size * (size - 1) * (2 * size + 5) for size in ties.values())
    return (count * (count - 1) * (2 * count + 5) - correction) / 18
