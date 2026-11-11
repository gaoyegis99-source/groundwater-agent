from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from moperators.operators.native import StagedInput
from moperators.static_implementations import execute


def _run(
    source: Path,
    output: Path,
    *,
    format_id: str,
    metadata: dict | None = None,
    **parameters,
) -> dict:
    media_type = "application/x-npy" if format_id == "npy" else "text/csv"
    return execute(
        "statistics.describe",
        "1.0.0",
        {"data": [StagedInput(source, media_type, format_id, metadata or {})]},
        parameters,
        output,
    )


def _value(result: dict, name: str) -> dict:
    assert result[name]["kind"] == "value"
    return result[name]["value"]


def test_table_summary_counts_declared_nulls_nonfinite_values_and_groups(
    tmp_path: Path,
) -> None:
    source = tmp_path / "levels.csv"
    source.write_text(
        "well,head\nA,10\nA,NA\nA,nan\nB,14\nB,\nB,16\n",
        encoding="utf-8",
    )

    result = _run(
        source,
        tmp_path / "output",
        format_id="csv",
        metadata={"dataContract": {"nullValues": ["", "NA"]}},
        axis="columns",
        column="head",
        groupField="well",
        unit="m",
    )

    overall = _value(result, "summary")["head"]
    assert overall == {
        "rowCount": 6,
        "count": 3,
        "nullCount": 2,
        "nonFiniteCount": 1,
        "min": 10.0,
        "max": 16.0,
        "mean": pytest.approx(40 / 3),
        "stddev": pytest.approx(np.std([10.0, 14.0, 16.0])),
        "q1": 12.0,
        "median": 14.0,
        "q3": 15.0,
    }

    details = _value(result, "details")
    assert details["schemaVersion"] == "statistics-describe-details.v1"
    assert details["scope"] == {
        "basis": "FULL_STAGED_INPUT",
        "inputFormat": "csv",
        "sourceCount": 6,
        "sourceCountKind": "rows",
    }
    assert details["selection"] == {
        "axis": "columns",
        "column": "head",
        "groupField": "well",
        "unit": "m",
    }
    assert details["conventions"] == {
        "declaredNulls": "excluded-and-counted",
        "nonFiniteValues": "excluded-and-counted",
        "stddev": "population-ddof-0",
        "quantiles": "linear-interpolation",
    }
    assert details["series"][0]["field"] == {
        "kind": "table-column",
        "name": "head",
    }
    assert details["series"][0]["statistics"] == overall
    groups = details["series"][0]["groups"]
    assert [group["key"] for group in groups] == [
        {"type": "string", "value": "A"},
        {"type": "string", "value": "B"},
    ]
    assert groups[0]["statistics"]["rowCount"] == 3
    assert groups[0]["statistics"]["count"] == 1
    assert groups[0]["statistics"]["nullCount"] == 1
    assert groups[0]["statistics"]["nonFiniteCount"] == 1
    assert groups[1]["statistics"]["mean"] == 15.0
    assert groups[1]["statistics"]["stddev"] == 1.0
    assert "full staged input artifact" in details["interpretation"].lower()
    assert any("wider population" in item.lower() for item in details["limitations"])
    assert any("no unit conversion" in item.lower() for item in details["limitations"])


def test_npy_columns_expose_empty_nonfinite_series_without_poisoning_others(
    tmp_path: Path,
) -> None:
    source = tmp_path / "values.npy"
    np.save(source, np.asarray([[1.0, np.nan], [3.0, np.inf]], dtype=np.float64))

    result = _run(
        source,
        tmp_path / "output",
        format_id="npy",
        axis="columns",
        unit="m",
    )

    summary = _value(result, "summary")
    assert summary["0"]["count"] == 2
    assert summary["0"]["mean"] == 2.0
    assert summary["1"] == {
        "rowCount": 2,
        "count": 0,
        "nullCount": 0,
        "nonFiniteCount": 2,
        "min": None,
        "max": None,
        "mean": None,
        "stddev": None,
        "q1": None,
        "median": None,
        "q3": None,
    }
    details = _value(result, "details")
    assert details["scope"] == {
        "basis": "FULL_STAGED_INPUT",
        "inputFormat": "npy",
        "sourceCount": 4,
        "sourceCountKind": "elements",
    }
    assert [series["field"] for series in details["series"]] == [
        {"kind": "array-column", "name": "0"},
        {"kind": "array-column", "name": "1"},
    ]


def test_summary_rejects_grouping_for_arrays_and_all_nonfinite_inputs(
    tmp_path: Path,
) -> None:
    source = tmp_path / "values.npy"
    np.save(source, np.asarray([np.nan, np.inf], dtype=np.float64))

    with pytest.raises(ValueError, match=r"^GROUP_FIELD_UNSUPPORTED$"):
        _run(
            source,
            tmp_path / "group-output",
            format_id="npy",
            axis="flatten",
            groupField="well",
        )
    with pytest.raises(ValueError, match=r"^NO_FINITE_VALUES$"):
        _run(
            source,
            tmp_path / "finite-output",
            format_id="npy",
            axis="flatten",
        )


def test_grouped_summary_rejects_unbounded_cardinality(tmp_path: Path) -> None:
    source = tmp_path / "groups.csv"
    source.write_text(
        "group,value\n" + "".join(f"g{index},{index}\n" for index in range(101)),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"^GROUP_LIMIT_EXCEEDED$"):
        _run(
            source,
            tmp_path / "output",
            format_id="csv",
            column="value",
            groupField="group",
        )
