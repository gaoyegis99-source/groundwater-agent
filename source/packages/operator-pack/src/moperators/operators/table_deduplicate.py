from __future__ import annotations

from pathlib import Path
from typing import Any

from moperators.operators.table_document_common_v1 import (
    connection,
    identifier,
    one,
    read_rules,
    read_table,
    require_fields,
    summary,
    table_with_ordinal,
    write_table,
)


def run(
    inputs: dict[str, list[Any]], parameters: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    table = read_table(one(inputs, "data"))
    rules = read_rules(one(inputs, "rules"), "table-deduplicate-rules.v1")
    keys = rules.get("keys")
    if not isinstance(keys, list) or not 1 <= len(keys) <= 64:
        raise ValueError("RULESET_INVALID")
    require_fields(table, keys)
    keep = parameters.get("keep")
    if keep not in {"first", "last", "error"}:
        raise ValueError("PARAMETER_INVALID")
    database = connection(output_dir)
    database.register("source", table_with_ordinal(table))
    partition = ",".join(identifier(key) for key in keys)
    direction = "DESC" if keep == "last" else "ASC"
    ranked = (
        "SELECT *, ROW_NUMBER() OVER(PARTITION BY "
        f"{partition} ORDER BY __moperators_ordinal {direction}) AS __rn, "
        f"COUNT(*) OVER(PARTITION BY {partition}) AS __count FROM source"
    )
    duplicate_row = database.execute(
        f"SELECT COUNT(*) FROM ({ranked}) WHERE __count > 1"
    ).fetchone()
    if duplicate_row is None:
        database.close()
        raise RuntimeError("deduplication count query did not return a result")
    duplicate_count = int(duplicate_row[0])
    if keep == "error" and duplicate_count:
        database.close()
        raise ValueError("DUPLICATE_KEY")
    fields = ",".join(identifier(name) for name in table.column_names)
    unique = database.execute(
        f"SELECT {fields} FROM ({ranked}) WHERE __rn=1 ORDER BY __moperators_ordinal"
    ).to_arrow_table()
    duplicates = database.execute(
        f"SELECT {fields} FROM ({ranked}) WHERE __rn>1 ORDER BY __moperators_ordinal"
    ).to_arrow_table()
    database.close()
    return {
        "unique": write_table(unique, output_dir / "unique.parquet"),
        "duplicates": write_table(duplicates, output_dir / "duplicates.parquet"),
        "summary": summary(
            {"uniqueRowCount": unique.num_rows, "duplicateRowCount": duplicates.num_rows}
        ),
    }
