from __future__ import annotations

from pathlib import Path
from typing import Any

from moperators.operators.table_document_common_v1 import (
    connection,
    identifier,
    one,
    read_rules,
    read_table,
    summary,
    table_with_ordinal,
    write_table,
)


def run(
    inputs: dict[str, list[Any]], parameters: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    del parameters
    table = read_table(one(inputs, "data"))
    rules = read_rules(one(inputs, "rules"), "table-sort-rules.v1")
    keys = rules.get("keys")
    if not isinstance(keys, list) or not 1 <= len(keys) <= 64:
        raise ValueError("RULESET_INVALID")
    clauses: list[str] = []
    for key in keys:
        if not isinstance(key, dict) or key.get("field") not in table.column_names:
            raise ValueError("FIELD_REQUIRED")
        direction, nulls = key.get("direction"), key.get("nulls")
        if direction not in {"asc", "desc"} or nulls not in {"first", "last"}:
            raise ValueError("RULESET_INVALID")
        clauses.append(f"{identifier(key['field'])} {direction.upper()} NULLS {nulls.upper()}")
    database = connection(output_dir)
    database.register("source", table_with_ordinal(table))
    fields = ",".join(identifier(name) for name in table.column_names)
    result = database.execute(
        f"SELECT {fields} FROM source ORDER BY {','.join(clauses)}, __moperators_ordinal"
    ).to_arrow_table()
    database.close()
    return {
        "data": write_table(result, output_dir / "data.parquet"),
        "summary": summary({"rowCount": result.num_rows}),
    }
