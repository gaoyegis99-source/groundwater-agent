from dataclasses import replace
from uuid import uuid4

from fastapi.testclient import TestClient

from platform_knowledge_next.api import create_app
from platform_knowledge_next.ingestion import Importer


P = "/knowledge/v3/"


class Data:
    def begin_attempt(self):
        pass

    def validate(self, _binding):
        pass


def headers(subject: str, roles: str = "researcher") -> dict[str, str]:
    return {
        "X-Project-ID": "local-data",
        "X-Platform-Subject": subject,
        "X-Platform-Display-Name": subject,
        "X-Platform-Roles": roles,
    }


def test_private_knowledge_is_filtered_across_catalog_retrieval_and_receipts(
    db, package, embeddings
) -> None:
    package.idempotency_key = "acl-" + uuid4().hex
    trusted = replace(db.settings, identity_mode="gateway")
    alice_operator = headers("alice@example.test", "researcher,admin")
    alice = headers("alice@example.test")
    bob = headers("bob@example.test")
    auditor = headers("audit@example.test", "viewer")

    with TestClient(create_app(trusted, embeddings)) as client:
        submitted = client.post(
            P + "imports",
            headers=alice_operator,
            json=package.model_dump(mode="json", by_alias=True),
        )
        assert submitted.status_code == 202, submitted.text
        assert Importer(db, embeddings, Data()).run_once()
        imported = client.get(
            P + "imports/" + submitted.json()["importId"],
            headers=alice_operator,
        ).json()
        version_id = imported["result"]["versions"][0]["versionId"]
        document_id = package.documents[0].knowledge_id

        initial = client.get(
            P + f"documents/{document_id}/access", headers=alice
        )
        assert initial.status_code == 200, initial.text
        assert initial.json() == {
            "resourceKind": "KNOWLEDGE",
            "resourceId": document_id,
            "ownerSubject": "alice@example.test",
            "visibility": "PROJECT",
        }
        private = client.put(
            P + f"documents/{document_id}/access",
            headers=alice,
            json={"visibility": "PRIVATE"},
        )
        assert private.status_code == 200, private.text

        assert client.get(P + "documents", headers=bob).json()["items"] == []
        assert client.get(
            P + f"versions/{version_id}", headers=bob
        ).status_code == 404
        hidden = client.post(
            P + "retrievals",
            headers=bob,
            json={"query": "地下水", "mode": "lexical"},
        )
        assert hidden.status_code == 200, hidden.text
        assert hidden.json()["status"] == "NO_MATCH"

        shared = client.put(
            P + f"documents/{document_id}/access",
            headers=alice,
            json={"visibility": "PROJECT"},
        )
        assert shared.status_code == 200, shared.text
        visible = client.post(
            P + "retrievals",
            headers=bob,
            json={"query": "地下水", "mode": "lexical"},
        )
        assert visible.status_code == 200, visible.text
        assert visible.json()["items"]
        receipt_id = visible.json()["receiptId"]
        assert client.get(P + "retrievals/" + receipt_id, headers=bob).status_code == 200
        assert client.get(
            P + "retrievals/" + receipt_id, headers=alice
        ).status_code == 404
        assert client.post(
            P + "lookup", headers=auditor, json={"key": "head"}
        ).status_code == 200
        blocked = client.put(
            P + f"documents/{document_id}/access",
            headers=auditor,
            json={"visibility": "PROJECT"},
        )
        assert blocked.status_code == 403
