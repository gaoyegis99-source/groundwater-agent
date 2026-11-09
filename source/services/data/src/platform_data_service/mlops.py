"""Experiment tracking and immutable Data-backed model registry lifecycle."""

from __future__ import annotations

from psycopg.types.json import Jsonb

from . import access
from .adapters.database import digest, identity
from .errors import DataProblemError
from .outbox import enqueue


def _experiment_wire(row: dict) -> dict:
    return {
        "experiment_id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "owner_subject": row["owner_subject"],
        "actor": row["actor"],
        "created_at": row["created_at"],
    }


def create_experiment(
    connection, project: str, body, actor: dict
) -> tuple[dict, bool]:
    existing = connection.execute(
        "SELECT * FROM ml_experiments WHERE project=%s AND name=%s",
        (project, body.name),
    ).fetchone()
    if existing is not None:
        if existing["description"] != body.description:
            raise DataProblemError("ML_EXPERIMENT_CONFLICT", 409)
        return _experiment_wire(existing), False
    row = connection.execute(
        """INSERT INTO ml_experiments(
             id,project,name,description,owner_subject,actor
           ) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *""",
        (
            identity(),
            project,
            body.name,
            body.description,
            actor["subject"],
            Jsonb(actor),
        ),
    ).fetchone()
    enqueue(
        connection,
        project=project,
        topic="mlflow",
        aggregate_type="ML_EXPERIMENT",
        aggregate_id=row["id"],
        event_type="ml.experiment.created",
        payload={
            "experimentId": row["id"],
            "name": row["name"],
            "description": row["description"],
        },
        idempotency_key=f"ml-experiment:{row['id']}",
    )
    return _experiment_wire(row), True


def _resolve(
    connection, project: str, model_id: str, alias: str, actor: dict
) -> dict:
    access.require_dataset(connection, project, model_id, actor)
    row = connection.execute(
        """SELECT registry.model_id,alias.alias,alias.revision AS alias_revision,
                  registry.registry_version,registry.benchmark_status,registry.tags,
                  registry.legacy_stage,registry.data_version_id,
                  registry.artifact_object_id,registry.artifact_checksum,
                  alias.actor,alias.created_at
           FROM model_alias_revisions AS alias
           JOIN model_registry_versions AS registry
             ON registry.project=alias.project
            AND registry.model_id=alias.model_id
            AND registry.registry_version=alias.registry_version
           WHERE alias.project=%s AND alias.model_id=%s AND alias.alias=%s
           ORDER BY alias.revision DESC LIMIT 1""",
        (project, model_id, alias),
    ).fetchone()
    if row is None:
        raise DataProblemError("MODEL_ALIAS_NOT_FOUND", 404)
    return row


def resolve_alias(
    connection, project: str, model_id: str, alias: str, actor: dict
) -> dict:
    return _resolved_wire(_resolve(connection, project, model_id, alias, actor))


def _resolved_wire(row: dict, *, audit: bool = False) -> dict:
    value = {
        "model_id": row["model_id"],
        "alias": row["alias"],
        "alias_revision": row["alias_revision"],
        "registry_version": row["registry_version"],
        "benchmark_status": row["benchmark_status"],
        "tags": row["tags"],
        "legacy_stage": row["legacy_stage"],
        "data_version_id": row["data_version_id"],
        "artifact_object_id": row["artifact_object_id"],
        "artifact_checksum": row["artifact_checksum"],
    }
    if audit:
        value.update(actor=row["actor"], created_at=row["created_at"])
    return value


def _run_wire(row: dict) -> dict:
    return {
        "experiment_run_id": row["id"],
        "experiment_id": row["experiment_id"],
        "input_versions": row["input_versions"],
        "resolved_model_inputs": row["resolved_model_inputs"],
        "parameters": row["parameters"],
        "metrics": row["metrics"],
        "environment": row["environment"],
        "operator_implementations": row["operator_implementations"],
        "actor": row["actor"],
        "created_at": row["created_at"],
    }


def record_run(connection, project: str, body, actor: dict) -> tuple[dict, bool]:
    request_digest = digest(body.model_dump(by_alias=True, mode="json"))
    existing = connection.execute(
        "SELECT * FROM ml_runs WHERE project=%s AND idempotency_key=%s",
        (project, body.idempotency_key),
    ).fetchone()
    if existing is not None:
        if existing["request_digest"] != request_digest:
            raise DataProblemError("IDEMPOTENCY_CONFLICT", 409)
        return _run_wire(existing), False
    experiment = connection.execute(
        "SELECT id FROM ml_experiments WHERE project=%s AND id=%s",
        (project, body.experiment_id),
    ).fetchone()
    if experiment is None:
        raise DataProblemError("ML_EXPERIMENT_NOT_FOUND", 404)
    for version_id in body.input_versions:
        access.require_version(connection, project, version_id, actor)
    resolved = [
        _resolve(connection, project, item.model_id, item.alias, actor)
        for item in body.model_inputs
    ]
    resolved_wire = [
        {
            "modelId": item["model_id"],
            "alias": item["alias"],
            "aliasRevision": item["alias_revision"],
            "registryVersion": item["registry_version"],
            "benchmarkStatus": item["benchmark_status"],
            "tags": item["tags"],
            "legacyStage": item["legacy_stage"],
            "dataVersionId": item["data_version_id"],
            "artifactObjectId": item["artifact_object_id"],
            "artifactChecksum": item["artifact_checksum"],
        }
        for item in resolved
    ]
    run_id = identity()
    row = connection.execute(
        """INSERT INTO ml_runs(
             id,project,experiment_id,idempotency_key,request_digest,input_versions,
             resolved_model_inputs,parameters,metrics,environment,
             operator_implementations,actor
           ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
        (
            run_id,
            project,
            body.experiment_id,
            body.idempotency_key,
            request_digest,
            Jsonb(body.input_versions),
            Jsonb(resolved_wire),
            Jsonb(body.parameters),
            Jsonb(body.metrics),
            Jsonb(body.environment.model_dump(by_alias=True, mode="json")),
            Jsonb(
                [
                    item.model_dump(by_alias=True, mode="json")
                    for item in body.operator_implementations
                ]
            ),
            Jsonb(actor),
        ),
    ).fetchone()
    enqueue(
        connection,
        project=project,
        topic="mlflow",
        aggregate_type="ML_RUN",
        aggregate_id=run_id,
        event_type="ml.run.recorded",
        payload={
            "experimentRunId": run_id,
            "experimentId": body.experiment_id,
            "inputVersions": body.input_versions,
            "resolvedModelInputs": resolved_wire,
            "parameters": body.parameters,
            "metrics": body.metrics,
            "environment": body.environment.model_dump(by_alias=True, mode="json"),
            "operatorImplementations": [
                item.model_dump(by_alias=True, mode="json")
                for item in body.operator_implementations
            ],
        },
        idempotency_key=f"ml-run:{run_id}",
    )
    return _run_wire(row), True


def get_run(connection, project: str, run_id: str, actor: dict) -> dict:
    row = connection.execute(
        "SELECT * FROM ml_runs WHERE project=%s AND id=%s", (project, run_id)
    ).fetchone()
    if row is None:
        raise DataProblemError("ML_RUN_NOT_FOUND", 404)
    for version_id in row["input_versions"]:
        access.require_version(connection, project, version_id, actor)
    for model in row["resolved_model_inputs"]:
        access.require_dataset(connection, project, model["modelId"], actor)
    return _run_wire(row)


def _model_wire(row: dict) -> dict:
    return {
        "model_id": row["model_id"],
        "registry_version": row["registry_version"],
        "data_version_id": row["data_version_id"],
        "artifact_object_id": row["artifact_object_id"],
        "artifact_checksum": row["artifact_checksum"],
        "experiment_run_id": row["experiment_run_id"],
        "benchmark_status": row["benchmark_status"],
        "tags": row["tags"],
        "legacy_stage": row["legacy_stage"],
        "actor": row["actor"],
        "created_at": row["created_at"],
    }


def register_model_version(
    connection, project: str, model_id: str, body, actor: dict
) -> tuple[dict, bool]:
    resource = access.require_dataset(
        connection, project, model_id, actor, "manage", lock=True, hide=False
    )
    if resource["asset_kind"] != "MODEL":
        raise DataProblemError("RESOURCE_IS_NOT_MODEL", 422)
    version = connection.execute(
        """SELECT id,dataset_id FROM versions
           WHERE id=%s AND dataset_id=%s""",
        (body.data_version_id, model_id),
    ).fetchone()
    if version is None:
        raise DataProblemError("MODEL_DATA_VERSION_NOT_FOUND", 404)
    artifact = connection.execute(
        """SELECT id,descriptor FROM objects
           WHERE version_id=%s AND descriptor->>'format'='pytorch-state-dict'
           ORDER BY id LIMIT 1""",
        (body.data_version_id,),
    ).fetchone()
    if artifact is None:
        raise DataProblemError("MODEL_ARTIFACT_NOT_FOUND", 422)
    if body.experiment_run_id is not None:
        run = connection.execute(
            "SELECT id FROM ml_runs WHERE project=%s AND id=%s",
            (project, body.experiment_run_id),
        ).fetchone()
        if run is None:
            raise DataProblemError("ML_RUN_NOT_FOUND", 404)
    existing = connection.execute(
        """SELECT * FROM model_registry_versions
           WHERE project=%s AND model_id=%s AND data_version_id=%s""",
        (project, model_id, body.data_version_id),
    ).fetchone()
    if existing is not None:
        if existing["experiment_run_id"] != body.experiment_run_id:
            raise DataProblemError("MODEL_VERSION_CONFLICT", 409)
        return _model_wire(existing), False
    latest = connection.execute(
        """SELECT COALESCE(max(registry_version),0) AS version
           FROM model_registry_versions WHERE project=%s AND model_id=%s""",
        (project, model_id),
    ).fetchone()["version"]
    row = connection.execute(
        """INSERT INTO model_registry_versions(
             project,model_id,registry_version,data_version_id,artifact_object_id,
             artifact_checksum,experiment_run_id,actor,benchmark_status,tags
           ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
        (
            project,
            model_id,
            latest + 1,
            body.data_version_id,
            artifact["id"],
            artifact["descriptor"]["checksum"],
            body.experiment_run_id,
            Jsonb(actor),
            body.benchmark_status,
            Jsonb(body.tags),
        ),
    ).fetchone()
    _model_event(connection, project, row, "ml.model_version.registered")
    return _model_wire(row), True


def _get_model_version(
    connection, project: str, model_id: str, registry_version: int, *, lock=False
) -> dict:
    row = connection.execute(
        """SELECT * FROM model_registry_versions
           WHERE project=%s AND model_id=%s AND registry_version=%s"""
        + (" FOR UPDATE" if lock else ""),
        (project, model_id, registry_version),
    ).fetchone()
    if row is None:
        raise DataProblemError("MODEL_REGISTRY_VERSION_NOT_FOUND", 404)
    return row


def get_model_version(
    connection, project: str, model_id: str, registry_version: int, actor: dict
) -> dict:
    access.require_dataset(connection, project, model_id, actor)
    return _model_wire(
        _get_model_version(connection, project, model_id, registry_version)
    )


def update_model_benchmark(
    connection, project: str, model_id: str, registry_version: int, body, actor: dict
) -> dict:
    access.require_dataset(
        connection, project, model_id, actor, "manage", lock=True, hide=False
    )
    row = _get_model_version(
        connection, project, model_id, registry_version, lock=True
    )
    if row["benchmark_status"] != body.expected_status:
        raise DataProblemError(
            "MODEL_BENCHMARK_CONFLICT", 409,
            currentBenchmarkStatus=row["benchmark_status"],
        )
    row = connection.execute(
        """UPDATE model_registry_versions SET benchmark_status=%s,tags=%s,actor=%s
           WHERE project=%s AND model_id=%s AND registry_version=%s RETURNING *""",
        (
            body.benchmark_status,
            Jsonb(body.tags),
            Jsonb(actor),
            project,
            model_id,
            registry_version,
        ),
    ).fetchone()
    _model_event(connection, project, row, "ml.model_version.benchmark_updated")
    return _model_wire(row)


def set_alias(
    connection,
    project: str,
    model_id: str,
    alias: str,
    body,
    actor: dict,
) -> dict:
    access.require_dataset(
        connection, project, model_id, actor, "manage", lock=True, hide=False
    )
    model = _get_model_version(
        connection, project, model_id, body.registry_version
    )
    current = connection.execute(
        """SELECT * FROM model_alias_revisions
           WHERE project=%s AND model_id=%s AND alias=%s
           ORDER BY revision DESC LIMIT 1""",
        (project, model_id, alias),
    ).fetchone()
    revision = 0 if current is None else current["revision"]
    if revision != body.expected_revision:
        raise DataProblemError("MODEL_ALIAS_CONFLICT", 409, currentRevision=revision)
    alias_row = connection.execute(
        """INSERT INTO model_alias_revisions(
             project,model_id,alias,revision,registry_version,actor
           ) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *""",
        (
            project,
            model_id,
            alias,
            revision + 1,
            body.registry_version,
            Jsonb(actor),
        ),
    ).fetchone()
    resolved = {
        **model,
        "alias": alias,
        "alias_revision": alias_row["revision"],
        "actor": alias_row["actor"],
        "created_at": alias_row["created_at"],
    }
    enqueue(
        connection,
        project=project,
        topic="mlflow",
        aggregate_type="MODEL_ALIAS",
        aggregate_id=f"{model_id}:{alias}",
        event_type="ml.model_alias.updated",
        payload={
            "modelId": model_id,
            "alias": alias,
            "aliasRevision": alias_row["revision"],
            "registryVersion": body.registry_version,
            "dataVersionId": model["data_version_id"],
            "artifactChecksum": model["artifact_checksum"],
        },
        idempotency_key=(
            f"ml-alias:{model_id}:{alias}:{alias_row['revision']}"
        ),
    )
    return _resolved_wire(resolved, audit=True)


def _model_event(connection, project: str, row: dict, event_type: str) -> None:
    enqueue(
        connection,
        project=project,
        topic="mlflow",
        aggregate_type="MODEL_VERSION",
        aggregate_id=f"{row['model_id']}:{row['registry_version']}",
        event_type=event_type,
        payload={
            "modelId": row["model_id"],
            "registryVersion": row["registry_version"],
            "dataVersionId": row["data_version_id"],
            "artifactObjectId": row["artifact_object_id"],
            "artifactChecksum": row["artifact_checksum"],
            "experimentRunId": row["experiment_run_id"],
            "benchmarkStatus": row["benchmark_status"],
            "tags": row["tags"],
            "legacyStage": row["legacy_stage"],
        },
        idempotency_key=(
            f"{event_type}:{row['model_id']}:{row['registry_version']}:"
            f"{row['benchmark_status']}:{digest(row['tags'])}"
        ),
    )
