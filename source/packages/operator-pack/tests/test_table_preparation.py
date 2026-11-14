from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as parquet
import pytest

from moperators.operators import table_deduplicate, table_project
from moperators.operators.native import StagedInput


def _inputs(tmp_path: Path, table: pa.Table, rules: dict) -> dict[str, list[StagedInput]]:
    source = tmp_path / "source.parquet"
    rules_file = tmp_path / "rules.json"
    parquet.write_table(table, source)
    rules_file.write_text(json.dumps(rules), encoding="utf-8")
    return {
        "data": [StagedInput(source, "application/vnd.apache.parquet", "parquet", {})],
        "rules": [StagedInput(rules_file, "application/json", "json", {})],
    }


@pytest.mark.parametrize(("keep", "row"), [("first", 1), ("last", 2), ("error", None)])
def test_deduplication_honors_policy_for_compound_keys_with_null(tmp_path, keep, row):
    inputs = _inputs(
        tmp_path, pa.table({"site": ["001", "001", "002"], "period": [None] * 3, "row": [1, 2, 3]}),
        {"schemaVersion": "table-deduplicate-rules.v1", "keys": ["site", "period"]},
    )
    if row is None:
        with pytest.raises(ValueError, match=r"^DUPLICATE_KEY$"):
            table_deduplicate.run(inputs, {"keep": keep}, tmp_path)
        assert not (tmp_path / "unique.parquet").exists()
        return
    output = table_deduplicate.run(inputs, {"keep": keep}, tmp_path)
    assert parquet.read_table(output["unique"]["path"])["row"].to_pylist() == [row, 3]
    assert parquet.read_table(output["duplicates"]["path"])["row"].to_pylist() == [3 - row]


def test_project_reports_an_invalid_numeric_cast(tmp_path):
    inputs = _inputs(
        tmp_path, pa.table({"label": ["001", "invalid"]}),
        {
            "schemaVersion": "table-project-rules.v1",
            "columns": [{"source": "label", "target": "amount", "type": "int64"}],
        },
    )
    with pytest.raises(ValueError, match=r"^TYPE_CAST_FAILED$"):
        table_project.run(inputs, {}, tmp_path)
