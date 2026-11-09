from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from moperators.operators.analysis_common import TableInput, grouped_series, write


def run(source: TableInput, parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    from scipy import stats

    grouped = grouped_series(source, parameters, reject_duplicates=True)
    group_field = parameters["groupField"]
    result = []
    for group in sorted(grouped, key=str):
        series = grouped[group]
        if len(series) < 2 or len({moment for moment, _ in series}) < 2:
            result.append({group_field: group, "status": "NOT_EVALUABLE"})
            continue
        origin = series[0][0]
        x = np.asarray([(moment - origin).total_seconds() / 86400 for moment, _ in series])
        y = np.asarray([value for _, value in series])
        regression = stats.linregress(x, y)
        result.append(
            {
                group_field: group,
                "status": "EVALUATED",
                "observation_count": len(series),
                "slope_per_day": float(regression.slope),
                "slope_per_year": float(regression.slope * 365.2425),
                "intercept_at_first_observation": float(regression.intercept),
                "r_squared": float(regression.rvalue**2),
                "p_value": float(regression.pvalue),
                "standard_error": float(regression.stderr),
                "evidence_band": "TWO_POINT" if len(series) == 2 else "MULTI_POINT",
            }
        )
    return {
        "trends": write(result, output_dir / "linear-trends.parquet"),
        "summary": {"kind": "value", "value": {"groupCount": len(result)}},
    }
