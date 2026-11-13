from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.development.product_selection import load_selected_product_by_id


ROOT = Path(__file__).resolve().parents[2]
DOMAIN = load_selected_product_by_id(ROOT, "groundwater").domain.root
OPERATOR = ROOT / "packages/operator-pack/catalog/operators/statistics.describe/1.0.0"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _checksum(document: dict) -> str:
    canonical = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def test_model_preserves_summary_and_adds_auditable_details() -> None:
    model = _load(DOMAIN / "models/summary.model.json")
    workflow = _load(DOMAIN / "workflows/summary.workflow.json")

    assert model["version"] == "3.1.0"
    assert workflow["version"] == "1.1.0"
    assert model["workflowRef"] == {
        "workflowId": "groundwater.summary",
        "version": "1.1.0",
        "checksum": _checksum(workflow),
    }
    assert model["finalOutputs"] == ["summary", "summary-details"]
    assert [output["name"] for output in workflow["outputs"]] == [
        "summary",
        "summary-details",
    ]
    assert workflow["outputs"][0]["nodeOutput"]["port"] == "summary"
    assert workflow["outputs"][1]["nodeOutput"]["port"] == "details"

    summary = model["outputPublications"]["summary"]["semantics"]
    details = model["outputPublications"]["summary-details"]["semantics"]
    assert "full staged input" in summary["description"].lower()
    assert all(
        term in details["description"].lower()
        for term in ("null", "non-finite", "group", "unit")
    )
    assert any("wider population" in item.lower() for item in details["limitations"])
    assert any("unit conversion" in item.lower() for item in details["limitations"])


def test_model_binds_numeric_field_group_and_explicit_unit_contracts() -> None:
    model = _load(DOMAIN / "models/summary.model.json")
    workflow = _load(DOMAIN / "workflows/summary.workflow.json")
    properties = model["tool"]["inputSchema"]["properties"]

    assert set(properties) == {"versionId", "axis", "column", "groupField", "unit"}
    assert model["defaultParameters"] == {"axis": "columns", "unit": ""}
    assert model["parameterBindings"] == {
        name: name for name in ("axis", "column", "groupField", "unit")
    }
    requirements = {item["parameter"]: item for item in model["fieldRequirements"]}
    assert requirements["column"]["allowedTypes"] == ["number"]
    assert requirements["groupField"]["allowedTypes"] == [
        "boolean",
        "date-time",
        "integer",
        "number",
        "string",
    ]
    assert model["semanticRequirements"] == [
        {
            "kind": "fieldUnit",
            "inputParameter": "versionId",
            "fieldParameter": "column",
            "unitParameter": "unit",
        }
    ]
    assert workflow["parameters"] == ["axis", "column", "groupField", "unit"]
    assert workflow["nodes"][0]["parameters"] == {
        name: {"workflowParameter": name}
        for name in ("axis", "column", "groupField", "unit")
    }


def test_operator_manifest_freezes_details_scope_and_failure_boundaries() -> None:
    manifest = _load(OPERATOR / "manifest.json")
    smoke = _load(OPERATOR / "smoke.json")
    outputs = {output["name"]: output for output in manifest["outputs"]}

    assert set(outputs) == {"summary", "details"}
    assert outputs["summary"]["kind"] == "value"
    details = outputs["details"]["schema"]
    assert set(details["required"]) == {
        "schemaVersion",
        "scope",
        "selection",
        "series",
        "conventions",
        "interpretation",
        "limitations",
    }
    assert {
        "GROUP_FIELD_UNSUPPORTED",
        "GROUP_LIMIT_EXCEEDED",
        "GROUP_VALUE_INVALID",
        "NO_FINITE_VALUES",
    } <= set(manifest["errorCodes"])
    parameters = manifest["parametersSchema"]["properties"]
    assert parameters["groupField"] == {"type": "string", "minLength": 1}
    assert parameters["unit"] == {"type": "string", "default": "", "maxLength": 64}
    assert smoke["expectedOutputs"] == {
        "summary": {"kind": "value"},
        "details": {"kind": "value"},
    }
    assert smoke["parameters"] == {
        "axis": "columns",
        "column": "value",
        "unit": "",
    }


def test_product_ui_explains_scope_fields_units_and_grouping() -> None:
    ui = _load(ROOT / "products/groundwater/ui/product-ui.json")
    presentation = ui["models"]["groundwater.summary"]

    assert all(
        phrase in presentation["description"]
        for phrase in ("完整输入文件", "空值", "非有限值")
    )
    assert all(
        phrase in presentation["applicability"]
        for phrase in ("表格", "NPY", "不代表总体")
    )
    assert set(presentation["parameterOverrides"]) == {"groupField", "unit"}
    assert set(ui["parameterNames"]["axis"]["values"]) == {
        "columns",
        "flatten",
    }
    assert presentation["confirmationSummary"] == [
        {"label": "统计字段", "parameters": ["column", "groupField", "unit"]},
        {"label": "数组统计范围", "parameters": ["axis"]},
    ]
    assert ui["outputs"]["summary"]["viewer"] == "json"
    assert ui["outputs"]["summary-details"] == {
        "label": "描述统计范围与语义",
        "viewer": "json",
    }


def test_agent_guidance_uses_current_identity_and_does_not_infer_semantics() -> None:
    instructions = (DOMAIN / "instructions.md").read_text(encoding="utf-8")
    cards = _load(DOMAIN / "knowledge/model-cards.v1.json")
    card = next(
        document
        for document in cards["documents"]
        if document["knowledgeId"] == "gw-model-card-summary"
    )
    card_text = " ".join(chunk["content"] for chunk in card["chunks"])

    for text in (instructions, card_text):
        assert "groundwater.summary@3.1.0" in text
        assert "groundwater.summary@1.1.0" in text
        assert "summary-details" in text
        assert "FULL_STAGED_INPUT" in text
        assert "groupField" in text
        assert "unit" in text
        assert "groundwater.summary@3.0.0" not in text
        assert "Workflow groundwater.summary@3.0.0" not in text
    assert "100" in card_text
    assert "不推断" in card_text
    assert "总体" in card_text
