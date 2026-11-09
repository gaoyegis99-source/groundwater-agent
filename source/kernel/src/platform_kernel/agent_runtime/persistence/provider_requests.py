from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from ...database import connect_database, duration_ms, utc_now
from ...errors import ResourceNotFoundError


_LOGGER = logging.getLogger(__name__)


def list_provider_requests(
    database: Path, agent_run_id: str, project_id: str,
) -> tuple[dict[str, object], ...]:
    """Project durable provider attempts without request or response bodies."""
    with connect_database(database) as connection:
        row = connection.execute(
            "SELECT 1 FROM agent_runs WHERE agent_run_id=? AND project_id=?",
            (agent_run_id, project_id),
        ).fetchone()
        if row is None:
            raise ResourceNotFoundError("AGENT_RUN_NOT_FOUND")
        records = connection.execute(
            """SELECT provider_request_id,stage,provider,model,protocol,attempt,
                      outcome,error_class,input_tokens,output_tokens,total_tokens,
                      latency_ms,created_at
               FROM provider_requests WHERE agent_run_id=?
               ORDER BY created_at,provider_request_id""",
            (agent_run_id,),
        ).fetchall()
    items = []
    for record in records:
        item = dict(record)
        if item["outcome"] in {"STARTED", "UNKNOWN"}:
            # Stored zeroes are placeholders before Provider accounting arrives,
            # not evidence that the external request consumed no tokens or time.
            for field in ("input_tokens", "output_tokens", "total_tokens", "latency_ms"):
                item[field] = None
        items.append(item)
    return tuple(items)


def provider_latency_metrics(database: Path) -> dict[str, float]:
    with connect_database(database) as connection:
        records = connection.execute(
            "SELECT stage,avg(latency_ms) AS latency FROM provider_requests "
            "WHERE outcome IN ('SUCCEEDED','FAILED') GROUP BY stage"
        ).fetchall()
    return {record["stage"]: float(record["latency"]) for record in records}


def provider_unknown_metrics(database: Path) -> dict[str, float]:
    with connect_database(database) as connection:
        row = connection.execute(
            "SELECT count(*) AS count,min(created_at) AS oldest "
            "FROM provider_requests WHERE outcome='UNKNOWN'"
        ).fetchone()
    oldest = row["oldest"]
    return {
        "count": float(row["count"]),
        "oldest_age_seconds": (
            max(0.0, duration_ms(oldest, utc_now()) / 1000.0)
            if oldest is not None else 0.0
        ),
    }


def recover_provider_requests(database: Path) -> int:
    """An unacknowledged Provider call has unknown outcome after process loss."""
    with connect_database(database) as connection:
        updated = connection.execute(
            """UPDATE provider_requests SET outcome='UNKNOWN',
               error_class='PROCESS_INTERRUPTED'
               WHERE outcome='STARTED'"""
        )
        return updated.rowcount


def begin_provider_request(
    database: Path, *, agent_run_id: str, stage: str, provider: str,
    model: str, protocol: str, attempt: int,
) -> str:
    """Persist one body-free Provider attempt before network dispatch."""
    provider_request_id = str(uuid4())
    with connect_database(database) as connection:
        connection.execute(
            """INSERT INTO provider_requests(
                   provider_request_id,agent_run_id,stage,provider,model,protocol,
                   attempt,outcome,error_class,input_tokens,output_tokens,total_tokens,
                   latency_ms,created_at
               ) VALUES(?,?,?,?,?,?,?,'STARTED',NULL,0,0,0,0,?)""",
            (
                provider_request_id, agent_run_id, stage, provider, model,
                protocol, attempt, utc_now(),
            ),
        )
    return provider_request_id


def finish_provider_request(
    database: Path, provider_request_id: str, *, outcome: str,
    error_class: str | None, input_tokens: int, output_tokens: int,
    total_tokens: int, latency_ms: int,
) -> None:
    with connect_database(database) as connection:
        connection.execute(
            """UPDATE provider_requests SET outcome=?,error_class=?,
                   input_tokens=?,output_tokens=?,total_tokens=?,latency_ms=?
               WHERE provider_request_id=? AND outcome='STARTED'""",
            (
                outcome, error_class, input_tokens, output_tokens, total_tokens,
                latency_ms, provider_request_id,
            ),
        )


def mark_provider_protocol_failure(
    database: Path, agent_run_id: str,
) -> tuple[str, int] | None:
    """Fail the most recent attempt and return its stable stage/attempt."""
    with connect_database(database) as connection:
        row = connection.execute(
            """SELECT provider_request_id,stage,attempt,outcome FROM provider_requests
               WHERE agent_run_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1""",
            (agent_run_id,),
        ).fetchone()
        if row is None or row["attempt"] not in {1, 2} or row["outcome"] == "UNKNOWN":
            return None
        connection.execute(
            """UPDATE provider_requests SET outcome='FAILED',
                   error_class='PROTOCOL_OR_SCHEMA_INVALID'
               WHERE provider_request_id=? AND outcome<>'UNKNOWN'""",
            (row["provider_request_id"],),
        )
    return row["stage"], row["attempt"]
