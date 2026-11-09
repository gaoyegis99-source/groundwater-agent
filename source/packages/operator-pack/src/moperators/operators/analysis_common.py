from __future__ import annotations

import csv
import math
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pyarrow as pa
import pyarrow.ipc as arrow_ipc
import pyarrow.parquet as parquet


class TableInput(Protocol):
    @property
    def path(self) -> Path: ...

    @property
    def format_id(self) -> str: ...

    @property
    def metadata(self) -> dict[str, Any]: ...


def artifact(path: Path) -> dict[str, Any]:
    return {
        "kind": "artifact",
        "path": str(path),
        "fileName": path.name,
        "mediaType": "application/vnd.apache.parquet",
        "format": "parquet",
    }


def rows(source: TableInput) -> list[dict[str, Any]]:
    if source.format_id == "csv":
        with source.path.open(encoding="utf-8", newline="") as stream:
            return [dict(row) for row in csv.DictReader(stream)]
    if source.format_id == "parquet":
        return parquet.read_table(source.path).to_pylist()
    if source.format_id == "arrow-ipc-file":
        with arrow_ipc.open_file(source.path) as reader:
            return reader.read_all().to_pylist()
    raise ValueError("FORMAT_UNSUPPORTED")


def write(rows_: list[dict[str, Any]], path: Path) -> dict[str, Any]:
    if not rows_:
        raise ValueError("EMPTY_RESULT")
    columns = list(dict.fromkeys(key for row in rows_ for key in row))
    normalized = [{column: row.get(column) for column in columns} for row in rows_]
    parquet.write_table(pa.Table.from_pylist(normalized), path)
    return artifact(path)


def field(row: dict[str, Any], name: str) -> Any:
    if not name or name not in row:
        raise ValueError(f"FIELD_REQUIRED: {name}")
    return row[name]


def number(value: Any) -> float:
    if value is None or isinstance(value, bool) or (isinstance(value, str) and not value.strip()):
        raise ValueError("NON_NUMERIC_VALUE")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("NON_NUMERIC_VALUE") from exc
    if not math.isfinite(result):
        raise ValueError("NON_NUMERIC_VALUE")
    return result


def input_timezone(source: TableInput) -> ZoneInfo | None:
    contract = _data_contract(source)
    value = contract.get("timezone")
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError("INPUT_METADATA_INVALID")
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("TIMEZONE_UNSUPPORTED") from None


def input_null_values(source: TableInput) -> frozenset[str]:
    values = _data_contract(source).get("nullValues", [])
    if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
        raise ValueError("INPUT_METADATA_INVALID")
    return frozenset(values)


def is_missing(value: Any, null_values: frozenset[str] = frozenset()) -> bool:
    return value is None or (isinstance(value, str) and (not value.strip() or value in null_values))


def _data_contract(source: TableInput) -> dict[str, Any]:
    metadata = getattr(source, "metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("INPUT_METADATA_INVALID")
    contract = metadata.get("dataContract", {})
    if not isinstance(contract, dict):
        raise ValueError("INPUT_METADATA_INVALID")
    return contract


def timestamp(value: Any, timezone_: ZoneInfo | None = None) -> datetime:
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
    if timezone_ is not None:
        if result.tzinfo is not None:
            return result.astimezone(timezone_)
        first = result.replace(tzinfo=timezone_, fold=0)
        second = result.replace(tzinfo=timezone_, fold=1)
        valid = [
            candidate
            for candidate in (first, second)
            if candidate.astimezone(UTC).astimezone(timezone_).replace(tzinfo=None) == result
        ]
        if not valid:
            raise ValueError("TIMESTAMP_INVALID")
        if len(valid) == 2 and valid[0].utcoffset() != valid[1].utcoffset():
            raise ValueError("TIMESTAMP_AMBIGUOUS")
        return valid[0]
    if result.tzinfo is not None:
        if result.utcoffset() != timedelta(0):
            raise ValueError("TIMEZONE_UNSUPPORTED")
        result = result.astimezone(UTC).replace(tzinfo=None)
    return result


def iso(value: datetime) -> str:
    encoded = value.isoformat(timespec="seconds")
    if value.tzinfo is None:
        return encoded + "Z"
    return encoded.removesuffix("+00:00") + ("Z" if encoded.endswith("+00:00") else "")


def bucket(value: datetime, frequency: str) -> datetime:
    if frequency == "day":
        return datetime(value.year, value.month, value.day, tzinfo=value.tzinfo)
    if frequency == "month":
        return datetime(value.year, value.month, 1, tzinfo=value.tzinfo)
    if frequency == "quarter":
        return datetime(
            value.year,
            ((value.month - 1) // 3) * 3 + 1,
            1,
            tzinfo=value.tzinfo,
        )
    if frequency == "year":
        return datetime(value.year, 1, 1, tzinfo=value.tzinfo)
    raise ValueError("FREQUENCY_UNSUPPORTED")


def next_period(value: datetime, frequency: str) -> datetime:
    if frequency == "day":
        return value + timedelta(days=1)
    months = {"month": 1, "quarter": 3, "year": 12}.get(frequency)
    if months is None:
        raise ValueError("FREQUENCY_UNSUPPORTED")
    month_index = value.year * 12 + value.month - 1 + months
    return datetime(month_index // 12, month_index % 12 + 1, 1, tzinfo=value.tzinfo)


def grouped_series(
    source: TableInput,
    parameters: dict[str, Any],
    *,
    reject_duplicates: bool = False,
) -> dict[Any, list[tuple[datetime, float]]]:
    group_field = parameters["groupField"]
    time_field = parameters["timeField"]
    value_field = parameters["valueField"]
    grouped: dict[Any, list[tuple[datetime, float]]] = defaultdict(list)
    seen: set[tuple[Any, datetime]] = set()
    timezone_ = input_timezone(source)
    null_values = input_null_values(source)
    for row in rows(source):
        group = field(row, group_field)
        grouped[group]
        moment = timestamp(field(row, time_field), timezone_)
        if reject_duplicates and (group, moment) in seen:
            raise ValueError("DUPLICATE_TIMESTAMP")
        seen.add((group, moment))
        raw_value = field(row, value_field)
        if is_missing(raw_value, null_values):
            continue
        grouped[group].append((moment, number(raw_value)))
    for values in grouped.values():
        values.sort(key=lambda item: item[0])
    return grouped
