from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import pyarrow as pa

from moperators.operators.advanced_timeseries_common import (
    bucket,
    column,
    ensure_unique,
    next_period,
    numbers,
    one,
    read_table,
    timestamps,
    value_summary,
    write_rows,
)


def run(inputs: dict[str, Any], parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    enabled = parameters.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("THRESHOLD_INVALID")
    group_field = parameters["groupField"]
    time_field = parameters["timeField"]
    value_field = parameters["valueField"]
    table = read_table(one(inputs))
    event_schema, classified_schema = _output_schemas(
        table, group_field, time_field, value_field
    )
    if not enabled:
        return {
            "classified": write_rows(
                [], output_dir / "threshold-classification.parquet", classified_schema
            ),
            "events": write_rows([], output_dir / "threshold-events.parquet", event_schema),
            "summary": value_summary(
                {
                    "status": "NOT_REQUESTED",
                    "interpretation": "Threshold-event detection was not requested.",
                    "limitations": [
                        "No threshold was assumed; enable this branch with an explicit "
                        "threshold and comparison direction."
                    ],
                }
            ),
        }
    comparison = parameters.get("comparison")
    source = parameters.get("thresholdSource", "scalar")
    if comparison not in {"above", "atOrAbove", "below", "atOrBelow"}:
        raise ValueError("THRESHOLD_INVALID")
    if source not in {"scalar", "field"}:
        raise ValueError("THRESHOLD_INVALID")
    frequency = parameters["frequency"]
    groups = column(table, group_field).to_pylist()
    periods = [bucket(value, frequency) for value in timestamps(table, time_field)]
    values = numbers(table, value_field)
    ensure_unique(zip(groups, periods, strict=True), "DUPLICATE_PERIOD")
    if source == "scalar":
        threshold = _threshold(parameters.get("thresholdValue"))
        thresholds = [threshold] * len(values)
    else:
        threshold_field = parameters.get("thresholdField")
        if not isinstance(threshold_field, str) or not threshold_field:
            raise ValueError("THRESHOLD_INVALID")
        thresholds = [_threshold(value) for value in column(table, threshold_field).to_pylist()]

    grouped: dict[Any, list[tuple[Any, float, float]]] = defaultdict(list)
    for group, period, value, threshold in zip(
        groups, periods, values, thresholds, strict=True
    ):
        if value is None:
            raise AssertionError("strict numeric parsing cannot return null")
        grouped[group].append((period, value, threshold))

    event_rows: list[dict[str, Any]] = []
    event_ids: dict[tuple[Any, Any], str] = {}
    for group in sorted(grouped, key=str):
        current: list[tuple[Any, float, float]] = []
        sequence = 0
        for item in sorted(grouped[group]):
            period, value, threshold = item
            qualifies = _qualifies(value, threshold, comparison)
            consecutive = not current or period == next_period(current[-1][0], frequency)
            if current and (not qualifies or not consecutive):
                sequence += 1
                _append_event(
                    event_rows,
                    event_ids,
                    group_field,
                    group,
                    sequence,
                    current,
                    comparison,
                )
                current = []
            if qualifies:
                current.append(item)
        if current:
            sequence += 1
            _append_event(event_rows, event_ids, group_field, group, sequence, current, comparison)

    def classified_rows() -> Any:
        for group in sorted(grouped, key=str):
            for period, value, threshold in sorted(grouped[group]):
                event_id = event_ids.get((group, period))
                yield {
                    group_field: group,
                    time_field: period,
                    value_field: value,
                    "threshold": threshold,
                    "qualifies": event_id is not None,
                    "event_id": event_id,
                    "status": "QUALIFYING" if event_id is not None else "NOT_QUALIFYING",
                }

    return {
        "classified": write_rows(
            classified_rows(), output_dir / "threshold-classification.parquet", classified_schema
        ),
        "events": write_rows(event_rows, output_dir / "threshold-events.parquet", event_schema),
        "summary": value_summary(
            {
                "groupCount": len(grouped),
                "rowCount": len(periods),
                "eventCount": len(event_rows),
                "status": "COMPLETE",
                "frequency": frequency,
                "comparison": comparison,
                "thresholdSource": source,
                "thresholdValue": threshold if source == "scalar" else None,
                "interpretation": (
                    "Events are consecutive calendar periods that satisfy the explicit "
                    "comparison against the supplied threshold."
                ),
                "limitations": [
                    "The supplied threshold is a user decision, not a platform standard "
                    "or inferred regulatory limit."
                ],
            }
        ),
    }


def _output_schemas(
    table: pa.Table, group_field: str, time_field: str, value_field: str
) -> tuple[pa.Schema, pa.Schema]:
    group_type = table.schema.field(group_field).type
    event_schema = pa.schema(
        [
            pa.field(group_field, group_type),
            pa.field("event_id", pa.string(), nullable=False),
            pa.field("event_start", pa.timestamp("us"), nullable=False),
            pa.field("event_end", pa.timestamp("us"), nullable=False),
            pa.field("period_count", pa.int64(), nullable=False),
            pa.field("extreme_time", pa.timestamp("us"), nullable=False),
            pa.field("extreme_value", pa.float64(), nullable=False),
            pa.field("threshold_min", pa.float64(), nullable=False),
            pa.field("threshold_max", pa.float64(), nullable=False),
            pa.field("maximum_threshold_distance", pa.float64(), nullable=False),
            pa.field("mean_threshold_distance", pa.float64(), nullable=False),
        ]
    )
    classified_schema = pa.schema(
        [
            pa.field(group_field, group_type),
            pa.field(time_field, pa.timestamp("us")),
            pa.field(value_field, pa.float64()),
            pa.field("threshold", pa.float64()),
            pa.field("qualifies", pa.bool_(), nullable=False),
            pa.field("event_id", pa.string()),
            pa.field("status", pa.string(), nullable=False),
        ]
    )
    return event_schema, classified_schema


def _threshold(value: Any) -> float:
    if value is None or isinstance(value, bool):
        raise ValueError("THRESHOLD_INVALID")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("THRESHOLD_INVALID") from exc
    if not math.isfinite(result):
        raise ValueError("THRESHOLD_INVALID")
    return result


def _qualifies(value: float, threshold: float, comparison: str) -> bool:
    return {
        "above": value > threshold,
        "atOrAbove": value >= threshold,
        "below": value < threshold,
        "atOrBelow": value <= threshold,
    }[comparison]


def _append_event(
    rows: list[dict[str, Any]],
    event_ids: dict[tuple[Any, Any], str],
    group_field: str,
    group: Any,
    sequence: int,
    members: list[tuple[Any, float, float]],
    comparison: str,
) -> None:
    event_id = f"{group}:{sequence:06d}"
    for period, _, _ in members:
        event_ids[(group, period)] = event_id
    extreme = (
        max(members, key=lambda item: item[1])
        if comparison in {"above", "atOrAbove"}
        else min(members, key=lambda item: item[1])
    )
    distances = [abs(value - threshold) for _, value, threshold in members]
    thresholds = [threshold for _, _, threshold in members]
    rows.append(
        {
            group_field: group,
            "event_id": event_id,
            "event_start": members[0][0],
            "event_end": members[-1][0],
            "period_count": len(members),
            "extreme_time": extreme[0],
            "extreme_value": extreme[1],
            "threshold_min": min(thresholds),
            "threshold_max": max(thresholds),
            "maximum_threshold_distance": max(distances),
            "mean_threshold_distance": sum(distances) / len(distances),
        }
    )
