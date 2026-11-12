from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from scripts.development.product_selection import load_selected_product_by_id


SOURCE = Path(__file__).resolve().parents[2]
DOMAIN = load_selected_product_by_id(SOURCE, "groundwater").domain.root
MODEL_PATH = DOMAIN / "models/monitoring-trend.model.json"
WORKFLOW_PATH = DOMAIN / "workflows/monitoring-trend.workflow.json"

LEGACY_OUTPUTS = {
    "aggregated-series",
    "aggregate-summary",
    "completeness-profiles",
    "completeness-summary",
    "linear-trends",
    "linear-summary",
    "mann-kendall-trends",
    "mann-kendall-summary",
    "outlier-observations",
    "outlier-summary",
    "period-changes",
    "period-change-summary",
}

EXPANDED_OUTPUTS = {
    "change-points",
    "change-point-summary",
    "seasonal-trends",
    "seasonal-trend-summary",
    "gap-runs",
    "gap-run-summary",
    "threshold-classification",
    "threshold-events",
    "threshold-event-summary",
}

LEGACY_ARGUMENTS = {
    "versionId": "version-1",
    "groupField": "well",
    "timeField": "time",
    "valueField": "head",
    "frequency": "month",
    "expectedStart": "2024-01-01",
    "expectedEnd": "2025-12-31",
    "baselineStart": "2024-01-01",
    "baselineEnd": "2024-12-31",
    "comparisonStart": "2025-01-01",
    "comparisonEnd": "2025-12-31",
    "aggregation": "mean",
}


def _documents() -> tuple[dict, dict]:
    return json.loads(MODEL_PATH.read_text()), json.loads(WORKFLOW_PATH.read_text())


def test_monitoring_trend_adds_four_nodes_without_breaking_legacy_contract() -> None:
    model, workflow = _documents()
    schema = model["tool"]["inputSchema"]
    validator = Draft202012Validator(schema)

    validator.validate(LEGACY_ARGUMENTS)
    output_names = {item["name"] for item in workflow["outputs"]}
    assert LEGACY_OUTPUTS <= output_names
    assert output_names == LEGACY_OUTPUTS | EXPANDED_OUTPUTS
    assert set(model["finalOutputs"]) == output_names
    assert set(model["outputPublications"]) == output_names

    operators = {node["nodeId"]: node["operatorId"] for node in workflow["nodes"]}
    assert operators | {
        "change-point": "statistics.pettitt_change_point",
        "seasonal-trend": "statistics.seasonal_mann_kendall",
        "gap-runs": "timeseries.gap_runs",
        "threshold-events": "timeseries.threshold_events",
    } == operators
    assert len(workflow["nodes"]) == 10


def test_optional_scientific_branches_require_explicit_decisions() -> None:
    model, _ = _documents()
    schema = model["tool"]["inputSchema"]
    validator = Draft202012Validator(schema)
    defaults = model["defaultParameters"]

    assert defaults["seasonalTrendEnabled"] is False
    assert defaults["thresholdEventsEnabled"] is False
    assert "seasonFrequency" not in defaults
    assert "eventThreshold" not in defaults
    assert "eventComparison" not in defaults

    seasonal = {**LEGACY_ARGUMENTS, "seasonalTrendEnabled": True}
    with pytest.raises(Exception):
        validator.validate(seasonal)
    validator.validate({**seasonal, "seasonFrequency": "month"})

    threshold = {**LEGACY_ARGUMENTS, "thresholdEventsEnabled": True}
    with pytest.raises(Exception):
        validator.validate(threshold)
    validator.validate({**threshold, "eventThreshold": 12.5, "eventComparison": "below"})


def test_expanded_outputs_describe_interpretation_limits() -> None:
    model, _ = _documents()

    for name in EXPANDED_OUTPUTS:
        semantics = model["outputPublications"][name]["semantics"]
        assert semantics["description"]
        assert semantics["limitations"]
    assert "contiguous" in model["outputPublications"]["gap-runs"]["semantics"][
        "description"
    ].lower()
    assert "overall completeness" in model["outputPublications"]["gap-runs"][
        "semantics"
    ]["limitations"][0].lower()
