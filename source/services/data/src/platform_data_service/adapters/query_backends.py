from datetime import date, datetime, time
from decimal import Decimal
import math
import duckdb

from ..errors import DataProblemError
from .sandbox import restrict_query


def scalar(value):
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        raise DataProblemError("NONFINITE_QUERY_RESULT")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise DataProblemError("RESULT_TYPE_UNSUPPORTED")


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


class DuckBackend:
    def execute(self, plan, store, settings, work):
        inp = plan["inputs"][0]
        obj = next(x for x in inp["objects"] if x["role"] == "query")
        store.verify(obj)
        path = str(store.path(obj))
        config = {
            "threads": 1,
            "memory_limit": f"{settings.query_memory_mb}MB",
            "temp_directory": str(work / "duck-tmp"),
            "max_temp_directory_size": f"{settings.temp_mb}MB",
            "autoinstall_known_extensions": "false",
            "autoload_known_extensions": "false",
            "allow_unsigned_extensions": "false",
        }
        try:
            with duckdb.connect(config=config) as db:
                db.execute("SET allowed_paths = ?", [[path]])
                db.execute("SET enable_external_access = false")
                restrict_query(store.objects, work, settings)
                db.read_parquet(path).create_view("data")
                params, conditions = [], []
                for condition in plan["filters"]:
                    field, op, value = (
                        quoted(condition["field"]),
                        condition["op"],
                        condition["value"],
                    )
                    if op == "is_null":
                        conditions.append(f"{field} IS NULL")
                    elif value is None and op in {"eq", "ne"}:
                        conditions.append(
                            f"{field} IS {'NOT ' if op == 'ne' else ''}NULL"
                        )
                    elif op == "contains":
                        conditions.append(f"contains(CAST({field} AS VARCHAR), ?)")
                        params.append(str(value))
                    else:
                        operator = {
                            "eq": "=",
                            "ne": "<>",
                            "lt": "<",
                            "le": "<=",
                            "gt": ">",
                            "ge": ">=",
                        }[op]
                        conditions.append(f"{field} {operator} ?")
                        params.append(value)
                where = " WHERE " + " AND ".join(conditions) if conditions else ""
                matched = db.execute(
                    "SELECT count(*) FROM data" + where, params
                ).fetchone()[0]
                groups = plan["group_by"]
                if plan["aggregates"]:
                    selection = [quoted(x) for x in groups] + [
                        f"{a['function']}({quoted(a['field']) if a['field'] else '*'}) AS {quoted(a['alias'])}"
                        for a in plan["aggregates"]
                    ]
                    suffix = (
                        " GROUP BY " + ",".join(quoted(x) for x in groups)
                        if groups
                        else ""
                    )
                    tie = [quoted(x) for x in groups]
                else:
                    selection = [
                        quoted(x)
                        for x in plan["fields"]
                        or [f["id"] for f in inp["descriptor"]["fields"]]
                    ]
                    suffix, tie = "", ['"_row"']
                order = [
                    f"{quoted(x['field'])} {x['direction']} NULLS LAST"
                    for x in plan["sort"]
                ] + tie
                suffix += " ORDER BY " + ",".join(order) if order else ""
                sql = (
                    "SELECT "
                    + ",".join(selection)
                    + " FROM data"
                    + where
                    + suffix
                    + " LIMIT ?"
                )
                cursor = db.execute(sql, [*params, settings.result_rows + 1])
                fields = [{"id": x[0], "type": str(x[1])} for x in cursor.description]
                rows, size = [], 0
                from .database import canonical

                while row := cursor.fetchone():
                    converted = [scalar(x) for x in row]
                    size += len(canonical(converted).encode())
                    if size > settings.result_bytes:
                        raise DataProblemError("QUERY_RESULT_BUDGET", 413)
                    rows.append(converted)
                return {
                    "fields": fields,
                    "rows": rows[: settings.result_rows],
                    "coverage": {
                        "source_rows": inp["descriptor"]["row_count"],
                        "matched_rows": matched,
                        "calculation": "all_matching_rows",
                    },
                    "truncated": len(rows) > settings.result_rows,
                    "engine": {"name": "duckdb", "version": duckdb.__version__},
                }
        except duckdb.OutOfMemoryException:
            raise DataProblemError("QUERY_MEMORY_BUDGET", 413) from None
        except (
            duckdb.ConversionException,
            duckdb.InvalidInputException,
            duckdb.BinderException,
        ):
            raise DataProblemError("QUERY_TYPE_INVALID") from None


class MetricBackend:
    def execute(self, plan, store, settings, work):
        # Immutable metric rows were selected and compatibility-checked in PostgreSQL at submission.
        restrict_query(store.objects, work, settings)
        names = [
            "version_id",
            "name",
            "definition",
            "unit",
            "evaluation_scope",
            "value",
        ]
        return {
            "fields": [
                {"id": x, "type": "double" if x == "value" else "string"} for x in names
            ],
            "rows": [[m[x] for x in names] for m in plan["metric_rows"]],
            "coverage": {
                "calculation": "all_selected_versions",
                "versions": len(plan["metric_rows"]),
            },
            "truncated": False,
            "engine": {"name": "postgresql", "version": plan["postgres_version"]},
        }


BACKENDS = {"table": DuckBackend(), "metrics": MetricBackend()}
