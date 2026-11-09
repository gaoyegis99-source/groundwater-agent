from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pyarrow as pa
import pyarrow.parquet as parquet

from moperators.operators.analysis_common import artifact, bucket, field, number, rows, timestamp


class Input(Protocol):
    @property
    def path(self) -> Path: ...

    @property
    def format_id(self) -> str: ...


def one(inputs: Mapping[str, Sequence[Input]], port: str) -> Input:
    values = inputs.get(port, ())
    if len(values) != 1:
        raise ValueError(f"FIELD_REQUIRED: {port}")
    return values[0]


def write_rows(values: list[dict[str, Any]], path: Path) -> dict[str, Any]:
    parquet.write_table(pa.Table.from_pylist(values), path)
    return artifact(path)


def grouped_values(
    source: Input,
    *,
    group_field: str,
    time_field: str,
    value_field: str,
    reject_duplicates: bool = True,
) -> dict[Any, list[tuple[datetime, float]]]:
    result: dict[Any, list[tuple[datetime, float]]] = defaultdict(list)
    seen: set[tuple[Any, datetime]] = set()
    for row in rows(source):
        group = field(row, group_field)
        moment = timestamp(field(row, time_field))
        if reject_duplicates and (group, moment) in seen:
            raise ValueError("DUPLICATE_TIMESTAMP")
        seen.add((group, moment))
        result[group].append((moment, number(field(row, value_field))))
    for series in result.values():
        series.sort(key=lambda item: item[0])
    return result


def period_values(
    source: Input,
    *,
    group_field: str,
    time_field: str,
    value_field: str,
    frequency: str,
) -> dict[Any, dict[datetime, float]]:
    result: dict[Any, dict[datetime, float]] = defaultdict(dict)
    for row in rows(source):
        group = field(row, group_field)
        period = bucket(timestamp(field(row, time_field)), frequency)
        if period in result[group]:
            raise ValueError("DUPLICATE_PERIOD")
        result[group][period] = number(field(row, value_field))
    return result


def shift_period(value: datetime, frequency: str, periods: int) -> datetime:
    if frequency == "day":
        from datetime import timedelta

        return value + timedelta(days=periods)
    step = {"month": 1, "quarter": 3, "year": 12}.get(frequency)
    if step is None:
        raise ValueError("FREQUENCY_UNSUPPORTED")
    month_index = value.year * 12 + value.month - 1 + step * periods
    return datetime(month_index // 12, month_index % 12 + 1, 1)


def summary(value: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "value", "value": value}


def correlation_row(
    group_field: str,
    group: Any,
    lag: int,
    pairs: list[tuple[float, float]],
    minimum_pairs: int,
) -> dict[str, Any]:
    from scipy.stats import pearsonr

    status = "OK"
    correlation = p_value = None
    if len(pairs) < minimum_pairs:
        status = "NOT_EVALUABLE"
    else:
        left, right = np.asarray(pairs, dtype=np.float64).T
        if np.std(left) == 0 or np.std(right) == 0:
            status = "ZERO_VARIANCE"
        else:
            statistic = pearsonr(left, right)
            correlation, p_value = float(statistic.statistic), float(statistic.pvalue)
    return {
        group_field: group,
        "lag_periods": lag,
        "pair_count": len(pairs),
        "pearson_r": correlation,
        "p_value": p_value,
        "status": status,
    }
