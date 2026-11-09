from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from moperators.operators.analysis_common import (
    TableInput,
    field,
    input_null_values,
    is_missing,
    number,
    rows,
    write,
)


def run(source: TableInput, parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    source_rows = rows(source)
    group_field, value_field = parameters["groupField"], parameters["valueField"]
    threshold = float(parameters.get("threshold", 3.5))
    groups: dict[Any, list[tuple[int, float]]] = defaultdict(list)
    missing: dict[Any, list[int]] = defaultdict(list)
    null_values = input_null_values(source)
    for index, row in enumerate(source_rows):
        group = field(row, group_field)
        groups[group]
        raw_value = field(row, value_field)
        if is_missing(raw_value, null_values):
            missing[group].append(index)
            continue
        groups[group].append((index, number(raw_value)))
    diagnostics: dict[int, dict[str, Any]] = {}
    outlier_count = 0
    for values in groups.values():
        if not values:
            continue
        array = np.asarray([value for _, value in values])
        center = float(np.median(array))
        mad = float(np.median(np.abs(array - center)))
        for index, value in values:
            if mad == 0:
                is_outlier = value != center
                modified_z = None
                rule = "MAD_ZERO_DIFFERENT_FROM_MEDIAN"
            else:
                modified_z = 0.6744897501960817 * (value - center) / mad
                is_outlier = abs(modified_z) > threshold
                rule = "MODIFIED_Z_THRESHOLD"
            outlier_count += int(is_outlier)
            diagnostics[index] = {
                "median": center,
                "mad": mad,
                "modified_z": modified_z,
                "is_outlier": is_outlier,
                "rule": rule,
                "status": "EVALUATED",
            }
    for group, indexes in missing.items():
        status = "MISSING_VALUE" if groups[group] else "NOT_EVALUABLE"
        for index in indexes:
            diagnostics[index] = {
                "median": None,
                "mad": None,
                "modified_z": None,
                "is_outlier": None,
                "rule": "MISSING_VALUE",
                "status": status,
            }
    result = [{**row, **diagnostics[index]} for index, row in enumerate(source_rows)]
    return {
        "observations": write(result, output_dir / "mad-outliers.parquet"),
        "summary": {
            "kind": "value",
            "value": {
                "rowCount": len(source_rows),
                "outlierCount": outlier_count,
                "missingValueCount": sum(map(len, missing.values())),
                "notEvaluableGroupCount": sum(not values for values in groups.values()),
                "threshold": threshold,
            },
        },
    }
