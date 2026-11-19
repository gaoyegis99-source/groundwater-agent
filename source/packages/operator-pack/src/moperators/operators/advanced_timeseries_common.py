from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.csv as arrow_csv
import pyarrow.ipc as arrow_ipc
import pyarrow.parquet as parquet

from moperators.operators.analysis_common import (
    TableInput,
    is_missing,
    next_period,
    number,
)
from moperators.operators.analysis_common import (
    bucket as bucket,
)
from moperators.operators.analysis_common import (
    timestamp as timestamp,
)
from moperators.operators.extension_common import shift_period as shift_period


def one(inputs: Mapping[str, Sequence[TableInput]], port: str = "observations") -> TableInput:
    values = inputs.get(port, ())
    if len(values) != 1:
        raise ValueError(f"FIELD_REQUIRED: {port}")
    return values[0]


def read_table(source: TableInput, time_field: str, value_field: str) -> pa.Table:
    if source.format_id == "csv":
        return arrow_csv.read_csv(
            source.path,
            convert_options=arrow_csv.ConvertOptions(
                column_types={time_field: pa.string(), value_field: pa.string()},
            ),
        )
    if source.format_id == "parquet":
        return parquet.read_table(source.path)
    if source.format_id == "arrow-ipc-file":
        with arrow_ipc.open_file(source.path) as reader:
            return reader.read_all()
    raise ValueError("FORMAT_UNSUPPORTED")


def column(table: pa.Table, name: str) -> pa.ChunkedArray:
    if not name or name not in table.column_names:
        raise ValueError(f"FIELD_REQUIRED: {name}")
    return table[name]


def timestamps(table: pa.Table, name: str, timezone_: ZoneInfo | None) -> list[datetime]:
    return [timestamp(value, timezone_) for value in column(table, name).to_pylist()]


def numbers(table: pa.Table, name: str, null_values: frozenset[str]) -> list[float | None]:
    return [
        None if is_missing(value, null_values) else number(value)
        for value in column(table, name).to_pylist()
    ]


def period_range(start: datetime, end: datetime, frequency: str) -> list[datetime]:
    if start > end:
        raise ValueError("TIME_RANGE_INVALID")
    current = bucket(start, frequency)
    final = bucket(end, frequency)
    result: list[datetime] = []
    while current <= final:
        result.append(current)
        current = next_period(current, frequency)
    return result


def ensure_unique(keys: Iterable[tuple[Any, datetime]], error_code: str) -> None:
    seen: set[tuple[Any, datetime]] = set()
    for key in keys:
        if key in seen:
            raise ValueError(error_code)
        seen.add(key)


def write_rows(
    rows: Iterable[dict[str, Any]],
    path: Path,
    schema: pa.Schema,
    *,
    batch_size: int = 65_536,
) -> dict[str, Any]:
    writer = parquet.ParquetWriter(path, schema)
    pending: list[dict[str, Any]] = []
    try:
        for row in rows:
            pending.append(row)
            if len(pending) == batch_size:
                writer.write_table(pa.Table.from_pylist(pending, schema=schema))
                pending.clear()
        if pending:
            writer.write_table(pa.Table.from_pylist(pending, schema=schema))
        elif path.stat().st_size == 4:
            writer.write_table(pa.Table.from_pylist([], schema=schema))
    finally:
        writer.close()
    return {
        "kind": "artifact",
        "path": str(path),
        "fileName": path.name,
        "mediaType": "application/vnd.apache.parquet",
        "format": "parquet",
    }


def value_summary(value: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "value", "value": value}
