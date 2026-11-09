from dataclasses import replace
import hashlib
from uuid import uuid4

import httpx

from platform_data_service.adapters.database import Database
from platform_data_service.outbox_delivery import OutboxDispatcher


def publish(client, worker):
    content = b"well,value\n001,2\n"
    response = client.post(
        "/data/v3/publications",
        json={
            "idempotencyKey": "lineage-" + uuid4().hex,
            "outputs": [
                {
                    "name": "table",
                    "title": "Lineage table",
                    "format": "csv",
                    "policy": "FILE_QUERYABLE",
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    publication_id = response.json()["publicationId"]
    assert client.put(
        f"/data/v3/publications/{publication_id}/items/table",
        content=content,
        headers={
            "X-Content-SHA256": "sha256:" + hashlib.sha256(content).hexdigest()
        },
    ).status_code == 200
    assert client.post(
        f"/data/v3/publications/{publication_id}/prepare"
    ).status_code == 202
    assert worker.once()
    response = client.post(f"/data/v3/publications/{publication_id}/commit")
    assert response.status_code == 200, response.text
    return publication_id, response.json()["outputs"][0]


def test_publication_writes_openlineage_start_and_complete_transactionally(
    client, worker, settings
) -> None:
    publication_id, output = publish(client, worker)
    database = Database(settings.dsn)
    with database.transaction() as connection:
        rows = connection.execute(
            """SELECT event_type,payload,state FROM outbox_events
               WHERE aggregate_id=%s AND topic='openlineage'
               ORDER BY event_type""",
            (publication_id,),
        ).fetchall()
    assert [row["event_type"] for row in rows] == [
        "openlineage.COMPLETE",
        "openlineage.START",
    ]
    complete = rows[0]["payload"]
    assert complete["eventType"] == "COMPLETE"
    assert complete["run"]["facets"]["v6_publication"]["publicationId"] == publication_id
    assert complete["outputs"][0]["name"] == output["datasetId"]
    facets = complete["outputs"][0]["facets"]
    assert facets["version"]["datasetVersion"] == output["versionId"]
    assert facets["v6_data_version"]["semanticRevisionId"] == output["semanticId"]
    assert facets["v6_data_version"]["checksums"]
    assert all(row["state"] == "PENDING" for row in rows)


def test_outbox_delivery_is_idempotent_fail_open_and_reclaims_lease(
    settings, client
) -> None:
    response = client.post(
        "/data/v3/publications",
        json={
            "idempotencyKey": "delivery-" + uuid4().hex,
            "outputs": [
                {
                    "name": "table",
                    "title": "Delivery table",
                    "format": "csv",
                    "policy": "FILE_QUERYABLE",
                }
            ],
        },
    )
    assert response.status_code == 201
    publication_id = response.json()["publicationId"]
    configured = replace(settings, openlineage_url="https://lineage.example.test/events")
    database = Database(settings.dsn)
    dispatcher = OutboxDispatcher(configured, database)
    requests = []

    def fail_then_succeed(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503 if len(requests) == 1 else 200)

    dispatcher.client.close()
    dispatcher.client = httpx.Client(
        transport=httpx.MockTransport(fail_then_succeed), trust_env=False
    )
    try:
        assert dispatcher.once()
        with database.transaction() as connection:
            failed = connection.execute(
                """SELECT * FROM outbox_events
                   WHERE aggregate_id=%s AND event_type='openlineage.START'""",
                (publication_id,),
            ).fetchone()
            assert failed["state"] == "FAILED"
            assert failed["last_error"] == "HTTP_503"
            connection.execute(
                """UPDATE outbox_events SET next_attempt_at=now()-interval '1 second'
                   WHERE id=%s""",
                (failed["id"],),
            )
        assert dispatcher.once()
        with database.transaction() as connection:
            delivered = connection.execute(
                "SELECT * FROM outbox_events WHERE id=%s", (failed["id"],)
            ).fetchone()
        assert delivered["state"] == "DELIVERED"
        assert delivered["attempts"] == 2
        assert requests[0].headers["Idempotency-Key"] == requests[1].headers[
            "Idempotency-Key"
        ]
    finally:
        dispatcher.close()
