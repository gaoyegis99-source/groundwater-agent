from dataclasses import replace
import hashlib
from uuid import uuid4

from fastapi.testclient import TestClient

from platform_data_service.api.app import create_app


def headers(subject: str, roles: str = "researcher") -> dict[str, str]:
    return {
        "X-Project-ID": "local-data",
        "X-Platform-Subject": subject,
        "X-Platform-Display-Name": subject,
        "X-Platform-Roles": roles,
    }


def publish(client: TestClient, worker, actor: dict[str, str], *, format="csv"):
    content = b"well,value\n001,2\n" if format == "csv" else b"model-state"
    response = client.post(
        "/data/v3/publications",
        headers=actor,
        json={
            "idempotencyKey": "acl-" + uuid4().hex,
            "outputs": [
                {
                    "name": "asset",
                    "title": "ACL asset",
                    "format": format,
                    "policy": "FILE_QUERYABLE" if format == "csv" else "FILE_ONLY",
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    publication_id = response.json()["publicationId"]
    response = client.put(
        f"/data/v3/publications/{publication_id}/items/asset",
        headers={
            **actor,
            "X-Content-SHA256": "sha256:" + hashlib.sha256(content).hexdigest(),
        },
        content=content,
    )
    assert response.status_code == 200, response.text
    response = client.post(
        f"/data/v3/publications/{publication_id}/prepare", headers=actor
    )
    assert response.status_code == 202, response.text
    assert worker.once()
    response = client.post(
        f"/data/v3/publications/{publication_id}/commit", headers=actor
    )
    assert response.status_code == 200, response.text
    return response.json()["outputs"][0]


def test_private_dataset_is_filtered_without_resource_grants(
    settings, worker
) -> None:
    trusted = replace(settings, identity_mode="gateway")
    alice_operator = headers("alice@example.test", "researcher,admin")
    alice = headers("alice@example.test")
    bob = headers("bob@example.test")
    with TestClient(create_app(trusted)) as client:
        output = publish(client, worker, alice_operator)
        dataset_id = output["datasetId"]
        version_id = output["versionId"]

        access = client.get(
            f"/data/v3/datasets/{dataset_id}/access", headers=alice
        )
        assert access.status_code == 200, access.text
        assert access.json() == {
            "resourceKind": "DATASET",
            "resourceId": dataset_id,
            "ownerSubject": "alice@example.test",
            "visibility": "PROJECT",
        }
        private = client.put(
            f"/data/v3/datasets/{dataset_id}/access",
            headers=alice,
            json={
                "visibility": "PRIVATE",
            },
        )
        assert private.status_code == 200, private.text
        assert private.json()["visibility"] == "PRIVATE"

        assert client.get("/data/v3/datasets", headers=bob).json()["items"] == []
        hidden = client.get(f"/data/v3/versions/{version_id}", headers=bob)
        assert hidden.status_code == 404

        shared = client.put(
            f"/data/v3/datasets/{dataset_id}/access",
            headers=alice,
            json={
                "visibility": "PROJECT",
            },
        )
        assert shared.status_code == 200, shared.text
        assert len(client.get("/data/v3/datasets", headers=bob).json()["items"]) == 1
        assert client.get(
            f"/data/v3/versions/{version_id}", headers=bob
        ).status_code == 200
        assert client.get(
            f"/data/v3/datasets/{dataset_id}/access", headers=bob
        ).status_code == 403
        invalid_legacy_acl = client.put(
            f"/data/v3/datasets/{dataset_id}/access",
            headers=alice,
            json={
                "visibility": "PROJECT",
                "grants": [],
            },
        )
        assert invalid_legacy_acl.status_code == 422


def test_model_assets_and_auditor_read_only_policy(settings, worker) -> None:
    trusted = replace(settings, identity_mode="gateway")
    operator = headers("operator@example.test", "researcher,admin")
    auditor = headers("audit@example.test", "viewer")
    with TestClient(create_app(trusted)) as client:
        output = publish(
            client, worker, operator, format="pytorch-state-dict"
        )
        access = client.get(
            f"/data/v3/datasets/{output['datasetId']}/access", headers=operator
        )
        assert access.status_code == 200, access.text
        assert access.json()["resourceKind"] == "MODEL"
        assert client.get(
            f"/data/v3/versions/{output['versionId']}", headers=auditor
        ).status_code == 200
        blocked = client.post(
            "/data/v3/queries",
            headers=auditor,
            json={
                "idempotencyKey": "auditor-query",
                "versionIds": [output["versionId"]],
            },
        )
        assert blocked.status_code == 403
        assert blocked.json()["code"] == "ROLE_FORBIDDEN"
