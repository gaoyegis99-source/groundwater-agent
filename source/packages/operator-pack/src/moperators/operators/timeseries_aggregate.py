from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from moperators.operators.analysis_common import (
    TableInput,
    bucket,
    grouped_series,
    iso,
    write,
)


def run(source: TableInput, parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    group_field = parameters["groupField"]
    frequency = parameters["frequency"]
    buckets: dict[tuple[Any, datetime], list[float]] = defaultdict(list)
    grouped = grouped_series(source, parameters)
    for group, series in grouped.items():
        for moment, value in series:
            buckets[(group, bucket(moment, frequency))].append(value)
    result = []
    ordered = sorted(buckets.items(), key=lambda item: (str(item[0][0]), item[0][1]))
    for (group, period), values in ordered:
        array = np.asarray(values, dtype=np.float64)
        result.append(
            {
                group_field: group,
                "period_start": iso(period),
                "observation_count": len(values),
                "minimum": float(np.min(array)),
                "maximum": float(np.max(array)),
                "mean": float(np.mean(array)),
                "median": float(np.median(array)),
                "stddev": float(np.std(array)),
                "status": "EVALUATED",
            }
        )
    for group, series in grouped.items():
        if not series:
            result.append(
                {
                    group_field: group,
                    "period_start": None,
                    "observation_count": 0,
                    "minimum": None,
                    "maximum": None,
                    "mean": None,
                    "median": None,
                    "stddev": None,
                    "status": "NOT_EVALUABLE",
                }
            )
    result.sort(key=lambda item: (str(item[group_field]), item["period_start"] or ""))
    return {
        "series": write(result, output_dir / "aggregate.parquet"),
        "summary": {
            "kind": "value",
            "value": {
                "groupPeriodCount": len(buckets),
                "notEvaluableGroupCount": sum(not values for values in grouped.values()),
            },
        },
    }
