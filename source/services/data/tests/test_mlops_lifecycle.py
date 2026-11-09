from dataclasses import replace
import hashlib
from uuid import uuid4

from fastapi.testclient import TestClient

from platform_data_service.adapters.database import Database
from platform_data_service.api.app import create_app


def headers(subject: str, roles: str = "researcher") -> dict[str, str]:
    return {
        "X-Project-ID": "local-data",
        "X-Platform-Subject": subject,
        "X-Platform-Display-Name": subject,
        "X-Platform-Roles": roles,
    }


def publish_model(client, worker, actor, *, dataset_id=None, expected_version=None):
    content = ("weights:" + uuid4().hex).encode()
    output = {
        "name": "model",
        "title": "Groundwater model",
        "format": "pytorch-state-dict",
        "policy": "FILE_ONLY",
    }
    if dataset_id is not None:
        output.update(datasetId=dataset_id, expectedVersion=expected_version)
    response = client.post(
        "/data/v3/publications",
        headers=actor,
        json={
            "idempotencyKey": "model-" + uuid4().hex,
            "outputs": [output],
        },
    )
    assert response.status_code == 201, response.text
    publication_id = response.json()["publicationId"]
    response = client.put(
        f"/data/v3/publications/{publication_id}/items/model",
        headers={
            **actor,
            "X-Content-SHA256": "sha256:" + hashlib.sha256(content).hexdigest(),
        },
        content=content,
    )
    assert response.status_code == 200, response.text
    assert client.post(
        f"/data/v3/publications/{publication_id}/prepare", headers=actor
    ).status_code == 202
    assert worker.once()
    response = client.post(
        f"/data/v3/publications/{publication_id}/commit", headers=actor
    )
    assert response.status_code == 200, response.text
    return response.json()["outputs"][0]


def assess(client, actor, model_id, version, expected, target, tags=None):
    response = client.post(
        f"/data/v3/ml/models/{model_id}/versions/{version}/benchmark",
        headers=actor,
        json={
            "expectedStatus": expected,
            "benchmarkStatus": target,
            "tags": tags or [],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_ml_experiment_registry_alias_resolution_and_rollback(settings, worker):
    trusted = replace(settings, identity_mode="gateway")
    operator = headers("operator@example.test", "researcher,admin")
    auditor = headers("auditor@example.test", "viewer")
    with TestClient(create_app(trusted)) as client:
        first_data = publish_model(client, worker, operator)
        experiment = client.post(
            "/data/v3/ml/experiments",
            headers=operator,
            json={"name": "transfer-learning", "description": "groundwater"},
        )
        assert experiment.status_code == 201, experiment.text
        experiment_id = experiment.json()["experimentId"]
        initial_run = {
            "idempotencyKey": "pretrain-1",
            "experimentId": experiment_id,
            "inputVersions": [first_data["versionId"]],
            "modelInputs": [],
            "parameters": {"epochs": 10, "device": "cpu"},
            "metrics": {"loss": 0.25},
            "environment": {
                "pythonVersion": "3.12.8",
                "platform": "linux-x86_64",
                "packages": {"torch": "2.7.1"},
                "imageDigest": "sha256:" + "1" * 64,
            },
            "operatorImplementations": [
                {
                    "operatorId": "ml.gru_attention_pretrain",
                    "operatorVersion": "1.0.0",
                    "implementationDigest": "sha256:" + "2" * 64,
                }
            ],
        }
        run = client.post("/data/v3/ml/runs", headers=operator, json=initial_run)
        assert run.status_code == 201, run.text
        run_id = run.json()["experimentRunId"]
        replay = client.post("/data/v3/ml/runs", headers=operator, json=initial_run)
        assert replay.status_code == 200
        assert replay.json()["experimentRunId"] == run_id

        registered = client.post(
            f"/data/v3/ml/models/{first_data['datasetId']}/versions",
            headers=operator,
            json={
                "dataVersionId": first_data["versionId"],
                "experimentRunId": run_id,
            },
        )
        assert registered.status_code == 201, registered.text
        first = registered.json()
        assert first["registryVersion"] == 1
        assert first["benchmarkStatus"] == "NOT_ASSESSED"
        assert first["tags"] == []
        assert first["artifactChecksum"].startswith("sha256:")
        skipped = client.post(
            f"/data/v3/ml/models/{first_data['datasetId']}/versions/1/benchmark",
            headers=operator,
            json={
                "expectedStatus": "PASSED",
                "benchmarkStatus": "FAILED",
                "tags": [],
            },
        )
        assert skipped.status_code == 409
        assessed = assess(
            client,
            operator,
            first_data["datasetId"],
            1,
            "NOT_ASSESSED",
            "PASSED",
            ["baseline"],
        )
        assert assessed["tags"] == ["baseline"]
        champion = client.put(
            f"/data/v3/ml/models/{first_data['datasetId']}/aliases/champion",
            headers=operator,
            json={"expectedRevision": 0, "registryVersion": 1},
        )
        assert champion.status_code == 200, champion.text
        assert champion.json()["dataVersionId"] == first_data["versionId"]

        tracked = client.post(
            "/data/v3/ml/runs",
            headers=operator,
            json={
                **initial_run,
                "idempotencyKey": "finetune-1",
                "modelInputs": [
                    {"modelId": first_data["datasetId"], "alias": "champion"}
                ],
            },
        )
        assert tracked.status_code == 201, tracked.text
        frozen = tracked.json()["resolvedModelInputs"][0]
        assert frozen == {
            "modelId": first_data["datasetId"],
            "alias": "champion",
            "aliasRevision": 1,
            "registryVersion": 1,
            "benchmarkStatus": "PASSED",
            "tags": ["baseline"],
            "legacyStage": None,
            "dataVersionId": first_data["versionId"],
            "artifactObjectId": first["artifactObjectId"],
            "artifactChecksum": first["artifactChecksum"],
        }

        second_data = publish_model(
            client,
            worker,
            operator,
            dataset_id=first_data["datasetId"],
            expected_version=first_data["versionId"],
        )
        second = client.post(
            f"/data/v3/ml/models/{first_data['datasetId']}/versions",
            headers=operator,
            json={"dataVersionId": second_data["versionId"]},
        )
        assert second.status_code == 201, second.text
        assert second.json()["registryVersion"] == 2
        assess(
            client, operator, first_data["datasetId"], 2, "NOT_ASSESSED", "PASSED"
        )
        moved = client.put(
            f"/data/v3/ml/models/{first_data['datasetId']}/aliases/champion",
            headers=operator,
            json={"expectedRevision": 1, "registryVersion": 2},
        )
        assert moved.status_code == 200
        assert moved.json()["aliasRevision"] == 2
        rolled_back = client.put(
            f"/data/v3/ml/models/{first_data['datasetId']}/aliases/champion",
            headers=operator,
            json={"expectedRevision": 2, "registryVersion": 1},
        )
        assert rolled_back.status_code == 200
        assert rolled_back.json()["aliasRevision"] == 3
        resolved = client.get(
            f"/data/v3/ml/models/{first_data['datasetId']}/aliases/champion:resolve",
            headers=auditor,
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["dataVersionId"] == first_data["versionId"]
        # The already recorded run retains the first immutable resolution even
        # while the mutable alias moves and rolls back.
        recorded = client.get(
            f"/data/v3/ml/runs/{tracked.json()['experimentRunId']}",
            headers=auditor,
        )
        assert recorded.status_code == 200
        assert recorded.json()["resolvedModelInputs"] == [frozen]

        production = client.put(
            f"/data/v3/ml/models/{first_data['datasetId']}/aliases/production",
            headers=operator,
            json={"expectedRevision": 0, "registryVersion": 1},
        )
        assert production.status_code == 200
        assert client.put(
            f"/data/v3/ml/models/{first_data['datasetId']}/aliases/production",
            headers=operator,
            json={"expectedRevision": 0, "registryVersion": 1},
        ).status_code == 409

        activity = client.get("/data/v3/activity", headers=operator)
        assert activity.status_code == 200, activity.text
        activity_items = activity.json()["items"]
        assert any(item["kind"] == "ml_run" for item in activity_items)
        assert any(
            item["kind"] == "model_alias"
            and item["details"]["alias"] == "production"
            for item in activity_items
        )

        database = Database(settings.dsn)
        with database.transaction() as connection:
            events = connection.execute(
                "SELECT count(*) AS count FROM outbox_events WHERE topic='mlflow'"
            ).fetchone()["count"]
            stages = connection.execute(
                """SELECT to_stage FROM model_stage_events
                   WHERE model_id=%s AND registry_version=1 ORDER BY sequence""",
                (first_data["datasetId"],),
            ).fetchall()
        assert events >= 10
        assert stages == []
