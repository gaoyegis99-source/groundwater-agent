"""Versioned quality governance backed by the existing ``table.validate`` operator."""

from __future__ import annotations

from psycopg.types.json import Jsonb

from . import access
from .adapters.database import digest, identity
from .errors import DataProblemError
from .outbox import enqueue as enqueue_outbox


def _suite_version(connection, project: str, suite_version_id: str) -> dict:
    row = connection.execute(
        """SELECT id,project,suite_id,version_number,title,rules,body_digest,
                  actor,created_at
           FROM quality_suite_versions WHERE project=%s AND id=%s""",
        (project, suite_version_id),
    ).fetchone()
    if row is None:
        raise DataProblemError("QUALITY_SUITE_VERSION_NOT_FOUND", 404)
    return row


def _suite_wire(row: dict) -> dict:
    constraints = [rule["constraint"] for rule in row["rules"]]
    return {
        "suite_id": row["suite_id"],
        "suite_version_id": row["id"],
        "version_number": row["version_number"],
        "title": row["title"],
        "rules": row["rules"],
        "table_validation_rules": {
            "schemaVersion": "table-validation-rules.v1",
            "constraints": constraints,
        },
        "body_digest": row["body_digest"],
        "actor": row["actor"],
        "created_at": row["created_at"],
    }


def create_suite_version(
    connection, project: str, suite_id: str, body, actor: dict
) -> tuple[dict, bool]:
    rules = body.model_dump(by_alias=True, mode="json")["rules"]
    body_digest = digest({"title": body.title, "rules": rules})
    existing = connection.execute(
        """SELECT id,project,suite_id,version_number,title,rules,body_digest,
                  actor,created_at
           FROM quality_suite_versions
           WHERE project=%s AND suite_id=%s AND body_digest=%s""",
        (project, suite_id, body_digest),
    ).fetchone()
    if existing is not None:
        return _suite_wire(existing), False

    connection.execute(
        """INSERT INTO quality_suites(project,suite_id,title,owner_subject)
           VALUES(%s,%s,%s,%s) ON CONFLICT(project,suite_id) DO NOTHING""",
        (project, suite_id, body.title, actor["subject"]),
    )
    suite = connection.execute(
        """SELECT * FROM quality_suites
           WHERE project=%s AND suite_id=%s FOR UPDATE""",
        (project, suite_id),
    ).fetchone()
    if suite["latest_version_id"] != body.expected_latest_version_id:
        raise DataProblemError(
            "QUALITY_SUITE_VERSION_CONFLICT",
            409,
            currentVersionId=suite["latest_version_id"],
        )
    version_id = identity()
    version_number = suite["latest_version_number"] + 1
    row = connection.execute(
        """INSERT INTO quality_suite_versions(
             id,project,suite_id,version_number,title,rules,body_digest,actor
           ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
        (
            version_id,
            project,
            suite_id,
            version_number,
            body.title,
            Jsonb(rules),
            body_digest,
            Jsonb(actor),
        ),
    ).fetchone()
    connection.execute(
        """UPDATE quality_suites
           SET title=%s,latest_version_id=%s,latest_version_number=%s
           WHERE project=%s AND suite_id=%s""",
        (body.title, version_id, version_number, project, suite_id),
    )
    return _suite_wire(row), True


def get_suite_version(connection, project: str, suite_version_id: str) -> dict:
    return _suite_wire(_suite_version(connection, project, suite_version_id))


def _binding_wire(row: dict) -> dict:
    return {
        "dataset_id": row["dataset_id"],
        "revision": row["revision"],
        "suite_version_id": row["suite_version_id"],
        "enabled": row["enabled"],
        "actor": row["actor"],
        "created_at": row["created_at"],
    }


def get_binding(connection, project: str, dataset_id: str, actor: dict) -> dict:
    access.require_dataset(connection, project, dataset_id, actor)
    row = connection.execute(
        """SELECT * FROM quality_bindings
           WHERE project=%s AND dataset_id=%s
           ORDER BY revision DESC LIMIT 1""",
        (project, dataset_id),
    ).fetchone()
    if row is None:
        raise DataProblemError("QUALITY_BINDING_NOT_FOUND", 404)
    return _binding_wire(row)


def replace_binding(
    connection, project: str, dataset_id: str, body, actor: dict
) -> dict:
    access.require_dataset(
        connection, project, dataset_id, actor, "manage", lock=True, hide=False
    )
    _suite_version(connection, project, body.suite_version_id)
    current = connection.execute(
        """SELECT * FROM quality_bindings
           WHERE project=%s AND dataset_id=%s
           ORDER BY revision DESC LIMIT 1""",
        (project, dataset_id),
    ).fetchone()
    revision = 0 if current is None else current["revision"]
    if revision != body.expected_revision:
        raise DataProblemError(
            "QUALITY_BINDING_CONFLICT", 409, currentRevision=revision
        )
    row = connection.execute(
        """INSERT INTO quality_bindings(
             project,dataset_id,revision,suite_version_id,enabled,actor
           ) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *""",
        (
            project,
            dataset_id,
            revision + 1,
            body.suite_version_id,
            body.enabled,
            Jsonb(actor),
        ),
    ).fetchone()
    return _binding_wire(row)


def _derive_status(rules: dict[str, dict], results: dict[str, object]) -> str:
    if any(result.status == "ERROR" for result in results.values()):
        return "ERROR"
    failed = [
        rule_id for rule_id, result in results.items() if result.status == "FAIL"
    ]
    if any(rules[rule_id]["severity"] == "ERROR" for rule_id in failed):
        return "FAILED"
    if failed:
        return "PASSED_WITH_WARNINGS"
    return "PASSED"


def _run_wire(connection, row: dict) -> dict:
    results = connection.execute(
        """SELECT rule_id,severity,status,violation_count,code,evidence
           FROM quality_rule_results WHERE run_id=%s ORDER BY rule_id""",
        (row["id"],),
    ).fetchall()
    return {
        "quality_run_id": row["id"],
        "dataset_id": row["dataset_id"],
        "version_id": row["version_id"],
        "semantic_id": row["semantic_id"],
        "binding_revision": row["binding_revision"],
        "suite_version_id": row["suite_version_id"],
        "operator_run_id": row["operator_run_id"],
        "operator_id": row["operator_id"],
        "operator_version": row["operator_version"],
        "implementation_digest": row["implementation_digest"],
        "status": row["status"],
        "summary": row["summary"],
        "results": results,
        "actor": row["actor"],
        "created_at": row["created_at"],
    }


def submit_run(connection, project: str, body, actor: dict) -> tuple[dict, bool]:
    request_digest = digest(body.model_dump(by_alias=True, mode="json"))
    replay = connection.execute(
        "SELECT * FROM quality_runs WHERE project=%s AND idempotency_key=%s",
        (project, body.idempotency_key),
    ).fetchone()
    if replay is not None:
        if replay["request_digest"] != request_digest:
            raise DataProblemError("IDEMPOTENCY_CONFLICT", 409)
        return _run_wire(connection, replay), False

    resource = access.require_version(
        connection, project, body.version_id, actor, "modify", hide=False
    )
    if resource["id"] != body.dataset_id:
        raise DataProblemError("QUALITY_DATASET_VERSION_MISMATCH", 422)
    semantic = connection.execute(
        "SELECT id FROM semantic_revisions WHERE id=%s AND version_id=%s",
        (body.semantic_id, body.version_id),
    ).fetchone()
    if semantic is None:
        raise DataProblemError("SEMANTIC_REVISION_NOT_FOUND", 404)
    binding = connection.execute(
        """SELECT * FROM quality_bindings
           WHERE project=%s AND dataset_id=%s
           ORDER BY revision DESC LIMIT 1""",
        (project, body.dataset_id),
    ).fetchone()
    if binding is None:
        raise DataProblemError("QUALITY_BINDING_NOT_FOUND", 404)
    if binding["revision"] != body.binding_revision:
        raise DataProblemError(
            "QUALITY_BINDING_CONFLICT",
            409,
            currentRevision=binding["revision"],
        )
    if not binding["enabled"]:
        raise DataProblemError("QUALITY_BINDING_DISABLED", 409)
    suite = _suite_version(connection, project, binding["suite_version_id"])
    rules = {rule["constraint"]["id"]: rule for rule in suite["rules"]}
    results = {result.rule_id: result for result in body.results}
    if set(results) != set(rules):
        raise DataProblemError(
            "QUALITY_RESULTS_INCOMPLETE",
            422,
            missing=sorted(set(rules) - set(results)),
            unexpected=sorted(set(results) - set(rules)),
        )
    status = _derive_status(rules, results)
    violation_count = sum(result.violation_count for result in results.values())
    summary = {
        "ruleCount": len(rules),
        "passedRuleCount": sum(result.status == "PASS" for result in results.values()),
        "failedRuleCount": sum(result.status == "FAIL" for result in results.values()),
        "erroredRuleCount": sum(result.status == "ERROR" for result in results.values()),
        "violationCount": violation_count,
        "severityCounts": {
            severity: sum(
                result.status != "PASS" and rules[rule_id]["severity"] == severity
                for rule_id, result in results.items()
            )
            for severity in ("ERROR", "WARN", "INFO")
        },
    }
    run_id = identity()
    row = connection.execute(
        """INSERT INTO quality_runs(
             id,project,dataset_id,version_id,semantic_id,binding_revision,
             suite_version_id,idempotency_key,request_digest,operator_run_id,
             operator_id,operator_version,implementation_digest,status,summary,actor
           ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           RETURNING *""",
        (
            run_id,
            project,
            body.dataset_id,
            body.version_id,
            body.semantic_id,
            body.binding_revision,
            binding["suite_version_id"],
            body.idempotency_key,
            request_digest,
            body.operator_run_id,
            body.operator_id,
            body.operator_version,
            body.implementation_digest,
            status,
            Jsonb(summary),
            Jsonb(actor),
        ),
    ).fetchone()
    for rule_id in sorted(results):
        result = results[rule_id]
        severity = rules[rule_id]["severity"]
        connection.execute(
            """INSERT INTO quality_rule_results(
                 run_id,rule_id,severity,status,violation_count,code,evidence
               ) VALUES(%s,%s,%s,%s,%s,%s,%s)""",
            (
                run_id,
                rule_id,
                severity,
                result.status,
                result.violation_count,
                result.code,
                Jsonb(result.evidence),
            ),
        )
        if result.status == "PASS":
            continue
        alert_id = identity()
        connection.execute(
            """INSERT INTO quality_alerts(
                 id,project,dataset_id,version_id,run_id,rule_id,severity,status,
                 violation_count
               ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                alert_id,
                project,
                body.dataset_id,
                body.version_id,
                run_id,
                rule_id,
                severity,
                result.status,
                result.violation_count,
            ),
        )
        enqueue_outbox(
            connection,
            project=project,
            topic="quality.alerts",
            aggregate_type="QUALITY_ALERT",
            aggregate_id=alert_id,
            event_type="quality.alert.created",
            payload={
                "qualityAlertId": alert_id,
                "qualityRunId": run_id,
                "datasetId": body.dataset_id,
                "versionId": body.version_id,
                "ruleId": rule_id,
                "severity": severity,
                "status": result.status,
                "violationCount": result.violation_count,
            },
            idempotency_key=f"quality-alert:{run_id}:{rule_id}",
        )
    enqueue_outbox(
        connection,
        project=project,
        topic="quality.runs",
        aggregate_type="QUALITY_RUN",
        aggregate_id=run_id,
        event_type="quality.run.completed",
        payload={
            "qualityRunId": run_id,
            "datasetId": body.dataset_id,
            "versionId": body.version_id,
            "semanticId": body.semantic_id,
            "suiteVersionId": binding["suite_version_id"],
            "status": status,
            "summary": summary,
        },
        idempotency_key=f"quality-run:{run_id}",
    )
    return _run_wire(connection, row), True


def get_run(connection, project: str, run_id: str, actor: dict) -> dict:
    row = connection.execute(
        "SELECT * FROM quality_runs WHERE project=%s AND id=%s",
        (project, run_id),
    ).fetchone()
    if row is None:
        raise DataProblemError("QUALITY_RUN_NOT_FOUND", 404)
    access.require_dataset(connection, project, row["dataset_id"], actor)
    return _run_wire(connection, row)


def list_runs(
    connection, project: str, dataset_id: str, actor: dict, offset: int, limit: int
) -> dict:
    access.require_dataset(connection, project, dataset_id, actor)
    rows = connection.execute(
        """SELECT * FROM quality_runs
           WHERE project=%s AND dataset_id=%s
           ORDER BY created_at DESC,id DESC OFFSET %s LIMIT %s""",
        (project, dataset_id, offset, limit + 1),
    ).fetchall()
    return {
        "items": [_run_wire(connection, row) for row in rows[:limit]],
        "next_cursor": str(offset + limit) if len(rows) > limit else None,
    }


def list_alerts(
    connection, project: str, dataset_id: str, actor: dict, offset: int, limit: int
) -> dict:
    access.require_dataset(connection, project, dataset_id, actor)
    rows = connection.execute(
        """SELECT id AS quality_alert_id,dataset_id,version_id,
                  run_id AS quality_run_id,rule_id,severity,status,
                  violation_count,created_at
           FROM quality_alerts WHERE project=%s AND dataset_id=%s
           ORDER BY created_at DESC,id DESC OFFSET %s LIMIT %s""",
        (project, dataset_id, offset, limit + 1),
    ).fetchall()
    return {
        "items": rows[:limit],
        "next_cursor": str(offset + limit) if len(rows) > limit else None,
    }
