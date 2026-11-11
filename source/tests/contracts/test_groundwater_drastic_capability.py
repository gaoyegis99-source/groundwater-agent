from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOMAIN = ROOT / "domains/groundwater/6.0.0"
OPERATOR = (
    ROOT
    / "packages/operator-pack/catalog/operators"
    / "groundwater.drastic.evaluate/1.0.0/manifest.json"
)
FACTORS = ("D", "R", "A", "S", "T", "I", "C")
INPUT_BINDINGS = {
    "D": "depthVersionId",
    "R": "rechargeVersionId",
    "A": "aquiferMediaVersionId",
    "S": "soilMediaVersionId",
    "T": "topographyVersionId",
    "I": "vadoseImpactVersionId",
    "C": "hydraulicConductivityVersionId",
    "config": "configVersionId",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _checksum(document: dict) -> str:
    canonical = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def test_drastic_operator_has_fixed_seven_factor_ports_and_a_config() -> None:
    operator = _load(OPERATOR)

    assert operator["id"] == "groundwater.drastic.evaluate"
    assert operator["version"] == "1.0.0"
    assert operator["runtime"] == "native"
    assert [item["name"] for item in operator["inputs"]] == [*FACTORS, "config"]
    assert all(item["cardinality"] == "one" for item in operator["inputs"])
    assert [item["name"] for item in operator["outputs"]] == [
        "index",
        "classification",
        "summary",
    ]
    assert operator["parametersSchema"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert set(operator["errorCodes"]) >= {
        "CONFIG_INVALID",
        "SOURCE_NOT_VERIFIED",
        "CRS_REQUIRED",
        "GRID_MISMATCH",
        "BAND_INVALID",
    }


def test_groundwater_drastic_model_is_one_node_with_exact_version_bindings() -> None:
    model = _load(DOMAIN / "models/drastic.model.json")
    workflow = _load(DOMAIN / "workflows/drastic.workflow.json")

    assert model["modelId"] == "groundwater.drastic"
    assert model["version"] == "1.0.0"
    assert model["workflowRef"] == {
        "workflowId": "groundwater.drastic",
        "version": "1.0.0",
        "checksum": _checksum(workflow),
    }
    assert model["tool"]["name"] == "run_drastic"
    assert model["inputBindings"] == INPUT_BINDINGS
    assert set(model["tool"]["inputSchema"]["required"]) == set(INPUT_BINDINGS.values())
    assert model["defaultParameters"] == {}
    assert model["parameterBindings"] == {}
    assert model["finalOutputs"] == [
        "drastic-index",
        "drastic-classification",
        "drastic-summary",
    ]
    assert len(workflow["nodes"]) == 1
    assert workflow["nodes"][0]["operatorId"] == "groundwater.drastic.evaluate"
    assert workflow["nodes"][0]["inputs"] == {
        name: {"workflowInput": name} for name in INPUT_BINDINGS
    }
    assert [item["name"] for item in workflow["outputs"]] == model["finalOutputs"]
    assert model["semanticRequirements"] == [
        {"kind": "crs", "inputParameter": parameter}
        for parameter in list(INPUT_BINDINGS.values())[:-1]
    ]


def test_drastic_publications_keep_formula_provenance_nodata_and_limits_visible() -> (
    None
):
    model = _load(DOMAIN / "models/drastic.model.json")
    publications = model["outputPublications"]

    index = publications["drastic-index"]["semantics"]
    classification = publications["drastic-classification"]["semantics"]
    summary = publications["drastic-summary"]["semantics"]
    index_text = " ".join([index["description"], *index["limitations"]]).lower()
    class_text = " ".join(
        [classification["description"], *classification["limitations"]]
    ).lower()
    summary_text = " ".join([summary["description"], *summary["limitations"]]).lower()

    for phrase in ("d, r, a, s, t, i and c", "weight", "rating", "nodata"):
        assert phrase in index_text
    assert "exactly aligned" in index_text
    assert "no implicit" in index_text
    assert "class" in class_text
    assert "relative vulnerability" in class_text
    assert "not a pollution probability" in class_text
    for phrase in (
        "rule version",
        "source",
        "uncovered",
        "factor weight",
        "grid",
    ):
        assert phrase in summary_text


def test_product_and_agent_guidance_expose_all_factors_and_scientific_boundary() -> (
    None
):
    ui = _load(ROOT / "products/groundwater/ui/product-ui.json")
    presentation = ui["models"]["groundwater.drastic"]
    instructions = (DOMAIN / "instructions.md").read_text(encoding="utf-8")
    cards = _load(DOMAIN / "knowledge/model-cards.v1.json")
    card = next(
        document
        for document in cards["documents"]
        if document["knowledgeId"] == "gw-model-card-drastic"
    )
    card_text = " ".join(chunk["content"] for chunk in card["chunks"])

    combined_ui = f"{presentation['description']} {presentation['applicability']}"
    for phrase in ("D、R、A、S、T、I、C", "相对脆弱性", "不是污染概率", "NoData"):
        assert phrase in combined_ui
    assert presentation["parameterOverrides"] == {}
    assert ui["outputs"]["drastic-index"]["viewer"] == "map"
    assert ui["outputs"]["drastic-classification"]["viewer"] == "map"
    assert ui["outputs"]["drastic-summary"]["viewer"] == "json"

    for text in (instructions, card_text):
        for phrase in (
            "groundwater.drastic@1.0.0",
            "DRASTIC",
            "D/R/A/S/T/I/C",
            "groundwater-drastic-config.v1",
            "drastic-index",
            "drastic-classification",
            "drastic-summary",
            "NoData",
            "未覆盖",
            "不能",
            "污染概率",
        ):
            assert phrase in text
