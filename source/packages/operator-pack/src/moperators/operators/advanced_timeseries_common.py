from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import pyarrow as pa
import pyarrow.csv as arrow_csv
import pyarrow.ipc as arrow_ipc
import pyarrow.parquet as parquet


class Input(Protocol):
    @property
    def path(self) -> Path: ...

    @property
    def format_id(self) -> str: ...


def one(inputs: Mapping[str, Sequence[Input]], port: str = "observations") -> Input:
    values = inputs.get(port, ())
    if len(values) != 1:
        raise ValueError(f"FIELD_REQUIRED: {port}")
    return values[0]


def read_table(source: Input) -> pa.Table:
    if source.format_id == "csv":
        return arrow_csv.read_csv(source.path)
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


def timestamps(table: pa.Table, name: str) -> list[datetime]:
    return [timestamp(value) for value in column(table, name).to_pylist()]


def numbers(table: pa.Table, name: str, *, allow_null: bool = False) -> list[float | None]:
    result: list[float | None] = []
    for value in column(table, name).to_pylist():
        if value is None or isinstance(value, bool) or (
            isinstance(value, str) and not value.strip()
        ):
            if allow_null:
                result.append(None)
                continue
            raise ValueError("NON_NUMERIC_VALUE")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("NON_NUMERIC_VALUE") from exc
        if not math.isfinite(number):
            raise ValueError("NON_NUMERIC_VALUE")
        result.append(number)
    return result


def timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        text = value.strip()
        try:
            result = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
        except ValueError as exc:
            raise ValueError("TIMESTAMP_INVALID") from exc
    else:
        raise ValueError("TIMESTAMP_INVALID")
    if result.tzinfo is not None:
        if result.utcoffset() != timedelta(0):
            raise ValueError("TIMEZONE_UNSUPPORTED")
        result = result.astimezone(UTC).replace(tzinfo=None)
    return result


def bucket(value: datetime, frequency: str) -> datetime:
    if frequency == "day":
        return datetime(value.year, value.month, value.day)
    if frequency == "month":
        return datetime(value.year, value.month, 1)
    if frequency == "quarter":
        return datetime(value.year, ((value.month - 1) // 3) * 3 + 1, 1)
    if frequency == "year":
        return datetime(value.year, 1, 1)
    raise ValueError("FREQUENCY_UNSUPPORTED")


def next_period(value: datetime, frequency: str) -> datetime:
    if frequency == "day":
        return value + timedelta(days=1)
    months = {"month": 1, "quarter": 3, "year": 12}.get(frequency)
    if months is None:
        raise ValueError("FREQUENCY_UNSUPPORTED")
    month_index = value.year * 12 + value.month - 1 + months
    return datetime(month_index // 12, month_index % 12 + 1, 1)


def shift_period(value: datetime, frequency: str, periods: int) -> datetime:
    result = value
    if periods >= 0:
        for _ in range(periods):
            result = next_period(result, frequency)
        return result
    if frequency == "day":
        return value + timedelta(days=periods)
    months = {"month": 1, "quarter": 3, "year": 12}.get(frequency)
    if months is None:
        raise ValueError("FREQUENCY_UNSUPPORTED")
    month_index = value.year * 12 + value.month - 1 + months * periods
    return datetime(month_index // 12, month_index % 12 + 1, 1)


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
