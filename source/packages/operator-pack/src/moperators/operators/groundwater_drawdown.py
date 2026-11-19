from __future__ import annotations

from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from moperators.operators.analysis_common import (
    field,
    input_null_values,
    input_timezone,
    is_missing,
    number,
    rows,
    timestamp,
)
from moperators.operators.extension_common import one, summary, write_rows

UNITS = {"m": 1.0, "cm": 0.01, "mm": 0.001}


def run(inputs: dict[str, Any], parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    unit = parameters["headUnit"]
    if unit not in UNITS:
        raise ValueError("UNIT_UNSUPPORTED")
    factor = UNITS[unit]
    group_field = parameters["groupField"]
    time_field = parameters["timeField"]
    head_field = parameters["headField"]
    parsed: list[tuple[Any, Any, float, dict[str, Any]]] = []
    seen: set[tuple[Any, Any]] = set()
    source = one(inputs, "observations")
    timezone_ = input_timezone(source)
    null_values = input_null_values(source)
    for row in rows(source):
        group = field(row, group_field)
        moment = timestamp(field(row, time_field), timezone_)
        if (group, moment) in seen:
            raise ValueError("DUPLICATE_TIMESTAMP")
        seen.add((group, moment))
        raw = field(row, head_field)
        if not is_missing(raw, null_values):
            parsed.append((group, moment, number(raw) * factor, row))
    references = _references(parsed, parameters, timezone_, null_values)
    output = [
        {
            group_field: group,
            time_field: moment,
            "observed_head_m": head,
            "reference_head_m": references[group],
            "drawdown_m": references[group] - head,
        }
        for group, moment, head, _ in sorted(parsed, key=lambda item: (str(item[0]), item[1]))
    ]
    return {
        "drawdowns": write_rows(output, output_dir / "drawdowns.parquet"),
        "summary": summary({"groupCount": len(references), "rowCount": len(output)}),
    }


def _references(
    values: list[tuple[Any, Any, float, dict[str, Any]]],
    parameters: dict[str, Any],
    timezone_: ZoneInfo | None,
    null_values: frozenset[str],
) -> dict[Any, float]:
    mode = parameters["referenceMode"]
    result: dict[Any, float] = {}
    if mode == "referenceField":
        reference_field = parameters.get("referenceHeadField")
        reference_unit = parameters.get("referenceHeadUnit")
        if not reference_field or reference_unit not in UNITS:
            raise ValueError("REFERENCE_INVALID")
        for group, _, _, row in values:
            raw = field(row, reference_field)
            if is_missing(raw, null_values):
                raise ValueError("REFERENCE_INVALID")
            reference = number(raw) * UNITS[reference_unit]
            if group in result and not np.isclose(result[group], reference):
                raise ValueError("REFERENCE_INVALID")
            result[group] = reference
        return result
    if mode != "baselinePeriod":
        raise ValueError("REFERENCE_INVALID")
    start = timestamp(parameters.get("baselineStart"), timezone_)
    end = timestamp(parameters.get("baselineEnd"), timezone_)
    if start > end:
        raise ValueError("TIME_RANGE_INVALID")
    statistic = parameters.get("baselineStatistic")
    if statistic not in {"mean", "median", "max"}:
        raise ValueError("REFERENCE_INVALID")
    groups = sorted({item[0] for item in values}, key=str)
    for group in groups:
        baseline = np.asarray(
            [
                head
                for item_group, moment, head, _ in values
                if item_group == group and start <= moment <= end
            ],
            dtype=np.float64,
        )
        if not baseline.size:
            raise ValueError("REFERENCE_INVALID")
        result[group] = float(
            np.mean(baseline)
            if statistic == "mean"
            else np.median(baseline)
            if statistic == "median"
            else np.max(baseline)
        )
    return result
