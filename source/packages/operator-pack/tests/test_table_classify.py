from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as parquet
import pytest

from moperators.operators.native import StagedInput
from moperators.static_implementations import execute


def _source(path: Path, text: str) -> StagedInput:
    path.write_text(text, encoding="utf-8")
    return StagedInput(
        path,
        "text/csv",
        "csv",
        {"dataContract": {"nullValues": ["", "NA"]}},
    )


def _rules_input(path: Path, value: dict) -> StagedInput:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return StagedInput(path, "application/json", "json", {})


def _classes() -> list[dict]:
    return [
        {"code": "I", "label": "Class I", "order": 1},
        {"code": "II", "label": "Class II", "order": 2},
        {"code": "III", "label": "Class III", "order": 3},
    ]


def _bound(
    minimum: float | None,
    maximum: float | None,
    *,
    minimum_inclusive: bool,
    maximum_inclusive: bool,
) -> dict:
    value = {
        "minimumInclusive": minimum_inclusive,
        "maximumInclusive": maximum_inclusive,
    }
    if minimum is not None:
        value["minimum"] = minimum
    if maximum is not None:
        value["maximum"] = maximum
    return value


def _indicator(
    name: str,
    unit: str,
    basis: str,
    classifications: list[dict],
    *,
    verification_status: str = "VERIFIED",
) -> dict:
    return {
        "indicator": name,
        "unit": unit,
        "basis": basis,
        "source": {
            "id": f"source-{name}",
            "title": f"Source for {name}",
            "version": "2026-01",
            "locator": f"table:{name}",
        },
        "verificationStatus": verification_status,
        "classifications": classifications,
    }


def _rules(indicators: list[dict]) -> dict:
    return {
        "schemaVersion": "table-classification-rules.v1",
        "ruleSetId": "water-quality-demo",
        "title": "Auditable water-quality demonstration rules",
        "version": "2026-11-10",
        "overallMethod": "worst-class-order",
        "classes": _classes(),
        "indicators": indicators,
    }


def _nitrate(*, verification_status: str = "VERIFIED") -> dict:
    return _indicator(
        "nitrate",
        "mg/L",
        "mass concentration as nitrate",
        [
            {
                "classCode": "I",
                "bounds": [_bound(0, 10, minimum_inclusive=True, maximum_inclusive=True)],
            },
            {
                "classCode": "II",
                "bounds": [_bound(10, 20, minimum_inclusive=False, maximum_inclusive=True)],
            },
            {
                "classCode": "III",
                "bounds": [_bound(20, 30, minimum_inclusive=False, maximum_inclusive=True)],
            },
        ],
        verification_status=verification_status,
    )


def _ph(*, verification_status: str = "VERIFIED") -> dict:
    return _indicator(
        "ph",
        "1",
        "field pH at 25 degC",
        [
            {
                "classCode": "I",
                "bounds": [_bound(6.5, 8.5, minimum_inclusive=True, maximum_inclusive=True)],
            },
            {
                "classCode": "III",
                "bounds": [
                    _bound(None, 6.5, minimum_inclusive=False, maximum_inclusive=False),
                    _bound(8.5, None, minimum_inclusive=False, maximum_inclusive=False),
                ],
            },
        ],
        verification_status=verification_status,
    )


def _bindings(*names: str) -> dict:
    available = {
        "nitrate": {
            "indicator": "nitrate",
            "field": "nitrate_mg_l",
            "unit": "mg/L",
            "basis": "mass concentration as nitrate",
        },
        "ph": {
            "indicator": "ph",
            "field": "ph",
            "unit": "1",
            "basis": "field pH at 25 degC",
        },
    }
    return {
        "schemaVersion": "table-classification-bindings.v1",
        "bindings": [available[name] for name in names],
    }


def _run(
    tmp_path: Path,
    csv_text: str,
    rules: dict,
    parameters: dict,
    *,
    name: str,
) -> dict:
    return execute(
        "table.classify",
        "1.0.0",
        {
            "data": [_source(tmp_path / f"{name}.csv", csv_text)],
            "rules": [_rules_input(tmp_path / f"{name}-rules.json", rules)],
            "bindings": [_rules_input(tmp_path / f"{name}-bindings.json", parameters)],
        },
        {},
        tmp_path / name,
    )


def _rows(output: dict) -> list[dict]:
    assert output["kind"] == "artifact"
    return parquet.read_table(output["path"]).to_pylist()


def _value(output: dict) -> dict:
    assert output["kind"] == "value"
    assert isinstance(output["value"], dict)
    return output["value"]


def test_classifies_open_closed_boundaries_and_two_sided_ph_rules(
    tmp_path: Path,
) -> None:
    outputs = _run(
        tmp_path,
        "sample,nitrate_mg_l,ph\nA,10,6.5\nB,10.01,6.4\nC,20,8.6\n",
        _rules([_nitrate(), _ph()]),
        _bindings("nitrate", "ph"),
        name="boundaries",
    )

    indicator_rows = _rows(outputs["indicatorClassifications"])
    assert [(row["row_number"], row["indicator"], row["class_code"]) for row in indicator_rows] == [
        (1, "nitrate", "I"),
        (1, "ph", "I"),
        (2, "nitrate", "II"),
        (2, "ph", "III"),
        (3, "nitrate", "II"),
        (3, "ph", "III"),
    ]
    classified = _rows(outputs["classified"])
    assert [row["overall_class_code"] for row in classified] == ["I", "III", "III"]
    assert [row["overall_class_order"] for row in classified] == [1, 3, 3]
    assert all(row["overall_status"] == "EVALUATED" for row in classified)
    assert all(row["overall_unevaluated_reason"] is None for row in classified)
    assert all(row["overall_unevaluated_details"] is None for row in classified)


def test_preserves_exact_integer_boundaries_beyond_float_precision(
    tmp_path: Path,
) -> None:
    boundary = 2**53
    nitrate = _nitrate()
    nitrate["classifications"] = [
        {
            "classCode": "I",
            "bounds": [_bound(0, boundary, minimum_inclusive=True, maximum_inclusive=True)],
        },
        {
            "classCode": "II",
            "bounds": [
                _bound(
                    boundary,
                    boundary + 2,
                    minimum_inclusive=False,
                    maximum_inclusive=True,
                )
            ],
        },
    ]
    outputs = _run(
        tmp_path,
        f"sample,nitrate_mg_l\nA,{boundary + 1}\n",
        _rules([nitrate]),
        _bindings("nitrate"),
        name="exact-large-integer",
    )

    indicator = _rows(outputs["indicatorClassifications"])[0]
    assert indicator["class_code"] == "II"
    first_bound = _rows(outputs["ruleSummary"])[0]
    assert first_bound["maximum_text"] == str(boundary)


def test_reports_missing_non_numeric_out_of_range_and_unverified_rules(
    tmp_path: Path,
) -> None:
    outputs = _run(
        tmp_path,
        "sample,nitrate_mg_l,ph\nA,,7\nB,31,7\nC,nope,7\n",
        _rules([_nitrate(), _ph(verification_status="UNVERIFIED")]),
        _bindings("nitrate", "ph"),
        name="unevaluated",
    )

    indicator_rows = _rows(outputs["indicatorClassifications"])
    nitrate = [row for row in indicator_rows if row["indicator"] == "nitrate"]
    ph = [row for row in indicator_rows if row["indicator"] == "ph"]
    assert [row["unevaluated_reason"] for row in nitrate] == [
        "MISSING_VALUE",
        "OUT_OF_RANGE",
        "NON_NUMERIC_VALUE",
    ]
    assert {row["unevaluated_reason"] for row in ph} == {"RULE_NOT_VERIFIED"}
    assert all(row["class_code"] is None for row in indicator_rows)

    classified = _rows(outputs["classified"])
    assert {row["overall_status"] for row in classified} == {"NOT_EVALUATED"}
    assert {row["overall_unevaluated_reason"] for row in classified} == {
        "REQUIRED_INDICATOR_NOT_EVALUATED"
    }
    first_details = json.loads(classified[0]["overall_unevaluated_details"])
    assert first_details == [
        {"indicator": "nitrate", "reason": "MISSING_VALUE"},
        {"indicator": "ph", "reason": "RULE_NOT_VERIFIED"},
    ]

    summary = _value(outputs["summary"])
    assert summary["rowCount"] == 3
    assert summary["evaluatedRowCount"] == 0
    assert summary["unevaluatedRowCount"] == 3
    assert summary["indicatorCounts"]["nitrate"]["unevaluatedReasons"] == {
        "MISSING_VALUE": 1,
        "NON_NUMERIC_VALUE": 1,
        "OUT_OF_RANGE": 1,
    }
    assert summary["indicatorCounts"]["ph"]["unevaluatedReasons"] == {"RULE_NOT_VERIFIED": 3}


def test_open_open_internal_boundary_is_explicitly_not_evaluated(tmp_path: Path) -> None:
    nitrate = _nitrate()
    nitrate["classifications"][0]["bounds"][0]["maximumInclusive"] = False
    outputs = _run(
        tmp_path,
        "sample,nitrate_mg_l\nA,10\n",
        _rules([nitrate]),
        _bindings("nitrate"),
        name="gap",
    )

    indicator = _rows(outputs["indicatorClassifications"])[0]
    assert indicator["status"] == "NOT_EVALUATED"
    assert indicator["unevaluated_reason"] == "NO_MATCHING_CLASS"
    classified = _rows(outputs["classified"])[0]
    assert classified["overall_class_code"] is None
    assert classified["overall_unevaluated_reason"] == "REQUIRED_INDICATOR_NOT_EVALUATED"


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("unit", "mg/kg", "UNIT_MISMATCH"),
        ("basis", "mass concentration as nitrogen", "BASIS_MISMATCH"),
    ],
)
def test_rejects_unit_or_basis_mismatch(
    tmp_path: Path,
    field: str,
    value: str,
    error: str,
) -> None:
    parameters = _bindings("nitrate")
    parameters["bindings"][0][field] = value
    with pytest.raises(ValueError, match=f"^{error}$"):
        _run(
            tmp_path,
            "sample,nitrate_mg_l\nA,5\n",
            _rules([_nitrate()]),
            parameters,
            name=error.lower(),
        )


def test_rejects_overlapping_class_bounds_including_equal_closed_boundary(
    tmp_path: Path,
) -> None:
    nitrate = _nitrate()
    nitrate["classifications"][1]["bounds"][0]["minimumInclusive"] = True
    with pytest.raises(ValueError, match=r"^RULESET_INVALID$"):
        _run(
            tmp_path,
            "sample,nitrate_mg_l\nA,10\n",
            _rules([nitrate]),
            _bindings("nitrate"),
            name="overlap",
        )


def test_rule_summary_preserves_sources_bounds_and_verification_status(
    tmp_path: Path,
) -> None:
    outputs = _run(
        tmp_path,
        "sample,ph\nA,7\n",
        _rules([_ph()]),
        _bindings("ph"),
        name="provenance",
    )

    assert set(outputs) == {
        "classified",
        "indicatorClassifications",
        "ruleSummary",
        "summary",
    }
    rule_rows = _rows(outputs["ruleSummary"])
    assert len(rule_rows) == 3
    assert {row["source_id"] for row in rule_rows} == {"source-ph"}
    assert {row["source_locator"] for row in rule_rows} == {"table:ph"}
    assert {row["verification_status"] for row in rule_rows} == {"VERIFIED"}
    assert sum(row["class_code"] == "III" for row in rule_rows) == 2
    assert any(row["minimum"] is None for row in rule_rows)
    assert any(row["maximum"] is None for row in rule_rows)

    summary = _value(outputs["summary"])
    assert summary["schemaVersion"] == "table-classification-summary.v1"
    assert summary["scope"] == {
        "basis": "FULL_STAGED_INPUT",
        "inputFormat": "csv",
    }
    assert summary["ruleSet"] == {
        "id": "water-quality-demo",
        "title": "Auditable water-quality demonstration rules",
        "version": "2026-11-10",
        "overallMethod": "worst-class-order",
    }
    assert summary["conventions"]["unverifiedRules"] == "not-evaluated"
    assert summary["conventions"]["units"] == "exact-match-no-conversion"
    assert any("not scientific validation" in item.lower() for item in summary["limitations"])
