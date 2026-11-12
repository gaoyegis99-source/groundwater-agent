from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.development.product_selection import load_selected_product_by_id


SOURCE = Path(__file__).resolve().parents[2]
DOMAIN = load_selected_product_by_id(SOURCE, "groundwater").domain.root
MODEL_PATH = DOMAIN / "models/monitoring-coverage.model.json"
WORKFLOW_PATH = DOMAIN / "workflows/monitoring-coverage.workflow.json"
UI_PATH = SOURCE / "products/groundwater/ui/product-ui.json"
INSTRUCTIONS_PATH = DOMAIN / "instructions.md"
MODEL_CARDS_PATH = DOMAIN / "knowledge/model-cards.v1.json"
DISSOLVE_PATH = (
    SOURCE
    / "packages/operator-pack/catalog/operators/vector.dissolve/1.0.0/manifest.json"
)
DISSOLVE_SMOKE_PATH = DISSOLVE_PATH.with_name("smoke.json")


def _documents() -> tuple[dict, dict, dict]:
    return (
        json.loads(MODEL_PATH.read_text()),
        json.loads(WORKFLOW_PATH.read_text()),
        json.loads(UI_PATH.read_text()),
    )


def test_monitoring_coverage_preserves_map_and_adds_a_diagnostic_summary() -> None:
    model, workflow, _ = _documents()

    assert model["version"] == "3.1.0"
    assert workflow["version"] == "1.2.0"
    assert model["workflowRef"]["version"] == workflow["version"]
    canonical = json.dumps(
        workflow, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    assert model["workflowRef"]["checksum"] == (
        "sha256:" + hashlib.sha256(canonical).hexdigest()
    )

    output_names = {item["name"] for item in workflow["outputs"]}
    assert output_names == {"coverage", "coverage-summary"}
    assert set(model["finalOutputs"]) == output_names
    assert set(model["outputPublications"]) == output_names
    assert next(item for item in workflow["outputs"] if item["name"] == "coverage")[
        "nodeOutput"
    ] == {"nodeId": "geojson", "port": "data"}
    assert next(
        item for item in workflow["outputs"] if item["name"] == "coverage-summary"
    )["nodeOutput"] == {"nodeId": "dissolve", "port": "summary"}


def test_monitoring_coverage_enables_non_disableable_point_preflight() -> None:
    model, workflow, _ = _documents()
    point_mode = model["tool"]["inputSchema"]["properties"]["pointCoverageMode"]

    assert point_mode["type"] == "boolean"
    assert point_mode["const"] is True
    assert model["defaultParameters"]["pointCoverageMode"] is True
    assert model["parameterBindings"]["pointCoverageMode"] == "pointCoverageMode"
    assert "pointCoverageMode" in workflow["parameters"]
    dissolve = next(node for node in workflow["nodes"] if node["nodeId"] == "dissolve")
    assert dissolve["parameters"]["pointCoverageMode"] == {
        "workflowParameter": "pointCoverageMode"
    }


def test_coverage_publications_explain_crs_metrics_duplicates_and_scope() -> None:
    model, _, ui = _documents()
    coverage = model["outputPublications"]["coverage"]["semantics"]
    summary = model["outputPublications"]["coverage-summary"]["semantics"]

    assert "convex hull" in coverage["description"].lower()
    assert coverage["crs"]
    combined = " ".join(
        [coverage["description"], summary["description"]]
        + coverage["limitations"]
        + summary["limitations"]
    ).lower()
    assert "duplicate" in combined
    assert "area" in combined and "perimeter" in combined
    assert "geodesic" in combined and "projected" in combined
    assert "aquifer boundary" in combined
    assert "influence area" in combined

    ui_model = ui["models"]["groundwater.monitoring-coverage"]
    ui_text = f"{ui_model['description']} {ui_model['applicability']}"
    assert "凸包" in ui_text
    assert "重复" in ui_text
    assert "含水层边界" in ui_text
    assert "影响范围" in ui_text
    assert ui["outputs"]["coverage-summary"]["viewer"] == "json"


def test_dissolve_contract_exposes_coverage_diagnostics_and_stable_errors() -> None:
    manifest = json.loads(DISSOLVE_PATH.read_text())
    smoke = json.loads(DISSOLVE_SMOKE_PATH.read_text())
    point_mode = manifest["parametersSchema"]["properties"]["pointCoverageMode"]
    summary = next(item for item in manifest["outputs"] if item["name"] == "summary")
    properties = set(summary["schema"]["properties"])

    assert point_mode == {"default": False, "type": "boolean"}
    assert {
        "inputFeatureCount",
        "inputPointCount",
        "uniquePointCount",
        "duplicatePointCount",
        "hullGeometryType",
        "crs",
        "crsKind",
        "measurementMethod",
        "areaSquareMeters",
        "perimeterMeters",
        "areaUnit",
        "perimeterUnit",
        "resultKind",
        "interpretation",
        "limitations",
    } <= properties
    assert {
        "POINT_GEOMETRY_REQUIRED",
        "INSUFFICIENT_UNIQUE_POINTS",
        "COLLINEAR_POINTS",
        "CRS_UNSUPPORTED",
    } <= set(manifest["errorCodes"])
    assert smoke["expectedOutputs"]["summary"] == {"kind": "value"}


def test_agent_guidance_tracks_the_installed_coverage_contract() -> None:
    instructions = INSTRUCTIONS_PATH.read_text()
    cards = json.loads(MODEL_CARDS_PATH.read_text())
    card = next(
        item
        for item in cards["documents"]
        if item["knowledgeId"] == "gw-model-card-monitoring-coverage"
    )
    guidance = (
        instructions + " " + " ".join(chunk["content"] for chunk in card["chunks"])
    )

    assert "groundwater.monitoring-coverage@3.1.0" in guidance
    assert "groundwater.monitoring-coverage@1.2.0" in guidance
    for phrase in (
        "重复",
        "面积",
        "周长",
        "coverage-summary",
        "含水层边界",
        "影响范围",
    ):
        assert phrase in guidance
