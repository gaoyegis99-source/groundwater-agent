import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from platform_knowledge_next.config import Settings
from platform_knowledge_next.api import create_app
from platform_knowledge_next.data_adapter import DataAdapter
from platform_knowledge_next.errors import KnowledgeProblemError
from platform_knowledge_next.package_conversion import convert_domain_package
from platform_knowledge_next.models import (
    DataBinding,
    EvidenceBundleV1,
    KnowledgePackageV1,
)
from platform_knowledge_next.wire import model_to_wire
from platform_protocol_contracts import validate_camel_json


class _Embeddings:
    def close(self):
        pass


def test_evidence_bundle_maps_platform_owned_nested_fields_to_camel_case():
    response = model_to_wire(
        EvidenceBundleV1(
            status="NO_MATCH",
            receipt_id="receipt-1",
            knowledge_revision=1,
            index_generation="knowledge-index-v1",
            observed_at="2026-10-15T00:00:00Z",
            items=[],
            coverage={
                "retrieval_exhaustive": False,
                "required_context_complete": True,
                "request_binding": {
                    "service_binding": "data-primary",
                    "project_id": "local-data",
                },
                "cohorts": {
                    "general": {
                        "dense_candidates": 1,
                        "returned_primary_units": 1,
                    }
                },
            },
            budget={"returned_units": 0, "returned_chars": 0},
            timings_ms={"embedding_provider": 1.5},
        )
    )

    validate_camel_json(response)
    assert response["coverage"]["retrievalExhaustive"] is False
    assert response["coverage"]["requestBinding"]["projectId"] == "local-data"
    assert response["coverage"]["cohorts"]["general"]["denseCandidates"] == 1
    assert response["timingsMs"] == {"embeddingProvider": 1.5}


def test_openapi_declares_catalog_collection_pages():
    spec = create_app(Settings("postgresql://unused"), _Embeddings()).openapi()
    expected = {
        "/knowledge/v3/imports": "ImportPage",
        "/knowledge/v3/documents": "DocumentPage",
        "/knowledge/v3/documents/{documentId}/versions": "VersionPage",
        "/knowledge/v3/versions/{versionId}/lifecycle": "LifecyclePage",
    }
    for path, model in expected.items():
        schema = spec["paths"][path]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        assert schema["$ref"].endswith("/" + model)
        properties = spec["components"]["schemas"][model]["properties"]
        assert properties["items"]["type"] == "array"
        assert "nextCursor" in properties


def test_current_domain_documents_convert_without_loss():
    from scripts.development.product_selection import load_selected_product_by_id

    repository = Path(__file__).resolve().parents[3]
    root = load_selected_product_by_id(repository, "groundwater").domain.root / "knowledge"
    docs, units = 0, 0
    for path in root.glob("*.json"):
        body = json.loads(path.read_text())
        package = convert_domain_package(body, project="local-data", idempotency_key=path.stem)
        docs += len(package.documents)
        units += sum(len(d.units) for d in package.documents)
        for before, after in zip(body["documents"], package.documents, strict=True):
            assert before["knowledgeId"] == after.knowledge_id
            assert [u["content"] for u in before["chunks"]] == [
                u.content for u in after.units
            ]
    expected_docs = sum(
        len(json.loads(path.read_text())["documents"]) for path in root.glob("*.json")
    )
    expected_units = sum(
        len(document["chunks"])
        for path in root.glob("*.json")
        for document in json.loads(path.read_text())["documents"]
    )
    assert (docs, units) == (expected_docs, expected_units)


def test_evaluator_submits_domain_bundle_as_sdk_wire_model():
    path = Path(__file__).resolve().parents[1] / "scripts/evaluate.py"
    spec = importlib.util.spec_from_file_location("knowledge_evaluator", path)
    evaluator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evaluator)
    repository = Path(__file__).resolve().parents[3]
    domain = evaluator.load_selected_product_by_id(repository, "groundwater").domain
    package_path = domain.root / domain.manifest["knowledgeDocuments"][0]

    class Client:
        def submit_import(self, request):
            assert request.project_id == "audit"
            assert request.documents[0].units[0].content
            return evaluator.models.ImportResponse.model_validate({
                "importId": "import-1", "state": "QUEUED", "dataAttempts": 0,
                "actor": {}, "lastError": None, "nextRetryAt": None, "retryDeadline": None,
            })

    assert evaluator.submit_domain_package(Client(), package_path, "audit") == "import-1"


def test_domain_conversion_preserves_classification_and_context():
    from scripts.development.product_selection import load_selected_product_by_id

    repository = Path(__file__).resolve().parents[3]
    domain = load_selected_product_by_id(repository, "groundwater").domain
    body = domain.definitions["knowledge/model-cards.v1.json"]
    package = convert_domain_package(body, project="local-data", idempotency_key="metadata")
    by_id = {document.knowledge_id: document for document in package.documents}
    trend = by_id["gw-model-card-monitoring-trend"]
    assert trend.model_id == "groundwater.monitoring-trend"
    assert trend.model_version == "3.1.0"
    assert trend.model_checksum.startswith("sha256:")
    assert "监测趋势" in trend.aliases
    assert by_id["gw-model-card-render-map"].units[1].required_units == ["chunk-0"]
    assert trend.units[0].locator == {"section": "模型与适用任务"}

    body = domain.definitions["knowledge/groundwater-statistical-methods.v1.json"]
    methods = convert_domain_package(body, project="local-data", idempotency_key="methods")
    assert {document.knowledge_id for document in methods.documents} >= {
        "gw-method-sen-slope", "gw-method-pettitt"
    }
    assert len(methods.documents) == 10
    assert all(document.kind in {"definition", "method"} and len(document.units) == 1
               for document in methods.documents)


@pytest.mark.parametrize(
    "change", ["project", "semantic", "version", "field", "missing", "offline"]
)
def test_data_binding_rejections(change):
    def respond(request):
        assert request.headers["x-platform-subject"] == "platform-knowledge"
        assert request.headers["x-platform-display-name"] == "Platform Knowledge"
        assert request.headers["x-platform-roles"] == "researcher"
        if change == "offline":
            raise httpx.ConnectError("offline")
        if change == "missing":
            return httpx.Response(404)
        if request.url.path.endswith("capabilities"):
            body = {"project": "wrong" if change == "project" else "local-data"}
        elif "/semantics/" in request.url.path:
            body = {
                "id": "wrong" if change == "semantic" else "s1",
                "version_id": "v1",
                "body": {
                    "fields": {} if change == "field" else {"f0": {"meaning": "head"}}
                },
            }
        else:
            body = {"id": "wrong" if change == "version" else "v1", "dataset_id": "d1"}
        return httpx.Response(200, json=body)

    data = DataAdapter(
        Settings("unused", data_url="http://data"),
        httpx.Client(transport=httpx.MockTransport(respond)),
    )
    with pytest.raises(KnowledgeProblemError):
        data.validate(
            DataBinding(
                project_id="local-data",
                dataset_id="d1",
                version_id="v1",
                semantic_id="s1",
                field_id="f0",
            )
        )


def test_historical_semantics_not_latest():
    paths = []

    def respond(request):
        paths.append(request.url.path)
        if request.url.path.endswith("capabilities"):
            body = {
                "version": "3.0.0", "productId": "platform",
                "project": "local-data",
                "currentUser": {
                    "subject": "local-operator",
                    "displayName": "Local Operator",
                    "roles": ["researcher", "admin"],
                },
                "permissions": {
                    "catalogRead": True, "queryCreate": True,
                    "previewCreate": True, "objectDownload": True,
                    "userUploadCreate": False,
                    "managedPublicationWrite": False,
                    "semanticWrite": False,
                    "accessWrite": False,
                    "governanceRead": False,
                    "qualityWrite": False,
                    "mlopsWrite": False,
                },
                "identityMode": "stable", "writeEnabled": False,
                "formats": [], "formatMediaTypes": {}, "query": {},
                "preview": {}, "limits": {}, "contracts": {},
                "openapi": "/data/v3/openapi.json",
            }
        elif "/semantics/" in request.url.path:
            body = {
                "semanticId": "s1",
                "versionId": "v1",
                "body": {"fields": {"f0": {"meaning": "head"}}},
                "actor": {}, "createdAt": "2026-01-01T00:00:00Z",
            }
        else:
            body = {
                "versionId": "v1", "datasetId": "d1", "currentVersion": "v1",
                "publicationId": "publication", "semanticId": "s2",
                "title": "fixture", "descriptor": {}, "semantics": {},
                "objects": [], "inputVersions": [],
                "createdAt": "2026-01-01T00:00:00Z", "tags": [],
                "project": "local-data",
            }
        return httpx.Response(200, json=body)

    DataAdapter(
        Settings("unused", data_url="http://data"),
        httpx.Client(transport=httpx.MockTransport(respond)),
    ).validate(
        DataBinding(
            project_id="local-data",
            dataset_id="d1",
            version_id="v1",
            semantic_id="s1",
            field_id="f0",
        )
    )
    assert paths[-1].endswith("/semantics/s1")


def test_invalid_unit_dependencies_and_duplicate_documents(package):
    body = package.model_dump(mode="json", by_alias=True)
    body["documents"][0]["units"][0]["required_units"] = ["missing"]
    with pytest.raises(ValidationError):
        KnowledgePackageV1.model_validate(body)
    body = package.model_dump(mode="json", by_alias=True)
    body["documents"] *= 2
    with pytest.raises(ValidationError):
        KnowledgePackageV1.model_validate(body)
