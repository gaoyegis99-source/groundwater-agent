from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from moperators.operators.analysis_common import (
    TableInput,
    bucket,
    field,
    input_null_values,
    input_timezone,
    is_missing,
    iso,
    next_period,
    number,
    rows,
    timestamp,
    write,
)


def run(source: TableInput, parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    source_rows = rows(source)
    group_field = parameters["groupField"]
    time_field = parameters["timeField"]
    value_field = parameters["valueField"]
    frequency = parameters["frequency"]
    timezone_ = input_timezone(source)
    null_values = input_null_values(source)
    start = timestamp(parameters["expectedStart"], timezone_)
    end = timestamp(parameters["expectedEnd"], timezone_)
    if start > end:
        raise ValueError("TIME_RANGE_INVALID")
    first_period, last_period = bucket(start, frequency), bucket(end, frequency)
    expected: list[datetime] = []
    cursor = first_period
    while cursor <= last_period:
        expected.append(cursor)
        cursor = next_period(cursor, frequency)
    by_group: dict[Any, list[tuple[datetime, bool]]] = defaultdict(list)
    for row in source_rows:
        group = field(row, group_field)
        by_group[group]
        moment = timestamp(field(row, time_field), timezone_)
        if start <= moment <= end:
            raw_value = field(row, value_field)
            valid = not is_missing(raw_value, null_values)
            if valid:
                number(raw_value)
            by_group[group].append((moment, valid))
    result = []
    for group in sorted(by_group, key=str):
        observations = by_group[group]
        counts = Counter(bucket(moment, frequency) for moment, valid in observations if valid)
        observed = sum(period in counts for period in expected)
        duplicate = sum(max(counts[period] - 1, 0) for period in expected)
        valid_times = [moment for moment, valid in observations if valid]
        result.append(
            {
                group_field: group,
                "expected_period_count": len(expected),
                "observed_period_count": observed,
                "missing_period_count": len(expected) - observed,
                "duplicate_period_count": duplicate,
                "null_value_count": sum(not valid for _, valid in observations),
                "completeness_ratio": observed / len(expected) if expected else None,
                "actual_start": iso(min(valid_times)) if valid_times else None,
                "actual_end": iso(max(valid_times)) if valid_times else None,
                "status": "EVALUATED" if valid_times else "NOT_EVALUABLE",
            }
        )
    return {
        "profiles": write(result, output_dir / "completeness.parquet"),
        "summary": {"kind": "value", "value": {"groupCount": len(result)}},
    }
