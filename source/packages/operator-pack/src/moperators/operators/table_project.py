from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa

from moperators.operators.table_document_common_v1 import (
    one,
    read_rules,
    read_table,
    strict_cast,
    summary,
    write_table,
)


def run(
    inputs: dict[str, list[Any]], parameters: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    del parameters
    table = read_table(one(inputs, "data"))
    rules = read_rules(one(inputs, "rules"), "table-project-rules.v1")
    columns = rules.get("columns")
    if not isinstance(columns, list) or not 1 <= len(columns) <= 1024:
        raise ValueError("RULESET_INVALID")
    arrays: list[pa.ChunkedArray] = []
    names: list[str] = []
    for rule in columns:
        if not isinstance(rule, dict):
            raise ValueError("RULESET_INVALID")
        source, target, target_type = rule.get("source"), rule.get("target"), rule.get("type")
        if source not in table.column_names or not isinstance(target, str) or not target:
            raise ValueError("FIELD_REQUIRED")
        arrays.append(strict_cast(table[source], target_type))
        names.append(target)
    if len(names) != len(set(names)):
        raise ValueError("OUTPUT_FIELD_CONFLICT")
    result = pa.Table.from_arrays(arrays, names=names)
    return {
        "data": write_table(result, output_dir / "data.parquet"),
        "summary": summary({"rowCount": result.num_rows, "fieldCount": result.num_columns}),
    }
