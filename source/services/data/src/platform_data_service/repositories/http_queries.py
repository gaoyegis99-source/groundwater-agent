from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

from ..catalog import service as catalog
from ..errors import DataProblemError


def list_versions(database, project: str, dataset_id: str, after: str, limit: int):
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT v.id,v.created_at,v.semantic_id
               FROM versions v JOIN datasets d ON d.id=v.dataset_id
               WHERE d.id=%s AND d.project=%s AND v.id>%s
               ORDER BY v.id LIMIT %s""",
            (dataset_id, project, after, limit + 1),
        ).fetchall()
    return {
        "items": rows[:limit],
        "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None,
    }


def get_semantic_revision(database, project: str, version_id: str, semantic_id: str):
    with database.transaction() as connection:
        catalog.version(connection, project, version_id)
        return connection.execute(
            """SELECT id,version_id,body,actor,created_at
               FROM semantic_revisions WHERE id=%s AND version_id=%s""",
            (semantic_id, version_id),
        ).fetchone()


def list_semantic_revisions(
    database, project: str, version_id: str, after: str, limit: int
):
    with database.transaction() as connection:
        catalog.version(connection, project, version_id)
        rows = connection.execute(
            """SELECT id,actor,created_at
               FROM semantic_revisions
               WHERE version_id=%s
                 AND (%s='' OR (created_at,id) > (
                   SELECT created_at,id FROM semantic_revisions
                   WHERE id=%s AND version_id=%s
                 ))
               ORDER BY created_at,id LIMIT %s""",
            (version_id, after, after, version_id, limit + 1),
        ).fetchall()
    return {
        "items": rows[:limit],
        "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None,
    }


def get_object_descriptor(database, project: str, version_id: str, object_id: str):
    with database.transaction() as connection:
        catalog.version(connection, project, version_id)
        return connection.execute(
            "SELECT descriptor FROM objects WHERE id=%s AND version_id=%s",
            (object_id, version_id),
        ).fetchone()


def _activity_item(
    *,
    operation_id: str,
    kind: str,
    state: str,
    title: str,
    subject: str,
    created_at,
    updated_at,
    href: str,
    details: dict | None = None,
):
    return {
        "service": "data",
        "operation_id": operation_id,
        "kind": kind,
        "state": state,
        "title": title,
        "subject": subject,
        "created_at": created_at,
        "updated_at": updated_at,
        "href": href,
        "details": details or {},
    }


def _operation_href(row) -> str:
    if row["kind"] == "query":
        return f"/data/?view=query&query={quote(row['id'], safe='')}"
    if row["kind"] == "prepare":
        return (
            "/data/?view=publication&publication="
            f"{quote(row['target'], safe='')}"
        )
    version_id = (row["payload"] or {}).get("version_id")
    if version_id:
        return f"/data/?view=catalog&version={quote(version_id, safe='')}"
    return "/data/?view=catalog"


def list_operator_activity(database, project: str, limit: int):
    """Return recent project-wide work for the Operator activity center."""

    with database.transaction() as connection:
        operations = connection.execute(
            """SELECT id,kind,target,payload,state,actor,created_at,updated_at,error
               FROM operations WHERE project=%s
               ORDER BY updated_at DESC,id DESC LIMIT %s""",
            (project, limit),
        ).fetchall()
        publications = connection.execute(
            """SELECT publication.id,publication.state,publication.owner_subject,
                      publication.manifest,publication.created_at,
                      COALESCE(latest.created_at,publication.created_at) AS updated_at
               FROM publications publication
               LEFT JOIN LATERAL (
                   SELECT event.created_at FROM publication_events event
                   WHERE event.publication_id=publication.id
                   ORDER BY event.seq DESC LIMIT 1
               ) latest ON true
               WHERE publication.project=%s
               ORDER BY updated_at DESC,publication.id DESC LIMIT %s""",
            (project, limit),
        ).fetchall()
        quality_runs = connection.execute(
            """SELECT run.id,run.dataset_id,run.version_id,run.status,run.summary,
                      run.actor,run.created_at,dataset.title
               FROM quality_runs run
               JOIN datasets dataset ON dataset.id=run.dataset_id
               WHERE run.project=%s
               ORDER BY run.created_at DESC,run.id DESC LIMIT %s""",
            (project, limit),
        ).fetchall()
        quality_alerts = connection.execute(
            """SELECT alert.id,alert.dataset_id,alert.version_id,alert.run_id,
                      alert.rule_id,alert.severity,alert.status,
                      alert.violation_count,alert.created_at,run.actor,dataset.title
               FROM quality_alerts alert
               JOIN quality_runs run ON run.id=alert.run_id
               JOIN datasets dataset ON dataset.id=alert.dataset_id
               WHERE alert.project=%s
               ORDER BY alert.created_at DESC,alert.id DESC LIMIT %s""",
            (project, limit),
        ).fetchall()
        experiment_runs = connection.execute(
            """SELECT run.id,run.actor,run.created_at,experiment.name
               FROM ml_runs run
               JOIN ml_experiments experiment ON experiment.id=run.experiment_id
               WHERE run.project=%s
               ORDER BY run.created_at DESC,run.id DESC LIMIT %s""",
            (project, limit),
        ).fetchall()
        model_stages = connection.execute(
            """SELECT event.model_id,event.registry_version,event.sequence,
                      event.from_stage,event.to_stage,event.actor,event.created_at,
                      registry.data_version_id,dataset.title
               FROM model_stage_events event
               JOIN model_registry_versions registry
                 ON registry.model_id=event.model_id
                AND registry.registry_version=event.registry_version
               JOIN datasets dataset ON dataset.id=event.model_id
               WHERE registry.project=%s
               ORDER BY event.created_at DESC,event.model_id DESC,
                        event.registry_version DESC,event.sequence DESC LIMIT %s""",
            (project, limit),
        ).fetchall()
        model_aliases = connection.execute(
            """SELECT alias.model_id,alias.alias,alias.revision,
                      alias.registry_version,alias.actor,alias.created_at,
                      registry.data_version_id,dataset.title
               FROM model_alias_revisions alias
               JOIN model_registry_versions registry
                 ON registry.project=alias.project
                AND registry.model_id=alias.model_id
                AND registry.registry_version=alias.registry_version
               JOIN datasets dataset ON dataset.id=alias.model_id
               WHERE alias.project=%s
               ORDER BY alias.created_at DESC,alias.model_id DESC,
                        alias.alias DESC,alias.revision DESC LIMIT %s""",
            (project, limit),
        ).fetchall()
    labels = {
        "query": "数据查询",
        "preview": "文件预览",
        "prepare": "发布准备",
    }
    items = [
        _activity_item(
            operation_id=row["id"],
            kind=row["kind"],
            state=row["state"],
            title=f"{labels.get(row['kind'], row['kind'])} · {row['target']}",
            subject=row["actor"].get("subject", "unknown"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            href=_operation_href(row),
            details={
                "target": row["target"],
                "errorCode": (row["error"] or {}).get("code"),
            },
        )
        for row in operations
    ]
    for row in publications:
        outputs = row["manifest"].get("outputs", [])
        title = outputs[0].get("title") if outputs else None
        items.append(
            _activity_item(
                operation_id=row["id"],
                kind="publication",
                state=row["state"],
                title=title or f"数据发布 · {len(outputs)} 个输出",
                subject=row["owner_subject"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                href=(
                    "/data/?view=publication&publication="
                    f"{quote(row['id'], safe='')}"
                ),
                details={"outputCount": len(outputs)},
            )
        )
    items.extend(
        _activity_item(
            operation_id=row["id"],
            kind="quality_run",
            state=row["status"],
            title=f"质量检查 · {row['title']}",
            subject=row["actor"].get("subject", "unknown"),
            created_at=row["created_at"],
            updated_at=row["created_at"],
            href=(
                "/data/?view=catalog&version="
                f"{quote(row['version_id'], safe='')}"
            ),
            details={
                "datasetId": row["dataset_id"],
                "versionId": row["version_id"],
                "summary": row["summary"],
            },
        )
        for row in quality_runs
    )
    items.extend(
        _activity_item(
            operation_id=row["id"],
            kind="quality_alert",
            state="FAILED" if row["severity"] == "ERROR" else "WARNING",
            title=f"质量告警 {row['rule_id']} · {row['title']}",
            subject=row["actor"].get("subject", "unknown"),
            created_at=row["created_at"],
            updated_at=row["created_at"],
            href=(
                "/data/?view=catalog&version="
                f"{quote(row['version_id'], safe='')}"
            ),
            details={
                "datasetId": row["dataset_id"],
                "versionId": row["version_id"],
                "qualityRunId": row["run_id"],
                "ruleId": row["rule_id"],
                "severity": row["severity"],
                "status": row["status"],
                "violationCount": row["violation_count"],
            },
        )
        for row in quality_alerts
    )
    items.extend(
        _activity_item(
            operation_id=row["id"],
            kind="ml_run",
            state="RECORDED",
            title=f"实验运行已记录 · {row['name']}",
            subject=row["actor"].get("subject", "unknown"),
            created_at=row["created_at"],
            updated_at=row["created_at"],
            href="/data/?view=catalog",
            details={"experimentRunId": row["id"]},
        )
        for row in experiment_runs
    )
    items.extend(
        _activity_item(
            operation_id=(
                f"{row['model_id']}:{row['registry_version']}:{row['sequence']}"
            ),
            kind="model_stage",
            state=row["to_stage"],
            title=(
                f"模型版本 {row['registry_version']} 进入 "
                f"{row['to_stage']} · {row['title']}"
            ),
            subject=row["actor"].get("subject", "unknown"),
            created_at=row["created_at"],
            updated_at=row["created_at"],
            href=(
                "/data/?view=catalog&version="
                f"{quote(row['data_version_id'], safe='')}"
            ),
            details={
                "modelId": row["model_id"],
                "registryVersion": row["registry_version"],
                "fromStage": row["from_stage"],
                "toStage": row["to_stage"],
            },
        )
        for row in model_stages
    )
    items.extend(
        _activity_item(
            operation_id=(
                f"{row['model_id']}:{row['alias']}:{row['revision']}"
            ),
            kind="model_alias",
            state="RECORDED",
            title=(
                f"模型别名 {row['alias']} 指向版本 "
                f"{row['registry_version']} · {row['title']}"
            ),
            subject=row["actor"].get("subject", "unknown"),
            created_at=row["created_at"],
            updated_at=row["created_at"],
            href=(
                "/data/?view=catalog&version="
                f"{quote(row['data_version_id'], safe='')}"
            ),
            details={
                "modelId": row["model_id"],
                "alias": row["alias"],
                "aliasRevision": row["revision"],
                "registryVersion": row["registry_version"],
            },
        )
        for row in model_aliases
    )
    items.sort(
        key=lambda item: (item["updated_at"], item["operation_id"]), reverse=True
    )
    return {
        "items": items[:limit],
        "generated_at": datetime.now(timezone.utc),
    }


def _quality_trend(versions: list[dict], quality_runs: list[dict]) -> list[dict]:
    latest_by_version: dict[str, dict] = {}
    for run in quality_runs:
        latest_by_version.setdefault(run["version_id"], run)
    points = []
    for version in reversed(versions):
        run = latest_by_version.get(version["version_id"])
        quality = version["descriptor"].get("quality") or {}
        if run is not None:
            quality = {
                "source": "quality-control-plane",
                "status": run["status"],
                "qualityRunId": run["id"],
                "suiteVersionId": run["suite_version_id"],
                "summary": run["summary"],
            }
        points.append(
            {
                "version_id": version["version_id"],
                "created_at": version["created_at"],
                "row_count": version["descriptor"].get("row_count"),
                "quality": quality,
            }
        )
    return points


def get_dataset_governance(database, project: str, dataset_id: str):
    """Project a readable governance view without creating a second source of truth."""

    with database.transaction() as connection:
        dataset = connection.execute(
            """SELECT d.id,d.title,d.current_version,p.owner_subject
               FROM datasets d
               JOIN versions v ON v.id=d.current_version
               JOIN publications p ON p.id=v.publication_id
               WHERE d.id=%s AND d.project=%s""",
            (dataset_id, project),
        ).fetchone()
        if dataset is None:
            raise DataProblemError("DATASET_NOT_FOUND", 404)
        versions = connection.execute(
            """SELECT v.id AS version_id,v.created_at,v.descriptor,
                      v.publication_id,p.owner_subject
               FROM versions v
               JOIN publications p ON p.id=v.publication_id
               WHERE v.dataset_id=%s AND p.project=%s
               ORDER BY v.created_at DESC,v.id DESC LIMIT 50""",
            (dataset_id, project),
        ).fetchall()
        lineage = connection.execute(
            """SELECT output.id AS output_version_id,input.id AS input_version_id,
                      input.dataset_id AS input_dataset_id,source.title AS input_title,
                      output.created_at
               FROM lineage edge
               JOIN versions output ON output.id=edge.version_id
               JOIN datasets target ON target.id=output.dataset_id
               JOIN versions input ON input.id=edge.input_version_id
               JOIN datasets source ON source.id=input.dataset_id
               WHERE target.id=%s AND target.project=%s
               ORDER BY output.created_at DESC,output.id,input.id LIMIT 100""",
            (dataset_id, project),
        ).fetchall()
        revisions = connection.execute(
            """SELECT revision.id,revision.version_id,revision.actor,
                      revision.created_at
               FROM semantic_revisions revision
               JOIN versions v ON v.id=revision.version_id
               JOIN datasets d ON d.id=v.dataset_id
               WHERE d.id=%s AND d.project=%s
               ORDER BY revision.created_at DESC,revision.id DESC LIMIT 100""",
            (dataset_id, project),
        ).fetchall()
        publication_events = connection.execute(
            """SELECT event.seq,event.state,event.created_at,p.id AS publication_id,
                      p.owner_subject,v.id AS version_id
               FROM publication_events event
               JOIN publications p ON p.id=event.publication_id
               JOIN versions v ON v.publication_id=p.id
               JOIN datasets d ON d.id=v.dataset_id
               WHERE d.id=%s AND d.project=%s
               ORDER BY event.created_at DESC,event.seq DESC LIMIT 100""",
            (dataset_id, project),
        ).fetchall()
        quality_runs = connection.execute(
            """SELECT id,version_id,status,summary,suite_version_id,actor,created_at
               FROM quality_runs
               WHERE dataset_id=%s AND project=%s
               ORDER BY created_at DESC,id DESC LIMIT 100""",
            (dataset_id, project),
        ).fetchall()
        quality_alerts = connection.execute(
            """SELECT alert.id,alert.version_id,alert.run_id,alert.rule_id,
                      alert.severity,alert.status,alert.violation_count,
                      alert.created_at,run.actor
               FROM quality_alerts alert
               JOIN quality_runs run ON run.id=alert.run_id
               WHERE alert.dataset_id=%s AND alert.project=%s
               ORDER BY alert.created_at DESC,alert.id DESC LIMIT 100""",
            (dataset_id, project),
        ).fetchall()
    activity = [
        _activity_item(
            operation_id=row["id"],
            kind="semantic_revision",
            state="RECORDED",
            title="更新字段含义与业务语义",
            subject=row["actor"].get("subject", "unknown"),
            created_at=row["created_at"],
            updated_at=row["created_at"],
            href=f"/data/?view=catalog&version={row['version_id']}",
            details={"versionId": row["version_id"]},
        )
        for row in revisions
    ]
    activity.extend(
        _activity_item(
            operation_id=f"{row['publication_id']}:{row['seq']}",
            kind="publication",
            state=row["state"],
            title="数据发布状态变化",
            subject=row["owner_subject"],
            created_at=row["created_at"],
            updated_at=row["created_at"],
            href=f"/data/?view=catalog&version={row['version_id']}",
            details={
                "publicationId": row["publication_id"],
                "versionId": row["version_id"],
            },
        )
        for row in publication_events
    )
    activity.extend(
        _activity_item(
            operation_id=row["id"],
            kind="quality_run",
            state=row["status"],
            title="数据质量检查完成",
            subject=row["actor"].get("subject", "unknown"),
            created_at=row["created_at"],
            updated_at=row["created_at"],
            href=f"/data/?view=catalog&version={row['version_id']}",
            details={
                "versionId": row["version_id"],
                "suiteVersionId": row["suite_version_id"],
                "summary": row["summary"],
            },
        )
        for row in quality_runs
    )
    activity.extend(
        _activity_item(
            operation_id=row["id"],
            kind="quality_alert",
            state="FAILED" if row["severity"] == "ERROR" else "WARNING",
            title=f"质量告警 · {row['rule_id']}",
            subject=row["actor"].get("subject", "unknown"),
            created_at=row["created_at"],
            updated_at=row["created_at"],
            href=f"/data/?view=catalog&version={row['version_id']}",
            details={
                "versionId": row["version_id"],
                "qualityRunId": row["run_id"],
                "severity": row["severity"],
                "status": row["status"],
                "violationCount": row["violation_count"],
            },
        )
        for row in quality_alerts
    )
    activity.sort(
        key=lambda item: (item["updated_at"], item["operation_id"]), reverse=True
    )
    return {
        "dataset_id": dataset["id"],
        "title": dataset["title"],
        "current_version": dataset["current_version"],
        "owner_subject": dataset["owner_subject"],
        "quality_trend": _quality_trend(versions, quality_runs),
        "lineage": [
            {
                **row,
                "is_current": row["output_version_id"]
                == dataset["current_version"],
            }
            for row in lineage
        ],
        "activity": activity[:100],
        "generated_at": datetime.now(timezone.utc),
    }
