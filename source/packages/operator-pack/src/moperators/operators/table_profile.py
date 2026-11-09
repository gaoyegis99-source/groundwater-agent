from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa

from moperators.operators.table_document_common_v1 import (
    connection,
    identifier,
    one,
    read_table,
    summary,
    write_table,
)


def run(
    inputs: dict[str, list[Any]], parameters: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    del parameters
    table = read_table(one(inputs, "data"))
    database = connection(output_dir)
    database.register("source", table)
    rows: list[dict[str, Any]] = []
    for field in table.schema:
        column = table[field.name]
        non_null = column.length() - column.null_count
        name = identifier(field.name)
        distinct = database.execute(f"SELECT COUNT(DISTINCT {name}) FROM source").fetchone()
        if distinct is None:
            raise RuntimeError("profile query did not return a result")
        row: dict[str, Any] = {
            "field_name": field.name,
            "data_type": str(field.type),
            "row_count": table.num_rows,
            "null_count": column.null_count,
            "non_null_count": non_null,
            "distinct_count": int(distinct[0]),
            "minimum": None,
            "maximum": None,
            "mean": None,
            "population_stddev": None,
            "minimum_length": None,
            "maximum_length": None,
        }
        if non_null and (
            pa.types.is_integer(field.type)
            or pa.types.is_floating(field.type)
            or pa.types.is_decimal(field.type)
        ):
            metrics = database.execute(
                f"SELECT MIN({name}),MAX({name}),AVG({name}),STDDEV_POP({name}) FROM source"
            ).fetchone()
            if metrics is None:
                raise RuntimeError("profile query did not return a result")
            row.update(
                minimum=float(metrics[0]),
                maximum=float(metrics[1]),
                mean=float(metrics[2]),
                population_stddev=float(metrics[3]),
            )
        elif non_null and (pa.types.is_string(field.type) or pa.types.is_large_string(field.type)):
            lengths = database.execute(
                f"SELECT MIN(LENGTH({name})),MAX(LENGTH({name})) FROM source"
            ).fetchone()
            if lengths is None:
                raise RuntimeError("profile query did not return a result")
            row["minimum_length"] = int(lengths[0])
            row["maximum_length"] = int(lengths[1])
        rows.append(row)
    database.close()
    result = pa.Table.from_pylist(rows)
    return {
        "profiles": write_table(result, output_dir / "profiles.parquet"),
        "summary": summary({"rowCount": table.num_rows, "fieldCount": table.num_columns}),
    }
