from __future__ import annotations

from pathlib import Path
from typing import Any

from moperators.operators.table_document_common_v1 import (
    connection,
    identifier,
    json_scalar,
    one,
    read_rules,
    read_table,
    summary,
    table_with_ordinal,
    write_table,
)


def _condition(
    value: Any,
    fields: set[str],
    parameters: list[Any],
    depth: int = 1,
    count: list[int] | None = None,
) -> str:
    count = [0] if count is None else count
    count[0] += 1
    if depth > 8 or count[0] > 256 or not isinstance(value, dict):
        raise ValueError("RULESET_INVALID")
    operation = value.get("op")
    if operation in {"and", "or"}:
        children = value.get("conditions")
        if not isinstance(children, list) or not children:
            raise ValueError("RULESET_INVALID")
        joiner = " AND " if operation == "and" else " OR "
        return (
            "("
            + joiner.join(
                _condition(item, fields, parameters, depth + 1, count) for item in children
            )
            + ")"
        )
    if operation == "not":
        return f"(NOT {_condition(value.get('condition'), fields, parameters, depth + 1, count)})"
    field = value.get("field")
    if field not in fields:
        raise ValueError("FIELD_REQUIRED")
    name = identifier(field)
    if operation in {"isNull", "isNotNull"}:
        return f"({name} IS {'NOT ' if operation == 'isNotNull' else ''}NULL)"
    comparison = {"eq": "=", "ne": "<>", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}.get(
        operation
    )
    scalar = value.get("value")
    if comparison is not None and json_scalar(scalar):
        parameters.append(scalar)
        return f"({name} {comparison} ?)"
    if (
        operation == "in"
        and isinstance(scalar, list)
        and scalar
        and all(json_scalar(item) for item in scalar)
    ):
        parameters.extend(scalar)
        return f"({name} IN ({','.join('?' for _ in scalar)}))"
    if (
        operation == "between"
        and json_scalar(value.get("minimum"))
        and json_scalar(value.get("maximum"))
    ):
        parameters.extend([value["minimum"], value["maximum"]])
        return f"({name} BETWEEN ? AND ?)"
    raise ValueError("RULESET_INVALID")


def run(
    inputs: dict[str, list[Any]], parameters: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    del parameters
    table = read_table(one(inputs, "data"))
    rules = read_rules(one(inputs, "rules"), "table-filter-rules.v1")
    bound: list[Any] = []
    predicate = _condition(rules.get("condition"), set(table.column_names), bound)
    database = connection(output_dir)
    database.register("source", table_with_ordinal(table))
    fields = ",".join(identifier(name) for name in table.column_names)
    order = "ORDER BY __moperators_ordinal"
    matched = database.execute(
        f"SELECT {fields} FROM source WHERE COALESCE({predicate}, FALSE) {order}", bound
    ).to_arrow_table()
    rejected = database.execute(
        f"SELECT {fields} FROM source WHERE NOT COALESCE({predicate}, FALSE) {order}", bound
    ).to_arrow_table()
    database.close()
    return {
        "matched": write_table(matched, output_dir / "matched.parquet"),
        "rejected": write_table(rejected, output_dir / "rejected.parquet"),
        "summary": summary(
            {"matchedRowCount": matched.num_rows, "rejectedRowCount": rejected.num_rows}
        ),
    }
