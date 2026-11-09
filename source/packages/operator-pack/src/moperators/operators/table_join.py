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


def _duplicates(database: Any, relation: str, keys: list[str]) -> bool:
    names = ",".join(identifier(key) for key in keys)
    non_null = " AND ".join(f"{identifier(key)} IS NOT NULL" for key in keys)
    query = f"SELECT 1 FROM {relation} WHERE {non_null} GROUP BY {names} HAVING COUNT(*)>1 LIMIT 1"
    return database.execute(query).fetchone() is not None


def run(
    inputs: dict[str, list[Any]], parameters: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    left, right = read_table(one(inputs, "left")), read_table(one(inputs, "right"))
    rules = read_rules(one(inputs, "rules"), "table-join-rules.v1")
    keys = rules.get("keys")
    if (
        not isinstance(keys, list)
        or not 1 <= len(keys) <= 64
        or any(not isinstance(item, dict) for item in keys)
    ):
        raise ValueError("RULESET_INVALID")
    left_keys, right_keys = (
        [item.get("left") for item in keys],
        [item.get("right") for item in keys],
    )
    require_fields(left, left_keys)
    require_fields(right, right_keys)
    join_type, cardinality = parameters.get("joinType"), parameters.get("cardinality")
    maximum = parameters.get("maxOutputRows")
    if (
        join_type not in {"inner", "left", "right", "full"}
        or cardinality not in {"oneToOne", "oneToMany", "manyToOne", "manyToMany"}
        or not isinstance(maximum, int)
        or maximum < 1
    ):
        raise ValueError("PARAMETER_INVALID")
    database = connection(output_dir)
    database.register("left_source", table_with_ordinal(left, "__left_ordinal"))
    database.register("right_source", table_with_ordinal(right, "__right_ordinal"))
    if cardinality in {"oneToOne", "oneToMany"} and _duplicates(database, "left_source", left_keys):
        raise ValueError("JOIN_CARDINALITY_VIOLATION")
    if cardinality in {"oneToOne", "manyToOne"} and _duplicates(
        database, "right_source", right_keys
    ):
        raise ValueError("JOIN_CARDINALITY_VIOLATION")
    condition = " AND ".join(
        f"l.{identifier(a)}=r.{identifier(b)}" for a, b in zip(left_keys, right_keys, strict=True)
    )
    selections: list[str] = []
    collision = (
        (set(left.column_names) & set(right.column_names)) - set(left_keys) - set(right_keys)
    )
    for name in left.column_names:
        if name in left_keys and join_type in {"right", "full"}:
            right_name = right_keys[left_keys.index(name)]
            selections.append(
                f"COALESCE(l.{identifier(name)},r.{identifier(right_name)}) AS {identifier(name)}"
            )
        else:
            target = f"left__{name}" if name in collision else name
            selections.append(f"l.{identifier(name)} AS {identifier(target)}")
    for name in right.column_names:
        if name in right_keys:
            continue
        target = f"right__{name}" if name in collision or name in left.column_names else name
        selections.append(f"r.{identifier(name)} AS {identifier(target)}")
    join_sql = {"inner": "INNER", "left": "LEFT", "right": "RIGHT", "full": "FULL"}[join_type]
    base = f"FROM left_source l {join_sql} JOIN right_source r ON {condition}"
    count_row = database.execute(f"SELECT COUNT(*) {base}").fetchone()
    if count_row is None:
        database.close()
        raise RuntimeError("join count query did not return a result")
    count = int(count_row[0])
    if count > maximum:
        database.close()
        raise ValueError("OUTPUT_ROW_LIMIT_EXCEEDED")
    order = (
        "ORDER BY COALESCE(l.__left_ordinal,9223372036854775807),"
        "COALESCE(r.__right_ordinal,9223372036854775807)"
    )
    result = database.execute(f"SELECT {','.join(selections)} {base} {order}").to_arrow_table()
    database.close()
    return {
        "data": write_table(result, output_dir / "data.parquet"),
        "summary": summary({"rowCount": result.num_rows}),
    }
