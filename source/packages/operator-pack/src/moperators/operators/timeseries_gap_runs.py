from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from moperators.operators.analysis_common import bucket, field, next_period, rows, timestamp
from moperators.operators.extension_common import one, summary, write_rows


def run(inputs: dict[str, Any], parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    source = one(inputs, "observations")
    group_field = parameters["groupField"]
    time_field = parameters["timeField"]
    value_field = parameters["valueField"]
    frequency = parameters["frequency"]
    start = timestamp(parameters["expectedStart"])
    end = timestamp(parameters["expectedEnd"])
    if start > end:
        raise ValueError("TIME_RANGE_INVALID")
    first, last = bucket(start, frequency), bucket(end, frequency)
    observed: dict[Any, set[Any]] = defaultdict(set)
    groups: set[Any] = set()
    for row in rows(source):
        group = field(row, group_field)
        groups.add(group)
        moment = timestamp(field(row, time_field))
        raw = field(row, value_field)
        if start <= moment <= end and raw is not None and not (
            isinstance(raw, str) and not raw.strip()
        ):
            observed[group].add(bucket(moment, frequency))
    result: list[dict[str, Any]] = []
    longest = 0
    for group in sorted(groups, key=str):
        cursor = first
        run_start = None
        run_count = 0
        while cursor <= last:
            if cursor not in observed[group]:
                run_start = run_start or cursor
                run_count += 1
            elif run_start is not None:
                previous = shift_back(cursor, frequency)
                result.append(_row(group_field, group, run_start, previous, run_count))
                longest = max(longest, run_count)
                run_start, run_count = None, 0
            cursor = next_period(cursor, frequency)
        if run_start is not None:
            result.append(_row(group_field, group, run_start, last, run_count))
            longest = max(longest, run_count)
    return {
        "gaps": write_rows(result, output_dir / "gap-runs.parquet"),
        "summary": summary(
            {"groupCount": len(groups), "gapRunCount": len(result), "longestGapPeriods": longest}
        ),
    }


def shift_back(value: Any, frequency: str) -> Any:
    from moperators.operators.extension_common import shift_period

    return shift_period(value, frequency, -1)


def _row(field_name: str, group: Any, start: Any, end: Any, count: int) -> dict[str, Any]:
    return {
        field_name: group,
        "gap_start": start,
        "gap_end": end,
        "missing_period_count": count,
    }
