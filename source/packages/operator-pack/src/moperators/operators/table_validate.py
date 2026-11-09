from __future__ import annotations

from pathlib import Path
from typing import Any

from moperators.operators.table_document_common_v1 import (
    connection,
    identifier,
    literal,
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
    del parameters
    table = read_table(one(inputs, "data"))
    rules = read_rules(one(inputs, "rules"), "table-validation-rules.v1")
    constraints = rules.get("constraints")
    if not isinstance(constraints, list) or not 1 <= len(constraints) <= 1024:
        raise ValueError("RULESET_INVALID")
    constraint_ids = [item.get("id") for item in constraints]
    if len(constraint_ids) != len(set(constraint_ids)):
        raise ValueError("RULESET_INVALID")
    queries: list[str] = []
    for constraint in constraints:
        if not isinstance(constraint, dict) or not isinstance(constraint.get("id"), str):
            raise ValueError("RULESET_INVALID")
        constraint_id, kind = constraint["id"], constraint.get("kind")
        predicate: str
        if kind == "range":
            field = constraint.get("field")
            require_fields(table, [field])
            parts = []
            if "minimum" in constraint:
                parts.append(f"{identifier(field)} < {float(constraint['minimum'])}")
            if "maximum" in constraint:
                parts.append(f"{identifier(field)} > {float(constraint['maximum'])}")
            if not parts:
                raise ValueError("RULESET_INVALID")
            predicate = " OR ".join(parts)
        elif kind == "notNull":
            field = constraint.get("field")
            require_fields(table, [field])
            predicate = f"{identifier(field)} IS NULL"
        elif kind == "length":
            field = constraint.get("field")
            require_fields(table, [field])
            parts = []
            if "minimum" in constraint:
                parts.append(f"LENGTH({identifier(field)}) < {int(constraint['minimum'])}")
            if "maximum" in constraint:
                parts.append(f"LENGTH({identifier(field)}) > {int(constraint['maximum'])}")
            predicate = " OR ".join(parts) if parts else "FALSE"
        elif kind == "enum":
            field, values = constraint.get("field"), constraint.get("values")
            require_fields(table, [field])
            if not isinstance(values, list) or not values:
                raise ValueError("RULESET_INVALID")
            items = ",".join(literal(str(item)) for item in values)
            predicate = f"CAST({identifier(field)} AS VARCHAR) NOT IN ({items})"
        elif kind == "unique":
            fields = constraint.get("fields")
            if not isinstance(fields, list) or not fields:
                raise ValueError("RULESET_INVALID")
            require_fields(table, fields)
            partition = ",".join(identifier(field) for field in fields)
            queries.append(
                "SELECT __row_index+1 AS row_number,"
                f"{literal(constraint_id)} AS constraint_id,'DUPLICATE_KEY' AS code "
                "FROM (SELECT __row_index,COUNT(*) OVER(PARTITION BY "
                f"{partition}) AS n FROM source) WHERE n>1"
            )
            continue
        elif kind == "requiredField":
            field = constraint.get("field")
            if field not in table.column_names:
                queries.append(
                    "SELECT __row_index+1 AS row_number,"
                    f"{literal(constraint_id)} AS constraint_id,'FIELD_REQUIRED' AS code "
                    "FROM source"
                )
                continue
            predicate = "FALSE"
        elif kind == "type":
            field, expected = constraint.get("field"), constraint.get("dataType")
            require_fields(table, [field])
            if str(table.schema.field(field).type) != expected:
                queries.append(
                    "SELECT __row_index+1 AS row_number,"
                    f"{literal(constraint_id)} AS constraint_id,'TYPE_MISMATCH' AS code "
                    "FROM source"
                )
            continue
        else:
            raise ValueError("RULESET_INVALID")
        queries.append(
            "SELECT __row_index+1 AS row_number,"
            f"{literal(constraint_id)} AS constraint_id,'CONSTRAINT_VIOLATION' AS code "
            f"FROM source WHERE COALESCE({predicate},FALSE)"
        )
    database = connection(output_dir)
    database.register("source", table_with_ordinal(table, "__row_index"))
    union = (
        " UNION ALL ".join(queries)
        if queries
        else "SELECT 0::BIGINT row_number,'' constraint_id,'' code WHERE FALSE"
    )
    violations = database.execute(
        f"SELECT * FROM ({union}) ORDER BY row_number,constraint_id"
    ).to_arrow_table()
    database.register("violations", violations)
    fields = ",".join(identifier(name) for name in table.column_names)
    valid = database.execute(
        f"SELECT {fields} FROM source WHERE __row_index+1 NOT IN "
        "(SELECT row_number FROM violations) ORDER BY __row_index"
    ).to_arrow_table()
    invalid = database.execute(
        f"SELECT {fields} FROM source WHERE __row_index+1 IN "
        "(SELECT row_number FROM violations) ORDER BY __row_index"
    ).to_arrow_table()
    database.close()
    return {
        "valid": write_table(valid, output_dir / "valid.parquet"),
        "invalid": write_table(invalid, output_dir / "invalid.parquet"),
        "violations": write_table(violations, output_dir / "violations.parquet"),
        "summary": summary(
            {
                "validRowCount": valid.num_rows,
                "invalidRowCount": invalid.num_rows,
                "violationCount": violations.num_rows,
            }
        ),
    }
