"""End-to-end API contracts missed by the earlier algorithm/recovery tests."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
import subprocess
import sys

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from platform_embedding_client import UpstreamServiceError
import pytest

from platform_knowledge_next.api import MAX_BODY, create_app
from platform_knowledge_next.config import Settings
from platform_knowledge_next.errors import KnowledgeProblemError
from platform_knowledge_next.ingestion import Importer, submit
from platform_knowledge_next.models import KnowledgePackageV1

from conftest import Data
from test_service import job


P = "/knowledge/v3/"


def test_kernel_verified_gateway_identity_and_management_roles(db, embeddings):
    settings = replace(db.settings, identity_mode="gateway")
    user = {
        "X-Project-ID": settings.project,
        "X-Platform-Subject": "user@example.test",
        "X-Platform-Display-Name": "Project User",
        "X-Platform-Roles": "researcher",
    }
    auditor = {
        **user,
        "X-Platform-Subject": "auditor@example.test",
        "X-Platform-Display-Name": "Project Viewer",
        "X-Platform-Roles": "viewer",
    }
    operator = {
        **user,
        "X-Platform-Subject": "operator@example.test",
        "X-Platform-Display-Name": "Project Admin",
        "X-Platform-Roles": "admin",
    }
    with TestClient(create_app(settings, embeddings)) as client:
        assert client.get(P + "documents").status_code == 401
        assert (
            client.get(P + "documents", headers={"X-Project-ID": "wrong"}).status_code
            == 401
        )
        assert (
            client.get(
                P + "documents",
                headers={
                    key: value
                    for key, value in user.items()
                    if key != "X-Platform-Display-Name"
                },
            ).status_code
            == 401
        )
        assert (
            client.get(
                P + "documents",
                headers={
                    **user,
                    "X-Platform-Display-Name": ("名" * 81).encode("utf-8"),
                },
            ).status_code
            == 401
        )
        auditor_read = client.get(P + "documents", headers=auditor)
        assert auditor_read.status_code == 200
        capabilities = client.get(P + "capabilities", headers=user)
        assert capabilities.status_code == 200
        body = capabilities.json()
        assert body["vectorSearch"] == "exact"
        assert body["identity"] == "gateway"
        assert body["productId"] == settings.product
        assert body["currentUser"] == {
            "subject": "user@example.test",
            "displayName": "Project User",
            "roles": ["researcher"],
        }
        assert body["permissions"] == {
            "catalogRead": True,
            "retrievalCreate": True,
            "evidenceRead": True,
            "evidenceValidate": True,
            "importWrite": False,
            "lifecycleWrite": False,
            "accessWrite": True,
        }
        operator_body = client.get(P + "capabilities", headers=operator).json()
        assert operator_body["permissions"]["importWrite"] is True
        assert operator_body["permissions"]["lifecycleWrite"] is True
        assert operator_body["permissions"]["accessWrite"] is True
        assert (
            client.get(
                P + "documents", headers={**auditor, "X-Project-ID": "wrong"}
            ).status_code
            == 403
        )
        forbidden = client.post(P + "imports", headers=user, json={})
        assert forbidden.status_code == 403
        assert forbidden.json()["code"] == "ROLE_FORBIDDEN"


def test_readiness_checks_database_and_embedding(monkeypatch, embeddings):
    monkeypatch.setattr(
        "platform_knowledge_next.api.Database.check", lambda _self: None
    )
    with TestClient(create_app(Settings("postgresql://unused"), embeddings)) as client:
        response = client.get("/internal/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "components": {"database": "UP", "embedding": "UP"},
    }


def test_readiness_reports_embedding_failure(monkeypatch, embeddings):
    monkeypatch.setattr(
        "platform_knowledge_next.api.Database.check", lambda _self: None
    )

    def unavailable():
        raise UpstreamServiceError("EMBEDDING_PROVIDER_UNAVAILABLE")

    embeddings.ready = unavailable
    with TestClient(create_app(Settings("postgresql://unused"), embeddings)) as client:
        response = client.get("/internal/health/ready")
    assert response.status_code == 503
    assert response.json()["components"] == {
        "database": "UP",
        "embedding": "DOWN",
    }


def test_http_catalog_pagination_and_import_listing(db, package, embeddings):
    base = package.documents[0]
    package.documents = [base.model_copy(deep=True) for _ in range(25)]
    for n, doc in enumerate(package.documents):
        doc.knowledge_id = f"catalog-{n:02}"
    with TestClient(create_app(db.settings, embeddings)) as client:
        accepted = client.post(
            P + "imports", json=package.model_dump(mode="json", by_alias=True)
        )
        assert accepted.status_code == 202
        iid = accepted.json()["importId"]
        assert client.get(P + "imports/" + iid).json()["state"] == "QUEUED"
        assert client.get(P + "documents").json()["items"] == []
        Importer(db, embeddings, Data()).run_once()
        seen = []
        cursor = ""
        while True:
            page = client.get(
                P + "documents", params={"cursor": cursor, "limit": 7}
            ).json()
            seen.extend(row["documentId"] for row in page["items"])
            cursor = page["nextCursor"]
            if cursor is None:
                break
        assert seen == [d.knowledge_id for d in package.documents]
        assert (
            len(
                client.get(P + "documents", params={"query": "catalog-0"}).json()[
                    "items"
                ]
            )
            == 10
        )
        assert client.get(P + "imports").json()["items"][0]["state"] == "SUCCEEDED"
        assert client.get(P + "imports", params={"cursor": iid}).json()["items"] == []
        assert client.get(P + "documents", params={"limit": 101}).status_code == 422
        for path in [
            "imports/missing",
            "versions/missing",
            "versions/missing/lifecycle",
            "retrievals/missing",
        ]:
            assert client.get(P + path).status_code == 404


def test_history_lifecycle_cas_receipt_and_exact_read(
    db, package, embeddings, imported
):
    with TestClient(create_app(db.settings, embeddings)) as client:
        old = client.post(P + "lookup", json={"key": "head"}).json()
        item = old["items"][0]
        receipt = client.get(P + "retrievals/" + old["receiptId"]).json()
        ref = {key: item[key] for key in ("versionId", "unitId", "checksum")}
        package.idempotency_key = "replacement"
        package.documents[0].expected_version = imported
        package.documents[0].units[0].content = "新版正文：不得替换旧引用。"
        iid = client.post(
            P + "imports", json=package.model_dump(mode="json", by_alias=True)
        ).json()["importId"]
        Importer(db, embeddings, Data()).run_once()
        current = client.get(P + "imports/" + iid).json()["result"]["versions"][0][
            "versionId"
        ]
        assert current != imported
        assert len(client.get(P + "documents/head/versions").json()["items"]) == 2
        read = client.post(P + "evidence/read", json={"refs": [ref]}).json()
        assert read["items"][0]["content"] == item["content"]
        assert (
            client.post(P + "lookup", json={"key": "head"}).json()["items"][0][
                "versionId"
            ]
            == current
        )
        change = {
            "expectedRevision": 1,
            "status": "WITHDRAWN",
            "reviewAfter": "2099-01-01",
            "reason": "test",
        }
        assert (
            client.post(
                P + "versions/" + imported + "/lifecycle", json=change
            ).status_code
            == 200
        )
        assert (
            client.post(
                P + "versions/" + imported + "/lifecycle", json=change
            ).status_code
            == 409
        )
        validated = client.post(P + "evidence/validate", json={"refs": [ref]}).json()[
            "items"
        ][0]
        assert validated["status"] == "WITHDRAWN" and validated["content"] is None
        change.update(expectedRevision=2, status="ACTIVE")
        assert (
            client.post(
                P + "versions/" + imported + "/lifecycle", json=change
            ).status_code
            == 200
        )
        assert (
            len(client.get(P + "versions/" + imported + "/lifecycle").json()["items"])
            == 3
        )
        assert (
            client.post(P + "lookup", json={"key": "head"}).json()["items"][0][
                "versionId"
            ]
            == current
        )
        assert client.get(P + "retrievals/" + old["receiptId"]).json() == receipt
        validated = client.post(P + "evidence/validate", json={"refs": [ref]}).json()[
            "items"
        ][0]
        assert validated["status"] == "OK" and validated["content"] is None


def test_all_static_tools_and_openapi_agree(db, embeddings, imported):
    with TestClient(create_app(db.settings, embeddings)) as client:
        openapi = client.get("/openapi.json").json()
        tools = client.get(P + "tools").json()["tools"]
        item = client.post(P + "lookup", json={"key": "head"}).json()["items"][0]
        requests = {
            "retrieve_evidence": {
                "query": "水头",
                "mode": "lexical",
                "scope": "general",
            },
            "read_evidence": {
                "refs": [
                    {
                        "version_id": item["versionId"],
                        "unit_id": item["unitId"],
                        "checksum": item["checksum"],
                    }
                ]
            },
            "lookup_knowledge": {"key": "head"},
        }
        for tool in tools:
            arguments = requests[tool["name"]]
            Draft202012Validator(tool["inputSchema"]).validate(arguments)
            result = client.post(
                tool["path"],
                json={
                    "callId": "test",
                    "projectId": "local-data",
                    "arguments": arguments,
                },
            )
            assert result.status_code == 200
            assert set(result.json()) == {"data", "outcome"}
            Draft202012Validator(tool["outputSchema"]).validate(result.json()["data"])
            assert set(result.json()["outcome"]) == {"outcomeCode", "facts"}
        assert (
            "scope"
            in openapi["components"]["schemas"]["RetrievalRequest"]["properties"]
        )
        path = "/internal/tools/v5/retrieve_evidence"
        for invalid in [
            {"query": "x", "scope": "bound"},
            {"query": "x", "unknown": True},
        ]:
            assert (
                client.post(
                    path,
                    json={
                        "callId": "test",
                        "projectId": "local-data",
                        "arguments": invalid,
                    },
                ).status_code
                == 422
            )
            assert list(
                Draft202012Validator(tools[0]["inputSchema"]).iter_errors(invalid)
            )
        assert (
            client.post(
                path,
                json={
                    "callId": "test",
                    "projectId": "wrong",
                    "arguments": requests["retrieve_evidence"],
                },
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/internal/tools/v5/missing",
                json={"callId": "test", "projectId": "local-data", "arguments": {}},
            ).status_code
            == 404
        )
        assert (
            client.post(
                path,
                json={
                    "callId": "",
                    "projectId": "local-data",
                    "arguments": requests["retrieve_evidence"],
                },
            ).status_code
            == 422
        )


def test_http_input_limits_are_atomic_and_readonly_covers_lifecycle(
    db, package, embeddings, imported
):
    with TestClient(create_app(db.settings, embeddings)) as client:
        assert (
            client.post(
                P + "imports",
                content=b" " * (MAX_BODY + 1),
                headers={"Content-Type": "application/json"},
            ).status_code
            == 413
        )
        assert (
            client.post(
                P + "imports",
                content=b"{",
                headers={"Content-Type": "application/json"},
            ).status_code
            == 422
        )
        invalid = package.model_dump(mode="json", by_alias=True)
        invalid["project_id"] = "wrong"
        assert client.post(P + "imports", json=invalid).status_code == 422
        invalid = package.model_dump(mode="json", by_alias=True)
        invalid["documents"][0]["units"][0]["content"] = "x" * 4001
        result = client.post(P + "imports", json=invalid)
        assert result.status_code == 422
        assert result.headers["content-type"].startswith("application/problem+json")
        assert result.json()["code"] == "VALIDATION_ERROR"
        assert result.json()["errors"][0]["pointer"] == "/documents/0/units/0/content"
        assert len(client.get(P + "imports").json()["items"]) == 1
    with TestClient(
        create_app(replace(db.settings, read_only=True), embeddings)
    ) as client:
        assert (
            client.post(
                P + "versions/" + imported + "/lifecycle",
                json={
                    "expectedRevision": 1,
                    "status": "WITHDRAWN",
                    "reviewAfter": "2099-01-01",
                    "reason": "test",
                },
            ).status_code
            == 403
        )
        assert (
            client.post(
                P + "retrievals", json={"query": "水头", "mode": "lexical"}
            ).json()["status"]
            == "OK"
        )


def test_simultaneous_replay_and_commit_response_loss(
    db, package, embeddings, monkeypatch
):
    with ThreadPoolExecutor(8) as pool:
        accepted = list(pool.map(lambda _: submit(db, package), range(8)))
    assert len({r["import_id"] for r in accepted}) == 1
    importer = Importer(db, embeddings, Data())
    commit = importer.commit

    def lost_response(*args):
        commit(*args)
        raise KnowledgeProblemError("COMMIT_RESPONSE_LOST", 503)

    monkeypatch.setattr(importer, "commit", lost_response)
    importer.run_once()
    assert job(db, accepted[0]["import_id"])["state"] == "SUCCEEDED"
    assert submit(db, package)["state"] == "SUCCEEDED"
    assert not importer.run_once()
    with db.transaction() as c:
        assert c.execute("SELECT count(*) AS n FROM versions").fetchone()["n"] == 1
        assert c.execute("SELECT count(*) AS n FROM imports").fetchone()["n"] == 1


def test_cli_domain_conversion_and_typed_http_client_write_guard():
    from pathlib import Path
    from platform_knowledge_client import KnowledgeClient, operations
    import httpx

    root = Path(__file__).resolve().parents[3]
    from scripts.development.product_selection import load_selected_product_by_id

    domain = load_selected_product_by_id(root, "groundwater").domain.root
    source = next((domain / "knowledge").glob("*.json"))
    cli_environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            map(
                str,
                (
                    root / "services/knowledge-next/src",
                    root / "packages/protocol-contracts/src",
                ),
            )
        ),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "platform_knowledge_next.cli",
            "convert",
            str(source),
            "--project",
            "local-data",
            "--key",
            "cli-functional",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=cli_environment,
    )
    package = KnowledgePackageV1.model_validate_json(result.stdout)
    assert package.documents and package.idempotency_key == "cli-functional"
    client = KnowledgeClient(
        "http://knowledge",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(500))
        ),
    )
    with pytest.raises(ValueError, match="WRITES_NOT_ENABLED"):
        client.call(operations.KNOWLEDGE_SUBMIT_IMPORT, body={})
    assert operations.KNOWLEDGE_RETRIEVE.path == "/retrievals"
    # Generated contract is checked against the checked-in declaration, not only itself.
    generated = json.loads(
        subprocess.check_output(
            [sys.executable, "-m", "platform_knowledge_next.cli", "contracts"],
            env=cli_environment,
        )
    )
    checked = json.loads(
        (
            root
            / "services/knowledge-next/src/platform_knowledge_next/contracts/observation-tools.v5.json"
        ).read_text()
    )
    assert generated == checked
