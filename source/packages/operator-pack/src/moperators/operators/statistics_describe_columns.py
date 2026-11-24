from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa

from moperators.operators.native import (
    StagedInput,
    _declared_nulls,
    _is_declared_null,
    _statistics_values,
)
from moperators.operators.table_document_common_v1 import one, read_table, summary

_NUMERIC_TEXT = re.compile(
    r"[+-]?(?:(?:0|[1-9][0-9]*)(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
)
_NONFINITE_TEXT = {"nan", "inf", "infinity"}


def _numeric_text_column(values: list[Any], nulls: frozenset[str]) -> bool:
    has_finite = False
    for value in values:
        if _is_declared_null(value, nulls):
            continue
        text = value.strip()
        unsigned = text[1:] if text.startswith(("+", "-")) else text
        if unsigned.lower() in _NONFINITE_TEXT:
            continue
        if _NUMERIC_TEXT.fullmatch(text) is None:
            return False
        has_finite |= math.isfinite(float(text))
    return has_finite


def run(
    inputs: dict[str, list[StagedInput]],
    parameters: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    del output_dir
    infer = parameters.get("inferNumericStrings", False)
    if set(parameters) - {"inferNumericStrings"} or not isinstance(infer, bool):
        raise ValueError("PARAMETER_INVALID")

    source = one(inputs, "data")
    nulls = _declared_nulls(source)
    try:
        table = read_table(source)
    except (pa.ArrowException, UnicodeError) as error:
        raise ValueError("INPUT_INVALID") from error
    names = table.column_names
    if len(set(names)) != len(names) or any(not name.strip() for name in names):
        raise ValueError("HEADER_INVALID")

    columns = {}
    excluded = {}
    for field in table.schema:
        dtype = field.type.value_type if pa.types.is_dictionary(field.type) else field.type
        if pa.types.is_integer(dtype) or pa.types.is_floating(dtype) or pa.types.is_decimal(dtype):
            values = table[field.name].to_pylist()
            basis = "PHYSICAL_TYPE"
        elif pa.types.is_string(dtype) or pa.types.is_large_string(dtype):
            if not infer:
                excluded[field.name] = "NUMERIC_INFERENCE_DISABLED"
                continue
            values = table[field.name].to_pylist()
            if not _numeric_text_column(values, nulls):
                excluded[field.name] = "TEXT_NOT_NUMERIC"
                continue
            basis = "INFERRED_NUMERIC_TEXT"
        else:
            excluded[field.name] = "NON_NUMERIC_TYPE"
            continue
        try:
            with np.errstate(over="raise", invalid="raise"):
                statistics = _statistics_values(
                    values,
                    null_values=nulls,
                    allow_numeric_strings=basis == "INFERRED_NUMERIC_TEXT",
                )
        except FloatingPointError as error:
            raise ValueError("NUMERIC_RANGE_OVERFLOW") from error
        columns[field.name] = {"numericBasis": basis, "statistics": statistics}

    if not columns:
        raise ValueError("NO_NUMERIC_COLUMNS")
    return {"summary": summary({
        "schemaVersion": "statistics-describe-columns.v1",
        "scope": {
            "basis": "FULL_STAGED_INPUT",
            "inputFormat": source.format_id,
            "rowCount": table.num_rows,
        },
        "conventions": {
            "stddev": "population-ddof-0",
            "quantiles": "linear-interpolation",
            "declaredNulls": "excluded-and-counted",
            "nonFiniteValues": "excluded-and-counted",
        },
        "columns": columns,
        "excludedColumns": excluded,
    })}
