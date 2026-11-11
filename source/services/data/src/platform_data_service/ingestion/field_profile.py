"""Bounded field profiles collected during the existing whole-table scan."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
import math
import re

import pyarrow as pa


_INTEGER = re.compile(r"^[+-]?\d+$")
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1


def _bounded_text(value: str, limit: int = 160) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _display_value(value: object) -> object:
    if isinstance(value, str):
        return _bounded_text(value)
    if isinstance(value, bytes):
        return f"<binary {len(value)} bytes>"
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return f"<list {len(value)} items>"
    if isinstance(value, dict):
        return f"<object {len(value)} keys>"
    return _bounded_text(str(value))


def _temporal_kind(data_type: pa.DataType) -> str | None:
    if pa.types.is_date(data_type):
        return "date"
    if pa.types.is_timestamp(data_type):
        return "timestamp"
    if pa.types.is_time(data_type):
        return "time"
    return None


def _has_range(data_type: pa.DataType) -> bool:
    return (
        pa.types.is_integer(data_type)
        or pa.types.is_floating(data_type)
        or pa.types.is_decimal(data_type)
        or _temporal_kind(data_type) is not None
    )


def _string_type(data_type: pa.DataType) -> bool:
    return pa.types.is_string(data_type) or pa.types.is_large_string(data_type)


@dataclass
class _FieldProfile:
    data_type: pa.DataType
    sample_limit: int
    null_count: int = 0
    non_null_count: int = 0
    sample_values: list[object] = field(default_factory=list)
    minimum: object | None = None
    maximum: object | None = None
    non_finite_count: int = 0
    numeric_checked: int = 0
    numeric_parsed: int = 0
    numeric_integer_only: bool = True
    numeric_int64_safe: bool = True
    numeric_float64_safe: bool = True
    identifier_like: bool = False

    def consume(self, values: list[object]) -> None:
        for value in values:
            if value is None:
                self.null_count += 1
                continue
            self.non_null_count += 1
            if len(self.sample_values) < self.sample_limit:
                self.sample_values.append(_display_value(value))
            self._consume_range(value)
            self._consume_numeric_string(value)

    def _consume_range(self, value: object) -> None:
        if not _has_range(self.data_type):
            return
        if isinstance(value, float) and not math.isfinite(value):
            self.non_finite_count += 1
            return
        if self.minimum is None or value < self.minimum:
            self.minimum = value
        if self.maximum is None or value > self.maximum:
            self.maximum = value

    def _consume_numeric_string(self, value: object) -> None:
        if not _string_type(self.data_type) or not isinstance(value, str):
            return
        self.numeric_checked += 1
        text = value.strip()
        try:
            parsed = Decimal(text)
        except InvalidOperation:
            self.numeric_integer_only = False
            return
        if not parsed.is_finite():
            self.numeric_integer_only = False
            self.numeric_float64_safe = False
            return
        self.numeric_parsed += 1
        is_integer = bool(_INTEGER.fullmatch(text))
        self.numeric_integer_only = self.numeric_integer_only and is_integer
        if is_integer:
            integer = int(text)
            self.numeric_int64_safe = (
                self.numeric_int64_safe
                and _INT64_MIN <= integer <= _INT64_MAX
            )
            unsigned = text.lstrip("+-")
            if len(unsigned) > 1 and unsigned.startswith("0"):
                self.identifier_like = True
        try:
            converted = float(parsed)
            self.numeric_float64_safe = (
                self.numeric_float64_safe
                and math.isfinite(converted)
                and Decimal(str(converted)) == parsed
            )
        except (OverflowError, ValueError):
            self.numeric_float64_safe = False

    def as_dict(self) -> dict:
        result: dict[str, object] = {
            "non_null_count": self.non_null_count,
            "sample_values": self.sample_values,
        }
        if self.minimum is not None or self.maximum is not None:
            result["value_range"] = {
                "minimum": _display_value(self.minimum),
                "maximum": _display_value(self.maximum),
            }
        if self.non_finite_count:
            result["non_finite_count"] = self.non_finite_count
        if self.numeric_checked:
            numeric: dict[str, object] = {
                "checked_count": self.numeric_checked,
                "parsed_count": self.numeric_parsed,
                "parse_success_rate": round(
                    self.numeric_parsed / self.numeric_checked, 6
                ),
                "identifier_like": self.identifier_like,
            }
            if (
                self.numeric_parsed == self.numeric_checked
                and not self.identifier_like
            ):
                if self.numeric_integer_only and self.numeric_int64_safe:
                    numeric["suggested_type"] = "int64"
                elif self.numeric_float64_safe:
                    numeric["suggested_type"] = "float64"
            result["numeric_string"] = numeric
        temporal_kind = _temporal_kind(self.data_type)
        if temporal_kind is not None:
            temporal: dict[str, object] = {
                "kind": temporal_kind,
                "checked_count": self.non_null_count,
                "parsed_count": self.non_null_count,
                "parse_success_rate": 1.0 if self.non_null_count else None,
            }
            if pa.types.is_timestamp(self.data_type):
                temporal["physical_timezone"] = self.data_type.tz
            result["temporal"] = temporal
        return result


class TableFieldProfiler:
    """Collect whole-table facts without retaining whole columns in memory."""

    sample_limit = 5

    def __init__(self, schema: pa.Schema) -> None:
        self.schema = schema
        self._fields = [
            _FieldProfile(field.type, self.sample_limit) for field in schema
        ]

    def consume(self, batch: pa.RecordBatch) -> None:
        if batch.schema != self.schema:
            raise ValueError("field profile schema changed during ingestion")
        for index, profile in enumerate(self._fields):
            profile.consume(batch.column(index).to_pylist())

    def descriptor(self) -> dict:
        return {
            "schema_version": "field-profile.v1",
            "statistics_scope": "all_rows",
            "sample_scope": "first_non_null_values",
            "sample_limit_per_field": self.sample_limit,
        }

    def field(self, index: int) -> dict:
        profile = self._fields[index]
        return {
            "nullable": self.schema.field(index).nullable,
            "null_count": profile.null_count,
            "profile": profile.as_dict(),
        }
