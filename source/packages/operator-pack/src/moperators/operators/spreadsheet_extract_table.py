from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

import pyarrow as pa
from openpyxl import load_workbook

from moperators.operators.table_document_common_v1 import one, summary, write_table

RESERVED = {"__sheet_name", "__source_row_number"}


def _normalized_columns(rows: list[list[Any]]) -> list[list[Any]]:
    if not rows:
        return rows
    columns = list(zip(*rows, strict=True))
    normalized: list[list[Any]] = [[] for _ in rows]
    for column in columns:
        values = [value for value in column if value is not None]
        types = {type(value) for value in values}
        stringify = len(types) > 1 and not types <= {int, float}
        for index, value in enumerate(column):
            normalized[index].append(str(value) if stringify and value is not None else value)
    return normalized


def run(
    inputs: dict[str, list[Any]], parameters: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    source = one(inputs, "workbook")
    if source.format_id != "xlsx":
        raise ValueError("FORMAT_UNSUPPORTED")
    sheet_name, header_row = parameters.get("sheetName"), parameters.get("headerRow")
    formula_mode, maximum = parameters.get("formulaMode"), parameters.get("maxOutputRows")
    if (
        not isinstance(sheet_name, str)
        or not sheet_name
        or not isinstance(header_row, int)
        or header_row < 1
    ):
        raise ValueError("PARAMETER_INVALID")
    if (
        formula_mode not in {"formulaText", "cachedValue"}
        or not isinstance(maximum, int)
        or maximum < 1
    ):
        raise ValueError("PARAMETER_INVALID")
    try:
        formula_book = load_workbook(
            BytesIO(source.path.read_bytes()), data_only=False, read_only=False
        )
        value_book = load_workbook(
            BytesIO(source.path.read_bytes()),
            data_only=formula_mode == "cachedValue",
            read_only=False,
        )
    except (BadZipFile, ParseError, KeyError, ValueError) as exc:
        raise ValueError("WORKBOOK_INVALID") from exc
    if sheet_name not in formula_book.sheetnames:
        raise ValueError("SHEET_REQUIRED")
    formula_sheet, sheet = formula_book[sheet_name], value_book[sheet_name]
    nonempty = [
        (cell.row, cell.column)
        for row in formula_sheet.iter_rows()
        for cell in row
        if cell.value is not None
    ]
    if not nonempty:
        raise ValueError("HEADER_INVALID")
    last_row, last_column = max(row for row, _ in nonempty), max(column for _, column in nonempty)
    if header_row > last_row:
        raise ValueError("HEADER_INVALID")
    if any(
        item.min_row <= last_row and item.max_row >= header_row and item.min_col <= last_column
        for item in formula_sheet.merged_cells.ranges
    ):
        raise ValueError("MERGED_CELLS_UNSUPPORTED")
    headers = [formula_sheet.cell(header_row, column).value for column in range(1, last_column + 1)]
    if any(value is None or not str(value).strip() for value in headers):
        raise ValueError("HEADER_INVALID")
    names = [str(value) for value in headers]
    if len(names) != len(set(names)):
        raise ValueError("HEADER_INVALID")
    if RESERVED & set(names):
        raise ValueError("OUTPUT_FIELD_CONFLICT")
    row_count = last_row - header_row
    if row_count > maximum:
        raise ValueError("OUTPUT_ROW_LIMIT_EXCEEDED")
    values: list[list[Any]] = []
    for row_number in range(header_row + 1, last_row + 1):
        row: list[Any] = []
        for column in range(1, last_column + 1):
            formula = formula_sheet.cell(row_number, column).value
            value = sheet.cell(row_number, column).value
            if (
                formula_mode == "cachedValue"
                and isinstance(formula, str)
                and formula.startswith("=")
                and value is None
            ):
                raise ValueError("FORMULA_VALUE_UNAVAILABLE")
            row.append(value)
        values.append(row)
    values = _normalized_columns(values)
    records = [
        {
            "__sheet_name": sheet_name,
            "__source_row_number": header_row + index + 1,
            **dict(zip(names, row, strict=True)),
        }
        for index, row in enumerate(values)
    ]
    result = (
        pa.Table.from_pylist(records)
        if records
        else pa.table(
            {
                "__sheet_name": pa.array([], type=pa.string()),
                "__source_row_number": pa.array([], type=pa.int64()),
                **{name: pa.array([], type=pa.string()) for name in names},
            }
        )
    )
    formula_book.close()
    value_book.close()
    return {
        "data": write_table(result, output_dir / "data.parquet"),
        "summary": summary(
            {"sheetName": sheet_name, "rowCount": result.num_rows, "fieldCount": len(names)}
        ),
    }
