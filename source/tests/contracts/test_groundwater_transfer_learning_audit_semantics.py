from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOMAIN = ROOT / "domains/groundwater/6.0.0"
OPERATORS = ROOT / "packages/operator-pack/catalog/operators"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _checksum(document: dict) -> str:
    canonical = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def test_transfer_model_preserves_58_node_25_output_contract_and_split_chain() -> None:
    model = _load(DOMAIN / "models/transfer-learning-analysis.model.json")
    workflow = _load(DOMAIN / "workflows/transfer-learning-analysis.workflow.json")
    nodes = {node["nodeId"]: node for node in workflow["nodes"]}

    assert model["version"] == "3.1.0"
    assert workflow["version"] == "1.0.0"
    assert model["workflowRef"] == {
        "workflowId": "groundwater.transfer-learning-analysis",
        "version": "1.0.0",
        "checksum": _checksum(workflow),
    }
    assert len(workflow["nodes"]) == 58
    assert len(model["finalOutputs"]) == 25
    assert model["finalOutputs"] == [output["name"] for output in workflow["outputs"]]

    assert nodes["source-scaler"]["inputs"]["data"]["nodeOutput"] == {
        "nodeId": "source-split",
        "port": "fitRows",
    }
    assert nodes["target-scaler"]["inputs"]["data"]["nodeOutput"] == {
        "nodeId": "target-split",
        "port": "fitRows",
    }
    assert nodes["target-scaled"]["inputs"]["data"]["nodeOutput"] == {
        "nodeId": "target-rows",
        "port": "matched",
    }
    assert nodes["holdout-windows"]["inputs"]["targets"]["nodeOutput"] == {
        "nodeId": "target-split",
        "port": "holdoutTargets",
    }
    assert nodes["predict"]["inputs"]["features"]["nodeOutput"] == {
        "nodeId": "holdout-windows",
        "port": "features",
    }
    assert nodes["inverse"]["inputs"]["scaler"]["nodeOutput"] == {
        "nodeId": "target-scaler",
        "port": "scaler",
    }
    assert nodes["evaluate"]["inputs"]["predictions"]["nodeOutput"] == {
        "nodeId": "inverse",
        "port": "data",
    }


def test_publications_explain_split_single_step_metrics_and_diagnostics() -> None:
    model = _load(DOMAIN / "models/transfer-learning-analysis.model.json")
    publications = model["outputPublications"]

    for name, publication in publications.items():
        assert publication["semantics"]["description"] != (
            f"groundwater.transfer-learning-analysis output {name}."
        )

    target_split = publications["target-split"]["semantics"]
    predictions = publications["predictions"]["semantics"]
    metrics = publications["metrics"]["semantics"]
    per_well = publications["per-well-metrics"]["semantics"]
    imagery = publications["imagery-coverage"]["semantics"]
    map_month = publications["map-month-diagnostics"]["semantics"]
    loss = publications["finetune-loss"]["semantics"]

    assert all(
        phrase in target_split["description"].lower()
        for phrase in ("chronological", "training", "holdout", "boundary")
    )
    assert (
        "training rows only"
        in publications["target-scaler"]["semantics"]["description"].lower()
    )
    assert all(
        phrase in predictions["description"].lower()
        for phrase in ("single-step", "holdout", "observed history")
    )
    assert any("recursive" in item.lower() for item in predictions["limitations"])
    metric_text = " ".join([metrics["description"], *metrics["limitations"]])
    for phrase in ("MAE", "RMSE", "R²", "persistence", "r2_reason"):
        assert phrase in metric_text
    assert "per well" in per_well["description"].lower()
    assert "baseline" in per_well["description"].lower()
    assert all(
        phrase in imagery["description"].lower()
        for phrase in ("first selected band", "mask", "minimum")
    )
    assert all(
        phrase in map_month["description"].lower()
        for phrase in ("target time", "selection", "selected time", "status")
    )
    assert any("not validation" in item.lower() for item in loss["limitations"])


def test_operator_manifests_publish_the_audit_fields() -> None:
    descriptions = {
        operator: _load(OPERATORS / operator / "1.0.0/manifest.json")["description"]
        for operator in (
            "timeseries.chronological_split",
            "statistics.regression_metrics",
            "raster.window",
            "timeseries.snapshot",
        )
    }

    for phrase in (
        "trainingStart",
        "holdoutStart",
        "fitScope",
        "evaluationMode",
    ):
        assert phrase in descriptions["timeseries.chronological_split"]
    for phrase in (
        "baseline_rmse",
        "baseline_r2_reason",
        "rmse_improvement_pct",
    ):
        assert phrase in descriptions["statistics.regression_metrics"]
    for phrase in ("selectedBands", "maskBand", "minimumRequiredFraction"):
        assert phrase in descriptions["raster.window"]
    for phrase in ("target_time", "selected_time", "selection", "status"):
        assert phrase in descriptions["timeseries.snapshot"]


def test_product_ui_names_the_backtest_boundary_and_key_audit_results() -> None:
    ui = _load(ROOT / "products/groundwater/ui/product-ui.json")
    presentation = ui["models"]["groundwater.transfer-learning-analysis"]
    combined = f"{presentation['description']} {presentation['applicability']}"

    for phrase in ("单步留出回测", "训练段", "观测历史", "递归未来预测"):
        assert phrase in combined
    assert ui["outputs"]["target-split"]["label"] == "目标域时间划分诊断"
    assert ui["outputs"]["metrics"]["label"] == "留出回测总体指标"
    assert ui["outputs"]["per-well-metrics"]["label"] == "逐井留出回测指标"
    assert ui["outputs"]["imagery-coverage"]["label"] == "影像窗口覆盖诊断"
    assert ui["outputs"]["map-month-diagnostics"]["label"] == "制图月份选择诊断"


def test_agent_guidance_tracks_current_identity_and_null_metric_reasons() -> None:
    instructions = (DOMAIN / "instructions.md").read_text(encoding="utf-8")
    cards = _load(DOMAIN / "knowledge/model-cards.v1.json")
    card = next(
        document
        for document in cards["documents"]
        if document["knowledgeId"] == "gw-model-card-transfer-learning-analysis"
    )
    card_text = " ".join(chunk["content"] for chunk in card["chunks"])

    for text in (instructions, card_text):
        assert "groundwater.transfer-learning-analysis@3.1.0" in text
        assert "groundwater.transfer-learning-analysis@1.0.0" in text
        for phrase in (
            "target-split",
            "metrics",
            "imagery-coverage",
            "map-month-diagnostics",
            "单步",
            "训练",
            "观测",
            "R²",
            "null",
        ):
            assert phrase in text
        assert "groundwater.transfer-learning-analysis@3.0.0" not in text
