from __future__ import annotations

from decimal import Decimal

import pyarrow as pa
import pyarrow.ipc as ipc
import pyarrow.parquet as parquet
import pytest

from moperators.operators.native import StagedInput, _table_convert


@pytest.mark.parametrize("row_count", [0, 70000])
def test_columnar_conversion_preserves_schema_and_nulls(tmp_path, row_count: int) -> None:
    schema = pa.schema(
        [
            pa.field("integer", pa.int64(), metadata={b"unit": b"count"}),
            pa.field("double", pa.float64()),
            pa.field("decimal", pa.decimal128(12, 2)),
            pa.field("time", pa.timestamp("us", tz="UTC")),
            pa.field("text", pa.string()),
        ],
        metadata={b"origin": b"schema-test"},
    )
    data = [
        [index if index % 2 else None for index in range(row_count)],
        [float(index) if index % 2 else None for index in range(row_count)],
        [Decimal("1.25") if index % 2 else None for index in range(row_count)],
        [index * 1000 if index % 2 else None for index in range(row_count)],
        ["" if index % 2 else None for index in range(row_count)],
    ]
    table = pa.Table.from_arrays(
        [pa.array(values, type=field.type) for values, field in zip(data, schema, strict=True)],
        schema=schema,
    )
    original = tmp_path / "original.parquet"
    parquet.write_table(table, original, row_group_size=32768)
    arrow = tmp_path / "converted.arrow"
    _table_convert(StagedInput(original, "application/vnd.apache.parquet", "parquet", {}),
                   "arrow-ipc-file", arrow)
    with ipc.open_file(arrow) as reader:
        converted = reader.read_all()
    assert converted.schema == schema
    assert converted.equals(table)

    round_trip = tmp_path / "round-trip.parquet"
    _table_convert(StagedInput(arrow, "application/vnd.apache.arrow.file", "arrow-ipc-file", {}),
                   "parquet", round_trip)
    assert parquet.read_table(round_trip).equals(table)


def test_columnar_to_json_preserves_native_json_values_or_rejects_loss(tmp_path) -> None:
    source = tmp_path / "input.parquet"
    parquet.write_table(pa.table({"number": [1, None], "flag": [True, False]}), source)
    output = tmp_path / "output.json"
    _table_convert(StagedInput(source, "application/vnd.apache.parquet", "parquet", {}),
                   "json", output)
    assert output.read_text() == '[{"flag":true,"number":1},{"flag":false,"number":null}]'

    parquet.write_table(pa.table({"decimal": pa.array([Decimal("1.25")])}), source)
    with pytest.raises(ValueError, match="CONVERSION_NOT_ALLOWED"):
        _table_convert(StagedInput(source, "application/vnd.apache.parquet", "parquet", {}),
                       "json", output)

    parquet.write_table(pa.table({"large_integer": [1 << 53]}), source)
    with pytest.raises(ValueError, match="CONVERSION_NOT_ALLOWED"):
        _table_convert(StagedInput(source, "application/vnd.apache.parquet", "parquet", {}),
                       "json", output)

    parquet.write_table(pa.table({"nested": [[1, None, 3]]}), source)
    _table_convert(StagedInput(source, "application/vnd.apache.parquet", "parquet", {}),
                   "json", output)
    assert output.read_text() == '[{"nested":[1,null,3]}]'


def test_columnar_to_json_rejects_duplicate_field_names(tmp_path) -> None:
    source = tmp_path / "duplicate.arrow"
    batch = pa.record_batch([pa.array([1]), pa.array([2])], names=["x", "x"])
    with ipc.new_file(source, batch.schema) as writer:
        writer.write_batch(batch)
    with pytest.raises(ValueError, match="CONVERSION_NOT_ALLOWED"):
        _table_convert(
            StagedInput(source, "application/vnd.apache.arrow.file", "arrow-ipc-file", {}),
            "json", tmp_path / "output.json",
        )
