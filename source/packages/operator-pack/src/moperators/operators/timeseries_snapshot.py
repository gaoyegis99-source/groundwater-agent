from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from moperators.operators.analysis_common import TableInput, field, rows, timestamp, write


def run(source: TableInput, parameters: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    source_rows = rows(source)
    group_field, time_field = parameters["groupField"], parameters["timeField"]
    target = timestamp(parameters["targetTime"])
    max_age = float(parameters["maxAgeDays"])
    selection = parameters["selection"]
    grouped: dict[Any, list[tuple[datetime, dict[str, Any]]]] = defaultdict(list)
    seen: set[tuple[Any, datetime]] = set()
    for row in source_rows:
        group = field(row, group_field)
        moment = timestamp(field(row, time_field))
        if (group, moment) in seen:
            raise ValueError("DUPLICATE_TIMESTAMP")
        seen.add((group, moment))
        grouped[group].append((moment, row))
    selected: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for group in sorted(grouped, key=str):
        candidates = grouped[group]
        if selection == "exact":
            eligible = [item for item in candidates if item[0] == target]
            chosen = eligible[0] if eligible else None
        elif selection == "latestAtOrBefore":
            eligible = [item for item in candidates if item[0] <= target]
            chosen = max(eligible, key=lambda item: item[0]) if eligible else None
        elif selection == "nearest":
            chosen = min(candidates, key=lambda item: (abs(item[0] - target), item[0]))
        else:
            raise ValueError("SELECTION_UNSUPPORTED")
        if chosen is None:
            diagnostics.append({group_field: group, "status": "NO_MATCH", "age_days": None})
            continue
        age = abs((chosen[0] - target).total_seconds()) / 86400
        if age > max_age:
            diagnostics.append({group_field: group, "status": "MAX_AGE_EXCEEDED", "age_days": age})
            continue
        selected.append(chosen[1])
        diagnostics.append({group_field: group, "status": "SELECTED", "age_days": age})
    outputs = {
        "diagnostics": write(diagnostics, output_dir / "snapshot-diagnostics.parquet"),
        "summary": {
            "kind": "value",
            "value": {"groupCount": len(grouped), "selectedCount": len(selected)},
        },
    }
    if not selected:
        raise ValueError("EMPTY_RESULT")
    outputs["snapshot"] = write(selected, output_dir / "snapshot.parquet")
    return outputs
