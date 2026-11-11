from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "platform" / "assembler"))
from assembler import compile_product  # noqa: E402

OPERATOR = (
    ROOT / "packages/operator-pack/catalog/operators/table.classify/1.0.0/manifest.json"
)
DOMAIN = ROOT / "domains/groundwater/6.0.0"
MODEL = DOMAIN / "models/rule-classification.model.json"
WORKFLOW = DOMAIN / "workflows/rule-classification.workflow.json"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_table_classify_public_contract_is_a_rule_artifact_not_hidden_parameters() -> (
    None
):
    manifest = _json(OPERATOR)

    assert manifest["id"] == "table.classify"
    assert manifest["version"] == "1.0.0"
    assert manifest["runtime"] == "native"
    assert [(item["name"], item["formats"]) for item in manifest["inputs"]] == [
        ("data", ["csv", "parquet", "arrow-ipc-file"]),
        ("rules", ["json"]),
        ("bindings", ["json"]),
    ]
    assert [item["name"] for item in manifest["outputs"]] == [
        "classified",
        "indicatorClassifications",
        "ruleSummary",
        "summary",
    ]
    assert manifest["parametersSchema"] == {
        "additionalProperties": False,
        "properties": {},
        "type": "object",
    }
    assert "RULESET_INVALID" in manifest["errorCodes"]
    assert "UNIT_MISMATCH" in manifest["errorCodes"]
    assert "BASIS_MISMATCH" in manifest["errorCodes"]
    assert "verification" in manifest["description"].lower()
    assert "open" in manifest["description"].lower()
    assert "multi-interval" in manifest["description"].lower()


def test_table_classify_is_frozen_in_catalog_runtime_and_product_authoring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _json(ROOT / "packages/operator-pack/catalog/index.json")
    lock = _json(ROOT / "packages/operator-pack/catalog/implementation-lock.json")
    source = (
        ROOT / "packages/operator-pack/src/moperators/static_implementations.py"
    ).read_text(encoding="utf-8")

    assert any(item["id"] == "table.classify" for item in index["operators"])
    locked = lock["operators"]["table.classify@1.0.0"]
    assert "src/moperators/operators/table_classify.py" in locked["sourceFiles"]
    assert '("table.classify", "1.0.0"): _native' in source
    monkeypatch.setenv("PLATFORM_GENERATED_ROOT", str(tmp_path / ".generated"))
    output = compile_product(
        ROOT,
        ROOT / "products/groundwater/product-manifest.json",
    )
    kernel = _json(output / "kernel-config.json")
    executor = _json(output / "executor-config.json")
    authoring = kernel["workflowAuthoring"]
    assert len(authoring["operators"]) == 109
    assert "table.classify@1.0.0" in authoring["enabledOperators"]
    assert authoring["operatorCatalogDigest"] == executor["operatorCatalogDigest"]
    compiled = next(
        item for item in authoring["operators"] if item["id"] == "table.classify"
    )
    assert compiled["implementationRevision"] == locked["implementationRevision"]


def test_rule_classification_model_binds_three_exact_artifacts_and_four_outputs() -> (
    None
):
    manifest = _json(DOMAIN / "domain-manifest.json")
    workflow = _json(WORKFLOW)
    model = _json(MODEL)

    assert (
        "workflows/rule-classification.workflow.json" in manifest["workflowDefinitions"]
    )
    assert "models/rule-classification.model.json" in manifest["executableModels"]
    assert workflow["workflowId"] == "groundwater.rule-classification"
    assert workflow["version"] == "1.0.0"
    assert [item["name"] for item in workflow["inputs"]] == [
        "data",
        "rules",
        "bindings",
    ]
    assert workflow["parameters"] == []
    assert workflow["nodes"] == [
        {
            "nodeId": "classify",
            "operatorId": "table.classify",
            "operatorVersion": "1.0.0",
            "inputs": {
                "data": {"workflowInput": "data"},
                "rules": {"workflowInput": "rules"},
                "bindings": {"workflowInput": "bindings"},
            },
            "parameters": {},
        }
    ]
    assert [item["name"] for item in workflow["outputs"]] == [
        "classified-table",
        "indicator-classifications",
        "classification-rule-summary",
        "classification-summary",
    ]

    assert model["modelId"] == "groundwater.rule-classification"
    assert model["version"] == "1.0.0"
    assert model["tool"]["name"] == "run_rule_classification"
    schema = model["tool"]["inputSchema"]
    assert schema["required"] == [
        "dataVersionId",
        "rulesVersionId",
        "bindingsVersionId",
    ]
    assert model["inputBindings"] == {
        "data": "dataVersionId",
        "rules": "rulesVersionId",
        "bindings": "bindingsVersionId",
    }
    assert model["defaultParameters"] == {}
    assert model["parameterBindings"] == {}
    assert model["finalOutputs"] == [
        "classified-table",
        "indicator-classifications",
        "classification-rule-summary",
        "classification-summary",
    ]
    publications = model["outputPublications"]
    assert set(publications) == set(model["finalOutputs"])
    summary_text = json.dumps(publications, ensure_ascii=False).lower()
    assert "not evaluated" in summary_text
    assert "verification" in summary_text
    assert "no unit conversion" in summary_text
    assert "not scientific validation" in summary_text


def test_rule_classification_is_explained_in_agent_ui_and_model_card() -> None:
    ui = _json(ROOT / "products/groundwater/ui/product-ui.json")
    instructions = (DOMAIN / "instructions.md").read_text(encoding="utf-8")
    cards = _json(DOMAIN / "knowledge/model-cards.v1.json")

    model_ui = ui["models"]["groundwater.rule-classification"]
    assert list(model_ui["inputs"]) == [
        "dataVersionId",
        "rulesVersionId",
        "bindingsVersionId",
    ]
    assert ui["outputs"]["classified-table"]["viewer"] == "table"
    assert ui["outputs"]["indicator-classifications"]["viewer"] == "table"
    assert ui["outputs"]["classification-rule-summary"]["viewer"] == "table"
    assert ui["outputs"]["classification-summary"]["viewer"] == "json"
    assert "groundwater.rule-classification@1.0.0" in instructions
    assert "不得编造规则" in instructions
    assert "未核验" in instructions
    assert "开闭区间" in instructions
    assert "pH" in instructions

    card = next(
        item
        for item in cards["documents"]
        if item["knowledgeId"] == "gw-model-card-rule-classification"
    )
    assert len(card["chunks"]) == 3
    card_text = json.dumps(card, ensure_ascii=False).lower()
    assert "worst-class-order" in card_text
    assert "multi-interval" in card_text
    assert "not evaluated" in card_text
