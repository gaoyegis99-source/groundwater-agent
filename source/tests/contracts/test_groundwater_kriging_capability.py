from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOMAIN = ROOT / "domains/groundwater/6.0.0"
OPERATOR = (
    ROOT
    / "packages/operator-pack/catalog/operators"
    / "points.interpolate.kriging/1.0.0/manifest.json"
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _checksum(document: dict) -> str:
    canonical = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def test_kriging_operator_contract_has_five_auditable_outputs() -> None:
    operator = _load(OPERATOR)

    assert operator["id"] == "points.interpolate.kriging"
    assert operator["version"] == "1.0.0"
    assert operator["runtime"] == "native"
    assert [output["name"] for output in operator["outputs"]] == [
        "prediction",
        "variance",
        "variogramDiagnostics",
        "loo",
        "summary",
    ]
    parameters = operator["parametersSchema"]["properties"]
    assert parameters["krigingType"]["enum"] == ["ordinary", "universal"]
    assert parameters["variogramModel"]["enum"] == [
        "spherical",
        "exponential",
        "gaussian",
    ]
    for name in (
        "valueField",
        "xField",
        "yField",
        "sourceCrs",
        "targetCrs",
        "pixelSize",
        "neighborCount",
        "minimumNeighbors",
        "searchRadius",
        "exact",
    ):
        assert name in parameters
    assert set(operator["errorCodes"]) >= {
        "PROJECTED_CRS_REQUIRED",
        "DUPLICATE_COORDINATES",
        "DRIFT_RANK_DEFICIENT",
        "INSUFFICIENT_INTERPOLATION_POINTS",
        "VARIOGRAM_PARAMETERS_INCOMPLETE",
        "KRIGING_NUMERICAL_FAILURE",
    }


def test_groundwater_kriging_model_is_one_node_and_binds_exact_data() -> None:
    model = _load(DOMAIN / "models/kriging.model.json")
    workflow = _load(DOMAIN / "workflows/kriging.workflow.json")

    assert model["modelId"] == "groundwater.kriging"
    assert model["version"] == "1.0.0"
    assert model["workflowRef"] == {
        "workflowId": "groundwater.kriging",
        "version": "1.0.0",
        "checksum": _checksum(workflow),
    }
    assert model["tool"]["name"] == "run_kriging"
    assert model["inputBindings"] == {"points": "versionId"}
    assert model["finalOutputs"] == [
        "kriging-prediction",
        "kriging-variance",
        "variogram-diagnostics",
        "kriging-loo",
        "kriging-summary",
    ]
    assert len(workflow["nodes"]) == 1
    assert workflow["nodes"][0]["operatorId"] == "points.interpolate.kriging"
    assert [output["name"] for output in workflow["outputs"]] == model["finalOutputs"]
    requirements = model["semanticRequirements"]
    assert {
        "kind": "crs",
        "inputParameter": "versionId",
        "crsParameter": "sourceCrs",
    } in requirements
    assert {
        "kind": "crsParameter",
        "parameter": "targetCrs",
        "requirement": "projectedMetric",
    } in requirements
    fields = {item["parameter"] for item in model["fieldRequirements"]}
    assert fields == {"valueField", "xField", "yField"}


def test_publications_explain_variance_loo_extrapolation_and_idw_boundary() -> None:
    model = _load(DOMAIN / "models/kriging.model.json")
    publications = model["outputPublications"]

    prediction = publications["kriging-prediction"]["semantics"]
    variance = publications["kriging-variance"]["semantics"]
    diagnostics = publications["variogram-diagnostics"]["semantics"]
    loo = publications["kriging-loo"]["semantics"]
    summary = publications["kriging-summary"]["semantics"]
    assert "estimate" in prediction["description"].lower()
    assert any("extrapolation" in item.lower() for item in prediction["limitations"])
    assert "kriging variance" in variance["description"].lower()
    assert any(
        "confidence interval" in item.lower() for item in variance["limitations"]
    )
    assert all(
        phrase in diagnostics["description"].lower()
        for phrase in ("empirical", "model", "range", "sill", "nugget")
    )
    for phrase in ("leave-one-out", "mean error", "rmse", "standardized", "rmsse"):
        assert phrase in loo["description"].lower()
    combined = " ".join([summary["description"], *summary["limitations"]]).lower()
    assert "idw" in combined
    assert "not guaranteed" in combined
    assert "projected" in combined


def test_product_ui_exposes_method_variogram_neighbor_and_exact_controls() -> None:
    ui = _load(ROOT / "products/groundwater/ui/product-ui.json")
    presentation = ui["models"]["groundwater.kriging"]
    combined = f"{presentation['description']} {presentation['applicability']}"

    for phrase in ("克里金", "方差", "变异函数", "留一法", "外推"):
        assert phrase in combined
    for parameter in (
        "krigingType",
        "variogramModel",
        "neighborCount",
        "minimumNeighbors",
        "searchRadius",
        "exact",
    ):
        assert parameter in presentation["parameterOverrides"]
    assert ui["outputs"]["kriging-prediction"]["label"] == "克里金预测栅格"
    assert ui["outputs"]["kriging-variance"]["label"] == "克里金方差栅格"
    assert ui["outputs"]["variogram-diagnostics"]["label"] == "变异函数诊断"
    assert ui["outputs"]["kriging-loo"]["label"] == "留一法交叉验证"


def test_agent_guidance_and_model_card_keep_scientific_limits_visible() -> None:
    instructions = (DOMAIN / "instructions.md").read_text(encoding="utf-8")
    cards = _load(DOMAIN / "knowledge/model-cards.v1.json")
    card = next(
        document
        for document in cards["documents"]
        if document["knowledgeId"] == "gw-model-card-kriging"
    )
    card_text = " ".join(chunk["content"] for chunk in card["chunks"])

    for text in (instructions, card_text):
        for phrase in (
            "groundwater.kriging@1.0.0",
            "Ordinary Kriging",
            "Universal Kriging",
            "variogram",
            "kriging-prediction",
            "kriging-variance",
            "variogram-diagnostics",
            "kriging-loo",
            "ME",
            "RMSE",
            "RMSSE",
            "IDW",
            "不能保证",
            "外推",
        ):
            assert phrase in text
