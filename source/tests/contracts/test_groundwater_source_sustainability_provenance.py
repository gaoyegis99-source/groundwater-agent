from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.development.product_selection import load_selected_product_by_id


ROOT = Path(__file__).resolve().parents[2]
DOMAIN = load_selected_product_by_id(ROOT, "groundwater").domain.root
OPERATOR = (
    ROOT
    / "packages/operator-pack/catalog/operators"
    / "groundwater.source_sustainability.evaluate/1.0.0/manifest.json"
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _checksum(document: dict) -> str:
    canonical = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def test_source_sustainability_preserves_one_remote_node_and_three_outputs() -> None:
    model = _load(DOMAIN / "models/source-sustainability.model.json")
    workflow = _load(DOMAIN / "workflows/source-sustainability.workflow.json")

    assert model["version"] == "1.1.0"
    assert workflow["version"] == "1.0.0"
    assert model["workflowRef"] == {
        "workflowId": "groundwater.source-sustainability",
        "version": "1.0.0",
        "checksum": _checksum(workflow),
    }
    assert model["finalOutputs"] == [
        "sustainability-result",
        "sustainability-result-file",
        "sustainability-run-receipt",
    ]
    assert len(workflow["nodes"]) == 1
    assert workflow["nodes"][0]["operatorId"] == (
        "groundwater.source_sustainability.evaluate"
    )


def test_publications_distinguish_remote_results_from_platform_verification() -> None:
    model = _load(DOMAIN / "models/source-sustainability.model.json")
    tool_text = model["tool"]["description"]
    publications = model["outputPublications"]
    result = publications["sustainability-result"]["semantics"]
    result_file = publications["sustainability-result-file"]["semantics"]
    receipt = publications["sustainability-run-receipt"]["semantics"]

    for phrase in ("外部服务", "不在平台内复现", "版本白名单", "完整性"):
        assert phrase in tool_text
    assert "external" in result["source"].lower()
    assert "structured value" in result["description"].lower()
    assert any(
        "not locally reproduced" in item.lower() for item in result["limitations"]
    )
    assert any("regulatory" in item.lower() for item in result["limitations"])
    assert all(
        phrase in result_file["description"].lower()
        for phrase in ("remote", "download", "size", "sha256")
    )
    receipt_text = " ".join([receipt["description"], *receipt["limitations"]])
    for phrase in (
        "operationType",
        "serviceVersion",
        "modelVersion",
        "requestSha256",
        "inputs",
        "valuesSha256",
        "artifacts",
        "remoteReceiptSha256",
    ):
        assert phrase in receipt_text
    assert "integrity is not scientific validation" in receipt_text.lower()


def test_operator_and_product_pin_the_external_dependency_and_failure_surface() -> None:
    operator = _load(OPERATOR)
    product = _load(ROOT / "products/groundwater/product-manifest.json")
    remote = next(
        item
        for item in product["remoteServices"]
        if item["serviceId"] == "groundwater-sustainability"
    )

    assert operator["runtime"] == "remote_http"
    assert operator["remoteBinding"] == {
        "serviceId": "groundwater-sustainability",
        "operationType": "sustainability.evaluate",
    }
    for phrase in ("external", "allowlist", "receipt", "not reproduce"):
        assert phrase in operator["description"].lower()
    assert set(operator["errorCodes"]) >= {
        "OPERATOR_TIMEOUT",
        "REMOTE_AUTHENTICATION_FAILED",
        "REMOTE_BINDING_INVALID",
        "REMOTE_INTEGRITY_FAILED",
        "REMOTE_OPERATION_FAILED",
        "REMOTE_PROTOCOL_INVALID",
        "REMOTE_SUBMISSION_UNCERTAIN",
        "REMOTE_VERSION_MISMATCH",
    }
    assert remote["apiVersion"] == "remote-operation.v1"
    assert remote["allowedServiceVersions"] == ["0.7.0"]
    assert remote["allowedModelVersions"] == ["sustainability-evaluation.v1.1"]
    assert remote["allowedOperations"] == ["sustainability.evaluate"]
    assert "baseUrl" not in remote


def test_product_ui_exposes_external_provenance_and_result_boundaries() -> None:
    ui = _load(ROOT / "products/groundwater/ui/product-ui.json")
    presentation = ui["models"]["groundwater.source-sustainability"]
    combined = f"{presentation['description']} {presentation['applicability']}"

    for phrase in ("外部服务", "平台不复现", "版本白名单", "完整性校验", "行政审批"):
        assert phrase in combined
    assert ui["outputs"]["sustainability-result"]["label"] == (
        "外部服务返回的结构化评价"
    )
    assert ui["outputs"]["sustainability-result-file"]["label"] == (
        "完整性校验后的外部结果文件"
    )
    assert ui["outputs"]["sustainability-run-receipt"]["label"] == (
        "外部执行与完整性凭据"
    )


def test_agent_guidance_names_receipt_fields_failures_and_non_reproduction() -> None:
    instructions = (DOMAIN / "instructions.md").read_text(encoding="utf-8")
    cards = _load(DOMAIN / "knowledge/model-cards.v1.json")
    card = next(
        document
        for document in cards["documents"]
        if document["knowledgeId"] == "gw-model-card-source-sustainability"
    )
    card_text = " ".join(chunk["content"] for chunk in card["chunks"])

    for text in (instructions, card_text):
        assert "groundwater.source-sustainability@1.1.0" in text
        assert "groundwater.source-sustainability@1.0.0" in text
        for phrase in (
            "外部服务",
            "平台不复现",
            "sustainability-result",
            "sustainability-result-file",
            "sustainability-run-receipt",
            "operationType",
            "valuesSha256",
            "REMOTE_VERSION_MISMATCH",
            "REMOTE_INTEGRITY_FAILED",
            "REMOTE_SUBMISSION_UNCERTAIN",
            "行政审批",
        ):
            assert phrase in text
