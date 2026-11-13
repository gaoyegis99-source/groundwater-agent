"""Claim and recovery queue for intent-routing requests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from ..database import connect_database, scheduler_store_writable, utc_now
from ..errors import scheduler_write
from .message_request_common import IntentRouteRequest, route_from_row


class IntentRoutingMetrics(TypedDict):
    pending: int
    running: int
    oldest_wait_seconds: float


def route_store_writable(database: Path) -> bool:
    with scheduler_write():
        return scheduler_store_writable(database)


def intent_routing_metrics(database: Path) -> IntentRoutingMetrics:
    with connect_database(database) as connection:
        rows = connection.execute(
            """SELECT routing_status,count(*) AS count,min(created_at) AS oldest
                 FROM message_requests AS r
                WHERE r.requested_mode='AUTO'
                  AND r.routing_status IN ('ROUTING_REQUESTED','ROUTING_RUNNING')
                  AND NOT EXISTS (
                      SELECT 1 FROM run_controls AS stop
                       WHERE stop.agent_run_id=r.agent_run_id
                  )
                GROUP BY routing_status"""
        )
        states = {row["routing_status"]: (row["count"], row["oldest"]) for row in rows}
    pending, oldest = states.get("ROUTING_REQUESTED", (0, None))
    running, _ = states.get("ROUTING_RUNNING", (0, None))
    age = 0.0 if oldest is None else max(
        0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(oldest)).total_seconds(),
    )
    return {"pending": pending, "running": running, "oldest_wait_seconds": age}


def requested_intent_routes(
    database: Path, limit: int | None = None
) -> tuple[IntentRouteRequest, ...]:
    if limit is not None and limit < 1:
        return ()
    limit_clause = "" if limit is None else " LIMIT ?"
    parameters = () if limit is None else (limit,)
    with connect_database(database) as connection:
        rows = connection.execute(
            """SELECT r.*,s.project_id FROM message_requests AS r
               JOIN sessions AS s ON s.session_id=r.session_id
               WHERE r.requested_mode='AUTO' AND r.routing_status='ROUTING_REQUESTED'
                 AND NOT EXISTS (
                     SELECT 1 FROM run_controls AS stop
                     WHERE stop.agent_run_id=r.agent_run_id
                 )
               ORDER BY r.rowid""" + limit_clause,
            parameters,
        ).fetchall()
    return tuple(route_from_row(row) for row in rows)


def claim_intent_route(database: Path, agent_run_id: str) -> IntentRouteRequest | None:
    started_at = utc_now()
    with scheduler_write(), connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        claimed = connection.execute(
            """UPDATE message_requests
               SET routing_status='ROUTING_RUNNING',route_started_at=?,
                   route_finished_at=NULL,route_duration_ms=NULL,
                   route_attempts=route_attempts+1
               WHERE agent_run_id=? AND requested_mode='AUTO'
                 AND routing_status='ROUTING_REQUESTED'
                 AND NOT EXISTS (
                     SELECT 1 FROM run_controls AS stop
                     WHERE stop.agent_run_id=message_requests.agent_run_id
                 )""",
            (started_at, agent_run_id),
        ).rowcount
        if claimed == 0:
            return None
        if connection.execute(
            """UPDATE sessions SET agent_start_state='ROUTING_RUNNING'
               WHERE pending_agent_run_id=? AND agent_start_state='ROUTING_REQUESTED'""",
            (agent_run_id,),
        ).rowcount != 1:
            raise ValueError("INTENT_ROUTE_SESSION_STATE_INVALID")
        row = connection.execute(
            """SELECT r.*,s.project_id FROM message_requests AS r
               JOIN sessions AS s ON s.session_id=r.session_id
               WHERE r.agent_run_id=?""", (agent_run_id,),
        ).fetchone()
        return route_from_row(row)


def recover_intent_routes(database: Path) -> None:
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            """SELECT session_id,agent_run_id FROM message_requests
               WHERE requested_mode='AUTO' AND routing_status='ROUTING_RUNNING'"""
        ).fetchall()
        for row in rows:
            connection.execute(
                """UPDATE message_requests SET routing_status='ROUTING_REQUESTED',
                       unknown_cost_attempts=unknown_cost_attempts+1,
                       route_started_at=NULL,route_finished_at=NULL,
                       route_duration_ms=NULL WHERE agent_run_id=?""",
                (row["agent_run_id"],),
            )
            connection.execute(
                """UPDATE sessions SET agent_start_state='ROUTING_REQUESTED'
                   WHERE session_id=? AND pending_agent_run_id=?""",
                (row["session_id"], row["agent_run_id"]),
            )
