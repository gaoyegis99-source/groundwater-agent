from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa

from moperators.operators.table_document_common_v1 import (
    one,
    read_table,
    require_fields,
    summary,
    write_table,
)


def run(
    inputs: dict[str, list[Any]], parameters: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    table = read_table(one(inputs, "blocks"))
    require_fields(table, ["block_id", "block_index", "text"])
    maximum, overlap, max_chunks = (
        parameters.get("maxCharacters"),
        parameters.get("overlapCharacters"),
        parameters.get("maxChunks"),
    )
    if (
        not isinstance(maximum, int)
        or maximum < 1
        or not isinstance(overlap, int)
        or overlap < 0
        or overlap >= maximum
        or not isinstance(max_chunks, int)
        or max_chunks < 1
    ):
        raise ValueError("PARAMETER_INVALID")
    rows = sorted(
        table.select(["block_id", "block_index", "text"]).to_pylist(),
        key=lambda item: item["block_index"],
    )
    text_parts: list[str] = []
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    for index, row in enumerate(rows):
        if index:
            text_parts.append("\n")
            cursor += 1
        text = row["text"] or ""
        start = cursor
        text_parts.append(text)
        cursor += len(text)
        spans.append((start, cursor, row["block_id"]))
    text = "".join(text_parts)
    step = maximum - overlap
    chunks: list[dict[str, Any]] = []
    for start in range(0, len(text), step):
        end = min(len(text), start + maximum)
        if len(chunks) >= max_chunks:
            raise ValueError("OUTPUT_ROW_LIMIT_EXCEEDED")
        touching = [span for span in spans if span[1] > start and span[0] < end]
        if not touching:
            continue
        chunks.append(
            {
                "chunk_id": f"chunk-{len(chunks) + 1:08d}",
                "chunk_index": len(chunks) + 1,
                "text": text[start:end],
                "start_block_id": touching[0][2],
                "end_block_id": touching[-1][2],
                "start_character_offset": start,
                "end_character_offset": end,
            }
        )
        if end == len(text):
            break
    schema = pa.schema(
        [
            ("chunk_id", pa.string()),
            ("chunk_index", pa.int64()),
            ("text", pa.string()),
            ("start_block_id", pa.string()),
            ("end_block_id", pa.string()),
            ("start_character_offset", pa.int64()),
            ("end_character_offset", pa.int64()),
        ]
    )
    result = pa.Table.from_pylist(chunks, schema=schema)
    return {
        "chunks": write_table(result, output_dir / "chunks.parquet"),
        "summary": summary({"chunkCount": len(chunks), "characterCount": len(text)}),
    }
