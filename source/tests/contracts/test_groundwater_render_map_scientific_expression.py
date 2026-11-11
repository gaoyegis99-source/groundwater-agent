from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOMAIN = ROOT / "domains/groundwater/6.0.0"
OPERATOR = (
    ROOT / "packages/operator-pack/catalog/operators/cartography.render-png/1.0.0"
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _checksum(document: dict) -> str:
    canonical = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def test_model_preserves_image_and_publishes_scientific_summary() -> None:
    model = _load(DOMAIN / "models/render-map.model.json")
    workflow = _load(DOMAIN / "workflows/render-map.workflow.json")

    assert model["version"] == "3.1.0"
    assert workflow["version"] == "1.1.0"
    assert model["workflowRef"] == {
        "workflowId": "groundwater.render-map",
        "version": "1.1.0",
        "checksum": _checksum(workflow),
    }
    assert model["finalOutputs"] == ["map-image", "map-summary"]
    assert [output["name"] for output in workflow["outputs"]] == [
        "map-image",
        "map-summary",
    ]
    assert workflow["outputs"][0]["nodeOutput"]["port"] == "image"
    assert workflow["outputs"][1]["nodeOutput"]["port"] == "summary"

    image = model["outputPublications"]["map-image"]["semantics"]
    summary = model["outputPublications"]["map-summary"]["semantics"]
    assert "single" in image["description"].lower()
    assert "source CRS" in image["description"]
    assert "extent" in summary["description"].lower()
    assert "nodata" in summary["description"].lower()
    assert any("reprojection" in item.lower() for item in summary["limitations"])
    assert any("basemap" in item.lower() for item in summary["limitations"])


def test_model_binds_explicit_static_map_expression_parameters() -> None:
    model = _load(DOMAIN / "models/render-map.model.json")
    workflow = _load(DOMAIN / "workflows/render-map.workflow.json")
    schema = model["tool"]["inputSchema"]
    properties = schema["properties"]
    expected = {
        "sourceVersionId",
        "title",
        "valueField",
        "palette",
        "width",
        "height",
        "unit",
    }

    assert set(properties) == expected
    assert properties["palette"]["enum"] == [
        "viridis",
        "cividis",
        "plasma",
        "magma",
        "Blues",
        "RdBu_r",
    ]
    assert properties["width"] == {
        "type": "integer",
        "minimum": 640,
        "maximum": 4096,
    }
    assert properties["height"] == {
        "type": "integer",
        "minimum": 480,
        "maximum": 4096,
    }
    assert model["defaultParameters"] == {
        "title": "",
        "valueField": "",
        "palette": "viridis",
        "width": 1024,
        "height": 768,
        "unit": "",
    }
    assert model["parameterBindings"] == {
        name: name
        for name in ("title", "valueField", "palette", "width", "height", "unit")
    }
    assert workflow["parameters"] == [
        "title",
        "valueField",
        "palette",
        "width",
        "height",
        "unit",
    ]
    node = workflow["nodes"][0]
    assert node["parameters"] == {
        name: {"workflowParameter": name}
        for name in ("title", "valueField", "palette", "width", "height", "unit")
    }


def test_operator_manifest_freezes_summary_and_errors() -> None:
    manifest = _load(OPERATOR / "manifest.json")
    smoke = _load(OPERATOR / "smoke.json")
    outputs = {output["name"]: output for output in manifest["outputs"]}

    assert set(outputs) == {"image", "summary"}
    assert outputs["image"]["formats"] == ["png"]
    assert outputs["summary"]["kind"] == "value"
    summary_schema = outputs["summary"]["schema"]
    assert set(summary_schema["required"]) == {
        "sourceKind",
        "sourceFormat",
        "crs",
        "extent",
        "image",
        "style",
        "data",
        "interpretation",
        "limitations",
    }
    assert {"CARTOGRAPHY_FORMAT_MISMATCH", "CARTOGRAPHY_FIELD_INVALID"} <= set(
        manifest["errorCodes"]
    )
    parameters = manifest["parametersSchema"]["properties"]
    assert parameters["palette"]["default"] == "viridis"
    assert parameters["width"]["default"] == 1024
    assert parameters["height"]["default"] == 768
    assert parameters["unit"]["default"] == ""
    assert smoke["expectedOutputs"]["summary"] == {"kind": "value"}
    assert smoke["parameters"] == {
        "title": "Monitoring values",
        "valueField": "value",
        "palette": "viridis",
        "width": 1024,
        "height": 768,
        "unit": "m",
    }


def test_product_ui_exposes_expression_and_static_boundary() -> None:
    ui = _load(ROOT / "products/groundwater/ui/product-ui.json")
    presentation = ui["models"]["groundwater.render-map"]

    assert all(
        phrase in presentation["description"]
        for phrase in ("单文件", "PNG", "CRS", "范围", "无数据")
    )
    assert all(
        phrase in presentation["applicability"]
        for phrase in ("不重投影", "不支持多图层", "在线底图", "交互地图", "PDF")
    )
    assert set(presentation["parameterOverrides"]) == {
        "valueField",
        "palette",
        "width",
        "height",
        "unit",
    }
    assert presentation["confirmationSummary"] == [
        {
            "label": "地图表达",
            "parameters": ["title", "valueField", "palette", "unit"],
        },
        {"label": "输出尺寸", "parameters": ["width", "height"]},
    ]
    assert ui["outputs"]["map-image"]["viewer"] == "image"
    assert ui["outputs"]["map-summary"] == {
        "label": "静态地图表达摘要",
        "viewer": "json",
    }


def test_agent_guidance_uses_current_identity_and_does_not_overclaim() -> None:
    instructions = (DOMAIN / "instructions.md").read_text(encoding="utf-8")
    cards = _load(DOMAIN / "knowledge/model-cards.v1.json")
    card = next(
        document
        for document in cards["documents"]
        if document["knowledgeId"] == "gw-model-card-render-map"
    )
    card_text = " ".join(chunk["content"] for chunk in card["chunks"])

    for text in (instructions, card_text):
        assert "groundwater.render-map@3.1.0" in text
        assert "groundwater.render-map@1.1.0" in text
        assert "sourceVersionId" in text
        assert "map-summary" in text
        assert "CRS" in text and "extent" in text.lower()
        assert "nodata" in text.lower()
        assert "sourceDataId" not in text
        assert "sourceResourceId" not in text
        assert "groundwater.render-map@3.0.0" not in text
    assert all(
        phrase in card_text
        for phrase in ("多图层", "在线底图", "交互地图", "PDF", "不重投影")
    )
