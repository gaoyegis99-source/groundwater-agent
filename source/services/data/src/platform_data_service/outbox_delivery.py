"""Leased, retryable delivery of transactional outbox events."""

from __future__ import annotations

import httpx


class OutboxDispatcher:
    def __init__(self, settings, database) -> None:
        self.database = database
        self.destinations = {
            topic: endpoint
            for topic, endpoint in (
                ("openlineage", settings.openlineage_url),
                ("quality.alerts", settings.quality_webhook_url),
                ("quality.runs", settings.quality_webhook_url),
                ("mlflow", settings.mlflow_adapter_url),
            )
            if endpoint is not None
        }
        self.client = httpx.Client(timeout=5.0, trust_env=False)

    def close(self) -> None:
        self.client.close()

    def once(self) -> bool:
        if not self.destinations:
            return False
        topics = sorted(self.destinations)
        with self.database.transaction() as connection:
            row = connection.execute(
                """SELECT * FROM outbox_events
                   WHERE topic=ANY(%s)
                     AND (
                       (state IN ('PENDING','FAILED') AND next_attempt_at<=now())
                       OR (state='DELIVERING' AND locked_until<now())
                     )
                   ORDER BY created_at,id
                   FOR UPDATE SKIP LOCKED LIMIT 1""",
                (topics,),
            ).fetchone()
            if row is None:
                return False
            row = connection.execute(
                """UPDATE outbox_events
                   SET state='DELIVERING',attempts=attempts+1,
                       locked_until=now()+interval '30 seconds',last_error=NULL
                   WHERE id=%s RETURNING *""",
                (row["id"],),
            ).fetchone()
        error: str | None = None
        try:
            response = self.client.post(
                self.destinations[row["topic"]],
                json=row["payload"],
                headers={
                    "Idempotency-Key": row["idempotency_key"],
                    "X-Platform-Event-Type": row["event_type"],
                },
            )
            if not 200 <= response.status_code < 300:
                error = f"HTTP_{response.status_code}"
        except httpx.HTTPError as cause:
            error = type(cause).__name__
        with self.database.transaction() as connection:
            if error is None:
                connection.execute(
                    """UPDATE outbox_events
                       SET state='DELIVERED',delivered_at=now(),locked_until=NULL
                       WHERE id=%s AND state='DELIVERING'""",
                    (row["id"],),
                )
            else:
                connection.execute(
                    """UPDATE outbox_events
                       SET state='FAILED',locked_until=NULL,last_error=%s,
                           next_attempt_at=now()+LEAST(300,power(2,attempts))
                             * interval '1 second'
                       WHERE id=%s AND state='DELIVERING'""",
                    (error, row["id"]),
                )
        return True
