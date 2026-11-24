from __future__ import annotations

import json
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as parquet
import pytest


def _describe(tmp_path, table, *, infer_numeric_strings=False, null_tokens=()):
    from moperators.operators.statistics_describe_columns import run

    path = tmp_path / "observations.parquet"
    parquet.write_table(table, path)
    source = SimpleNamespace(
        path=path,
        format_id="parquet",
        metadata={"dataContract": {"nullValues": list(null_tokens)}},
    )
    outputs = run(
        {"data": [source]}, {"inferNumericStrings": infer_numeric_strings}, tmp_path,
    )
    assert set(outputs) == {"summary"}
    assert outputs["summary"]["kind"] == "value"
    return outputs["summary"]["value"]


def test_complete_summary_for_physical_numeric_and_inferred_csv_columns(tmp_path):
    result = _describe(tmp_path, pa.table({
        "head": [10.0, None, float("inf"), 16.0],
        "change": ["-1", "NA", "nan", "3"],
        "empty": pa.array([None, None, None, None], type=pa.float64()),
        "well": ["001", "002", "003", "004"],
        "mixed": ["1", "review", "2", "3"],
        "malformed": ["1", "++inf", "2", "3"],
        "flag": [True, True, False, True],
    }), infer_numeric_strings=True, null_tokens=("NA",))
    assert result["schemaVersion"] == "statistics-describe-columns.v1"
    assert result["scope"] == {
        "basis": "FULL_STAGED_INPUT", "rowCount": 4, "inputFormat": "parquet",
    }
    assert set(result["columns"]) == {"head", "change", "empty"}
    assert result["columns"]["head"] == {
        "numericBasis": "PHYSICAL_TYPE",
        "statistics": {
            "rowCount": 4, "count": 2, "nullCount": 1, "nonFiniteCount": 1,
            "min": 10.0, "max": 16.0, "mean": 13.0, "stddev": 3.0,
            "q1": 11.5, "median": 13.0, "q3": 14.5,
        },
    }
    assert result["columns"]["change"] == {
        "numericBasis": "INFERRED_NUMERIC_TEXT",
        "statistics": {
            "rowCount": 4, "count": 2, "nullCount": 1, "nonFiniteCount": 1,
            "min": -1.0, "max": 3.0, "mean": 1.0, "stddev": 2.0,
            "q1": 0.0, "median": 1.0, "q3": 2.0,
        },
    }
    assert result["columns"]["empty"]["statistics"] == {
        "rowCount": 4, "count": 0, "nullCount": 4, "nonFiniteCount": 0,
        "min": None, "max": None, "mean": None, "stddev": None,
        "q1": None, "median": None, "q3": None,
    }
    assert set(result["excludedColumns"]) == {"well", "mixed", "malformed", "flag"}


def test_string_inference_requires_an_explicit_choice(tmp_path):
    table = pa.table({"well": ["07", "08"], "value": ["8", "12"]})
    with pytest.raises(ValueError, match=r"^NO_NUMERIC_COLUMNS$"):
        _describe(tmp_path, table)
    assert set(_describe(tmp_path, table, infer_numeric_strings=True)["columns"]) == {"value"}


def test_reports_overflow_instead_of_returning_nonfinite_metrics(tmp_path):
    with pytest.raises(ValueError, match=r"^NUMERIC_RANGE_OVERFLOW$"):
        _describe(tmp_path, pa.table({"head": [1e308, 1e308]}))


def test_worker_entrypoint_sorts_then_describes_every_numeric_column(tmp_path):
    from moperators.runtime.entrypoint import run_payload

    fields = [f"measurement_{index}" for index in range(30)]
    observations = tmp_path / "observations.csv"
    observations.write_text(
        "well," + ",".join(fields) + "\n"
        + "\n".join(
            f"W{4 - row}," + ",".join(str(index + row) for index in range(len(fields)))
            for row in range(4)
        ) + "\n",
        encoding="utf-8",
    )
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({
        "schemaVersion": "table-sort-rules.v1",
        "keys": [{"field": "well", "direction": "asc", "nulls": "last"}],
    }), encoding="utf-8")
    sorted_dir = tmp_path / "sorted"
    sorted_dir.mkdir()
    result = run_payload({
        "operatorId": "table.sort",
        "operatorVersion": "1.0.0",
        "inputs": {
            "data": [{"path": str(observations), "mediaType": "text/csv", "format": "csv"}],
            "rules": [{"path": str(rules), "mediaType": "application/json", "format": "json"}],
        },
        "parameters": {},
        "outputDirectory": str(sorted_dir),
    })
    staged = result["outputs"]["data"]
    assert parquet.read_table(staged["path"], columns=["well"])["well"].to_pylist() == [
        "W1", "W2", "W3", "W4",
    ]
    outcome = run_payload({
        "operatorId": "statistics.describe_columns",
        "operatorVersion": "1.0.0",
        "inputs": {"data": [{
            "path": staged["path"],
            "mediaType": "application/vnd.apache.parquet",
            "format": "parquet",
        }]},
        "parameters": {"inferNumericStrings": True},
        "outputDirectory": str(tmp_path / "statistics"),
    })
    assert set(outcome["outputs"]) == {"summary"}
    value = outcome["outputs"]["summary"]["value"]
    assert list(value["columns"]) == fields
    for index, name in enumerate(fields):
        assert value["columns"][name]["statistics"] == {
            "rowCount": 4, "count": 4, "nullCount": 0, "nonFiniteCount": 0,
            "min": index, "max": index + 3.0, "mean": index + 1.5,
            "stddev": pytest.approx(1.118033988749895),
            "q1": index + 0.75, "median": index + 1.5, "q3": index + 2.25,
        }
