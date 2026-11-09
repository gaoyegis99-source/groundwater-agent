from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from scripts.development import generate_public_api_contract as module_under_test

ROOT = Path(__file__).resolve().parents[2]


def test_checked_in_public_contract_matches_owner_openapi() -> None:
    expected = module_under_test.encoded_contract(ROOT)
    assert (
        ROOT / "products/groundwater/contracts/public-api-v12.json"
    ).read_bytes() == expected


def test_explicit_product_selection_matches_single_product_default() -> None:
    assert module_under_test.encoded_contract(
        ROOT, "groundwater"
    ) == module_under_test.encoded_contract(ROOT)


def test_public_contract_has_only_gateway_paths_and_unique_operations() -> None:
    document = json.loads(module_under_test.encoded_contract(ROOT))
    assert document["info"]["version"] == "11"
    assert document["paths"]
    assert all(path.startswith("/api/v12/") for path in document["paths"])
    assert not any("/internal/" in path for path in document["paths"])
    operation_ids = [
        operation["operationId"]
        for path in document["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]
    assert len(operation_ids) == len(set(operation_ids))
    assert {
        "kernel__ProblemDetailsV11",
        "data__ProblemDetailsV11",
        "knowledge_next__ProblemDetailsV11",
    } <= set(document["components"]["schemas"])
    references = []

    def collect(value):
        if isinstance(value, dict):
            references.extend(
                item for key, item in value.items() if key == "$ref"
            )
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(document)
    schema_prefix = "#/components/schemas/"
    assert all(reference.startswith(schema_prefix) for reference in references)
    assert all(
        reference.removeprefix(schema_prefix) in document["components"]["schemas"]
        for reference in references
    )


def test_public_problem_contracts_share_the_same_required_core() -> None:
    document = json.loads(module_under_test.encoded_contract(ROOT))
    schemas = document["components"]["schemas"]
    required = {
        "type", "title", "status", "detail", "code",
        "requestId", "traceId", "errors",
    }
    for service in ("kernel", "data", "knowledge_next"):
        problem = schemas[f"{service}__ProblemDetailsV11"]
        assert required <= set(problem["required"])
        assert required <= set(problem["properties"])

    problem_contents = [
        response["content"]
        for path_item in document["paths"].values()
        for operation in path_item.values()
        if isinstance(operation, dict)
        for status, response in operation.get("responses", {}).items()
        if not str(status).startswith("2")
        and "application/problem+json" in response.get("content", {})
    ]
    assert problem_contents
    assert all(set(content) == {"application/problem+json"} for content in problem_contents)


def test_all_service_problem_schemas_are_identical() -> None:
    paths = (
        ROOT / "kernel/contracts/openapi-v12.json",
        ROOT / "services/data/contracts/openapi-v3.json",
        ROOT / "services/knowledge-next/contracts/openapi-v3.json",
        ROOT / "services/executor/contracts/openapi-v6.json",
    )
    schemas = [
        json.loads(path.read_text())["components"]["schemas"]["ProblemDetailsV11"]
        for path in paths
    ]
    assert all(schema == schemas[0] for schema in schemas[1:])


def test_data_and_knowledge_emit_the_same_public_problem_shape(tmp_path) -> None:
    from platform_data_service.api.app import create_app as create_data_app
    from platform_data_service.settings import Settings as DataSettings
    from platform_knowledge_next.api import create_app as create_knowledge_app
    from platform_knowledge_next.config import Settings as KnowledgeSettings

    data = TestClient(create_data_app(DataSettings(
        "postgresql://contract.invalid/contract", tmp_path.resolve(), project="p",
        product="groundwater",
    )))
    knowledge = TestClient(create_knowledge_app(
        KnowledgeSettings(
            "postgresql://contract.invalid/contract",
            project="p",
            product="groundwater",
        ),
        embeddings=object(),
    ))
    responses = (
        data.post("/data/v3/discovery", json={"limit": 0}),
        knowledge.post("/knowledge/v3/retrievals", json={}),
    )
    for response in responses:
        body = response.json()
        assert response.status_code == 422
        assert response.headers["content-type"].startswith(
            "application/problem+json"
        )
        assert body["code"] == "VALIDATION_ERROR"
        assert body["status"] == 422
        assert body["traceId"]
        assert body["requestId"]
        assert body["errors"][0]["pointer"].startswith("/")
        assert "error" not in body
        assert body["type"].startswith("urn:groundwater:problem:")


def test_public_http_rejects_snake_case_but_tool_arguments_keep_contract_keys(
    tmp_path,
) -> None:
    from platform_data_service.api.app import create_app as create_data_app
    from platform_data_service.settings import Settings as DataSettings
    from platform_knowledge_next.api import create_app as create_knowledge_app
    from platform_knowledge_next.config import Settings as KnowledgeSettings

    data = TestClient(create_data_app(DataSettings(
        "postgresql://contract.invalid/contract", tmp_path.resolve(), project="p"
    )))
    knowledge = TestClient(create_knowledge_app(
        KnowledgeSettings("postgresql://contract.invalid/contract", project="p"),
        embeddings=object(),
    ))

    data_public = data.post(
        "/data/v3/queries",
        json={"idempotency_key": "request", "versionIds": ["version"]},
    )
    knowledge_public = knowledge.post(
        "/knowledge/v3/retrievals",
        json={"query": "head", "budget": {"max_units": 1}},
    )
    data_tool = data.post(
        "/internal/tools/v4/discover_datasets",
        json={
            "callId": "call",
            "projectId": "p",
            "arguments": {"query": "head", "fieldTerms": ["f0"]},
        },
    )
    knowledge_tool = knowledge.post(
        "/internal/tools/v5/retrieve_evidence",
        json={
            "callId": "call",
            "projectId": "p",
            "arguments": {"query": "head", "budget": {"maxUnits": 1}},
        },
    )

    assert data_public.status_code == 422
    assert knowledge_public.status_code == 422
    assert data_tool.status_code == 422
    assert knowledge_tool.status_code == 422
