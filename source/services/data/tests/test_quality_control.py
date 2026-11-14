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


def publish(client: TestClient, worker, actor: dict[str, str]) -> dict:
    content = b"well,value\n001,2\n002,4\n"
    response = client.post(
        "/data/v3/publications",
        headers=actor,
        json={
            "idempotencyKey": "quality-" + uuid4().hex,
            "outputs": [
                {
                    "name": "table",
                    "title": "Quality source",
                    "format": "csv",
                    "policy": "FILE_QUERYABLE",
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    publication_id = response.json()["publicationId"]
    response = client.put(
        f"/data/v3/publications/{publication_id}/items/table",
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


def suite_body(expected=None) -> dict:
    return {
        "expectedLatestVersionId": expected,
        "title": "Monitoring table checks",
        "rules": [
            {
                "severity": "ERROR",
                "constraint": {
                    "id": "well-required",
                    "kind": "notNull",
                    "field": "well",
                },
            },
            {
                "severity": "WARN",
                "constraint": {
                    "id": "value-range",
                    "kind": "range",
                    "field": "value",
                    "minimum": 0,
                    "maximum": 100,
                },
            },
            {
                "severity": "INFO",
                "constraint": {
                    "id": "well-unique",
                    "kind": "unique",
                    "fields": ["well"],
                },
            },
        ],
    }


def test_quality_suite_binding_complete_run_alert_and_replay(settings, worker) -> None:
    trusted = replace(settings, identity_mode="gateway")
    operator = headers("operator@example.test", "researcher,admin")
    auditor = headers("auditor@example.test", "viewer")
    with TestClient(create_app(trusted)) as client:
        output = publish(client, worker, operator)
        created = client.post(
            "/data/v3/quality/suites/monitoring/versions",
            headers=operator,
            json=suite_body(),
        )
        assert created.status_code == 201, created.text
        suite = created.json()
        assert suite["versionNumber"] == 1
        assert suite["tableValidationRules"] == {
            "schemaVersion": "table-validation-rules.v1",
            "constraints": [rule["constraint"] for rule in suite_body()["rules"]],
        }
        replay = client.post(
            "/data/v3/quality/suites/monitoring/versions",
            headers=operator,
            json=suite_body(),
        )
        assert replay.status_code == 200
        assert replay.json()["suiteVersionId"] == suite["suiteVersionId"]

        binding = client.put(
            f"/data/v3/datasets/{output['datasetId']}/quality-binding",
            headers=operator,
            json={
                "expectedRevision": 0,
                "suiteVersionId": suite["suiteVersionId"],
                "enabled": True,
            },
        )
        assert binding.status_code == 200, binding.text
        assert binding.json()["revision"] == 1
        stale = client.put(
            f"/data/v3/datasets/{output['datasetId']}/quality-binding",
            headers=operator,
            json={
                "expectedRevision": 0,
                "suiteVersionId": suite["suiteVersionId"],
                "enabled": True,
            },
        )
        assert stale.status_code == 409

        request = {
            "idempotencyKey": "quality-run-1",
            "datasetId": output["datasetId"],
            "versionId": output["versionId"],
            "semanticId": output["semanticId"],
            "bindingRevision": 1,
            "operatorRunId": "executor-run-42",
            "operatorId": "table.validate",
            "operatorVersion": "1.0.0",
            "implementationDigest": "sha256:" + "0" * 64,
            "results": [
                {
                    "ruleId": "well-required",
                    "status": "PASS",
                    "violationCount": 0,
                },
                {
                    "ruleId": "value-range",
                    "status": "FAIL",
                    "violationCount": 2,
                    "code": "CONSTRAINT_VIOLATION",
                    "evidence": {"violationsChecksum": "sha256:" + "1" * 64},
                },
                {
                    "ruleId": "well-unique",
                    "status": "PASS",
                    "violationCount": 0,
                },
            ],
        }
        incomplete = client.post(
            "/data/v3/quality/runs",
            headers=operator,
            json={**request, "idempotencyKey": "incomplete", "results": request["results"][:1]},
        )
        assert incomplete.status_code == 422
        assert incomplete.json()["code"] == "QUALITY_RESULTS_INCOMPLETE"

        run = client.post("/data/v3/quality/runs", headers=operator, json=request)
        assert run.status_code == 201, run.text
        result = run.json()
        assert result["status"] == "PASSED_WITH_WARNINGS"
        discovery = client.post("/data/v3/discovery", headers=operator, json={})
        assert discovery.status_code == 200, discovery.text
        assert discovery.json()["items"][0]["quality"] == {
            "qualityRunId": result["qualityRunId"],
            "suiteVersionId": suite["suiteVersionId"],
            "status": "PASSED_WITH_WARNINGS",
        }
        assert result["summary"] == {
            "ruleCount": 3,
            "passedRuleCount": 2,
            "failedRuleCount": 1,
            "erroredRuleCount": 0,
            "violationCount": 2,
            "severityCounts": {"ERROR": 0, "WARN": 1, "INFO": 0},
        }
        assert [item["ruleId"] for item in result["results"]] == [
            "value-range",
            "well-required",
            "well-unique",
        ]
        replay = client.post("/data/v3/quality/runs", headers=operator, json=request)
        assert replay.status_code == 200
        assert replay.json()["qualityRunId"] == result["qualityRunId"]

        runs = client.get(
            f"/data/v3/datasets/{output['datasetId']}/quality-runs",
            headers=auditor,
        )
        assert runs.status_code == 200, runs.text
        assert [item["qualityRunId"] for item in runs.json()["items"]] == [
            result["qualityRunId"]
        ]
        alerts = client.get(
            f"/data/v3/datasets/{output['datasetId']}/quality-alerts",
            headers=auditor,
        )
        assert alerts.status_code == 200, alerts.text
        assert len(alerts.json()["items"]) == 1
        assert alerts.json()["items"][0]["ruleId"] == "value-range"
        assert client.post(
            "/data/v3/quality/runs", headers=auditor, json=request
        ).status_code == 403

        activity = client.get("/data/v3/activity", headers=operator)
        assert activity.status_code == 200, activity.text
        activity_items = activity.json()["items"]
        assert any(
            item["kind"] == "quality_run"
            and item["operationId"] == result["qualityRunId"]
            and item["state"] == "PASSED_WITH_WARNINGS"
            for item in activity_items
        )
        assert any(
            item["kind"] == "quality_alert"
            and item["details"]["ruleId"] == "value-range"
            and item["state"] == "WARNING"
            for item in activity_items
        )
        governance = client.get(
            f"/data/v3/datasets/{output['datasetId']}/governance",
            headers=operator,
        )
        assert governance.status_code == 200, governance.text
        point = governance.json()["qualityTrend"][-1]
        assert point["quality"] == {
            "source": "quality-control-plane",
            "status": "PASSED_WITH_WARNINGS",
            "qualityRunId": result["qualityRunId"],
            "suiteVersionId": suite["suiteVersionId"],
            "summary": result["summary"],
        }
        assert {item["kind"] for item in governance.json()["activity"]} >= {
            "quality_run",
            "quality_alert",
        }

        changed = client.post(
            f"/data/v3/versions/{output['versionId']}/semantics",
            headers=operator,
            json={"expectedSemanticId": output["semanticId"], "semantics": {
                "description": "Revised semantics",
            }},
        )
        assert changed.status_code == 200, changed.text
        item = client.post("/data/v3/discovery", headers=operator, json={}).json()["items"][0]
        assert item["semanticId"] == changed.json()["semanticId"]
        assert item["quality"] is None
        assert client.get(f"/data/v3/quality/runs/{result['qualityRunId']}", headers=auditor).status_code == 200

        with settings_database(settings) as connection:
            events = connection.execute(
                """SELECT event_type FROM outbox_events
                   WHERE topic LIKE 'quality.%' ORDER BY event_type"""
            ).fetchall()
        assert [event["event_type"] for event in events] == [
            "quality.alert.created",
            "quality.run.completed",
        ]


def settings_database(settings):
    from platform_data_service.adapters.database import Database

    return Database(settings.dsn).transaction()


def test_quality_contract_rejects_invalid_and_partial_rules(client) -> None:
    invalid = client.post(
        "/data/v3/quality/suites/invalid/versions",
        json={
            "expectedLatestVersionId": None,
            "title": "invalid",
            "rules": [
                {
                    "severity": "ERROR",
                    "constraint": {
                        "id": "range",
                        "kind": "range",
                        "field": "value",
                    },
                }
            ],
        },
    )
    assert invalid.status_code == 422

    duplicate = client.post(
        "/data/v3/quality/suites/duplicate/versions",
        json={
            "expectedLatestVersionId": None,
            "title": "duplicate",
            "rules": [
                {
                    "severity": severity,
                    "constraint": {
                        "id": "same",
                        "kind": "notNull",
                        "field": "value",
                    },
                }
                for severity in ("ERROR", "WARN")
            ],
        },
    )
    assert duplicate.status_code == 422
