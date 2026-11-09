from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from moperators.operators.pdf_text_common_v1 import parse_pdf
from moperators.operators.table_document_common_v1 import one, summary, write_markdown, write_table

BLOCK_SCHEMA = pa.schema(
    [
        ("block_id", pa.string()),
        ("block_index", pa.int64()),
        ("block_type", pa.string()),
        ("text", pa.string()),
        ("page_number", pa.int64()),
        ("x0", pa.float64()),
        ("y0", pa.float64()),
        ("x1", pa.float64()),
        ("y1", pa.float64()),
        ("paragraph_number", pa.int64()),
        ("table_number", pa.int64()),
        ("table_row", pa.int64()),
        ("table_column", pa.int64()),
    ]
)


def _base(index: int, block_type: str, text: str) -> dict[str, Any]:
    return {
        "block_id": f"block-{index:08d}",
        "block_index": index,
        "block_type": block_type,
        "text": text,
        "page_number": None,
        "x0": None,
        "y0": None,
        "x1": None,
        "y1": None,
        "paragraph_number": None,
        "table_number": None,
        "table_row": None,
        "table_column": None,
    }


def _docx(path: Path) -> tuple[list[dict[str, Any]], int, list[int]]:
    try:
        document = Document(str(path))
    except Exception as exc:
        raise ValueError("DOCUMENT_INVALID") from exc
    rows: list[dict[str, Any]] = []
    paragraph_number = table_number = 0
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            paragraph_number += 1
            text = Paragraph(child, document).text
            if text:
                row = _base(len(rows) + 1, "paragraph", text)
                row["paragraph_number"] = paragraph_number
                rows.append(row)
        elif child.tag.endswith("}tbl"):
            table_number += 1
            table = Table(child, document)
            for row_number, table_row in enumerate(table.rows, 1):
                for column_number, cell in enumerate(table_row.cells, 1):
                    text = "\n".join(item.text for item in cell.paragraphs if item.text)
                    if text:
                        row = _base(len(rows) + 1, "table_cell", text)
                        row.update(
                            table_number=table_number,
                            table_row=row_number,
                            table_column=column_number,
                        )
                        rows.append(row)
    return rows, 0, []


def _pdf(path: Path, maximum_pages: int) -> tuple[list[dict[str, Any]], int, list[int]]:
    inspection = parse_pdf(path, maximum_pages)
    rows: list[dict[str, Any]] = []
    for block in inspection.blocks:
        row = _base(len(rows) + 1, "text", block.text)
        row.update(
            page_number=block.page_number,
            x0=block.x0,
            y0=block.y0,
            x1=block.x1,
            y1=block.y1,
        )
        rows.append(row)
    if not rows:
        raise ValueError("TEXT_LAYER_REQUIRED")
    return rows, inspection.page_count, inspection.empty_pages


def _text(path: Path) -> tuple[list[dict[str, Any]], int, list[int]]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("ENCODING_UNSUPPORTED") from exc
    rows = [
        _base(index, "paragraph", line)
        for index, line in enumerate((line for line in text.splitlines() if line), 1)
    ]
    return rows, 0, []


def run(
    inputs: dict[str, list[Any]], parameters: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    source = one(inputs, "document")
    max_pages, max_characters = parameters.get("maxPages"), parameters.get("maxOutputCharacters")
    if (
        not isinstance(max_pages, int)
        or max_pages < 1
        or not isinstance(max_characters, int)
        or max_characters < 1
    ):
        raise ValueError("PARAMETER_INVALID")
    if source.format_id == "pdf":
        rows, pages, empty_pages = _pdf(source.path, max_pages)
    elif source.format_id == "docx":
        rows, pages, empty_pages = _docx(source.path)
    elif source.format_id in {"text", "markdown"}:
        rows, pages, empty_pages = _text(source.path)
    else:
        raise ValueError("FORMAT_UNSUPPORTED")
    text = "\n\n".join(row["text"] for row in rows)
    if len(text) > max_characters:
        raise ValueError("DOCUMENT_LIMIT_EXCEEDED")
    table = pa.Table.from_pylist(rows, schema=BLOCK_SCHEMA)
    return {
        "text": write_markdown(text, output_dir / "text.md"),
        "blocks": write_table(table, output_dir / "blocks.parquet"),
        "summary": summary(
            {
                "pageCount": pages,
                "blockCount": len(rows),
                "characterCount": len(text),
                "emptyTextPages": empty_pages,
            }
        ),
    }
