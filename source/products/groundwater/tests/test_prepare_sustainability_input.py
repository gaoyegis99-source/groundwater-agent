from __future__ import annotations

import json
from pathlib import Path

from moperators.operators.sustainability_preparation import (
    MANAGEMENT,
    SIMULATION_FIELDS,
    TEXT_FIELDS,
    UNITS,
    prepare,
)


def _example() -> tuple[dict, dict]:
    root = Path(__file__).resolve().parents[4]
    payload = json.loads((
        root / "third-party-services/groundwater-sustainability-service"
        / "examples/sustainability_task_request.json"
    ).read_bytes())
    scope = {
        "waterSource": payload["context"]["water_source"],
        "startDate": "2024-01-01",
        "endDate": payload["context"]["evaluation_time"],
    }
    documents = {
        role: {"scope": dict(scope), "synthetic": True, "fields": {}}
        for role in ("simulation", "monitoring", "management")
    }
    for field, value in payload.items():
        if field not in UNITS and field not in TEXT_FIELDS:
            continue
        if field in MANAGEMENT:
            role = "management"
        elif field in SIMULATION_FIELDS:
            role = "simulation"
        else:
            role = "monitoring"
        documents[role]["fields"][field] = {
            "value": value, "unit": UNITS.get(field), "basis": "合成用例", "source": "example",
        }
    return payload, documents


def test_prepare_preserves_the_source_role_and_assessment_payload() -> None:
    payload, documents = _example()
    candidate, report = prepare(documents)
    assert candidate == {
        field: value for field, value in payload.items()
        if field not in {"client_request_id", "evidence_note"}
    }
    assert report["status"] == "REVIEW_REQUIRED"
    assert report["fields"]["recharge_r_m3_a"]["role"] == "simulation"
    assert report["fields"]["class_iii_count_nd"]["role"] == "monitoring"
    assert report["fields"]["prohibited_area"]["role"] == "management"


def test_prepare_blocks_scope_mismatch_and_simulated_monitoring_counts() -> None:
    _, documents = _example()
    documents["monitoring"]["scope"]["endDate"] = "2024-12-31"
    documents["simulation"]["fields"]["class_iii_count_nd"] = {
        "value": 1, "unit": "count", "basis": "模拟", "source": "example",
    }
    candidate, report = prepare(documents)
    assert candidate is None
    assert report["status"] == "BLOCKED"
    assert {"SCOPE_MISMATCH", "SIMULATION_FIELD_NOT_ALLOWED", "FIELD_MISSING"} <= {
        issue["code"] for issue in report["issues"]
    }


def test_prepare_blocks_duplicate_indicator_sources() -> None:
    _, documents = _example()
    field = "recharge_r_m3_a"
    documents["monitoring"]["fields"][field] = documents["simulation"]["fields"][field]
    candidate, report = prepare(documents)
    assert candidate is None
    assert {"role": "monitoring", "field": field, "code": "MULTIPLE_SOURCES_FOR_FIELD"} in (
        report["issues"]
    )
