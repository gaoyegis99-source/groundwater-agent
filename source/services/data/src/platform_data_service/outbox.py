"""Transactional event outbox shared by quality and interoperability adapters."""

from psycopg.types.json import Jsonb

from .adapters.database import identity


def enqueue(
    connection,
    *,
    project: str,
    topic: str,
    aggregate_type: str,
    aggregate_id: str,
    event_type: str,
    payload: dict,
    idempotency_key: str,
) -> str:
    event_id = identity()
    row = connection.execute(
        """INSERT INTO outbox_events(
             id,project,topic,aggregate_type,aggregate_id,event_type,payload,
             idempotency_key
           ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT(idempotency_key) DO NOTHING
           RETURNING id""",
        (
            event_id,
            project,
            topic,
            aggregate_type,
            aggregate_id,
            event_type,
            Jsonb(payload),
            idempotency_key,
        ),
    ).fetchone()
    if row is not None:
        return row["id"]
    return connection.execute(
        "SELECT id FROM outbox_events WHERE idempotency_key=%s",
        (idempotency_key,),
    ).fetchone()["id"]
