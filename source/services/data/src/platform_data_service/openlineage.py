"""Lossless projection of V6 publication lineage into OpenLineage events."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from .outbox import enqueue


PRODUCER = "https://platform.local/contracts/v6"
FACET_SCHEMA = "https://platform.local/contracts/openlineage/v6-data-version.json"


def _time(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _run_id(publication_id: str) -> str:
    try:
        return str(UUID(publication_id))
    except ValueError:
        return str(UUID(bytes=publication_id.encode("utf-8")[:16].ljust(16, b"\0")))


def _facet(**values) -> dict:
    return {"_producer": PRODUCER, "_schemaURL": FACET_SCHEMA, **values}


def _dataset(namespace: str, row: dict) -> dict:
    descriptor = row.get("descriptor") or {}
    checksums = sorted(
        {
            item.get("checksum")
            for item in descriptor.get("objects", [])
            if isinstance(item, dict) and item.get("checksum")
        }
    )
    return {
        "namespace": namespace,
        "name": row["dataset_id"],
        "facets": {
            "version": {
                "_producer": PRODUCER,
                "_schemaURL": (
                    "https://openlineage.io/spec/facets/1-0-0/"
                    "DatasetVersionDatasetFacet.json"
                ),
                "datasetVersion": row["version_id"],
            },
            "v6_data_version": _facet(
                versionId=row["version_id"],
                semanticRevisionId=row["semantic_id"],
                checksums=checksums,
                qualitySummary=row.get("quality_summary"),
            ),
        },
    }


def _event(
    *,
    project: str,
    publication: dict,
    event_type: str,
    inputs: list[dict],
    outputs: list[dict],
    error: dict | None = None,
) -> dict:
    namespace = f"v6://data/{project}"
    run_facets = {
        "v6_publication": _facet(
            publicationId=publication["id"],
            publicationKind=publication["publication_kind"],
            manifestDigest=publication["digest"],
        )
    }
    if error is not None:
        run_facets["errorMessage"] = {
            "_producer": PRODUCER,
            "_schemaURL": (
                "https://openlineage.io/spec/facets/1-0-0/"
                "ErrorMessageRunFacet.json"
            ),
            "message": error["code"],
            "programmingLanguage": "python",
            "stackTrace": "",
        }
    return {
        "eventType": event_type,
        "eventTime": _time(),
        "run": {"runId": _run_id(publication["id"]), "facets": run_facets},
        "job": {
            "namespace": namespace,
            "name": "data.publication",
            "facets": {},
        },
        "inputs": [_dataset(namespace, item) for item in inputs],
        "outputs": [_dataset(namespace, item) for item in outputs],
        "producer": PRODUCER,
        "schemaURL": "https://openlineage.io/spec/2-0-2/OpenLineage.json",
    }


def _inputs(connection, publication: dict) -> list[dict]:
    identifiers = publication["manifest"].get("input_versions", [])
    if not identifiers:
        return []
    return connection.execute(
        """SELECT version.id AS version_id,version.semantic_id,
                  version.descriptor,dataset.id AS dataset_id
           FROM versions AS version
           JOIN datasets AS dataset ON dataset.id=version.dataset_id
           WHERE version.id=ANY(%s) ORDER BY version.id""",
        (identifiers,),
    ).fetchall()


def _outputs(connection, publication_id: str) -> list[dict]:
    rows = connection.execute(
        """SELECT version.id AS version_id,version.semantic_id,
                  version.descriptor,dataset.id AS dataset_id
           FROM versions AS version
           JOIN datasets AS dataset ON dataset.id=version.dataset_id
           WHERE version.publication_id=%s ORDER BY version.id""",
        (publication_id,),
    ).fetchall()
    for row in rows:
        objects = connection.execute(
            "SELECT descriptor FROM objects WHERE version_id=%s ORDER BY id",
            (row["version_id"],),
        ).fetchall()
        row["descriptor"] = {
            **(row["descriptor"] or {}),
            "objects": [item["descriptor"] for item in objects],
        }
        quality = connection.execute(
            """SELECT summary FROM quality_runs
               WHERE version_id=%s ORDER BY created_at DESC,id DESC LIMIT 1""",
            (row["version_id"],),
        ).fetchone()
        row["quality_summary"] = None if quality is None else quality["summary"]
    return rows


def record_start(connection, project: str, publication: dict) -> None:
    payload = _event(
        project=project,
        publication=publication,
        event_type="START",
        inputs=_inputs(connection, publication),
        outputs=[],
    )
    enqueue(
        connection,
        project=project,
        topic="openlineage",
        aggregate_type="PUBLICATION",
        aggregate_id=publication["id"],
        event_type="openlineage.START",
        payload=payload,
        idempotency_key=f"openlineage:{publication['id']}:START",
    )


def record_complete(connection, project: str, publication: dict) -> None:
    payload = _event(
        project=project,
        publication=publication,
        event_type="COMPLETE",
        inputs=_inputs(connection, publication),
        outputs=_outputs(connection, publication["id"]),
    )
    enqueue(
        connection,
        project=project,
        topic="openlineage",
        aggregate_type="PUBLICATION",
        aggregate_id=publication["id"],
        event_type="openlineage.COMPLETE",
        payload=payload,
        idempotency_key=f"openlineage:{publication['id']}:COMPLETE",
    )


def record_fail(
    connection, project: str, publication: dict, code: str
) -> None:
    payload = _event(
        project=project,
        publication=publication,
        event_type="FAIL",
        inputs=_inputs(connection, publication),
        outputs=[],
        error={"code": code},
    )
    enqueue(
        connection,
        project=project,
        topic="openlineage",
        aggregate_type="PUBLICATION",
        aggregate_id=publication["id"],
        event_type="openlineage.FAIL",
        payload=payload,
        idempotency_key=f"openlineage:{publication['id']}:FAIL",
    )
